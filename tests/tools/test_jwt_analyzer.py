"""Tests for the jwt_analyzer tool."""

import base64
import json
import time
import pytest

from cai.tools.web.jwt_analyzer import (
    _b64_decode_url,
    _check_alg,
    _check_claims,
    _parse_segment,
    _run_jwt_analyze,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_jwt(header: dict, payload: dict, sig: str = "fakesig") -> str:
    return f"{_b64url(header)}.{_b64url(payload)}.{sig}"


# ---------------------------------------------------------------------------
# _b64_decode_url
# ---------------------------------------------------------------------------

def test_b64_decode_url_no_padding():
    encoded = base64.urlsafe_b64encode(b"hello").rstrip(b"=").decode()
    assert _b64_decode_url(encoded) == b"hello"


def test_b64_decode_url_with_dashes():
    # URL-safe encoding uses - instead of +
    raw = b"\xfb\xef"
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    assert _b64_decode_url(encoded) == raw


# ---------------------------------------------------------------------------
# _check_alg
# ---------------------------------------------------------------------------

def test_check_alg_none():
    warnings = _check_alg("none")
    assert any("CRITICAL" in w for w in warnings)


def test_check_alg_hs256():
    warnings = _check_alg("HS256")
    assert any("symmetric" in w.lower() or "hs256" in w.lower() for w in warnings)


def test_check_alg_rs256():
    warnings = _check_alg("RS256")
    assert warnings == []


# ---------------------------------------------------------------------------
# _check_claims
# ---------------------------------------------------------------------------

def test_check_claims_no_exp():
    warnings = _check_claims({})
    assert any("exp" in w.lower() for w in warnings)


def test_check_claims_expired():
    past = int(time.time()) - 3600
    warnings = _check_claims({"exp": past})
    assert any("EXPIRED" in w for w in warnings)


def test_check_claims_long_expiry():
    far_future = int(time.time()) + 400 * 86400  # 400 days
    warnings = _check_claims({"exp": far_future})
    assert any("long" in w.lower() for w in warnings)


def test_check_claims_valid_short_exp():
    soon = int(time.time()) + 3600  # 1 hour
    warnings = _check_claims({"exp": soon, "iat": int(time.time()), "sub": "user", "iss": "test"})
    # No critical warnings for a well-formed short-lived token
    assert not any("CRITICAL" in w or "EXPIRED" in w or "long" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# _run_jwt_analyze
# ---------------------------------------------------------------------------

def test_analyze_empty():
    result = _run_jwt_analyze("")
    assert "Error" in result


def test_analyze_bad_format():
    result = _run_jwt_analyze("not.a.jwt.with.too.many.parts")
    assert "Error" in result or "parts" in result


def test_analyze_two_parts():
    result = _run_jwt_analyze("only.two")
    assert "Error" in result


def test_analyze_basic_hs256():
    token = _make_jwt(
        {"alg": "HS256", "typ": "JWT"},
        {"sub": "1234", "iat": int(time.time()), "exp": int(time.time()) + 3600},
    )
    result = _run_jwt_analyze(token)
    assert "HS256" in result
    assert "1234" in result


def test_analyze_alg_none():
    token = _make_jwt({"alg": "none", "typ": "JWT"}, {"sub": "admin"})
    result = _run_jwt_analyze(token)
    assert "CRITICAL" in result
    assert "none" in result.lower()


def test_analyze_jku_warning():
    token = _make_jwt(
        {"alg": "RS256", "typ": "JWT", "jku": "https://attacker.com/keys.json"},
        {"sub": "user", "exp": int(time.time()) + 3600},
    )
    result = _run_jwt_analyze(token)
    assert "CRITICAL" in result
    assert "jku" in result.lower() or "key" in result.lower()


def test_analyze_expired_token():
    token = _make_jwt(
        {"alg": "HS256", "typ": "JWT"},
        {"sub": "user", "exp": int(time.time()) - 86400},
    )
    result = _run_jwt_analyze(token)
    assert "EXPIRED" in result


def test_analyze_no_exp_warning():
    token = _make_jwt({"alg": "RS256", "typ": "JWT"}, {"sub": "user"})
    result = _run_jwt_analyze(token)
    assert "exp" in result.lower()


def test_analyze_empty_signature():
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url({"sub": "admin"})
    token = f"{header}.{payload}."
    result = _run_jwt_analyze(token)
    assert "CRITICAL" in result or "empty" in result.lower()
