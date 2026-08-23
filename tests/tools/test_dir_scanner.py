"""Tests for the dir_scanner tool."""

import http.client
from unittest.mock import patch, MagicMock, call
import pytest

from cai.tools.web.dir_scanner import (
    _Hit,
    _generate_paths,
    _load_wordlist,
    _run_dir_scan,
    _BUILTIN_WORDLIST,
)


# ---------------------------------------------------------------------------
# _generate_paths
# ---------------------------------------------------------------------------

def test_generate_paths_bare_word():
    paths = list(_generate_paths(["admin"], [""]))
    assert "/admin" in paths


def test_generate_paths_with_extensions():
    paths = list(_generate_paths(["test"], [".php", ".html"]))
    assert "/test.php" in paths
    assert "/test.html" in paths


def test_generate_paths_empty_and_extension():
    paths = list(_generate_paths(["index"], ["", ".php"]))
    assert "/index" in paths
    assert "/index.php" in paths


# ---------------------------------------------------------------------------
# _load_wordlist
# ---------------------------------------------------------------------------

def test_load_wordlist_none_returns_builtin():
    words = _load_wordlist(None)
    assert words == _BUILTIN_WORDLIST


def test_load_wordlist_missing_file_falls_back():
    words = _load_wordlist("/nonexistent/path/wordlist.txt")
    assert words == _BUILTIN_WORDLIST


def test_load_wordlist_reads_file(tmp_path):
    wl = tmp_path / "words.txt"
    wl.write_text("admin\nlogin\n# comment\n\nbackup\n")
    words = _load_wordlist(str(wl))
    assert words == ["admin", "login", "backup"]


# ---------------------------------------------------------------------------
# _run_dir_scan — mocked HTTP responses
# ---------------------------------------------------------------------------

def _make_mock_response(status: int, headers: dict = None, body: bytes = b""):
    resp = MagicMock()
    resp.status = status
    headers = headers or {}
    resp.getheader = lambda name, default="": headers.get(name, default)
    resp.read = lambda n=None: body[:n] if n else body
    return resp


def _patch_probe_for_scan(hits: dict[str, int]):
    """Patch _probe so each path returns a Hit if path (stripped) is in hits."""
    from cai.tools.web import dir_scanner as m

    def fake_probe(host, port, use_ssl, path, timeout):
        bare = path.split("?")[0].rstrip("/")
        status = hits.get(bare)
        if status is not None:
            return _Hit(path=bare, status=status, size=100)
        return None

    return patch.object(m, "_probe", side_effect=fake_probe)


def test_run_dir_scan_finds_hit():
    hits = {"/admin": 200, "/login": 302}
    with _patch_probe_for_scan(hits):
        result = _run_dir_scan("http://target.local", wordlist=None, threads=2, timeout=1)
    assert "200" in result
    assert "/admin" in result


def test_run_dir_scan_no_hits():
    with _patch_probe_for_scan({}):
        result = _run_dir_scan("http://target.local", wordlist=None, threads=2, timeout=1)
    assert "No interesting paths found" in result


def test_run_dir_scan_filters_by_codes():
    hits = {"/admin": 200, "/old": 403}
    with _patch_probe_for_scan(hits):
        result = _run_dir_scan(
            "http://target.local",
            wordlist=None,
            threads=2,
            timeout=1,
            include_codes="200",
        )
    assert "/admin" in result
    assert "/old" not in result


def test_run_dir_scan_bad_url():
    result = _run_dir_scan("")
    assert "Error" in result or "could not parse" in result


def test_run_dir_scan_with_extensions(tmp_path):
    wl = tmp_path / "words.txt"
    wl.write_text("shell\n")
    hits = {"/shell.php": 200}
    with _patch_probe_for_scan(hits):
        result = _run_dir_scan(
            "http://target.local",
            wordlist=str(wl),
            extensions="php",
            threads=2,
            timeout=1,
        )
    assert "/shell.php" in result


def test_run_dir_scan_respects_max_results():
    # Make every path return 200
    from cai.tools.web import dir_scanner as m

    def always_hit(host, port, use_ssl, path, timeout):
        return _Hit(path=path, status=200, size=50)

    with patch.object(m, "_probe", side_effect=always_hit):
        result = _run_dir_scan(
            "http://target.local",
            wordlist=None,
            threads=4,
            timeout=1,
            max_results=5,
        )
    # Should report at most 5 results
    hit_lines = [ln for ln in result.splitlines() if ln.startswith("200")]
    assert len(hit_lines) <= 5


def test_run_dir_scan_https():
    hits = {"/admin": 200}
    with _patch_probe_for_scan(hits):
        result = _run_dir_scan("https://secure.target.local", wordlist=None, threads=2, timeout=1)
    assert "https" in result or "443" in result or "secure.target.local" in result
