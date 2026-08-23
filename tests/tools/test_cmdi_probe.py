"""Tests for OS command injection (CMDi) probe tool."""
import unittest
from unittest.mock import patch

from cai.tools.web.cmdi_probe import (
    CMDiFinding,
    _check_cmdi,
    _inject_param,
    _run_cmdi_probe,
)


class TestInjectParam(unittest.TestCase):
    def test_replaces_existing_param(self):
        url = "https://example.com/ping?host=8.8.8.8"
        result = _inject_param(url, "host", "8.8.8.8; sleep 5")
        self.assertIn("host=", result)
        self.assertNotIn("8.8.8.8&", result)

    def test_adds_new_param(self):
        url = "https://example.com/run"
        result = _inject_param(url, "cmd", "test; sleep 5")
        self.assertIn("cmd=", result)

    def test_preserves_other_params(self):
        url = "https://example.com/run?format=json&input=test"
        result = _inject_param(url, "input", "payload")
        self.assertIn("format=json", result)


class TestCheckCMDi(unittest.TestCase):
    def test_returns_safe_when_no_params(self):
        with patch("cai.tools.web.cmdi_probe._get", return_value=(200, 0.1)):
            findings = _check_cmdi("https://example.com/page")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")
        self.assertIn("No query parameters", findings[0].detail)

    def test_returns_safe_when_connection_fails(self):
        with patch("cai.tools.web.cmdi_probe._get", return_value=(-1, 0.0)):
            findings = _check_cmdi("https://unreachable.example.com/run?cmd=test")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")

    def test_confirmed_on_strong_time_delay(self):
        call_count = [0]

        def fake_get(url, timeout=15.0):
            call_count[0] += 1
            if call_count[0] == 1:  # baseline
                return 200, 0.1
            if "sleep" in url.lower() or "%3B" in url or "%26" in url:
                return 200, 5.5  # strong delay
            return 200, 0.1

        with patch("cai.tools.web.cmdi_probe._get", side_effect=fake_get):
            findings = _check_cmdi("https://example.com/ping?host=localhost")

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)
        self.assertEqual(confirmed[0].severity, "CRITICAL")

    def test_probable_on_moderate_delay(self):
        call_count = [0]

        def fake_get(url, timeout=15.0):
            call_count[0] += 1
            if call_count[0] == 1:  # baseline
                return 200, 0.1
            # Return moderate delay but below CONFIRMED threshold
            return 200, 4.0  # 4s / 0.1 = 40× ratio → PROBABLE (4s >= 3.5s, 40 >= 4)

        with patch("cai.tools.web.cmdi_probe._get", side_effect=fake_get):
            findings = _check_cmdi("https://example.com/run?cmd=test")

        # Should get CONFIRMED since 4.0s >= 4.0s (MIN_DELAY=4) and ratio=40 >= 5
        good = [f for f in findings if f.status in ("CONFIRMED", "PROBABLE")]
        self.assertTrue(len(good) > 0)

    def test_adds_https_scheme(self):
        calls = []

        def fake_get(url, timeout=15.0):
            calls.append(url)
            return 200, 0.1

        with patch("cai.tools.web.cmdi_probe._get", side_effect=fake_get):
            _check_cmdi("example.com/run?cmd=test")
        self.assertTrue(all(u.startswith("https://") for u in calls))

    def test_skips_when_baseline_is_slow(self):
        call_count = [0]

        def fake_get(url, timeout=15.0):
            call_count[0] += 1
            return 200, 2.0  # always slow

        with patch("cai.tools.web.cmdi_probe._get", side_effect=fake_get):
            findings = _check_cmdi("https://example.com/run?cmd=test")

        # Should skip the param due to slow baseline
        safe = [f for f in findings if f.status == "SAFE"]
        self.assertTrue(len(safe) > 0)
        self.assertIn("too slow", safe[0].detail)

    def test_stops_after_first_confirmed_per_param(self):
        call_count = [0]
        confirmed_count = [0]

        def fake_get(url, timeout=15.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return 200, 0.1
            confirmed_count[0] += 1
            return 200, 6.0

        with patch("cai.tools.web.cmdi_probe._get", side_effect=fake_get):
            findings = _check_cmdi("https://example.com/run?cmd=test")

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        # Should stop at first CONFIRMED, not try all payloads
        self.assertEqual(len(confirmed), 1)

    def test_safe_when_no_delay(self):
        with patch("cai.tools.web.cmdi_probe._get", return_value=(200, 0.05)):
            findings = _check_cmdi("https://example.com/run?cmd=test")
        confirmed_or_probable = [f for f in findings if f.status in ("CONFIRMED", "PROBABLE")]
        self.assertEqual(confirmed_or_probable, [])

    def test_fallback_to_http(self):
        https_calls = [0]

        def fake_get(url, timeout=15.0):
            if url.startswith("https://"):
                https_calls[0] += 1
                return -1, 0.0
            return 200, 0.1

        with patch("cai.tools.web.cmdi_probe._get", side_effect=fake_get):
            findings = _check_cmdi("https://example.com/run?cmd=test")
        self.assertTrue(https_calls[0] >= 1)


class TestRunCMDiProbe(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_cmdi_probe("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_cmdi_probe("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = CMDiFinding("u", "", "", "INFO", "SAFE", 0, 0.1, "ok")
        with patch("cai.tools.web.cmdi_probe._check_cmdi", return_value=[safe]):
            out = _run_cmdi_probe("https://example.com?cmd=test")
        self.assertIn("Summary:", out)

    def test_confirmed_shown_in_output(self):
        vuln = CMDiFinding(
            "https://example.com/run?cmd=test%3B+sleep+5",
            "cmd", "test; sleep 5", "CRITICAL", "CONFIRMED", 5.5, 0.1, "found"
        )
        with patch("cai.tools.web.cmdi_probe._check_cmdi", return_value=[vuln]):
            out = _run_cmdi_probe("https://example.com?cmd=test")
        self.assertIn("CONFIRMED", out)
        self.assertIn("CRITICAL", out)

    def test_note_shown_when_confirmed(self):
        vuln = CMDiFinding("u", "cmd", "payload", "CRITICAL", "CONFIRMED", 5.5, 0.1, "found")
        with patch("cai.tools.web.cmdi_probe._check_cmdi", return_value=[vuln]):
            out = _run_cmdi_probe("https://example.com?cmd=test")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = CMDiFinding("u", "", "", "INFO", "SAFE", 0, 0.1, "ok")
        with patch("cai.tools.web.cmdi_probe._check_cmdi", return_value=[safe]) as m:
            _run_cmdi_probe("https://a.com?cmd=1, https://b.com?cmd=2")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("cmdi_probe", TOOL_REGISTRY._tools)

    def test_timing_shown_in_output(self):
        vuln = CMDiFinding(
            "u", "cmd", "test; sleep 5", "CRITICAL", "CONFIRMED", 5.5, 0.1, "found"
        )
        with patch("cai.tools.web.cmdi_probe._check_cmdi", return_value=[vuln]):
            out = _run_cmdi_probe("https://example.com?cmd=test")
        self.assertIn("Timing", out)


if __name__ == "__main__":
    unittest.main()
