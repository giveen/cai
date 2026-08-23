"""Tests for cookie security attribute analyzer."""
import unittest
from unittest.mock import patch

from cai.tools.web.cookie_analyzer import (
    CookieFinding,
    _analyze_cookies,
    _check_cookie,
    _parse_cookie_attrs,
    _run_cookie_analyzer,
)


class TestParseCookieAttrs(unittest.TestCase):
    def test_simple_name_value(self):
        attrs = _parse_cookie_attrs("session=abc123")
        self.assertEqual(attrs["_name"], "session")
        self.assertEqual(attrs["_value"], "abc123")

    def test_secure_httponly_flags(self):
        attrs = _parse_cookie_attrs("session=abc; Secure; HttpOnly")
        self.assertTrue(attrs.get("secure"))
        self.assertTrue(attrs.get("httponly"))

    def test_samesite_strict(self):
        attrs = _parse_cookie_attrs("session=abc; SameSite=Strict")
        self.assertEqual(attrs.get("samesite"), "Strict")

    def test_domain_attribute(self):
        attrs = _parse_cookie_attrs("id=xyz; Domain=example.com; Path=/")
        self.assertEqual(attrs.get("domain"), "example.com")
        self.assertEqual(attrs.get("path"), "/")

    def test_full_secure_cookie(self):
        attrs = _parse_cookie_attrs(
            "session=tok; Secure; HttpOnly; SameSite=Lax; Path=/; Domain=app.example.com"
        )
        self.assertTrue(attrs.get("secure"))
        self.assertTrue(attrs.get("httponly"))
        self.assertEqual(attrs.get("samesite"), "Lax")


class TestCheckCookie(unittest.TestCase):
    def _attrs(self, raw: str) -> dict:
        from cai.tools.web.cookie_analyzer import _parse_cookie_attrs
        return _parse_cookie_attrs(raw)

    def test_missing_secure_on_https(self):
        attrs = self._attrs("session=abc; HttpOnly; SameSite=Lax")
        findings = _check_cookie(attrs, is_https=True)
        secure_findings = [f for f in findings if f.attribute == "Secure" and f.status == "MISSING"]
        self.assertTrue(len(secure_findings) > 0)
        self.assertEqual(secure_findings[0].severity, "HIGH")

    def test_no_missing_secure_on_http(self):
        attrs = self._attrs("session=abc; HttpOnly; SameSite=Lax")
        findings = _check_cookie(attrs, is_https=False)
        secure_missing = [f for f in findings if f.attribute == "Secure" and f.status == "MISSING"]
        self.assertEqual(secure_missing, [])

    def test_missing_httponly(self):
        attrs = self._attrs("session=abc; Secure; SameSite=Lax")
        findings = _check_cookie(attrs, is_https=True)
        httponly_findings = [f for f in findings if f.attribute == "HttpOnly" and f.status == "MISSING"]
        self.assertTrue(len(httponly_findings) > 0)
        self.assertEqual(httponly_findings[0].severity, "MEDIUM")

    def test_missing_samesite(self):
        attrs = self._attrs("session=abc; Secure; HttpOnly")
        findings = _check_cookie(attrs, is_https=True)
        ss_findings = [f for f in findings if f.attribute == "SameSite" and f.status == "MISSING"]
        self.assertTrue(len(ss_findings) > 0)

    def test_samesite_none_without_secure(self):
        attrs = self._attrs("session=abc; SameSite=None; HttpOnly")
        findings = _check_cookie(attrs, is_https=True)
        ss_findings = [f for f in findings if f.attribute == "SameSite" and f.status == "MISCONFIGURED"]
        self.assertTrue(len(ss_findings) > 0)
        self.assertEqual(ss_findings[0].severity, "HIGH")

    def test_samesite_none_with_secure_is_low(self):
        attrs = self._attrs("session=abc; Secure; SameSite=None; HttpOnly")
        findings = _check_cookie(attrs, is_https=True)
        ss_findings = [f for f in findings if f.attribute == "SameSite" and f.status == "MISCONFIGURED"]
        self.assertTrue(len(ss_findings) > 0)
        self.assertEqual(ss_findings[0].severity, "LOW")

    def test_samesite_strict_is_ok(self):
        attrs = self._attrs("session=abc; Secure; HttpOnly; SameSite=Strict")
        findings = _check_cookie(attrs, is_https=True)
        ss_findings = [f for f in findings if f.attribute == "SameSite" and f.status == "PRESENT"]
        self.assertTrue(len(ss_findings) > 0)

    def test_overbroad_domain(self):
        attrs = self._attrs("session=abc; Secure; HttpOnly; SameSite=Lax; Domain=example.com")
        findings = _check_cookie(attrs, is_https=True)
        dom_findings = [f for f in findings if f.attribute == "Domain" and f.status == "MISCONFIGURED"]
        self.assertTrue(len(dom_findings) > 0)

    def test_specific_subdomain_domain_not_flagged(self):
        attrs = self._attrs("session=abc; Secure; HttpOnly; SameSite=Lax; Domain=app.example.com")
        findings = _check_cookie(attrs, is_https=True)
        dom_findings = [f for f in findings if f.attribute == "Domain" and f.status == "MISCONFIGURED"]
        self.assertEqual(dom_findings, [])

    def test_secure_prefix_without_secure_flag(self):
        attrs = self._attrs("__Secure-session=abc; HttpOnly; SameSite=Lax")
        findings = _check_cookie(attrs, is_https=True)
        prefix_findings = [f for f in findings if "__Secure-" in f.attribute and f.status == "MISCONFIGURED"]
        self.assertTrue(len(prefix_findings) > 0)
        self.assertEqual(prefix_findings[0].severity, "HIGH")

    def test_host_prefix_without_required_attrs(self):
        attrs = self._attrs("__Host-session=abc; HttpOnly; SameSite=Lax; Domain=example.com")
        findings = _check_cookie(attrs, is_https=True)
        prefix_findings = [f for f in findings if "__Host-" in f.attribute and f.status == "MISCONFIGURED"]
        self.assertTrue(len(prefix_findings) > 0)

    def test_perfect_cookie_has_no_issues(self):
        attrs = self._attrs("session=abc; Secure; HttpOnly; SameSite=Strict; Path=/")
        findings = _check_cookie(attrs, is_https=True)
        issues = [f for f in findings if f.status in ("MISSING", "MISCONFIGURED")]
        self.assertEqual(issues, [])


class TestAnalyzeCookies(unittest.TestCase):
    def test_error_on_connection_failure(self):
        with patch("cai.tools.web.cookie_analyzer._fetch_cookies", return_value=(-1, False, [])):
            status, findings = _analyze_cookies("https://unreachable.example.com")
        self.assertEqual(status, -1)
        self.assertEqual(findings, [])

    def test_empty_when_no_cookies(self):
        with patch("cai.tools.web.cookie_analyzer._fetch_cookies", return_value=(200, True, [])):
            status, findings = _analyze_cookies("https://example.com")
        self.assertEqual(status, 200)
        self.assertEqual(findings, [])

    def test_missing_httponly_detected(self):
        cookies = ["session=abc; Secure; SameSite=Lax"]
        with patch("cai.tools.web.cookie_analyzer._fetch_cookies", return_value=(200, True, cookies)):
            status, findings = _analyze_cookies("https://example.com")
        httponly_miss = [f for f in findings if f.attribute == "HttpOnly" and f.status == "MISSING"]
        self.assertTrue(len(httponly_miss) > 0)

    def test_adds_https_scheme(self):
        captured = []

        def fake_fetch(url, timeout=8.0):
            captured.append(url)
            return (200, True, [])

        with patch("cai.tools.web.cookie_analyzer._fetch_cookies", side_effect=fake_fetch):
            _analyze_cookies("example.com")
        self.assertTrue(captured[0].startswith("https://"))

    def test_multiple_cookies_analyzed(self):
        cookies = [
            "session=abc; Secure; HttpOnly; SameSite=Strict",
            "tracking=xyz",  # missing everything
        ]
        with patch("cai.tools.web.cookie_analyzer._fetch_cookies", return_value=(200, True, cookies)):
            status, findings = _analyze_cookies("https://example.com")
        cookie_names = {f.cookie_name for f in findings}
        self.assertIn("session", cookie_names)
        self.assertIn("tracking", cookie_names)


class TestRunCookieAnalyzer(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_cookie_analyzer("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_cookie_analyzer("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        with patch("cai.tools.web.cookie_analyzer._analyze_cookies", return_value=(200, [])):
            out = _run_cookie_analyzer("https://example.com")
        self.assertIn("Summary:", out)

    def test_no_cookies_message(self):
        with patch("cai.tools.web.cookie_analyzer._analyze_cookies", return_value=(200, [])):
            out = _run_cookie_analyzer("https://example.com")
        self.assertIn("No Set-Cookie", out)

    def test_comma_separated_targets(self):
        with patch("cai.tools.web.cookie_analyzer._analyze_cookies", return_value=(200, [])) as m:
            _run_cookie_analyzer("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_error_when_connection_fails(self):
        with patch("cai.tools.web.cookie_analyzer._analyze_cookies", return_value=(-1, [])):
            out = _run_cookie_analyzer("https://unreachable.example.com")
        self.assertIn("ERROR", out)

    def test_note_shown_when_issues_found(self):
        vuln = CookieFinding("session", "HttpOnly", "MEDIUM", "MISSING", "missing httponly")
        with patch("cai.tools.web.cookie_analyzer._analyze_cookies", return_value=(200, [vuln])):
            out = _run_cookie_analyzer("https://example.com")
        self.assertIn("Note:", out)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("cookie_analyzer", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
