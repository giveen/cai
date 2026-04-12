import json
from datetime import datetime

import pytest

from cai.orchestration import persistence
from cai import session


def _write_journal(path: str, entry: dict) -> None:
    p = persistence._get_journal_path(path)
    journal = {"meta": {"version": 1, "updated_at": datetime.utcnow().isoformat() + "Z"}, "entries": [entry]}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(journal, f, ensure_ascii=False, indent=2)


def test_is_fact_anchored_and_clear(tmp_path):
    ws = str(tmp_path)
    entry = {
        "id": "e-anchor-1",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "category": "credential",
        "source": "test",
        "source_tool": "test",
        "confidence_score": 1.0,
        "session_id": None,
        "fact": {"username": "admin", "password": "pass123"},
    }
    _write_journal(ws, entry)

    assert persistence.is_fact_anchored("admin", workspace_dir=ws)

    cleared = persistence.clear_anchor(search_text="admin", workspace_dir=ws)
    assert cleared is True
    assert not persistence.is_fact_anchored("admin", workspace_dir=ws)


def test_commit_changes_respects_anchor_and_clearing(tmp_path):
    ws = str(tmp_path)
    entry = {
        "id": "e-anchor-2",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "category": "credential",
        "source": "test",
        "source_tool": "test",
        "confidence_score": 1.0,
        "session_id": None,
        "fact": {"username": "admin", "password": "pass123"},
    }
    _write_journal(ws, entry)

    # Attempt to commit a credential matching anchored fact — should be skipped
    res = session.commit_changes({"credentials": {"admin": "newpass"}}, workspace_dir=ws)
    assert isinstance(res, dict)
    assert "admin" not in res.get("added_credentials", [])
    state = session._read_state(ws)
    assert "admin" not in state.get("credentials", {})

    # Clear anchor and commit again — should be added
    cleared = persistence.clear_anchor(search_text="admin", workspace_dir=ws)
    assert cleared is True

    res2 = session.commit_changes({"credentials": {"admin": "newpass"}}, workspace_dir=ws)
    assert "admin" in res2.get("added_credentials", [])
    state2 = session._read_state(ws)
    assert state2.get("credentials", {}).get("admin") == "newpass"
