"""Minimal TUI package for CAI.

Expose a `run_tui` helper that attempts to start a Textual app and
falls back to a simple Rich-based live panel if Textual isn't available.
"""
from .app import run_tui

__all__ = ["run_tui"]
