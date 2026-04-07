"""Wake-up short-memory index for session-critical facts.

Provides a small in-memory index for session-scoped critical facts that
should be prioritized ahead of longer-term retrieval results. Facts are
stored with optional TTLs and priority scores; searches return the most
relevant wake-up facts for a given session.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, List, Optional

from cai.rag.embeddings import (
    get_embeddings_provider,
    LocalDeterministicEmbeddingsProvider,
)


class WakeupIndex:
    def __init__(
        self,
        max_facts_per_session: int = 200,
        embeddings_provider: Optional[Any] = None,
        use_faiss: Optional[bool] = None,
    ):
        self.max_facts_per_session = int(max_facts_per_session)
        self.embeddings_provider = embeddings_provider
        env_use = os.getenv("CAI_USE_FAISS", "").lower()
        self.use_faiss = bool(use_faiss) or env_use in ("1", "true", "yes")
        # session_id -> { key -> entry }
        self._sessions: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _now(self) -> float:
        return time.time()

    def _ensure_provider(self):
        if self.embeddings_provider is None:
            try:
                self.embeddings_provider = get_embeddings_provider()
            except Exception:
                # Fallback to deterministic provider for local dev/tests
                self.embeddings_provider = LocalDeterministicEmbeddingsProvider()

    def _purge_expired(self, session_id: str) -> None:
        now = self._now()
        entries = self._sessions.get(session_id)
        if not entries:
            return
        to_delete = [k for k, v in entries.items() if v.get("expires_at") and v["expires_at"] <= now]
        for k in to_delete:
            del entries[k]

    def add_fact(
        self,
        session_id: str,
        key: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl: Optional[float] = None,
        priority: float = 0.0,
    ) -> bool:
        """Add or replace a fact for a session.

        Args:
            session_id: session identifier
            key: unique key for the fact within the session
            text: fact text
            metadata: optional metadata dict
            ttl: optional time-to-live in seconds
            priority: numeric priority (higher == more important)
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not key:
            raise ValueError("key is required")

        self._ensure_provider()
        entries = self._sessions.setdefault(session_id, {})
        # purge expired before counting
        self._purge_expired(session_id)

        # compute vector (best-effort)
        vector = None
        try:
            vector = self.embeddings_provider.embed_texts([text])[0]
        except Exception:
            vector = None

        now = self._now()
        expires_at = (now + float(ttl)) if ttl is not None else None

        entry = {
            "key": key,
            "text": text,
            "metadata": metadata or {},
            "vector": vector,
            "priority": float(priority),
            "created_at": now,
            "expires_at": expires_at,
        }

        # enforce size limits: evict lowest priority then oldest
        if key not in entries and len(entries) + 1 > self.max_facts_per_session:
            # choose victim
            victim = min(entries.values(), key=lambda e: (e.get("priority", 0.0), e.get("created_at", 0.0)))
            victim_key = victim.get("key")
            if victim_key in entries:
                del entries[victim_key]

        entries[key] = entry
        return True

    def search_facts(self, session_id: str, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Return top-k wake-up facts for `session_id` most relevant to `query`.

        The ranking combines vector similarity (if available) and the
        configured `priority` field.
        """
        entries = self._sessions.get(session_id)
        if not entries:
            return []

        # purge expired entries
        self._purge_expired(session_id)
        if not entries:
            return []

        self._ensure_provider()
        # compute query vector
        qvec = None
        try:
            qvec = self.embeddings_provider.embed_texts([query])[0]
        except Exception:
            qvec = None

        def cos_sim(a, b):
            denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
            if denom == 0:
                return 0.0
            return sum(float(x) * float(y) for x, y in zip(a, b)) / denom

        scored = []
        for entry in entries.values():
            vec = entry.get("vector")
            sim = 0.0
            if vec is not None and qvec is not None and len(vec) == len(qvec):
                try:
                    sim = float(cos_sim(qvec, vec))
                except Exception:
                    sim = 0.0
            else:
                # simple token overlap fallback
                try:
                    qtokens = set((query or "").lower().split())
                    etokens = set((entry.get("text") or "").lower().split())
                    if qtokens:
                        sim = float(len(qtokens & etokens)) / float(len(qtokens))
                    else:
                        sim = 0.0
                except Exception:
                    sim = 0.0

            # combine with priority (small weight)
            score = sim + float(entry.get("priority", 0.0)) * 0.1
            scored.append((score, entry))

        scored.sort(key=lambda s: s[0], reverse=True)
        out = []
        for score, entry in scored[:top_k]:
            out.append({
                "key": entry.get("key"),
                "text": entry.get("text"),
                "metadata": entry.get("metadata"),
                "score": float(score),
                "priority": float(entry.get("priority", 0.0)),
                "expires_at": entry.get("expires_at"),
            })
        return out

    def purge_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
        return True

    def list_sessions(self) -> List[str]:
        return list(self._sessions.keys())


__all__ = ["WakeupIndex"]
