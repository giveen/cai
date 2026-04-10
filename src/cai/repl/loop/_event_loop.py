"""Session-scoped event loop singleton for the CAI REPL.

``asyncio.run()`` creates a brand-new event loop and **closes it** as soon as
the coroutine finishes.  Any async resource (e.g. an httpx connection pool)
that is still alive at GC time then tries to schedule a callback on the now-
closed loop, which raises::

    RuntimeError: Event loop is closed

Keeping a single loop alive for the whole REPL session prevents this because
the loop is never torn down between agent turns.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

_T = TypeVar("_T")

_repl_loop: asyncio.AbstractEventLoop | None = None


def get_repl_loop() -> asyncio.AbstractEventLoop:
    """Return (and lazily create) the session-scoped event loop."""
    global _repl_loop
    if _repl_loop is None or _repl_loop.is_closed():
        _repl_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_repl_loop)
    return _repl_loop


def run_async(coro: Any) -> Any:
    """Run *coro* on the persistent REPL event loop.

    Drop-in replacement for ``asyncio.run()`` that reuses the same loop so
    that async resources (httpx clients, etc.) can clean up without hitting
    a closed loop.

    If the loop happens to already be running (nested async context, e.g.
    inside an `asyncio.run()` that imported us), fall back to creating a
    fresh loop rather than raising ``RuntimeError: This event loop is already
    running``.
    """
    loop = get_repl_loop()
    if loop.is_running():
        # Cannot call run_until_complete on a running loop.  Create a fresh
        # dedicated loop for this call so the caller doesn't crash silently.
        import sys

        sys.stderr.write(
            "[CAI WARNING] run_async: event loop already running — "
            "spinning a fresh loop for this call.\n"
        )
        sys.stderr.flush()
        _tmp = asyncio.new_event_loop()
        try:
            return _tmp.run_until_complete(coro)
        finally:
            try:
                _tmp.close()
            except Exception:
                pass
    return loop.run_until_complete(coro)
