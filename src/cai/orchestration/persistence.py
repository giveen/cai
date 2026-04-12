"""Persistence helpers for a simple State Journal (intelligence.json).

Provides atomic read/write helpers and a function-tool `sync_to_journal`
that agents can call to append structured facts to the workspace journal.

The journal lives at `$CAI_WORKSPACE_DIR/intelligence.json` (or derived
workspace path from cai.tools.common._get_workspace_dir()). A human
readable `README_ENGAGEMENT.md` is also (re)generated on each commit.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cai.sdk.agents import function_tool
from cai.sdk.agents.run_context import RunContextWrapper

try:
    # Prefer the workspace resolution helper used across tools
    from cai.tools.common import _get_workspace_dir
except Exception:  # pragma: no cover - fallback
    def _get_workspace_dir() -> str:  # type: ignore
        return os.getcwd()

JOURNAL_NAME = "intelligence.json"
README_NAME = "README_ENGAGEMENT.md"


def _get_journal_path(workspace_dir: Optional[str] = None) -> Path:
    if workspace_dir:
        base = Path(workspace_dir)
    else:
        base = Path(_get_workspace_dir())
    base.mkdir(parents=True, exist_ok=True)
    return base / JOURNAL_NAME


def _get_readme_path(workspace_dir: Optional[str] = None) -> Path:
    if workspace_dir:
        base = Path(workspace_dir)
    else:
        base = Path(_get_workspace_dir())
    base.mkdir(parents=True, exist_ok=True)
    return base / README_NAME


def _read_journal(workspace_dir: Optional[str] = None) -> dict:
    p = _get_journal_path(workspace_dir)
    if not p.exists():
        return {"meta": {"version": 1}, "entries": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {"meta": {"version": 1}, "entries": []}
    except Exception:
        # If the journal is corrupt, return a minimal structure so we don't crash tools.
        return {"meta": {"version": 1}, "entries": []}


def read_journal(workspace_dir: Optional[str] = None) -> dict:
    """Public wrapper to read the intelligence journal.

    Returns the journal dict with keys `meta` and `entries`.
    """
    return _read_journal(workspace_dir)


def is_fact_anchored(search_text: str, category_candidates: Optional[list[str]] = None, workspace_dir: Optional[str] = None) -> bool:
    """Return True if a journal entry matching search_text exists with confidence==1.0.

    Matching is a best-effort substring match against the serialized `fact`.
    """
    try:
        j = _read_journal(workspace_dir)
        entries = j.get("entries", []) or []
        needle = str(search_text)
        for e in entries:
            try:
                conf = float(e.get("confidence_score", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            if conf < 1.0:
                continue
            cat = (e.get("category") or "").lower()
            if category_candidates and cat not in [c.lower() for c in category_candidates]:
                continue
            fact = e.get("fact")
            try:
                fact_text = json.dumps(fact, ensure_ascii=False) if not isinstance(fact, str) else fact
            except Exception:
                fact_text = str(fact)
            if needle in fact_text:
                return True
        return False
    except Exception:
        return False


def clear_anchor(entry_id: Optional[str] = None, search_text: Optional[str] = None, workspace_dir: Optional[str] = None) -> bool:
    """Clear anchored status for matching journal entries.

    If `entry_id` is provided, clear that entry. Otherwise, if
    `search_text` is provided, clear any anchored entries whose serialized
    fact contains the search_text. Returns True if any change was made.
    """
    try:
        j = _read_journal(workspace_dir)
        entries = j.get("entries", []) or []
        changed = False
        for e in entries:
            if entry_id and e.get("id") == entry_id:
                e["confidence_score"] = 0.0
                changed = True
                continue
            if search_text:
                try:
                    fact_text = json.dumps(e.get("fact"), ensure_ascii=False) if not isinstance(e.get("fact"), str) else e.get("fact")
                except Exception:
                    fact_text = str(e.get("fact"))
                if search_text in fact_text:
                    e["confidence_score"] = 0.0
                    changed = True

        if not changed:
            return False

        j.setdefault("meta", {})["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _write_journal_atomic(j, workspace_dir)
        try:
            _render_readme(j, workspace_dir)
        except Exception:
            pass
        return True
    except Exception:
        return False


def clear_all_anchors(workspace_dir: Optional[str] = None) -> bool:
    """Clear anchored status (confidence_score) for all journal entries.

    Returns True if any anchors were cleared.
    """
    try:
        j = _read_journal(workspace_dir)
        entries = j.get("entries", []) or []
        changed = False
        for e in entries:
            try:
                conf = float(e.get("confidence_score", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            if conf >= 1.0:
                e["confidence_score"] = 0.0
                changed = True

        if not changed:
            return False

        j.setdefault("meta", {})["updated_at"] = datetime.utcnow().isoformat() + "Z"
        _write_journal_atomic(j, workspace_dir)
        try:
            _render_readme(j, workspace_dir)
        except Exception:
            pass
        return True
    except Exception:
        return False


def summarize_journal(workspace_dir: Optional[str] = None, max_entries: int = 6) -> str:
    """Produce a short human-readable summary of the journal suitable for the TUI.

    The summary includes total entries, per-category counts, last-updated timestamp,
    and a short list of recent facts.
    """
    j = _read_journal(workspace_dir)
    entries = j.get("entries", []) or []
    total = len(entries)
    meta = j.get("meta", {}) or {}
    updated = meta.get("updated_at") or (entries[-1]["timestamp"] if entries else "—")

    # Count by category
    counts: dict[str, int] = {}
    for e in entries:
        c = e.get("category") or "unknown"
        counts[c] = counts.get(c, 0) + 1

    lines = ["[bold #00ff00]Target Summary[/bold #00ff00]", ""]
    lines.append(f"  Entries: {total}")
    lines.append(f"  Updated: {updated}")
    if counts:
        cat_line = ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
        lines.append(f"  Categories: {cat_line}")
    lines.append("")

    # Recent facts
    recent = entries[-max_entries:] if entries else []
    for e in reversed(recent):
        ts = e.get("timestamp", "?")
        cat = e.get("category", "?")
        fact = e.get("fact", "")
        try:
            fact_str = json.dumps(fact, ensure_ascii=False)
        except Exception:
            fact_str = str(fact)
        fact_short = _shorten(fact_str, limit=120)
        try:
            conf = float(e.get("confidence_score", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        lock = " 🔒" if conf >= 1.0 else ""
        lines.append(f"- [{ts}] ({cat}){lock} {fact_short}")

    if not recent:
        lines.append("(no discoveries recorded yet)")

    return "\n".join(lines)


def _write_journal_atomic(data: dict, workspace_dir: Optional[str] = None) -> None:
    p = _get_journal_path(workspace_dir)
    d = p.parent
    d.mkdir(parents=True, exist_ok=True)

    # Write to a temp file on the same filesystem and fsync before rename
    fd, tmp_path = tempfile.mkstemp(prefix=p.name, suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                # Not fatal on platforms without fsync semantics
                pass
        os.replace(tmp_path, str(p))
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _shorten(s: str, limit: int = 240) -> str:
    if s is None:
        return ""
    ss = str(s)
    if len(ss) <= limit:
        return ss
    return ss[: limit - 3] + "..."


def _render_readme(journal: dict, workspace_dir: Optional[str] = None) -> None:
    p = _get_readme_path(workspace_dir)
    lines = ["# Engagement Summary", "", "This file summarizes recent facts recorded by agents.", ""]
    entries = journal.get("entries", []) or []
    # show most recent 10
    for e in entries[-10:][::-1]:
        ts = e.get("timestamp", "?")
        cid = e.get("id", "?")
        cat = e.get("category", "")
        src = e.get("source", "")
        fact = e.get("fact", "")
        try:
            fact_text = _shorten(json.dumps(fact, ensure_ascii=False))
        except Exception:
            fact_text = _shorten(str(fact))
        try:
            conf = float(e.get("confidence_score", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        lock = " 🔒" if conf >= 1.0 else ""
        lines.append(f"- [{ts}] ({cat}){lock} {fact_text}  ")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


@function_tool
def sync_to_journal(
    ctx: RunContextWrapper[Any],
    fact: dict | list | str,
    category: str = "observation",
    source: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    confidence_score: float = 0.0,
    source_tool: Optional[str] = None,
) -> str:
    """Append a fact to the State Journal (intelligence.json).

    Args:
        ctx: Run context (automatically provided by the agent runtime).
        fact: Arbitrary JSON-serializable object describing the observation.
        category: A short category or tag (e.g. "credential", "host", "observation").
        source: Optional origin string (tool name, URL, or human note).
        workspace_dir: Optional override for CAI workspace directory.

    Returns:
        A simple status string confirming the commit and entry id.
    """
    try:
        # Normalize fact into a JSON serializable value
        if isinstance(fact, str):
            try:
                fact_val = json.loads(fact)
            except Exception:
                fact_val = {"text": fact}
        else:
            fact_val = fact

        journal = _read_journal(workspace_dir)
        entry_id = uuid.uuid4().hex
        resolved_source = source or (getattr(ctx.context, "name", None) if ctx and getattr(ctx, "context", None) else None)
        entry = {
            "id": entry_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "category": category,
            "source": resolved_source,
            # canonical source_tool field for provenance (tool or URL)
            "source_tool": source_tool or resolved_source,
            # confidence_score: 0.0-1.0 indicating reliability of this fact
            "confidence_score": float(confidence_score) if confidence_score is not None else 0.0,
            "session_id": os.getenv("CAI_SESSION_ID") or os.getenv("SESSION_ID"),
            "fact": fact_val,
        }
        journal.setdefault("entries", []).append(entry)
        # update meta
        journal.setdefault("meta", {})["updated_at"] = datetime.utcnow().isoformat() + "Z"

        _write_journal_atomic(journal, workspace_dir)
        # regenerate readme for human operators
        try:
            _render_readme(journal, workspace_dir)
        except Exception:
            pass

        # Emit a lightweight telemetry event so the TUI can pick up recent commits
        try:
            telem = {
                "event": "journal_commit",
                "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                "ts_ms": int(datetime.utcnow().timestamp() * 1000),
                "terminal_id": 1,
                "agent_name": source or (getattr(ctx.context, "name", None) if ctx and getattr(ctx, "context", None) else None),
                "data": {"id": entry_id, "category": category, "confidence_score": float(confidence_score) if confidence_score is not None else 0.0},
            }
            telem_file = os.path.join(os.getcwd(), "tui_telemetry.log")
            try:
                with open(telem_file, "a", encoding="utf-8") as tf:
                    tf.write(json.dumps(telem, ensure_ascii=True) + "\n")
            except Exception:
                pass
        except Exception:
            pass

        # Optionally enqueue this journal entry into RAG ingestion so downstream
        # vector stores receive the `confidence_score` in metadata. Controlled
        # via the environment variable `CAI_RAG_INGEST_JOURNAL` (off by default).
        try:
            if os.getenv("CAI_RAG_INGEST_JOURNAL", "0").lower() in ("1", "true", "yes"):
                try:
                    from cai.rag.chunking import chunk_text
                    from cai.rag.vector_db_adapter import get_vector_db_adapter
                    from cai.rag.ingestion import get_ingestor

                    adapter = get_vector_db_adapter()
                    text = json.dumps(fact_val, ensure_ascii=False) if not isinstance(fact_val, str) else fact_val
                    chunk_size = int(os.getenv("CAI_RAG_CHUNK_SIZE", "1000"))
                    overlap = int(os.getenv("CAI_RAG_CHUNK_OVERLAP", "200"))
                    chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
                    texts_to_add = [c.get("text") for c in chunks] if chunks else [text]
                    prov = {
                        "source": "journal",
                        "timestamp": entry["timestamp"],
                        "confidence_score": float(confidence_score) if confidence_score is not None else 0.0,
                        "source_tool": entry.get("source_tool"),
                    }
                    metas = [{"provenance": prov} for _ in texts_to_add]
                    ing = get_ingestor(adapter)
                    ing.enqueue("_journal_", entry_id, texts_to_add, metas)
                except Exception:
                    # best-effort: never raise from journaling
                    pass
        except Exception:
            pass

        return json.dumps({"status": "ok", "id": entry_id, "timestamp": entry["timestamp"]})
    except Exception as exc:  # pragma: no cover - return error to LLM rather than crash
        return json.dumps({"status": "error", "message": str(exc)})
