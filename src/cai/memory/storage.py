"""Workspace-aware storage backend for memory evidence.

The default implementation persists JSONL records inside the active workspace.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Iterable, Protocol
import uuid

from pydantic import BaseModel, Field, ValidationError

from cai.memory.logic import clean_data


class EvidenceRecord(BaseModel):
    """Strict schema for stored technical memory events."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    topic: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    source: str = Field(default="agent")
    tags: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)


class StorageBackend(Protocol):
    """Protocol for memory persistence engines."""

    def initialize(self) -> Path:
        """Create and return storage directory."""
        ...

    def append(self, event: EvidenceRecord | dict[str, Any]) -> EvidenceRecord:
        """Persist one event and return validated record."""
        ...

    def load_all(self) -> list[EvidenceRecord]:
        """Load all persisted events."""
        ...


class WorkspaceJSONStore:
    """JSONL memory store rooted in the active workspace."""

    def __init__(self, relative_dir: str = ".cai/memory", file_name: str = "evidence.jsonl") -> None:
        self.relative_dir = Path(relative_dir)
        self.file_name = file_name
        self.storage_root = self._resolve_workspace_root() / self.relative_dir
        self.file_path = self.storage_root / self.file_name

    def _resolve_workspace_root(self) -> Path:
        try:
            from cai.tools.workspace import get_project_space

            return get_project_space().ensure_initialized().resolve()
        except Exception:
            # Safe fallback for environments where workspace bootstrap is not ready.
            return Path.cwd().resolve()

    def initialize(self) -> Path:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self.file_path.touch()
        return self.storage_root

    def append(self, event: EvidenceRecord | dict[str, Any]) -> EvidenceRecord:
        self.initialize()

        if isinstance(event, EvidenceRecord):
            record = event
        else:
            try:
                record = EvidenceRecord.model_validate(clean_data(event))
            except ValidationError as exc:
                raise ValueError(f"Invalid memory event payload: {exc}") from exc

        payload = clean_data(record.model_dump(mode="json"))
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

        return EvidenceRecord.model_validate(payload)

    def load_all(self) -> list[EvidenceRecord]:
        self.initialize()
        records: list[EvidenceRecord] = []

        with self.file_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    records.append(EvidenceRecord.model_validate(payload))
                except (json.JSONDecodeError, ValidationError):
                    # Skip malformed line; preserve robust recovery.
                    continue

        return records

    def iter_topic(self, topic: str) -> Iterable[EvidenceRecord]:
        needle = topic.strip().lower()
        for record in self.load_all():
            if needle in record.topic.lower():
                yield record
