"""Tests for cai.util.maintenance smoke/integration behavior."""
from __future__ import annotations

import subprocess
from pathlib import Path

from cai.util import maintenance


def test_parse_new_chunks():
    s = "Indexed 200 chunks from 5 files (0 skipped). New/updated chunks this run: 42."
    assert maintenance._parse_new_chunks(s) == 42


def test_emit_tui_notification(monkeypatch):
    recorded: list[str] = []

    def fake_write(msg: str, style: str | None = None):
        recorded.append(msg)

    # Patch the symbol used by the maintenance module (it imports write_progress at module import)
    monkeypatch.setattr(maintenance, "write_progress", fake_write)

    maintenance._emit_tui_notification(3, "... PayloadsAllTheThings ...")
    assert any("Knowledge base updated" in m for m in recorded)
    assert any("3 new payload" in m for m in recorded)


def test_sync_knowledge_base_success(monkeypatch):
    recorded: list[str] = []

    def fake_write(msg: str, style: str | None = None):
        recorded.append(msg)

    # Patch the symbol used by the maintenance module
    monkeypatch.setattr(maintenance, "write_progress", fake_write)

    fake_stdout = "Indexed 10 chunks from 2 files (0 skipped). New/updated chunks this run: 7.\n"
    fake_cp = subprocess.CompletedProcess(
        args=["bash", "scripts/vault_sync.sh"], returncode=0, stdout=fake_stdout, stderr=""
    )

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_cp)

    val = maintenance.sync_knowledge_base()
    assert val == 7
    assert any(
        "Knowledge base sync starting" in m or "Knowledge base updated" in m
        for m in recorded
    )


def test_sync_knowledge_base_missing_script(monkeypatch):
    monkeypatch.setattr(maintenance, "_SYNC_SCRIPT", Path("/nonexistent/vault_sync.sh"))
    val = maintenance.sync_knowledge_base()
    assert val == -1


def test_start_and_stop_scheduler():
    # Start and stop should be idempotent and not raise
    maintenance.start_scheduler()
    assert maintenance._scheduler is not None
    maintenance.stop_scheduler()
    assert maintenance._scheduler is None
