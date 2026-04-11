import os

import pytest

from cai.tools import common as common
import cai.session as session_module


def test_should_skip_recon_detects_nmap_by_default(monkeypatch):
    # Simulate existing targets in workspace state
    monkeypatch.setattr(session_module, "_read_state", lambda workspace_dir=None: {"targets": ["1.2.3.4"]})
    assert common._should_skip_recon("nmap", None, None) is True


def test_should_not_skip_when_forced_in_args(monkeypatch):
    monkeypatch.setattr(session_module, "_read_state", lambda workspace_dir=None: {"targets": ["1.2.3.4"]})
    assert common._should_skip_recon("nmap", {"force": True}, None) is False


def test_should_not_skip_when_env_forced(monkeypatch):
    monkeypatch.setattr(session_module, "_read_state", lambda workspace_dir=None: {"targets": ["1.2.3.4"]})
    monkeypatch.setenv("CAI_FORCE_RECON", "true")
    assert common._should_skip_recon("nmap", None, None) is False


def test_non_recon_tool_not_skipped(monkeypatch):
    monkeypatch.setattr(session_module, "_read_state", lambda workspace_dir=None: {"targets": ["1.2.3.4"]})
    assert common._should_skip_recon("hashcat", None, "password cracking") is False
