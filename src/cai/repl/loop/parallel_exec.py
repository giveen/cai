"""cai.repl.loop.parallel_exec — parallel agent execution helpers for one turn.

Two entry points are provided:

* ``run_parallel_configs``   — runs all PARALLEL_CONFIGS agents (named configs
                               set up via /parallel).  Extracted from the large
                               ``if PARALLEL_CONFIGS:`` block in cli.py.
* ``run_simple_parallel``    — runs ``parallel_count`` identical copies of the
                               currently active agent type.  Extracted from the
                               ``if parallel_count > 1:`` block in cli.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional


# ---------------------------------------------------------------------------
# PARALLEL_CONFIGS path
# ---------------------------------------------------------------------------

def run_parallel_configs(user_input: str, agent: Any, console: Any) -> None:
    """Run all PARALLEL_CONFIGS agents for one user turn.

    Parameters
    ----------
    user_input: the current user message
    agent:      the currently active (single-agent) agent instance used to seed
                parallel histories when no isolated copies exist yet
    console:    Rich Console for debug / error output

    Raises
    ------
    KeyboardInterrupt — if the user presses Ctrl+C.  Parallel histories are
                        saved before re-raising so the next session can resume.
    """
    from cai.agents import get_agent_by_name
    from cai.repl.commands.parallel import (
        PARALLEL_AGENT_INSTANCES,
        PARALLEL_CONFIGS,
        ParallelConfig,
    )
    from cai.repl.loop.agent_sync import update_agent_models_recursively
    from cai.sdk.agents import Runner
    from cai.sdk.agents.parallel_isolation import (
        PARALLEL_ISOLATION,
        run_parallel_agents as _run_parallel_agents,
        save_parallel_histories as _save_parallel_histories,
    )
    from cai.sdk.agents.shutdown_coordinator import SHUTDOWN_COORDINATOR

    # ---------------------------------------------------------------- IDs
    agent_ids = [
        config.id or f"P{idx}" for idx, config in enumerate(PARALLEL_CONFIGS, 1)
    ]

    # ------------------------------------------- transfer/check histories
    already_has_histories = False
    if PARALLEL_ISOLATION.is_parallel_mode():
        for aid in agent_ids:
            if PARALLEL_ISOLATION.get_isolated_history(aid):
                already_has_histories = True
                break

    if not already_has_histories:
        current_history: list = []
        if hasattr(agent, "model") and hasattr(agent.model, "message_history"):
            current_history = agent.model.message_history
        elif hasattr(agent, "name"):
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            current_history = AGENT_MANAGER.get_message_history(agent.name)

        transfer_to_all = True
        if "different contexts" in os.getenv("CAI_PATTERN_DESCRIPTION", "").lower():
            transfer_to_all = False

        if transfer_to_all:
            PARALLEL_ISOLATION.transfer_to_parallel(
                current_history, len(PARALLEL_CONFIGS), agent_ids
            )
        else:
            PARALLEL_ISOLATION._parallel_mode = True
            if current_history and agent_ids:
                PARALLEL_ISOLATION.clear_all_histories()
                PARALLEL_ISOLATION.replace_isolated_history(
                    agent_ids[0], current_history.copy()
                )
                for aid in agent_ids[1:]:
                    PARALLEL_ISOLATION.replace_isolated_history(aid, [])
    else:
        PARALLEL_ISOLATION._parallel_mode = True

    # ---------------------------------------------- ensure instances exist
    from cai.agents import get_available_agents

    for idx, config in enumerate(PARALLEL_CONFIGS, 1):
        instance_key = (config.agent_name, idx)
        if instance_key not in PARALLEL_AGENT_INSTANCES:
            base_agent = get_available_agents().get(config.agent_name.lower())
            if base_agent:
                agent_display_name = getattr(base_agent, "name", config.agent_name)
                custom_name = f"{str(agent_display_name)} #{idx}"
                model_to_use = config.model or os.getenv("CAI_MODEL", "alias1")
                instance_agent = get_agent_by_name(
                    config.agent_name,
                    custom_name=str(custom_name),
                    model_override=model_to_use,
                    agent_id=(config.id or ""),
                )
                PARALLEL_AGENT_INSTANCES[instance_key] = instance_agent

    # ------------------------------------------- per-agent runner closure
    async def run_agent_instance(config: ParallelConfig, input_text: str):
        """Run a single agent instance with its own configuration."""
        instance_agent: Any = None
        agent_id: Optional[str] = None
        try:
            instance_number = PARALLEL_CONFIGS.index(config) + 1
            agent_id = config.id or f"P{instance_number}"
            instance_key = (config.agent_name, instance_number)
            instance_agent = PARALLEL_AGENT_INSTANCES.get(instance_key)

            if not instance_agent:
                from cai.agents.patterns import get_pattern

                agent_display_name = None
                actual_agent_name = config.agent_name

                if config.agent_name.endswith("_pattern"):
                    pattern = get_pattern(config.agent_name)
                    if pattern and hasattr(pattern, "entry_agent"):
                        agent_display_name = getattr(
                            pattern.entry_agent, "name", config.agent_name
                        )
                else:
                    base_agent = get_available_agents().get(config.agent_name.lower())
                    agent_display_name = (
                        base_agent.name if base_agent else config.agent_name
                    )

                if not config.agent_name.endswith("_pattern"):
                    custom_name = f"{str(agent_display_name)} #{instance_number}"
                else:
                    custom_name = (
                        str(agent_display_name)
                        if agent_display_name is not None
                        else config.agent_name
                    )

                model_to_use = config.model or os.getenv("CAI_MODEL", "alias1")
                instance_agent = get_agent_by_name(
                    actual_agent_name,
                    custom_name=str(custom_name)
                    if custom_name is not None
                    else config.agent_name,
                    model_override=model_to_use,
                    agent_id=(config.id or ""),
                )
                PARALLEL_AGENT_INSTANCES[instance_key] = instance_agent

            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            agent_display_name = getattr(instance_agent, "name", config.agent_name)
            AGENT_MANAGER.set_parallel_agent(
                agent_id, instance_agent, agent_display_name or config.agent_name
            )

            model_to_use = config.model or os.getenv("CAI_MODEL", "alias1")
            if model_to_use:
                update_agent_models_recursively(instance_agent, model_to_use)

            instance_input = config.prompt if config.prompt else input_text
            result = await Runner.run(instance_agent, instance_input)

            try:
                from cai.util import cli_print_tool_output, finish_tool_streaming

                agent_display_name = getattr(instance_agent, "name", config.agent_name)
                streaming_sessions = getattr(
                    cli_print_tool_output, "_streaming_sessions", {}
                )
                for session_id, session_info in list(streaming_sessions.items()):
                    if session_info.get(
                        "agent_name"
                    ) == agent_display_name and not session_info.get("is_complete", False):
                        finish_tool_streaming(
                            tool_name=session_info.get("tool_name", "unknown"),
                            args=session_info.get("args", {}),
                            output=session_info.get(
                                "current_output", "Tool execution completed"
                            ),
                            call_id=session_id,
                            execution_info={"status": "completed", "is_final": True},
                            token_info={
                                "agent_name": agent_display_name,
                                "agent_id": getattr(
                                    instance_agent.model, "agent_id", None
                                )
                                if hasattr(instance_agent, "model")
                                else None,
                            },
                        )
            except Exception:
                pass

            if instance_agent and agent_id:
                if hasattr(instance_agent, "model") and hasattr(
                    instance_agent.model, "message_history"
                ):
                    PARALLEL_ISOLATION.replace_isolated_history(
                        agent_id, instance_agent.model.message_history
                    )
            return (config, result)

        except asyncio.CancelledError:
            try:
                from cai.util import cleanup_agent_streaming_resources

                if instance_agent:
                    dn = getattr(instance_agent, "name", config.agent_name)
                    cleanup_agent_streaming_resources(dn or config.agent_name)
            except Exception:
                pass

            if instance_agent and agent_id:
                if hasattr(instance_agent, "model") and hasattr(
                    instance_agent.model, "message_history"
                ):
                    PARALLEL_ISOLATION.replace_isolated_history(
                        agent_id, instance_agent.model.message_history
                    )
            raise

        except Exception as exc:
            try:
                from cai.util import cleanup_agent_streaming_resources

                if instance_agent:
                    dn = getattr(instance_agent, "name", config.agent_name)
                    cleanup_agent_streaming_resources(dn or config.agent_name)
            except Exception:
                pass

            if instance_agent and agent_id:
                if hasattr(instance_agent, "model") and hasattr(
                    instance_agent.model, "message_history"
                ):
                    PARALLEL_ISOLATION.replace_isolated_history(
                        agent_id, instance_agent.model.message_history
                    )

            _logger = logging.getLogger(__name__)
            error_details = f"Error in {config.agent_name}"
            if config.model:
                error_details += f" (model: {config.model})"
            error_details += f": {exc}"
            _logger.error(error_details, exc_info=True)

            if os.getenv("CAI_DEBUG", "1") == "2":
                console.print(f"[bold red]{error_details}[/bold red]")
            return (config, None)

    # --------------------------------------------------- dispatch all agents
    try:
        asyncio.run(_run_parallel_agents(PARALLEL_CONFIGS, user_input, run_agent_instance))
    except KeyboardInterrupt:
        try:
            _save_parallel_histories(PARALLEL_CONFIGS, PARALLEL_AGENT_INSTANCES)
        except Exception:
            pass
        try:
            targets = os.getenv("CAI_SHUTDOWN_TARGETS", "")
            targets_list = [t.strip() for t in targets.split(",") if t.strip()]
            SHUTDOWN_COORDINATOR.shutdown(
                sigterm_targets=targets_list if targets_list else None
            )
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# parallel_count > 1 path  (same agent type, N copies)
# ---------------------------------------------------------------------------

def run_simple_parallel(
    conversation_input: Any,
    agent: Any,
    console: Any,
    last_agent_type: str,
    parallel_count: int,
) -> None:
    """Run *parallel_count* copies of the current agent type.

    Results are added to ``agent.model.message_history`` in-place.
    """
    from cai.agents import get_agent_by_name, get_available_agents
    from cai.repl.loop.agent_sync import update_agent_models_recursively
    from cai.sdk.agents import Runner

    async def _run_instance(instance_number: int, conv: Any):
        try:
            base_agent = get_available_agents().get(last_agent_type.lower())
            agent_display_name = base_agent.name if base_agent else last_agent_type
            custom_name = f"{agent_display_name} #{instance_number + 1}"
            instance_agent = get_agent_by_name(
                last_agent_type,
                custom_name=custom_name,
                agent_id=f"P{instance_number + 1}",
            )

            if (
                hasattr(instance_agent, "model")
                and agent is not None
                and hasattr(agent, "model")
            ):
                if hasattr(instance_agent.model, "model") and hasattr(agent.model, "model"):
                    instance_specific = os.getenv(
                        f"CAI_{last_agent_type.upper()}_{instance_number + 1}_MODEL"
                    )
                    if instance_specific:
                        model_to_use: Any = instance_specific
                    else:
                        agent_specific = os.getenv(f"CAI_{last_agent_type.upper()}_MODEL")
                        model_to_use = (
                            agent_specific
                            if agent_specific
                            else getattr(agent.model, "model", None)
                        )
                    update_agent_models_recursively(instance_agent, model_to_use)

            result = await Runner.run(instance_agent, conv)
            return (instance_number, result)
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Error in instance %d: %s", instance_number, exc, exc_info=True
            )
            if os.getenv("CAI_DEBUG", "1") == "2":
                console.print(f"[bold red]Error in instance {instance_number}: {exc}[/bold red]")
            return (instance_number, None)

    async def _run_all():
        tasks = [_run_instance(i, conversation_input) for i in range(parallel_count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = []
        for res in results:
            if isinstance(res, tuple) and len(res) == 2:
                idx, r = res
                if r is not None and not isinstance(r, Exception):
                    valid.append((idx, r))
        return valid

    results = asyncio.run(_run_all())

    for _idx, result in results:
        if result and hasattr(result, "final_output") and result.final_output:
            agent.model.add_to_message_history(
                {"role": "assistant", "content": str(result.final_output)}
            )
