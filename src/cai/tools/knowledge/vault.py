"""Cyber-Vault knowledge tool.

Provides `query_knowledge_base` — a @function_tool that queries the local
ChromaDB vector database built by ``scripts/ingest_vault.py``.

The ChromaDB is embedded with the ``all-MiniLM-L6-v2`` SentenceTransformer
so the entire retrieval pipeline runs offline, without any API keys.

If the database has not been built yet the tool returns a helpful message
explaining how to run the ingestion script.

TUI integration
---------------
The tool calls ``notify_tool_loading(True)`` at start and ``False`` on exit so
the Textual TUI can show a ``LoadingIndicator`` labelled "Searching Cyber-Vault…"
while the embedding + query runs.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from cai.sdk.agents import function_tool
from cai.util import notify_tool_loading, write_progress

_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"
_CHROMA_DIR = _KNOWLEDGE_DIR / "chroma_db"
_COLLECTION_NAME = "cyber_vault"
_EMBED_MODEL = "all-MiniLM-L6-v2"

# Lazy singletons so the tool is importable even when deps are missing
_client = None
_collection = None
_embed_fn = None
_load_lock = threading.Lock()


def _ensure_loaded() -> str | None:
    """Initialise the ChromaDB client and collection. Returns an error string or None."""
    global _client, _collection, _embed_fn

    if _collection is not None:
        return None  # already loaded

    with _load_lock:
        if _collection is not None:
            return None

        # Check DB exists
        if not _CHROMA_DIR.exists():
            return (
                "[CYBER-VAULT] Database not found. "
                "Run `python scripts/ingest_vault.py` from the repo root to build it."
            )

        try:
            import chromadb  # type: ignore
        except ModuleNotFoundError:
            return "[CYBER-VAULT] chromadb not installed. Run: pip install chromadb"

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ModuleNotFoundError:
            return (
                "[CYBER-VAULT] sentence-transformers not installed."
                " Run: pip install sentence-transformers"
            )

        try:
            _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))

            # Build embedding function wrapper
            _model = SentenceTransformer(_EMBED_MODEL)

            class _EmbedFn:
                def _encode(self, texts: list[str]) -> list[list[float]]:
                    return _model.encode(
                        texts,
                        convert_to_numpy=True,
                        show_progress_bar=False,
                    ).tolist()

                def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
                    return self._encode(input)

                def embed_documents(self, input: list[str]) -> list[list[float]]:  # noqa: A002
                    """ChromaDB calls this when ingesting/upserting documents."""
                    return self._encode(input)

                def embed_query(self, input: list[str]) -> list[list[float]]:  # noqa: A002
                    """ChromaDB calls this when querying the collection."""
                    return self._encode(input)

                def name(self) -> str:
                    """Return a stable name for this embedding function.

                    ChromaDB may call `name()` on embedding functions for
                    configuration checks; provide a simple identifier so the
                    client code is compatible with newer chromadb versions.
                    """
                    return f"sentence-transformers:{_EMBED_MODEL}"

            _embed_fn = _EmbedFn()
            _collection = _client.get_collection(
                name=_COLLECTION_NAME,
                embedding_function=_embed_fn,  # type: ignore[arg-type]
            )
        except Exception as exc:
            _client = _collection = _embed_fn = None
            return f"[CYBER-VAULT] Failed to open collection: {exc}"

    return None


def _wrap_code_blocks(text: str) -> str:
    """Ensure fenced code blocks are properly closed in the chunk text."""
    # Count opening ``` fences; close the last unclosed one if needed
    fences = re.findall(r"^```", text, re.MULTILINE)
    if len(fences) % 2 != 0:
        text = text.rstrip() + "\n```"
    return text


def _format_result(rank: int, doc: str, meta: dict) -> str:
    source = meta.get("source", "unknown")
    chunk_idx = meta.get("chunk_index", "?")
    doc = _wrap_code_blocks(doc.strip())

    lines = [
        f"### [{rank}] {source}  (chunk {chunk_idx})",
        "",
        doc,
        "",
    ]
    return "\n".join(lines)


@function_tool
def query_knowledge_base(query: str, top_k: int = 3) -> str:
    """Query the local Cyber-Vault (HackTricks + PayloadsAllTheThings).

    Retrieves the *top_k* most relevant Markdown chunks (default 3) for *query*
    using locally computed sentence embeddings — no internet or API key required.

    Returns a formatted string prefixed with ``[LOCAL KNOWLEDGE - OFFLINE VAULT]``
    containing the source file path and full chunk content for each result.
    Exploit payloads are automatically wrapped in fenced code blocks.

    Run ``python scripts/ingest_vault.py`` once to build the index.
    """
    query = (query or "").strip()
    if not query:
        return "[CYBER-VAULT] query parameter is required."

    # Clamp top_k to a safe range
    top_k = max(1, min(int(top_k), 10))

    notify_tool_loading(True)
    write_progress("Searching Cyber-Vault…", "cyan")

    err = _ensure_loaded()
    if err:
        notify_tool_loading(False)
        return err

    try:
        results = _collection.query(query_texts=[query], n_results=top_k)  # type: ignore[union-attr]
    except Exception as exc:
        notify_tool_loading(False)
        return f"[CYBER-VAULT] Query failed: {exc}"

    docs: list[str] = (results.get("documents") or [[]])[0]  # type: ignore[index]
    metas: list[dict] = (results.get("metadatas") or [[]])[0]  # type: ignore[assignment,index]
    distances: list[float] = (results.get("distances") or [[]])[0]  # type: ignore[index]

    if not docs:
        notify_tool_loading(False)
        return "[CYBER-VAULT] No results found for that query."

    parts = [
        "[LOCAL KNOWLEDGE - OFFLINE VAULT]",
        f"Query: {query}",
        f"Results: {len(docs)} of {top_k} requested",
        "",
        "---",
        "",
    ]

    for rank, (doc, meta, dist) in enumerate(zip(docs, metas, distances), 1):
        similarity = round(1 - dist, 4) if dist is not None else "?"
        parts.append(f"> Relevance score: {similarity}")
        parts.append(_format_result(rank, doc, meta))
        parts.append("---")
        parts.append("")

    notify_tool_loading(False)
    write_progress(f"Cyber-Vault: {len(docs)} results retrieved.", "green")
    return "\n".join(parts)
