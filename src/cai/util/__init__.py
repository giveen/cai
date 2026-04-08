"""
Compatibility shim for `cai.util`.

This file provides a compact, well-scoped subset of the original
`util.py` functionality that tests and other modules import at
package-import time. It focuses on timers, a cost tracker, a few
helpers (prompt/template rendering), and lightweight no-op streaming
helpers to avoid import-time NameErrors. The goal is compatibility
during the refactor while keeping behavior simple and safe.
"""

from __future__ import annotations

import atexit
import importlib.resources
import json
import os
import pathlib
import threading
import time
import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from rich.console import Console
from rich.theme import Theme
from wasabi.util import color

# Simple themed console for lightweight output used by some helpers
theme = Theme({"dim": "#9E9E9E"})
console = Console(theme=theme)

# -------------------- Timers (active / idle) --------------------
_active_timer_start: Optional[float] = None
_active_time_total: float = 0.0
_idle_timer_start: Optional[float] = None
_idle_time_total: float = 0.0
_timing_lock = threading.Lock()


def start_active_timer() -> None:
    global _active_timer_start, _idle_timer_start, _idle_time_total
    with _timing_lock:
        if _idle_timer_start is not None:
            _idle_time_total += time.time() - _idle_timer_start
            _idle_timer_start = None
        if _active_timer_start is None:
            _active_timer_start = time.time()


def stop_active_timer() -> None:
    global _active_timer_start, _active_time_total, _idle_timer_start
    with _timing_lock:
        if _active_timer_start is not None:
            _active_time_total += time.time() - _active_timer_start
            _active_timer_start = None
        if _idle_timer_start is None:
            _idle_timer_start = time.time()


def start_idle_timer() -> None:
    global _idle_timer_start, _active_timer_start, _active_time_total
    with _timing_lock:
        if _active_timer_start is not None:
            _active_time_total += time.time() - _active_timer_start
            _active_timer_start = None
        if _idle_timer_start is None:
            _idle_timer_start = time.time()


def stop_idle_timer() -> None:
    global _idle_timer_start, _idle_time_total, _active_timer_start
    with _timing_lock:
        if _idle_timer_start is not None:
            _idle_time_total += time.time() - _idle_timer_start
            _idle_timer_start = None
        if _active_timer_start is None:
            _active_timer_start = time.time()


def _format_seconds(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def get_active_time_seconds() -> float:
    with _timing_lock:
        total = _active_time_total
        if _active_timer_start is not None:
            total += time.time() - _active_timer_start
    return total


def get_idle_time_seconds() -> float:
    with _timing_lock:
        total = _idle_time_total
        if _idle_timer_start is not None:
            total += time.time() - _idle_timer_start
    return total


def get_active_time() -> str:
    return _format_seconds(get_active_time_seconds())


def get_idle_time() -> str:
    return _format_seconds(get_idle_time_seconds())


# Start in idle state
start_idle_timer()

# -------------------- Cost tracking (minimal, test-friendly) --------------------
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)


@dataclass
class CostTracker:
    session_total_cost: float = 0.0
    current_agent_total_cost: float = 0.0
    model_pricing_cache: Dict[str, tuple] = field(default_factory=dict)
    calculated_costs_cache: Dict[str, float] = field(default_factory=dict)
    last_interaction_cost: float = 0.0

    def log_final_cost(self) -> None:
        if os.environ.get("CAI_COST_DISPLAYED", "").lower() == "true":
            return
        print(f"\nTotal CAI Session Cost: ${self.session_total_cost:.6f}")

    def get_model_pricing(self, model_name: str) -> tuple:
        model_key = model_name
        if model_key in self.model_pricing_cache:
            return self.model_pricing_cache[model_key]
        try:
            pricing_path = pathlib.Path("pricing.json")
            if pricing_path.exists():
                with pricing_path.open(encoding="utf-8") as fh:
                    local = json.load(fh)
                    if model_key in local:
                        info = local[model_key]
                        pricing = (
                            float(info.get("input_cost_per_token", 0)),
                            float(info.get("output_cost_per_token", 0)),
                        )
                        self.model_pricing_cache[model_key] = pricing
                        return pricing
        except Exception:
            pass
        # Fallback to LiteLLM mapping (best-effort)
        try:
            import requests

            resp = requests.get(LITELLM_URL, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                p = data.get(model_key) or data.get(model_key.lower())
                if p:
                    pricing = (
                        float(p.get("input_cost_per_token", 0)),
                        float(p.get("output_cost_per_token", 0)),
                    )
                    self.model_pricing_cache[model_key] = pricing
                    return pricing
        except Exception:
            pass
        self.model_pricing_cache[model_key] = (0.0, 0.0)
        return (0.0, 0.0)

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        label: Optional[str] = None,
        force_calculation: bool = False,
    ) -> float:
        key = f"{model}_{input_tokens}_{output_tokens}"
        if key in self.calculated_costs_cache and not force_calculation:
            return float(self.calculated_costs_cache[key])
        in_cost, out_cost = self.get_model_pricing(model)
        total = input_tokens * in_cost + output_tokens * out_cost
        self.calculated_costs_cache[key] = total
        return float(total)

    def update_session_cost(self, new_cost: float) -> None:
        if new_cost <= 0:
            return
        self.session_total_cost += float(new_cost)

    def reset_cost_for_local_model(self, model_name: str) -> bool:
        input_cost, output_cost = self.get_model_pricing(model_name)
        if input_cost == 0.0 and output_cost == 0.0:
            self.last_interaction_cost = 0.0
            return True
        return False

    def reset_agent_costs(self) -> None:
        self.current_agent_total_cost = 0.0


# Single global tracker used around the codebase
COST_TRACKER = CostTracker()
atexit.register(COST_TRACKER.log_final_cost)


def calculate_model_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    label: Optional[str] = None,
    force_calculation: bool = False,
) -> float:
    try:
        return float(
            COST_TRACKER.calculate_cost(
                model, input_tokens, output_tokens, label=label, force_calculation=force_calculation
            )
        )
    except Exception:
        try:
            in_cost, out_cost = COST_TRACKER.get_model_pricing(model)
            return float(input_tokens * in_cost + output_tokens * out_cost)
        except Exception:
            return 0.0


# -------------------- Message helpers (keeps behavior for tests) --------------------
def fix_message_list(messages):  # pylint: disable=R0914,R0915,R0912
    sanitized_messages = []
    for msg in messages:
        msg_copy = msg.copy()
        if msg_copy.get("role") == "tool" and msg_copy.get("tool_call_id"):
            if len(msg_copy["tool_call_id"]) > 40:
                msg_copy["tool_call_id"] = msg_copy["tool_call_id"][:40]
        if msg_copy.get("role") == "assistant" and msg_copy.get("tool_calls"):
            tc_copy = []
            for tc in msg_copy.get("tool_calls", []):
                tc_c = tc.copy()
                if tc_c.get("id") and len(tc_c.get("id")) > 40:
                    tc_c["id"] = tc_c["id"][:40]
                tc_copy.append(tc_c)
            msg_copy["tool_calls"] = tc_copy
        sanitized_messages.append(msg_copy)

    processed_messages = []
    tool_call_map = {}

    for i, msg in enumerate(sanitized_messages):
        if msg.get("role") in ["user", "system"] and (
            msg.get("content") is None or not str(msg.get("content", "")).strip()
        ):
            if msg.get("role") == "system":
                msg["content"] = ""
                processed_messages.append(msg)
            continue
        processed_messages.append(msg)
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("id"):
                    tool_id = tc.get("id")
                    if tool_id not in tool_call_map:
                        tool_call_map[tool_id] = {
                            "assistant_idx": len(processed_messages) - 1,
                            "tool_idx": None,
                        }
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool_id = msg.get("tool_call_id")
            if tool_id in tool_call_map:
                tool_call_map[tool_id]["tool_idx"] = len(processed_messages) - 1
            else:
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {"name": "unknown_function", "arguments": "{}"},
                        }
                    ],
                }
                processed_messages.insert(len(processed_messages) - 1, assistant_msg)
                tool_call_map[tool_id] = {
                    "assistant_idx": len(processed_messages) - 2,
                    "tool_idx": len(processed_messages) - 1,
                }

    # Re-order tool messages so they follow their assistant messages
    i = 0
    max_iter = max(10, len(processed_messages) * 3 + 10)
    iter_count = 0
    while i < len(processed_messages):
        iter_count += 1
        if iter_count > max_iter:
            break
        msg = processed_messages[i]
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool_id = msg.get("tool_call_id")
            if i > 0:
                j = i - 1
                while j >= 0 and processed_messages[j].get("role") == "tool":
                    j -= 1
                prev_non_tool = processed_messages[j] if j >= 0 else None
                is_valid = False
                if (
                    prev_non_tool
                    and prev_non_tool.get("role") == "assistant"
                    and prev_non_tool.get("tool_calls")
                ):
                    if any(tc.get("id") == tool_id for tc in prev_non_tool.get("tool_calls", [])):
                        is_valid = True
                if not is_valid:
                    assistant_idx = None
                    for k in range(i - 1, -1, -1):
                        a = processed_messages[k]
                        if (
                            a.get("role") == "assistant"
                            and a.get("tool_calls")
                            and any(tc.get("id") == tool_id for tc in a.get("tool_calls", []))
                        ):
                            assistant_idx = k
                            break
                    if assistant_idx is not None:
                        tool_msg = processed_messages.pop(i)
                        insert_at = assistant_idx + 1
                        while (
                            insert_at < len(processed_messages)
                            and processed_messages[insert_at].get("role") == "tool"
                        ):
                            insert_at += 1
                        processed_messages.insert(insert_at, tool_msg)
                        i = min(i, insert_at)
                        continue
                    else:
                        assistant_msg = {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": tool_id,
                                    "type": "function",
                                    "function": {"name": "unknown_function", "arguments": "{}"},
                                }
                            ],
                        }
                        processed_messages.insert(i, assistant_msg)
                        i += 2
                        continue
        i += 1

    # Ensure content not None
    for msg in processed_messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            pass
        elif msg.get("role") != "tool" and msg.get("content") is None and not msg.get("tool_calls"):
            msg["content"] = ""
        if msg.get("role") == "tool":
            if msg.get("content") is None or msg.get("content") == "":
                msg["content"] = f"Tool response for {msg.get('tool_call_id', 'unknown')}"

    # Claude interleaving simplification
    i = 0
    while i < len(processed_messages) - 1:
        cur = processed_messages[i]
        nxt = processed_messages[i + 1]
        if (
            cur.get("role") == "assistant"
            and cur.get("tool_calls")
            and (nxt.get("role") != "tool" or not nxt.get("tool_call_id"))
        ):
            tool_id = cur["tool_calls"][0].get("id", "unknown")
            tool_name = cur["tool_calls"][0].get("function", {}).get("name", "unknown_function")
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_id,
                "content": f"Auto-generated response for {tool_name}",
            }
            processed_messages.insert(i + 1, tool_msg)
            i += 2
        else:
            i += 1
    return processed_messages


# -------------------- Lightweight streaming/CLI helpers (no-op / safe) --------------------
# These are intentionally minimal so modules importing them don't fail.
_STREAMING_SESSIONS: Dict[str, Dict[str, Any]] = {}
_AGENT_STREAMING_CONTEXTS: Dict[str, Dict[str, Any]] = {}
# Backwards-compatible name used by older modules
_LIVE_STREAMING_PANELS = _STREAMING_SESSIONS


def cli_print_tool_output(
    tool_name: str,
    output: str,
    call_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    **_kwargs,
) -> None:
    # Always suppress empty output
    if not output or not output.strip():
        return

    # Build a deduplication key from tool_name + command args + agent_id
    args = _kwargs.get("args") or {}
    if isinstance(args, dict):
        cmd_arg = args.get("command", "") or str(args)
    else:
        cmd_arg = str(args)
    token_info = _kwargs.get("token_info") or {}
    agent_id = token_info.get("agent_id", "") if isinstance(token_info, dict) else ""
    command_key = f"{tool_name}:{cmd_arg}:{agent_id}"

    streaming_enabled = os.getenv("CAI_STREAM", "true").lower() not in ("false", "0", "no")

    if streaming_enabled:
        # Content-based deduplication: suppress exact duplicates
        seen = cli_print_tool_output._seen_calls  # type: ignore[attr-defined]
        content_key = (command_key, output)
        if content_key in seen:
            return
        seen.add(content_key)
    else:
        # Time-based deduplication: suppress if same key shown within threshold
        display_times = cli_print_tool_output._command_display_times  # type: ignore[attr-defined]
        threshold = 0.5
        last = display_times.get(command_key)
        if last is not None and (time.time() - last) < threshold:
            return
        display_times[command_key] = time.time()

    if call_id:
        _STREAMING_SESSIONS[call_id] = {
            "tool_name": tool_name,
            "current_output": output,
            "agent_name": agent_name,
            "is_complete": True,
        }
    # Sanitize output: strip ANSI escapes, normalize CR->LF, remove progress-meter
    # noise (e.g., curl progress), wrap long lines, and truncate to a reasonable
    # maximum length to avoid flooding the CLI.
    try:
        sanitized = str(output or "")
        # normalize line endings
        sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")

        # remove ANSI escape sequences
        try:
            sanitized = re.sub(r"\x1B[@-_][0-?]*[ -/]*[@-~]", "", sanitized)
        except Exception:
            sanitized = re.sub(r"\x1b\[[0-9;]*[mK]", "", sanitized)

        # drop obvious progress meter header/lines (starts with "% ")
        lines = []
        for ln in sanitized.splitlines():
            s = ln.strip()
            if not s:
                lines.append("")
                continue
            # skip curl-like progress lines that begin with a percent column
            if s.startswith("% ") or s.startswith("%Total") or s.startswith("%\t"):
                continue
            # skip repeated carriage-like artifacts
            if set(s) <= set("-=.#|<>*%0123456789 ") and len(s) < 120:
                # likely a progress bar or separator line
                continue
            lines.append(ln)
        sanitized = "\n".join(lines)

        # collapse excessive blank lines
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)

        # truncate very long outputs
        max_chars = 4000
        if len(sanitized) > max_chars:
            sanitized = sanitized[:max_chars] + "…"

        # wrap long lines to terminal width if available
        try:
            width = console.size.width or 120
        except Exception:
            width = 120
        wrap_width = max(40, int(width) - 20)
        wrapped = []
        for ln in sanitized.splitlines():
            if len(ln) > wrap_width:
                wrapped.append(textwrap.fill(ln, width=wrap_width))
            else:
                wrapped.append(ln)
        sanitized = "\n".join(wrapped)

        from rich.markup import escape as _escape
        console.print(f"{_escape(tool_name)} {sanitized}")
    except Exception:
        try:
            # best-effort fallback
            print(tool_name, str(output))
        except Exception:
            pass


# Deduplication state attached to the function itself
cli_print_tool_output._seen_calls: set = set()  # type: ignore[attr-defined]
cli_print_tool_output._command_display_times: Dict[str, float] = {}  # type: ignore[attr-defined]


def cli_print_tool_call(
    tool_name: str,
    tool_args: Optional[dict] = None,
    tool_output: Optional[str] = None,
    call_id: Optional[str] = None,
    interaction_input_tokens: int = 0,
    interaction_output_tokens: int = 0,
    interaction_reasoning_tokens: int = 0,
    total_input_tokens: int = 0,
    total_output_tokens: int = 0,
    total_reasoning_tokens: int = 0,
    model: Optional[str] = None,
    agent_name: Optional[str] = None,
    debug: bool = False,
    **_kwargs,
) -> None:
    """Minimal backward-compatible printer for tool calls used in templates."""
    if call_id:
        _STREAMING_SESSIONS[call_id] = {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "current_output": str(tool_output) if tool_output is not None else "",
            "agent_name": agent_name,
            "is_complete": True,
            "tokens": {
                "interaction": {
                    "input": interaction_input_tokens,
                    "output": interaction_output_tokens,
                    "reasoning": interaction_reasoning_tokens,
                },
                "total": {
                    "input": total_input_tokens,
                    "output": total_output_tokens,
                    "reasoning": total_reasoning_tokens,
                },
                "model": model,
            },
        }
    try:
        if tool_output is None:
            console.print(f"[tool][{tool_name}][/tool] (no output)")
        else:
            out = str(tool_output)
            display = out if len(out) <= 500 else out[:500] + "…"
            console.print(f"[tool][{tool_name}][/tool] {display}")
        if debug:
            console.print(
                f"[debug] tokens={total_input_tokens}+{total_output_tokens} model={model}"
            )
    except Exception:
        try:
            if tool_output is not None:
                print(tool_output)
        except Exception:
            pass


def cli_print_agent_messages(agent_name: str, messages: list | None = None, *args, **_kwargs) -> None:
    """Print agent messages for the CLI.

    Backwards-compatible shim: callers sometimes pass a single ``message``
    (keyword) or a ``messages`` list. Accept either and render message
    contents to stdout. Additional kwargs are accepted for compatibility
    (eg. ``suppress_empty``) and ignored here.
    """
    try:
        # Support both `message=` (single dict) and `messages=` (list)
        if messages is None:
            maybe_msg = _kwargs.get("message") if isinstance(_kwargs, dict) else None
            if maybe_msg is not None:
                msgs = [maybe_msg]
            else:
                msgs = []
        else:
            msgs = list(messages)

        suppress = bool(_kwargs.get("suppress_empty", False)) if isinstance(_kwargs, dict) else False

        if suppress and not msgs:
            return

        # Print a short header then each message content
        console.print(f"[agent]{agent_name}[/agent] {len(msgs)} messages")
        for m in msgs:
            try:
                # Support dicts with a 'content' field or plain strings
                content = m.get("content") if isinstance(m, dict) else str(m)
                if content is None:
                    continue
                console.print(content)
            except Exception:
                try:
                    console.print(str(m))
                except Exception:
                    pass
    except Exception:
        pass


def start_tool_streaming(call_id: str, tool_name: str, agent_name: Optional[str] = None) -> None:
    _STREAMING_SESSIONS[call_id] = {
        "tool_name": tool_name,
        "current_output": "",
        "agent_name": agent_name,
        "is_complete": False,
    }


def update_tool_streaming(call_id: str, chunk: str) -> None:
    s = _STREAMING_SESSIONS.get(call_id)
    if s is not None:
        s["current_output"] = s.get("current_output", "") + str(chunk)


def finish_tool_streaming(*args, **_kwargs) -> None:
    """
    Finish a tool streaming session.

    Supports both legacy positional signature used across the codebase
    (tool_name, tool_args, content, call_id, execution_info, token_info)
    and the newer keyword-based form where `call_id` is provided.
    """
    call_id = None

    # Prefer explicit keyword argument
    if "call_id" in _kwargs and isinstance(_kwargs["call_id"], str):
        call_id = _kwargs["call_id"]
    # Legacy positional form: call_id is the 4th positional argument
    elif args and len(args) >= 4 and isinstance(args[3], str):
        call_id = args[3]
    # Single-argument form where only call_id was provided
    elif len(args) == 1 and isinstance(args[0], str):
        call_id = args[0]

    if not call_id:
        return

    s = _STREAMING_SESSIONS.get(call_id)
    if s is not None:
        s["is_complete"] = True


def create_agent_streaming_context(
    agent_name: str,
    counter: int = 0,
    model: str = "",
    **_kwargs,
) -> Dict[str, Any]:
    """Create and register a streaming context for *agent_name*.

    Accepts the extra ``counter`` and ``model`` kwargs that
    ``OpenAIChatCompletionsModel.stream_response`` passes so callers do not
    need to be kept in sync with the stub signature.
    """
    ctx = {"agent_name": agent_name, "live": None, "is_started": False}
    _AGENT_STREAMING_CONTEXTS[agent_name] = ctx
    # Track active contexts on the function for legacy cleanup
    if not hasattr(create_agent_streaming_context, "_active_streaming"):
        create_agent_streaming_context._active_streaming = {}
    create_agent_streaming_context._active_streaming[agent_name] = ctx
    return ctx


def update_agent_streaming_content(
    agent_name_or_ctx,
    content: str = "",
    token_stats: Optional[Dict[str, Any]] = None,
    **_kwargs,
) -> None:
    """Append *content* to the streaming context and print it to the terminal.

    Accepts either the *agent_name* string or the context dict returned by
    ``create_agent_streaming_context`` as the first argument, matching the
    calling convention used in ``openai_chatcompletions.py``.
    """
    import sys

    # Resolve agent name
    if isinstance(agent_name_or_ctx, dict):
        agent_name_or_ctx = agent_name_or_ctx.get("agent_name")
    if not agent_name_or_ctx:
        return

    ctx = _AGENT_STREAMING_CONTEXTS.get(agent_name_or_ctx)
    if ctx is not None:
        ctx["last_content"] = content

    # Write the text token directly to stdout so it appears on screen
    # immediately — this is the primary rendering path when the full Rich
    # Live panel is not in use.
    if content:
        try:
            sys.stdout.write(content)
            sys.stdout.flush()
        except Exception:
            pass


def finish_agent_streaming(
    agent_name_or_ctx,
    token_stats: Optional[Dict[str, Any]] = None,
    **_kwargs,
) -> None:
    """Finalise the streaming session for *agent_name_or_ctx*.

    Accepts either the *agent_name* string or the context dict (as passed by
    ``openai_chatcompletions.py``) and prints a trailing newline when the
    streaming context indicates that content was actually printed.
    """
    import sys

    if isinstance(agent_name_or_ctx, dict):
        agent_name_or_ctx = agent_name_or_ctx.get("agent_name")
    if not agent_name_or_ctx:
        return

    ctx = _AGENT_STREAMING_CONTEXTS.pop(agent_name_or_ctx, None)
    if hasattr(create_agent_streaming_context, "_active_streaming"):
        create_agent_streaming_context._active_streaming.pop(agent_name_or_ctx, None)

    # Print a newline after the streamed content so the next prompt starts
    # on a fresh line.
    if ctx and ctx.get("last_content"):
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass


def cleanup_all_streaming_resources() -> None:
    _STREAMING_SESSIONS.clear()
    _AGENT_STREAMING_CONTEXTS.clear()


def cleanup_agent_streaming_resources(agent_name: str) -> None:
    for k, v in list(_STREAMING_SESSIONS.items()):
        if v.get("agent_name") == agent_name:
            _STREAMING_SESSIONS.pop(k, None)
    _AGENT_STREAMING_CONTEXTS.pop(agent_name, None)


# -------------------- Claude thinking helpers (minimal) --------------------
def start_claude_thinking_if_applicable(
    agent_name: Optional[str] = None, *_, **__
) -> Optional[Dict[str, Any]]:
    """Return a minimal thinking context when requested by callers.

    The real implementation shows a live 'thinking' panel. Here we return
    a small context dict that calling code can inspect and update.
    """
    if not agent_name:
        return None
    ctx = {
        "agent_name": agent_name,
        "is_started": False,
        "live": None,
    }
    _AGENT_STREAMING_CONTEXTS.setdefault(agent_name, ctx)
    return ctx


def update_claude_thinking_content(agent_name_or_ctx, content: str) -> None:
    """Update thinking content for the given agent.

    Accepts either the *agent_name* string or the context dict returned by
    ``start_claude_thinking_if_applicable`` (which is what call sites in
    ``openai_chatcompletions.py`` pass as *thinking_context*).
    """
    if isinstance(agent_name_or_ctx, dict):
        agent_name_or_ctx = agent_name_or_ctx.get("agent_name")
    if not agent_name_or_ctx:
        return
    ctx = _AGENT_STREAMING_CONTEXTS.get(agent_name_or_ctx)
    if ctx is not None:
        ctx["thinking_content"] = content


def finish_claude_thinking_display(agent_name_or_ctx) -> None:
    """Finalise and remove the thinking context for the given agent.

    Accepts either the *agent_name* string or the context dict returned by
    ``start_claude_thinking_if_applicable``.
    """
    if isinstance(agent_name_or_ctx, dict):
        agent_name_or_ctx = agent_name_or_ctx.get("agent_name")
    if not agent_name_or_ctx:
        return
    _AGENT_STREAMING_CONTEXTS.pop(agent_name_or_ctx, None)


# -------------------- Prompt/template helpers --------------------
def load_prompt_template(template_path: str) -> str:
    try:
        parts = template_path.split("/")
        pkg = ".".join(["cai"] + parts[:-1])
        fname = parts[-1]
        try:
            return importlib.resources.read_text(pkg, fname)
        except Exception:
            with importlib.resources.path(pkg, fname) as p:
                return pathlib.Path(p).read_text(encoding="utf-8")
    except Exception:
        return ""


def create_system_prompt_renderer(base_instructions: str) -> Callable[..., str]:
    def render(run_context=None, agent=None):
        if run_context is None and agent is None:
            return base_instructions
        return base_instructions

    render._is_system_prompt_renderer = True
    render._base_instructions = base_instructions
    return render


def append_instructions(agent: Any, additional_instructions: str) -> None:
    if not agent.instructions:
        return
    if callable(agent.instructions) and getattr(
        agent.instructions, "_is_system_prompt_renderer", False
    ):
        base = agent.instructions._base_instructions
        agent.instructions = create_system_prompt_renderer(base + additional_instructions)
    elif callable(agent.instructions):
        orig = agent.instructions

        def wrapped(*a, **k):
            return orig(*a, **k) + additional_instructions

        agent.instructions = wrapped
    else:
        agent.instructions = str(agent.instructions) + additional_instructions


def visualize_agent_graph(start_agent: Any) -> None:
    try:
        console.print(f"Agent graph for {getattr(start_agent, 'name', '<unknown>')}")
    except Exception:
        pass


# -------------------- Small utilities --------------------
def fix_litellm_transcription_annotations() -> bool:
    return False


def setup_ctf():
    """Minimal shim for CTF setup used by the CLI during startup.

    Returns a tuple of (ctf_object_or_None, messages_ctf_str).
    This is a lightweight no-op implementation intended only to satisfy
    import-time usage in tests and simple CLI runs during the refactor.
    """
    return None, ""


def get_ollama_api_base() -> str:
    return (
        os.environ.get("OLLAMA_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or "http://localhost:8000/v1"
    )


def get_ollama_auth_headers() -> Dict[str, str]:
    api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("OPENAI_API_KEY")
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def get_minimax_api_base() -> str:
    return (
        os.environ.get("MINIMAX_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or "http://localhost:8080/v1"
    )


def get_model_name(model: str) -> str:
    return model


def get_model_input_tokens(model: str) -> Optional[int]:
    # Best-effort mapping for UI; returns None when unknown
    return None


# Exported API
__all__ = [
    "console",
    "color",
    "COST_TRACKER",
    "calculate_model_cost",
    "start_active_timer",
    "stop_active_timer",
    "start_idle_timer",
    "stop_idle_timer",
    "get_active_time",
    "get_idle_time",
    "get_active_time_seconds",
    "get_idle_time_seconds",
    "fix_message_list",
    "setup_ctf",
    "cli_print_tool_output",
    "cli_print_tool_call",
    "cli_print_agent_messages",
    "start_tool_streaming",
    "update_tool_streaming",
    "finish_tool_streaming",
    "create_agent_streaming_context",
    "update_agent_streaming_content",
    "finish_agent_streaming",
    "cleanup_all_streaming_resources",
    "cleanup_agent_streaming_resources",
    "start_claude_thinking_if_applicable",
    "update_claude_thinking_content",
    "finish_claude_thinking_display",
    "load_prompt_template",
    "create_system_prompt_renderer",
    "append_instructions",
    "visualize_agent_graph",
    "get_ollama_api_base",
    "get_ollama_auth_headers",
    "get_minimax_api_base",
    "get_model_name",
    "get_model_input_tokens",
]
