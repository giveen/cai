"""cai.repl.loop.agent_sync — per-iteration model and agent-type sync helpers.

Also exports ``update_agent_models_recursively`` which is shared between the
CLI main loop and the parallel execution helpers.

Two functions are extracted from the inner ``try:`` block of the CLI main loop:

* ``update_agent_models_recursively`` — propagates a model name change to an
                                        agent and all its handoff agents.
* ``sync_model``            — resolves the active model from env-vars and
                              propagates any change to the current agent.
* ``switch_agent_if_needed`` — recreates the agent when ``CAI_AGENT_TYPE`` or
                               ``CAI_AGENT_SWITCH_HANDLED`` changes, and
                               cancels stray asyncio tasks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Tuple


def update_agent_models_recursively(agent: Any, model_to_use: str) -> None:
    """Propagate *model_to_use* to *agent* and all its handoff agents.

    Prefers ``AGENT_MANAGER.sync_models`` when available; falls back to
    direct attribute updates.
    """
    try:
        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

        AGENT_MANAGER.sync_models(model_to_use, target_agent=agent)
        return
    except Exception:
        pass

    try:
        if hasattr(agent, "model") and agent.model is not None:
            try:
                agent.model.model = model_to_use
            except Exception:
                pass

        handoffs = getattr(agent, "handoff_agents", None)
        if handoffs:
            for h in handoffs:
                try:
                    if hasattr(h, "model") and h.model is not None:
                        h.model.model = model_to_use
                except Exception:
                    pass
    except Exception:
        pass


def sync_model(
    agent: Any,
    last_model: str,
    last_agent_type: str,
) -> Tuple[str, str]:
    """Resolve the active model from env-vars and sync it onto *agent*.

    Parameters
    ----------
    agent:          current agent instance
    last_model:     last model name that was applied
    last_agent_type: current agent-type key (used for per-agent model override)

    Returns
    -------
    ``(current_model, updated_last_model)``
    """
    current_model = os.getenv("CAI_MODEL", "alias1")
    agent_specific_model = os.getenv(f"CAI_{last_agent_type.upper()}_MODEL")
    if agent_specific_model:
        current_model = agent_specific_model

    if current_model != last_model and hasattr(agent, "model"):
        try:
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            AGENT_MANAGER.sync_models(current_model)
        except Exception:
            # Best-effort: if manager sync fails, try a direct attribute update
            try:
                agent.model.model = current_model
            except Exception:
                pass
        last_model = current_model

    return current_model, last_model


def switch_agent_if_needed(
    agent: Any,
    last_model: str,
    last_agent_type: str,
    current_model: str,
    console: Any,
) -> Tuple[Any, str, str, bool]:
    """Recreate the agent when ``CAI_AGENT_TYPE`` has changed.

    Parameters
    ----------
    agent:          current agent instance
    last_model:     last model name that was applied
    last_agent_type: last agent-type key that was active
    current_model:  freshly resolved model name (from ``sync_model``)
    console:        Rich Console for debug output

    Returns
    -------
    ``(agent, last_model, last_agent_type, should_continue)``

    ``should_continue`` is ``True`` when the loop iteration should be
    restarted with ``continue`` (i.e. the **/agent** command pre-handled the
    switch via ``CAI_AGENT_SWITCH_HANDLED``).
    """
    from cai.repl.loop.session import get_agent_short_name

    current_agent_type = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")

    if current_agent_type == last_agent_type:
        return agent, last_model, last_agent_type, False

    # ------------------------------------------------------ pre-handled switch
    # The /agent command already switched the agent and set the flag.
    if os.environ.get("CAI_AGENT_SWITCH_HANDLED") == "1":
        os.environ["CAI_AGENT_SWITCH_HANDLED"] = "0"

        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

        if hasattr(AGENT_MANAGER, "_current_agent_strong_ref"):
            agent = AGENT_MANAGER._current_agent_strong_ref
            delattr(AGENT_MANAGER, "_current_agent_strong_ref")
        else:
            agent = AGENT_MANAGER.get_active_agent()

        if agent:
            last_agent_type = current_agent_type
        else:
            from cai.agents import get_agent_by_name

            agent = get_agent_by_name(current_agent_type, agent_id="P1")
            last_agent_type = current_agent_type
            agent_name = agent.name if hasattr(agent, "name") else current_agent_type
            AGENT_MANAGER.set_active_agent(agent, agent_name, "P1")

        return agent, last_model, last_agent_type, True  # signal loop `continue`

    # --------------------------------------------------------- full agent switch
    try:
        from cai.agents import get_agent_by_name
        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER
        from cai.util import COST_TRACKER

        agent = get_agent_by_name(current_agent_type, agent_id="P1")
        last_agent_type = current_agent_type

        COST_TRACKER.reset_agent_costs()

        agent_name = agent.name if hasattr(agent, "name") else current_agent_type
        current_active_name = AGENT_MANAGER._active_agent_name

        if current_active_name == agent_name:
            existing = AGENT_MANAGER.get_active_agent()
            if existing:
                agent = existing
        else:
            AGENT_MANAGER.switch_to_single_agent(agent, agent_name)

        if hasattr(agent, "model"):
            if hasattr(agent.model, "disable_rich_streaming"):
                agent.model.disable_rich_streaming = False
            if hasattr(agent.model, "suppress_final_output"):
                agent.model.suppress_final_output = False

            agent_specific_model = os.getenv(f"CAI_{current_agent_type.upper()}_MODEL")
            model_to_apply = agent_specific_model if agent_specific_model else current_model

            try:
                AGENT_MANAGER.sync_models(model_to_apply, target_agent=agent)
            except Exception:
                try:
                    agent.model.model = model_to_apply
                except Exception:
                    pass
            last_model = model_to_apply

            if hasattr(agent.model, "set_agent_name"):
                agent.model.set_agent_name(get_agent_short_name(agent))

        # ----------------------------------------- cancel stray asyncio tasks
        try:
            try:
                all_tasks = asyncio.all_tasks()
            except Exception:
                TaskType = getattr(asyncio, "Task", None)
                if TaskType is not None and getattr(TaskType, "all_tasks", None):
                    try:
                        all_tasks = getattr(TaskType, "all_tasks")()
                    except Exception:
                        all_tasks = set()
                else:
                    all_tasks = set()

            try:
                current_task = asyncio.current_task()
            except Exception:
                TaskType = getattr(asyncio, "Task", None)
                current_task = None
                if TaskType is not None and getattr(TaskType, "current_task", None):
                    try:
                        current_task = getattr(TaskType, "current_task")()
                    except Exception:
                        current_task = None

            for task in list(all_tasks):
                if task is not current_task and not getattr(task, "done", lambda: False)():
                    try:
                        task.cancel()
                    except Exception:
                        pass
        except Exception:
            pass

    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.debug("Error switching agent: %s", exc)
        if os.getenv("CAI_DEBUG", "1") == "2":
            console.print(f"[red]Error switching agent: {exc}[/red]")

    return agent, last_model, last_agent_type, False
