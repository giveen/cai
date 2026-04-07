"""Vector DB adapter interface and concrete adapters.

Provides a small adapter abstraction so callers can switch between
Qdrant (existing) and MemPalace for A/B testing retrieval quality.

The intention is non-destructive: QdrantAdapter delegates to the
project's existing `QdrantConnector` when available; MemPalaceAdapter
implements best-effort read/search via the `mempalace` Python API or
CLI. Add/ingest operations for MemPalace are intentionally not implemented
here to avoid accidental data migration.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from abc import ABC, abstractmethod
from typing import Any, List, Optional


class VectorDBAdapter(ABC):
    """Abstract Vector DB adapter."""

    @abstractmethod
    def search(self, collection_name: str, query_text: str, limit: int = 3) -> Any:
        raise NotImplementedError()

    @abstractmethod
    def create_collection(self, collection_name: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def add_points(self, id_point: Any, collection_name: str, texts: List[str], metadata: List[dict]) -> bool:
        raise NotImplementedError()


def get_vector_db_adapter(name: Optional[str] = None, **kwargs) -> VectorDBAdapter:
    """Factory to get an adapter by name or environment `CAI_VECTOR_DB`.

    Supported names: "qdrant" (default), "mempalace".
    """
    source = (name or os.getenv("CAI_VECTOR_DB", "qdrant")).lower()
    if source in ("qdrant", "q"):
        return QdrantAdapter(**kwargs)
    if source in ("mempalace", "palace", "mp"):
        return MemPalaceAdapter(**kwargs)
    raise ValueError(f"Unknown vector DB adapter: {source}")


class QdrantAdapter(VectorDBAdapter):
    """Adapter that delegates to the project's existing Qdrant connector.

    This tries to import `QdrantConnector` from `cai.rag.vector_db` lazily so
    that environments without the connector won't fail import-time.
    """

    def __init__(self, client: Optional[Any] = None):
        self._client = client

    def _ensure_client(self):
        if self._client is None:
            try:
                # The project historically referenced `cai.rag.vector_db.QdrantConnector`.
                from cai.rag.vector_db import QdrantConnector  # type: ignore

                self._client = QdrantConnector()
            except Exception as exc:  # pragma: no cover - runtime environment dependent
                raise RuntimeError(
                    "QdrantConnector is not available; ensure the project's vector_db "
                    "module or qdrant client is installed and importable"
                ) from exc

    def search(self, collection_name: str, query_text: str, limit: int = 3):
        self._ensure_client()
        return self._client.search(collection_name=collection_name, query_text=query_text, limit=limit)

    def create_collection(self, collection_name: str) -> bool:
        self._ensure_client()
        return self._client.create_collection(collection_name)

    def add_points(self, id_point: Any, collection_name: str, texts: List[str], metadata: List[dict]) -> bool:
        self._ensure_client()
        return self._client.add_points(id_point=id_point, collection_name=collection_name, texts=texts, metadata=metadata)


class MemPalaceAdapter(VectorDBAdapter):
    """Adapter to query MemPalace.

    Notes:
    - This adapter focuses on *search* (read) so it is safe for A/B testing.
    - `add_points` is intentionally a no-op / not-implemented to avoid
      accidental migration of CAI's Qdrant data into MemPalace.
    - Two invocation methods are attempted in order: Python API import,
      then CLI via the `mempalace` command.
    """

    def __init__(self, palace_path: Optional[str] = None):
        self.palace_path = palace_path or os.getenv("CAI_MEMPALACE_PATH", "~/.mempalace/palace")

    def search(self, collection_name: str, query_text: str, limit: int = 3):
        # Try Python API first (if mempalace package is installed)
        try:
            from mempalace.searcher import search_memories  # type: ignore

            # Many mempalace APIs accept palace_path/palace args; adapt if needed.
            try:
                return search_memories(query_text, palace_path=self.palace_path, top_k=limit)  # type: ignore
            except TypeError:
                # fallback if different signature
                return search_memories(query_text, palace_path=self.palace_path)  # type: ignore
        except Exception:  # pragma: no cover - external dependency
            # Fallback to calling the `mempalace` CLI. Return raw CLI output.
            cmd = ["mempalace", "search", query_text, "--palace", self.palace_path]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return proc.stdout.strip()
            except Exception as exc:  # pragma: no cover - runtime dependent
                raise RuntimeError("Failed to query MemPalace: ensure mempalace is installed") from exc

    def create_collection(self, collection_name: str) -> bool:
        # Not applicable for MemPalace (file/closet based) — treat as no-op
        return True

    def add_points(self, id_point: Any, collection_name: str, texts: List[str], metadata: List[dict]) -> bool:
        # Intentionally not implemented to avoid accidental writes; use mempalace CLI or API
        raise NotImplementedError("MemPalaceAdapter.add_points is not implemented. Use mempalace CLI/API for ingestion.")
