"""cai.repl.loop.session — one-time session initialization.

``initialize_session`` is extracted from a ~120-line block that previously
sat at the top of ``_run_cai_cli_impl`` in ``cai.cli``.  It encapsulates
every startup side-effect (cost/agent reset, UI-kit construction, session
recording, RAG pre-loading, banner display) and returns the five constructed
objects the main loop needs.

Also exports ``get_agent_short_name``, a tiny helper that returns the
display name of an agent object.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Tuple


def get_agent_short_name(agent: Any) -> str:
    """Return the display name of *agent*, falling back to ``"Agent"``."""
    if hasattr(agent, "name"):
        return agent.name
    return "Agent"


def initialize_session(
    starting_agent: Any,
    console: Any,
    last_agent_type: str,
) -> Tuple[Any, list, Any, Optional[str], Any]:
    """Run one-time session startup and return five UI/session objects.

    Side-effects
    ------------
    * Loads ``.env`` and sets up env/warnings via ``initialize_env``
    * Resets ``COST_TRACKER`` and ``AGENT_MANAGER`` *(required)*
    * Constructs the input UI kit (completer, key-bindings) *(required)*
    * Sets up session logging and starts ``GLOBAL_USAGE_TRACKER`` *(required)*
    * Loads WakeupIndex and TripleStore summaries *(best-effort)*
    * Displays the banner and quick-guide
    * Configures model flags on *starting_agent*

    Returns
    -------
    ``(command_completer, current_text, kb, history_file, session_logger)``
    """
    # Guarantee .env is loaded and proxy settings (OPENAI_API_BASE,
    # OPENAI_API_KEY, etc.) are visible before the first agent turn, even when
    # this module is imported without going through cli.py.
    try:
        from cai.bootstrap import initialize_env
        initialize_env()
    except Exception:
        pass

    # ------------------------------------------------------------------ agents
    from cai.util import COST_TRACKER

    COST_TRACKER.reset_agent_costs()

    from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

    AGENT_MANAGER.reset_registry()
    starting_agent_name = getattr(starting_agent, "name", last_agent_type)
    AGENT_MANAGER.switch_to_single_agent(starting_agent, starting_agent_name)

    # ----------------------------------------------------------------- UI kit
    from cai.repl.commands import FuzzyCommandCompleter
    from cai.repl.ui.keybindings import create_key_bindings
    from cai.repl.ui.logging import setup_session_logging
    from cai.sdk.agents.global_usage_tracker import GLOBAL_USAGE_TRACKER
    from cai.sdk.agents.run_to_jsonl import get_session_recorder

    command_completer = FuzzyCommandCompleter()
    current_text: list = [""]
    kb = create_key_bindings(current_text)

    history_file = setup_session_logging()
    session_logger = get_session_recorder()
    assert session_logger is not None

    GLOBAL_USAGE_TRACKER.start_session(
        session_id=session_logger.session_id,
        agent_name=None,  # Will be updated when agent is selected
    )

    # --------------------------------------------------------- data pre-load
    # WakeupIndex (best-effort)
    try:
        from cai.rag.summaries import load_summaries_for_session
        from cai.rag.wakeup_store import get_global_wakeup_index

        wakeup_idx = get_global_wakeup_index()
        try:
            count = load_summaries_for_session(
                session_id=session_logger.session_id,
                palace_texts=None,
                wakeup_index=wakeup_idx,
                regenerate_if_missing=False,
            )
        except Exception:
            count = 0

        if os.getenv("CAI_DEBUG", "1") == "2":
            print(
                f"Loaded {count} wakeup summaries into WakeupIndex for session "
                f"{session_logger.session_id}"
            )
    except Exception:
        # Best-effort: don't fail session startup if wakeup loading fails
        pass

    # TripleStore contradiction check (best-effort)
    try:
        from cai.rag.triplestore_store import get_global_triplestore

        ts = get_global_triplestore()
        try:
            window_sec = int(
                os.getenv("CAI_TRIPLESTORE_CONTRADICTION_WINDOW_SECONDS", str(24 * 3600))
            )
        except Exception:
            window_sec = 24 * 3600
        try:
            contradictions = ts.detect_contradictions(window_seconds=window_sec)
            n = len(contradictions)
            if os.getenv("CAI_DEBUG", "1") == "2":
                print(
                    f"TripleStore: detected {n} contradictions in last {window_sec} seconds"
                )
            logging.getLogger(__name__).info("TripleStore startup contradictions=%d", n)
        except Exception:
            # Best-effort: do not fail startup for triple-store checks
            pass
    except Exception:
        # Best-effort: do not fail session startup if triplestore init fails
        pass

    # ------------------------------------------------------------- UI: banner
    from cai.repl.ui.banner import display_banner, display_quick_guide

    display_banner(console)
    print("\n")
    display_quick_guide(console)

    # -------------------------------------- notify auto-compact status if set
    _sc_model = os.getenv("CAI_SUPPORT_MODEL")
    _sc_interval = os.getenv("CAI_SUPPORT_INTERVAL")
    if _sc_model and _sc_interval:
        try:
            console.print(
                f"[bold cyan]🗜  Auto-compact enabled: every {int(_sc_interval)} LLM responses "
                f"using {_sc_model}[/bold cyan]"
            )
        except ValueError:
            pass

    # ----------------------------------------------- configure agent's model
    if hasattr(starting_agent, "model"):
        if hasattr(starting_agent.model, "disable_rich_streaming"):
            starting_agent.model.disable_rich_streaming = False
        if hasattr(starting_agent.model, "suppress_final_output"):
            starting_agent.model.suppress_final_output = False
        if hasattr(starting_agent.model, "set_agent_name"):
            starting_agent.model.set_agent_name(get_agent_short_name(starting_agent))

    return command_completer, current_text, kb, history_file, session_logger
