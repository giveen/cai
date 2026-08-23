"""Tests for the subdomain_bruteforce tool."""

import socket
from unittest.mock import patch, MagicMock

import pytest

from cai.tools.reconnaissance.subdomain_bruteforce import (
    _BUILTIN_SUBS,
    _SubResult,
    _detect_wildcard,
    _load_wordlist,
    _resolve,
    _run_subdomain_bruteforce,
)


# ---------------------------------------------------------------------------
# _load_wordlist
# ---------------------------------------------------------------------------

def test_load_wordlist_none_returns_builtin():
    words = _load_wordlist(None)
    assert words == _BUILTIN_SUBS


def test_load_wordlist_missing_file_fallback():
    words = _load_wordlist("/nonexistent/path/subdomains.txt")
    assert words == _BUILTIN_SUBS


def test_load_wordlist_reads_file(tmp_path):
    wl = tmp_path / "subs.txt"
    wl.write_text("www\nmail\n# comment\n\nblog\n")
    words = _load_wordlist(str(wl))
    assert words == ["www", "mail", "blog"]


# ---------------------------------------------------------------------------
# _resolve (mocked)
# ---------------------------------------------------------------------------

def test_resolve_returns_ips_on_success():
    fake_infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.4", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.2.3.5", 0)),
    ]
    with (
        patch("socket.getaddrinfo", return_value=fake_infos),
        patch("socket.gethostbyname_ex", return_value=("host", ["alias.example.com"], ["1.2.3.4"])),
    ):
        ips, cname = _resolve("www.example.com")
    assert set(ips) == {"1.2.3.4", "1.2.3.5"}
    assert cname == "alias.example.com"


def test_resolve_returns_empty_on_nxdomain():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
        ips, cname = _resolve("nonexistent.example.com")
    assert ips == []
    assert cname == ""


# ---------------------------------------------------------------------------
# _detect_wildcard (mocked)
# ---------------------------------------------------------------------------

def test_detect_wildcard_true():
    fake_infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 0))]
    with (
        patch("socket.getaddrinfo", return_value=fake_infos),
        patch("socket.gethostbyname_ex", return_value=("h", [], ["1.1.1.1"])),
    ):
        wc = _detect_wildcard("example.com")
    assert "1.1.1.1" in wc


def test_detect_wildcard_false():
    with patch("socket.getaddrinfo", side_effect=socket.gaierror):
        wc = _detect_wildcard("example.com")
    assert wc is None


# ---------------------------------------------------------------------------
# _run_subdomain_bruteforce (mocked)
# ---------------------------------------------------------------------------

def _mock_resolve_returns(results: dict):
    """Patch _resolve so each subdomain maps to (ips, cname)."""
    from cai.tools.reconnaissance import subdomain_bruteforce as m

    def fake_resolve(subdomain):
        return results.get(subdomain, ([], ""))

    return patch.object(m, "_resolve", side_effect=fake_resolve)


def test_run_finds_subdomain():
    resolved = {
        "cai-wildcard-probe-99999.example.com": ([], ""),
        "www.example.com": (["1.2.3.4"], ""),
        "mail.example.com": ([], ""),
    }
    with _mock_resolve_returns(resolved):
        result = _run_subdomain_bruteforce("example.com", wordlist=None, threads=2)
    assert "www.example.com" in result
    assert "1.2.3.4" in result


def test_run_no_results():
    resolved = {}  # everything NXDOMAIN
    with _mock_resolve_returns(resolved):
        result = _run_subdomain_bruteforce("example.com", wordlist=None, threads=2)
    assert "No subdomains" in result


def test_run_wildcard_suppressed():
    # Wildcard resolves to 9.9.9.9; "www" also resolves to 9.9.9.9 → suppressed
    resolved = {
        "cai-wildcard-probe-99999.example.com": (["9.9.9.9"], ""),
        "www.example.com": (["9.9.9.9"], ""),      # matches wildcard → skip
        "api.example.com": (["1.2.3.4"], ""),       # unique → kept
    }
    with _mock_resolve_returns(resolved):
        result = _run_subdomain_bruteforce("example.com", wordlist=None, threads=2)
    assert "api.example.com" in result
    assert "www.example.com" not in result.splitlines()[len(result.splitlines()) - 10:]


def test_run_invalid_domain():
    result = _run_subdomain_bruteforce("notadomain")
    assert "Error" in result


def test_run_custom_wordlist(tmp_path):
    wl = tmp_path / "subs.txt"
    wl.write_text("api\n")
    resolved = {
        "cai-wildcard-probe-99999.target.io": ([], ""),
        "api.target.io": (["10.0.0.1"], ""),
    }
    with _mock_resolve_returns(resolved):
        result = _run_subdomain_bruteforce("target.io", wordlist=str(wl), threads=2)
    assert "api.target.io" in result


def test_run_max_results():
    # Resolve everything to a unique IP — check that max_results caps output
    from cai.tools.reconnaissance import subdomain_bruteforce as m

    counter = [0]

    def counting_resolve(subdomain):
        if "wildcard-probe" in subdomain:
            return [], ""
        counter[0] += 1
        return [f"1.0.{counter[0]}.1"], ""

    with patch.object(m, "_resolve", side_effect=counting_resolve):
        result = _run_subdomain_bruteforce(
            "example.com",
            wordlist=None,
            threads=4,
            max_results=3,
        )
    # At most 3 result lines
    hit_lines = [ln for ln in result.splitlines() if ".example.com" in ln]
    assert len(hit_lines) <= 3
