"""Global accessor for a singleton TripleStore instance.

Provides a `get_global_triplestore()` factory so different parts of
the application can share a single TripleStore without tight coupling
or circular imports.
"""

from __future__ import annotations

import os

from cai.rag.triplestore import TripleStore

_GLOBAL_TRIPLESTORE: TripleStore | None = None


def _default_triplestore_path() -> str:
    return os.environ.get("CAI_TRIPLESTORE_PATH") or os.path.join(
        os.getcwd(), ".cai", "triplestore.db"
    )


def get_global_triplestore(
    db_path: str | None = None, pragmas: dict[str, str] | None = None
) -> TripleStore:
    """Return a singleton TripleStore, creating it on first use.

    If `db_path` is not provided the function will consult the
    `CAI_TRIPLESTORE_PATH` env var, falling back to `.cai/triplestore.db`.
    The directory will be created if necessary. If disk-backed store
    cannot be opened, a memory-backed TripleStore is used as a fallback.
    """
    global _GLOBAL_TRIPLESTORE
    if _GLOBAL_TRIPLESTORE is None:
        path = db_path or _default_triplestore_path()
        # ensure directory exists for on-disk DB
        dirpath = os.path.dirname(path)
        if dirpath and not os.path.exists(dirpath):
            try:
                os.makedirs(dirpath, exist_ok=True)
            except Exception:
                # best-effort: ignore failures and let TripleStore fall back
                pass
        try:
            _GLOBAL_TRIPLESTORE = TripleStore(db_path=path, pragmas=pragmas)
        except Exception:
            _GLOBAL_TRIPLESTORE = TripleStore()
    return _GLOBAL_TRIPLESTORE


def set_global_triplestore(ts: TripleStore) -> None:
    """Replace the global TripleStore (useful for tests)."""
    global _GLOBAL_TRIPLESTORE
    _GLOBAL_TRIPLESTORE = ts


__all__ = ["get_global_triplestore", "set_global_triplestore"]
