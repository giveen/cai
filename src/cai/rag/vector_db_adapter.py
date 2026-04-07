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
import time
import hashlib
import datetime as _dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type


@dataclass
class VectorDBConfig:
    """Lightweight configuration holder for vector DB adapters.

    Backends may read provider-specific options from `options`.
    """

    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    options: Dict[str, Any] = field(default_factory=dict)


def with_retries(retries: int = 3, base_delay: float = 0.2, backoff: float = 2.0):
    """Simple retry decorator with exponential backoff.

    Keep this intentionally dependency-free to avoid adding runtime
    requirements for the core adapter layer.
    """

    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_exc = None
            delay = base_delay
            for attempt in range(retries):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # pragma: no cover - network/runtime dependent
                    last_exc = exc
                    if attempt < retries - 1:
                        time.sleep(delay)
                        delay *= backoff
            # If we get here, raise the last exception
            raise last_exc

        return wrapper

    return decorator


class VectorDBAdapter(ABC):
    """Abstract Vector DB adapter.

    Implementations should be lightweight shims around concrete
    vector-database SDKs. Methods should return stable, serializable
    structures where practical (e.g. list-of-dicts for `search`).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, embeddings_provider: Optional[Any] = None):
        self.config = VectorDBConfig(**(config or {})) if config is not None else VectorDBConfig()
        # Optional embeddings provider instance. If not provided, a
        # provider will be lazily created by `embed_texts()` using the
        # `get_embeddings_provider` factory from `cai.rag.embeddings`.
        self.embeddings_provider = embeddings_provider

    @abstractmethod
    def search(self, collection_name: str, query_text: str, limit: int = 3) -> Any:
        raise NotImplementedError()

    @abstractmethod
    def create_collection(self, collection_name: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def add_points(self, id_point: Any, collection_name: str, texts: List[str], metadata: List[dict]) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Return a health dictionary like {'ok': bool, 'details': str|dict}.

        Implementations should try to be non-destructive and fast.
        """
        raise NotImplementedError()

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Return embeddings for the provided texts using the configured
        embeddings provider. If no provider was supplied at construction
        time, a default provider is created lazily.
        """
        if self.embeddings_provider is None:
            try:
                # Lazy import to avoid import-time cycles
                from cai.rag.embeddings import get_embeddings_provider  # type: ignore

                self.embeddings_provider = get_embeddings_provider()
            except Exception:
                # Fall back to a trivial deterministic provider if factory fails
                from cai.rag.embeddings import LocalDeterministicEmbeddingsProvider  # type: ignore

                self.embeddings_provider = LocalDeterministicEmbeddingsProvider()

        return self.embeddings_provider.embed_texts(texts)


def get_vector_db_adapter(name: Optional[str] = None, **kwargs) -> VectorDBAdapter:
    """Factory to get an adapter by name or environment `CAI_VECTOR_DB`.

    Supported names: "qdrant" (default), "mempalace".
    """
    source = (name or os.getenv("CAI_VECTOR_DB", "qdrant")).lower()
    # If the caller didn't provide an embeddings provider instance, create
    # one via the centralized factory so all adapters share the same
    # embeddings configuration by default.
    if "embeddings_provider" not in kwargs:
        try:
            from cai.rag.embeddings import get_embeddings_provider  # type: ignore

            kwargs["embeddings_provider"] = get_embeddings_provider()
        except Exception:
            # Non-fatal: fall back to adapters creating providers lazily.
            pass
    # Prefer registry if backends have been registered
    try:
        if source in _BACKEND_REGISTRY:  # type: ignore[name-defined]
            return _BACKEND_REGISTRY[source](**kwargs)  # type: ignore[name-defined]
    except NameError:
        # Registry not yet defined (older modules importing early) — fall back
        pass

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

    def __init__(self, client: Optional[Any] = None, config: Optional[Dict[str, Any]] = None, embeddings_provider: Optional[Any] = None):
        super().__init__(config=config, embeddings_provider=embeddings_provider)
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

    @with_retries(retries=3)
    def search(self, collection_name: str, query_text: str, limit: int = 3):
        self._ensure_client()
        return self._client.search(collection_name=collection_name, query_text=query_text, limit=limit)

    @with_retries(retries=2)
    def create_collection(self, collection_name: str) -> bool:
        self._ensure_client()
        return self._client.create_collection(collection_name)

    @with_retries(retries=2)
    def add_points(self, id_point: Any, collection_name: str, texts: List[str], metadata: List[dict]) -> bool:
        self._ensure_client()
        # Attempt to compute embeddings and pass them to the client if the
        # client's `add_points` supports an explicit `vectors` argument.
        vectors = None
        try:
            vectors = self.embed_texts(texts)
        except Exception:
            vectors = None

        if vectors is not None:
            try:
                return self._client.add_points(
                    id_point=id_point, collection_name=collection_name, texts=texts, metadata=metadata, vectors=vectors
                )
            except TypeError:
                # Client does not accept `vectors`; fall back to original call
                pass

        return self._client.add_points(id_point=id_point, collection_name=collection_name, texts=texts, metadata=metadata)

    def health_check(self) -> Dict[str, Any]:
        """Lightweight health check for Qdrant connector.

        Attempts to ensure the client can be instantiated, then looks for
        common health/ping methods. Falls back to an HTTP collection list
        check if `QDRANT_URL` or `CAI_QDRANT_URL` is provided.
        """
        try:
            self._ensure_client()
        except Exception as exc:  # pragma: no cover - environment dependent
            return {"ok": False, "error": str(exc)}

        client = self._client
        # Prefer explicit health_check
        if hasattr(client, "health_check") and callable(getattr(client, "health_check")):
            try:
                res = client.health_check()
                return {"ok": True, "details": res}
            except Exception as exc:  # pragma: no cover - environment dependent
                return {"ok": False, "error": str(exc)}

        # Try ping
        if hasattr(client, "ping") and callable(getattr(client, "ping")):
            try:
                res = client.ping()
                return {"ok": True, "details": getattr(res, "__dict__", str(res))}
            except Exception as exc:  # pragma: no cover - environment dependent
                return {"ok": False, "error": str(exc)}

        # Last resort: check HTTP endpoint if provided
        qdrant_url = os.getenv("QDRANT_URL") or os.getenv("CAI_QDRANT_URL")
        if qdrant_url:
            try:
                import urllib.request

                url = qdrant_url.rstrip("/") + "/collections"
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = resp.read().decode("utf-8")
                    return {"ok": True, "details": data[:1024]}
            except Exception as exc:  # pragma: no cover - environment dependent
                return {"ok": False, "error": f"HTTP check failed: {exc}"}

        return {"ok": True, "details": "client instantiated (no explicit health endpoint available)"}


class MemPalaceAdapter(VectorDBAdapter):
    """Adapter to query MemPalace.

    Notes:
    - This adapter focuses on *search* (read) so it is safe for A/B testing.
    - `add_points` is intentionally a no-op / not-implemented to avoid
      accidental migration of CAI's Qdrant data into MemPalace.
    - Two invocation methods are attempted in order: Python API import,
      then CLI via the `mempalace` command.
    """

    def __init__(self, palace_path: Optional[str] = None, config: Optional[Dict[str, Any]] = None, embeddings_provider: Optional[Any] = None):
        super().__init__(config=config, embeddings_provider=embeddings_provider)
        # Allow palace_path to be provided via explicit arg, config options, or env
        self.palace_path = (
            palace_path
            or self.config.options.get("palace_path")
            or os.getenv("CAI_MEMPALACE_PATH", "~/.mempalace/palace")
        )

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

    def health_check(self) -> Dict[str, Any]:
        # Check palace path existence first
        try:
            path = os.path.expanduser(self.palace_path)
            if os.path.exists(path):
                return {"ok": True, "details": f"palace_path exists: {path}"}
        except Exception:
            pass

        # Try CLI
        try:
            proc = subprocess.run(["mempalace", "--version"], capture_output=True, text=True, timeout=3)
            if proc.returncode == 0:
                return {"ok": True, "details": proc.stdout.strip()}
        except Exception:
            pass

        # Try python import
        try:
            import mempalace  # type: ignore

            version = getattr(mempalace, "__version__", "unknown")
            return {"ok": True, "details": f"mempalace python package {version}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# Backend registry so new adapters can be registered at runtime
_BACKEND_REGISTRY: Dict[str, Type[VectorDBAdapter]] = {}


def register_vector_db_backend(name: str, cls: Type[VectorDBAdapter]) -> None:
    """Register a backend adapter class under a short name."""
    _BACKEND_REGISTRY[name.lower()] = cls


# Register built-in adapters
register_vector_db_backend("qdrant", QdrantAdapter)
register_vector_db_backend("q", QdrantAdapter)
register_vector_db_backend("mempalace", MemPalaceAdapter)
register_vector_db_backend("palace", MemPalaceAdapter)
register_vector_db_backend("mp", MemPalaceAdapter)


def list_registered_backends() -> List[str]:
    """Return names of currently registered vector DB backends."""
    return list(_BACKEND_REGISTRY.keys())


class LocalFallbackAdapter(VectorDBAdapter):
    """Lightweight in-memory vector store with optional FAISS acceleration.

    This adapter is intended for local development and testing. It stores
    vectors, texts, and metadata in-process and performs a linear scan
    search when FAISS is not available. If `use_faiss` is set in the
    adapter config options and `faiss` + `numpy` are installed, searches
    will use FAISS for speed.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, embeddings_provider: Optional[Any] = None):
        super().__init__(config=config, embeddings_provider=embeddings_provider)
        opts = self.config.options or {}
        env_use = os.getenv("CAI_USE_FAISS", "").strip()
        self.use_faiss = bool(opts.get("use_faiss") or env_use in ("1", "true", "True"))
        self._faiss_available = False
        self._faiss = None
        self._np = None
        if self.use_faiss:
            try:
                import faiss  # type: ignore
                import numpy as np  # type: ignore

                self._faiss = faiss
                self._np = np
                self._faiss_available = True
            except Exception:
                self._faiss_available = False

        # collections: name -> {'ids':[], 'texts':[], 'metadata':[], 'vectors':[]}
        self._collections: Dict[str, Dict[str, List[Any]]] = {}

    def create_collection(self, collection_name: str) -> bool:
        if collection_name in self._collections:
            return True
        self._collections[collection_name] = {"ids": [], "texts": [], "metadata": [], "vectors": []}
        return True

    def add_points(self, id_point: Any, collection_name: str, texts: List[str], metadata: List[dict]) -> bool:
        # Ensure collection
        self.create_collection(collection_name)
        col = self._collections[collection_name]

        # Normalize ids and metadata lists to match texts length
        if isinstance(id_point, (list, tuple)) and len(id_point) == len(texts):
            ids = list(id_point)
        else:
            if len(texts) == 1:
                ids = [id_point]
            else:
                # generate per-item ids when a single id is provided for multiple texts
                import uuid

                base = id_point or ""
                ids = [f"{base}-{i}" if base else str(uuid.uuid4()) for i in range(len(texts))]

        if metadata is None:
            metadata = [{} for _ in texts]
        elif isinstance(metadata, (list, tuple)) and len(metadata) == len(texts):
            metadata_list = list(metadata)
        elif isinstance(metadata, dict):
            metadata_list = [metadata for _ in texts]
        else:
            # Fallback: try to coerce
            metadata_list = [m if isinstance(m, dict) else {} for m in (metadata if isinstance(metadata, list) else [metadata])]
            if len(metadata_list) < len(texts):
                metadata_list = metadata_list * (len(texts) // len(metadata_list) + 1)
            metadata_list = metadata_list[: len(texts)]

        # If metadata_list variable not defined above
        try:
            metadata_list
        except NameError:
            metadata_list = [{} for _ in texts]

        # Ensure provenance metadata exists for each text (best-effort)
        try:
            session = os.getenv("CAI_SESSION_ID") or os.getenv("SESSION_ID")
        except Exception:
            session = None

        for i, t in enumerate(texts):
            try:
                md = metadata_list[i] if i < len(metadata_list) else {}
            except Exception:
                md = {}
            if not isinstance(md, dict):
                md = {}
            if "provenance" not in md:
                try:
                    ch = hashlib.sha256((t or "").encode("utf-8")).hexdigest()
                except Exception:
                    ch = None
                prov = {
                    "source": __name__,
                    "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
                    "session_id": session,
                    "tool_name": "local_add_points",
                    "original_text": t,
                    "chunk_id": ids[i] if i < len(ids) else str(uuid.uuid4()),
                    "content_hash": ch,
                }
                md["provenance"] = prov
            # ensure metadata_list updated
            if i < len(metadata_list):
                metadata_list[i] = md
            else:
                metadata_list.append(md)

        # Compute embeddings (best-effort)
        try:
            vectors = self.embed_texts(texts)
        except Exception:
            vectors = [None for _ in texts]

        # Append to collection
        for i, t in enumerate(texts):
            col["ids"].append(ids[i])
            col["texts"].append(t)
            col["metadata"].append(metadata_list[i])
            col["vectors"].append(vectors[i] if vectors and i < len(vectors) else None)

        return True

    def export_collection(self, collection_name: str) -> List[Dict[str, Any]]:
        """Return a list of documents for the collection with optional vectors.

        Each document is a dict: {id, text, metadata, vector}
        """
        if collection_name not in self._collections:
            return []
        col = self._collections[collection_name]
        out: List[Dict[str, Any]] = []
        for i in range(len(col["ids"])):
            out.append({
                "id": col["ids"][i],
                "text": col["texts"][i],
                "metadata": col["metadata"][i],
                "vector": col["vectors"][i],
            })
        return out

    def search(self, collection_name: str, query_text: str, limit: int = 3):
        if collection_name not in self._collections:
            return []
        col = self._collections[collection_name]
        if not col["texts"]:
            return []

        try:
            qvec = self.embed_texts([query_text])[0]
        except Exception:
            return []

        # Filter out vectors that are None or the wrong dimension
        vectors = col["vectors"]
        valid = [i for i, v in enumerate(vectors) if v is not None and len(v) == len(qvec)]
        if not valid:
            return []

        # FAISS path
        if self._faiss_available:
            try:
                arr = self._np.array([vectors[i] for i in valid], dtype="float32")
                index = self._faiss.IndexFlatIP(arr.shape[1])
                index.add(arr)
                qarr = self._np.array([qvec], dtype="float32")
                k = min(limit, arr.shape[0])
                D, I = index.search(qarr, k)
                results = []
                for score, idx in zip(D[0], I[0]):
                    orig_i = valid[int(idx)]
                    results.append({
                        "id": col["ids"][orig_i],
                        "text": col["texts"][orig_i],
                        "metadata": col["metadata"][orig_i],
                        "score": float(score),
                    })
                return results
            except Exception:
                # Fall back to naive
                pass

        # Naive linear scan (dot product)
        scores = []
        for i in valid:
            v = vectors[i]
            score = 0.0
            try:
                score = sum(float(a) * float(b) for a, b in zip(qvec, v))
            except Exception:
                score = 0.0
            scores.append((score, i))

        scores.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, i in scores[:limit]:
            results.append({
                "id": col["ids"][i],
                "text": col["texts"][i],
                "metadata": col["metadata"][i],
                "score": float(score),
            })
        return results

    def health_check(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "details": {
                "type": "local-fallback",
                "faiss_available": bool(self._faiss_available),
                "collections": list(self._collections.keys()),
            },
        }


# Register local fallback adapter aliases
register_vector_db_backend("local", LocalFallbackAdapter)
register_vector_db_backend("faiss", LocalFallbackAdapter)
register_vector_db_backend("inmemory", LocalFallbackAdapter)
