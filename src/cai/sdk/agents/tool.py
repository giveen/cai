from __future__ import annotations

import inspect
import json
import ast
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable, Literal, Union, overload

from typing import TYPE_CHECKING
try:
    from pydantic import ValidationError
except ImportError:  # pragma: no cover
    class ValidationError(Exception):  # type: ignore[no-redef]
        pass
if TYPE_CHECKING:
    from openai.types.responses.file_search_tool_param import Filters, RankingOptions
    from openai.types.responses.web_search_tool_param import UserLocation
else:
    # Runtime fallbacks when optional packages are missing
    Filters = Any
    RankingOptions = Any
    UserLocation = Any
from typing_extensions import Concatenate, ParamSpec

from . import _debug
from .computer import AsyncComputer, Computer
from .exceptions import ModelBehaviorError
try:
    from .function_schema import DocstringStyle, function_schema
except Exception:  # pragma: no cover - optional docstring parsing support
    from typing import Any

    DocstringStyle = Any

    def function_schema(*args, **kwargs):
        raise RuntimeError("function_schema is unavailable because optional dependencies are missing")
from .items import RunItem
from .logger import logger
from .run_context import RunContextWrapper
from .tracing import SpanError
from .util import _error_tracing
from .util._types import MaybeAwaitable


def truncate_for_logging(output: Any, max_length: int = 1000) -> str:
    """Truncate output for logging purposes."""
    output_str = str(output)
    if len(output_str) <= max_length:
        return output_str
    return f"{output_str[:max_length]}... (truncated)"


def _forgiving_json_loads(s: str) -> Any:
    """Attempt to repair and parse common malformed JSON emitted by LLMs.

    Heuristics (best-effort, non-destructive):
    - extract a JSON-like substring between the first { and last } or [ and ]
    - try ast.literal_eval for python-style dicts
    - replace single quotes with double quotes and normalize True/False/None
    - fall back to simple key:value pair extraction
    Raises ValueError if unable to parse.
    """
    if not isinstance(s, str):
        raise ValueError("input must be a string")
    st = s.strip()

    # Try direct JSON first
    try:
        return json.loads(st)
    except Exception:
        pass

    # Extract JSON-like substring {...} or [...] if present
    try:
        if "{" in st and "}" in st:
            i = st.find("{")
            j = st.rfind("}")
            cand = st[i : j + 1]
            try:
                return json.loads(cand)
            except Exception:
                # try ast.literal_eval
                try:
                    val = ast.literal_eval(cand)
                    if isinstance(val, (dict, list)):
                        return val
                except Exception:
                    pass
        if "[" in st and "]" in st:
            i = st.find("[")
            j = st.rfind("]")
            cand = st[i : j + 1]
            try:
                return json.loads(cand)
            except Exception:
                try:
                    val = ast.literal_eval(cand)
                    if isinstance(val, (dict, list)):
                        return val
                except Exception:
                    pass
    except Exception:
        pass

    # Try ast.literal_eval on the whole string (accepts single quotes, True/False/None)
    try:
        val = ast.literal_eval(st)
        if isinstance(val, (dict, list)):
            return val
    except Exception:
        pass

    # Heuristic: replace single quotes with double quotes and normalize booleans/null
    try:
        t = st
        # remove common trailing text like 'Output:' or surrounding backticks
        t = re.sub(r"^.*?(```|\()", "", t)
        t = re.sub(r"(```|\)).*$", "", t)
        # remove stray newlines
        t = t.replace("\n", " ")
        t2 = t.replace("'", '"')
        t2 = re.sub(r"\bNone\b", "null", t2)
        t2 = re.sub(r"\bTrue\b", "true", t2)
        t2 = re.sub(r"\bFalse\b", "false", t2)
        t2 = re.sub(r",\s*}\b", "}", t2)
        return json.loads(t2)
    except Exception:
        pass

    # Last resort: extract simple key:value pairs like key: value
    try:
        pairs = re.findall(r"([A-Za-z0-9_\-]+)\s*[:=]\s*\"?([^,\n\}\]]+)\"?", st)
        if pairs:
            out = {}
            for k, v in pairs:
                out[k.strip()] = v.strip().strip('"').strip("'")
            return out
    except Exception:
        pass

    raise ValueError("Unable to parse input as JSON")

ToolParams = ParamSpec("ToolParams")

ToolFunctionWithoutContext = Callable[ToolParams, Any]
ToolFunctionWithContext = Callable[Concatenate[RunContextWrapper[Any], ToolParams], Any]

ToolFunction = Union[ToolFunctionWithoutContext[ToolParams], ToolFunctionWithContext[ToolParams]]


@dataclass
class FunctionToolResult:
    tool: FunctionTool
    """The tool that was run."""

    output: Any
    """The output of the tool."""

    run_item: RunItem
    """The run item that was produced as a result of the tool call."""


@dataclass
class FunctionTool:
    """A tool that wraps a function. In most cases, you should use  the `function_tool` helpers to
    create a FunctionTool, as they let you easily wrap a Python function.
    """

    name: str
    """The name of the tool, as shown to the LLM. Generally the name of the function."""

    description: str
    """A description of the tool, as shown to the LLM."""

    params_json_schema: dict[str, Any]
    """The JSON schema for the tool's parameters."""

    on_invoke_tool: Callable[[RunContextWrapper[Any], str], Awaitable[Any]]
    """A function that invokes the tool with the given context and parameters. The params passed
    are:
    1. The tool run context.
    2. The arguments from the LLM, as a JSON string.

    You must return a string representation of the tool output, or something we can call `str()` on.
    In case of errors, you can either raise an Exception (which will cause the run to fail) or
    return a string error message (which will be sent back to the LLM).
    """

    strict_json_schema: bool = True
    """Whether the JSON schema is in strict mode. We **strongly** recommend setting this to True,
    as it increases the likelihood of correct JSON input."""

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Allow calling the FunctionTool directly like a coroutine for convenience.

        Positional arguments are mapped to schema properties in declaration order;
        keyword arguments are passed by name.
        """
        props = list(self.params_json_schema.get("properties", {}).keys())
        json_data = dict(zip(props, args))
        json_data.update(kwargs)
        ctx = RunContextWrapper(context=None)
        return await self.on_invoke_tool(ctx, json.dumps(json_data))


@dataclass
class FileSearchTool:
    """A hosted tool that lets the LLM search through a vector store. Currently only supported with
    OpenAI models, using the Responses API.
    """

    vector_store_ids: list[str]
    """The IDs of the vector stores to search."""

    max_num_results: int | None = None
    """The maximum number of results to return."""

    include_search_results: bool = False
    """Whether to include the search results in the output produced by the LLM."""

    ranking_options: RankingOptions | None = None
    """Ranking options for search."""

    filters: Filters | None = None
    """A filter to apply based on file attributes."""

    @property
    def name(self):
        return "file_search"


@dataclass
class WebSearchTool:
    """A hosted tool that lets the LLM search the web. Currently only supported with OpenAI models,
    using the Responses API.
    """

    user_location: UserLocation | None = None
    """Optional location for the search. Lets you customize results to be relevant to a location."""

    search_context_size: Literal["low", "medium", "high"] = "medium"
    """The amount of context to use for the search."""

    @property
    def name(self):
        return "web_search_preview"


@dataclass
class ComputerTool:
    """A hosted tool that lets the LLM control a computer."""

    computer: Computer | AsyncComputer
    """The computer implementation, which describes the environment and dimensions of the computer,
    as well as implements the computer actions like click, screenshot, etc.
    """

    @property
    def name(self):
        return "computer_use_preview"


Tool = Union[FunctionTool, FileSearchTool, WebSearchTool, ComputerTool]
"""A tool that can be used in an agent."""


def default_tool_error_function(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """The default tool error function, which just returns a generic error message."""
    return f"An error occurred while running the tool. Please try again. Error: {str(error)}"


ToolErrorFunction = Callable[[RunContextWrapper[Any], Exception], MaybeAwaitable[str]]


@overload
def function_tool(
    func: ToolFunction[...],
    *,
    name_override: str | None = None,
    description_override: str | None = None,
    docstring_style: DocstringStyle | None = None,
    use_docstring_info: bool = True,
    failure_error_function: ToolErrorFunction | None = None,
    strict_mode: bool = True,
) -> FunctionTool:
    """Overload for usage as @function_tool (no parentheses)."""
    ...


@overload
def function_tool(
    *,
    name_override: str | None = None,
    description_override: str | None = None,
    docstring_style: DocstringStyle | None = None,
    use_docstring_info: bool = True,
    failure_error_function: ToolErrorFunction | None = None,
    strict_mode: bool = True,
) -> Callable[[ToolFunction[...]], FunctionTool]:
    """Overload for usage as @function_tool(...)."""
    ...


def function_tool(
    func: ToolFunction[...] | None = None,
    *,
    name_override: str | None = None,
    description_override: str | None = None,
    docstring_style: DocstringStyle | None = None,
    use_docstring_info: bool = True,
    failure_error_function: ToolErrorFunction | None = default_tool_error_function,
    strict_mode: bool = True,
) -> FunctionTool | Callable[[ToolFunction[...]], FunctionTool]:
    """
    Decorator to create a FunctionTool from a function. By default, we will:
    1. Parse the function signature to create a JSON schema for the tool's parameters.
    2. Use the function's docstring to populate the tool's description.
    3. Use the function's docstring to populate argument descriptions.
    The docstring style is detected automatically, but you can override it.

    If the function takes a `RunContextWrapper` as the first argument, it *must* match the
    context type of the agent that uses the tool.

    Args:
        func: The function to wrap.
        name_override: If provided, use this name for the tool instead of the function's name.
        description_override: If provided, use this description for the tool instead of the
            function's docstring.
        docstring_style: If provided, use this style for the tool's docstring. If not provided,
            we will attempt to auto-detect the style.
        use_docstring_info: If True, use the function's docstring to populate the tool's
            description and argument descriptions.
        failure_error_function: If provided, use this function to generate an error message when
            the tool call fails. The error message is sent to the LLM. If you pass None, then no
            error message will be sent and instead an Exception will be raised.
        strict_mode: Whether to enable strict mode for the tool's JSON schema. We *strongly*
            recommend setting this to True, as it increases the likelihood of correct JSON input.
            If False, it allows non-strict JSON schemas. For example, if a parameter has a default
            value, it will be optional, additional properties are allowed, etc. See here for more:
            https://platform.openai.com/docs/guides/structured-outputs?api-mode=responses#supported-schemas
    """

    def _create_function_tool(the_func: ToolFunction[...]) -> FunctionTool:
        schema = function_schema(
            func=the_func,
            name_override=name_override,
            description_override=description_override,
            docstring_style=docstring_style,
            use_docstring_info=use_docstring_info,
            strict_json_schema=strict_mode,
        )

        def _sanitize_parsed_json(json_data: Any) -> Any:
            """Attempt to repair common nesting issues in parsed tool JSON.

            Heuristics:
            - If top-level expected parameter names are missing, look for them
              inside nested dict values and promote them to top-level.
            - If promoted primitive values are numeric/bool but likely expected
              as strings (e.g., host/domain), coerce to `str` to satisfy Pydantic.
            This is intentionally conservative and only promotes when the
            top-level key is absent.
            """
            try:
                if not isinstance(json_data, dict):
                    return json_data

                # discover expected param names from the pydantic model
                expected_fields = set()
                try:
                    expected_fields = set(getattr(schema.params_pydantic_model, "__fields__", {}).keys())
                except Exception:
                    try:
                        expected_fields = set(getattr(schema.params_pydantic_model, "model_fields", {}).keys())
                    except Exception:
                        expected_fields = set()

                if not expected_fields:
                    return json_data

                # Promote nested keys when missing at top-level
                missing = expected_fields - set(json_data.keys())
                if missing:
                    # First, if some top-level entries are dicts containing expected keys,
                    # promote those nested keys to top-level.
                    for k, v in list(json_data.items()):
                        if isinstance(v, dict):
                            for nk in list(v.keys()):
                                if nk in missing and nk not in json_data:
                                    val = v.get(nk)
                                    # Coerce primitive numeric/bool to str for safety
                                    if isinstance(val, (int, float, bool)):
                                        val = str(val)
                                    json_data[nk] = val
                                    missing.discard(nk)

                    # If still missing, search deeper across other nested dicts
                    if missing:
                        for want in list(missing):
                            for k, v in list(json_data.items()):
                                if isinstance(v, dict) and want in v:
                                    val = v.get(want)
                                    if isinstance(val, (int, float, bool)):
                                        val = str(val)
                                    json_data[want] = val
                                    missing.discard(want)
                                    break

                # Determine expected field types (pydantic v1/v2 compat) so we
                # only coerce dict->str when the schema actually expects a string
                expected_types: dict[str, Any] = {}
                try:
                    # Pydantic v1
                    if hasattr(schema.params_pydantic_model, "__fields__") and getattr(schema.params_pydantic_model, "__fields__", None):
                        for fname, fobj in getattr(schema.params_pydantic_model, "__fields__", {}).items():
                            expected_types[fname] = getattr(fobj, "outer_type_", None) or getattr(fobj, "type_", None)
                    # Pydantic v2
                    elif hasattr(schema.params_pydantic_model, "model_fields") and getattr(schema.params_pydantic_model, "model_fields", None):
                        for fname, finfo in getattr(schema.params_pydantic_model, "model_fields", {}).items():
                            # finfo is a dict-like with an 'annotation' key
                            try:
                                expected_types[fname] = finfo.get("annotation") if isinstance(finfo, dict) else None
                            except Exception:
                                expected_types[fname] = None
                except Exception:
                    expected_types = {}

                def _expects_str(tp: Any) -> bool:
                    """Return True when the expected type is (or includes) `str`."""
                    try:
                        if tp is str:
                            return True
                        # Handle typing.Union and other generic aliases
                        from typing import get_origin, get_args

                        origin = get_origin(tp)
                        if origin is None:
                            # If it's a typing alias like 'str' wrapped, try equality
                            return False
                        if origin is Union:
                            return any(_expects_str(a) for a in get_args(tp))
                        return False
                    except Exception:
                        return False

                # Finally, if some values are dicts but the schema expects primitives (notably str),
                # try to coerce simple dict->str by JSON-encoding the dict. Only do this when the
                # expected type explicitly includes `str` to avoid converting model/dict types.
                for k in list(json_data.keys()):
                    if k in expected_fields and isinstance(json_data[k], dict):
                        exp = expected_types.get(k)
                        if _expects_str(exp):
                            try:
                                json_data[k] = json.dumps(json_data[k])
                            except Exception:
                                json_data[k] = str(json_data[k])

                return json_data
            except Exception:
                return json_data


        async def _on_invoke_tool_impl(ctx: RunContextWrapper[Any], input: str) -> Any:
            # Parse JSON input; attempt forgiving repairs for common LLM output formats
            json_data: dict[str, Any] = {}
            if input:
                try:
                    json_data = json.loads(input)
                except Exception as e:
                    try:
                        json_data = _forgiving_json_loads(input)
                        if _debug.DONT_LOG_TOOL_DATA:
                            logger.debug(f"Forgiving-parse succeeded for tool {schema.name}")
                        else:
                            logger.debug(f"Forgiving-parse succeeded for tool {schema.name}: {input}")
                    except Exception as e2:
                        if _debug.DONT_LOG_TOOL_DATA:
                            logger.debug(f"Invalid JSON input for tool {schema.name}")
                        else:
                            logger.debug(f"Invalid JSON input for tool {schema.name}: {input}")
                        raise ModelBehaviorError(
                            f"Invalid JSON input for tool {schema.name}: {input}"
                        ) from e2

            # Sanitize parsed JSON to handle common nesting/mis-formatting cases
            try:
                json_data = _sanitize_parsed_json(json_data)
            except Exception:
                # If sanitizer fails for any reason, continue with original json_data
                pass

            if _debug.DONT_LOG_TOOL_DATA:
                logger.debug(f"Invoking tool {schema.name}")
            else:
                logger.debug(f"Invoking tool {schema.name} with input {input}")

            try:
                parsed = (
                    schema.params_pydantic_model(**json_data)
                    if json_data
                    else schema.params_pydantic_model()
                )
            except ValidationError as e:
                raise ModelBehaviorError(f"Invalid JSON input for tool {schema.name}: {e}") from e

            args, kwargs_dict = schema.to_call_args(parsed)

            if not _debug.DONT_LOG_TOOL_DATA:
                logger.debug(f"Tool call args: {args}, kwargs: {kwargs_dict}")

            if inspect.iscoroutinefunction(the_func):
                if schema.takes_context:
                    result = await the_func(ctx, *args, **kwargs_dict)
                else:
                    result = await the_func(*args, **kwargs_dict)
            else:
                # Run synchronous functions in a thread pool to avoid blocking the event loop
                import asyncio
                import functools
                
                if schema.takes_context:
                    func_with_args = functools.partial(the_func, ctx, *args, **kwargs_dict)
                else:
                    func_with_args = functools.partial(the_func, *args, **kwargs_dict)
                
                # Run in thread pool executor to prevent blocking
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, func_with_args)

            if _debug.DONT_LOG_TOOL_DATA:
                logger.debug(f"Tool {schema.name} completed.")
            else:
                logger.debug(f"Tool {schema.name} returned {truncate_for_logging(result)}")

            return result

        async def _on_invoke_tool(ctx: RunContextWrapper[Any], input: str) -> Any:
            try:
                return await _on_invoke_tool_impl(ctx, input)
            except Exception as e:
                if failure_error_function is None:
                    raise

                result = failure_error_function(ctx, e)
                if inspect.isawaitable(result):
                    return await result

                _error_tracing.attach_error_to_current_span(
                    SpanError(
                        message="Error running tool (non-fatal)",
                        data={
                            "tool_name": schema.name,
                            "error": str(e),
                        },
                    )
                )
                return result

        return FunctionTool(
            name=schema.name,
            description=schema.description or "",
            params_json_schema=schema.params_json_schema,
            on_invoke_tool=_on_invoke_tool,
            strict_json_schema=strict_mode,
        )

    # If func is actually a callable, we were used as @function_tool with no parentheses
    if callable(func):
        return _create_function_tool(func)

    # Otherwise, we were used as @function_tool(...), so return a decorator
    def decorator(real_func: ToolFunction[...]) -> FunctionTool:
        return _create_function_tool(real_func)

    return decorator
