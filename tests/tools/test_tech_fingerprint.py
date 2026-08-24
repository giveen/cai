"""Tests for HTTP technology fingerprinter (network stubbed)."""

import pytest
from unittest.mock import patch

from cai.tools.web.tech_fingerprint import _run_tech_fingerprint, _fetch


def _make_fetch(status=200, headers=None, body="", cookies=()):
    """Build a fake _fetch that returns the 4-tuple signature (status, headers, body, raw_pairs)."""
    def _fake(url, method="GET", headers_arg=None, timeout=10):
        hdr = headers or {}
        raw_pairs = list(hdr.items()) + [("set-cookie", c) for c in cookies]
        return (status, hdr, body, raw_pairs)
    return _fake


# ─── Basic detection ──────────────────────────────────────────────────────────

def test_empty_url_returns_error():
    assert "Error" in _run_tech_fingerprint("")


def test_url_without_scheme_handled():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"server": "nginx/1.24"},
        body="",
    )):
        result = _run_tech_fingerprint("example.com")
    assert "nginx" in result.lower()


def test_nginx_server_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"server": "nginx/1.24.0"},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "nginx" in result.lower()


def test_apache_server_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"server": "Apache/2.4.41 (Ubuntu)"},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "Apache" in result


def test_php_via_xpoweredby():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"x-powered-by": "PHP/8.1.0"},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "PHP" in result


def test_aspnet_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"x-powered-by": "ASP.NET", "server": "Microsoft-IIS/10.0"},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "ASP.NET" in result
    assert "IIS" in result


def test_cloudflare_cdn_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"cf-ray": "abc123-FRA", "server": "cloudflare"},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "Cloudflare" in result


def test_wordpress_body_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={},
        body='<script src="/wp-content/themes/theme.js"></script>',
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "WordPress" in result


def test_drupal_header_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={"x-drupal-cache": "HIT"},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "Drupal" in result


def test_nextjs_body_detected():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={},
        body='<script id="__NEXT_DATA__" type="application/json"></script>',
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "Next.js" in result


# ─── Security headers ─────────────────────────────────────────────────────────

def test_missing_security_headers_reported():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={},
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "Missing Security Headers" in result
    assert "strict-transport-security" in result


def test_present_security_headers_reported():
    with patch("cai.tools.web.tech_fingerprint._fetch", _make_fetch(
        headers={
            "strict-transport-security": "max-age=31536000; includeSubDomains",
            "content-security-policy": "default-src 'self'",
            "x-frame-options": "DENY",
            "x-content-type-options": "nosniff",
            "referrer-policy": "strict-origin",
        },
        body="",
    )):
        result = _run_tech_fingerprint("https://example.com", probe_paths=False)
    assert "HSTS enabled" in result
    assert "All baseline security headers present" in result


# ─── Path probing ─────────────────────────────────────────────────────────────

def test_env_exposure_detected_via_path_probe():
    def _probe_fetch(url, method="GET", headers_arg=None, timeout=10):
        if ".env" in url:
            return (200, {}, "APP_KEY=base64:abcdef123456", [])
        return (200, {}, "", [])

    with patch("cai.tools.web.tech_fingerprint._fetch", _probe_fetch):
        result = _run_tech_fingerprint("https://example.com", probe_paths=True)
    assert "EXPOSED" in result
    assert ".env" in result


def test_git_exposure_detected_via_path_probe():
    def _probe_fetch(url, method="GET", headers_arg=None, timeout=10):
        if ".git/HEAD" in url:
            return (200, {}, "ref: refs/heads/main", [])
        return (200, {}, "", [])

    with patch("cai.tools.web.tech_fingerprint._fetch", _probe_fetch):
        result = _run_tech_fingerprint("https://example.com", probe_paths=True)
    assert "Git repo exposed" in result


def test_probe_paths_false_skips_path_probes():
    call_count = [0]

    def _probe_fetch(url, method="GET", headers_arg=None, timeout=10):
        call_count[0] += 1
        return (200, {}, "", [])

    with patch("cai.tools.web.tech_fingerprint._fetch", _probe_fetch):
        _run_tech_fingerprint("https://example.com", probe_paths=False)
    # Only the homepage should be fetched
    assert call_count[0] == 1


# ─── Registry ─────────────────────────────────────────────────────────────────

def test_registry_registration():
    from cai.tool_registry import TOOL_REGISTRY
    assert "tech_fingerprint" in TOOL_REGISTRY._tools


def test_function_tool_name():
    from cai.tools.web.tech_fingerprint import tech_fingerprint
    assert tech_fingerprint.name == "tech_fingerprint"
