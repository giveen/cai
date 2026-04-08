"""cai.repl.loop.input_handler — idle-timer-aware user input acquisition.

``get_next_input`` is extracted from the ``try:`` block of the CLI main loop.
It decides which source to use for the next user message (initial prompt,
auto-compact replay, interactive readline, or CTF script), accumulates idle
time, and normalizes the returned string.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple


def get_next_input(
    force_until_flag: bool,
    ctf_init: int,
    use_initial_prompt: bool,
    initial_prompt: Optional[str],
    _post_compact_input: Optional[str],
    command_completer: Any,
    kb: Any,
    history_file: Optional[str],
    current_text: list,
    messages_ctf: str,
    idle_time: float,
    idle_start_time: float,
) -> Tuple[str, bool, Optional[str], int, float]:
    """Determine the next user input and return updated state.

    Priority (when not in CTF-force mode):
    1. ``initial_prompt``     — first iteration only
    2. ``_post_compact_input`` — injected by auto-compact replay
    3. Interactive readline prompt

    In CTF-force mode the pre-loaded ``messages_ctf`` string is used and
    ``ctf_init`` is set to ``1`` so the loop switches to interactive mode
    on the next iteration.

    Also accumulates ``idle_time`` from ``idle_start_time``, stops the idle
    timer, and starts the active timer.

    Returns
    -------
    ``(user_input, use_initial_prompt, _post_compact_input, ctf_init, idle_time)``
    """
    from cai.repl.ui.prompt import get_user_input
    from cai.repl.ui.toolbar import get_toolbar_with_refresh
    from cai.util import start_active_timer, stop_idle_timer

    if not force_until_flag and ctf_init != 0:
        if use_initial_prompt:
            user_input: Optional[str] = initial_prompt
            use_initial_prompt = False  # Only use it once
        elif _post_compact_input is not None:
            # Auto-compact just ran — replay the last task so the agent
            # continues working without waiting for human input.
            user_input = _post_compact_input
            _post_compact_input = None
        else:
            # Get user input with command completion and history
            user_input = get_user_input(
                command_completer, kb, history_file, get_toolbar_with_refresh, current_text
            )
    else:
        user_input = messages_ctf
        ctf_init = 1

    idle_time += time.time() - idle_start_time

    # Stop measuring user idle time and start measuring active time
    stop_idle_timer()
    start_active_timer()

    # Normalise: ensure a non-empty string is always returned
    if user_input is None:
        user_input = ""
    if not user_input.strip():
        user_input = "User input is empty, maybe wants to continue"

    return user_input, use_initial_prompt, _post_compact_input, ctf_init, idle_time
