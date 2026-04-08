import logging
import sys
from typing import Literal

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

try:
    from . import _config
except Exception:  # pragma: no cover - optional OpenAI-backed config
    _config = None

from .agent import Agent, ToolsToFinalOutputFunction, ToolsToFinalOutputResult
from .agent_output import AgentOutputSchema
from .computer import AsyncComputer, Button, Computer, Environment
from .exceptions import (
    AgentsException,
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelBehaviorError,
    OutputGuardrailTripwireTriggered,
    UserError,
)
from .guardrail import (
    GuardrailFunctionOutput,
    InputGuardrail,
    InputGuardrailResult,
    OutputGuardrail,
    OutputGuardrailResult,
    input_guardrail,
    output_guardrail,
)
from .handoffs import Handoff, HandoffInputData, HandoffInputFilter, handoff

try:
    from .items import (
        HandoffCallItem,
        HandoffOutputItem,
        ItemHelpers,
        MessageOutputItem,
        ModelResponse,
        ReasoningItem,
        RunItem,
        ToolCallItem,
        ToolCallOutputItem,
        TResponseInputItem,
    )
except Exception:  # pragma: no cover - items may depend on optional OpenAI types
    HandoffCallItem = None
    HandoffOutputItem = None
    ItemHelpers = None
    MessageOutputItem = None
    ModelResponse = None
    ReasoningItem = None
    RunItem = None
    ToolCallItem = None
    ToolCallOutputItem = None
    TResponseInputItem = None
from .lifecycle import AgentHooks, RunHooks
from .model_settings import ModelSettings
from .models.interface import Model, ModelProvider, ModelTracing

try:
    from .models.openai_chatcompletions import OpenAIChatCompletionsModel
except Exception:  # pragma: no cover - OpenAI models optional
    OpenAIChatCompletionsModel = None

try:
    from .models.openai_provider import OpenAIProvider
except Exception:  # pragma: no cover - OpenAI provider optional
    OpenAIProvider = None

try:
    from .models.openai_responses import OpenAIResponsesModel
except Exception:  # pragma: no cover - OpenAI responses optional
    OpenAIResponsesModel = None
from .result import RunResult, RunResultStreaming
from .run import RunConfig, Runner
from .run_context import RunContextWrapper, TContext
from .stream_events import (
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
    StreamEvent,
)

try:
    from .tool import (
        ComputerTool,
        FileSearchTool,
        FunctionTool,
        FunctionToolResult,
        Tool,
        WebSearchTool,
        default_tool_error_function,
        function_tool,
    )
except Exception:  # pragma: no cover - tool module may require OpenAI types
    ComputerTool = None
    FileSearchTool = None
    FunctionTool = None
    FunctionToolResult = None
    Tool = None
    WebSearchTool = None
    default_tool_error_function = None
    function_tool = None
from .tracing import (
    AgentSpanData,
    CustomSpanData,
    FunctionSpanData,
    GenerationSpanData,
    GuardrailSpanData,
    HandoffSpanData,
    MCPListToolsSpanData,
    Span,
    SpanData,
    SpanError,
    SpeechGroupSpanData,
    SpeechSpanData,
    Trace,
    TracingProcessor,
    TranscriptionSpanData,
    add_trace_processor,
    agent_span,
    custom_span,
    function_span,
    gen_span_id,
    gen_trace_id,
    generation_span,
    get_current_span,
    get_current_trace,
    guardrail_span,
    handoff_span,
    mcp_tools_span,
    set_trace_processors,
    set_tracing_disabled,
    set_tracing_export_api_key,
    speech_group_span,
    speech_span,
    trace,
    transcription_span,
)
from .usage import Usage


def set_default_openai_key(key: str, use_for_tracing: bool = True) -> None:
    """Set the default OpenAI API key to use for LLM requests (and optionally tracing(). This is
    only necessary if the OPENAI_API_KEY environment variable is not already set.

    If provided, this key will be used instead of the OPENAI_API_KEY environment variable.

    Args:
        key: The OpenAI key to use.
        use_for_tracing: Whether to also use this key to send traces to OpenAI. Defaults to True
            If False, you'll either need to set the OPENAI_API_KEY environment variable or call
            set_tracing_export_api_key() with the API key you want to use for tracing.
    """
    _config.set_default_openai_key(key, use_for_tracing)


def set_default_openai_client(client: AsyncOpenAI, use_for_tracing: bool = True) -> None:
    """Set the default OpenAI client to use for LLM requests and/or tracing. If provided, this
    client will be used instead of the default OpenAI client.

    Args:
        client: The OpenAI client to use.
        use_for_tracing: Whether to use the API key from this client for uploading traces. If False,
            you'll either need to set the OPENAI_API_KEY environment variable or call
            set_tracing_export_api_key() with the API key you want to use for tracing.
    """
    _config.set_default_openai_client(client, use_for_tracing)


def set_default_openai_api(api: Literal["chat_completions", "responses"]) -> None:
    """Set the default API to use for OpenAI LLM requests. By default, we will use the responses API
    but you can set this to use the chat completions API instead.
    """
    _config.set_default_openai_api(api)


def enable_verbose_stdout_logging():
    """Enables verbose logging to stdout. This is useful for debugging."""
    logger = logging.getLogger("openai.agents")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.StreamHandler(sys.stdout))


__all__ = [
    "Agent",
    "ToolsToFinalOutputFunction",
    "ToolsToFinalOutputResult",
    "Runner",
    "Model",
    "ModelProvider",
    "ModelTracing",
    "ModelSettings",
    "OpenAIChatCompletionsModel",
    "OpenAIProvider",
    "OpenAIResponsesModel",
    "AgentOutputSchema",
    "Computer",
    "AsyncComputer",
    "Environment",
    "Button",
    "AgentsException",
    "InputGuardrailTripwireTriggered",
    "OutputGuardrailTripwireTriggered",
    "MaxTurnsExceeded",
    "ModelBehaviorError",
    "UserError",
    "InputGuardrail",
    "InputGuardrailResult",
    "OutputGuardrail",
    "OutputGuardrailResult",
    "GuardrailFunctionOutput",
    "input_guardrail",
    "output_guardrail",
    "handoff",
    "Handoff",
    "HandoffInputData",
    "HandoffInputFilter",
    "TResponseInputItem",
    "MessageOutputItem",
    "ModelResponse",
    "RunItem",
    "HandoffCallItem",
    "HandoffOutputItem",
    "ToolCallItem",
    "ToolCallOutputItem",
    "ReasoningItem",
    "ModelResponse",
    "ItemHelpers",
    "RunHooks",
    "AgentHooks",
    "RunContextWrapper",
    "TContext",
    "RunResult",
    "RunResultStreaming",
    "RunConfig",
    "RawResponsesStreamEvent",
    "RunItemStreamEvent",
    "AgentUpdatedStreamEvent",
    "StreamEvent",
    "FunctionTool",
    "FunctionToolResult",
    "ComputerTool",
    "FileSearchTool",
    "Tool",
    "WebSearchTool",
    "function_tool",
    "Usage",
    "add_trace_processor",
    "agent_span",
    "custom_span",
    "function_span",
    "generation_span",
    "get_current_span",
    "get_current_trace",
    "guardrail_span",
    "handoff_span",
    "set_trace_processors",
    "set_tracing_disabled",
    "speech_group_span",
    "transcription_span",
    "speech_span",
    "mcp_tools_span",
    "trace",
    "Trace",
    "TracingProcessor",
    "SpanError",
    "Span",
    "SpanData",
    "AgentSpanData",
    "CustomSpanData",
    "FunctionSpanData",
    "GenerationSpanData",
    "GuardrailSpanData",
    "HandoffSpanData",
    "SpeechGroupSpanData",
    "SpeechSpanData",
    "MCPListToolsSpanData",
    "TranscriptionSpanData",
    "set_default_openai_key",
    "set_default_openai_client",
    "set_default_openai_api",
    "set_tracing_export_api_key",
    "enable_verbose_stdout_logging",
    "gen_trace_id",
    "gen_span_id",
    "default_tool_error_function",
]
