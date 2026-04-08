"""RAG monitoring tool exposed to agents.

Provides a small function-tool `get_rag_status()` that returns a snapshot
of in-memory RAG metrics collected by the adapters (faiss cache metrics,
recent queries, and totals).
"""
from __future__ import annotations

from typing import Any, Dict

from cai.rag.vector_db_adapter import get_rag_status as _get_rag_status
from cai.sdk.agents import function_tool


def _get_rag_status_impl() -> Dict[str, Any]:
    """Return RAG status snapshot."""
    return _get_rag_status()


# Expose as a function tool for agents
get_rag_status = function_tool(_get_rag_status_impl)
