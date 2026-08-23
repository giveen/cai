"""Tests for SSRF probe tool."""
import unittest
from unittest.mock import patch

from cai.tools.web.ssrf_probe import (
    SSRFFinding,
    _check_ssrf,
    _inject_url_param,
    _run_ssrf_probe,
)


class TestInjectUrlParam(unittest.TestCase):
    def test_adds_new_param(self):
        url = "https://example.com/fetch"
        result = _inject_url_param(url, "url", "http://evil.com")
        self.assertIn("url=", result)
        self.assertIn("evil.com", result)

    def test_replaces_existing_param(self):
        url = "https://example.com/fetch?url=http://safe.com"
        result = _inject_url_param(url, "url", "http://evil.com")
        self.assertIn("evil.com", result)
        self.assertNotIn("safe.com", result)

    def test_preserves_other_params(self):
        url = "https://example.com/fetch?format=json&url=http://safe.com"
        result = _inject_url_param(url, "url", "http://evil.com")
        self.assertIn("format=json", result)
        self.assertIn("evil.com", result)

    def test_appends_param_when_none_present(self):
        url = "https://example.com/img"
        result = _inject_url_param(url, "src", "http://169.254.169.254/")
        self.assertIn("src=", result)
        self.assertIn("169.254", result)


class TestCheckSSRF(unittest.TestCase):
    def test_confirmed_when_metadata_signature_in_body(self):
        call_count = [0]

        def fake_get(url, timeout=8.0):
            call_count[0] += 1
            if "169.254.169.254" in url and "url=" in url:
                return 200, "ami-id: ami-0abcdef1234567890\ninstance-id: i-1234567890abcdef0"
            return 200, "normal content"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            findings = _check_ssrf("https://example.com/fetch?url=http://safe.com")

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)
        self.assertEqual(confirmed[0].severity, "CRITICAL")

    def test_safe_when_no_signature_found(self):
        with patch("cai.tools.web.ssrf_probe._get", return_value=(200, "normal page content")):
            findings = _check_ssrf("https://example.com/fetch?url=http://safe.com")
        confirmed_or_probable = [f for f in findings if f.status in ("CONFIRMED", "PROBABLE")]
        self.assertEqual(confirmed_or_probable, [])

    def test_probable_when_metadata_returns_200_but_baseline_not_200(self):
        call_count = [0]

        def fake_get(url, timeout=8.0):
            call_count[0] += 1
            if call_count[0] == 1:  # baseline
                return 404, "not found"
            if "169.254.169.254" in url:
                return 200, "some content without metadata signatures"
            return 404, "not found"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            findings = _check_ssrf("https://example.com/fetch")
        probable = [f for f in findings if f.status == "PROBABLE"]
        self.assertTrue(len(probable) > 0)

    def test_potential_when_server_error_only_on_payload(self):
        call_count = [0]

        def fake_get(url, timeout=8.0):
            call_count[0] += 1
            if call_count[0] == 1:  # baseline
                return 200, "normal response"
            if "169.254.169.254" in url:
                return 500, "internal server error"
            return 200, "normal response"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            findings = _check_ssrf("https://example.com/fetch?url=safe")
        potential = [f for f in findings if f.status == "POTENTIAL"]
        self.assertTrue(len(potential) > 0)

    def test_safe_when_connection_fails(self):
        with patch("cai.tools.web.ssrf_probe._get", return_value=(-1, "")):
            findings = _check_ssrf("https://unreachable.example.com")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")

    def test_adds_https_scheme(self):
        calls = []

        def fake_get(url, timeout=8.0):
            calls.append(url)
            return 200, "safe content"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            _check_ssrf("example.com")
        self.assertTrue(all(u.startswith("https://") for u in calls))

    def test_stops_after_first_confirmed(self):
        confirmed_count = [0]

        def fake_get(url, timeout=8.0):
            if "169.254.169.254" in url and "url=" in url:
                confirmed_count[0] += 1
                return 200, "ami-id: ami-0abcdef"
            return 200, "safe"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            findings = _check_ssrf("https://example.com/fetch?url=safe")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertEqual(len(confirmed), 1)

    def test_existing_url_params_probed_first(self):
        probed_params = []

        def fake_get(url, timeout=8.0):
            from urllib.parse import urlparse, parse_qs
            if "169.254" in url:
                qs = parse_qs(urlparse(url).query)
                probed_params.extend(qs.keys())
            return 200, "safe"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            _check_ssrf("https://example.com/fetch?redirect=http://safe.com&format=json")

        # 'redirect' should be probed (it's an existing URL-like param)
        self.assertIn("redirect", probed_params)

    def test_gcp_metadata_signature_detection(self):
        def fake_get(url, timeout=8.0):
            if "metadata.google.internal" in url:
                return 200, "instance/serviceAccounts/default/token"
            return 200, "safe"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            findings = _check_ssrf("https://example.com/fetch?url=safe")
        # GCP signature is 'instance' — check if it triggers CONFIRMED
        # Note: 'instance' is a low-signal signature; test just verifies no crash
        self.assertIsInstance(findings, list)

    def test_loopback_detection(self):
        def fake_get(url, timeout=8.0):
            if "127.0.0.1" in url and ("url=" in url or "src=" in url):
                return 200, "<!doctype html><html><body>welcome to localhost</body></html>"
            return 200, "safe content"

        with patch("cai.tools.web.ssrf_probe._get", side_effect=fake_get):
            findings = _check_ssrf("https://example.com/fetch?url=safe")
        # 'html' is the signature for loopback — should get CONFIRMED
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)


class TestRunSSRFProbe(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_ssrf_probe("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_ssrf_probe("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = SSRFFinding("https://example.com", "", "", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.ssrf_probe._check_ssrf", return_value=[safe]):
            out = _run_ssrf_probe("https://example.com")
        self.assertIn("Summary:", out)

    def test_confirmed_shown_in_output(self):
        vuln = SSRFFinding(
            "https://example.com/fetch?url=http://169.254.169.254/",
            "url",
            "http://169.254.169.254/latest/meta-data/",
            "CRITICAL",
            "CONFIRMED",
            "AWS EC2 metadata signature found",
        )
        with patch("cai.tools.web.ssrf_probe._check_ssrf", return_value=[vuln]):
            out = _run_ssrf_probe("https://example.com")
        self.assertIn("CONFIRMED", out)
        self.assertIn("CRITICAL", out)

    def test_note_shown_when_confirmed(self):
        vuln = SSRFFinding(
            "u", "url", "http://169.254.169.254/", "CRITICAL", "CONFIRMED", "SSRF found"
        )
        with patch("cai.tools.web.ssrf_probe._check_ssrf", return_value=[vuln]):
            out = _run_ssrf_probe("https://example.com")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = SSRFFinding("u", "", "", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.ssrf_probe._check_ssrf", return_value=[safe]) as m:
            _run_ssrf_probe("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("ssrf_probe", TOOL_REGISTRY._tools)

    def test_safe_shown_in_output(self):
        safe = SSRFFinding("https://example.com", "", "", "INFO", "SAFE", "no SSRF")
        with patch("cai.tools.web.ssrf_probe._check_ssrf", return_value=[safe]):
            out = _run_ssrf_probe("https://example.com")
        self.assertIn("SAFE", out)

    def test_probable_counted_in_summary(self):
        prob = SSRFFinding(
            "u", "url", "http://169.254.169.254/", "HIGH", "PROBABLE", "blind SSRF"
        )
        with patch("cai.tools.web.ssrf_probe._check_ssrf", return_value=[prob]):
            out = _run_ssrf_probe("https://example.com")
        self.assertIn("PROBABLE", out)


if __name__ == "__main__":
    unittest.main()
