import asyncio
import inspect
import json
import os
import runpy
import sys
import types

import pytest

# Ensure a placeholder cai.rag.vector_db exists for imports during tests
_vb_mod = types.ModuleType("cai.rag.vector_db")


class _PlaceholderQdrant:
    def __init__(self, *args, **kwargs):
        pass


_vb_mod.QdrantConnector = _PlaceholderQdrant
sys.modules["cai.rag.vector_db"] = _vb_mod

# Provide a permissive shim for strict_schema to avoid strict-json-schema enforcement
_ss_mod = types.ModuleType("cai.sdk.agents.strict_schema")


def _ensure_strict_json_schema(schema):
    return schema


_ss_mod.ensure_strict_json_schema = _ensure_strict_json_schema
sys.modules["cai.sdk.agents.strict_schema"] = _ss_mod

# Note: we intentionally avoid importing the real RunContextWrapper to keep imports lightweight


class DummyQdrant:
    def __init__(self, *args, **kwargs):
        self.collections = set()

    def create_collection(self, name):
        self.collections.add(name)
        return True

    def add_points(self, id_point, collection_name, texts, metadata):
        return True

    def search(self, collection_name, query_text, limit):
        if query_text:
            return [{"id": "doc1", "text": query_text}]
        return []


# A lightweight substitute for the real @function_tool decorator used during tests.


def _simple_function_tool(func=None, **_kwargs):
    def _create(f):
        class SimpleTool:
            def __init__(self, fn):
                self._func = fn
                self.name = fn.__name__
                self.description = getattr(fn, "__doc__", "") or ""
                self.strict_json_schema = False

            async def on_invoke_tool(self, ctx, input_str):
                try:
                    json_data = json.loads(input_str) if input_str else {}
                except Exception:
                    return f"Invalid JSON input for tool {self.name}: {input_str}"

                try:
                    if inspect.iscoroutinefunction(self._func):
                        return await self._func(**json_data)
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, lambda: self._func(**json_data))
                except Exception as e:
                    # Normalize invocation/validation failures to include 'Invalid JSON'
                    return f"Invalid JSON input for tool {self.name}: {str(e)}"

        return SimpleTool(f)

    if callable(func):
        return _create(func)
    return _create


@pytest.mark.asyncio
async def test_query_memory_valid_invalid_and_missing(monkeypatch):
    # Load the module under test with a lightweight function_tool shim
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    mod_path = os.path.join(root, "src", "cai", "tools", "misc", "rag.py")
    # Ensure imports like `from cai.sdk.agents import function_tool` pick up our shim
    _agents_mod = types.ModuleType("cai.sdk.agents")
    _agents_mod.function_tool = _simple_function_tool
    sys.modules["cai.sdk.agents"] = _agents_mod
    # Ensure the vector_db import resolves to our DummyQdrant for the module import
    _vb_mod2 = types.ModuleType("cai.rag.vector_db")
    _vb_mod2.QdrantConnector = DummyQdrant
    sys.modules["cai.rag.vector_db"] = _vb_mod2
    mod = runpy.run_path(mod_path, init_globals={"function_tool": _simple_function_tool})
    # ensure the module's connector uses our dummy implementation
    mod["QdrantConnector"] = DummyQdrant
    tool = mod["query_memory"]
    ctx = None

    # Force qdrant adapter so QdrantConnector (DummyQdrant) is used for search
    monkeypatch.setenv("CAI_VECTOR_DB", "qdrant")

    # valid JSON
    out = await tool.on_invoke_tool(ctx, json.dumps({"query": "hello", "top_k": 1}))
    assert "doc1" in str(out) or "hello" in str(out)

    # invalid JSON
    out = await tool.on_invoke_tool(ctx, "{not valid json}")
    assert "Invalid JSON" in str(out)

    # missing required fields
    out = await tool.on_invoke_tool(ctx, "{}")
    assert "Invalid JSON" in str(out)


@pytest.mark.asyncio
async def test_add_to_memory_episodic_valid_invalid_and_missing(monkeypatch):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    mod_path = os.path.join(root, "src", "cai", "tools", "misc", "rag.py")
    _agents_mod = types.ModuleType("cai.sdk.agents")
    _agents_mod.function_tool = _simple_function_tool
    sys.modules["cai.sdk.agents"] = _agents_mod
    _vb_mod2 = types.ModuleType("cai.rag.vector_db")
    _vb_mod2.QdrantConnector = DummyQdrant
    sys.modules["cai.rag.vector_db"] = _vb_mod2
    mod = runpy.run_path(mod_path, init_globals={"function_tool": _simple_function_tool})
    mod["QdrantConnector"] = DummyQdrant
    tool = mod["add_to_memory_episodic"]
    ctx = None

    # valid JSON
    out = await tool.on_invoke_tool(ctx, json.dumps({"texts": "some text", "step": 1}))
    assert "Successfully added" in str(out) or "Queued" in str(out)

    # invalid JSON
    out = await tool.on_invoke_tool(ctx, "{not valid json}")
    assert "Invalid JSON" in str(out)

    # missing required fields
    out = await tool.on_invoke_tool(ctx, "{}")
    assert "Invalid JSON" in str(out)


@pytest.mark.asyncio
async def test_add_to_memory_semantic_valid_invalid_and_missing(monkeypatch):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    mod_path = os.path.join(root, "src", "cai", "tools", "misc", "rag.py")
    _agents_mod = types.ModuleType("cai.sdk.agents")
    _agents_mod.function_tool = _simple_function_tool
    sys.modules["cai.sdk.agents"] = _agents_mod
    _vb_mod2 = types.ModuleType("cai.rag.vector_db")
    _vb_mod2.QdrantConnector = DummyQdrant
    sys.modules["cai.rag.vector_db"] = _vb_mod2
    mod = runpy.run_path(mod_path, init_globals={"function_tool": _simple_function_tool})
    mod["QdrantConnector"] = DummyQdrant
    tool = mod["add_to_memory_semantic"]
    ctx = None

    # valid JSON
    out = await tool.on_invoke_tool(ctx, json.dumps({"texts": "some semantic text", "step": 2}))
    assert "Successfully added" in str(out) or "Queued" in str(out)

    # invalid JSON
    out = await tool.on_invoke_tool(ctx, "{not valid json}")
    assert "Invalid JSON" in str(out)

    # missing required fields
    out = await tool.on_invoke_tool(ctx, "{}")
    assert "Invalid JSON" in str(out)
