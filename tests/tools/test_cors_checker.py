"""Tests for CORS misconfiguration checker (no network — monkey-patch _request/_preflight)."""

import pytest
from unittest.mock import patch

from cai.tools.web.cors_checker import (
    _acao,
    _acac,
    _run_cors_check,
)


# ─── Unit helpers ─────────────────────────────────────────────────────────────

def test_acao_present():
    assert _acao({"access-control-allow-origin": "https://foo.com"}) == "https://foo.com"


def test_acao_absent():
    assert _acao({}) is None


def test_acac_true():
    assert _acac({"access-control-allow-credentials": "true"}) is True


def test_acac_false_variants():
    assert not _acac({"access-control-allow-credentials": "false"})
    assert not _acac({})
    # "True" (capital T) is non-standard but treated permissively by our checker
    assert _acac({"access-control-allow-credentials": "True"})


def test_acac_case_insensitive():
    # Spec says "true" (lowercase), but be safe
    assert _acac({"access-control-allow-credentials": "true"}) is True


# ─── Integration tests (network stubbed) ─────────────────────────────────────

def _make_request_mock(acao_value, credentials=False):
    """Return a fake _request callable that always echoes the requested origin if acao_value='reflect'."""
    def _fake_request(url, origin, method="GET", extra_headers=None, timeout=10):
        acao = origin if acao_value == "reflect" else acao_value
        acac_val = "true" if credentials else "false"
        headers = {}
        if acao is not None:
            headers["access-control-allow-origin"] = acao
        headers["access-control-allow-credentials"] = acac_val
        headers["vary"] = "origin"
        return (200, headers, "")
    return _fake_request


def _make_preflight_mock(acao_value="absent", credentials=False):
    def _fake_preflight(url, origin, method="PUT", req_headers="X-Custom-Header", timeout=10):
        headers = {}
        if acao_value != "absent":
            headers["access-control-allow-origin"] = acao_value
        if credentials:
            headers["access-control-allow-credentials"] = "true"
        headers["access-control-allow-methods"] = "GET, POST"
        return (200, headers)
    return _fake_preflight


def _baseline_mock(acao=None):
    """Return a mock for the baseline (no Origin) request."""
    def _fake_connect(parsed, timeout=10):
        class FakeResp:
            status = 200
            def getheaders(self):
                hdrs = []
                if acao:
                    hdrs.append(("Access-Control-Allow-Origin", acao))
                hdrs.append(("Vary", "Origin"))
                return hdrs
            def read(self, n=4096):
                return b""
        class FakeConn:
            def request(self, *a, **kw): pass
            def getresponse(self): return FakeResp()
            def close(self): pass
        return FakeConn()
    return _fake_connect


def _run_with_stubs(acao_val, credentials=False, preflight_acao="absent"):
    with (
        patch("cai.tools.web.cors_checker._connect", _baseline_mock()),
        patch("cai.tools.web.cors_checker._request", _make_request_mock(acao_val, credentials)),
        patch("cai.tools.web.cors_checker._preflight", _make_preflight_mock(preflight_acao, credentials)),
    ):
        return _run_cors_check("https://target.example.com/api/data")


# ─── Scenario tests ───────────────────────────────────────────────────────────

def test_clean_no_cors_headers():
    result = _run_with_stubs(acao_val=None)
    assert "No CORS misconfigurations detected" in result


def test_reflected_origin_with_credentials_is_critical():
    result = _run_with_stubs(acao_val="reflect", credentials=True)
    assert "CRITICAL" in result
    assert "Arbitrary origin reflected" in result


def test_reflected_origin_no_credentials_is_high():
    result = _run_with_stubs(acao_val="reflect", credentials=False)
    assert "HIGH" in result
    assert "Arbitrary origin reflected" in result


def test_wildcard_with_credentials_is_high():
    result = _run_with_stubs(acao_val="*", credentials=True)
    assert "HIGH" in result
    assert "Wildcard" in result


def test_wildcard_without_credentials_medium():
    # Wildcard ACAO in baseline (no Origin sent) triggers MEDIUM finding
    with (
        patch("cai.tools.web.cors_checker._connect", _baseline_mock(acao="*")),
        patch("cai.tools.web.cors_checker._request", _make_request_mock(None, False)),
        patch("cai.tools.web.cors_checker._preflight", _make_preflight_mock()),
    ):
        result = _run_cors_check("https://target.example.com/api/data")
    assert "MEDIUM" in result
    assert "Wildcard" in result


def test_null_origin_reflected_with_credentials_critical():
    def _fake_request(url, origin, method="GET", extra_headers=None, timeout=10):
        if origin == "null":
            return (200, {"access-control-allow-origin": "null", "access-control-allow-credentials": "true", "vary": "origin"}, "")
        return (200, {"vary": "origin"}, "")

    with (
        patch("cai.tools.web.cors_checker._connect", _baseline_mock()),
        patch("cai.tools.web.cors_checker._request", _fake_request),
        patch("cai.tools.web.cors_checker._preflight", _make_preflight_mock()),
    ):
        result = _run_cors_check("https://target.example.com/api/data")
    assert "CRITICAL" in result
    assert "Null origin" in result


def test_empty_url_returns_error():
    result = _run_cors_check("")
    assert "Error" in result


def test_url_without_scheme_handled():
    with (
        patch("cai.tools.web.cors_checker._connect", _baseline_mock()),
        patch("cai.tools.web.cors_checker._request", _make_request_mock(None)),
        patch("cai.tools.web.cors_checker._preflight", _make_preflight_mock()),
    ):
        result = _run_cors_check("target.example.com/api")
    assert "target.example.com" in result


def test_custom_origin_probed():
    with (
        patch("cai.tools.web.cors_checker._connect", _baseline_mock()),
        patch("cai.tools.web.cors_checker._request", _make_request_mock("reflect", credentials=True)),
        patch("cai.tools.web.cors_checker._preflight", _make_preflight_mock()),
    ):
        result = _run_cors_check("https://target.example.com/api", "https://myevil.com")
    assert "myevil.com" in result
    assert "CRITICAL" in result


def test_registry_registration():
    from cai.tool_registry import TOOL_REGISTRY
    assert "cors_checker" in TOOL_REGISTRY._tools


def test_function_tool_wrapper():
    from cai.tools.web.cors_checker import cors_checker
    # function_tool returns a FunctionTool object (not a plain callable)
    assert cors_checker.name == "cors_checker"
