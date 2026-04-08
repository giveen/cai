"""Environment sanitizer utilities extracted from `cli.py`.

This module centralizes early startup behavior that must run before
heavy imports: loading `.env`, configuring warnings, and applying
comprehensive logging filters to reduce noisy output during tests
and normal runs.
"""

from __future__ import annotations

import logging

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore

# Import the helpers from their new locations. bootstrap remains a
# small orchestrator that configures the environment and delegates
# the implementation details to utility modules.
from cai.repl.ui.logging_filters import install_comprehensive_error_filter  # type: ignore
from cai.util.warnings import install_warning_handler  # type: ignore


def initialize_env() -> None:
    """Initialize environment: load .env, configure warnings and logging filters."""
    # Load .env early if python-dotenv is available
    if load_dotenv is not None:
        try:
            load_dotenv(override=True)
        except Exception:
            # Best-effort: don't fail startup if .env can't be loaded
            pass
    # Delegate warnings and logging filter installation to their modules.
    try:
        install_warning_handler()
    except Exception:
        logging.getLogger(__name__).debug("Failed to install warning handler", exc_info=True)

    try:
        install_comprehensive_error_filter()
    except Exception:
        logging.getLogger(__name__).debug("Failed to install logging filters", exc_info=True)


__all__ = ["initialize_env"]
