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
from typing import Any


def resolve_api_base() -> str | None:
    """Return the resolved base URL for the active API server.

    Priority chain (highest → lowest):

    1. ``LOCAL_API_BASE``  — universal local server override (Ollama, vLLM,
       llama.cpp, …). Set this single variable to point every agent at a
       local endpoint without touching any provider-specific flag.
    2. ``OPENAI_API_BASE`` — OpenAI-compatible proxy base URL.
    3. ``OPENAI_BASE_URL`` — standard OpenAI SDK env var.
    4. ``None``            — fall through to library / provider default.
    """
    return (
        os.getenv("LOCAL_API_BASE")
        or os.getenv("OPENAI_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or None
    )


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
    _last_api_base: str | None = None,
) -> tuple[str, str]:
    """Resolve the active model and API base URL from env-vars, sync onto *agent*.

    Parameters
    ----------
    agent:           current agent instance
    last_model:      last model name that was applied
    last_agent_type: current agent-type key (used for per-agent model override)
    _last_api_base:  last resolved API base URL; when it changes the agent's
                     client is refreshed even if the model name is unchanged.

    Returns
    -------
    ``(current_model, updated_last_model)``
    """
    current_model = os.getenv("CAI_MODEL", "alias1")
    agent_specific_model = os.getenv(f"CAI_{last_agent_type.upper()}_MODEL")
    if agent_specific_model:
        current_model = agent_specific_model

    # Resolve the current API base using the universal priority chain.
    current_api_base = resolve_api_base()
    api_base_changed = current_api_base != _last_api_base

    if (current_model != last_model or api_base_changed) and hasattr(agent, "model"):
        try:
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            AGENT_MANAGER.sync_models(current_model)
            # After syncing the model name (or when LOCAL_API_BASE changed),
            # push a fresh AsyncOpenAI client built from the current
            # environment so that the base URL and API key are always
            # reflected (sync_models resets _client to None but does not
            # rebuild it with fresh env vars).
            _refresh_agent_client(AGENT_MANAGER.get_active_agent())
        except Exception:
            # Best-effort: if manager sync fails, try a direct attribute update
            try:
                agent.model.model = current_model
            except Exception:
                pass
        last_model = current_model

    return current_model, last_model


def _refresh_agent_client(agent: Any) -> None:
    """Rebuild *agent*'s OpenAI client from the current environment variables.

    Called after ``AGENT_MANAGER.sync_models()`` to ensure the active base URL
    and API key are applied to the new client.  The base URL is resolved via
    :func:`resolve_api_base` which honours the ``LOCAL_API_BASE`` →
    ``OPENAI_API_BASE`` → ``OPENAI_BASE_URL`` priority chain.
    """
    if agent is None:
        return
    model = getattr(agent, "model", None)
    if model is None:
        return
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return
    try:
        api_key = os.getenv("ALIAS_API_KEY") or os.getenv("OPENAI_API_KEY") or "sk-placeholder"
        base_url = resolve_api_base()
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        model._client = AsyncOpenAI(**client_kwargs)
    except Exception:
        pass


def switch_agent_if_needed(
    agent: Any,
    last_model: str,
    last_agent_type: str,
    current_model: str,
    console: Any,
) -> tuple[Any, str, str, bool]:
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
                        all_tasks = TaskType.all_tasks()
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
                        current_task = TaskType.current_task()
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
