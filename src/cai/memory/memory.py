"""Standalone memory module for agentic cybersecurity workflows.

This implementation is intentionally original and provides:
- Short-term volatile memory (deque)
- Long-term persistent memory (SQLite)
- Keyword-ranked retrieval
- Summarization for context grooming
- Redaction before persistence
- Workspace-aware storage paths
- Thread-safe writes/reads
"""

from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import uuid

from pydantic import BaseModel, Field, ConfigDict


_TOKEN_RE = re.compile(r"[a-z0-9_\-]{2,}", re.IGNORECASE)

# Redaction targets common secret formats and key-value disclosures.
_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(password|passwd|pwd|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(api[_-]?key|token|access[_-]?key|private[_-]?key)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{16,}\b"),
)


class MemoryEvent(BaseModel):
    """Validated memory event shape for both short-term and long-term stores."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    agent_id: str = Field(default="default")
    topic: str = Field(default="general", min_length=1)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    importance: int = Field(default=1, ge=1, le=5)


class ContextWindow(BaseModel):
    """Result payload returned by get_context()."""

    query: str
    total_matches: int = Field(ge=0)
    events: list[MemoryEvent] = Field(default_factory=list)


class MemoryManager:
    """Epistemic memory manager with volatile and persistent layers.

    API:
    - add_event(...)
    - get_context(...)
    - clear(...)
    - summarize(...)
    """

    def __init__(
        self,
        *,
        short_term_size: int = 120,
        db_relative_path: str = ".cai/memory/memory.db",
    ) -> None:
        self._short_term: deque[MemoryEvent] = deque(maxlen=max(1, short_term_size))
        self._lock = threading.RLock()

        self._workspace_root = self._resolve_workspace_root()
        self._db_path = self._workspace_root / db_relative_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure_db()

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def database_path(self) -> Path:
        return self._db_path

    def _resolve_workspace_root(self) -> Path:
        try:
            from cai.tools.workspace import get_project_space

            return get_project_space().ensure_initialized().resolve()
        except Exception:
            return Path.cwd().resolve()

    def _configure_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    importance INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_topic ON memory_events(topic);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_created_at ON memory_events(created_at);"
            )
            self._conn.commit()

    @contextmanager
    def _transaction(self):
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cursor.close()

    def add_event(
        self,
        content: str,
        *,
        topic: str = "general",
        tags: list[str] | None = None,
        agent_id: str = "default",
        importance: int = 1,
        persist: bool = True,
    ) -> MemoryEvent:
        """Add one event to short-term memory and optionally persist to long-term storage."""
        event = MemoryEvent(
            agent_id=agent_id,
            topic=topic,
            content=self._clean(content),
            tags=[self._clean(tag) for tag in (tags or [])],
            importance=importance,
        )

        with self._lock:
            self._short_term.append(event)

        if persist:
            with self._transaction() as cur:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO memory_events
                    (event_id, created_at, agent_id, topic, content, tags, importance)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.created_at.isoformat(),
                        event.agent_id,
                        event.topic,
                        event.content,
                        "|".join(event.tags),
                        event.importance,
                    ),
                )

        return event

    def get_context(
        self,
        query: str,
        *,
        limit: int = 12,
        include_short_term: bool = True,
        include_long_term: bool = True,
    ) -> ContextWindow:
        """Return ranked relevant context for a query.

        Ranking strategy:
        - topic token overlap has strongest weight
        - tags have medium weight
        - content token overlap contributes broad relevance
        - importance adds slight tie-breaking
        """
        ranked: dict[str, tuple[float, MemoryEvent]] = {}
        q_tokens = self._tokenize(query)

        if include_short_term:
            with self._lock:
                for event in list(self._short_term):
                    score = self._rank_event(event, q_tokens)
                    if score <= 0:
                        continue
                    ranked[event.event_id] = (score, event)

        if include_long_term:
            for event in self._fetch_all_events():
                score = self._rank_event(event, q_tokens)
                if score <= 0:
                    continue
                previous = ranked.get(event.event_id)
                if previous is None or score > previous[0]:
                    ranked[event.event_id] = (score, event)

        ordered = sorted(ranked.values(), key=lambda pair: pair[0], reverse=True)
        selected = [event for _, event in ordered[: max(1, limit)]]

        return ContextWindow(query=query, total_matches=len(ranked), events=selected)

    def clear(
        self,
        *,
        short_term: bool = True,
        long_term: bool = False,
        topic: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, int]:
        """Forget memory selectively from short-term and/or long-term layers."""
        short_cleared = 0
        long_cleared = 0

        if short_term:
            with self._lock:
                short_cleared = len(self._short_term)
                self._short_term.clear()

        if long_term:
            with self._transaction() as cur:
                where_parts: list[str] = []
                params: list[Any] = []

                if topic:
                    where_parts.append("topic = ?")
                    params.append(topic)
                if agent_id:
                    where_parts.append("agent_id = ?")
                    params.append(agent_id)

                sql = "DELETE FROM memory_events"
                if where_parts:
                    sql += " WHERE " + " AND ".join(where_parts)

                cur.execute(sql, params)
                long_cleared = cur.rowcount if cur.rowcount is not None else 0

        return {"short_term_cleared": short_cleared, "long_term_cleared": long_cleared}

    def summarize(
        self,
        *,
        max_events: int = 80,
        max_points: int = 8,
        include_short_term: bool = True,
        include_long_term: bool = True,
    ) -> str:
        """Compress memory into an executive technical summary string."""
        events: list[MemoryEvent] = []

        if include_long_term:
            events.extend(self._fetch_recent_events(limit=max_events))

        if include_short_term:
            with self._lock:
                events.extend(list(self._short_term))

        if not events:
            return "No memory events available."

        # Deduplicate by event_id and keep newest first.
        latest: dict[str, MemoryEvent] = {}
        for event in sorted(events, key=lambda e: e.created_at, reverse=True):
            latest[event.event_id] = event

        ordered = list(latest.values())[:max_events]
        topic_counts: Counter[str] = Counter(e.topic for e in ordered)

        key_points: list[str] = []
        for event in ordered:
            point = f"[{event.topic}] {event.content.strip()}"
            key_points.append(point)
            if len(key_points) >= max_points:
                break

        lines = [
            f"Events reviewed: {len(ordered)}",
            "Topic distribution: "
            + ", ".join(f"{topic}={count}" for topic, count in topic_counts.most_common(6)),
            "Key lessons learned:",
        ]
        lines.extend(f"- {self._clean(point)}" for point in key_points)

        return "\n".join(lines)

    def close(self) -> None:
        """Close persistent resources."""
        with self._lock:
            self._conn.close()

    def _fetch_all_events(self) -> list[MemoryEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, created_at, agent_id, topic, content, tags, importance FROM memory_events"
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _fetch_recent_events(self, *, limit: int) -> list[MemoryEvent]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_id, created_at, agent_id, topic, content, tags, importance
                FROM memory_events
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: sqlite3.Row) -> MemoryEvent:
        tags_raw = str(row["tags"]).strip()
        tags = [tag for tag in tags_raw.split("|") if tag] if tags_raw else []

        return MemoryEvent(
            event_id=str(row["event_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            agent_id=str(row["agent_id"]),
            topic=str(row["topic"]),
            content=str(row["content"]),
            tags=tags,
            importance=int(row["importance"]),
        )

    def _rank_event(self, event: MemoryEvent, query_tokens: set[str]) -> float:
        if not query_tokens:
            return float(event.importance)

        topic_tokens = self._tokenize(event.topic)
        content_tokens = self._tokenize(event.content)
        tag_tokens = self._tokenize(" ".join(event.tags))

        score = 0.0
        score += 3.0 * len(query_tokens & topic_tokens)
        score += 2.0 * len(query_tokens & tag_tokens)
        score += 1.0 * len(query_tokens & content_tokens)
        score += 0.2 * event.importance
        return score

    def _tokenize(self, text: str) -> set[str]:
        return {token.lower() for token in _TOKEN_RE.findall(text)}

    def _clean(self, text: str) -> str:
        masked = text
        for pattern in _REDACTION_PATTERNS:
            if pattern.pattern.startswith("\\bAKIA") or pattern.pattern.startswith("\\b(?:sk|rk)"):
                masked = pattern.sub("[REDACTED_SECRET]", masked)
                continue

            def _replace(match: re.Match[str]) -> str:
                key = match.group(1)
                return f"{key}=[REDACTED_SECRET]"

            masked = pattern.sub(_replace, masked)
        return masked


__all__ = ["ContextWindow", "MemoryEvent", "MemoryManager"]
