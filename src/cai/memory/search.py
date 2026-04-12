"""Memory retrieval and ranking helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import re

from cai.memory.storage import EvidenceRecord


_TOKEN_RE = re.compile(r"[a-z0-9_\-]{2,}", flags=re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


@dataclass(slots=True)
class SearchHit:
    """Ranked result container for memory queries."""

    score: float
    record: EvidenceRecord


class MemorySearch:
    """Lightweight keyword ranking over persisted memory records."""

    def query(self, topic: str, records: Iterable[EvidenceRecord], limit: int = 10) -> list[SearchHit]:
        prompt_tokens = _tokenize(topic)
        if not prompt_tokens:
            return []

        ranked: list[SearchHit] = []
        for record in records:
            score = self._score_record(record, prompt_tokens)
            if score <= 0:
                continue
            ranked.append(SearchHit(score=score, record=record))

        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[: max(limit, 1)]

    def _score_record(self, record: EvidenceRecord, prompt_tokens: set[str]) -> float:
        topic_tokens = _tokenize(record.topic)
        finding_tokens = _tokenize(record.finding)
        tags_tokens = _tokenize(" ".join(record.tags))
        artifact_tokens = _tokenize(str(record.artifacts))

        score = 0.0
        score += 3.0 * len(prompt_tokens & topic_tokens)
        score += 2.0 * len(prompt_tokens & tags_tokens)
        score += 1.5 * len(prompt_tokens & finding_tokens)
        score += 0.5 * len(prompt_tokens & artifact_tokens)

        return score
