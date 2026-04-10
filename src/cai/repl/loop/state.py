"""CliSessionState — all mutable variables that survive across loop iterations.

This dataclass is the single object passed between the extracted loop helpers
(initialize_session, sync_model_and_agent, get_next_input, etc.) so that none
of those functions need long parameter lists.

Usage in ``_run_cai_cli_impl`` (after the refactor is complete)::

    state = initialize_session(starting_agent, max_turns, initial_prompt)
    while True:
        sync_model_and_agent(state)
        user_input = get_next_input(state)
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CliSessionState:
    """All state that persists across iterations of the CLI main loop."""

    # ------------------------------------------------------------------ agents
    agent: Any
    """The currently active agent instance."""

    # ----------------------------------------------------------------- counters
    turn_count: int = 0
    idle_time: float = 0.0
    idle_start_time: float = 0.0

    # -------------------------------------------------- inter-iteration signals
    _post_compact_input: str | None = None
    """Replay message set by auto-compact so the agent can continue without
    waiting for the user to re-type the task."""

    _last_user_input: str = ""
    """Last raw input captured; used by ContextCompactedError retry handler."""

    _skip_auto_compact_after_interrupt: bool = False
    """Prevent auto-compact immediately after a KeyboardInterrupt."""

    # ----------------------------------------------- agent / model bookkeeping
    last_model: str = ""
    last_agent_type: str = ""
    parallel_count: int = 1
    use_initial_prompt: bool = False

    # --------------------------------------------------------- turn-limit state
    max_turns: float = float("inf")
    prev_max_turns: float = float("inf")
    turn_limit_reached: bool = False

    # ----------------------------------- UI / session objects (set by init step)
    console: Any = field(default=None, repr=False)
    command_completer: Any = field(default=None, repr=False)
    current_text: Any = field(default_factory=lambda: [""], repr=False)
    kb: Any = field(default=None, repr=False)
    history_file: str | None = None
    session_logger: Any = field(default=None, repr=False)

    # --------------------------------------------------------- CTF integration
    force_until_flag: bool = False
    initial_prompt: str | None = None

    # ------------------------------------------- Universal Local API settings
    local_api_base: str | None = field(default=None)
    """Resolved base URL for the active local API server.

    Priority chain (highest to lowest):
    1. ``LOCAL_API_BASE``  — universal local override
    2. ``OPENAI_API_BASE`` — OpenAI-compatible proxy
    3. ``OPENAI_BASE_URL`` — standard OpenAI env var
    4. ``None``            — use the library / provider default

    Set once at session initialisation by :func:`initialize_session` and
    re-evaluated on every iteration by :func:`sync_model` so that changing
    the variable during a session takes effect immediately.
    """
