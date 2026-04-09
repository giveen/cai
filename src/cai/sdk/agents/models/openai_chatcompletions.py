from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import os
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast, overload

import litellm
import tiktoken

try:
    from openai import NOT_GIVEN, AsyncOpenAI, AsyncStream, NotGiven
except Exception:  # pragma: no cover - optional OpenAI SDK
    NOT_GIVEN = None
    AsyncOpenAI = None
    AsyncStream = None
    NotGiven = None
# ---------------------------------------------------------------------------
# LLM output sanitiser – strips hallucinated markup from local/small models
# (Gemma-4, Qwen, Mistral, etc.) before we attempt JSON parsing.
# ---------------------------------------------------------------------------
import re as _re

import httpx

# Create custom InputTokensDetails class since it's not available in current OpenAI version
from openai.types import ChatModel
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionDeveloperMessageParam,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolChoiceOptionParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_tool_param import ChatCompletionToolParam
from openai.types.chat.completion_create_params import ResponseFormat
from openai.types.completion_usage import CompletionUsage
from openai.types.responses import (
    EasyInputMessageParam,
    Response,
    ResponseCompletedEvent,
    ResponseContentPartAddedEvent,
    ResponseContentPartDoneEvent,
    ResponseCreatedEvent,
    ResponseFileSearchToolCallParam,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseFunctionToolCallParam,
    ResponseInputContentParam,
    ResponseInputImageParam,
    ResponseInputTextParam,
    ResponseOutputItem,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputMessageParam,
    ResponseOutputRefusal,
    ResponseOutputText,
    ResponseRefusalDeltaEvent,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_input_param import FunctionCallOutput, ItemReference, Message
from openai.types.responses.response_usage import OutputTokensDetails

from cai.sdk.agents.global_usage_tracker import GLOBAL_USAGE_TRACKER
from cai.sdk.agents.parallel_isolation import PARALLEL_ISOLATION
from cai.sdk.agents.run_to_jsonl import get_session_recorder
from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER
from cai.util import (
    _LIVE_STREAMING_PANELS,
    COST_TRACKER,
    calculate_model_cost,
    cli_print_agent_messages,
    cli_print_tool_output,
    create_agent_streaming_context,
    finish_agent_streaming,
    get_minimax_api_base,
    get_ollama_api_base,
    start_active_timer,
    start_claude_thinking_if_applicable,
    start_idle_timer,
    stop_active_timer,
    stop_idle_timer,
    update_agent_streaming_content,
)

# Patterns emitted by some models that are NOT valid JSON and must be removed.
# IMPORTANT: greedy/DOTALL stop-tag patterns must come BEFORE the generic
# <|...|> pattern so they consume the trailing hallucinated text before the
# shorter pattern eats just the tag itself.
_LLM_TAG_PATTERNS = [
    _re.compile(
        r"<\|tool_response\|>.*", _re.DOTALL
    ),  # hallucinated inline response (greedy, first)
    _re.compile(r"<tool_response>.*", _re.DOTALL),  # undecorated variant
    _re.compile(r"</tool_call>.*", _re.DOTALL),  # everything after </tool_call>
    _re.compile(r'<\|"\|>'),  # <|"|>  (Gemma-4 quote tokens)
    _re.compile(r"<\|[^>]{0,30}\|>"),  # remaining generic <|...|> tokens
]

# Stop-tags that signal the LLM tried to "one-shot" its own tool response.
_STREAM_STOP_SUBSTRINGS = (
    "</tool_call>",
    "<|tool_response|>",
    "<tool_response>",
)


def _sanitize_llm_tool_args(raw: str | None) -> str:
    """Strip malformed tokens from LLM-generated tool-call arguments.

    Returns a string that is safe to pass to json.loads(), falling back to
    '{}' for None / empty / hopelessly broken input.
    """
    if not raw or not raw.strip():
        return "{}"
    cleaned = raw
    for pat in _LLM_TAG_PATTERNS:
        cleaned = pat.sub("", cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return "{}"
    return cleaned


def _parse_tool_args(raw: str | None, tool_name: str = "") -> dict:
    """Parse tool-call arguments with a layered fallback strategy.

    1. Sanitise then json.loads.
    2. If that fails, try to extract the 'command' value via regex and
       return {'command': <value>} so the tool can still execute.
    3. If all else fails, return {}.
    """
    import json as _json

    cleaned = _sanitize_llm_tool_args(raw)
    try:
        parsed = _json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except _json.JSONDecodeError:
        pass
    # Regex fallback: pull out "command": "..." or "command": '...'
    m = _re.search(r'["\']command["\']\s*:\s*["\']([^"\']+)["\']', cleaned)
    if m:
        return {"command": m.group(1)}
    return {}


def _sanitize_tool_choice_value(tc):
    """Normalise *tc* to an allowed Response.tool_choice literal or object.

    LiteLLM/OpenAI callers may pass empty strings or other sentinel values which
    Pydantic rejects for the Response.tool_choice field.  Normalise to an allowed
    literal (``'auto'`` | ``'none'`` | ``'required'``) or pass through dict/object
    values unchanged.
    """
    try:
        if tc is None or tc is NOT_GIVEN:
            return "auto"
        if isinstance(tc, str):
            s = tc.strip().lower()
            if s in ("none", "auto", "required"):
                return s
            return "auto"
        return tc
    except Exception:
        return "auto"


# Custom ResponseUsage that makes prompt_tokens/input_tokens and completion_tokens/output_tokens compatible
class CustomResponseUsage(ResponseUsage):
    """
    Custom ResponseUsage class that provides compatibility between different field naming conventions.
    Works with both input_tokens/output_tokens and prompt_tokens/completion_tokens.
    """

    @property
    def prompt_tokens(self) -> int:
        """Alias for input_tokens to maintain compatibility"""
        return self.input_tokens

    @property
    def completion_tokens(self) -> int:
        """Alias for output_tokens to maintain compatibility"""
        return self.output_tokens


from .. import _debug
from ..agent_output import AgentOutputSchema
from ..exceptions import AgentsException, UserError
from ..handoffs import Handoff
from ..items import ModelResponse, TResponseInputItem, TResponseOutputItem, TResponseStreamEvent
from ..logger import logger
from ..tool import FunctionTool, Tool
from ..tracing import generation_span
from ..tracing.span_data import GenerationSpanData
from ..tracing.spans import Span
from ..usage import Usage
from ..version import __version__
from .fake_id import FAKE_RESPONSES_ID
from .interface import Model, ModelTracing

if TYPE_CHECKING:
    from ..model_settings import ModelSettings


# Suppress debug info from litellm
litellm.suppress_debug_info = True

if os.getenv("CAI_MODEL") == "o3-mini" or os.getenv("CAI_MODEL") == "gemini-1.5-pro":
    litellm.drop_params = True

_USER_AGENT = f"Agents/Python {__version__}"
_HEADERS = {"User-Agent": _USER_AGENT}

# Global registry to track active model instances
# This allows us to access instance-based histories for commands like /history
import contextvars  # noqa: E402
import weakref  # noqa: E402

# DEPRECATED: Use AGENT_REGISTRY instead
ACTIVE_MODEL_INSTANCES = {}

# Persistent message history store for agents without active instances
# This allows /load and /flush commands to work even when agents aren't running
PERSISTENT_MESSAGE_HISTORIES = {}

# Context variable to track the current active model per async context
_current_model_context = contextvars.ContextVar("current_model", default=None)


def set_current_active_model(model):
    """Set the current active model for tool execution context."""
    _current_model_context.set(weakref.ref(model) if model else None)


def get_current_active_model():
    """Get the current active model."""
    model_ref = _current_model_context.get()
    if model_ref:
        return model_ref()
    return None


def get_agent_message_history(agent_name: str) -> list:
    """Get message history for a specific agent.

    With SimpleAgentManager, this is much simpler - we only have one active agent.
    """
    # Remove any ID suffix if present (e.g., "[P1]")
    if "[" in agent_name and agent_name.endswith("]"):
        base_name = agent_name.rsplit("[", 1)[0].strip()
    else:
        base_name = agent_name

    # Get history from SimpleAgentManager
    return AGENT_MANAGER.get_message_history(base_name)


# Helper functions to centralize legacy active-instance registration.
def register_active_model_instance(display_name: str, agent_id: str, instance: object) -> None:
    """Register an active model instance in the legacy registry.

    Centralizes registration so we can remove or change the underlying
    implementation later without touching all call sites.
    """
    try:
        key = (display_name, agent_id)
        ACTIVE_MODEL_INSTANCES[key] = weakref.ref(instance)
    except Exception:
        logger.exception(
            "Failed to register active model instance for %s", (display_name, agent_id)
        )


def unregister_active_model_instance(display_name: str, agent_id: str) -> None:
    """Unregister an active model instance from the legacy registry."""
    try:
        key = (display_name, agent_id)
        if key in ACTIVE_MODEL_INSTANCES:
            del ACTIVE_MODEL_INSTANCES[key]
    except Exception:
        logger.exception(
            "Failed to unregister active model instance for %s", (display_name, agent_id)
        )


def get_all_agent_histories() -> dict:
    """Get all agent message histories.

    With SimpleAgentManager, we only track the active agent's history.
    """
    return AGENT_MANAGER.get_all_histories()


def clear_agent_history(agent_name: str):
    """Clear history for a specific agent.

    With SimpleAgentManager, this is much simpler.
    """
    # Remove any ID suffix if present
    if "[" in agent_name and agent_name.endswith("]"):
        base_name = agent_name.rsplit("[", 1)[0].strip()
    else:
        base_name = agent_name

    # Clear from SimpleAgentManager
    AGENT_MANAGER.clear_history(base_name)

    # Also clear the current instance if it matches
    active_agent = AGENT_MANAGER.get_active_agent()
    if active_agent and hasattr(active_agent, "message_history"):
        if hasattr(active_agent, "agent_name") and active_agent.agent_name == base_name:
            active_agent.message_history.clear()
            # Reset context usage for this agent
            os.environ["CAI_CONTEXT_USAGE"] = "0.0"


def clear_all_histories():
    """Clear all agent histories."""
    # Clear from SimpleAgentManager
    AGENT_MANAGER.clear_all_histories()

    # Clear active agent's history if present
    active_agent = AGENT_MANAGER.get_active_agent()
    if active_agent and hasattr(active_agent, "message_history"):
        active_agent.message_history.clear()

    # Clear all persistent histories
    PERSISTENT_MESSAGE_HISTORIES.clear()

    # Reset context usage since all histories are cleared
    os.environ["CAI_CONTEXT_USAGE"] = "0.0"


@dataclass
class _StreamingState:
    started: bool = False
    text_content_index_and_output: tuple[int, ResponseOutputText] | None = None
    refusal_content_index_and_output: tuple[int, ResponseOutputRefusal] | None = None
    function_calls: dict[int, ResponseFunctionToolCall] = field(default_factory=dict)


# Add a new function for consistent token counting using tiktoken
def _check_reasoning_compatibility(messages):
    """
    Check if message history is compatible with Claude reasoning/thinking.

    According to Claude 4 docs, when reasoning is enabled, the final assistant
    message must start with a thinking block. If there are assistant messages
    with regular text content, reasoning should be disabled.

    Args:
        messages: List of message dictionaries

    Returns:
        bool: True if compatible with reasoning, False otherwise
    """
    if not messages:
        return True  # Empty messages are compatible

    # Find the last assistant message
    last_assistant_msg = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant_msg = msg
            break

    if not last_assistant_msg:
        return True  # No assistant messages, compatible

    # Check if the last assistant message has regular text content
    content = last_assistant_msg.get("content")
    if content:
        # If it's a string with text content, not compatible
        if isinstance(content, str) and content.strip():
            return False
        # If it's a list, check for text content blocks
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        return False

    # Check if message has tool_calls (these are compatible)
    if last_assistant_msg.get("tool_calls"):
        return True

    # If no content or only thinking blocks, it's compatible
    return True


def count_tokens_with_tiktoken(text_or_messages):
    """
    Count tokens consistently using tiktoken library.
    Works with both strings and message lists.
    Returns a tuple of (input_tokens, reasoning_tokens).
    """
    if not text_or_messages:
        return 0, 0

    try:
        # Try to use cl100k_base encoding (used by GPT-4 and GPT-3.5-turbo)
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        # Fall back to GPT-2 encoding if cl100k is not available
        logger.exception("tiktoken cl100k_base encoding unavailable: %s", e)
        try:
            encoding = tiktoken.get_encoding("gpt2")
        except Exception as e2:
            logger.exception("tiktoken gpt2 encoding unavailable: %s", e2)
            # If tiktoken fails, fall back to character estimate
            if isinstance(text_or_messages, str):
                return len(text_or_messages) // 4, 0
            elif isinstance(text_or_messages, list):
                total_len = 0
                for msg in text_or_messages:
                    if isinstance(msg, dict) and "content" in msg:
                        if isinstance(msg["content"], str):
                            total_len += len(msg["content"])
                return total_len // 4, 0
            else:
                return 0, 0

    # Process different input types
    if isinstance(text_or_messages, str):
        token_count = len(encoding.encode(text_or_messages))
        return token_count, 0
    elif isinstance(text_or_messages, list):
        total_tokens = 0
        reasoning_tokens = 0

        # Add tokens for the messages format (ChatML format overhead)
        # Each message has a base overhead (usually ~4 tokens)
        total_tokens += len(text_or_messages) * 4

        for msg in text_or_messages:
            if isinstance(msg, dict):
                # Add tokens for role
                if "role" in msg:
                    total_tokens += len(encoding.encode(msg["role"]))

                # Count content tokens
                if "content" in msg and msg["content"]:
                    if isinstance(msg["content"], str):
                        content_tokens = len(encoding.encode(msg["content"]))
                        total_tokens += content_tokens

                        # Count tokens in assistant messages as reasoning tokens
                        if msg.get("role") == "assistant":
                            reasoning_tokens += content_tokens
                    elif isinstance(msg["content"], list):
                        for content_part in msg["content"]:
                            if isinstance(content_part, dict) and "text" in content_part:
                                part_tokens = len(encoding.encode(content_part["text"]))
                                total_tokens += part_tokens
                                if msg.get("role") == "assistant":
                                    reasoning_tokens += part_tokens

        return total_tokens, reasoning_tokens
    else:
        return 0, 0


# ContextCompactedError is defined in cai.sdk.agents.exceptions and imported
# here so existing code in this module can use it without any change.
from ..exceptions import ContextCompactedError  # noqa: E402  # re-exported


class OpenAIChatCompletionsModel(Model):
    """OpenAI Chat Completions Model"""

    INTERMEDIATE_LOG_INTERVAL = 5

    def __init__(
        self,
        model: str | ChatModel,
        openai_client: AsyncOpenAI,
        agent_name: str = "CTF agent",  # Default to CTF agent instead of generic "Agent"
        agent_id: str | None = None,
        agent_type: str | None = None,  # The type of agent (e.g., "red_teamer")
    ) -> None:
        self.model = model
        self._client = openai_client
        # Check if we're using OLLAMA models
        self.is_ollama = os.getenv("OLLAMA") is not None and os.getenv("OLLAMA").lower() != "false"
        self.empty_content_error_shown = False

        # Track interaction counter and token totals for cli display
        self.interaction_counter = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_reasoning_tokens = 0
        self.total_cost = 0.0
        self.agent_name = agent_name
        self.agent_type = agent_type or agent_name.lower().replace(
            " ", "_"
        )  # For registry tracking
        self.uses_unified_context = False  # Flag to indicate if using shared message history

        # For SimpleAgentManager, we don't auto-register
        # The agent will be registered when explicitly created by cli.py
        self.agent_id = agent_id or AGENT_MANAGER.get_agent_id()
        self._display_name = self.agent_name

        # Instance-based message history
        # Check if we have an isolated history for this agent (parallel mode)
        if agent_id and PARALLEL_ISOLATION.is_parallel_mode():
            isolated_history = PARALLEL_ISOLATION.get_isolated_history(agent_id)
            if isolated_history is not None:
                self.message_history = isolated_history
            else:
                self.message_history = []
        else:
            # Get or create history from AGENT_MANAGER to ensure we share the same list reference
            # This is critical for proper history clearing to work
            existing_history = AGENT_MANAGER.get_message_history(self.agent_name)
            if existing_history is not None and isinstance(existing_history, list):
                # Use the existing list reference from AGENT_MANAGER
                self.message_history = existing_history
            else:
                # Create new history and ensure AGENT_MANAGER has it too
                self.message_history = []
                if self.agent_name not in AGENT_MANAGER._message_history:
                    AGENT_MANAGER._message_history[self.agent_name] = self.message_history

        # NOTE: Models should NOT register themselves with AGENT_MANAGER
        # The agent that owns this model will handle registration
        # This prevents duplicate registrations with agent keys

        # CRITICAL: Ensure AGENT_MANAGER uses the same list reference as the model
        # This is necessary for proper history clearing to work
        if agent_id is not None and not PARALLEL_ISOLATION.is_parallel_mode():
            if self.agent_name in AGENT_MANAGER._message_history:
                # Share the same list reference
                self.message_history = AGENT_MANAGER._message_history[self.agent_name]

        # Instance-based converter
        self._converter = _Converter()

        # Flags for CLI integration
        self.disable_rich_streaming = False  # Prevents creating a rich panel in the model
        self.suppress_final_output = False  # Prevents duplicate output at end of streaming

        # Initialize the session logger
        self.logger = get_session_recorder()

        # DEPRECATED: Still maintain backward compatibility with ACTIVE_MODEL_INSTANCES
        # Centralize registration so future removals are simpler.
        register_active_model_instance(self._display_name, self.agent_id, self)
        # Resume support for interrupted streams
        self._last_stream_request = None
        self._last_stream_partial = ""
        self._resume_available = False

    def get_full_display_name(self) -> str:
        """Get the full display name including ID."""
        return f"{self._display_name} [{self.agent_id}]"

    def __del__(self):
        """Clean up when the model instance is destroyed."""
        try:
            # DEPRECATED: Remove from old registry for backward compatibility
            if hasattr(self, "_display_name") and hasattr(self, "agent_id"):
                unregister_active_model_instance(self._display_name, self.agent_id)

        # SimpleAgentManager handles history persistence
        # No need to save to PERSISTENT_MESSAGE_HISTORIES

        except Exception:
            # Ignore any errors during cleanup
            pass

    async def cleanup(self) -> None:
        """Explicitly cleanup underlying clients and free instance registry.

        This is intended to be called when a temporary model instance (for
        example the summary/support model) is no longer needed. It will try
        to close the HTTP/async client if available, remove the instance
        from the legacy `ACTIVE_MODEL_INSTANCES` registry and clear the
        in-memory message history so any backing LLM server can free slots
        or context.
        """
        try:
            client = getattr(self, "_client", None)
            if client is not None:
                aclose = getattr(client, "aclose", None)
                if aclose:
                    try:
                        res = aclose()
                        # Await if it's awaitable
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        # Best-effort close
                        pass
                try:
                    delattr(self, "_client")
                except Exception:
                    pass
        except Exception:
            pass

        try:
            key = (getattr(self, "_display_name", None), getattr(self, "agent_id", None))
            if key in ACTIVE_MODEL_INSTANCES:
                del ACTIVE_MODEL_INSTANCES[key]
        except Exception:
            pass

        try:
            if hasattr(self, "message_history") and isinstance(self.message_history, list):
                self.message_history.clear()
        except Exception:
            pass

    def add_to_message_history(self, msg):
        """Add a message to this instance's history if it's not a duplicate.

        Now only adds to the instance's local history, no global registry.
        """
        is_duplicate = False

        if self.message_history:
            if msg.get("role") in ["system", "user"]:
                is_duplicate = any(
                    existing.get("role") == msg.get("role")
                    and existing.get("content") == msg.get("content")
                    for existing in self.message_history
                )
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                # For tool calls, remove any existing message with the same tool call ID
                # This handles the case where streaming might create duplicate entries
                tool_call_id = msg["tool_calls"][0].get("id")
                # Remove duplicates in-place to preserve list reference (important for swarm patterns)
                indices_to_remove = []
                for i, existing in enumerate(self.message_history):
                    if (
                        existing.get("role") == "assistant"
                        and existing.get("tool_calls")
                        and existing["tool_calls"][0].get("id") == tool_call_id
                    ):
                        indices_to_remove.append(i)
                # Remove in reverse order to avoid index shifting
                for i in reversed(indices_to_remove):
                    self.message_history.pop(i)
                is_duplicate = False  # Always add after removing duplicates
            elif msg.get("role") == "tool":
                is_duplicate = any(
                    existing.get("role") == "tool"
                    and existing.get("tool_call_id") == msg.get("tool_call_id")
                    for existing in self.message_history
                )

        if not is_duplicate:
            self.message_history.append(msg)
            # Also update SimpleAgentManager ONLY if they're not the same list reference
            # This avoids double-adding when they share the same list
            manager_history = AGENT_MANAGER.get_message_history(self.agent_name)
            if manager_history is not self.message_history:
                AGENT_MANAGER.add_to_history(self.agent_name, msg)
            # Update isolated history if in parallel mode
            if PARALLEL_ISOLATION.is_parallel_mode() and self.agent_id:
                PARALLEL_ISOLATION.update_isolated_history(self.agent_id, msg)

    def set_agent_name(self, name: str) -> None:
        """Set the agent name for CLI display purposes."""
        self.agent_name = name

    def _non_null_or_not_given(self, value: Any) -> Any:
        return value if value is not None else NOT_GIVEN

    async def _execute_model_call_with_retries(
        self,
        call_coro_factory,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ):
        """
        Execute an async call factory with retries for transient errors.

        call_coro_factory: callable returning a coroutine when invoked.
        Retries on httpx network errors and on HTTP 429/503 responses and litellm RateLimitError.
        """
        attempt = 0
        import random
        import re

        while True:
            try:
                return await call_coro_factory()
            except Exception as e:  # noqa: BLE001 - broad handling for many provider libs
                attempt += 1

                # Determine if the error is retryable (429 / 503 / network / rate limit)
                retriable = False
                retry_delay = None

                try:
                    if isinstance(e, httpx.HTTPStatusError):
                        status = e.response.status_code if e.response is not None else None
                        if status in (429, 503):
                            retriable = True
                            if e.response is not None:
                                ra = e.response.headers.get("Retry-After")
                                if ra:
                                    try:
                                        retry_delay = int(ra)
                                    except Exception:
                                        pass
                    elif isinstance(e, httpx.RequestError):
                        retriable = True
                except Exception:
                    # Fall back to other heuristics below
                    pass

                # Handle LiteLLM/OpenAI RateLimitError heuristics
                try:
                    if (
                        not retriable
                        and hasattr(litellm, "exceptions")
                        and isinstance(e, litellm.exceptions.RateLimitError)
                    ):
                        retriable = True
                        err_str = str(e)
                        m = re.search(r"retry[_-]?after[:\s]+(\d+)", err_str, re.IGNORECASE)
                        if m:
                            retry_delay = int(m.group(1))
                        else:
                            m2 = re.search(r"wait\s+(\d+)\s+seconds?", err_str, re.IGNORECASE)
                            if m2:
                                retry_delay = int(m2.group(1))
                except Exception:
                    pass

                # Basic string heuristics for other providers
                if not retriable:
                    es = str(e).lower()
                    if (
                        "rate limit" in es
                        or "429" in es
                        or "too many requests" in es
                        or "503" in es
                        or "service unavailable" in es
                    ):
                        retriable = True

                if not retriable or attempt > max_retries:
                    # Not retryable or exhausted retries: re-raise
                    raise

                # Compute exponential backoff with small jitter
                if retry_delay is None:
                    retry_delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    retry_delay = retry_delay + random.uniform(0, 0.1 * retry_delay)

                logger.debug(
                    f"Model call transient error (attempt {attempt}/{max_retries}), retrying after {retry_delay:.1f}s: {e}"
                )
                await asyncio.sleep(retry_delay)

    def _warn_empty_response(
        self,
        content: str | None,
        has_tool_calls: bool,
        has_refusal: bool,
        converted_items: list[Any] | None = None,
    ) -> None:
        """Log warning for empty or sentinel responses (e.g., <|endoftext|>).

        This helper is defensive: it attempts to detect meaningful text in several
        different content shapes (string, list of parts, converted output items)
        before emitting a warning. `converted_items` may be passed by callers
        to avoid re-parsing provider-specific message shapes.
        """
        try:
            if has_tool_calls or has_refusal:
                return

            def _has_text_in_content(c: Any) -> bool:
                try:
                    if c is None:
                        return False
                    if isinstance(c, str):
                        return bool(c.strip())
                    if isinstance(c, list):
                        for elem in c:
                            if isinstance(elem, dict):
                                txt = elem.get("text") or elem.get("content") or elem.get("refusal")
                                if txt and str(txt).strip():
                                    return True
                            else:
                                s = str(elem)
                                if s.strip():
                                    return True
                        return False
                    if isinstance(c, dict):
                        # Try common keys
                        txt = c.get("text") or c.get("content")
                        if isinstance(txt, str) and txt.strip():
                            return True
                        return bool(str(c).strip())
                    return bool(str(c).strip())
                except Exception:
                    return False

            is_content_non_empty = _has_text_in_content(content)

            # If the raw content looked empty, consult converted_items (if provided)
            if not is_content_non_empty and converted_items:
                for it in converted_items:
                    try:
                        # Treat function_call items as non-empty (they indicate a tool invocation)
                        if getattr(it, "type", None) == "function_call":
                            return
                        # Check common text-like attributes
                        txt = (
                            getattr(it, "text", None)
                            or getattr(it, "content", None)
                            or getattr(it, "refusal", None)
                        )
                        if txt and str(txt).strip():
                            is_content_non_empty = True
                            break
                    except Exception:
                        continue

            is_empty = not is_content_non_empty
            is_sentinel = False
            try:
                if isinstance(content, str) and "<|endoftext|>" in content:
                    is_sentinel = True
            except Exception:
                pass

            if is_empty or is_sentinel:
                detail = "empty" if is_empty else f"sentinel({str(content)[:50]})"
                logger.warning(
                    f"Model completed without output ({detail}). May indicate model-agent compatibility issue."
                )
        except Exception:
            # Never raise from a diagnostic helper
            return

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchema | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
    ) -> ModelResponse:
        # Increment the interaction counter for CLI display
        self.interaction_counter += 1
        self._intermediate_logs()

        # Set this as the current active model for tool execution context
        set_current_active_model(self)

        # Stop idle timer and start active timer to track LLM processing time
        stop_idle_timer()
        start_active_timer()

        with generation_span(
            model=str(self.model),
            model_config=dataclasses.asdict(model_settings)
            | {"base_url": str(self._get_client().base_url)},
            disabled=tracing.is_disabled(),
        ) as span_generation:
            # Prepare the messages for consistent token counting.
            # History is already included in `input` via cli.py's history_context mechanism
            # (history_context = agent.model.message_history is passed as conversation_input
            # to Runner.run, which then passes it as original_input to get_response).
            # Prepending message_history here would double-count every message.
            converted_messages = []
            new_messages = self._converter.items_to_messages(input, model_instance=self)
            converted_messages.extend(new_messages)

            if system_instructions:
                # Check if we already have a system message
                has_system = any(msg.get("role") == "system" for msg in converted_messages)
                if not has_system:
                    converted_messages.insert(
                        0,
                        {
                            "content": system_instructions,
                            "role": "system",
                        },
                    )

            # Add support for prompt caching for claude (not automatically applied)
            # Gemini supports it too
            # https://www.anthropic.com/news/token-saving-updates
            # Maximize cache efficiency by using up to 4 cache_control blocks
            if (str(self.model).startswith("claude") or "gemini" in str(self.model)) and len(
                converted_messages
            ) > 0:
                # Strategy: Cache the most valuable messages for maximum savings
                # 1. System message (always first priority)
                # 2. Long user messages (high token count)
                # 3. Assistant messages with tool calls (complex context)
                # 4. Recent context (last message)

                cache_candidates = []

                # Always cache system message if present
                for i, msg in enumerate(converted_messages):
                    if msg.get("role") == "system":
                        cache_candidates.append((i, len(str(msg.get("content", ""))), "system"))
                        break

                # Find long user messages and assistant messages with tool calls
                for i, msg in enumerate(converted_messages):
                    content_len = len(str(msg.get("content", "")))
                    role = msg.get("role")

                    if role == "user" and content_len > 500:  # Long user messages
                        cache_candidates.append((i, content_len, "user"))
                    elif role == "assistant" and msg.get("tool_calls"):  # Tool calls
                        cache_candidates.append(
                            (i, content_len + 200, "assistant_tools")
                        )  # Bonus for tool calls

                # Always consider the last message for recent context
                if len(converted_messages) > 1:
                    last_idx = len(converted_messages) - 1
                    last_msg = converted_messages[last_idx]
                    last_content_len = len(str(last_msg.get("content", "")))
                    cache_candidates.append((last_idx, last_content_len, "recent"))

                # Sort by value (content length) and select top 4 unique indices
                cache_candidates.sort(key=lambda x: x[1], reverse=True)
                selected_indices = []
                for idx, _, msg_type in cache_candidates:
                    if idx not in selected_indices:
                        selected_indices.append(idx)
                        if len(selected_indices) >= 4:  # Max 4 cache blocks
                            break

                # Apply cache_control to selected messages
                for idx in selected_indices:
                    msg_copy = converted_messages[idx].copy()
                    msg_copy["cache_control"] = {"type": "ephemeral"}
                    converted_messages[idx] = msg_copy

            # # --- Add to message_history: user, system, and assistant tool call messages ---
            # # Add system prompt to message_history
            # if system_instructions:
            #     sys_msg = {
            #         "role": "system",
            #         "content": system_instructions
            #     }
            #     self.add_to_message_history(sys_msg)

            # Add user prompt(s) to message_history
            if isinstance(input, str):
                user_msg = {"role": "user", "content": input}
                self.add_to_message_history(user_msg)
                # Log the user message
                self.logger.log_user_message(input)
            elif isinstance(input, list):
                for item in input:
                    # Try to extract user messages
                    if isinstance(item, dict):
                        if item.get("role") == "user":
                            user_msg = {"role": "user", "content": item.get("content", "")}
                            self.add_to_message_history(user_msg)
                            # Log the user message
                            if item.get("content"):
                                self.logger.log_user_message(item.get("content"))

            # IMPORTANT: Ensure the message list has valid tool call/result pairs
            # This needs to happen before the API call to prevent errors
            try:
                from cai.util import fix_message_list

                converted_messages = fix_message_list(converted_messages)
            except Exception:
                pass

            # Get token count estimate before API call for consistent counting
            estimated_input_tokens, _ = count_tokens_with_tiktoken(converted_messages)
            # Use a small baseline for empty prompts to avoid zero-token edgecases
            # (tests expect a minimal non-zero default for completions).
            if estimated_input_tokens == 0:
                estimated_input_tokens = 5

            # Calculate and set context usage for toolbar
            max_tokens = self._get_model_max_tokens(str(self.model))
            context_usage = estimated_input_tokens / max_tokens if max_tokens > 0 else 0.0
            os.environ["CAI_CONTEXT_USAGE"] = str(context_usage)

            # Check if auto-compaction is needed
            try:
                input, system_instructions, compacted = await self._auto_compact_if_needed(
                    estimated_input_tokens, input, system_instructions
                )
            except ContextCompactedError:
                # Ensure timers are consistent on early-abort so callers don't
                # leave active timers running after the runner is aborted.
                try:
                    stop_active_timer()
                except Exception:
                    pass
                try:
                    start_idle_timer()
                except Exception:
                    pass
                raise

            # If compaction occurred, recalculate tokens with new input
            if compacted:
                converted_messages = self._converter.items_to_messages(input, model_instance=self)
                if system_instructions:
                    converted_messages.insert(0, {"role": "system", "content": system_instructions})
                estimated_input_tokens, _ = count_tokens_with_tiktoken(converted_messages)

            # Pre-check price limit using estimated input tokens and a conservative estimate for output
            # This prevents starting a request that would immediately exceed the price limit
            if hasattr(COST_TRACKER, "check_price_limit"):
                # Use a conservative estimate for output tokens (roughly equal to input)
                estimated_cost = calculate_model_cost(
                    str(self.model), estimated_input_tokens, estimated_input_tokens
                )  # Conservative estimate
                try:
                    COST_TRACKER.check_price_limit(estimated_cost)
                except Exception:
                    # Stop active timer and start idle timer before re-raising the exception
                    stop_active_timer()
                    start_idle_timer()
                    raise

            try:
                response = await self._fetch_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    span_generation,
                    tracing,
                    stream=False,
                )
            except KeyboardInterrupt:
                # Handle KeyboardInterrupt during API call
                # Clean up any pending tool calls that weren't executed
                if hasattr(self, "_pending_tool_calls"):
                    # Clear all pending tool calls to prevent incomplete history
                    self._pending_tool_calls.clear()

                # Let the interrupt propagate up to end the current operation
                stop_active_timer()
                start_idle_timer()

                raise

            if _debug.DONT_LOG_MODEL_DATA:
                logger.debug("Received model response")
            else:
                import json

                logger.debug(
                    f"LLM resp:\n{json.dumps(response.choices[0].message.model_dump(), indent=2)}\n"
                )

            # Ensure we have reasonable token counts. Prefer fields returned by
            # the provider (supporting multiple naming conventions), otherwise
            # fall back to our tiktoken-based estimates.
            input_tokens = None
            output_tokens = None
            total_tokens = None

            if response.usage:
                usage_obj = response.usage

                # Support both `input_tokens`/`output_tokens` and
                # `prompt_tokens`/`completion_tokens` naming.
                try:
                    it = getattr(usage_obj, "input_tokens", None)
                    if it is None:
                        it = getattr(usage_obj, "prompt_tokens", None)
                    if it is not None:
                        input_tokens = int(it)
                except Exception:
                    input_tokens = None

                try:
                    ot = getattr(usage_obj, "output_tokens", None)
                    if ot is None:
                        ot = getattr(usage_obj, "completion_tokens", None)
                    if ot is not None:
                        output_tokens = int(ot)
                except Exception:
                    output_tokens = None

                try:
                    tt = getattr(usage_obj, "total_tokens", None)
                    if tt is not None:
                        total_tokens = int(tt)
                except Exception:
                    total_tokens = None

                # Fill missing values from estimates/defaults
                if input_tokens is None:
                    input_tokens = estimated_input_tokens
                if output_tokens is None:
                    output_tokens = 0
                if total_tokens is None:
                    total_tokens = input_tokens + output_tokens

                # Sanity-check: if provider returned implausible zero/low values,
                # prefer our estimate for input tokens.
                try:
                    approx_len = len(str(input)) // 10 if input is not None else 0
                except Exception:
                    approx_len = 0

                if input_tokens == 0 or (approx_len > 0 and input_tokens < max(1, approx_len)):
                    input_tokens = estimated_input_tokens
                    total_tokens = input_tokens + output_tokens
            else:
                # If no usage info at all, use estimates
                input_tokens = estimated_input_tokens
                output_tokens = 0
                total_tokens = input_tokens
                # print(f"\nDEBUG CONSISTENT TOKEN COUNTS - No API tokens, using estimates: input={input_tokens}, output={output_tokens}")

            # Update token totals for CLI display
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            reasoning_tokens = 0
            if (
                response.usage
                and hasattr(response.usage, "completion_tokens_details")
                and response.usage.completion_tokens_details
                and hasattr(response.usage.completion_tokens_details, "reasoning_tokens")
            ):
                # Guard against None or unexpected types for reasoning_tokens
                try:
                    reasoning_tokens = response.usage.completion_tokens_details.reasoning_tokens
                    if reasoning_tokens is None:
                        reasoning_tokens = 0
                    else:
                        # coerce numeric-like values to int
                        reasoning_tokens = int(reasoning_tokens)
                except Exception:
                    reasoning_tokens = 0

                self.total_reasoning_tokens += reasoning_tokens

            # Process costs for non-streaming mode
            model_name = str(self.model)
            interaction_cost = calculate_model_cost(model_name, input_tokens, output_tokens)

            # Process the costs through COST_TRACKER only once
            if interaction_cost > 0.0:
                # Check price limit before processing
                if hasattr(COST_TRACKER, "check_price_limit"):
                    COST_TRACKER.check_price_limit(interaction_cost)

                # Process interaction cost
                COST_TRACKER.process_interaction_cost(
                    model_name, input_tokens, output_tokens, reasoning_tokens, interaction_cost
                )

                # Process total cost
                total_cost = COST_TRACKER.process_total_cost(
                    model_name,
                    self.total_input_tokens,
                    self.total_output_tokens,
                    self.total_reasoning_tokens,
                    None,
                )

                # Track usage globally
                GLOBAL_USAGE_TRACKER.track_usage(
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=interaction_cost,
                    agent_name=self.agent_name,
                )
            else:
                # For free models
                total_cost = COST_TRACKER.session_total_cost

                # Still track token usage even for free models
                GLOBAL_USAGE_TRACKER.track_usage(
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=0.0,
                    agent_name=self.agent_name,
                )

            # Check if this message contains tool calls
            tool_output = None
            should_display_message = True

            if (
                hasattr(response.choices[0].message, "tool_calls")
                and response.choices[0].message.tool_calls
            ):
                # For each tool call in the message, get corresponding output if available
                for tool_call in response.choices[0].message.tool_calls:
                    call_id = tool_call.id

                    # Check if this tool call has already been displayed
                    if (
                        hasattr(_Converter, "tool_outputs")
                        and call_id in self._converter.tool_outputs
                    ):
                        tool_output_content = self._converter.tool_outputs[call_id]

                        # Check if this is a command sent to an existing async session
                        is_async_session_input = False
                        has_auto_output = False
                        is_regular_command = False
                        try:
                            # Handle empty/malformed arguments robustly
                            tool_args = tool_call.function.arguments
                            args = _parse_tool_args(tool_args, tool_call.function.name)
                            # Check if this is a regular command (not a session command)
                            if (
                                isinstance(args, dict)
                                and args.get("command")
                                and not args.get("session_id")
                                and not args.get("async_mode")
                            ):
                                is_regular_command = True
                            # Only consider it an async session input if it has session_id AND it's not creating a new session
                            elif (
                                isinstance(args, dict)
                                and args.get("session_id")
                                and not args.get("async_mode")  # Not creating a new session
                                and not args.get("creating_session")
                            ):  # Not marked as session creation
                                is_async_session_input = True
                                # Check if this has auto_output flag
                                has_auto_output = args.get("auto_output", False)
                        except Exception as e:
                            logger.exception(
                                "Error inspecting tool args for streaming check: %s", e
                            )

                        # For regular commands that were already shown via streaming, suppress the agent message
                        if (
                            is_regular_command
                            and tool_call.function.name == "generic_linux_command"
                        ):
                            # Check if this was executed very recently (likely shown via streaming)
                            if (
                                hasattr(_Converter, "recent_tool_calls")
                                and call_id in self._converter.recent_tool_calls
                            ):
                                tool_call_info = self._converter.recent_tool_calls[call_id]
                                if "start_time" in tool_call_info:
                                    time_since_execution = (
                                        time.time() - tool_call_info["start_time"]
                                    )
                                    # If executed within last 2 seconds, it was likely shown via streaming
                                    if time_since_execution < 2.0:
                                        should_display_message = False
                                        tool_output = None
                        elif is_async_session_input:
                            should_display_message = True
                            tool_output = None
                        # For async session inputs without auto_output, always show the agent message
                        elif is_async_session_input and not has_auto_output:
                            should_display_message = True
                            tool_output = None
                        # For session creation messages, also show them
                        elif (
                            "Started async session" in tool_output_content
                            or "session" in tool_output_content.lower()
                            and "async" in tool_output_content.lower()
                        ):
                            should_display_message = True
                            tool_output = None
                        else:
                            # For other tool calls, check if we should suppress based on timing
                            # Only suppress if this tool was JUST executed (within last 2 seconds)
                            if (
                                hasattr(_Converter, "recent_tool_calls")
                                and call_id in self._converter.recent_tool_calls
                            ):
                                tool_call_info = self._converter.recent_tool_calls[call_id]
                                if "start_time" in tool_call_info:
                                    time_since_execution = (
                                        time.time() - tool_call_info["start_time"]
                                    )
                                    # Only suppress if this was executed very recently
                                    if time_since_execution < 2.0:
                                        should_display_message = False
                                    else:
                                        # For older tool calls, show the message
                                        should_display_message = True
                        break

            # Additional check: Always show messages that have text content
            # This ensures agent explanations are not suppressed
            if (
                hasattr(response.choices[0].message, "content")
                and response.choices[0].message.content
                and str(response.choices[0].message.content).strip()
            ):
                # If the message has actual text content, always show it
                should_display_message = True

            # Display the agent message (this will show the command for async sessions)
            if should_display_message:
                # Ensure we're in non-streaming mode for proper markdown parsing
                previous_stream_setting = os.environ.get("CAI_STREAM", "false")
                os.environ["CAI_STREAM"] = "false"  # Force non-streaming mode for markdown parsing

                # Print the agent message for CLI display
                cli_print_agent_messages(
                    agent_name=getattr(self, "agent_name", "Agent"),
                    message=response.choices[0].message,
                    counter=getattr(self, "interaction_counter", 0),
                    model=str(self.model),
                    debug=False,
                    interaction_input_tokens=input_tokens,
                    interaction_output_tokens=output_tokens,
                    interaction_reasoning_tokens=reasoning_tokens,
                    total_input_tokens=getattr(self, "total_input_tokens", 0),
                    total_output_tokens=getattr(self, "total_output_tokens", 0),
                    total_reasoning_tokens=getattr(self, "total_reasoning_tokens", 0),
                    interaction_cost=interaction_cost,
                    total_cost=total_cost,
                    tool_output=tool_output,  # Pass tool_output only when needed
                    suppress_empty=True,  # Keep suppress_empty=True as requested
                )

                # Restore previous streaming setting
                os.environ["CAI_STREAM"] = previous_stream_setting

            # --- DEFERRED: Tool calls are no longer added immediately ---
            # Tool calls will be added atomically with their responses
            # to prevent incomplete message history on interruption
            assistant_msg = response.choices[0].message
            if hasattr(assistant_msg, "tool_calls") and assistant_msg.tool_calls:
                # Store pending tool calls but don't add to history yet
                if not hasattr(self, "_pending_tool_calls"):
                    self._pending_tool_calls = {}

                # Fix Google Gemini OpenAI compatibility issues.
                # When using the OpenAI-compatible API to call tools with Google Gemini
                # tool_call.id is returned as an empty string.
                if "openai/gemini" in os.getenv("CAI_MODEL"):
                    for tool_call in assistant_msg.tool_calls:
                        if tool_call.id is None or tool_call.id == "":
                            tool_call.id = uuid.uuid4().hex[:16]

                for tool_call in assistant_msg.tool_calls:
                    # Handle empty/malformed arguments robustly
                    tool_args = tool_call.function.arguments
                    tool_args = _sanitize_llm_tool_args(tool_args)

                    # Compose a message for the tool call
                    tool_call_msg = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": tool_call.type,
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_args,
                                },
                            }
                        ],
                    }

                    # Store for later atomic addition with response
                    self._pending_tool_calls[tool_call.id] = tool_call_msg

                    # Save the tool call details for later matching with output
                    # This is important for non-streaming mode to track tool calls properly
                    if not hasattr(self._converter, "recent_tool_calls"):
                        self._converter.recent_tool_calls = {}

                    # Store the tool call by ID for later reference
                    self._converter.recent_tool_calls[tool_call.id] = {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                        "start_time": time.time(),
                        "execution_info": {"start_time": time.time()},
                    }

                # Log the assistant tool call message
                tool_calls_list = []
                for tool_call in assistant_msg.tool_calls:
                    tool_calls_list.append(
                        {
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                    )
                self.logger.log_assistant_message(None, tool_calls_list)
            # If the assistant message is just text, add it as well
            elif hasattr(assistant_msg, "content") and assistant_msg.content:
                asst_msg = {"role": "assistant", "content": assistant_msg.content}
                self.add_to_message_history(asst_msg)
                # Log the assistant message
                self.logger.log_assistant_message(assistant_msg.content)

            # En no-streaming, también necesitamos añadir cualquier tool output al message_history
            # Esto se hace procesando los items de output del ModelResponse
            items = self._converter.message_to_output_items(response.choices[0].message)

            # Además, necesitamos añadir los tool outputs que se hayan generado
            # durante la ejecución de las herramientas
            if hasattr(_Converter, "tool_outputs"):
                for call_id, output_content in self._converter.tool_outputs.items():
                    # Verificar si ya existe un mensaje tool con este call_id en message_history
                    tool_msg_exists = any(
                        msg.get("role") == "tool" and msg.get("tool_call_id") == call_id
                        for msg in self.message_history
                    )

                    if not tool_msg_exists:
                        # Añadir el mensaje tool al message_history
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": output_content,
                        }
                        self.add_to_message_history(tool_msg)

            # Log the complete response for the session
            self.logger.rec_training_data(
                {
                    "model": str(self.model),
                    "messages": converted_messages,
                    "stream": False,
                    "tools": [t.params_json_schema for t in tools] if tools else [],
                    "tool_choice": model_settings.tool_choice,
                },
                response,
                self.total_cost,
                self.agent_name,
            )

            # Always count the interaction as one request when we received a response.
            # Debugging: show token counts prior to usage assignment. Use logger.debug
            # so output is controlled by the application's logging configuration
            # instead of printing unconditionally to stdout.
            try:
                logger.debug(
                    "DEBUG get_response tokens: estimated_input_tokens=%s input_tokens=%s output_tokens=%s",
                    estimated_input_tokens,
                    input_tokens,
                    output_tokens,
                )
            except Exception:
                # Do not let debugging logging break execution
                pass

            usage = Usage(
                requests=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )
            if tracing.include_data():
                span_generation.span_data.output = [response.choices[0].message.model_dump()]
            span_generation.span_data.usage = {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            }

            items = self._converter.message_to_output_items(response.choices[0].message)

            # For non-streaming responses, make sure we also log token usage with compatible field names
            # This ensures both streaming and non-streaming use consistent naming
            if not hasattr(response, "usage"):
                response.usage = {}
            if hasattr(response.usage, "prompt_tokens") and not hasattr(
                response.usage, "input_tokens"
            ):
                response.usage.input_tokens = response.usage.prompt_tokens
            if hasattr(response.usage, "completion_tokens") and not hasattr(
                response.usage, "output_tokens"
            ):
                response.usage.output_tokens = response.usage.completion_tokens

            # Ensure cost is properly initialized
            if not hasattr(response, "cost"):
                response.cost = None

            # Warn if response is empty or contains sentinel token.
            # Use a robust detection for tool-calls since different providers
            # may expose function calls under different attributes
            try:
                msg = response.choices[0].message

                # Basic check for explicit tool_calls attribute
                has_tool_calls = bool(getattr(msg, "tool_calls", None))

                # Check alternate provider attribute names
                if not has_tool_calls:
                    has_tool_calls = bool(getattr(msg, "function_call", None))
                if not has_tool_calls:
                    has_tool_calls = bool(getattr(msg, "tool_use", None))

                # Fall back to inspecting converted items for a function_call entry
                if not has_tool_calls:
                    try:
                        converted_items = self._converter.message_to_output_items(msg)
                        has_tool_calls = any(
                            getattr(it, "type", None) == "function_call" for it in converted_items
                        )
                    except Exception:
                        # If conversion fails, ignore and continue with available data
                        pass

                self._warn_empty_response(
                    getattr(msg, "content", None),
                    bool(has_tool_calls),
                    bool(getattr(msg, "refusal", None)),
                    converted_items=items,
                )
            except Exception:
                pass

            return ModelResponse(
                output=items,
                usage=usage,
                referenceable_id=None,
            )

        # Stop active timer and start idle timer when response is complete
        stop_active_timer()
        start_idle_timer()

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchema | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
    ) -> AsyncIterator[TResponseStreamEvent]:
        """
        Yields a partial message as it is generated, as well as the usage information.
        """
        # Initialize streaming contexts as None
        streaming_context = None
        thinking_context = None
        stream_interrupted = False

        try:
            # IMPORTANT: Pre-process input to ensure it's in the correct format
            # for streaming. This helps prevent errors during stream handling.
            if not isinstance(input, str):
                # Convert input items to messages and verify structure
                try:
                    input_items = list(input)  # Make sure it's a list
                    # Pre-verify the input messages to avoid errors during streaming
                    from cai.util import fix_message_list

                    # Apply fix_message_list to the input items that are dictionaries
                    dict_items = [item for item in input_items if isinstance(item, dict)]
                    if dict_items:
                        fixed_dict_items = fix_message_list(dict_items)

                        # Replace the original dict items with fixed ones while preserving non-dict items
                        new_input = []
                        dict_index = 0
                        for item in input_items:
                            if isinstance(item, dict):
                                if dict_index < len(fixed_dict_items):
                                    new_input.append(fixed_dict_items[dict_index])
                                    dict_index += 1
                            else:
                                new_input.append(item)

                        # Update input with the fixed version
                        input = new_input
                except Exception:
                    # Silently continue with original input if pre-processing failed
                    # This is not critical and shouldn't show warnings
                    pass

            # Increment the interaction counter for CLI display
            self.interaction_counter += 1
            self._intermediate_logs()

            # Stop idle timer and start active timer to track LLM processing time
            stop_idle_timer()
            start_active_timer()

            # --- Check if streaming should be shown in rich panel ---
            should_show_rich_stream = (
                os.getenv("CAI_STREAM", "false").lower() == "true"
                and not self.disable_rich_streaming
            )

            # Create streaming context if needed
            if should_show_rich_stream:
                try:
                    streaming_context = create_agent_streaming_context(
                        agent_name=self.agent_name,
                        counter=self.interaction_counter,
                        model=str(self.model),
                    )
                except Exception:
                    # Silently fall back to non-streaming display
                    streaming_context = None

            with generation_span(
                model=str(self.model),
                model_config=dataclasses.asdict(model_settings)
                | {"base_url": str(self._get_client().base_url)},
                disabled=tracing.is_disabled(),
            ) as span_generation:
                # Prepare messages for consistent token counting
                converted_messages = self._converter.items_to_messages(input, model_instance=self)
                if system_instructions:
                    converted_messages.insert(
                        0,
                        {
                            "content": system_instructions,
                            "role": "system",
                        },
                    )

                # Add support for prompt caching for claude (not automatically applied)
                # Gemini supports it too
                # https://www.anthropic.com/news/token-saving-updates
                # Maximize cache efficiency by using up to 4 cache_control blocks
                if (str(self.model).startswith("claude") or "gemini" in str(self.model)) and len(
                    converted_messages
                ) > 0:
                    # Strategy: Cache the most valuable messages for maximum savings
                    # 1. System message (always first priority)
                    # 2. Long user messages (high token count)
                    # 3. Assistant messages with tool calls (complex context)
                    # 4. Recent context (last message)

                    cache_candidates = []

                    # Always cache system message if present
                    for i, msg in enumerate(converted_messages):
                        if msg.get("role") == "system":
                            cache_candidates.append((i, len(str(msg.get("content", ""))), "system"))
                            break

                    # Find long user messages and assistant messages with tool calls
                    for i, msg in enumerate(converted_messages):
                        content_len = len(str(msg.get("content", "")))
                        role = msg.get("role")

                        if role == "user" and content_len > 500:  # Long user messages
                            cache_candidates.append((i, content_len, "user"))
                        elif role == "assistant" and msg.get("tool_calls"):  # Tool calls
                            cache_candidates.append(
                                (i, content_len + 200, "assistant_tools")
                            )  # Bonus for tool calls

                    # Always consider the last message for recent context
                    if len(converted_messages) > 1:
                        last_idx = len(converted_messages) - 1
                        last_msg = converted_messages[last_idx]
                        last_content_len = len(str(last_msg.get("content", "")))
                        cache_candidates.append((last_idx, last_content_len, "recent"))

                    # Sort by value (content length) and select top 4 unique indices
                    cache_candidates.sort(key=lambda x: x[1], reverse=True)
                    selected_indices = []
                    for idx, _, msg_type in cache_candidates:
                        if idx not in selected_indices:
                            selected_indices.append(idx)
                            if len(selected_indices) >= 4:  # Max 4 cache blocks
                                break

                    # Apply cache_control to selected messages
                    for idx in selected_indices:
                        msg_copy = converted_messages[idx].copy()
                        msg_copy["cache_control"] = {"type": "ephemeral"}
                        converted_messages[idx] = msg_copy

                #    # --- Add to message_history: user, system prompts ---
                #     if system_instructions:
                #         sys_msg = {
                #             "role": "system",
                #             "content": system_instructions
                #         }
                #         self.add_to_message_history(sys_msg)

                if isinstance(input, str):
                    user_msg = {"role": "user", "content": input}
                    self.add_to_message_history(user_msg)
                    # Log the user message
                    self.logger.log_user_message(input)
                elif isinstance(input, list):
                    for item in input:
                        if isinstance(item, dict):
                            if item.get("role") == "user":
                                user_msg = {"role": "user", "content": item.get("content", "")}
                                self.add_to_message_history(user_msg)
                                # Log the user message
                                if item.get("content"):
                                    self.logger.log_user_message(item.get("content"))
                # Get token count estimate before API call for consistent counting
                estimated_input_tokens, _ = count_tokens_with_tiktoken(converted_messages)

                # Check if auto-compaction is needed
                try:
                    input, system_instructions, compacted = await self._auto_compact_if_needed(
                        estimated_input_tokens, input, system_instructions
                    )
                except ContextCompactedError:
                    # Reset timers when aborting due to context compaction so
                    # higher-level callers don't continue with active timers.
                    try:
                        stop_active_timer()
                    except Exception:
                        pass
                    try:
                        start_idle_timer()
                    except Exception:
                        pass
                    raise

                # If compaction occurred, recalculate tokens with new input
                if compacted:
                    converted_messages = self._converter.items_to_messages(
                        input, model_instance=self
                    )
                    if system_instructions:
                        converted_messages.insert(
                            0, {"role": "system", "content": system_instructions}
                        )
                    estimated_input_tokens, _ = count_tokens_with_tiktoken(converted_messages)

                # Pre-check price limit using estimated input tokens and a conservative estimate for output
                # This prevents starting a stream that would immediately exceed the price limit
                if hasattr(COST_TRACKER, "check_price_limit"):
                    # Use a conservative estimate for output tokens (roughly equal to input)
                    estimated_cost = calculate_model_cost(
                        str(self.model), estimated_input_tokens, estimated_input_tokens
                    )  # Conservative estimate
                    try:
                        COST_TRACKER.check_price_limit(estimated_cost)
                    except Exception:
                        # Ensure streaming context is cleaned up in case of errors
                        if streaming_context:
                            try:
                                finish_agent_streaming(streaming_context, None)
                            except Exception:
                                pass
                        # Stop active timer and start idle timer before re-raising the exception
                        stop_active_timer()
                        start_idle_timer()
                        raise

                response, stream = await self._fetch_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    span_generation,
                    tracing,
                    stream=True,
                )

                # Save last-stream request so we can "resume" if the user interrupts.
                try:
                    self._last_stream_request = {
                        "system_instructions": system_instructions,
                        "input": input,
                        "model_settings": model_settings,
                        "tools": tools,
                        "output_schema": output_schema,
                        "handoffs": handoffs,
                        "tracing": tracing,
                    }
                    self._last_stream_partial = ""
                    self._resume_available = False
                except Exception:
                    pass

                usage: CompletionUsage | None = None
                state = _StreamingState()

                # Manual token counting (when API doesn't provide it)
                output_text = ""
                estimated_output_tokens = 0

                # Initialize a streaming text accumulator for rich display
                streaming_text_buffer = ""
                # For tool call streaming, accumulate tool_calls to add to message_history at the end
                streamed_tool_calls = []

                # Initialize Claude thinking display if applicable
                if should_show_rich_stream:  # Only show thinking in rich streaming mode
                    thinking_context = start_claude_thinking_if_applicable(
                        str(self.model), self.agent_name, self.interaction_counter
                    )

                # Ollama specific: accumulate full content to check for function calls at the end
                # Some Ollama models output the function call as JSON in the text content
                ollama_full_content = ""
                is_ollama = False

                model_str = str(self.model).lower()
                is_ollama = (
                    self.is_ollama
                    or "ollama" in model_str
                    or ":" in model_str
                    or "qwen" in model_str
                )

                # Add visual separation before agent output
                if streaming_context and should_show_rich_stream:
                    # If we're using rich context, we'll add separation through that
                    pass
                else:
                    # Removed clear visual separator to avoid blank lines during streaming
                    pass

                try:
                    async for chunk in stream:
                        # Check if we've been interrupted
                        if stream_interrupted:
                            break

                        if not state.started:
                            state.started = True
                            yield ResponseCreatedEvent(
                                response=response,
                                type="response.created",
                            )

                        # The usage is only available in the last chunk
                        if hasattr(chunk, "usage"):
                            usage = chunk.usage
                        # For Ollama/LiteLLM streams that don't have usage attribute
                        else:
                            usage = None

                        # Handle different stream chunk formats
                        if hasattr(chunk, "choices") and chunk.choices:
                            choices = chunk.choices
                        elif hasattr(chunk, "delta") and chunk.delta:
                            # Some providers might return delta directly
                            choices = [{"delta": chunk.delta}]
                        elif isinstance(chunk, dict) and "choices" in chunk:
                            choices = chunk["choices"]
                        # Special handling for Qwen/Ollama chunks
                        elif isinstance(chunk, dict) and (
                            "content" in chunk or "function_call" in chunk
                        ):
                            # Qwen direct delta format - convert to standard
                            choices = [{"delta": chunk}]
                        else:
                            # Skip chunks that don't contain choice data
                            continue

                        if not choices or len(choices) == 0:
                            continue

                        # Get the delta content
                        delta = None
                        if hasattr(choices[0], "delta"):
                            delta = choices[0].delta
                        elif isinstance(choices[0], dict) and "delta" in choices[0]:
                            delta = choices[0]["delta"]

                        if not delta:
                            continue

                        # Handle Claude reasoning content first (before regular content)
                        reasoning_content = None

                        # Check for Claude reasoning in different possible formats
                        if (
                            hasattr(delta, "reasoning_content")
                            and delta.reasoning_content is not None
                        ):
                            reasoning_content = delta.reasoning_content
                        elif (
                            isinstance(delta, dict)
                            and "reasoning_content" in delta
                            and delta["reasoning_content"] is not None
                        ):
                            reasoning_content = delta["reasoning_content"]

                        # Also check for thinking_blocks structure (Claude 4 format)
                        thinking_blocks = None
                        if hasattr(delta, "thinking_blocks") and delta.thinking_blocks is not None:
                            thinking_blocks = delta.thinking_blocks
                        elif (
                            isinstance(delta, dict)
                            and "thinking_blocks" in delta
                            and delta["thinking_blocks"] is not None
                        ):
                            thinking_blocks = delta["thinking_blocks"]

                        # Extract reasoning content from thinking blocks if available
                        if thinking_blocks and not reasoning_content:
                            for block in thinking_blocks:
                                if isinstance(block, dict) and block.get("type") == "thinking":
                                    reasoning_content = block.get("thinking", "")
                                    break
                                elif (
                                    isinstance(block, dict)
                                    and block.get("type") == "text"
                                    and "thinking" in str(block)
                                ):
                                    # Sometimes thinking content comes as text blocks
                                    reasoning_content = block.get("text", "")
                                    break

                        # Check for direct thinking field (some Claude models)
                        if not reasoning_content:
                            if hasattr(delta, "thinking") and delta.thinking is not None:
                                reasoning_content = delta.thinking
                            elif (
                                isinstance(delta, dict)
                                and "thinking" in delta
                                and delta["thinking"] is not None
                            ):
                                reasoning_content = delta["thinking"]

                        # Update thinking display if we have reasoning content
                        if reasoning_content:
                            if thinking_context:
                                # Streaming mode: Update the rich thinking display
                                from cai.util import update_claude_thinking_content

                                update_claude_thinking_content(thinking_context, reasoning_content)
                            else:
                                # Non-streaming mode: Use simple text output
                                from cai.util import (
                                    detect_claude_thinking_in_stream,
                                    print_claude_reasoning_simple,
                                )

                                # Check if model supports reasoning (Claude or DeepSeek)
                                model_str_lower = str(self.model).lower()
                                if (
                                    detect_claude_thinking_in_stream(str(self.model))
                                    or "deepseek" in model_str_lower
                                ):
                                    print_claude_reasoning_simple(
                                        reasoning_content, self.agent_name, str(self.model)
                                    )

                        # Handle text
                        content = None
                        if hasattr(delta, "content") and delta.content is not None:
                            content = delta.content
                        elif (
                            isinstance(delta, dict)
                            and "content" in delta
                            and delta["content"] is not None
                        ):
                            content = delta["content"]

                        if content:
                            # Hard-stop: some local models emit a self-predicted tool
                            # response before the tool actually runs.  Truncate the
                            # stream at the first stop-tag so we never forward the
                            # hallucinated content to the rest of the pipeline.
                            for _stop in _STREAM_STOP_SUBSTRINGS:
                                if _stop in content:
                                    # Keep content up to but not including the stop tag
                                    content = content[: content.index(_stop)]
                                    # Signal the outer loop to break after this chunk
                                    if content:
                                        # still emit the clean prefix
                                        pass
                                    else:
                                        content = None
                                    # Mark stream as logically finished
                                    stream_interrupted = True
                                    break

                        if content:
                            # it means thinking is complete and normal content is starting
                            # Close the thinking display automatically
                            if thinking_context:
                                from cai.util import finish_claude_thinking_display

                                finish_claude_thinking_display(thinking_context)
                                thinking_context = None  # Clear the context

                            # For Ollama, we need to accumulate the full content to check for function calls
                            if is_ollama:
                                ollama_full_content += content

                            # Add to the streaming text buffer
                            streaming_text_buffer += content

                            # Update streaming display if enabled - ALWAYS respect CAI_STREAM setting
                            # Both thinking and regular content should stream if streaming is enabled
                            if streaming_context:
                                # Calculate cost for current interaction
                                current_cost = calculate_model_cost(
                                    str(self.model), estimated_input_tokens, estimated_output_tokens
                                )

                                # Check price limit only for paid models
                                if (
                                    current_cost > 0
                                    and hasattr(COST_TRACKER, "check_price_limit")
                                    and estimated_output_tokens % 50 == 0
                                ):
                                    try:
                                        COST_TRACKER.check_price_limit(current_cost)
                                    except Exception:
                                        # Ensure streaming context is cleaned up
                                        if streaming_context:
                                            try:
                                                finish_agent_streaming(streaming_context, None)
                                            except Exception:
                                                pass
                                        # Stop timers and re-raise the exception
                                        stop_active_timer()
                                        start_idle_timer()
                                        raise

                                # Update session total cost for real-time display
                                # This is a temporary estimate during streaming that will be properly updated at the end
                                estimated_session_total = getattr(
                                    COST_TRACKER, "session_total_cost", 0.0
                                )

                                # For free models, don't add to the total cost
                                display_total_cost = estimated_session_total
                                if current_cost > 0:
                                    display_total_cost += current_cost

                                # Create token stats with both current interaction cost and updated total cost
                                token_stats = {
                                    "input_tokens": estimated_input_tokens,
                                    "output_tokens": estimated_output_tokens,
                                    "cost": current_cost,
                                    "total_cost": display_total_cost,
                                }

                                update_agent_streaming_content(
                                    streaming_context, content, token_stats
                                )

                            # More accurate token counting for text content
                            output_text += content
                            token_count, _ = count_tokens_with_tiktoken(output_text)
                            estimated_output_tokens = token_count

                            # Periodically check price limit during streaming
                            # This allows early termination if price limit is reached mid-stream
                            if (
                                estimated_output_tokens > 0 and estimated_output_tokens % 50 == 0
                            ):  # Check every ~50 tokens
                                # Calculate current estimated cost
                                current_estimated_cost = calculate_model_cost(
                                    str(self.model), estimated_input_tokens, estimated_output_tokens
                                )

                                # Check price limit only for paid models
                                if current_estimated_cost > 0 and hasattr(
                                    COST_TRACKER, "check_price_limit"
                                ):
                                    try:
                                        COST_TRACKER.check_price_limit(current_estimated_cost)
                                    except Exception:
                                        # Ensure streaming context is cleaned up
                                        if streaming_context:
                                            try:
                                                finish_agent_streaming(streaming_context, None)
                                            except Exception:
                                                pass
                                        # Stop timers and re-raise the exception
                                        stop_active_timer()
                                        start_idle_timer()
                                        raise

                                # Update the COST_TRACKER with the running cost for accurate display
                                if hasattr(COST_TRACKER, "interaction_cost"):
                                    COST_TRACKER.interaction_cost = current_estimated_cost

                                # Also update streaming context if available for live display
                                if streaming_context:
                                    # For free models, don't add to the session total
                                    if current_estimated_cost == 0:
                                        session_total = getattr(
                                            COST_TRACKER, "session_total_cost", 0.0
                                        )
                                    else:
                                        session_total = (
                                            getattr(COST_TRACKER, "session_total_cost", 0.0)
                                            + current_estimated_cost
                                        )

                                    updated_token_stats = {
                                        "input_tokens": estimated_input_tokens,
                                        "output_tokens": estimated_output_tokens,
                                        "cost": current_estimated_cost,
                                        "total_cost": session_total,
                                    }
                                    update_agent_streaming_content(
                                        streaming_context, "", updated_token_stats
                                    )

                            if not state.text_content_index_and_output:
                                # Initialize a content tracker for streaming text
                                state.text_content_index_and_output = (
                                    0 if not state.refusal_content_index_and_output else 1,
                                    ResponseOutputText(
                                        text="",
                                        type="output_text",
                                        annotations=[],
                                    ),
                                )
                                # Start a new assistant message stream
                                assistant_item = ResponseOutputMessage(
                                    id=FAKE_RESPONSES_ID,
                                    content=[],
                                    role="assistant",
                                    type="message",
                                    status="in_progress",
                                )
                                # Notify consumers of the start of a new output message + first content part
                                yield ResponseOutputItemAddedEvent(
                                    item=assistant_item,
                                    output_index=0,
                                    type="response.output_item.added",
                                )
                                yield ResponseContentPartAddedEvent(
                                    content_index=state.text_content_index_and_output[0],
                                    item_id=FAKE_RESPONSES_ID,
                                    output_index=0,
                                    part=ResponseOutputText(
                                        text="",
                                        type="output_text",
                                        annotations=[],
                                    ),
                                    type="response.content_part.added",
                                )
                            # Emit the delta for this segment of content
                            yield ResponseTextDeltaEvent(
                                content_index=state.text_content_index_and_output[0],
                                delta=content,
                                item_id=FAKE_RESPONSES_ID,
                                output_index=0,
                                type="response.output_text.delta",
                            )
                            # Accumulate the text into the response part
                            state.text_content_index_and_output[1].text += content

                        # Handle refusals (model declines to answer)
                        refusal_content = None
                        if hasattr(delta, "refusal") and delta.refusal:
                            refusal_content = delta.refusal
                        elif isinstance(delta, dict) and "refusal" in delta and delta["refusal"]:
                            refusal_content = delta["refusal"]

                        if refusal_content:
                            if not state.refusal_content_index_and_output:
                                # Initialize a content tracker for streaming refusal text
                                state.refusal_content_index_and_output = (
                                    0 if not state.text_content_index_and_output else 1,
                                    ResponseOutputRefusal(refusal="", type="refusal"),
                                )
                                # Start a new assistant message if one doesn't exist yet (in-progress)
                                assistant_item = ResponseOutputMessage(
                                    id=FAKE_RESPONSES_ID,
                                    content=[],
                                    role="assistant",
                                    type="message",
                                    status="in_progress",
                                )
                                # Notify downstream that assistant message + first content part are starting
                                yield ResponseOutputItemAddedEvent(
                                    item=assistant_item,
                                    output_index=0,
                                    type="response.output_item.added",
                                )
                                yield ResponseContentPartAddedEvent(
                                    content_index=state.refusal_content_index_and_output[0],
                                    item_id=FAKE_RESPONSES_ID,
                                    output_index=0,
                                    part=ResponseOutputText(
                                        text="",
                                        type="output_text",
                                        annotations=[],
                                    ),
                                    type="response.content_part.added",
                                )
                            # Emit the delta for this segment of refusal
                            yield ResponseRefusalDeltaEvent(
                                content_index=state.refusal_content_index_and_output[0],
                                delta=refusal_content,
                                item_id=FAKE_RESPONSES_ID,
                                output_index=0,
                                type="response.refusal.delta",
                            )
                            # Accumulate the refusal string in the output part
                            state.refusal_content_index_and_output[1].refusal += refusal_content

                        # Handle tool calls
                        # Because we don't know the name of the function until the end of the stream, we'll
                        # save everything and yield events at the end
                        tool_calls = self._detect_and_format_function_calls(delta)

                        if tool_calls:
                            for tc_delta in tool_calls:
                                tc_index = (
                                    tc_delta.index
                                    if hasattr(tc_delta, "index")
                                    else tc_delta.get("index", 0)
                                )
                                if tc_index not in state.function_calls:
                                    state.function_calls[tc_index] = ResponseFunctionToolCall(
                                        id=FAKE_RESPONSES_ID,
                                        arguments="",
                                        name="",
                                        type="function_call",
                                        call_id="",
                                    )

                                tc_function = None
                                if hasattr(tc_delta, "function"):
                                    tc_function = tc_delta.function
                                elif isinstance(tc_delta, dict) and "function" in tc_delta:
                                    tc_function = tc_delta["function"]

                                if tc_function:
                                    # Handle both object and dict formats
                                    args = ""
                                    if hasattr(tc_function, "arguments"):
                                        args = tc_function.arguments or ""
                                    elif (
                                        isinstance(tc_function, dict) and "arguments" in tc_function
                                    ):
                                        args = tc_function.get("arguments", "") or ""

                                    name = ""
                                    if hasattr(tc_function, "name"):
                                        name = tc_function.name or ""
                                    elif isinstance(tc_function, dict) and "name" in tc_function:
                                        name = tc_function.get("name", "") or ""

                                    state.function_calls[tc_index].arguments += args
                                    state.function_calls[tc_index].name += name

                                # Handle call_id in both formats
                                call_id = ""
                                if hasattr(tc_delta, "id"):
                                    call_id = tc_delta.id or ""
                                elif isinstance(tc_delta, dict) and "id" in tc_delta:
                                    call_id = tc_delta.get("id", "") or ""
                                else:
                                    # For Qwen models, generate a predictable ID if none is provided
                                    if state.function_calls[tc_index].name:
                                        # Generate a stable ID from the function name and arguments
                                        call_id = f"call_{hashlib.md5(state.function_calls[tc_index].name.encode()).hexdigest()[:8]}"

                                state.function_calls[tc_index].call_id += call_id

                                # --- Accumulate tool call for message_history ---
                                # Only add if not already present (avoid duplicates in streaming)
                                # Sanitise arguments: strip LLM-generated markup tokens and
                                # truncate any hallucinated <|tool_response|> continuation.
                                tool_args = _sanitize_llm_tool_args(
                                    state.function_calls[tc_index].arguments
                                )

                                tool_call_msg = {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": state.function_calls[tc_index].call_id,
                                            "type": "function",
                                            "function": {
                                                "name": state.function_calls[tc_index].name,
                                                "arguments": tool_args,
                                            },
                                        }
                                    ],
                                }

                                # Validate that the accumulated arguments are valid JSON before
                                # we consider this a complete tool call to display or persist.
                                args_are_valid_json = False
                                if isinstance(tool_args, str):
                                    try:
                                        # Accept any valid JSON (object/array/string/number) but
                                        # we prefer objects for tool arguments. If parsing fails,
                                        # treat as not ready yet.
                                        parsed_args = json.loads(tool_args)
                                        args_are_valid_json = True
                                    except Exception:
                                        args_are_valid_json = False
                                else:
                                    # Non-string args (already a dict/list) are considered valid
                                    args_are_valid_json = True

                                # Only add to streamed_tool_calls and display when JSON is valid
                                # and we have name and call id. This prevents executing partial
                                # fragments from streaming deltas.
                                if (
                                    args_are_valid_json
                                    and state.function_calls[tc_index].name
                                    and state.function_calls[tc_index].call_id
                                ):
                                    if tool_call_msg not in streamed_tool_calls:
                                        streamed_tool_calls.append(tool_call_msg)
                                        # Don't add to message history here - wait for tool output
                                        # to add both tool call and response atomically
                                        # NEW: Display tool call immediately when detected in streaming mode
                                        # First, finish any existing streaming context if it exists
                                        if streaming_context:
                                            try:
                                                finish_agent_streaming(streaming_context, None)
                                                streaming_context = None
                                            except Exception:
                                                pass

                                        # Create a message-like object for displaying the function call
                                        tool_msg = type(
                                            "ToolCallStreamDisplay",
                                            (),
                                            {
                                                "content": None,
                                                "tool_calls": [
                                                    type(
                                                        "ToolCallDetail",
                                                        (),
                                                        {
                                                            "function": type(
                                                                "FunctionDetail",
                                                                (),
                                                                {
                                                                    "name": state.function_calls[
                                                                        tc_index
                                                                    ].name,
                                                                    "arguments": state.function_calls[
                                                                        tc_index
                                                                    ].arguments,
                                                                },
                                                            ),
                                                            "id": state.function_calls[
                                                                tc_index
                                                            ].call_id,
                                                            "type": "function",
                                                        },
                                                    )
                                                ],
                                            },
                                        )

                                        # Display the tool call during streaming
                                        cli_print_agent_messages(
                                            agent_name=getattr(self, "agent_name", "Agent"),
                                            message=tool_msg,
                                            counter=getattr(self, "interaction_counter", 0),
                                            model=str(self.model),
                                            debug=False,
                                            interaction_input_tokens=estimated_input_tokens,
                                            interaction_output_tokens=estimated_output_tokens,
                                            interaction_reasoning_tokens=0,  # Not available during streaming yet
                                            total_input_tokens=getattr(
                                                self, "total_input_tokens", 0
                                            )
                                            + estimated_input_tokens,
                                            total_output_tokens=getattr(
                                                self, "total_output_tokens", 0
                                            )
                                            + estimated_output_tokens,
                                            total_reasoning_tokens=getattr(
                                                self, "total_reasoning_tokens", 0
                                            ),
                                            interaction_cost=None,
                                            total_cost=None,
                                            tool_output=None,  # Will be shown once tool is executed
                                            suppress_empty=True,  # Prevent empty panels
                                        )
                                        # Set flag to suppress final output to avoid duplication
                                        self.suppress_final_output = True

                except KeyboardInterrupt:
                    # Handle interruption during streaming
                    stream_interrupted = True
                    # Save partial content and request so the user can resume later
                    try:
                        self._last_stream_partial = streaming_text_buffer
                        self._resume_available = True
                    except Exception:
                        pass

                    print("\n[Streaming interrupted by user]", file=sys.stderr)

                    # Let the exception propagate after cleanup
                    raise

                except Exception as e:
                    # Handle other exceptions during streaming
                    logger.error(f"Error during streaming: {e}")
                    if "token" in str(e).lower() or "limit" in str(e).lower():
                        print("\n📏 Token limit exceeded - Response truncated")
                    raise

                # Special handling for Ollama - check if accumulated text contains a valid function call
                if is_ollama and ollama_full_content and len(state.function_calls) == 0:
                    # Look for JSON object that might be a function call
                    try:
                        # Try to extract a JSON object from the content
                        json_start = ollama_full_content.find("{")
                        json_end = ollama_full_content.rfind("}") + 1

                        if json_start >= 0 and json_end > json_start:
                            json_str = ollama_full_content[json_start:json_end]
                            # Try to parse the JSON
                            parsed = json.loads(json_str)

                            # Check if it looks like a function call
                            if "name" in parsed and "arguments" in parsed:
                                logger.debug(
                                    f"Found valid function call in Ollama output: {json_str}"
                                )

                                # Create a tool call ID
                                tool_call_id = f"call_{hashlib.md5((parsed['name'] + str(time.time())).encode()).hexdigest()[:8]}"

                                # Ensure arguments is a valid JSON string
                                arguments_str = ""
                                if isinstance(parsed["arguments"], dict):
                                    # Remove 'ctf' field if it exists
                                    if "ctf" in parsed["arguments"]:
                                        del parsed["arguments"]["ctf"]
                                    arguments_str = json.dumps(parsed["arguments"])
                                elif isinstance(parsed["arguments"], str):
                                    # If it's already a string, check if it's valid JSON
                                    try:
                                        # Try parsing to validate and remove 'ctf' if present
                                        args_dict = json.loads(parsed["arguments"])
                                        if isinstance(args_dict, dict) and "ctf" in args_dict:
                                            del args_dict["ctf"]
                                        arguments_str = json.dumps(args_dict)
                                    except Exception as e:
                                        logger.exception(
                                            "Failed to parse tool arguments as JSON, falling back to string: %s",
                                            e,
                                        )
                                        # If not valid JSON, encode it as a JSON string
                                        arguments_str = json.dumps(parsed["arguments"])
                                else:
                                    # For any other type, convert to string and then JSON
                                    arguments_str = json.dumps(str(parsed["arguments"]))
                                # Add it to our function_calls state
                                state.function_calls[0] = ResponseFunctionToolCall(
                                    id=FAKE_RESPONSES_ID,
                                    arguments=arguments_str,
                                    name=parsed["name"],
                                    type="function_call",
                                    call_id=tool_call_id[:40],
                                )

                                # Display the tool call in CLI
                                try:
                                    # First, finish any existing streaming context if it exists
                                    if streaming_context:
                                        try:
                                            finish_agent_streaming(streaming_context, None)
                                            streaming_context = None
                                        except Exception:
                                            pass

                                    # Create a message-like object to display the function call
                                    tool_msg = type(
                                        "ToolCallWrapper",
                                        (),
                                        {
                                            "content": None,
                                            "tool_calls": [
                                                type(
                                                    "ToolCallDetail",
                                                    (),
                                                    {
                                                        "function": type(
                                                            "FunctionDetail",
                                                            (),
                                                            {
                                                                "name": parsed["name"],
                                                                "arguments": arguments_str,
                                                            },
                                                        ),
                                                        "id": tool_call_id[:40],
                                                        "type": "function",
                                                    },
                                                )
                                            ],
                                        },
                                    )

                                    # Print the tool call using the CLI utility
                                    cli_print_agent_messages(
                                        agent_name=getattr(self, "agent_name", "Agent"),
                                        message=tool_msg,
                                        counter=getattr(self, "interaction_counter", 0),
                                        model=str(self.model),
                                        debug=False,
                                        interaction_input_tokens=estimated_input_tokens,
                                        interaction_output_tokens=estimated_output_tokens,
                                        interaction_reasoning_tokens=0,  # Not available for Ollama
                                        total_input_tokens=getattr(self, "total_input_tokens", 0)
                                        + estimated_input_tokens,
                                        total_output_tokens=getattr(self, "total_output_tokens", 0)
                                        + estimated_output_tokens,
                                        total_reasoning_tokens=getattr(
                                            self, "total_reasoning_tokens", 0
                                        ),
                                        interaction_cost=None,
                                        total_cost=None,
                                        tool_output=None,  # Will be shown once the tool is executed
                                        suppress_empty=True,  # Suppress empty panels during streaming
                                    )

                                    # Set flag to suppress final output to avoid duplication
                                    self.suppress_final_output = True
                                except Exception as e:
                                    # Silently log the error - don't disrupt the flow
                                    logger.debug(f"Display error (non-critical): {e}")

                                # Add to message history
                                tool_call_msg = {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": tool_call_id,
                                            "type": "function",
                                            "function": {
                                                "name": parsed["name"],
                                                "arguments": arguments_str,
                                            },
                                        }
                                    ],
                                }

                                streamed_tool_calls.append(tool_call_msg)
                                # Don't add to message history here - wait for tool output
                                # to add both tool call and response atomically

                                logger.debug(
                                    f"Added function call: {parsed['name']} with args: {arguments_str}"
                                )
                    except Exception:
                        pass

                function_call_starting_index = 0
                if state.text_content_index_and_output:
                    function_call_starting_index += 1
                    # Send end event for this content part
                    yield ResponseContentPartDoneEvent(
                        content_index=state.text_content_index_and_output[0],
                        item_id=FAKE_RESPONSES_ID,
                        output_index=0,
                        part=state.text_content_index_and_output[1],
                        type="response.content_part.done",
                    )

                if state.refusal_content_index_and_output:
                    function_call_starting_index += 1
                    # Send end event for this content part
                    yield ResponseContentPartDoneEvent(
                        content_index=state.refusal_content_index_and_output[0],
                        item_id=FAKE_RESPONSES_ID,
                        output_index=0,
                        part=state.refusal_content_index_and_output[1],
                        type="response.content_part.done",
                    )

                # Actually send events for the function calls
                for function_call in state.function_calls.values():
                    # First, a ResponseOutputItemAdded for the function call
                    yield ResponseOutputItemAddedEvent(
                        item=ResponseFunctionToolCall(
                            id=FAKE_RESPONSES_ID,
                            call_id=function_call.call_id[:40],
                            arguments=function_call.arguments,
                            name=function_call.name,
                            type="function_call",
                        ),
                        output_index=function_call_starting_index,
                        type="response.output_item.added",
                    )
                    # Then, yield the args
                    yield ResponseFunctionCallArgumentsDeltaEvent(
                        delta=function_call.arguments,
                        item_id=FAKE_RESPONSES_ID,
                        output_index=function_call_starting_index,
                        type="response.function_call_arguments.delta",
                    )
                    # Finally, the ResponseOutputItemDone
                    yield ResponseOutputItemDoneEvent(
                        item=ResponseFunctionToolCall(
                            id=FAKE_RESPONSES_ID,
                            call_id=function_call.call_id[:40],
                            arguments=function_call.arguments,
                            name=function_call.name,
                            type="function_call",
                        ),
                        output_index=function_call_starting_index,
                        type="response.output_item.done",
                    )

                # Finally, send the Response completed event
                outputs: list[ResponseOutputItem] = []
                if state.text_content_index_and_output or state.refusal_content_index_and_output:
                    assistant_msg = ResponseOutputMessage(
                        id=FAKE_RESPONSES_ID,
                        content=[],
                        role="assistant",
                        type="message",
                        status="completed",
                    )
                    if state.text_content_index_and_output:
                        assistant_msg.content.append(state.text_content_index_and_output[1])
                    if state.refusal_content_index_and_output:
                        assistant_msg.content.append(state.refusal_content_index_and_output[1])
                    outputs.append(assistant_msg)

                    # send a ResponseOutputItemDone for the assistant message
                    yield ResponseOutputItemDoneEvent(
                        item=assistant_msg,
                        output_index=0,
                        type="response.output_item.done",
                    )

                for function_call in state.function_calls.values():
                    outputs.append(function_call)

                final_response = response.model_copy()
                final_response.output = outputs

                # Get final token counts using consistent method
                input_tokens = estimated_input_tokens
                output_tokens = estimated_output_tokens

                # Use API token counts if available and reasonable
                if usage and hasattr(usage, "prompt_tokens") and usage.prompt_tokens > 0:
                    input_tokens = usage.prompt_tokens
                if usage and hasattr(usage, "completion_tokens") and usage.completion_tokens > 0:
                    output_tokens = usage.completion_tokens

                # Create a proper usage object with our token counts
                final_response.usage = CustomResponseUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    output_tokens_details=OutputTokensDetails(
                        reasoning_tokens=usage.completion_tokens_details.reasoning_tokens
                        if usage
                        and hasattr(usage, "completion_tokens_details")
                        and usage.completion_tokens_details
                        and hasattr(usage.completion_tokens_details, "reasoning_tokens")
                        and usage.completion_tokens_details.reasoning_tokens
                        else 0
                    ),
                    input_tokens_details={
                        "prompt_tokens": input_tokens,
                        "cached_tokens": usage.prompt_tokens_details.cached_tokens
                        if usage
                        and hasattr(usage, "prompt_tokens_details")
                        and usage.prompt_tokens_details
                        and hasattr(usage.prompt_tokens_details, "cached_tokens")
                        and usage.prompt_tokens_details.cached_tokens
                        else 0,
                    },
                )

                # Warn if streamed response is empty or contains sentinel token
                try:
                    text_content = ""
                    if state.text_content_index_and_output:
                        text_out = state.text_content_index_and_output[1]
                        text_content = getattr(text_out, "text", "") or getattr(
                            text_out, "content", ""
                        )
                    self._warn_empty_response(
                        text_content,
                        bool(state.function_calls),
                        bool(state.refusal_content_index_and_output),
                    )
                except Exception:
                    pass

                yield ResponseCompletedEvent(
                    response=final_response,
                    type="response.completed",
                )

                # Update token totals for CLI display
                if final_response.usage:
                    # Always update the total counters with the best available counts
                    self.total_input_tokens += final_response.usage.input_tokens
                    self.total_output_tokens += final_response.usage.output_tokens
                    if final_response.usage.output_tokens_details and hasattr(
                        final_response.usage.output_tokens_details, "reasoning_tokens"
                    ):
                        self.total_reasoning_tokens += (
                            final_response.usage.output_tokens_details.reasoning_tokens
                        )

                # Prepare final statistics for display
                interaction_input = final_response.usage.input_tokens if final_response.usage else 0
                interaction_output = (
                    final_response.usage.output_tokens if final_response.usage else 0
                )
                total_input = getattr(self, "total_input_tokens", 0)
                total_output = getattr(self, "total_output_tokens", 0)

                # Calculate costs for this model
                model_name = str(self.model)
                interaction_cost = calculate_model_cost(
                    model_name, interaction_input, interaction_output
                )
                # Get the previous total cost and add this interaction's cost
                # Don't recalculate cost for all tokens - that causes double-counting
                previous_total = getattr(COST_TRACKER, "session_total_cost", 0.0)
                total_cost = previous_total + interaction_cost

                # If interaction cost is zero, this is a free model
                if interaction_cost == 0:
                    # For free models, keep existing total and ensure cost tracking system knows it's free
                    total_cost = getattr(COST_TRACKER, "session_total_cost", 0.0)
                    if hasattr(COST_TRACKER, "reset_cost_for_local_model"):
                        COST_TRACKER.reset_cost_for_local_model(model_name)

                # Explicit conversion to float with fallback to ensure they're never None or 0
                interaction_cost = float(interaction_cost if interaction_cost is not None else 0.0)
                total_cost = float(total_cost if total_cost is not None else 0.0)

                # Process costs through COST_TRACKER only once per interaction
                if interaction_cost > 0.0:
                    # Check price limit before processing the new cost
                    if hasattr(COST_TRACKER, "check_price_limit"):
                        try:
                            COST_TRACKER.check_price_limit(interaction_cost)
                        except Exception:
                            # Ensure streaming context is cleaned up
                            if streaming_context:
                                try:
                                    finish_agent_streaming(streaming_context, None)
                                except Exception:
                                    pass
                            # Stop timers and re-raise the exception
                            stop_active_timer()
                            start_idle_timer()
                            raise

                    # Process the interaction cost (updates internal tracking)
                    COST_TRACKER.process_interaction_cost(
                        model_name,
                        interaction_input,
                        interaction_output,
                        final_response.usage.output_tokens_details.reasoning_tokens
                        if final_response.usage
                        and final_response.usage.output_tokens_details
                        and hasattr(final_response.usage.output_tokens_details, "reasoning_tokens")
                        else 0,
                        interaction_cost,
                    )

                    # Process the total cost (updates session total correctly)
                    total_cost = COST_TRACKER.process_total_cost(
                        model_name,
                        total_input,
                        total_output,
                        getattr(self, "total_reasoning_tokens", 0),
                        None,  # Let it calculate from tokens
                    )

                    # Track usage globally
                    GLOBAL_USAGE_TRACKER.track_usage(
                        model_name=model_name,
                        input_tokens=interaction_input,
                        output_tokens=interaction_output,
                        cost=interaction_cost,
                        agent_name=self.agent_name,
                    )
                else:
                    # For free models, still track token usage
                    GLOBAL_USAGE_TRACKER.track_usage(
                        model_name=model_name,
                        input_tokens=interaction_input,
                        output_tokens=interaction_output,
                        cost=0.0,
                        agent_name=self.agent_name,
                    )

                # Store the total cost for future recording
                self.total_cost = total_cost

                # Create final stats with explicit type conversion for all values
                final_stats = {
                    "interaction_input_tokens": int(interaction_input),
                    "interaction_output_tokens": int(interaction_output),
                    "interaction_reasoning_tokens": int(
                        final_response.usage.output_tokens_details.reasoning_tokens
                        if final_response.usage
                        and final_response.usage.output_tokens_details
                        and hasattr(final_response.usage.output_tokens_details, "reasoning_tokens")
                        else 0
                    ),
                    "total_input_tokens": int(total_input),
                    "total_output_tokens": int(total_output),
                    "total_reasoning_tokens": int(getattr(self, "total_reasoning_tokens", 0)),
                    "interaction_cost": float(interaction_cost),
                    "total_cost": float(total_cost),
                }

                # At the end of streaming, finish the streaming context if we were using it
                if streaming_context:
                    # Create a direct copy of the costs to ensure they remain as floats
                    direct_stats = final_stats.copy()
                    direct_stats["interaction_cost"] = float(interaction_cost)
                    direct_stats["total_cost"] = float(total_cost)
                    # Use the direct copy with guaranteed float costs
                    finish_agent_streaming(streaming_context, direct_stats)
                    streaming_context = None

                    # Removed extra newline after streaming completes to avoid blank lines
                    pass

                # Finish Claude thinking display if it was active
                if thinking_context:
                    from cai.util import finish_claude_thinking_display

                    finish_claude_thinking_display(thinking_context)

                    # Note: Content is now displayed during streaming, no need to show it again here

                if tracing.include_data():
                    span_generation.span_data.output = [final_response.model_dump()]

                span_generation.span_data.usage = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }

                # --- DEFERRED: Tool calls are no longer added immediately ---
                # Store pending tool calls but don't add to history yet
                if not hasattr(self, "_pending_tool_calls"):
                    self._pending_tool_calls = {}

                for tool_call_msg in streamed_tool_calls:
                    # Extract tool call ID from the message
                    if tool_call_msg.get("tool_calls"):
                        for tc in tool_call_msg["tool_calls"]:
                            self._pending_tool_calls[tc["id"]] = tool_call_msg

                # Log the assistant tool call message if any tool calls were collected
                if streamed_tool_calls:
                    tool_calls_list = []
                    for tool_call_msg in streamed_tool_calls:
                        for tool_call in tool_call_msg.get("tool_calls", []):
                            tool_calls_list.append(tool_call)
                    self.logger.log_assistant_message(None, tool_calls_list)

                # Always log text content if it exists, regardless of suppress_final_output
                # The suppress_final_output flag is only for preventing duplicate tool call display
                if (
                    state.text_content_index_and_output
                    and state.text_content_index_and_output[1].text
                ):
                    asst_msg = {
                        "role": "assistant",
                        "content": state.text_content_index_and_output[1].text,
                    }
                    self.add_to_message_history(asst_msg)
                    # Log the assistant message
                    self.logger.log_assistant_message(state.text_content_index_and_output[1].text)

                # Reset the suppress flag for future requests
                self.suppress_final_output = False

                # Log the complete response
                self.logger.rec_training_data(
                    {
                        "model": str(self.model),
                        "messages": converted_messages,
                        "stream": True,
                        "tools": [t.params_json_schema for t in tools] if tools else [],
                        "tool_choice": model_settings.tool_choice,
                    },
                    final_response,
                    self.total_cost,
                    self.agent_name,
                )

                # Clear any saved resume-state since the stream completed successfully
                try:
                    self._last_stream_request = None
                    self._last_stream_partial = ""
                    self._resume_available = False
                except Exception:
                    pass

                # Stop active timer and start idle timer when streaming is complete
                stop_active_timer()
                start_idle_timer()

        except KeyboardInterrupt:
            # Handle keyboard interruption specifically
            stream_interrupted = True

            # Ensure message history consistency by adding synthetic tool results
            # for any tool calls that were added but don't have corresponding results
            try:
                # Find all tool calls in recent assistant messages
                orphaned_tool_calls = []
                for msg in reversed(self.message_history[-10:]):  # Check recent messages
                    if msg.get("role") == "assistant" and msg.get("tool_calls"):
                        for tool_call in msg["tool_calls"]:
                            call_id = tool_call.get("id")
                            if call_id:
                                # Check if this tool call has a corresponding tool result
                                has_result = any(
                                    m.get("role") == "tool" and m.get("tool_call_id") == call_id
                                    for m in self.message_history
                                )
                                if not has_result:
                                    orphaned_tool_calls.append((call_id, tool_call))

                # Add synthetic tool results for orphaned tool calls
                for call_id, tool_call in orphaned_tool_calls:
                    tool_response_msg = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": "Tool execution interrupted",
                    }
                    self.add_to_message_history(tool_response_msg)

            except Exception as cleanup_error:
                # Don't let cleanup errors mask the original KeyboardInterrupt
                logger.debug(f"Error during interrupt cleanup: {cleanup_error}")

            # Make sure to clean up and re-raise
            raise

        except Exception as e:
            # Handle other exceptions
            logger.error(f"Error in stream_response: {e}")
            raise

        finally:
            # Always clean up resources
            # This block executes whether the try block succeeds, fails, or is interrupted

            # Clean up streaming context
            if streaming_context:
                try:
                    # Check if we need to force stop the streaming panel
                    if streaming_context.get("is_started", False) and streaming_context.get("live"):
                        streaming_context["live"].stop()

                    # Remove from active streaming contexts
                    if hasattr(create_agent_streaming_context, "_active_streaming"):
                        for key, value in list(
                            create_agent_streaming_context._active_streaming.items()
                        ):
                            if value is streaming_context:
                                del create_agent_streaming_context._active_streaming[key]
                                break
                except Exception as cleanup_error:
                    logger.debug(f"Error cleaning up streaming context: {cleanup_error}")

            # Clean up thinking context
            if thinking_context:
                try:
                    # Force finish the thinking display
                    from cai.util import finish_claude_thinking_display

                    finish_claude_thinking_display(thinking_context)
                except Exception as cleanup_error:
                    logger.debug(f"Error cleaning up thinking context: {cleanup_error}")

            # Clean up any live streaming panels
            if hasattr(cli_print_tool_output, "_streaming_sessions"):
                # Find any sessions related to this stream
                for call_id in list(cli_print_tool_output._streaming_sessions.keys()):
                    if call_id in _LIVE_STREAMING_PANELS:
                        try:
                            live = _LIVE_STREAMING_PANELS[call_id]
                            live.stop()
                            del _LIVE_STREAMING_PANELS[call_id]
                        except Exception:
                            pass

            # Stop active timer and start idle timer
            try:
                stop_active_timer()
                start_idle_timer()
            except Exception:
                pass

            # Stream cleanup completed

    async def resume_last_stream(self) -> AsyncIterator[TResponseStreamEvent]:
        """Resume the most recently interrupted stream if available.

        Returns an async iterator of stream events identical to calling
        `stream_response` with a continuation prompt appended to the original input.
        """
        if not getattr(self, "_resume_available", False) or not getattr(
            self, "_last_stream_request", None
        ):
            raise AgentsException("No resumable stream available")

        req = self._last_stream_request
        partial = getattr(self, "_last_stream_partial", "") or ""

        # Construct a continuation prompt that asks the model to continue the previous output
        continuation_text = (
            "Please continue the previous response. Previous partial output:\n\n" + partial
        )

        original_input = req.get("input")
        if isinstance(original_input, str):
            new_input = original_input + "\n\n" + continuation_text
        else:
            # Append a user message to continue
            new_input = list(original_input) + [{"role": "user", "content": continuation_text}]

        # Clear resume state to avoid accidental reuse
        try:
            self._resume_available = False
            self._last_stream_partial = ""
            self._last_stream_request = None
        except Exception:
            pass

        # Delegate to standard streaming API
        return self.stream_response(
            req.get("system_instructions"),
            new_input,
            req.get("model_settings"),
            req.get("tools"),
            req.get("output_schema"),
            req.get("handoffs"),
            req.get("tracing"),
        )

    @overload
    async def _fetch_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchema | None,
        handoffs: list[Handoff],
        span: Span[GenerationSpanData],
        tracing: ModelTracing,
        stream: Literal[True],
    ) -> tuple[Response, AsyncStream[ChatCompletionChunk]]: ...

    @overload
    async def _fetch_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchema | None,
        handoffs: list[Handoff],
        span: Span[GenerationSpanData],
        tracing: ModelTracing,
        stream: Literal[False],
    ) -> ChatCompletion: ...

    async def _fetch_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchema | None,
        handoffs: list[Handoff],
        span: Span[GenerationSpanData],
        tracing: ModelTracing,
        stream: bool = False,
    ) -> ChatCompletion | tuple[Response, AsyncStream[ChatCompletionChunk]]:
        # start by re-fetching self.is_ollama
        self.is_ollama = os.getenv("OLLAMA") is not None and os.getenv("OLLAMA").lower() == "true"

        # Build the message list from `input` only.
        # History is already included in `input` via cli.py's history_context mechanism:
        # cli.py passes history_context (= message_history) as part of conversation_input
        # to Runner.run, which passes it as original_input through to _fetch_response.
        # Prepending message_history again would send every historical message twice.
        converted_messages = []
        new_messages = self._converter.items_to_messages(input, model_instance=self)
        converted_messages.extend(new_messages)

        if system_instructions:
            # Check if we already have a system message
            has_system = any(msg.get("role") == "system" for msg in converted_messages)
            if not has_system:
                converted_messages.insert(
                    0,
                    {
                        "content": system_instructions,
                        "role": "system",
                    },
                )

        # Add support for prompt caching for claude (not automatically applied)
        # Gemini supports it too
        # https://www.anthropic.com/news/token-saving-updates
        # Maximize cache efficiency by using up to 4 cache_control blocks
        if (str(self.model).startswith("claude") or "gemini" in str(self.model)) and len(
            converted_messages
        ) > 0:
            # Strategy: Cache the most valuable messages for maximum savings
            # 1. System message (always first priority)
            # 2. Long user messages (high token count)
            # 3. Assistant messages with tool calls (complex context)
            # 4. Recent context (last message)

            cache_candidates = []

            # Always cache system message if present
            for i, msg in enumerate(converted_messages):
                if msg.get("role") == "system":
                    cache_candidates.append((i, len(str(msg.get("content", ""))), "system"))
                    break

            # Find long user messages and assistant messages with tool calls
            for i, msg in enumerate(converted_messages):
                content_len = len(str(msg.get("content", "")))
                role = msg.get("role")

                if role == "user" and content_len > 500:  # Long user messages
                    cache_candidates.append((i, content_len, "user"))
                elif role == "assistant" and msg.get("tool_calls"):  # Tool calls
                    cache_candidates.append(
                        (i, content_len + 200, "assistant_tools")
                    )  # Bonus for tool calls

            # Always consider the last message for recent context
            if len(converted_messages) > 1:
                last_idx = len(converted_messages) - 1
                last_msg = converted_messages[last_idx]
                last_content_len = len(str(last_msg.get("content", "")))
                cache_candidates.append((last_idx, last_content_len, "recent"))

            # Sort by value (content length) and select top 4 unique indices
            cache_candidates.sort(key=lambda x: x[1], reverse=True)
            selected_indices = []
            for idx, _, msg_type in cache_candidates:
                if idx not in selected_indices:
                    selected_indices.append(idx)
                    if len(selected_indices) >= 4:  # Max 4 cache blocks
                        break

            # Apply cache_control to selected messages
            for idx in selected_indices:
                msg_copy = converted_messages[idx].copy()
                msg_copy["cache_control"] = {"type": "ephemeral"}
                converted_messages[idx] = msg_copy
        if tracing.include_data():
            span.span_data.input = converted_messages

        # IMPORTANT: Always sanitize the message list to prevent tool call errors
        # This is critical to fix common errors with tool/assistant sequences
        try:
            from cai.util import fix_message_list

            prev_length = len(converted_messages)
            converted_messages = fix_message_list(converted_messages)
            new_length = len(converted_messages)

            # Log if the message list was changed significantly
            if new_length != prev_length:
                logger.debug(f"Message list was fixed: {prev_length} -> {new_length} messages")
        except Exception:
            pass

        parallel_tool_calls = (
            True if model_settings.parallel_tool_calls and tools and len(tools) > 0 else NOT_GIVEN
        )
        tool_choice = self._converter.convert_tool_choice(model_settings.tool_choice)
        response_format = self._converter.convert_response_format(output_schema)
        converted_tools = [ToolConverter.to_openai(tool) for tool in tools] if tools else []

        for handoff in handoffs:
            converted_tools.append(ToolConverter.convert_handoff_tool(handoff))

        if _debug.DONT_LOG_MODEL_DATA:
            logger.debug("Calling LLM")
        else:
            logger.debug(
                f"{json.dumps(converted_messages, indent=2)}\n"
                f"Tools:\n{json.dumps(converted_tools, indent=2)}\n"
                f"Stream: {stream}\n"
                f"Tool choice: {tool_choice}\n"
                f"Response format: {response_format}\n"
                f"Using OLLAMA: {self.is_ollama}\n"
            )

        # Only include `store` when explicitly configured on the model settings.
        # Previously we defaulted to True which caused local proxies to attempt
        # DB writes (and fail if no DB was configured). To avoid surprising
        # behavior, omit `store` by default and only send it when set.
        store = None
        if model_settings.store is not None:
            store = self._non_null_or_not_given(model_settings.store)

        # Check if we should use the agent's model instead of self.model
        # This prioritizes the model from Agent when available
        agent_model = None
        if hasattr(model_settings, "agent_model") and model_settings.agent_model:
            agent_model = model_settings.agent_model
            logger.debug(f"Using agent model: {agent_model} instead of {self.model}")

        # Prepare kwargs for the API call
        kwargs = {
            "model": agent_model if agent_model else self.model,
            "messages": converted_messages,
            "tools": converted_tools or NOT_GIVEN,
            "temperature": self._non_null_or_not_given(model_settings.temperature),
            "top_p": self._non_null_or_not_given(model_settings.top_p),
            "frequency_penalty": self._non_null_or_not_given(model_settings.frequency_penalty),
            "presence_penalty": self._non_null_or_not_given(model_settings.presence_penalty),
            "max_tokens": self._non_null_or_not_given(model_settings.max_tokens),
            "tool_choice": NOT_GIVEN if not converted_tools else tool_choice,
            "response_format": NOT_GIVEN if response_format is None else response_format,
            "parallel_tool_calls": parallel_tool_calls,
            "stream": stream,
            "stream_options": {"include_usage": True} if stream else NOT_GIVEN,
            "extra_headers": _HEADERS,
        }

        # Add store only when explicitly configured
        if store is not None:
            kwargs["store"] = store

        # Determine provider based on model string
        model_str = str(kwargs["model"]).lower()

        if "alias" in model_str and "alias1.5" not in model_str:  # NOTE: exclude alias1.5
            kwargs["api_base"] = "https://api.aliasrobotics.com:666/"
            kwargs["custom_llm_provider"] = "openai"
            kwargs["api_key"] = os.getenv("ALIAS_API_KEY", "REDACTED_ALIAS_KEY")
        elif "/" in model_str:
            # Handle provider/model format
            provider = model_str.split("/")[0]

            # Apply provider-specific configurations
            if provider == "ollama_cloud":
                # Ollama Cloud configuration
                ollama_api_key = os.getenv("OLLAMA_API_KEY")
                ollama_api_base = os.getenv("OLLAMA_API_BASE", "https://ollama.com")

                if ollama_api_key:
                    kwargs["api_key"] = ollama_api_key
                if ollama_api_base:
                    kwargs["api_base"] = ollama_api_base

                # Drop params not supported by Ollama
                litellm.drop_params = True
                kwargs.pop("parallel_tool_calls", None)
                kwargs.pop("store", None)
                if not converted_tools:
                    kwargs.pop("tool_choice", None)
            elif provider == "deepseek":
                litellm.drop_params = True
                kwargs.pop("parallel_tool_calls", None)
                kwargs.pop("store", None)  # DeepSeek doesn't support store parameter
                # Remove tool_choice if no tools are specified
                if not converted_tools:
                    kwargs.pop("tool_choice", None)

                # Add reasoning support for DeepSeek
                # DeepSeek supports reasoning_effort parameter
                if hasattr(model_settings, "reasoning_effort") and model_settings.reasoning_effort:
                    kwargs["reasoning_effort"] = model_settings.reasoning_effort
                else:
                    # Default to "low" reasoning effort if model supports it
                    kwargs["reasoning_effort"] = "low"
            elif provider == "claude" or "claude" in model_str:
                litellm.drop_params = True
                kwargs.pop("store", None)
                kwargs.pop(
                    "parallel_tool_calls", None
                )  # Claude doesn't support parallel tool calls
                # Remove tool_choice if no tools are specified
                if not converted_tools:
                    kwargs.pop("tool_choice", None)

                # Add extended reasoning support for Claude models
                # Supports Claude 3.7, Claude 4, and any model with "thinking" in the name
                has_reasoning_capability = (
                    "thinking" in model_str
                    or
                    # Claude 4 models support reasoning
                    "-4-" in model_str
                    or "sonnet-4" in model_str
                    or "haiku-4" in model_str
                    or "opus-4" in model_str
                    or "3.7" in model_str
                )

                if has_reasoning_capability:
                    # Clean the model name by removing "thinking" before sending to API
                    clean_model = kwargs["model"]
                    if isinstance(clean_model, str) and "thinking" in clean_model.lower():
                        # Remove "thinking" and clean up any extra spaces/separators
                        clean_model = re.sub(
                            r"[_-]?thinking[_-]?", "", clean_model, flags=re.IGNORECASE
                        )
                        clean_model = re.sub(
                            r"[-_]{2,}", "-", clean_model
                        )  # Clean up multiple separators
                        clean_model = clean_model.strip(
                            "-_"
                        )  # Clean up leading/trailing separators
                        kwargs["model"] = clean_model

                    # Check if message history is compatible with reasoning
                    messages = kwargs.get("messages", [])
                    is_compatible = _check_reasoning_compatibility(messages)

                    if is_compatible:
                        kwargs["reasoning_effort"] = (
                            "low"  # Use reasoning_effort instead of thinking
                        )
            elif provider == "gemini":
                kwargs.pop("parallel_tool_calls", None)
                # Add any specific gemini settings if needed
        else:
            # Handle models without provider prefix
            if "claude" in model_str or "anthropic" in model_str:
                litellm.drop_params = True
                # Remove parameters that Anthropic doesn't support
                kwargs.pop("store", None)
                kwargs.pop("parallel_tool_calls", None)
                # Remove tool_choice if no tools are specified
                if not converted_tools:
                    kwargs.pop("tool_choice", None)

                # Add extended reasoning support for Claude models
                # Supports Claude 3.7, Claude 4, and any model with "thinking" in the name
                has_reasoning_capability = "thinking" in model_str

                if has_reasoning_capability:
                    # Clean the model name by removing "thinking" before sending to API
                    clean_model = kwargs["model"]
                    if isinstance(clean_model, str) and "thinking" in clean_model.lower():
                        # Remove "thinking" and clean up any extra spaces/separators
                        clean_model = re.sub(
                            r"[_-]?thinking[_-]?", "", clean_model, flags=re.IGNORECASE
                        )
                        clean_model = re.sub(
                            r"[-_]{2,}", "-", clean_model
                        )  # Clean up multiple separators
                        clean_model = clean_model.strip(
                            "-_"
                        )  # Clean up leading/trailing separators
                        kwargs["model"] = clean_model

                    # Check if message history is compatible with reasoning
                    messages = kwargs.get("messages", [])
                    is_compatible = _check_reasoning_compatibility(messages)

                    if is_compatible:
                        kwargs["reasoning_effort"] = (
                            "low"  # Use reasoning_effort instead of thinking
                        )
            elif "gemini" in model_str:
                kwargs.pop("parallel_tool_calls", None)
            elif "qwen" in model_str or ":" in model_str:
                # Handle Ollama-served models with custom formats (e.g., alias1)
                # These typically need the Ollama provider
                litellm.drop_params = True
                kwargs.pop("parallel_tool_calls", None)
                if self.is_ollama:
                    kwargs.pop("store", None)  # Ollama doesn't support store parameter
                # These models may not support certain parameters
                if not converted_tools:
                    kwargs.pop("tool_choice", None)
                # Don't add custom_llm_provider here to avoid duplication with Ollama provider
                if self.is_ollama:
                    # Clean kwargs for ollama to avoid parameter conflicts
                    for param in ["custom_llm_provider"]:
                        kwargs.pop(param, None)
            elif any(x in model_str for x in ["o1", "o3", "o4"]):
                # Handle OpenAI reasoning models (o1, o3, o4)
                kwargs.pop("parallel_tool_calls", None)
                # Add reasoning effort if provided
                if hasattr(model_settings, "reasoning_effort"):
                    kwargs["reasoning_effort"] = model_settings.reasoning_effort

        # Fallback: if no api_base has been set yet and LOCAL_API_BASE is configured,
        # use it so that a LiteLLM proxy serving any custom model name (e.g. "reasoner",
        # "support") is reachable without requiring provider-prefix routing.
        if "api_base" not in kwargs:
            local_api_base = os.getenv("LOCAL_API_BASE", "").strip()
            local_api_key = os.getenv("LOCAL_API_KEY", "").strip()
            if local_api_base:
                kwargs["api_base"] = local_api_base
                kwargs["custom_llm_provider"] = "openai"
                if local_api_key:
                    kwargs["api_key"] = local_api_key
                litellm.drop_params = True

        # Filter out NotGiven/None/empty-string values to avoid JSON serialization issues
        filtered_kwargs = {}
        for key, value in kwargs.items():
            # Skip explicit NOT_GIVEN sentinel
            if value is NOT_GIVEN:
                continue
            # Treat None and empty-string as absent for API kwargs
            if value is None:
                continue
            if isinstance(value, str) and value == "":
                continue
            filtered_kwargs[key] = value
        # Ensure optional keys remain present as NOT_GIVEN when absent
        for _k in ("tools", "tool_choice", "response_format", "stream_options"):
            if _k not in filtered_kwargs:
                filtered_kwargs[_k] = NOT_GIVEN
        kwargs = filtered_kwargs

        # If a client was explicitly provided (for tests or caller-provided client), prefer using it.
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                if stream:
                    # When streaming, the client returns an async iterator/stream.
                    stream_obj = await client.chat.completions.create(**kwargs)
                    response = Response(
                        id=FAKE_RESPONSES_ID,
                        created_at=time.time(),
                        model=self.model,
                        object="response",
                        output=[],
                        tool_choice=_sanitize_tool_choice_value(tool_choice),
                        top_p=model_settings.top_p,
                        temperature=model_settings.temperature,
                        tools=[],
                        parallel_tool_calls=parallel_tool_calls or False,
                    )
                    return response, stream_obj
                else:
                    return await client.chat.completions.create(**kwargs)
            except Exception:
                # Fall back to the litellm/openai paths below if the provided client fails
                pass

        # Add retry logic for rate limits
        max_retries = 3
        retry_count = 0

        # Check if this is Ollama Cloud (ollama_cloud/ prefix)
        # Ollama Cloud is OpenAI-compatible, so we bypass LiteLLM to avoid parsing issues
        is_ollama_cloud = "ollama_cloud/" in model_str

        if is_ollama_cloud:
            # Use AsyncOpenAI client directly for Ollama Cloud
            # Ollama Cloud is fully OpenAI-compatible at /v1/chat/completions
            try:
                # Configure the client with Ollama Cloud settings
                ollama_api_key = os.getenv("OLLAMA_API_KEY") or os.getenv("OPENAI_API_KEY")
                ollama_base_url = os.getenv("OLLAMA_API_BASE", "https://ollama.com")

                # Ensure the URL has /v1 for OpenAI compatibility
                if not ollama_base_url.endswith("/v1"):
                    ollama_base_url = f"{ollama_base_url}/v1"

                # Create a temporary client configured for Ollama Cloud
                ollama_client = AsyncOpenAI(api_key=ollama_api_key, base_url=ollama_base_url)

                # Remove the ollama_cloud/ prefix from the model name
                clean_model = kwargs["model"].replace("ollama_cloud/", "")
                kwargs["model"] = clean_model

                # Remove LiteLLM-specific parameters
                kwargs.pop("extra_headers", None)
                kwargs.pop("api_key", None)
                kwargs.pop("api_base", None)
                kwargs.pop("custom_llm_provider", None)

                # Call Ollama Cloud using OpenAI-compatible API
                if stream:
                    return await ollama_client.chat.completions.create(**kwargs)
                else:
                    return await ollama_client.chat.completions.create(**kwargs)

            except Exception as e:
                # If Ollama Cloud fails, raise with helpful message
                raise Exception(
                    f"Error connecting to Ollama Cloud: {str(e)}\n"
                    f"Verify OLLAMA_API_KEY and OLLAMA_API_BASE are configured correctly."
                ) from e

        # Preserve a raw copy of kwargs for downstream litellm/openai helpers
        try:
            kwargs_raw = dict(kwargs)
        except Exception:
            kwargs_raw = kwargs

        while retry_count < max_retries:
            try:
                if self.is_ollama:
                    return await self._fetch_response_litellm_ollama(
                        kwargs, model_settings, tool_choice, stream, parallel_tool_calls
                    )
                else:
                    return await self._fetch_response_litellm_openai(
                        kwargs,
                        model_settings,
                        tool_choice,
                        stream,
                        parallel_tool_calls,
                        raw_kwargs=kwargs_raw,
                    )
            except litellm.exceptions.RateLimitError as e:
                retry_count += 1
                if retry_count >= max_retries:
                    print(f"\n❌ Rate limit exceeded after {max_retries} retries")
                    raise

                print(
                    f"\n⏳ Rate limit reached - Too many requests (attempt {retry_count}/{max_retries})"
                )
                # Try to extract retry delay from error response or use default
                retry_delay = 60  # Default delay in seconds
                try:
                    # Extract the JSON part from the error message
                    json_str = str(e.message).split("VertexAIException - ")[-1]
                    error_details = json.loads(json_str)

                    retry_info = next(
                        (
                            detail
                            for detail in error_details.get("error", {}).get("details", [])
                            if detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo"
                        ),
                        None,
                    )
                    if retry_info and "retryDelay" in retry_info:
                        retry_delay = int(retry_info["retryDelay"].rstrip("s"))
                except Exception:
                    # Try other common formats
                    import re

                    error_str = str(e)

                    # Look for "Retry-After" header or similar patterns
                    retry_match = re.search(r"retry[_-]?after[:\s]+(\d+)", error_str, re.IGNORECASE)
                    if retry_match:
                        retry_delay = int(retry_match.group(1))
                    # Look for "wait X seconds" patterns
                    elif wait_match := re.search(
                        r"wait\s+(\d+)\s+seconds?", error_str, re.IGNORECASE
                    ):
                        retry_delay = int(wait_match.group(1))
                    # Look for explicit retry delay mentions
                    elif delay_match := re.search(
                        r"retry\s+in\s+(\d+)\s+seconds?", error_str, re.IGNORECASE
                    ):
                        retry_delay = int(delay_match.group(1))

                # Use exponential backoff with jitter if no explicit delay found
                if retry_count > 1 and retry_delay == 60:
                    import random

                    retry_delay = min(300, retry_delay * retry_count) + random.randint(0, 10)

                print(f"💤 Waiting {retry_delay}s before retry... (Rate limit protection)")
                await asyncio.sleep(retry_delay)  # Use async sleep instead of time.sleep
                continue  # Retry the request
            except httpx.HTTPStatusError as e:
                # Handle HTTP status errors from underlying HTTP client (e.g., 429 / 503)
                status = None
                try:
                    status = e.response.status_code if e.response is not None else None
                except Exception:
                    status = None

                if status in (429, 503):
                    retry_count += 1
                    if retry_count >= max_retries:
                        print(f"\n❌ Rate limit or server overload after {max_retries} retries")
                        raise

                    # Try to honor Retry-After header if present
                    retry_delay = None
                    try:
                        if e.response is not None:
                            ra = e.response.headers.get("Retry-After")
                            if ra:
                                retry_delay = int(ra)
                    except Exception:
                        retry_delay = None

                    if retry_delay is None:
                        import random

                        retry_delay = min(300, 60 * retry_count) + random.randint(0, 10)

                    print(
                        f"\n⏳ HTTP {status} - retrying in {retry_delay}s (attempt {retry_count}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    # Non-retryable HTTP status - re-raise
                    raise
            except httpx.RequestError as e:
                # Network-level error - retry with backoff
                retry_count += 1
                if retry_count >= max_retries:
                    raise
                import random

                retry_delay = min(300, 2**retry_count) + random.uniform(0, 0.1 * (2**retry_count))
                logger.debug(
                    f"Network error during model call, retrying in {retry_delay:.1f}s: {e}"
                )
                await asyncio.sleep(retry_delay)
                continue  # Retry

            except litellm.exceptions.BadRequestError as e:
                error_msg = str(e)

                # Handle Claude reasoning/thinking compatibility errors
                if (
                    "Expected `thinking` or `redacted_thinking`, but found `text`" in error_msg
                    or "When `thinking` is enabled, a final `assistant` message must start with a thinking block"
                    in error_msg
                ):
                    # Retry without reasoning_effort
                    retry_kwargs = kwargs.copy()
                    retry_kwargs.pop("reasoning_effort", None)

                    try:
                        if stream:
                            response = Response(
                                id=FAKE_RESPONSES_ID,
                                created_at=time.time(),
                                model=self.model,
                                object="response",
                                output=[],
                                tool_choice=_sanitize_tool_choice_value(tool_choice),
                                top_p=model_settings.top_p,
                                temperature=model_settings.temperature,
                                tools=[],
                                parallel_tool_calls=parallel_tool_calls or False,
                            )
                            stream_obj = await litellm.acompletion(**retry_kwargs)
                            return response, stream_obj
                        else:
                            ret = await litellm.acompletion(**retry_kwargs)
                            return ret
                    except Exception:
                        # If retry also fails, raise the original error
                        raise e

                # print(color("BadRequestError encountered: " + str(e), fg="yellow"))
                if "LLM Provider NOT provided" in str(e):
                    model_str = str(self.model).lower()
                    provider = None
                    is_qwen = "qwen" in model_str or ":" in model_str

                    # Special handling for Qwen models
                    if is_qwen:
                        try:
                            # Use the specialized Qwen approach first
                            return await self._fetch_response_litellm_ollama(
                                kwargs, model_settings, tool_choice, stream, parallel_tool_calls
                            )
                        except Exception as qwen_e:
                            print(qwen_e)
                            # If that fails, try our direct OpenAI approach
                            qwen_params = kwargs.copy()
                            qwen_params["api_base"] = get_ollama_api_base()
                            qwen_params["custom_llm_provider"] = "openai"  # Use openai provider

                            # Make sure tools are passed
                            if "tools" in kwargs and kwargs["tools"]:
                                qwen_params["tools"] = kwargs["tools"]
                            if "tool_choice" in kwargs and kwargs["tool_choice"] is not NOT_GIVEN:
                                qwen_params["tool_choice"] = kwargs["tool_choice"]

                            try:
                                if stream:
                                    # Streaming case
                                    response = Response(
                                        id=FAKE_RESPONSES_ID,
                                        created_at=time.time(),
                                        model=self.model,
                                        object="response",
                                        output=[],
                                        tool_choice=_sanitize_tool_choice_value(tool_choice),
                                        top_p=model_settings.top_p,
                                        temperature=model_settings.temperature,
                                        tools=[],
                                        parallel_tool_calls=parallel_tool_calls or False,
                                    )
                                    stream_obj = await litellm.acompletion(**qwen_params)
                                    return response, stream_obj
                                else:
                                    # Non-streaming case
                                    ret = await litellm.acompletion(**qwen_params)
                                    return ret
                            except Exception as direct_e:
                                # All approaches failed, log and raise the original error
                                print(
                                    f"All Qwen approaches failed. Original error: {str(e)}, Direct error: {str(direct_e)}"
                                )
                                raise e

                    # Try to detect provider from model string
                    if "/" in model_str:
                        provider = model_str.split("/")[0]

                    if provider:
                        # Add provider-specific settings based on detected provider
                        provider_kwargs = kwargs.copy()
                        if provider == "deepseek":
                            provider_kwargs["custom_llm_provider"] = "deepseek"
                            provider_kwargs.pop(
                                "store", None
                            )  # DeepSeek doesn't support store parameter
                            provider_kwargs.pop(
                                "parallel_tool_calls", None
                            )  # DeepSeek doesn't support parallel tool calls

                            # Add reasoning support for DeepSeek
                            if (
                                hasattr(model_settings, "reasoning_effort")
                                and model_settings.reasoning_effort
                            ):
                                provider_kwargs["reasoning_effort"] = (
                                    model_settings.reasoning_effort
                                )
                            else:
                                # Default to "low" reasoning effort
                                provider_kwargs["reasoning_effort"] = "low"
                        elif provider == "claude" or "claude" in model_str:
                            provider_kwargs["custom_llm_provider"] = "anthropic"
                            provider_kwargs.pop(
                                "store", None
                            )  # Claude doesn't support store parameter
                            provider_kwargs.pop(
                                "parallel_tool_calls", None
                            )  # Claude doesn't support parallel tool calls

                            # Add extended reasoning support for Claude models
                            if "thinking" in model_str:
                                # Clean the model name by removing "thinking" before sending to API
                                clean_model = provider_kwargs["model"]
                                if (
                                    isinstance(clean_model, str)
                                    and "thinking" in clean_model.lower()
                                ):
                                    # Remove "thinking" and clean up any extra spaces/separators
                                    clean_model = re.sub(
                                        r"[_-]?thinking[_-]?", "", clean_model, flags=re.IGNORECASE
                                    )
                                    clean_model = re.sub(
                                        r"[-_]{2,}", "-", clean_model
                                    )  # Clean up multiple separators
                                    clean_model = clean_model.strip(
                                        "-_"
                                    )  # Clean up leading/trailing separators
                                    provider_kwargs["model"] = clean_model

                                # Check if message history is compatible with reasoning
                                messages = provider_kwargs.get("messages", [])
                                is_compatible = _check_reasoning_compatibility(messages)

                                if is_compatible:
                                    provider_kwargs["reasoning_effort"] = (
                                        "low"  # Use reasoning_effort instead of thinking
                                    )
                        elif provider == "gemini":
                            provider_kwargs["custom_llm_provider"] = "gemini"
                            provider_kwargs.pop(
                                "store", None
                            )  # Gemini doesn't support store parameter
                            provider_kwargs.pop(
                                "parallel_tool_calls", None
                            )  # Gemini doesn't support parallel tool calls
                        elif (
                            provider == "minimax"
                            or "minimax" in model_str
                            or str(self.model).lower().startswith("mm-")
                        ):
                            # Route MiniMax models to the minimax provider in LiteLLM
                            provider_kwargs["custom_llm_provider"] = "minimax"
                            # Minimax may not support store/parallel_tool_calls params
                            provider_kwargs.pop("store", None)
                            provider_kwargs.pop("parallel_tool_calls", None)
                            try:
                                api_base = get_minimax_api_base()
                                if api_base:
                                    provider_kwargs["api_base"] = api_base
                            except Exception:
                                # If helper not available or fails, continue without api_base
                                pass
                        else:
                            # For unknown providers, try ollama as fallback
                            return await self._fetch_response_litellm_ollama(
                                kwargs, model_settings, tool_choice, stream, parallel_tool_calls
                            )

                        # Attempt the provider-specific call (streaming and non-streaming)
                        try:
                            if stream:
                                response = Response(
                                    id=FAKE_RESPONSES_ID,
                                    created_at=time.time(),
                                    model=self.model,
                                    object="response",
                                    output=[],
                                    tool_choice="auto"
                                    if tool_choice is None or tool_choice == NOT_GIVEN
                                    else cast(Literal["auto", "required", "none"], tool_choice),
                                    top_p=model_settings.top_p,
                                    temperature=model_settings.temperature,
                                    tools=[],
                                    parallel_tool_calls=parallel_tool_calls or False,
                                )
                                stream_obj = await litellm.acompletion(**provider_kwargs)
                                return response, stream_obj
                            else:
                                ret = await litellm.acompletion(**provider_kwargs)
                                return ret
                        except Exception as direct_e:
                            # All provider-specific approaches failed, log and raise the original error
                            print(
                                f"All provider approaches failed. Original error: {str(e)}, Direct error: {str(direct_e)}"
                            )
                            raise e

                # Check for message sequence errors
                if (
                    "An assistant message with 'tool_calls'" in str(e)
                    or "`tool_use` blocks must be followed by a user message with `tool_result`"
                    in str(e)  # noqa: E501 # pylint: disable=C0301
                    or "`tool_use` ids were found without `tool_result` blocks immediately after"
                    in str(e)  # noqa: E501 # pylint: disable=C0301
                    or "An assistant message with 'tool_calls' must be followed by tool messages"
                    in str(e)
                    or "messages with role 'tool' must be a response to a preceeding message with 'tool_calls'"
                    in str(e)
                ):
                    print("⚠️  Message sequence error - Tool calls and results are out of order")

                    # Use the pretty message history printer instead of the simple loop
                    try:
                        from cai.util import print_message_history

                        print("\n📋 Current message sequence:")
                        print_message_history(kwargs["messages"], title="Message History")
                    except ImportError:
                        # Fall back to simple printing if the function isn't available
                        print("\n📋 Current message sequence:")
                        for i, msg in enumerate(kwargs["messages"]):
                            role = msg.get("role", "unknown")
                            content_type = (
                                "text"
                                if isinstance(msg.get("content"), str)
                                else "list"
                                if isinstance(msg.get("content"), list)
                                else "None"
                                if msg.get("content") is None
                                else type(msg.get("content")).__name__
                            )
                            tool_calls = "with tool_calls" if msg.get("tool_calls") else ""
                            tool_call_id = (
                                f", tool_call_id: {msg.get('tool_call_id')}"
                                if msg.get("tool_call_id")
                                else ""
                            )

                            print(
                                f"  [{i}] {role}{tool_call_id} (content: {content_type}) {tool_calls}"
                            )

                    # NOTE: EDGE CASE: Report Agent CTRL C error
                    #
                    # This fix CTRL-C error when message list is incomplete
                    # When a tool is not finished but the LLM generates a tool call
                    try:
                        from cai.util import fix_message_list

                        print("🔧 Auto-fixing message sequence...")
                        fixed_messages = fix_message_list(kwargs["messages"])

                        # Show the fixed messages if they're different
                        if fixed_messages != kwargs["messages"]:
                            try:
                                from cai.util import print_message_history

                                print_message_history(
                                    fixed_messages, title="Fixed Message Sequence"
                                )
                            except ImportError:
                                print("✅ Message sequence fixed successfully")

                        kwargs["messages"] = fixed_messages
                    except Exception:
                        pass

                    return await self._fetch_response_litellm_openai(
                        kwargs, model_settings, tool_choice, stream, parallel_tool_calls
                    )

                # this captures an error related to the fact
                # that the messages list contains an empty
                # content position
                if "expected a string, got null" in str(e):
                    print("⚠️  Empty content detected - Filling with placeholder")
                    # Fix for null content in messages
                    kwargs["messages"] = [
                        msg if msg.get("content") is not None else {**msg, "content": ""}
                        for msg in kwargs["messages"]
                    ]
                    return await self._fetch_response_litellm_openai(
                        kwargs, model_settings, tool_choice, stream, parallel_tool_calls
                    )

                # Handle Anthropic error for empty text content blocks
                if "text content blocks must be non-empty" in str(
                    e
                ) or "cache_control cannot be set for empty text blocks" in str(e):  # noqa
                    # Print the error message only once
                    print(
                        "⚠️  Empty text blocks detected - Adding placeholder content"
                    ) if not self.empty_content_error_shown else None
                    self.empty_content_error_shown = True

                    # Fix for empty content in messages for Anthropic models
                    kwargs["messages"] = [
                        msg
                        if msg.get("content") not in [None, ""]
                        else {**msg, "content": "Empty content block"}
                        for msg in kwargs["messages"]
                    ]
                    return await self._fetch_response_litellm_openai(
                        kwargs, model_settings, tool_choice, stream, parallel_tool_calls
                    )
                # Check for Python formatting errors - NOT context errors
                if "Cannot specify ',' with 's'" in str(e):
                    print("\n❌ Python formatting error - Not a context error")
                    print("⚠️  There's a bug in the code trying to format strings as numbers")
                    print(f"Error: {str(e)}")
                    raise
                # Check for context length errors in BadRequestError
                if (
                    "context_length_exceeded" in str(e)
                    or "prompt is too long" in str(e).lower()
                    or "maximum context length" in str(e).lower()
                    or "max_tokens" in str(e)
                    and "exceeded" in str(e).lower()
                    or "too many tokens" in str(e).lower()
                    or "token limit" in str(e).lower()
                ):
                    print("\n📦 Context window exceeded - Message history too long")

                    # Try to extract token info from different error formats
                    import re

                    error_str = str(e)

                    # Pattern 1: "X tokens > Y maximum" (Anthropic)
                    match1 = re.search(r"(\d+)\s*tokens?\s*>\s*(\d+)\s*maximum", error_str)
                    # Pattern 2: "requested X tokens...maximum context length is Y" (OpenAI)
                    match2 = re.search(r"requested\s+(\d+)\s+tokens.*maximum.*?(\d+)", error_str)
                    # Pattern 3: "This model's maximum context length is X tokens, however you requested Y"
                    match3 = re.search(
                        r"maximum context length is\s+(\d+).*requested\s+(\d+)", error_str
                    )

                    if match1:
                        used_tokens = int(match1.group(1))
                        max_tokens = int(match1.group(2))
                        print(f"🎯 Actual: {used_tokens:,} / {max_tokens:,} tokens")
                    elif match2:
                        used_tokens = int(match2.group(1))
                        max_tokens = int(match2.group(2))
                        print(f"🎯 Requested: {used_tokens:,} tokens (max: {max_tokens:,})")
                    elif match3:
                        max_tokens = int(match3.group(1))
                        used_tokens = int(match3.group(2))
                        print(f"🎯 Requested: {used_tokens:,} tokens (max: {max_tokens:,})")
                    print("\n💡 Quick fixes:")
                    print("  • /flush - Clear conversation history")
                    print("  • /compact - Manually compact context")
                    print("  • /model <larger-model> - Switch to model with more context")

                    raise
                else:
                    raise e

    async def _fetch_response_litellm_openai(
        self,
        kwargs: dict,
        model_settings: ModelSettings,
        tool_choice: ChatCompletionToolChoiceOptionParam | NotGiven,
        stream: bool,
        parallel_tool_calls: bool,
        raw_kwargs: dict | None = None,
    ) -> ChatCompletion | tuple[Response, AsyncStream[ChatCompletionChunk]]:
        """
        Handle standard LiteLLM API calls for OpenAI and compatible models.
        If a ContextWindowExceededError occurs due to a tool_call id being
        too long, truncate all tool_call ids in the messages to 40 characters
        and retry once silently.
        """
        # Normalize any OpenAI `NOT_GIVEN`/`NotGiven` sentinels so LiteLLM
        # receives plain Python values it understands. LiteLLM raises on
        # sentinel objects like `openai.NOT_GIVEN`, so remove keys whose
        # value is that sentinel (we keep None/empty-string handling upstream).
        try:
            try:
                from openai import NOT_GIVEN as _OPENAI_NOT_GIVEN  # type: ignore
            except Exception:
                _OPENAI_NOT_GIVEN = None
            # Remove any keys whose value is the OpenAI sentinel or an instance
            # of a class named 'NotGiven' (older/newer OpenAI variants).
            for k in list(kwargs.keys()):
                v = kwargs.get(k)
                if v is _OPENAI_NOT_GIVEN:
                    kwargs.pop(k, None)
                    continue
                if v is not None and type(v).__name__ == "NotGiven":
                    kwargs.pop(k, None)
                    continue
        except Exception:
            # Non-fatal: proceed and let downstream call fail if still invalid.
            pass

        # Determine whether to call a direct OpenAI-compatible client (self._client)
        use_direct_client = False
        client_kwargs = kwargs
        try:
            if getattr(self, "_client", None) is not None:
                chat_obj = getattr(self._client, "chat", None)
                if chat_obj is not None and getattr(chat_obj, "completions", None) is not None:
                    create_fn = getattr(chat_obj.completions, "create", None)
                    if callable(create_fn):
                        use_direct_client = True
            # Prefer raw_kwargs for direct client calls when available
            if use_direct_client and raw_kwargs is not None:
                client_kwargs = raw_kwargs
            # Strip LiteLLM-only params that the OpenAI AsyncClient doesn't accept
            if use_direct_client:
                _litellm_only = {"api_base", "api_key", "custom_llm_provider",
                                 "extra_headers", "store"}
                client_kwargs = {k: v for k, v in client_kwargs.items()
                                 if k not in _litellm_only}

        except Exception:
            use_direct_client = False

        try:
            if stream:
                if use_direct_client:
                    stream_obj = await self._client.chat.completions.create(**client_kwargs)
                else:
                    stream_obj = await litellm.acompletion(**kwargs)

                response = Response(
                    id=FAKE_RESPONSES_ID,
                    created_at=time.time(),
                    model=self.model,
                    object="response",
                    output=[],
                    tool_choice=_sanitize_tool_choice_value(tool_choice),
                    top_p=model_settings.top_p,
                    temperature=model_settings.temperature,
                    tools=[],
                    parallel_tool_calls=parallel_tool_calls or False,
                )
                return response, stream_obj
            else:
                # Standard OpenAI handling for non-streaming
                if use_direct_client:
                    ret = await self._client.chat.completions.create(**client_kwargs)
                else:
                    ret = await litellm.acompletion(**kwargs)
                return ret
        except Exception as e:
            error_msg = str(e)
            # Handle both OpenAI and Anthropic error messages for tool_call_id
            if (
                "string too long" in error_msg
                or "Invalid 'messages" in error_msg
                and "tool_call_id" in error_msg
                and "maximum length" in error_msg
            ):
                # Truncate all tool_call ids in all messages to 40 characters
                messages = kwargs.get("messages", [])
                for msg in messages:
                    # Truncate tool_call_id in the message itself if present
                    if (
                        "tool_call_id" in msg
                        and isinstance(msg["tool_call_id"], str)
                        and len(msg["tool_call_id"]) > 40
                    ):
                        msg["tool_call_id"] = msg["tool_call_id"][:40]
                    # Truncate tool_call ids in tool_calls if present
                    if "tool_calls" in msg and isinstance(msg["tool_calls"], list):
                        for tool_call in msg["tool_calls"]:
                            if (
                                isinstance(tool_call, dict)
                                and "id" in tool_call
                                and isinstance(tool_call["id"], str)
                                and len(tool_call["id"]) > 40
                            ):
                                tool_call["id"] = tool_call["id"][:40]
                kwargs["messages"] = messages
                # Retry once, silently
                if stream:
                    # Retry streaming call once (single request)
                    stream_obj = await litellm.acompletion(**kwargs)
                    response = Response(
                        id=FAKE_RESPONSES_ID,
                        created_at=time.time(),
                        model=self.model,
                        object="response",
                        output=[],
                        tool_choice=_sanitize_tool_choice_value(tool_choice),
                        top_p=model_settings.top_p,
                        temperature=model_settings.temperature,
                        tools=[],
                        parallel_tool_calls=parallel_tool_calls or False,
                    )
                    return response, stream_obj
                else:
                    ret = await litellm.acompletion(**kwargs)
                    return ret
            else:
                # Some providers (via litellm/OpenAI) may return a 500 when the
                # model attempted a `function_call` but generated malformed JSON
                # for the `arguments` field. Detect that case and retry once with
                # tools/functions disabled so the model returns plain text instead
                # of a function call. This avoids surfacing a hard exception when
                # the model produces invalid JSON.
                parse_error_signals = [
                    "Failed to parse tool call arguments",
                    "Failed to parse tool call arguments as JSON",
                    "parse_error",
                    "parse error",
                ]
                lower_msg = error_msg.lower()
                should_retry_without_tools = (
                    any(sig.lower() in lower_msg for sig in parse_error_signals)
                    and "tool" in lower_msg
                )

                if should_retry_without_tools:
                    # Prepare a conservative copy of kwargs without any tools or
                    # function schemas so the model will not attempt to emit a
                    # structured function_call.
                    kwargs_retry = dict(kwargs)
                    kwargs_retry.pop("tools", None)
                    kwargs_retry.pop("functions", None)
                    # Also remove any response_format/store hints which could
                    # confuse downstream providers.
                    kwargs_retry.pop("response_format", None)
                    kwargs_retry.pop("store", None)

                    client_kwargs_retry = (
                        raw_kwargs
                        if (use_direct_client and raw_kwargs is not None)
                        else kwargs_retry
                    )

                    if use_direct_client:
                        _litellm_only = {"api_base", "api_key", "custom_llm_provider",
                                         "extra_headers", "store"}
                        client_kwargs_retry = {k: v for k, v in client_kwargs_retry.items()
                                               if k not in _litellm_only}

                    try:
                        if stream:
                            if use_direct_client:
                                stream_obj = await self._client.chat.completions.create(
                                    **client_kwargs_retry
                                )
                            else:
                                stream_obj = await litellm.acompletion(**kwargs_retry)

                            response = Response(
                                id=FAKE_RESPONSES_ID,
                                created_at=time.time(),
                                model=self.model,
                                object="response",
                                output=[],
                                tool_choice=(
                                    "auto"
                                    if tool_choice is None or tool_choice == NOT_GIVEN
                                    else cast(Literal["auto", "required", "none"], tool_choice)
                                ),
                                top_p=model_settings.top_p,
                                temperature=model_settings.temperature,
                                tools=[],
                                parallel_tool_calls=parallel_tool_calls or False,
                            )
                            return response, stream_obj
                        else:
                            if use_direct_client:
                                ret = await self._client.chat.completions.create(
                                    **client_kwargs_retry
                                )
                            else:
                                ret = await litellm.acompletion(**kwargs_retry)
                            return ret
                    except Exception:
                        # If the retry fails, fall through and re-raise the
                        # original exception for visibility.
                        raise

                raise

    async def _fetch_response_litellm_ollama(
        self,
        kwargs: dict,
        model_settings: ModelSettings,
        tool_choice: ChatCompletionToolChoiceOptionParam | NotGiven,
        stream: bool,
        parallel_tool_calls: bool,
    ) -> ChatCompletion | tuple[Response, AsyncStream[ChatCompletionChunk]]:
        """
        Fetches a response from an Ollama or Qwen model using LiteLLM, ensuring
        that the 'format' parameter is not set to a JSON string, which can cause
        issues with the Ollama API.

        Args:
            kwargs (dict): Parameters for the completion request.
            model_settings (ModelSettings): Model configuration.
            tool_choice (ChatCompletionToolChoiceOptionParam | NotGiven): Tool choice.
            stream (bool): Whether to stream the response.
            parallel_tool_calls (bool): Whether to allow parallel tool calls.

        Returns:
            ChatCompletion or tuple[Response, AsyncStream[ChatCompletionChunk]]:
                The completion response or a tuple for streaming.
        """
        # Extract only supported parameters for Ollama
        ollama_supported_params = {
            "model": kwargs.get("model", ""),
            "messages": kwargs.get("messages", []),
            "stream": kwargs.get("stream", False),
        }

        # Add optional parameters if they exist and are not NOT_GIVEN
        for param in ["temperature", "top_p", "max_tokens"]:
            if param in kwargs and kwargs[param] is not NOT_GIVEN:
                ollama_supported_params[param] = kwargs[param]

        # Add extra headers if available
        if "extra_headers" in kwargs:
            ollama_supported_params["extra_headers"] = kwargs["extra_headers"]

        # Add tools for compatibility with Qwen
        if "tools" in kwargs and kwargs.get("tools") and kwargs.get("tools") is not NOT_GIVEN:
            ollama_supported_params["tools"] = kwargs.get("tools")

        # Remove None values and filter out unsupported parameters
        ollama_kwargs = {
            k: v
            for k, v in ollama_supported_params.items()
            if v is not None and k not in ["response_format", "store"]
        }

        # Check if this is a Qwen model
        model_str = str(self.model).lower()
        _is_qwen = "qwen" in model_str
        api_base = get_ollama_api_base()

        if stream:
            response = Response(
                id=FAKE_RESPONSES_ID,
                created_at=time.time(),
                model=self.model,
                object="response",
                output=[],
                tool_choice=_sanitize_tool_choice_value(tool_choice),
                top_p=model_settings.top_p,
                temperature=model_settings.temperature,
                tools=[],
                parallel_tool_calls=parallel_tool_calls or False,
            )
            # Get streaming response
            stream_obj = await litellm.acompletion(
                **ollama_kwargs, api_base=api_base, custom_llm_provider="openai"
            )
            return response, stream_obj
        else:
            # Get completion response
            return await litellm.acompletion(
                **ollama_kwargs,
                api_base=api_base,
                custom_llm_provider="openai",
            )

    def _get_model_max_tokens(self, model_name: str) -> int:
        """Get the maximum input tokens for a model from pricing.json or default."""
        try:
            import pathlib

            pricing_path = pathlib.Path("pricing.json")
            if pricing_path.exists():
                with open(pricing_path, encoding="utf-8") as f:
                    pricing_data = json.load(f)
                    model_info = pricing_data.get(model_name, {})
                    return model_info.get("max_input_tokens", 200000)
        except Exception:
            pass
        # Default to 200k if not found
        return 200000

    async def _auto_compact_if_needed(
        self,
        estimated_tokens: int,
        input: str | list[TResponseInputItem],
        system_instructions: str | None,
    ) -> tuple[str | list[TResponseInputItem], str | None, bool]:
        """Check if auto-compaction is needed and perform it if necessary.

        Returns:
            tuple: (potentially modified input, potentially modified system_instructions, whether compaction occurred)
        """
        # Check if auto-compaction is disabled
        if os.getenv("CAI_AUTO_COMPACT", "true").lower() == "false":
            return input, system_instructions, False

        # --- CAI_SUPPORT_INTERVAL count-based trigger ---
        # This fires on EVERY API call (not just at the outer CLI-loop level), so it correctly
        # handles agentic sessions where the agent makes many tool calls inside one Runner.run.
        _support_model = os.getenv("CAI_SUPPORT_MODEL")
        _support_interval_raw = os.getenv("CAI_SUPPORT_INTERVAL")
        if _support_model and _support_interval_raw:
            try:
                _support_interval = int(_support_interval_raw)
                if _support_interval > 0:
                    _asst_count = sum(
                        1
                        for m in self.message_history
                        if isinstance(m, dict) and m.get("role") == "assistant"
                    )
                    if _asst_count >= _support_interval:
                        from rich.console import Console as _Console

                        _console = _Console()
                        _console.print(
                            f"\n[bold yellow]⟳ Auto-compact: {_asst_count} LLM responses "
                            f"(threshold {_support_interval}) — summarising with "
                            f"{_support_model}[/bold yellow]"
                        )
                        _did_compact = False
                        try:
                            from cai.repl.commands.memory import (
                                APPLIED_MEMORY_IDS,
                                COMPACTED_SUMMARIES,
                                MEMORY_COMMAND_INSTANCE,
                            )

                            # Avoid mutating the global CompactCommand singleton in
                            # concurrent contexts. Instead, temporarily set the
                            # CAI_MODEL env var for the narrow scope of the
                            # summarization call so the memory summarizer will
                            # pick up the support model without a global mutation.
                            _orig_model_env = os.environ.get("CAI_MODEL")
                            try:
                                os.environ["CAI_MODEL"] = _support_model
                                _summary = await MEMORY_COMMAND_INSTANCE._ai_summarize_history(
                                    self.agent_name
                                )
                            finally:
                                if _orig_model_env is None:
                                    os.environ.pop("CAI_MODEL", None)
                                else:
                                    os.environ["CAI_MODEL"] = _orig_model_env

                            if _summary:
                                # Ensure APPLIED_MEMORY_IDS is reset when overwriting
                                # the in-memory compacted summary so IDs don't
                                # incorrectly reference an old summary.
                                if self.agent_name not in COMPACTED_SUMMARIES:
                                    COMPACTED_SUMMARIES[self.agent_name] = []
                                COMPACTED_SUMMARIES[self.agent_name] = [_summary]
                                APPLIED_MEMORY_IDS[self.agent_name] = []
                                self.message_history.clear()
                                # Re-inject the summary as the first exchange so
                                # the next Runner turn has full context and won't
                                # repeat work that was already attempted.
                                self.message_history.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "<previous_session_memory>\n"
                                            + _summary
                                            + "\n</previous_session_memory>\n\n"
                                            "This is your memory from the previous context window. "
                                            "Use it to continue your work. "
                                            "Do NOT retry any approach already marked as failed or exhausted."
                                        ),
                                    }
                                )
                                self.message_history.append(
                                    {
                                        "role": "assistant",
                                        "content": (
                                            "Understood. I have reviewed my previous session memory. "
                                            "I will continue the task using only new approaches "
                                            "and will not repeat anything already attempted."
                                        ),
                                    }
                                )
                                os.environ["CAI_CONTEXT_USAGE"] = "0.0"
                                _console.print(
                                    "[bold green]✓ Memory summary applied — "
                                    "context window reset — restarting task[/bold green]\n"
                                )
                                _did_compact = True
                        except Exception as _ce:
                            _console.print(f"[red]Auto-compact error: {_ce}[/red]")

                        # Only abort the current runner invocation if compaction
                        # actually succeeded; otherwise continue normally to avoid
                        # immediate retry loops when summarization fails.
                        if _did_compact:
                            raise ContextCompactedError(
                                f"Context compacted after {_asst_count} LLM responses "
                                f"(threshold {_support_interval})"
                            )
                        else:
                            _console.print(
                                "[yellow]Auto-compact did not produce a summary — continuing without compaction.[/yellow]"
                            )
            except ContextCompactedError:
                raise  # propagate to the outer runner / CLI loop
            except (ValueError, Exception):
                pass  # malformed interval — ignore silently

        max_tokens = self._get_model_max_tokens(str(self.model))
        threshold_percent = float(os.getenv("CAI_AUTO_COMPACT_THRESHOLD", "0.8"))
        threshold = max_tokens * threshold_percent

        if estimated_tokens <= threshold:
            # Context dropped below threshold — clear any previous failure record so we
            # can attempt compaction again the next time it's needed.
            if hasattr(self, "_compact_failed_tokens"):
                del self._compact_failed_tokens
            return input, system_instructions, False

        # ── Failure back-off ──────────────────────────────────────────────────
        # If the last compaction attempt failed (e.g. support model context too
        # small), suppress further attempts until the token count changes
        # meaningfully (>5 % growth).  This prevents the support model from
        # being called on every single LLM turn when it cannot possibly succeed.
        if hasattr(self, "_compact_failed_tokens"):
            growth = (estimated_tokens - self._compact_failed_tokens) / max(estimated_tokens, 1)
            if growth < 0.05:
                # Still at roughly the same size — skip silently.
                return input, system_instructions, False
            # Token count grew enough; clear the flag and try again.
            del self._compact_failed_tokens
        # ─────────────────────────────────────────────────────────────────────

        # Auto-compaction needed
        from rich.console import Console

        console = Console()

        # Update context usage in environment for toolbar
        context_usage = estimated_tokens / max_tokens
        os.environ["CAI_CONTEXT_USAGE"] = str(context_usage)

        console.print(
            f"\n[yellow]⚠️  Context usage at {(estimated_tokens / max_tokens) * 100:.1f}% ({estimated_tokens:,}/{max_tokens:,} tokens)[/yellow]"
        )
        console.print("[yellow]Triggering automatic context compaction...[/yellow]\n")

        # Import compact command components
        try:
            from cai.repl.commands.memory import MEMORY_COMMAND_INSTANCE

            # Generate AI summary of the conversation
            summary = await MEMORY_COMMAND_INSTANCE._ai_summarize_history(self.agent_name)

            if summary:
                # Clear the failure record on success
                if hasattr(self, "_compact_failed_tokens"):
                    del self._compact_failed_tokens

                # Store the summary
                from cai.repl.commands.memory import COMPACTED_SUMMARIES

                COMPACTED_SUMMARIES[self.agent_name] = summary

                # Clear the message history and keep only essential messages
                self.message_history.clear()
                # Reset context usage after clearing
                os.environ["CAI_CONTEXT_USAGE"] = "0.0"

                # Create new input with summary
                new_system_instructions = system_instructions or ""
                if new_system_instructions:
                    new_system_instructions += "\n\n"
                new_system_instructions += f"Previous conversation summary:\n{summary}"

                # Keep only the current input (user's latest message)
                if isinstance(input, str):
                    new_input = input
                else:
                    # For list input, keep only user messages
                    new_input = []
                    for item in input:
                        if hasattr(item, "role") and item.role == "user":
                            new_input.append(item)
                        elif isinstance(item, dict) and item.get("role") == "user":
                            new_input.append(item)

                    # If no user messages found, keep the original input
                    if not new_input:
                        new_input = input

                # Re-estimate tokens with compacted context
                test_messages = self._converter.items_to_messages(new_input, model_instance=self)
                if new_system_instructions:
                    test_messages.insert(0, {"role": "system", "content": new_system_instructions})
                new_tokens, _ = count_tokens_with_tiktoken(test_messages)

                console.print(
                    f"[green]✓ Context compacted: {estimated_tokens:,} → {new_tokens:,} tokens ({(1 - new_tokens / estimated_tokens) * 100:.1f}% reduction)[/green]\n"
                )

                # Update context usage after compaction
                new_context_usage = new_tokens / max_tokens if max_tokens > 0 else 0.0
                os.environ["CAI_CONTEXT_USAGE"] = str(new_context_usage)

                return new_input, new_system_instructions, True

            # Summary returned None — treat the same as an exception (record failure).
            console.print(
                "[yellow]Auto-compact did not produce a summary — continuing without compaction.[/yellow]"
            )
            console.print(
                "[yellow]Future compaction attempts will be suppressed until the conversation grows further.[/yellow]\n"
            )
            self._compact_failed_tokens = estimated_tokens

        except Exception as e:
            console.print(f"[red]Auto-compaction failed: {e}[/red]")
            console.print("[yellow]Continuing with full context...[/yellow]")
            console.print(
                "[yellow]Future compaction attempts will be suppressed until the conversation grows further.[/yellow]\n"
            )
            # Record the failure so we don't spam the support model on every turn.
            self._compact_failed_tokens = estimated_tokens

        return input, system_instructions, False

    def _intermediate_logs(self):
        """Intermediate logging placeholder (telemetry disabled)."""
        pass

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            # Resolve base URL using the same priority chain as the loop
            # package: LOCAL_API_BASE > OPENAI_API_BASE > OPENAI_BASE_URL.
            # This ensures every lazy client rebuild honours local proxy
            # config regardless of which code path triggers it.
            api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))
            base_url = (
                os.getenv("LOCAL_API_BASE")
                or os.getenv("OPENAI_API_BASE")
                or os.getenv("OPENAI_BASE_URL")
            )
            client_kwargs: dict = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    # Helper function to detect and format function calls from various models
    def _detect_and_format_function_calls(self, delta):
        """
        Helper to detect function calls in different formats and normalize them.
        Handles Qwen specifics where function calls may be formatted differently.

        Returns: List of normalized tool calls or None
        """
        # Standard OpenAI-style tool_calls format
        if hasattr(delta, "tool_calls") and delta.tool_calls:
            return delta.tool_calls
        elif isinstance(delta, dict) and "tool_calls" in delta and delta["tool_calls"]:
            return delta["tool_calls"]

        # Qwen/Ollama function_call format
        if isinstance(delta, dict) and "function_call" in delta:
            function_call = delta["function_call"]
            return [
                {
                    "index": 0,
                    "id": f"call_{time.time_ns()}",  # Generate a unique ID
                    "type": "function",
                    "function": {
                        "name": function_call.get("name", ""),
                        "arguments": function_call.get("arguments", ""),
                    },
                }
            ]

        if isinstance(delta, dict) and "content" in delta:
            content = delta["content"]
            # Try to detect if the content is a JSON string with function call format
            try:
                if isinstance(content, str) and "{" in content and "}" in content:
                    # Try to extract JSON from the content (it might be embedded in text)
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        json_str = content[json_start:json_end]
                        parsed = json.loads(json_str)
                        if "name" in parsed and "arguments" in parsed:
                            # This looks like a function call in JSON format
                            return [
                                {
                                    "index": 0,
                                    "id": f"call_{time.time_ns()}",  # Generate a unique ID
                                    "type": "function",
                                    "function": {
                                        "name": parsed["name"],
                                        "arguments": json.dumps(parsed["arguments"])
                                        if isinstance(parsed["arguments"], dict)
                                        else parsed["arguments"],
                                    },
                                }
                            ]
            except Exception:
                # If JSON parsing fails, just continue with normal processing
                pass

        # Anthropic-style tool_use format
        if hasattr(delta, "tool_use") and delta.tool_use:
            tool_use = delta.tool_use
            return [
                {
                    "index": 0,
                    "id": tool_use.get("id", f"tool_{time.time_ns()}"),
                    "type": "function",
                    "function": {
                        "name": tool_use.get("name", ""),
                        "arguments": tool_use.get("input", "{}"),
                    },
                }
            ]
        elif isinstance(delta, dict) and "tool_use" in delta and delta["tool_use"]:
            tool_use = delta["tool_use"]
            return [
                {
                    "index": 0,
                    "id": tool_use.get("id", f"tool_{time.time_ns()}"),
                    "type": "function",
                    "function": {
                        "name": tool_use.get("name", ""),
                        "arguments": tool_use.get("input", "{}"),
                    },
                }
            ]

        return None


class _Converter:
    def __init__(self):
        """Initialize converter with instance-based state."""
        self.recent_tool_calls = {}
        self.tool_outputs = {}

    def convert_tool_choice(
        self, tool_choice: Literal["auto", "required", "none"] | str | None
    ) -> ChatCompletionToolChoiceOptionParam | NotGiven:
        # When called directly by the converter tests we return a plain
        # string for the unspecified case (tests expect a `str`), but
        # `None`/empty-string values will be treated as omitted later
        # when building API kwargs.
        if tool_choice is None:
            return ""
        elif tool_choice == "auto":
            return "auto"
        elif tool_choice == "required":
            return "required"
        elif tool_choice == "none":
            return "none"
        else:
            return {
                "type": "function",
                "function": {
                    "name": tool_choice,
                },
            }

    def convert_response_format(
        self, final_output_schema: AgentOutputSchema | None
    ) -> ResponseFormat | NotGiven:
        # For the chat-completions converter, return `None` for plain-text
        # output to match the unit tests; the caller will translate this
        # into the OpenAI `NOT_GIVEN` sentinel when constructing kwargs.
        if not final_output_schema or final_output_schema.is_plain_text():
            return None

        return {
            "type": "json_schema",
            "json_schema": {
                "name": "final_output",
                "strict": final_output_schema.strict_json_schema,
                "schema": final_output_schema.json_schema(),
            },
        }

    def message_to_output_items(self, message: ChatCompletionMessage) -> list[TResponseOutputItem]:
        items: list[TResponseOutputItem] = []

        message_item = ResponseOutputMessage(
            id=FAKE_RESPONSES_ID,
            content=[],
            role="assistant",
            type="message",
            status="completed",
        )
        if message.content:
            message_item.content.append(
                ResponseOutputText(text=message.content, type="output_text", annotations=[])
            )
        if hasattr(message, "refusal") and message.refusal:
            message_item.content.append(
                ResponseOutputRefusal(refusal=message.refusal, type="refusal")
            )
        if hasattr(message, "audio") and message.audio:
            raise AgentsException("🎵 Audio output not supported - Text responses only")

        if message_item.content:
            items.append(message_item)

        if hasattr(message, "tool_calls") and message.tool_calls:
            for tool_call in message.tool_calls:
                items.append(
                    ResponseFunctionToolCall(
                        id=FAKE_RESPONSES_ID,
                        call_id=tool_call.id[:40],
                        arguments=tool_call.function.arguments,
                        name=tool_call.function.name,
                        type="function_call",
                    )
                )

        return items

    def maybe_easy_input_message(self, item: Any) -> EasyInputMessageParam | None:
        if not isinstance(item, dict):
            return None

        keys = item.keys()
        # EasyInputMessageParam only has these two keys
        if keys != {"content", "role"}:
            return None

        role = item.get("role", None)
        if role not in ("user", "assistant", "system", "developer"):
            return None

        if "content" not in item:
            return None

        return cast(EasyInputMessageParam, item)

    def maybe_input_message(self, item: Any) -> Message | None:
        if (
            isinstance(item, dict)
            and item.get("type") == "message"
            and item.get("role")
            in (
                "user",
                "system",
                "developer",
            )
        ):
            return cast(Message, item)

        return None

    def maybe_file_search_call(self, item: Any) -> ResponseFileSearchToolCallParam | None:
        if isinstance(item, dict) and item.get("type") == "file_search_call":
            return cast(ResponseFileSearchToolCallParam, item)
        return None

    def maybe_function_tool_call(self, item: Any) -> ResponseFunctionToolCallParam | None:
        if isinstance(item, dict) and item.get("type") == "function_call":
            return cast(ResponseFunctionToolCallParam, item)
        return None

    def maybe_function_tool_call_output(
        self,
        item: Any,
    ) -> FunctionCallOutput | None:
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            return cast(FunctionCallOutput, item)
        return None

    def maybe_item_reference(self, item: Any) -> ItemReference | None:
        if isinstance(item, dict) and item.get("type") == "item_reference":
            return cast(ItemReference, item)
        return None

    def maybe_response_output_message(self, item: Any) -> ResponseOutputMessageParam | None:
        # ResponseOutputMessage is only used for messages with role assistant
        if (
            isinstance(item, dict)
            and item.get("type") == "message"
            and item.get("role") == "assistant"
        ):
            return cast(ResponseOutputMessageParam, item)
        return None

    def extract_text_content(
        self, content: str | Iterable[ResponseInputContentParam]
    ) -> str | list[ChatCompletionContentPartTextParam]:
        all_content = self.extract_all_content(content)
        if isinstance(all_content, str):
            return all_content
        out: list[ChatCompletionContentPartTextParam] = []
        for c in all_content:
            if c.get("type") == "text":
                out.append(cast(ChatCompletionContentPartTextParam, c))
        return out

    def extract_all_content(
        self, content: str | Iterable[ResponseInputContentParam]
    ) -> str | list[ChatCompletionContentPartParam]:
        if isinstance(content, str):
            return content
        out: list[ChatCompletionContentPartParam] = []

        for c in content:
            if isinstance(c, dict) and c.get("type") == "input_text":
                casted_text_param = cast(ResponseInputTextParam, c)
                out.append(
                    ChatCompletionContentPartTextParam(
                        type="text",
                        text=casted_text_param["text"],
                    )
                )
            elif isinstance(c, dict) and c.get("type") == "input_image":
                casted_image_param = cast(ResponseInputImageParam, c)
                if "image_url" not in casted_image_param or not casted_image_param["image_url"]:
                    raise UserError("🖼️ Image URLs required - Upload images to a URL first")
                out.append(
                    ChatCompletionContentPartImageParam(
                        type="image_url",
                        image_url={
                            "url": casted_image_param["image_url"],
                            "detail": casted_image_param["detail"],
                        },
                    )
                )
            elif isinstance(c, dict) and c.get("type") == "input_file":
                raise UserError("📄 File uploads not supported - Use image URLs or text content")
            else:
                raise UserError(
                    "❓ Unrecognized content type - Expected 'input_text' or 'input_image'"
                )
        return out

    def items_to_messages(
        self,
        items: str | Iterable[TResponseInputItem],
        model_instance=None,
    ) -> list[ChatCompletionMessageParam]:
        """
        Convert a sequence of 'Item' objects into a list of ChatCompletionMessageParam.

        Rules:
        - EasyInputMessage or InputMessage (role=user) => ChatCompletionUserMessageParam
        - EasyInputMessage or InputMessage (role=system) => ChatCompletionSystemMessageParam
        - EasyInputMessage or InputMessage (role=developer) => ChatCompletionDeveloperMessageParam
        - InputMessage (role=assistant) => Start or flush a ChatCompletionAssistantMessageParam
        - response_output_message => Also produces/flushes a ChatCompletionAssistantMessageParam
        - tool calls get attached to the *current* assistant message, or create one if none.
        - tool outputs => ChatCompletionToolMessageParam
        """

        if isinstance(items, str):
            return [
                ChatCompletionUserMessageParam(
                    role="user",
                    content=items,
                )
            ]

        result: list[ChatCompletionMessageParam] = []
        current_assistant_msg: ChatCompletionAssistantMessageParam | None = None

        def flush_assistant_message() -> None:
            nonlocal current_assistant_msg
            if current_assistant_msg is not None:
                # The API doesn't support empty arrays for tool_calls
                if not current_assistant_msg.get("tool_calls"):
                    # Ensure content is not None if tool_calls are absent and content is also None
                    # Some models like Anthropic require some content, even if it's just a placeholder.
                    if current_assistant_msg.get("content") is None:
                        current_assistant_msg["content"] = (
                            "(No text content in this assistant message)"  # Or just an empty string if preferred
                        )
                    current_assistant_msg.pop(
                        "tool_calls", None
                    )  # Use pop with default to avoid KeyError
                result.append(current_assistant_msg)
                current_assistant_msg = None

        def ensure_assistant_message() -> ChatCompletionAssistantMessageParam:
            nonlocal current_assistant_msg
            if current_assistant_msg is None:
                current_assistant_msg = ChatCompletionAssistantMessageParam(role="assistant")
                current_assistant_msg["tool_calls"] = []
            return current_assistant_msg

        for item in items:
            # NEW: Handle 'tool' messages from history
            if (
                isinstance(item, dict)
                and item.get("role") == "tool"
                and "tool_call_id" in item
                and "content" in item
            ):
                flush_assistant_message()  # Ensure any pending assistant message is flushed
                tool_message: ChatCompletionToolMessageParam = {
                    "role": "tool",
                    "tool_call_id": item["tool_call_id"],
                    "content": str(item["content"] or ""),  # Ensure content is a string
                }
                result.append(tool_message)
                continue

            # 0) Assistant messages with tool_calls only (from memory)
            if (
                isinstance(item, dict)
                and item.get("role") == "assistant"
                and item.get("tool_calls")
            ):
                flush_assistant_message()
                tool_calls_param: list[ChatCompletionMessageToolCallParam] = []
                for tc in item["tool_calls"]:
                    function_details = tc.get("function", {})
                    arguments = function_details.get("arguments")
                    # Ensure arguments is a valid JSON string, defaulting to "{}" if empty or None
                    if arguments is None or (
                        isinstance(arguments, str) and arguments.strip() == ""
                    ):
                        arguments = "{}"
                    elif isinstance(arguments, dict):
                        # Ensure it's a string if it's a dict (should already be string per schema)
                        arguments = json.dumps(arguments)

                    tool_calls_param.append(
                        ChatCompletionMessageToolCallParam(
                            id=tc.get("id", "")[:40],
                            type=tc.get("type", "function"),
                            function={
                                "name": function_details.get("name", "unknown_function"),
                                "arguments": arguments,  # Use sanitized arguments
                            },
                        )
                    )
                msg_asst: ChatCompletionAssistantMessageParam = {
                    "role": "assistant",
                    "content": item.get("content"),  # Content can be None here
                    "tool_calls": tool_calls_param,
                }
                result.append(msg_asst)
                # Skip further processing for this item
                continue

            # 1) Check easy input message
            if easy_msg := self.maybe_easy_input_message(item):
                role = easy_msg["role"]
                content = easy_msg["content"]

                if role == "user":
                    flush_assistant_message()
                    msg_user: ChatCompletionUserMessageParam = {
                        "role": "user",
                        "content": self.extract_all_content(content),
                    }
                    result.append(msg_user)
                elif role == "system":
                    flush_assistant_message()
                    msg_system: ChatCompletionSystemMessageParam = {
                        "role": "system",
                        "content": self.extract_text_content(content),
                    }
                    result.append(msg_system)
                elif role == "developer":
                    flush_assistant_message()
                    msg_developer: ChatCompletionDeveloperMessageParam = {
                        "role": "developer",
                        "content": self.extract_text_content(content),
                    }
                    result.append(msg_developer)
                elif role == "assistant":
                    flush_assistant_message()
                    msg_assistant: ChatCompletionAssistantMessageParam = {
                        "role": "assistant",
                        "content": self.extract_text_content(content),
                    }
                    result.append(msg_assistant)
                else:
                    raise UserError(
                        f"👥 Invalid role '{role}' - Use: user, assistant, system, or developer"
                    )

            # 2) Check input message
            elif in_msg := self.maybe_input_message(item):
                role = in_msg["role"]
                content = in_msg["content"]
                flush_assistant_message()

                if role == "user":
                    msg_user = {
                        "role": "user",
                        "content": self.extract_all_content(content),
                    }
                    result.append(msg_user)
                elif role == "system":
                    msg_system = {
                        "role": "system",
                        "content": self.extract_text_content(content),
                    }
                    result.append(msg_system)
                elif role == "developer":
                    msg_developer = {
                        "role": "developer",
                        "content": self.extract_text_content(content),
                    }
                    result.append(msg_developer)
                else:
                    raise UserError(
                        f"👥 Invalid message role '{role}' - Must be: user, system, or developer"
                    )

            # 3) response output message => assistant
            elif resp_msg := self.maybe_response_output_message(item):
                flush_assistant_message()
                new_asst = ChatCompletionAssistantMessageParam(role="assistant")
                contents = resp_msg["content"]

                text_segments = []
                for c in contents:
                    if c["type"] == "output_text":
                        text_segments.append(c["text"])
                    elif c["type"] == "refusal":
                        new_asst["refusal"] = c["refusal"]
                    elif c["type"] == "output_audio":
                        # Can't handle this, b/c chat completions expects an ID which we dont have
                        raise UserError(
                            "🎵 Audio content must use audio IDs - Direct audio data not supported"
                        )
                    else:
                        raise UserError(
                            "❓ Unknown assistant message content - Check message format"
                        )

                if text_segments:
                    combined = "\n".join(text_segments)
                    new_asst["content"] = combined

                new_asst["tool_calls"] = []
                current_assistant_msg = new_asst

            # 4) function/file-search calls => attach to assistant
            elif file_search := self.maybe_file_search_call(item):
                asst = ensure_assistant_message()
                tool_calls = list(asst.get("tool_calls", []))
                new_tool_call = ChatCompletionMessageToolCallParam(
                    id=file_search["id"][:40],
                    type="function",
                    function={
                        "name": "file_search_call",
                        "arguments": json.dumps(
                            {
                                "queries": file_search.get("queries", []),
                                "status": file_search.get("status"),
                            }
                        ),
                    },
                )
                tool_calls.append(new_tool_call)
                asst["tool_calls"] = tool_calls

            elif func_call := self.maybe_function_tool_call(item):
                asst = ensure_assistant_message()
                tool_calls = list(asst.get("tool_calls", []))

                # Save the tool call details for later matching with output
                if not hasattr(self, "recent_tool_calls"):
                    self.recent_tool_calls = {}

                # Store the tool call by ID for later reference
                # Also store the current time for execution timing
                import time

                self.recent_tool_calls[func_call["call_id"]] = {
                    "name": func_call["name"],
                    "arguments": func_call["arguments"],
                    "start_time": time.time(),
                    "execution_info": {"start_time": time.time()},
                }

                arguments = func_call.get("arguments")  # func_call is a dict here
                # Ensure arguments is a valid JSON string, defaulting to "{}" if empty or None
                if arguments is None or (isinstance(arguments, str) and arguments.strip() == ""):
                    arguments = "{}"
                elif isinstance(arguments, dict):
                    arguments = json.dumps(arguments)

                new_tool_call = ChatCompletionMessageToolCallParam(
                    id=func_call["call_id"][:40],
                    type="function",
                    function={
                        "name": func_call["name"],
                        "arguments": arguments,  # Use sanitized arguments
                    },
                )
                tool_calls.append(new_tool_call)
                asst["tool_calls"] = tool_calls

            # 5) function call output => tool message
            elif func_output := self.maybe_function_tool_call_output(item):
                # Store the output for this call_id
                call_id = func_output["call_id"]
                output_content = func_output["output"]

                # IMPORTANT: Truncate call_id to 40 characters for consistency
                truncated_call_id = call_id[:40] if call_id else call_id

                # Update execution timing if we have the start time
                if hasattr(self, "recent_tool_calls") and call_id in self.recent_tool_calls:
                    tool_call_details = self.recent_tool_calls[call_id]  # Renamed for clarity
                    if "start_time" in tool_call_details:
                        end_time = time.time()
                        tool_execution_time = end_time - tool_call_details["start_time"]

                        # Update the execution info
                        if "execution_info" in tool_call_details:
                            tool_call_details["execution_info"]["end_time"] = end_time
                            tool_call_details["execution_info"]["tool_time"] = tool_execution_time

                            # If this is the first tool being executed, record the total time from conversation start
                            if not hasattr(self, "conversation_start_time"):
                                self.conversation_start_time = tool_call_details["start_time"]

                            total_time = end_time - getattr(
                                self, "conversation_start_time", tool_call_details["start_time"]
                            )
                            tool_call_details["execution_info"]["total_time"] = total_time

                # Store the output so it can be accessed later
                if not hasattr(self, "tool_outputs"):
                    self.tool_outputs = {}

                self.tool_outputs[call_id] = output_content

                # Display the tool output immediately with the matched tool call
                from cai.util import cli_print_tool_output

                # Look up the original tool call to get the name and arguments
                tool_name = "Unknown Tool"
                tool_args = {}
                execution_info = {}

                if hasattr(self, "recent_tool_calls") and call_id in self.recent_tool_calls:
                    tool_call_details = self.recent_tool_calls[call_id]  # Renamed for clarity
                    tool_name = tool_call_details.get("name", "Unknown Tool")
                    tool_args = tool_call_details.get("arguments", {})
                    execution_info = tool_call_details.get("execution_info", {})

                # Get token counts from the OpenAIChatCompletionsModel if available
                model_instance = None
                for frame in inspect.stack():
                    if "self" in frame.frame.f_locals:
                        self_obj = frame.frame.f_locals["self"]
                        if isinstance(self_obj, OpenAIChatCompletionsModel):
                            model_instance = self_obj
                            break

                # Always create a token_info dictionary, even if some values are zero
                token_info = {
                    "interaction_input_tokens": getattr(
                        model_instance, "interaction_input_tokens", 0
                    ),
                    "interaction_output_tokens": getattr(
                        model_instance, "interaction_output_tokens", 0
                    ),
                    "interaction_reasoning_tokens": getattr(
                        model_instance, "interaction_reasoning_tokens", 0
                    ),
                    "total_input_tokens": getattr(model_instance, "total_input_tokens", 0),
                    "total_output_tokens": getattr(model_instance, "total_output_tokens", 0),
                    "total_reasoning_tokens": getattr(model_instance, "total_reasoning_tokens", 0),
                    "model": str(getattr(model_instance, "model", "")),
                    "agent_name": getattr(model_instance, "agent_name", "Agent"),
                }

                # Use already-calculated costs from COST_TRACKER instead of recalculating
                if model_instance and hasattr(model_instance, "model"):
                    from cai.util import COST_TRACKER

                    # Use the last recorded costs instead of recalculating
                    token_info["interaction_cost"] = getattr(
                        COST_TRACKER, "last_interaction_cost", 0.0
                    )
                    token_info["total_cost"] = getattr(COST_TRACKER, "last_total_cost", 0.0)

                # Check if we're in streaming mode
                is_streaming_enabled = os.environ.get("CAI_STREAM", "false").lower() == "true"

                # Check if this output was already displayed during streaming
                # For async sessions, we always display since they don't have real streaming
                should_display = True

                # If streaming is enabled, check if this was already shown
                if (
                    is_streaming_enabled
                    and hasattr(self, "recent_tool_calls")
                    and call_id in self.recent_tool_calls
                ):
                    tool_call_info = self.recent_tool_calls[call_id]
                    # Check if this tool was executed very recently (within last 5 seconds)
                    # This indicates it was likely shown during streaming
                    if "start_time" in tool_call_info:
                        time_since_execution = time.time() - tool_call_info["start_time"]
                        # For generic_linux_command executed recently in streaming mode, skip display
                        # But always display for async session commands (they have session_id in args)
                        # and always display for non-generic_linux_command tools
                        if time_since_execution < 5.0 and "_command" in tool_name.lower():
                            # Parse arguments to check if this is an async session command
                            try:
                                args_dict = (
                                    json.loads(tool_args)
                                    if isinstance(tool_args, str)
                                    else tool_args
                                )
                                # If it has session_id, it's an async command - always show
                                if not (
                                    isinstance(args_dict, dict) and args_dict.get("session_id")
                                ):
                                    should_display = False
                            except Exception as e:
                                logger.exception(
                                    "Failed parsing tool args to decide display: %s", e
                                )
                                should_display = False

                # Only display if it hasn't been shown during streaming
                if should_display:
                    cli_print_tool_output(
                        tool_name=tool_name,
                        args=tool_args,
                        output=output_content,
                        call_id=call_id,
                        execution_info=execution_info,
                        token_info=token_info,
                    )

                # Continue with normal processing
                flush_assistant_message()

                # ATOMIC ADDITION: Add pending tool call and response together
                # This ensures we never have tool calls without responses in history
                if model_instance and hasattr(model_instance, "_pending_tool_calls"):
                    # Check if we have a pending tool call for this ID
                    if call_id in model_instance._pending_tool_calls:
                        # Add the assistant message with tool call first
                        pending_msg = model_instance._pending_tool_calls[call_id]
                        model_instance.add_to_message_history(pending_msg)

                        # Now add the tool response
                        tool_response_msg = {
                            "role": "tool",
                            "tool_call_id": truncated_call_id,
                            "content": func_output["output"],
                        }
                        model_instance.add_to_message_history(tool_response_msg)

                        # Remove from pending
                        del model_instance._pending_tool_calls[call_id]

                        # Log both messages
                        if hasattr(model_instance, "logger"):
                            # Log the tool call with its response
                            # Note: Tool responses are logged as part of the training data recording,
                            # not as separate events
                            pass

                # Now add the tool message with truncated call_id
                msg: ChatCompletionToolMessageParam = {
                    "role": "tool",
                    "tool_call_id": truncated_call_id,
                    "content": func_output["output"],
                }
                result.append(msg)

            # 6) item reference => handle or raise
            elif _item_ref := self.maybe_item_reference(item):
                raise UserError("🔗 Item references not supported - Include content directly")

            # 7) If we haven't recognized it => fail or ignore
            else:
                raise UserError(
                    "❌ Invalid message format - Check documentation for supported types"
                )

        flush_assistant_message()
        return result


class ToolConverter:
    @classmethod
    def to_openai(cls, tool: Tool) -> ChatCompletionToolParam:
        if isinstance(tool, FunctionTool):
            return {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.params_json_schema,
                },
            }

        raise UserError(
            f"Hosted tools are not supported with the ChatCompletions API. FGot tool type: "
            f"{type(tool)}, tool: {tool}"
        )

    @classmethod
    def convert_handoff_tool(cls, handoff: Handoff[Any]) -> ChatCompletionToolParam:
        return {
            "type": "function",
            "function": {
                "name": handoff.tool_name,
                "description": handoff.tool_description,
                "parameters": handoff.input_json_schema,
            },
        }
