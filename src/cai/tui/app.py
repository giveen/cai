"""Lightweight CAI TUI — compatibility wrapper.

This module re-exports the public API from app_impl to keep src/cai/tui/app.py small.
"""

from __future__ import annotations

from typing import Any

from cai.tui import app_impl as _impl

# Common top-level aliases
CAIApp = _impl.CAIApp


def run_tui(agent: Any | None = None, initial_prompt: str | None = None) -> None:
    return _impl.run_tui(agent=agent, initial_prompt=initial_prompt)


def run_tui_web(
    agent: Any | None = None,
    initial_prompt: str | None = None,
    host: str | None = None,
    port: int | None = None,
    detach_logging: bool = False,
):
    """Run the TUI served over HTTP using textual-web (best-effort).

    This attempts to use the `textual-web` package if available. If not
    installed, a helpful message is printed and the call returns False.
    """
    try:
        return _impl.run_tui_web(
            agent=agent,
            initial_prompt=initial_prompt,
            host=host,
            port=port,
            detach_logging=detach_logging,
        )
    except Exception:
        # Bubble up minimal fallback information to the caller.
        raise


CONFIG_FILE = _impl.CONFIG_FILE
_pretty_name = _impl._pretty_name
_BANNER_LINES = _impl._BANNER_LINES


def __getattr__(name: str):
    """Lazy attribute proxy to the full implementation module.

    This allows callers to import helpers (e.g. PromptModal) from
    `cai.tui.app` for backwards compatibility while keeping this file small.
    """
    try:
        return getattr(_impl, name)
    except AttributeError:
        raise AttributeError(f"module {__name__} has no attribute {name}")


def __dir__() -> list[str]:
    names = list(globals().keys())
    try:
        impl_names = [n for n in dir(_impl) if not n.startswith("_")]
    except Exception:
        impl_names = []
    return sorted(set(names + impl_names))


__all__ = [
    "run_tui",
    "CAIApp",
    "CONFIG_FILE",
    "_pretty_name",
    "_BANNER_LINES",
]
