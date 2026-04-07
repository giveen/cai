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

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = VectorDBConfig(**(config or {})) if config is not None else VectorDBConfig()

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


def get_vector_db_adapter(name: Optional[str] = None, **kwargs) -> VectorDBAdapter:
    """Factory to get an adapter by name or environment `CAI_VECTOR_DB`.

    Supported names: "qdrant" (default), "mempalace".
    """
    source = (name or os.getenv("CAI_VECTOR_DB", "qdrant")).lower()
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

    def __init__(self, client: Optional[Any] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
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

    def __init__(self, palace_path: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(config=config)
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
