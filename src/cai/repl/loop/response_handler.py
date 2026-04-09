"""cai.repl.loop.response_handler — single-agent (non-parallel) response execution.

Entry points
------------
* ``build_conversation_input`` — build the agent's conversation input from
  message history + current user message.
* ``run_single_response`` — run one turn of agent inference (streamed or
  non-streamed) and call ``handle_post_turn`` at the end.

Return value of run_single_response
-------------------------------------
``(agent, _post_compact_input, _skip_auto_compact_after_interrupt, should_continue)``

``should_continue`` is ``True`` when the caller must issue ``continue`` to
skip the rest of the loop body (e.g. after a ContextCompactedError or a
guardrail trip).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
import re
import ast
from typing import Any, Optional, Tuple
import textwrap

from rich.markup import escape as _escape

from cai.repl.loop._event_loop import run_async
from rich.panel import Panel


def build_conversation_input(agent: Any, user_input: str, messages_ctf: str) -> Any:
    """Build the conversation input list (or string) for the agent.

    Reconstructs ``history_context`` from the agent's ``message_history``,
    fixes the message list structure, then appends the current ``user_input``.
    Falls back to a plain ``messages_ctf + user_input`` string when no history
    is available.
    """
    history_context: list = []
    if hasattr(agent, "model") and hasattr(agent.model, "message_history"):
        for msg in agent.model.message_history:
            role = msg.get("role")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")

            if role == "user":
                history_context.append({"role": "user", "content": content or ""})
            elif role == "system":
                history_context.append({"role": "system", "content": content or ""})
            elif role == "assistant":
                if tool_calls:
                    history_context.append(
                        {
                            "role": "assistant",
                            "content": content,  # may be None
                            "tool_calls": tool_calls,
                        }
                    )
                elif content is not None:
                    history_context.append({"role": "assistant", "content": content})
                elif content is None and not tool_calls:
                    history_context.append({"role": "assistant", "content": None})
            elif role == "tool":
                history_context.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id"),
                        "content": msg.get("content"),
                    }
                )

    # Fix list structure before sending to the model.
    try:
        from cai.util import fix_message_list
        history_context = fix_message_list(history_context)
    except Exception:
        pass

    if history_context:
        history_context.append({"role": "user", "content": user_input})
        return history_context

    return (messages_ctf or "") + (user_input or "")


def run_single_response(
    agent: Any,
    conversation_input: Any,
    user_input: str,
    _post_compact_input: Optional[str],
    _skip_auto_compact_after_interrupt: bool,
    messages_ctf: str,
    console: Any,
    session_logger: Any,
    parallel_count: int,
    idle_time: float,
    start_time: float,
) -> Tuple[Any, Optional[str], bool, bool]:
    """Run one agent turn and post-turn orchestration.

    Returns
    -------
    (agent, _post_compact_input, _skip_auto_compact_after_interrupt, should_continue)
    """
    # Guardrail exceptions are exported from the package root.
    from cai.sdk.agents import (
        InputGuardrailTripwireTriggered,
        OutputGuardrailTripwireTriggered,
        Runner,
    )
    from cai.sdk.agents.exceptions import ContextCompactedError
    from cai.sdk.agents.items import ToolCallOutputItem
    from cai.sdk.agents.stream_events import RunItemStreamEvent

    # Capture user_input before runner calls so ContextCompactedError
    # handlers can reference it even on the very first iteration.
    _last_user_input = user_input if isinstance(user_input, str) else ""

    # Disable streaming by default, unless specifically enabled by the
    # environment. However, prefer the non-streaming path when the
    # Runner implementation does not provide a real `run_streamed` API
    # (for example, in test contexts where Runner is a MagicMock).
    cai_stream = os.getenv("CAI_STREAM", "false")
    if not cai_stream or cai_stream.strip() == "":
        cai_stream = "false"
    stream_env = cai_stream.lower() == "true"

    run_streamed_attr = getattr(Runner, "run_streamed", None)
    try:
        from unittest.mock import Mock
    except Exception:
        Mock = None

    if stream_env and callable(run_streamed_attr) and (Mock is None or not isinstance(run_streamed_attr, Mock)):
        stream = True
    else:
        stream = False

    if stream:
        async def process_streamed_response(agent, conversation_input):
            tool_calls_seen = {}   # call_id -> item
            tool_results_seen = set()
            result = None
            stream_iterator = None

            try:
                result = Runner.run_streamed(agent, conversation_input)
                stream_iterator = result.stream_events()

                async for event in stream_iterator:
                    if isinstance(event, RunItemStreamEvent):
                        # MESSAGE / REASONING outputs (render immediately)
                        if event.name in ("message_output_created", "reasoning_item_created"):
                            try:
                                from cai.sdk.agents.items import ItemHelpers

                                content = ItemHelpers.text_message_output(event.item)
                            except Exception:
                                try:
                                    raw = getattr(event.item, "raw_item", {}) or {}
                                    if isinstance(raw, dict):
                                        content = raw.get("content", "")
                                    else:
                                        content = str(raw)
                                except Exception:
                                    content = ""

                            if content:
                                # Decode bytes-like content and bytes-literal strings
                                out_str = None
                                try:
                                    if isinstance(content, (bytes, bytearray)):
                                        out_str = content.decode("utf-8", errors="replace")
                                    else:
                                        out_str = str(content)
                                        if out_str.startswith("b'") or out_str.startswith('b"'):
                                            try:
                                                _val = ast.literal_eval(out_str)
                                                if isinstance(_val, (bytes, bytearray)):
                                                    out_str = _val.decode("utf-8", errors="replace")
                                            except Exception:
                                                pass
                                except Exception:
                                    out_str = str(content)

                                # Filter noisy class representations
                                try:
                                    out_str = re.sub(r"<class '.*?'>", "", out_str)
                                except Exception:
                                    pass

                                agent_name = (
                                    getattr(agent, "name", None)
                                    or getattr(getattr(agent, "model", None), "agent_name", None)
                                    or "Agent"
                                )

                                if event.name == "message_output_created":
                                    # Final response → Intelligence Panel
                                    # Print a blank line first to visually separate from
                                    # any streaming tokens that were written to stdout.
                                    try:
                                        import sys as _sys
                                        _sys.stdout.write("\n")
                                        _sys.stdout.flush()
                                    except Exception:
                                        pass
                                    try:
                                        from cai.repl.ui.renderers import display_agent_analysis
                                        display_agent_analysis(out_str, agent_name)
                                    except Exception:
                                        try:
                                            console.print(out_str)
                                        except Exception:
                                            try:
                                                print(out_str)
                                            except Exception:
                                                pass
                                else:
                                    # reasoning_item_created → colorized intermediate thinking
                                    try:
                                        for line in out_str.splitlines():
                                            ls = line.strip()
                                            try:
                                                width = console.size.width
                                            except Exception:
                                                width = 120
                                            wrap_width = max(40, int(width) - 20)
                                            try:
                                                wrapped = textwrap.fill(line, width=wrap_width)
                                            except Exception:
                                                wrapped = line
                                            if ls.lower().startswith("thought"):
                                                console.print(f"[yellow]{_escape(wrapped)}[/yellow]")
                                            elif ls.lower().startswith("reflection"):
                                                console.print(f"[blue]{_escape(wrapped)}[/blue]")
                                            elif ls.lower().startswith("action"):
                                                console.print(f"[green]{_escape(wrapped)}[/green]")
                                            else:
                                                console.print(_escape(wrapped))
                                    except Exception:
                                        try:
                                            print(out_str)
                                        except Exception:
                                            pass

                                # Mark this message content as rendered so final-print
                                # paths can skip duplicate output.
                                try:
                                    from cai.util import mark_runitem_rendered
                                    mark_runitem_rendered(agent_name, content=out_str)
                                except Exception:
                                    pass

                        # TOOL CALLED: announce quickly (non-blocking)
                        elif event.name == "tool_called":
                            try:
                                raw = getattr(event.item, "raw_item", event.item)
                                fn_name = getattr(
                                    raw,
                                    "name",
                                    getattr(getattr(raw, "function", None), "name", "tool"),
                                )
                                fn_args = getattr(raw, "arguments", getattr(raw, "args", ""))
                                fn_args_str = str(fn_args)
                                fn_args_disp = fn_args_str[:80] + "…" if len(fn_args_str) > 80 else fn_args_str

                                call_id = None
                                try:
                                    if isinstance(raw, dict):
                                        call_id = raw.get("call_id")
                                    else:
                                        call_id = getattr(raw, "call_id", None)
                                except Exception:
                                    call_id = None

                                try:
                                    agent_name = str(getattr(agent, "agent_name", getattr(agent, "name", "Agent")))
                                    msg = f"🛠️  Running {fn_name}({fn_args_disp})..."
                                    try:
                                        from cai.repl.ui.logging import render_tool_output

                                        render_tool_output(fn_name or "tool", msg, agent_name=agent_name, style="yellow")
                                    except Exception:
                                        console.print(Panel(msg, title=agent_name, style="yellow"))
                                except Exception:
                                    try:
                                        print(f"[tool] Running {fn_name}({fn_args_disp})...")
                                    except Exception:
                                        pass

                                if call_id:
                                    tool_calls_seen[call_id] = event.item
                                    # Mark tool call as rendered (so we don't show it again)
                                    try:
                                        from cai.util import mark_runitem_rendered

                                        agent_name = getattr(agent, "name", None) or getattr(getattr(agent, "model", None), "agent_name", None) or "Agent"
                                        mark_runitem_rendered(agent_name, call_id=call_id)
                                    except Exception:
                                        pass
                            except Exception:
                                if hasattr(event.item, "raw_item"):
                                    call_id = getattr(event.item.raw_item, "call_id", None)
                                    if call_id:
                                        tool_calls_seen[call_id] = event.item

                        # TOOL OUTPUT: decode/filter and render in a Panel
                        elif event.name == "tool_output":
                            if isinstance(event.item, ToolCallOutputItem):
                                try:
                                    # Determine call_id if present
                                    raw_item = getattr(event.item, "raw_item", {}) or {}
                                    call_id = raw_item.get("call_id") if isinstance(raw_item, dict) else getattr(raw_item, "call_id", None)

                                    # Extract and normalise output to string
                                    output = event.item.output
                                    if isinstance(output, (bytes, bytearray)):
                                        out_str = output.decode("utf-8", errors="replace")
                                    else:
                                        out_str = str(output)
                                        if out_str.startswith("b'") or out_str.startswith('b"'):
                                            try:
                                                _val = ast.literal_eval(out_str)
                                                if isinstance(_val, (bytes, bytearray)):
                                                    out_str = _val.decode("utf-8", errors="replace")
                                            except Exception:
                                                pass

                                    # Filter noisy class representations
                                    out_str = re.sub(r"<class '.*?'>", "", out_str)

                                    # Try to determine a friendly tool name
                                    fn_name = None
                                    if call_id and call_id in tool_calls_seen:
                                        try:
                                            raw_call = getattr(tool_calls_seen[call_id], "raw_item", tool_calls_seen[call_id])
                                            fn_name = getattr(raw_call, "name", getattr(getattr(raw_call, "function", None), "name", None))
                                        except Exception:
                                            fn_name = None

                                    try:
                                        from cai.repl.ui.logging import render_tool_output

                                        agent_name = getattr(agent, "agent_name", getattr(agent, "name", "Agent"))
                                        render_tool_output(fn_name or "tool", out_str, agent_name=agent_name, style="cyan")
                                    except Exception:
                                        try:
                                            title = f"{fn_name or 'tool'} output"
                                            console.print(Panel(out_str, title=title, style="cyan"))
                                        except Exception:
                                            try:
                                                print(out_str)
                                            except Exception:
                                                pass

                                    # Mark the tool output as rendered so final-print paths can skip duplicates
                                    try:
                                        from cai.util import mark_runitem_rendered

                                        agent_name = getattr(agent, "name", None) or getattr(getattr(agent, "model", None), "agent_name", None) or "Agent"
                                        mark_runitem_rendered(agent_name, call_id=call_id, content=out_str)
                                    except Exception:
                                        pass

                                    # Store as historical tool output for later consumption
                                    try:
                                        if call_id:
                                            tool_results_seen.add(call_id)
                                        agent.model.add_to_message_history({
                                            "role": "tool",
                                            "tool_call_id": call_id,
                                            "content": event.item.output,
                                        })
                                    except Exception:
                                        pass
                                except Exception:
                                    # Best-effort: ignore any errors rendering tool output
                                    pass
                return result

            except OutputGuardrailTripwireTriggered:
                try:
                    from cai.util import cleanup_all_streaming_resources
                    cleanup_all_streaming_resources()
                except Exception:
                    pass
                if stream_iterator is not None:
                    _aclose = getattr(stream_iterator, "aclose", None)
                    if callable(_aclose):
                        try:
                            _res = _aclose()
                            if inspect.isawaitable(_res):
                                try:
                                    await _res
                                except Exception:
                                    pass
                        except Exception:
                            pass
                if result is not None and hasattr(result, "_cleanup_tasks"):
                    try:
                        result._cleanup_tasks()
                    except Exception:
                        pass
                raise

            except (KeyboardInterrupt, asyncio.CancelledError) as exc:
                if stream_iterator is not None:
                    _aclose = getattr(stream_iterator, "aclose", None)
                    if callable(_aclose):
                        try:
                            _res = _aclose()
                            if inspect.isawaitable(_res):
                                try:
                                    await _res
                                except Exception:
                                    pass
                        except Exception:
                            pass
                if result is not None and hasattr(result, "_cleanup_tasks"):
                    try:
                        result._cleanup_tasks()
                    except Exception:
                        pass
                try:
                    for call_id in tool_calls_seen:
                        if call_id not in tool_results_seen:
                            agent.model.add_to_message_history({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": "Tool execution interrupted",
                            })
                except Exception:
                    pass
                raise exc

            except ContextCompactedError:
                raise

            except Exception as exc:
                if stream_iterator is not None:
                    _aclose = getattr(stream_iterator, "aclose", None)
                    if callable(_aclose):
                        try:
                            _res = _aclose()
                            if inspect.isawaitable(_res):
                                try:
                                    await _res
                                except Exception:
                                    pass
                        except Exception:
                            pass
                if result is not None and hasattr(result, "_cleanup_tasks"):
                    try:
                        result._cleanup_tasks()
                    except Exception:
                        pass
                if isinstance(exc, OutputGuardrailTripwireTriggered):
                    raise
                logging.getLogger(__name__).error(
                    "Error occurred during streaming: %s", exc, exc_info=True
                )
                if os.getenv("CAI_DEBUG", "1") == "2":
                    import traceback
                    tb = traceback.format_exc()
                    print(f"\n[Error occurred during streaming: {exc}]\nLocation: {tb}")
                # Re-raise so the outer cli.py handler can surface this to
                # stderr and the user sees why the runner stopped.
                raise

        try:
            run_async(process_streamed_response(agent, conversation_input))
        except ContextCompactedError:
            _base = _last_user_input or "Continue the current task."
            _post_compact_input = (
                f"{_base}\n\n"
                "IMPORTANT: Your context window was just compacted. "
                "Your session memory is already loaded above. "
                "Review the 'Exhausted Approaches' section in your memory and "
                "DO NOT repeat any technique, command, URL, port scan, or login "
                "attempt already listed there. "
                "Pick up exactly where you left off using only NEW approaches."
            )
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER as _AM
            _reloaded = _AM.get_active_agent()
            if _reloaded is not None:
                agent = _reloaded
            console.print("[bold green]✓ Context window reset — resuming task[/bold green]\n")
            return agent, _post_compact_input, _skip_auto_compact_after_interrupt, True
        except OutputGuardrailTripwireTriggered as e:
            guardrail_name = e.guardrail_result.guardrail.get_name()
            reason = e.guardrail_result.output.output_info.get("reason", "Security policy violation")
            print("\n\033[91m🛡️  SECURITY GUARDRAIL TRIGGERED\033[0m")
            print(f"\033[91mGuardrail: {guardrail_name}\033[0m")
            print(f"\033[91mReason: {reason}\033[0m")
            print("\033[93mThe agent's output was blocked for security reasons.\033[0m")
            print("\033[96mYou can continue the conversation with a different request.\033[0m\n")
            return agent, _post_compact_input, _skip_auto_compact_after_interrupt, True
        except KeyboardInterrupt:
            raise
        except RuntimeError as e:
            if "This event loop is already running" in str(e) or "Cannot close a running event loop" in str(e):
                if sys.platform.startswith("win"):
                    PolicyCls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
                    if PolicyCls is not None:
                        asyncio.set_event_loop_policy(PolicyCls())
                    else:
                        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
                else:
                    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
                # Re-use the persistent loop; if it truly is running (nested
                # async context) this will also raise, but that's unavoidable.
                from cai.repl.loop._event_loop import get_repl_loop
                _fb_loop = get_repl_loop()
                try:
                    _fb_loop.run_until_complete(
                        process_streamed_response(agent, conversation_input)
                    )
                except OutputGuardrailTripwireTriggered as inner_e:
                    guardrail_name = inner_e.guardrail_result.guardrail.get_name()
                    reason = inner_e.guardrail_result.output.output_info.get(
                        "reason", "Security policy violation"
                    )
                    print("\n\033[91m🛡️  SECURITY GUARDRAIL TRIGGERED\033[0m")
                    print(f"\033[91mGuardrail: {guardrail_name}\033[0m")
                    print(f"\033[91mReason: {reason}\033[0m")
                    print("\033[93mThe agent's output was blocked for security reasons.\033[0m")
                    print("\033[96mYou can continue the conversation with a different request.\033[0m\n")
                    return agent, _post_compact_input, _skip_auto_compact_after_interrupt, True
                # Do NOT close the loop — it is the persistent session loop.
            else:
                raise

    else:
        # Non-streaming path
        try:
            response = run_async(Runner.run(agent, conversation_input))
        except ContextCompactedError:
            _base = _last_user_input or "Continue the current task."
            _post_compact_input = (
                f"{_base}\n\n"
                "IMPORTANT: Your context window was just compacted. "
                "Your session memory is already loaded above. "
                "Review the 'Exhausted Approaches' section in your memory and "
                "DO NOT repeat any technique, command, URL, port scan, or login "
                "attempt already listed there. "
                "Pick up exactly where you left off using only NEW approaches."
            )
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER as _AM
            _reloaded = _AM.get_active_agent()
            if _reloaded is not None:
                agent = _reloaded
            console.print("[bold green]✓ Context window reset — resuming task[/bold green]\n")
            return agent, _post_compact_input, _skip_auto_compact_after_interrupt, True
        except InputGuardrailTripwireTriggered as e:
            reason = "Potential security threat detected in input"
            if hasattr(e, "guardrail_result") and e.guardrail_result:
                if hasattr(e.guardrail_result, "output") and e.guardrail_result.output:
                    reason = e.guardrail_result.output.output_info.get("reason", reason)
            print("\n\033[91m🛡️  INPUT SECURITY GUARDRAIL TRIGGERED\033[0m")
            print(f"\033[91mReason: {reason}\033[0m")
            print("\033[93mYour input was blocked for security reasons.\033[0m")
            if "base64" in reason.lower() or "pattern" in reason.lower():
                print("\n\033[96mThis may be due to malicious content in the conversation history.\033[0m")
                print("\033[96mOptions:\033[0m")
                print("  1. Type \033[92m/clear\033[0m to clear the conversation history")
                print("  2. Type \033[92m/config set 26 false\033[0m to temporarily disable guardrails")
                print("  3. Type \033[92m/exit\033[0m to exit CAI")
            else:
                print("\033[96mPlease rephrase your request or try a different approach.\033[0m\n")
            return agent, _post_compact_input, _skip_auto_compact_after_interrupt, True
        except OutputGuardrailTripwireTriggered as e:
            guardrail_name = e.guardrail_result.guardrail.get_name()
            reason = e.guardrail_result.output.output_info.get("reason", "Security policy violation")
            print("\n\033[91m🛡️  SECURITY GUARDRAIL TRIGGERED\033[0m")
            print(f"\033[91mGuardrail: {guardrail_name}\033[0m")
            print(f"\033[91mReason: {reason}\033[0m")
            print("\033[93mThe agent's output was blocked for security reasons.\033[0m")
            print("\033[96mYou can continue the conversation with a different request.\033[0m\n")
            return agent, _post_compact_input, _skip_auto_compact_after_interrupt, True

        # Non-streaming: record ONLY tool outputs from response.new_items.
        # Tool call (assistant) messages are already stored by openai_chatcompletions.py.
        for item in response.new_items:
            if isinstance(item, ToolCallOutputItem):
                tool_call_id = item.raw_item["call_id"]
                tool_msg_exists = any(
                    msg.get("role") == "tool" and msg.get("tool_call_id") == tool_call_id
                    for msg in agent.model.message_history
                )
                if not tool_msg_exists:
                    agent.model.add_to_message_history({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": item.output,
                    })

    # Post-turn orchestration: message fixes + auto-compact check
    try:
        from cai.util.orchestration import handle_post_turn
        agent, _post_compact_input, _skip_auto_compact_after_interrupt = handle_post_turn(
            agent,
            console,
            _last_user_input,
            _post_compact_input,
            _skip_auto_compact_after_interrupt,
            parallel_count,
            session_logger=session_logger,
            start_time=start_time,
            idle_time=int(idle_time),
        )
    except Exception:
        pass

    # Clear per-agent rendered-runitem markers for this turn so future turns
    # don't incorrectly suppress output.
    try:
        from cai.util import clear_rendered_runitems_for_agent

        agent_name = getattr(agent, "name", None) or getattr(getattr(agent, "model", None), "agent_name", None) or "Agent"
        clear_rendered_runitems_for_agent(agent_name)
    except Exception:
        pass

    return agent, _post_compact_input, _skip_auto_compact_after_interrupt, False
