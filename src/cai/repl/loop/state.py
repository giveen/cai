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
from typing import Any, Optional


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
    _post_compact_input: Optional[str] = None
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
    history_file: Optional[str] = None
    session_logger: Any = field(default=None, repr=False)

    # --------------------------------------------------------- CTF integration
    force_until_flag: bool = False
    initial_prompt: Optional[str] = None
