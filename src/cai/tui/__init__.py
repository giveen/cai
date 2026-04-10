"""CAI TUI package.

Activated via:
    CAI_TUI=true cai
    cai --tui
"""

from .app import run_tui

__all__ = ["run_tui"]
