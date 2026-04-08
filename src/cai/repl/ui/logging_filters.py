"""Logging filters used by the REPL/UI to reduce noisy logs.

This module contains `ComprehensiveErrorFilter` moved out of
`cai.bootstrap` so the bootstrap module can act as a lightweight
orchestrator while the filter implementation lives near UI/logging
concerns.
"""
from __future__ import annotations

import logging


class ComprehensiveErrorFilter(logging.Filter):
    """Filter to suppress various expected errors and warnings in logs."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - simple wrapper
        msg = record.getMessage().lower()

        suppress_patterns = [
            "asynchronous generator",
            "asyncgen",
            "closedresourceerror",
            "didn't stop after athrow",
            "didnt stop after athrow",
            "generator didn't stop",
            "cancel scope",
            "unhandled errors in a taskgroup",
            "error in post_writer",
            "was never awaited",
            "connection error while setting up",
            "error closing",
            "anyio._backends",
            "httpx_sse",
            "connection reset by peer",
            "broken pipe",
            "connection aborted",
            "runtime warning",
            "runtimewarning",
            "coroutine",
            "task was destroyed",
            "event loop is closed",
            "session is closed",
            "unclosed client session",
            "unclosed connector",
            "client_session:",
            "connector:",
            "connections:",
        ]

        for pattern in suppress_patterns:
            if pattern in msg:
                return False

        # SSE cleanup messages
        if "sse" in msg and any(word in msg for word in ["cleanup", "closing", "shutdown", "closed"]):
            return False

        # MCP specific errors we handle
        if "error invoking mcp tool" in msg and "closedresourceerror" in msg:
            return False

        # Downgrade some MCP reconnect messages to DEBUG
        if "mcp server session not found" in msg or "successfully reconnected to mcp server" in msg:
            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"

        return True


__all__ = ["ComprehensiveErrorFilter"]


def install_comprehensive_error_filter(logger_names: list[str] | None = None) -> None:
    """Install the ``ComprehensiveErrorFilter`` on a set of loggers.

    The function is a small convenience wrapper so callers can opt-in to
    the same filter set without importing the implementation class.
    """
    if logger_names is None:
        logger_names = [
            "openai.agents",
            "mcp.client.sse",
            "httpx",
            "httpx_sse",
            "mcp",
            "asyncio",
            "anyio",
            "anyio._backends._asyncio",
            "cai.sdk.agents",
            "aiohttp",
        ]

    try:
        comprehensive_filter = ComprehensiveErrorFilter()
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            logger.addFilter(comprehensive_filter)
            if logger_name in ["asyncio", "anyio", "anyio._backends._asyncio"]:
                logger.setLevel(logging.ERROR)
            else:
                logger.setLevel(logging.WARNING)
    except Exception:
        # Best-effort: don't crash the application during bootstrap
        logging.getLogger(__name__).debug("Failed to install comprehensive logging filter", exc_info=True)


__all__ = ["ComprehensiveErrorFilter", "install_comprehensive_error_filter"]
