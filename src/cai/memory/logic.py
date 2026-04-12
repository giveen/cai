"""Memory processing logic for redaction and summarization.

This module contains pure functions that can be reused by storage and query
layers without coupling to any specific backend implementation.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import re
from typing import Any, Iterable

from pydantic import BaseModel, Field


_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(password|passwd|pwd|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(api[_-]?key|token|access[_-]?key|private[_-]?key)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{16,}\b"),
)


class MemorySummary(BaseModel):
    """Compact summary payload used for LLM context injection."""

    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    total_events: int = Field(ge=0)
    topics: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    text: str


def clean(value: str) -> str:
    """Mask probable secrets in free text.

    The redaction intentionally favors safety over precision and replaces
    suspicious values with stable placeholders.
    """
    masked = value

    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("\\bAKIA") or pattern.pattern.startswith("\\b(?:sk|rk)"):
            masked = pattern.sub("[REDACTED_SECRET]", masked)
            continue

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return f"{key}=[REDACTED_SECRET]"

        masked = pattern.sub(_replace, masked)

    return masked


def clean_data(payload: Any) -> Any:
    """Recursively redact strings in complex objects prior to persistence."""
    if isinstance(payload, str):
        return clean(payload)
    if isinstance(payload, list):
        return [clean_data(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(clean_data(item) for item in payload)
    if isinstance(payload, dict):
        return {str(key): clean_data(value) for key, value in payload.items()}
    return payload


def summarize_events(events: Iterable[dict[str, Any]], max_points: int = 8) -> MemorySummary:
    """Compress a sequence of events into a concise technical state update."""
    items = list(events)
    if not items:
        return MemorySummary(total_events=0, text="No memory events captured yet.")

    topic_counter: Counter[str] = Counter()
    key_points: list[str] = []

    for event in items:
        topic = str(event.get("topic", "general")).strip() or "general"
        topic_counter[topic] += 1

        finding = event.get("finding") or event.get("summary") or event.get("details")
        if isinstance(finding, str) and finding.strip():
            key_points.append(clean(finding.strip()))

        if len(key_points) >= max_points:
            break

    topics_ranked = [topic for topic, _ in topic_counter.most_common(6)]

    lines = [
        f"Events analyzed: {len(items)}",
        f"Top topics: {', '.join(topics_ranked) if topics_ranked else 'none'}",
    ]
    if key_points:
        lines.append("Key findings:")
        for point in key_points[:max_points]:
            lines.append(f"- {point}")

    return MemorySummary(
        total_events=len(items),
        topics=topics_ranked,
        key_points=key_points[:max_points],
        text="\n".join(lines),
    )
