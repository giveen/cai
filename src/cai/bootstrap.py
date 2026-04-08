"""Environment sanitizer utilities extracted from `cli.py`.

This module centralizes early startup behavior that must run before
heavy imports: loading `.env`, configuring warnings, and applying
comprehensive logging filters to reduce noisy output during tests
and normal runs.
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore


def custom_warning_handler(message: Any, category: Any, filename: str, lineno: int, file=None, line=None):
    """Custom warning handler used to reduce noise unless debug mode enabled."""
    # Only show warnings in debug mode (CAI_DEBUG==2)
    if os.getenv("CAI_DEBUG", "1") == "2":
        warnings.showwarning(message, category, filename, lineno, file, line)
    # Otherwise, silently ignore


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


def suppress_aiohttp_warnings() -> None:
    """Suppress aiohttp-specific warnings about unclosed sessions if available."""
    try:
        aiohttp_logger = logging.getLogger("aiohttp")
        aiohttp_logger.setLevel(logging.ERROR)

        aiohttp_client_logger = logging.getLogger("aiohttp.client")
        aiohttp_client_logger.setLevel(logging.ERROR)

        aiohttp_connector_logger = logging.getLogger("aiohttp.connector")
        aiohttp_connector_logger.setLevel(logging.ERROR)
    except Exception:
        # aiohttp not present or other issue — ignore
        pass


def initialize_env() -> None:
    """Initialize environment: load .env, configure warnings and logging filters."""
    # Load .env early if python-dotenv is available
    if load_dotenv is not None:
        try:
            load_dotenv(override=True)
        except Exception:
            # Best-effort: don't fail startup if .env can't be loaded
            pass

    # Configure Python warnings BEFORE heavy imports
    warnings.showwarning = custom_warning_handler

    # Suppress ALL warnings in non-debug mode
    if os.getenv("CAI_DEBUG", "1") != "2":
        warnings.filterwarnings("ignore")
        os.environ.setdefault("PYTHONWARNINGS", "ignore")

    # Apply comprehensive logging filter
    comprehensive_filter = ComprehensiveErrorFilter()

    loggers_to_configure = [
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

    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.addFilter(comprehensive_filter)
        if logger_name in ["asyncio", "anyio", "anyio._backends._asyncio"]:
            logger.setLevel(logging.ERROR)
        else:
            logger.setLevel(logging.WARNING)

    # Additional global warning filters to reduce noise
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
        # Best-effort
        pass

    # Make warning system less verbose if not explicitly configured
    if not sys.warnoptions:
        warnings.simplefilter("ignore", RuntimeWarning)
        warnings.simplefilter("ignore", ResourceWarning)

    # Suppress aiohttp warnings
    suppress_aiohttp_warnings()


__all__ = ["initialize_env", "custom_warning_handler", "ComprehensiveErrorFilter"]
