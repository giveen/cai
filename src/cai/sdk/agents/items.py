from __future__ import annotations

import abc
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, Union

if TYPE_CHECKING:
    from openai.types.responses import (
        Response,
        ResponseComputerToolCall,
        ResponseFileSearchToolCall,
        ResponseFunctionToolCall,
        ResponseFunctionWebSearch,
        ResponseInputItemParam,
        ResponseOutputItem,
        ResponseOutputMessage,
        ResponseOutputRefusal,
        ResponseOutputText,
        ResponseStreamEvent,
    )
    from openai.types.responses.response_input_item_param import (
        ComputerCallOutput,
        FunctionCallOutput,
    )
    from openai.types.responses.response_reasoning_item import ResponseReasoningItem
else:
    # Fallback types when `openai` is not installed. These are only used at runtime
    # for type-agnostic operations (e.g., agent discovery) and should not be relied on
    # for full model behavior. Use TYPE_CHECKING imports for accurate typing.
    Response = dict
    ResponseComputerToolCall = dict
    ResponseFileSearchToolCall = dict
    ResponseFunctionToolCall = dict
    ResponseFunctionWebSearch = dict
    ResponseInputItemParam = dict
    ResponseOutputItem = dict
    ResponseOutputMessage = dict
    ResponseOutputRefusal = dict
    ResponseOutputText = dict
    ResponseStreamEvent = dict
    ComputerCallOutput = dict
    FunctionCallOutput = dict
    ResponseReasoningItem = dict
try:
    from pydantic import BaseModel
except Exception:
    # Minimal fallback for BaseModel when pydantic is not installed.
    class BaseModel:  # pragma: no cover - fallback for environments without pydantic
        def __init__(self, *args, **kwargs):
            pass

        def model_dump(self, *args, **kwargs):
            return {}

        @classmethod
        def model_json_schema(cls):
            return {}


from typing import TypeAlias

from .exceptions import AgentsException, ModelBehaviorError
from .usage import Usage

if TYPE_CHECKING:
    from .agent import Agent

TResponse = Response
"""A type alias for the Response type."""

TResponseInputItem = ResponseInputItemParam
"""A type alias for the ResponseInputItemParam type."""

TResponseOutputItem = ResponseOutputItem
"""A type alias for the ResponseOutputItem type."""

TResponseStreamEvent = ResponseStreamEvent
"""A type alias for the ResponseStreamEvent type."""

T = TypeVar("T", bound=Union[TResponseOutputItem, TResponseInputItem])


@dataclass
class RunItemBase(Generic[T], abc.ABC):
    agent: Agent[Any]
    """The agent whose run caused this item to be generated."""

    raw_item: T
    """The raw Responses item from the run. This will always be a either an output item (i.e.
    `openai.types.responses.ResponseOutputItem` or an input item
    (i.e. `openai.types.responses.ResponseInputItemParam`).
    """

    def to_input_item(self) -> TResponseInputItem:
        """Converts this item into an input item suitable for passing to the model."""
        if isinstance(self.raw_item, dict):
            # We know that input items are dicts, so we can ignore the type error
            return self.raw_item  # type: ignore
        elif isinstance(self.raw_item, BaseModel):
            # All output items are Pydantic models that can be converted to input items.
            return self.raw_item.model_dump(exclude_unset=True)  # type: ignore
        else:
            raise AgentsException(f"Unexpected raw item type: {type(self.raw_item)}")


@dataclass
class MessageOutputItem(RunItemBase[ResponseOutputMessage]):
    """Represents a message from the LLM."""

    raw_item: ResponseOutputMessage
    """The raw response output message."""

    type: Literal["message_output_item"] = "message_output_item"


@dataclass
class HandoffCallItem(RunItemBase[ResponseFunctionToolCall]):
    """Represents a tool call for a handoff from one agent to another."""

    raw_item: ResponseFunctionToolCall
    """The raw response function tool call that represents the handoff."""

    type: Literal["handoff_call_item"] = "handoff_call_item"


@dataclass
class HandoffOutputItem(RunItemBase[TResponseInputItem]):
    """Represents the output of a handoff."""

    raw_item: TResponseInputItem
    """The raw input item that represents the handoff taking place."""

    source_agent: Agent[Any]
    """The agent that made the handoff."""

    target_agent: Agent[Any]
    """The agent that is being handed off to."""

    type: Literal["handoff_output_item"] = "handoff_output_item"


ToolCallItemTypes: TypeAlias = Union[
    ResponseFunctionToolCall,
    ResponseComputerToolCall,
    ResponseFileSearchToolCall,
    ResponseFunctionWebSearch,
]
"""A type that represents a tool call item."""


@dataclass
class ToolCallItem(RunItemBase[ToolCallItemTypes]):
    """Represents a tool call e.g. a function call or computer action call."""

    raw_item: ToolCallItemTypes
    """The raw tool call item."""

    type: Literal["tool_call_item"] = "tool_call_item"


@dataclass
class ToolCallOutputItem(RunItemBase[Union[FunctionCallOutput, ComputerCallOutput]]):
    """Represents the output of a tool call."""

    raw_item: FunctionCallOutput | ComputerCallOutput
    """The raw item from the model."""

    output: Any
    """The output of the tool call. This is whatever the tool call returned; the `raw_item`
    contains a string representation of the output.
    """

    type: Literal["tool_call_output_item"] = "tool_call_output_item"


@dataclass
class ReasoningItem(RunItemBase[ResponseReasoningItem]):
    """Represents a reasoning item."""

    raw_item: ResponseReasoningItem
    """The raw reasoning item."""

    type: Literal["reasoning_item"] = "reasoning_item"


RunItem: TypeAlias = Union[
    MessageOutputItem,
    HandoffCallItem,
    HandoffOutputItem,
    ToolCallItem,
    ToolCallOutputItem,
    ReasoningItem,
]
"""An item generated by an agent."""


@dataclass
class ModelResponse:
    output: list[TResponseOutputItem]
    """A list of outputs (messages, tool calls, etc) generated by the model"""

    usage: Usage
    """The usage information for the response."""

    referenceable_id: str | None
    """An ID for the response which can be used to refer to the response in subsequent calls to the
    model. Not supported by all model providers.
    """

    def to_input_items(self) -> list[TResponseInputItem]:
        """Convert the output into a list of input items suitable for passing to the model."""
        # We happen to know that the shape of the Pydantic output items are the same as the
        # equivalent TypedDict input items, so we can just convert each one.
        # This is also tested via unit tests.
        return [it.model_dump(exclude_unset=True) for it in self.output]  # type: ignore


class ItemHelpers:
    @classmethod
    def extract_last_content(cls, message: TResponseOutputItem) -> str:
        """Extracts the last text content or refusal from a message."""
        # Support both dict-style messages and pydantic/BaseModel-style messages.
        content = None
        try:
            if isinstance(message, dict):
                content = message.get("content", [])
            elif hasattr(message, "model_dump"):
                data = message.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
                content = data.get("content", [])
            else:
                content = getattr(message, "content", [])
        except Exception:
            content = getattr(message, "content", [])

        if not content:
            return ""

        last_content = content[-1]
        # dict-style content
        if isinstance(last_content, dict):
            if "text" in last_content:
                return last_content.get("text", "")
            if "refusal" in last_content:
                return last_content.get("refusal", "")

        # object-style content (e.g., pydantic models)
        if hasattr(last_content, "text"):
            return last_content.text
        if hasattr(last_content, "refusal"):
            return last_content.refusal

        raise ModelBehaviorError(f"Unexpected content type: {type(last_content)}")

    @classmethod
    def extract_last_text(cls, message: TResponseOutputItem) -> str | None:
        """Extracts the last text content from a message, if any. Ignores refusals."""
        try:
            if isinstance(message, dict):
                content = message.get("content", [])
            elif hasattr(message, "model_dump"):
                data = message.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
                content = data.get("content", [])
            else:
                content = getattr(message, "content", [])
        except Exception:
            content = getattr(message, "content", [])

        if not content:
            return None

        last_content = content[-1]
        if isinstance(last_content, dict):
            return last_content.get("text")

        if hasattr(last_content, "text"):
            return last_content.text

        return None

    @classmethod
    def input_to_new_input_list(
        cls, input: str | list[TResponseInputItem]
    ) -> list[TResponseInputItem]:
        """Converts a string or list of input items into a list of input items."""
        if isinstance(input, str):
            return [
                {
                    "content": input,
                    "role": "user",
                }
            ]
        return copy.deepcopy(input)

    @classmethod
    def text_message_outputs(cls, items: list[RunItem]) -> str:
        """Concatenates all the text content from a list of message output items."""
        text = ""
        for item in items:
            if isinstance(item, MessageOutputItem):
                text += cls.text_message_output(item)
        return text

    @classmethod
    def text_message_output(cls, message: MessageOutputItem) -> str:
        """Extracts all the text content from a single message output item."""
        text = ""
        # message.raw_item may be a BaseModel-like object or a dict
        try:
            if isinstance(message.raw_item, dict):
                parts = message.raw_item.get("content", [])
            elif hasattr(message.raw_item, "model_dump"):
                data = message.raw_item.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
                parts = data.get("content", [])
            else:
                parts = getattr(message.raw_item, "content", [])
        except Exception:
            parts = getattr(message.raw_item, "content", [])

        for item in parts:
            if isinstance(item, dict) and "text" in item:
                text += item.get("text", "")
            elif hasattr(item, "text"):
                text += item.text

        return text

    @classmethod
    def tool_call_output_item(
        cls, tool_call: ResponseFunctionToolCall, output: str
    ) -> FunctionCallOutput:
        """Creates a tool call output item from a tool call and its output."""
        return {
            "call_id": tool_call.call_id,
            "output": output,
            "type": "function_call_output",
        }
