"""Tests for SQL injection probe tool."""
import unittest
from unittest.mock import patch

from cai.tools.web.sqli_probe import (
    SQLiFinding,
    _check_sqli,
    _check_error_based,
    _check_boolean_based,
    _check_time_based,
    _inject_param,
    _run_sqli_probe,
)


class TestInjectParam(unittest.TestCase):
    def test_replaces_existing_param(self):
        url = "https://example.com/search?q=test"
        result = _inject_param(url, "q", "' OR '1'='1")
        self.assertIn("q=", result)
        self.assertNotIn("test", result)

    def test_adds_new_param(self):
        url = "https://example.com/page"
        result = _inject_param(url, "id", "1")
        self.assertIn("id=1", result)

    def test_preserves_other_params(self):
        url = "https://example.com/search?q=test&lang=en"
        result = _inject_param(url, "q", "payload")
        self.assertIn("lang=en", result)

    def test_preserves_base_path(self):
        url = "https://example.com/search?q=test"
        result = _inject_param(url, "q", "payload")
        self.assertIn("/search", result)


class TestCheckErrorBased(unittest.TestCase):
    def _has_sqli_payload(self, url: str) -> bool:
        """Check if URL contains a SQLi payload (handles percent-encoding)."""
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)
        for vals in qs.values():
            for v in vals:
                if "'" in v or '"' in v or "--" in v or "or" in v.lower():
                    return True
        return False

    def test_returns_finding_on_mysql_error(self):
        def fake_request(url, method="GET", body="", timeout=8.0):
            if self._has_sqli_payload(url):
                return 500, "you have an error in your sql syntax near 'or'", 0.1
            return 200, "results", 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            finding = _check_error_based("https://example.com/search?q=test", "q", "results", 8.0)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.technique, "error")
        self.assertEqual(finding.severity, "CRITICAL")
        self.assertEqual(finding.status, "CONFIRMED")
        self.assertIn("MySQL", finding.detail)

    def test_returns_none_when_no_error(self):
        with patch("cai.tools.web.sqli_probe._request", return_value=(200, "normal content", 0.1)):
            finding = _check_error_based("https://example.com/search?q=test", "q", "normal content", 8.0)
        self.assertIsNone(finding)

    def test_mssql_signature_detected(self):
        def fake_request(url, method="GET", body="", timeout=8.0):
            if self._has_sqli_payload(url):
                return 500, "unclosed quotation mark after the character string", 0.1
            return 200, "normal", 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            finding = _check_error_based("https://example.com/search?q=test", "q", "normal", 8.0)
        self.assertIsNotNone(finding)
        self.assertIn("MSSQL", finding.detail)

    def test_postgres_signature_detected(self):
        def fake_request(url, method="GET", body="", timeout=8.0):
            if self._has_sqli_payload(url):
                return 500, "pg::syntaxerror: unterminated quoted string at or near", 0.1
            return 200, "normal", 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            finding = _check_error_based("https://example.com/search?q=test", "q", "normal", 8.0)
        self.assertIsNotNone(finding)

    def test_no_finding_when_error_in_baseline(self):
        with patch("cai.tools.web.sqli_probe._request",
                   return_value=(500, "you have an error in your sql syntax near 'or'", 0.1)):
            # Error is already in baseline_body — should not flag as new
            finding = _check_error_based(
                "https://example.com/search?q=test", "q",
                "you have an error in your sql syntax near 'or'",  # baseline already has it
                8.0
            )
        self.assertIsNone(finding)


class TestCheckBooleanBased(unittest.TestCase):
    def _get_param_value(self, url: str, param: str) -> str:
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)
        vals = qs.get(param, [""])
        return vals[0] if vals else ""

    def test_returns_finding_when_responses_differ_significantly(self):
        # true condition returns many results, false returns none
        def fake_request(url, method="GET", body="", timeout=8.0):
            val = self._get_param_value(url, "q")
            if "1=1" in val or "OR 1=1" in val.upper():  # true payload
                return 200, "result1 result2 result3 result4 result5 content " * 20, 0.1
            if "1=2" in val or "OR 1=2" in val.upper():  # false payload
                return 200, "no results found", 0.1
            return 200, "default results content " * 10, 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            finding = _check_boolean_based(
                "https://example.com/search?q=test", "q",
                "default results content " * 10,  # baseline
                200, 8.0
            )
        self.assertIsNotNone(finding)
        self.assertEqual(finding.technique, "boolean")
        self.assertEqual(finding.severity, "CRITICAL")

    def test_returns_none_when_responses_same_length(self):
        with patch("cai.tools.web.sqli_probe._request", return_value=(200, "same content everywhere", 0.1)):
            finding = _check_boolean_based(
                "https://example.com/search?q=test", "q",
                "same content everywhere", 200, 8.0
            )
        self.assertIsNone(finding)


class TestCheckTimeBased(unittest.TestCase):
    def test_returns_high_finding_on_long_delay(self):
        baseline_elapsed = 0.1

        def fake_request(url, method="GET", body="", timeout=15.0):
            if "SLEEP" in url.upper() or "WAITFOR" in url.upper():
                return 200, "response", 4.0  # 40× baseline
            return 200, "normal", 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            finding = _check_time_based(
                "https://example.com/search?q=test", "q", baseline_elapsed, 8.0
            )
        self.assertIsNotNone(finding)
        self.assertEqual(finding.severity, "HIGH")
        self.assertEqual(finding.status, "PROBABLE")

    def test_returns_none_when_baseline_already_slow(self):
        finding = _check_time_based(
            "https://example.com/search?q=test", "q",
            2.0,  # already slow
            8.0
        )
        self.assertIsNone(finding)

    def test_returns_none_when_no_delay(self):
        with patch("cai.tools.web.sqli_probe._request", return_value=(200, "response", 0.1)):
            finding = _check_time_based(
                "https://example.com/search?q=test", "q", 0.1, 8.0
            )
        self.assertIsNone(finding)


class TestCheckSQLi(unittest.TestCase):
    def test_returns_safe_when_no_params(self):
        with patch("cai.tools.web.sqli_probe._request", return_value=(200, "normal", 0.1)):
            findings = _check_sqli("https://example.com/page")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")
        self.assertIn("No query parameters", findings[0].detail)

    def test_returns_safe_when_connection_fails(self):
        with patch("cai.tools.web.sqli_probe._request", return_value=(-1, "", 0.0)):
            findings = _check_sqli("https://unreachable.example.com/search?q=test")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")

    def test_adds_https_scheme(self):
        calls = []

        def fake_request(url, method="GET", body="", timeout=8.0):
            calls.append(url)
            return 200, "normal", 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            _check_sqli("example.com/search?q=test")
        self.assertTrue(all(u.startswith("https://") for u in calls))

    def test_confirmed_on_mysql_error(self):
        import urllib.parse

        def has_sqli(url):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query, keep_blank_values=True)
            for vals in qs.values():
                for v in vals:
                    if "'" in v or '"' in v or "--" in v:
                        return True
            return False

        def fake_request(url, method="GET", body="", timeout=8.0):
            if has_sqli(url):
                return 500, "you have an error in your sql syntax near '", 0.1
            return 200, "normal results", 0.1

        with patch("cai.tools.web.sqli_probe._request", side_effect=fake_request):
            findings = _check_sqli("https://example.com/search?q=test")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)

    def test_safe_when_no_anomaly(self):
        with patch("cai.tools.web.sqli_probe._request", return_value=(200, "normal content", 0.1)):
            findings = _check_sqli("https://example.com/search?q=test")
        confirmed_or_probable = [f for f in findings if f.status in ("CONFIRMED", "PROBABLE")]
        self.assertEqual(confirmed_or_probable, [])


class TestRunSQLiProbe(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_sqli_probe("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_sqli_probe("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = SQLiFinding("u", "q", "error", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.sqli_probe._check_sqli", return_value=[safe]):
            out = _run_sqli_probe("https://example.com?q=test")
        self.assertIn("Summary:", out)

    def test_confirmed_shown_in_output(self):
        vuln = SQLiFinding(
            "https://example.com/search?q='",
            "q", "error", "CRITICAL", "CONFIRMED", "MySQL error found"
        )
        with patch("cai.tools.web.sqli_probe._check_sqli", return_value=[vuln]):
            out = _run_sqli_probe("https://example.com?q=test")
        self.assertIn("CONFIRMED", out)
        self.assertIn("CRITICAL", out)

    def test_note_shown_when_confirmed(self):
        vuln = SQLiFinding("u", "q", "error", "CRITICAL", "CONFIRMED", "found")
        with patch("cai.tools.web.sqli_probe._check_sqli", return_value=[vuln]):
            out = _run_sqli_probe("https://example.com?q=test")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = SQLiFinding("u", "q", "error", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.sqli_probe._check_sqli", return_value=[safe]) as m:
            _run_sqli_probe("https://a.com?q=1, https://b.com?id=2")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("sqli_probe", TOOL_REGISTRY._tools)

    def test_technique_shown_in_output(self):
        vuln = SQLiFinding(
            "u", "q", "time", "HIGH", "PROBABLE", "time delay detected"
        )
        with patch("cai.tools.web.sqli_probe._check_sqli", return_value=[vuln]):
            out = _run_sqli_probe("https://example.com?q=test")
        self.assertIn("time", out)


if __name__ == "__main__":
    unittest.main()
