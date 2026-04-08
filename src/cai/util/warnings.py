"""Warning helpers used to control Python warnings output.

This module contains `custom_warning_handler` previously defined in
`cai.bootstrap`. Keeping it under `cai.util` groups utility helpers
and avoids bloating the early-bootstrap orchestrator.
"""

from __future__ import annotations

import os
import sys
import warnings
from typing import Any


def custom_warning_handler(
    message: Any, category: Any, filename: str, lineno: int, file=None, line=None
):
    """Custom warning handler used to reduce noise unless debug mode enabled.

    Only shows warnings when `CAI_DEBUG==2` to avoid noisy test and CLI
    output during normal operation.
    """
    if os.getenv("CAI_DEBUG", "1") == "2":
        warnings.showwarning(message, category, filename, lineno, file, line)


def install_warning_handler() -> None:
    """Install the module's custom warning handler and apply common filters.

    This centralizes warning configuration so callers (e.g. `bootstrap`) can
    call a single function instead of re-implementing filtering rules.
    """
    # Replace Python's warning display function with our quieter handler
    try:
        warnings.showwarning = custom_warning_handler
    except Exception:
        pass

    # If not in debug mode, reduce noise broadly
    if os.getenv("CAI_DEBUG", "1") != "2":
        try:
            warnings.filterwarnings("ignore")
            os.environ.setdefault("PYTHONWARNINGS", "ignore")
        except Exception:
            pass

    # Apply common ignores (best-effort)
    try:
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        warnings.filterwarnings("ignore", category=ResourceWarning)
        warnings.filterwarnings("ignore", message=".*asynchronous generator.*")
        warnings.filterwarnings("ignore", message=".*was never awaited.*")
        warnings.filterwarnings("ignore", message=".*didn't stop after athrow.*")
        warnings.filterwarnings("ignore", message=".*cancel scope.*")
        warnings.filterwarnings("ignore", message=".*coroutine.*was never awaited.*")
        warnings.filterwarnings("ignore", message=".*generator.*didn't stop.*")
        warnings.filterwarnings("ignore", message=".*Task was destroyed.*")
        warnings.filterwarnings("ignore", message=".*Event loop is closed.*")
        warnings.filterwarnings("ignore", message=".*Unclosed client session.*")
        warnings.filterwarnings("ignore", message=".*Unclosed connector.*")
    except Exception:
        pass

    # When python is launched without -W flags, make some sensible defaults
    try:
        if not sys.warnoptions:
            warnings.simplefilter("ignore", RuntimeWarning)
            warnings.simplefilter("ignore", ResourceWarning)
    except Exception:
        pass


__all__ = ["custom_warning_handler", "install_warning_handler"]
