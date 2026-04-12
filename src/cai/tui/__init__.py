"""CAI TUI package.

Activated via:
    CAI_TUI=true cai
    cai --tui
"""

from .app import run_tui, run_tui_web

__all__ = ["run_tui", "run_tui_web"]
