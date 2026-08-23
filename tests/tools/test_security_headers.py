"""Tests for HTTP security headers analyzer."""
import unittest
from unittest.mock import patch

from cai.tools.web.security_headers import (
    HeaderFinding,
    _analyze,
    _check_csp,
    _check_hsts,
    _check_x_content_type_options,
    _check_x_frame_options,
    _run_security_headers,
)


class TestCheckHSTS(unittest.TestCase):
    def test_missing_hsts(self):
        f = _check_hsts({})
        self.assertEqual(f.status, "MISSING")
        self.assertEqual(f.severity, "HIGH")

    def test_max_age_zero_disabled(self):
        f = _check_hsts({"strict-transport-security": "max-age=0"})
        self.assertEqual(f.status, "MISCONFIGURED")
        self.assertEqual(f.severity, "HIGH")

    def test_short_max_age(self):
        f = _check_hsts({"strict-transport-security": "max-age=86400"})
        self.assertEqual(f.status, "MISCONFIGURED")
        self.assertEqual(f.severity, "MEDIUM")

    def test_good_hsts_without_includesubdomains(self):
        f = _check_hsts({"strict-transport-security": "max-age=31536000"})
        self.assertEqual(f.status, "MISCONFIGURED")
        self.assertEqual(f.severity, "LOW")

    def test_good_hsts_full(self):
        f = _check_hsts({"strict-transport-security": "max-age=31536000; includeSubDomains; preload"})
        self.assertEqual(f.status, "PRESENT")
        self.assertEqual(f.severity, "INFO")


class TestCheckCSP(unittest.TestCase):
    def test_missing_csp(self):
        f = _check_csp({})
        self.assertEqual(f.status, "MISSING")
        self.assertEqual(f.severity, "HIGH")

    def test_unsafe_inline(self):
        f = _check_csp({"content-security-policy": "script-src 'unsafe-inline'"})
        self.assertEqual(f.status, "MISCONFIGURED")
        self.assertIn("unsafe-inline", f.detail)

    def test_unsafe_eval(self):
        f = _check_csp({"content-security-policy": "script-src 'unsafe-eval'"})
        self.assertEqual(f.status, "MISCONFIGURED")
        self.assertIn("unsafe-eval", f.detail)

    def test_wildcard_source(self):
        f = _check_csp({"content-security-policy": "script-src *"})
        self.assertEqual(f.status, "MISCONFIGURED")

    def test_good_csp(self):
        f = _check_csp({"content-security-policy": "default-src 'self'; script-src 'self'"})
        self.assertEqual(f.status, "PRESENT")

    def test_legacy_csp_header(self):
        f = _check_csp({"x-content-security-policy": "default-src 'self'"})
        self.assertEqual(f.status, "MISCONFIGURED")
        self.assertEqual(f.severity, "LOW")


class TestCheckXFrameOptions(unittest.TestCase):
    def test_missing(self):
        f = _check_x_frame_options({})
        self.assertEqual(f.status, "MISSING")
        self.assertEqual(f.severity, "HIGH")

    def test_deny(self):
        f = _check_x_frame_options({"x-frame-options": "DENY"})
        self.assertEqual(f.status, "PRESENT")

    def test_sameorigin(self):
        f = _check_x_frame_options({"x-frame-options": "SAMEORIGIN"})
        self.assertEqual(f.status, "PRESENT")

    def test_allow_from_obsolete(self):
        f = _check_x_frame_options({"x-frame-options": "ALLOW-FROM https://partner.com"})
        self.assertEqual(f.status, "MISCONFIGURED")

    def test_csp_frame_ancestors_supersedes(self):
        f = _check_x_frame_options({"content-security-policy": "frame-ancestors 'self'"})
        self.assertEqual(f.status, "PRESENT")


class TestCheckXContentTypeOptions(unittest.TestCase):
    def test_missing(self):
        f = _check_x_content_type_options({})
        self.assertEqual(f.status, "MISSING")
        self.assertEqual(f.severity, "MEDIUM")

    def test_nosniff(self):
        f = _check_x_content_type_options({"x-content-type-options": "nosniff"})
        self.assertEqual(f.status, "PRESENT")


class TestAnalyze(unittest.TestCase):
    def _mock_fetch(self, status, headers):
        return patch(
            "cai.tools.web.security_headers._fetch_headers",
            return_value=(status, headers),
        )

    def test_error_on_unreachable(self):
        with self._mock_fetch(-1, {}):
            status, findings = _analyze("https://unreachable.example.com")
        self.assertEqual(status, -1)
        self.assertEqual(findings, [])

    def test_all_headers_missing(self):
        with self._mock_fetch(200, {"server": "generic"}):
            status, findings = _analyze("https://example.com")
        self.assertEqual(status, 200)
        statuses = [f.status for f in findings]
        self.assertIn("MISSING", statuses)

    def test_secure_site_all_present(self):
        headers = {
            "strict-transport-security": "max-age=31536000; includeSubDomains",
            "content-security-policy": "default-src 'self'",
            "x-frame-options": "DENY",
            "x-content-type-options": "nosniff",
            "referrer-policy": "strict-origin",
            "permissions-policy": "geolocation=()",
            "cross-origin-opener-policy": "same-origin",
        }
        with self._mock_fetch(200, headers):
            status, findings = _analyze("https://example.com")
        missing_or_misc = [f for f in findings if f.status in ("MISSING", "MISCONFIGURED")]
        self.assertEqual(missing_or_misc, [])

    def test_url_without_scheme_gets_https(self):
        with self._mock_fetch(200, {}) as mock_fetch:
            _analyze("example.com")
        called_url = mock_fetch.call_args_list[0][0][0]
        self.assertTrue(called_url.startswith("https://"))

    def test_server_version_disclosure(self):
        with self._mock_fetch(200, {"server": "Apache/2.4.51"}):
            status, findings = _analyze("https://example.com")
        info_findings = [f for f in findings if f.header == "Server"]
        self.assertTrue(len(info_findings) > 0)
        self.assertIn("discloses version", info_findings[0].detail)

    def test_x_powered_by_disclosure(self):
        with self._mock_fetch(200, {"x-powered-by": "PHP/8.1.0"}):
            status, findings = _analyze("https://example.com")
        pb_findings = [f for f in findings if f.header == "X-Powered-By"]
        self.assertTrue(len(pb_findings) > 0)


class TestRunSecurityHeaders(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_security_headers("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_security_headers("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        with patch("cai.tools.web.security_headers._analyze") as mock_analyze:
            mock_analyze.return_value = (200, [
                HeaderFinding("X-Frame-Options", "HIGH", "MISSING", "No X-Frame-Options"),
            ])
            out = _run_security_headers("https://example.com")
        self.assertIn("Summary:", out)

    def test_comma_separated_targets(self):
        with patch("cai.tools.web.security_headers._analyze") as mock_analyze:
            mock_analyze.return_value = (200, [])
            out = _run_security_headers("https://a.com, https://b.com")
        self.assertEqual(mock_analyze.call_count, 2)

    def test_note_shown_when_issues_found(self):
        with patch("cai.tools.web.security_headers._analyze") as mock_analyze:
            mock_analyze.return_value = (200, [
                HeaderFinding("CSP", "HIGH", "MISSING", "no csp"),
            ])
            out = _run_security_headers("https://example.com")
        self.assertIn("Note:", out)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("security_headers", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
