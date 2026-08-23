"""Tests for Host Header Injection checker."""
import unittest
from unittest.mock import patch, call

from cai.tools.web.host_header_injection import (
    HHIFinding,
    _check_host_header_injection,
    _check_probe,
    _run_host_header_injection,
)


class TestCheckProbe(unittest.TestCase):
    def _mock_get(self, status, headers, body):
        return patch("cai.tools.web.host_header_injection._get", return_value=(status, headers, body))

    def test_reflected_when_canary_in_body(self):
        body = "welcome to evil.attacker.example.com please click here"
        with self._mock_get(200, {}, body):
            f = _check_probe("https://example.com", "test", "evil.attacker.example.com",
                             200, "baseline body", 5.0)
        self.assertEqual(f.verdict, "REFLECTED")
        self.assertEqual(f.severity, "HIGH")

    def test_redirect_when_canary_in_location(self):
        headers = {"location": "https://evil.attacker.example.com/"}
        with self._mock_get(302, headers, ""):
            f = _check_probe("https://example.com", "test", "evil.attacker.example.com",
                             200, "baseline body", 5.0)
        self.assertEqual(f.verdict, "REDIRECT")
        self.assertEqual(f.severity, "HIGH")

    def test_status_change_detection(self):
        with self._mock_get(500, {}, "error"):
            f = _check_probe("https://example.com", "test", "evil.attacker.example.com",
                             200, "baseline body", 5.0)
        self.assertEqual(f.verdict, "STATUS_CHANGE")
        self.assertEqual(f.severity, "LOW")

    def test_safe_when_no_indicators(self):
        with self._mock_get(200, {}, "normal page content"):
            f = _check_probe("https://example.com", "test", "evil.attacker.example.com",
                             200, "baseline body", 5.0)
        self.assertEqual(f.verdict, "SAFE")

    def test_safe_when_connection_fails(self):
        with self._mock_get(-1, {}, ""):
            f = _check_probe("https://example.com", "test", "evil.attacker.example.com",
                             200, "baseline body", 5.0)
        self.assertEqual(f.verdict, "SAFE")

    def test_case_insensitive_body_check(self):
        body = "reset password link: https://EVIL.ATTACKER.EXAMPLE.COM/reset?token=abc"
        with self._mock_get(200, {}, body.lower()):  # _get returns body.lower()
            f = _check_probe("https://example.com", "test", "evil.attacker.example.com",
                             200, "baseline body", 5.0)
        self.assertEqual(f.verdict, "REFLECTED")


class TestCheckHostHeaderInjection(unittest.TestCase):
    def test_adds_https_scheme_when_missing(self):
        captured_urls = []

        def fake_get(url, host_override="", extra_header=None, timeout=8.0):
            captured_urls.append(url)
            return (200, {}, "safe content")

        with patch("cai.tools.web.host_header_injection._get", side_effect=fake_get):
            _check_host_header_injection("example.com")
        self.assertTrue(all(u.startswith("https://") for u in captured_urls))

    def test_returns_safe_when_baseline_fails(self):
        with patch("cai.tools.web.host_header_injection._get", return_value=(-1, {}, "")):
            findings = _check_host_header_injection("https://unreachable.example.com")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].verdict, "SAFE")

    def test_runs_multiple_probes(self):
        def fake_get(url, host_override="", extra_header=None, timeout=8.0):
            return (200, {}, "normal content")

        with patch("cai.tools.web.host_header_injection._get", side_effect=fake_get):
            findings = _check_host_header_injection("https://example.com")
        # Should have one finding per probe
        self.assertGreater(len(findings), 3)

    def test_returns_reflected_finding(self):
        call_count = [0]

        def fake_get(url, host_override="", extra_header=None, timeout=8.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return (200, {}, "baseline content")
            # Subsequent calls reflect canary
            return (200, {}, "content with evil.attacker.example.com link")

        with patch("cai.tools.web.host_header_injection._get", side_effect=fake_get):
            findings = _check_host_header_injection("https://example.com")
        reflected = [f for f in findings if f.verdict == "REFLECTED"]
        self.assertTrue(len(reflected) > 0)


class TestRunHostHeaderInjection(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_host_header_injection("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_host_header_injection("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = HHIFinding("test", "INFO", "SAFE", "no issue")
        with patch("cai.tools.web.host_header_injection._check_host_header_injection",
                   return_value=[safe]):
            out = _run_host_header_injection("https://example.com")
        self.assertIn("Summary:", out)

    def test_reflected_shown_in_output(self):
        vuln = HHIFinding("arbitrary domain", "HIGH", "REFLECTED", "canary reflected")
        with patch("cai.tools.web.host_header_injection._check_host_header_injection",
                   return_value=[vuln]):
            out = _run_host_header_injection("https://example.com")
        self.assertIn("REFLECTED", out)
        self.assertIn("!!!", out)

    def test_note_shown_when_issues_found(self):
        vuln = HHIFinding("X-Forwarded-Host injection", "HIGH", "REFLECTED", "reflected")
        with patch("cai.tools.web.host_header_injection._check_host_header_injection",
                   return_value=[vuln]):
            out = _run_host_header_injection("https://example.com")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = HHIFinding("t", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.host_header_injection._check_host_header_injection",
                   return_value=[safe]) as m:
            _run_host_header_injection("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("host_header_injection", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
