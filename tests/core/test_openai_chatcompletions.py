from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from openai import NOT_GIVEN
from openai.types.chat.chat_completion import ChatCompletion, Choice
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage
from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputRefusal,
    ResponseOutputText,
)

from cai.sdk.agents import (
    ModelResponse,
    ModelSettings,
    ModelTracing,
    OpenAIChatCompletionsModel,
    OpenAIProvider,
    generation_span,
)
from cai.sdk.agents.models.fake_id import FAKE_RESPONSES_ID
import os

cai_model = os.getenv("CAI_MODEL", "qwen2.5:14b")


@pytest.mark.allow_call_model_methods
@pytest.mark.asyncio
async def test_get_response_with_text_message(monkeypatch) -> None:
    """
    When the model returns a ChatCompletionMessage with plain text content,
    `get_response` should produce a single `ResponseOutputMessage` containing
    a `ResponseOutputText` with that content, and a `Usage` populated from
    the completion's usage.
    """
    msg = ChatCompletionMessage(role="assistant", content="Hello")
    choice = Choice(index=0, finish_reason="stop", message=msg)
    chat = ChatCompletion(
        id="resp-id",
        created=0,
        model="fake",
        object="chat.completion",
        choices=[choice],
        usage=CompletionUsage(completion_tokens=5, prompt_tokens=7, total_tokens=12),
    )

    async def patched_fetch_response(self, *args, **kwargs):
        return chat

    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", patched_fetch_response)
    model = OpenAIProvider(use_responses=False).get_model(cai_model)
    resp: ModelResponse = await model.get_response(
        system_instructions=None,
        input="",
        model_settings=ModelSettings(),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
    )
    # Should have produced exactly one output message with one text part
    assert isinstance(resp, ModelResponse)
    assert len(resp.output) == 1
    assert isinstance(resp.output[0], ResponseOutputMessage)
    msg_item = resp.output[0]
    assert len(msg_item.content) == 1
    assert isinstance(msg_item.content[0], ResponseOutputText)
    assert msg_item.content[0].text == "Hello"
    # Usage should be preserved from underlying ChatCompletion.usage
    assert resp.usage.input_tokens == 7
    assert resp.usage.output_tokens == 5
    assert resp.usage.total_tokens == 12
    assert resp.referenceable_id is None


@pytest.mark.allow_call_model_methods
@pytest.mark.asyncio
async def test_get_response_with_refusal(monkeypatch) -> None:
    """
    When the model returns a ChatCompletionMessage with a `refusal` instead
    of normal `content`, `get_response` should produce a single
    `ResponseOutputMessage` containing a `ResponseOutputRefusal` part.
    """
    msg = ChatCompletionMessage(role="assistant", refusal="No thanks")
    choice = Choice(index=0, finish_reason="stop", message=msg)
    chat = ChatCompletion(
        id="resp-id",
        created=0,
        model="fake",
        object="chat.completion",
        choices=[choice],
        usage=None,
    )

    async def patched_fetch_response(self, *args, **kwargs):
        return chat

    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", patched_fetch_response)
    model = OpenAIProvider(use_responses=False).get_model(cai_model)
    resp: ModelResponse = await model.get_response(
        system_instructions=None,
        input="",
        model_settings=ModelSettings(),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
    )
    assert len(resp.output) == 1
    assert isinstance(resp.output[0], ResponseOutputMessage)
    refusal_part = resp.output[0].content[0]
    assert isinstance(refusal_part, ResponseOutputRefusal)
    assert refusal_part.refusal == "No thanks"
    # With no usage from the completion, requests is still counted and tokens are estimated.
    assert resp.usage.requests == 1
    assert resp.usage.input_tokens >= 0
    assert resp.usage.output_tokens == 0


@pytest.mark.allow_call_model_methods
@pytest.mark.asyncio
async def test_get_response_with_tool_call(monkeypatch) -> None:
    """
    If the ChatCompletionMessage includes one or more tool_calls, `get_response`
    should append corresponding `ResponseFunctionToolCall` items after the
    assistant message item with matching name/arguments.
    """
    tool_call = ChatCompletionMessageToolCall(
        id="call-id",
        type="function",
        function=Function(name="do_thing", arguments="{'x':1}"),
    )
    msg = ChatCompletionMessage(role="assistant", content="Hi", tool_calls=[tool_call])
    choice = Choice(index=0, finish_reason="stop", message=msg)
    chat = ChatCompletion(
        id="resp-id",
        created=0,
        model="fake",
        object="chat.completion",
        choices=[choice],
        usage=None,
    )

    async def patched_fetch_response(self, *args, **kwargs):
        return chat

    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", patched_fetch_response)
    model = OpenAIProvider(use_responses=False).get_model(cai_model)
    resp: ModelResponse = await model.get_response(
        system_instructions=None,
        input="",
        model_settings=ModelSettings(),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
    )
    # Expect a message item followed by a function tool call item.
    assert len(resp.output) == 2
    assert isinstance(resp.output[0], ResponseOutputMessage)
    fn_call_item = resp.output[1]
    assert isinstance(fn_call_item, ResponseFunctionToolCall)
    assert fn_call_item.call_id == "call-id"
    assert fn_call_item.name == "do_thing"
    assert fn_call_item.arguments == "{'x':1}"


def test_get_token_info_includes_agent_metadata() -> None:
    """The token info payload should expose agent metadata for the TUI sidebar."""

    from unittest.mock import MagicMock

    dummy_client = MagicMock()
    dummy_client.base_url = "http://fake"  # Attribute accessed in some code paths

    model = OpenAIChatCompletionsModel(model=cai_model, openai_client=dummy_client)  # type: ignore[arg-type]
    model.agent_name = "Bug Bounter"
    model.agent_id = "P42"
    model._display_name = "Bug Bounter"
    model._terminal_id = "terminal-9"
    model._terminal_number = 9

    token_info = model.get_token_info()

    assert token_info["agent_name"] == "Bug Bounter"
    assert token_info["agent_id"] == "P42"
    assert token_info["display_name"] == "Bug Bounter"
    assert token_info["terminal_id"] == "terminal-9"
    assert token_info["terminal_number"] == 9


@pytest.mark.asyncio
async def test_fetch_response_non_stream(monkeypatch) -> None:
    """
    Verify that `_fetch_response` builds the correct kwargs for the completion
    call when not streaming and returns the ChatCompletion object directly.
    We intercept the LiteLLM adapter to record what kwargs are forwarded.
    """
    from unittest.mock import AsyncMock, MagicMock

    msg = ChatCompletionMessage(role="assistant", content="ignored")
    choice = Choice(index=0, finish_reason="stop", message=msg)
    chat = ChatCompletion(
        id="resp-id",
        created=0,
        model="fake",
        object="chat.completion",
        choices=[choice],
    )

    captured_kwargs: dict[str, Any] = {}

    async def fake_litellm_openai(kwargs, model_name, model_settings, tool_choice, stream, parallel_tool_calls):
        captured_kwargs.update(kwargs)
        captured_kwargs["stream"] = stream
        return chat

    monkeypatch.setattr(
        "cai.sdk.agents.models.openai_chatcompletions._fetch_litellm_openai_impl",
        fake_litellm_openai,
    )

    _model = "gpt-4"
    dummy_client = MagicMock()
    dummy_client.base_url = httpx.URL("http://fake")
    model = OpenAIChatCompletionsModel(
        model=_model,
        openai_client=dummy_client,
        agent_name="FetchResponseNonStreamAgent",
    )  # type: ignore
    with generation_span(disabled=True) as span:
        result = await model._fetch_response(
            system_instructions="sys",
            input="hi",
            model_settings=ModelSettings(),
            tools=[],
            output_schema=None,
            handoffs=[],
            span=span,
            tracing=ModelTracing.DISABLED,
            stream=False,
        )

    # Verify the chat was returned
    assert result == chat
    # Verify the kwargs passed to the completion call
    assert captured_kwargs["stream"] is False
    assert captured_kwargs["model"] == _model
    assert captured_kwargs["messages"][0]["role"] == "system"
    assert captured_kwargs["messages"][0]["content"] == "sys"
    assert captured_kwargs["messages"][1]["role"] == "user"


@pytest.mark.asyncio
async def test_fetch_response_stream(monkeypatch) -> None:
    """
    When `stream=True`, `_fetch_response` should return a bare `Response`
    object along with the underlying async stream. Intercept the LiteLLM
    adapter to record forwarded kwargs and return a dummy async iterator.
    """
    from unittest.mock import MagicMock

    os.environ["CAI_STREAM"] = "true"

    async def event_stream() -> AsyncIterator[ChatCompletionChunk]:
        if False:  # pragma: no cover
            yield  # pragma: no cover

    captured_kwargs: dict[str, Any] = {}

    async def fake_litellm_openai(kwargs, model_name, model_settings, tool_choice, stream, parallel_tool_calls):
        captured_kwargs.update(kwargs)
        captured_kwargs["stream"] = stream
        # Return (Response, stream) tuple as the real implementation does for stream=True
        from cai.sdk.agents.models.fake_id import FAKE_RESPONSES_ID
        from openai.types.responses import Response as OAIResponse
        fake_resp = OAIResponse(
            id=FAKE_RESPONSES_ID,
            created_at=0,
            model=model_name,
            object="response",
            output=[],
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
        )
        return fake_resp, event_stream()

    monkeypatch.setattr(
        "cai.sdk.agents.models.openai_chatcompletions._fetch_litellm_openai_impl",
        fake_litellm_openai,
    )

    _model = "gpt-4"
    dummy_client = MagicMock()
    dummy_client.base_url = httpx.URL("http://fake")
    model = OpenAIChatCompletionsModel(model=_model, openai_client=dummy_client)  # type: ignore
    with generation_span(disabled=True) as span:
        response, stream = await model._fetch_response(
            system_instructions=None,
            input="hi",
            model_settings=ModelSettings(),
            tools=[],
            output_schema=None,
            handoffs=[],
            span=span,
            tracing=ModelTracing.DISABLED,
            stream=True,
        )
    # Verify streaming kwargs forwarded
    assert captured_kwargs["stream"] is True
    # Response is a proper openai Response
    assert isinstance(response, Response)
    assert response.id == FAKE_RESPONSES_ID
    assert response.model == _model
    assert response.object == "response"
    assert response.output == []
    # We returned the async iterator produced by our dummy.
    assert hasattr(stream, "__aiter__")


@pytest.mark.allow_call_model_methods
@pytest.mark.asyncio
async def test_interaction_counter_single_turn_with_tool_calls(monkeypatch) -> None:
    """
    Test that when the LLM returns both a message and tool calls in the same turn,
    the interaction counter is incremented only once (not separately for message and tool calls).
    """
    # Create a response with both message content and tool calls
    tool_call = ChatCompletionMessageToolCall(
        id="call-id",
        type="function",
        function=Function(name="do_thing", arguments='{"x":1}'),
    )
    msg = ChatCompletionMessage(
        role="assistant",
        content="I'll help you with that. Let me use a tool.",
        tool_calls=[tool_call],
    )
    choice = Choice(index=0, finish_reason="stop", message=msg)
    chat = ChatCompletion(
        id="resp-id",
        created=0,
        model="fake",
        object="chat.completion",
        choices=[choice],
        usage=CompletionUsage(completion_tokens=10, prompt_tokens=5, total_tokens=15),
    )

    async def patched_fetch_response(self, *args, **kwargs):
        return chat

    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-tests")
    monkeypatch.setattr(OpenAIChatCompletionsModel, "_fetch_response", patched_fetch_response)
    model = OpenAIProvider(use_responses=False).get_model(cai_model)

    # Initial counter should be 0
    assert model.interaction_counter == 0

    # Make the request
    resp: ModelResponse = await model.get_response(
        system_instructions="You are a helpful assistant",
        input="Help me with something",
        model_settings=ModelSettings(),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
    )

    # Counter should be incremented only once for the entire turn
    assert model.interaction_counter == 1

    # Verify response contains both message and tool call
    assert len(resp.output) == 2  # One message item, one tool call item
    assert isinstance(resp.output[0], ResponseOutputMessage)
    assert isinstance(resp.output[1], ResponseFunctionToolCall)

    # Make another request to ensure counter increments properly
    resp2: ModelResponse = await model.get_response(
        system_instructions="You are a helpful assistant",
        input="Another request",
        model_settings=ModelSettings(),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
    )

    # Counter should now be 2 (one increment per turn, not per item)
    assert model.interaction_counter == 2
