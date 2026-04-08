"""Text chunking and fingerprinting helpers for RAG ingestion.

Provides deterministic chunking (size + overlap) and stable
fingerprinting combining content hash and an embedding fingerprint.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any, Dict, List, Optional


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[Dict[str, Any]]:
    """Deterministically chunk ``text`` into pieces.

    Returns a list of dicts: {"text": str, "start": int, "end": int, "index": int}
    """
    if text is None:
        return []
    txt = str(text).strip()
    if not txt:
        return []
    try:
        chunk_size = int(chunk_size)
        overlap = int(overlap)
    except Exception:
        chunk_size = 1000
        overlap = 200
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    step = max(1, chunk_size - max(0, overlap))

    out: List[Dict[str, Any]] = []
    start = 0
    idx = 0
    L = len(txt)
    while start < L:
        end = start + chunk_size
        chunk = txt[start:end]
        out.append({"text": chunk, "start": start, "end": min(end, L), "index": idx})
        idx += 1
        start += step
    return out


def _embed_fingerprint_from_vec(vec: List[float]) -> Optional[str]:
    """Deterministically compute a hex fingerprint from an embedding vector.

    Packs floats as big-endian doubles and hashes the byte sequence.
    Returns hex digest or None on failure.
    """
    if vec is None:
        return None
    try:
        b = b"".join(struct.pack(">d", float(v)) for v in vec)
        return hashlib.sha256(b).hexdigest()
    except Exception:
        # Fallback: hash the repr string
        try:
            return hashlib.sha256(repr(vec).encode("utf-8")).hexdigest()
        except Exception:
            return None


def fingerprint_chunks(
    chunks: List[Dict[str, Any]],
    embeddings: Optional[List[List[float]]] = None,
) -> List[Dict[str, Any]]:
    """Given chunk dicts and optional embeddings, compute fingerprints.

    Each returned dict augments the chunk with keys:
      - content_hash: sha256 hex of text
      - embed_fingerprint: sha256 hex of embedding bytes (if embeddings provided)
      - fingerprint: combined "content:embed" when both present, else content_hash
      - chunk_id: a stable id derived from content_hash + index
    """
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(chunks):
        txt = c.get("text", "")
        try:
            content_hash = hashlib.sha256(txt.encode("utf-8")).hexdigest()
        except Exception:
            content_hash = None

        embed_fp = None
        if embeddings is not None and i < len(embeddings):
            embed_fp = _embed_fingerprint_from_vec(embeddings[i])

        if content_hash and embed_fp:
            fingerprint = f"{content_hash}:{embed_fp}"
        else:
            fingerprint = content_hash

        chunk_id = f"{content_hash}-{i}" if content_hash else f"chunk-{i}"

        new = dict(c)
        new.update(
            {
                "content_hash": content_hash,
                "embed_fingerprint": embed_fp,
                "fingerprint": fingerprint,
                "chunk_id": chunk_id,
            }
        )
        out.append(new)
    return out


__all__ = ["chunk_text", "fingerprint_chunks"]
