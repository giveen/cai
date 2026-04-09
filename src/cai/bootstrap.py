"""Environment sanitizer utilities extracted from `cli.py`.

This module centralizes early startup behavior that must run before
heavy imports: loading `.env`, configuring warnings, and applying
comprehensive logging filters to reduce noisy output during tests
and normal runs.
"""

from __future__ import annotations

import logging
import os

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
    # Propagate LOCAL_* environment variables to OPENAI_* when not explicitly set.
    # This makes a developer-friendly default so a local LiteLLM/OpenAI-compatible
    # proxy (configured via LOCAL_API_BASE / LOCAL_API_KEY) is used without
    # requiring duplicates of those vars as OPENAI_API_BASE / OPENAI_API_KEY.
    #
    # Key naming conventions across libraries:
    #   OPENAI_API_BASE  – used by LiteLLM
    #   OPENAI_BASE_URL  – used by the native openai-python SDK (AsyncOpenAI)
    # Both must be set so every code path (LiteLLM and direct AsyncOpenAI) reach the proxy.
    try:
        local_api_base = os.getenv("LOCAL_API_BASE", "").strip()
        local_api_key = os.getenv("LOCAL_API_KEY", "").strip()
        if local_api_base and not os.getenv("OPENAI_API_BASE"):
            os.environ["OPENAI_API_BASE"] = local_api_base
            logging.getLogger(__name__).debug(
                "Propagated LOCAL_API_BASE to OPENAI_API_BASE"
            )
        if local_api_key and not os.getenv("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = local_api_key
            logging.getLogger(__name__).debug(
                "Propagated LOCAL_API_KEY to OPENAI_API_KEY"
            )
        # Mirror OPENAI_API_BASE -> OPENAI_BASE_URL for the native openai-python SDK.
        # AsyncOpenAI() reads OPENAI_BASE_URL (not OPENAI_API_BASE) when no base_url
        # argument is supplied, so without this the native client bypasses the proxy.
        effective_base = os.getenv("OPENAI_API_BASE", "").strip()
        if effective_base and not os.getenv("OPENAI_BASE_URL"):
            os.environ["OPENAI_BASE_URL"] = effective_base
            logging.getLogger(__name__).debug(
                "Mirrored OPENAI_API_BASE to OPENAI_BASE_URL"
            )
    except Exception:
        logging.getLogger(__name__).debug(
            "Failed to propagate LOCAL_* to OPENAI_*", exc_info=True
        )
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
