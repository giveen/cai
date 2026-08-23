"""Tests for path traversal vulnerability probe."""
import unittest
from unittest.mock import patch

from cai.tools.web.path_traversal import (
    TraversalFinding,
    _build_probe_url,
    _check_path_traversal,
    _encodings,
    _run_path_traversal,
)


class TestEncodings(unittest.TestCase):
    def test_returns_four_variants(self):
        variants = _encodings("../../../../etc/passwd")
        self.assertEqual(len(variants), 4)

    def test_plain_is_unchanged(self):
        path = "../../../../etc/passwd"
        variants = dict(_encodings(path))
        self.assertEqual(variants["plain"], path)

    def test_url_encoded_different_from_plain(self):
        path = "../../../../etc/passwd"
        variants = dict(_encodings(path))
        self.assertNotEqual(variants["url-encoded"], path)
        self.assertIn("%2F", variants["url-encoded"].upper())

    def test_null_byte_suffix_present(self):
        path = "../../../../etc/passwd"
        variants = dict(_encodings(path))
        self.assertIn("%00", variants["null-byte"])


class TestBuildProbeUrl(unittest.TestCase):
    def test_appends_to_path(self):
        urls = _build_probe_url("https://example.com/files/report.pdf", "../../../../etc/passwd")
        self.assertTrue(any("etc/passwd" in u for u in urls))

    def test_replaces_last_segment(self):
        urls = _build_probe_url("https://example.com/files/report.pdf", "../../../../etc/passwd")
        self.assertGreater(len(urls), 1)

    def test_injects_into_query_param(self):
        urls = _build_probe_url(
            "https://example.com/view?file=report.pdf",
            "../../../../etc/passwd",
        )
        param_injected = [u for u in urls if "file=" in u and "etc%2Fpasswd" in u or "etc/passwd" in u]
        self.assertTrue(len(param_injected) > 0)

    def test_no_query_no_param_injection(self):
        urls = _build_probe_url("https://example.com/files/", "../../../../etc/passwd")
        self.assertGreater(len(urls), 0)


class TestCheckPathTraversal(unittest.TestCase):
    def test_confirmed_when_signature_in_body(self):
        def fake_get(url, timeout=6.0):
            if "passwd" in url:
                return 200, "root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:"
            return 200, "normal content"

        with patch("cai.tools.web.path_traversal._get", side_effect=fake_get):
            findings = _check_path_traversal("https://example.com/view?file=test.txt")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)
        self.assertEqual(confirmed[0].severity, "CRITICAL")

    def test_safe_when_no_signatures_found(self):
        with patch("cai.tools.web.path_traversal._get", return_value=(200, "normal page content no secrets")):
            findings = _check_path_traversal("https://example.com/view?file=test.txt")
        confirmed_or_probable = [f for f in findings if f.status in ("CONFIRMED", "PROBABLE")]
        self.assertEqual(confirmed_or_probable, [])

    def test_probable_when_traversal_returns_200_but_baseline_was_not_200(self):
        call_count = [0]

        def fake_get(url, timeout=6.0):
            call_count[0] += 1
            if call_count[0] == 1:  # baseline
                return 404, "not found"
            return 200, "some file content without known signatures"

        with patch("cai.tools.web.path_traversal._get", side_effect=fake_get):
            findings = _check_path_traversal("https://example.com/files/report.pdf")
        probable = [f for f in findings if f.status == "PROBABLE"]
        self.assertTrue(len(probable) > 0)

    def test_safe_when_connection_fails(self):
        with patch("cai.tools.web.path_traversal._get", return_value=(-1, "")):
            findings = _check_path_traversal("https://unreachable.example.com")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")

    def test_adds_https_scheme(self):
        calls = []

        def fake_get(url, timeout=6.0):
            calls.append(url)
            return 200, "safe content"

        with patch("cai.tools.web.path_traversal._get", side_effect=fake_get):
            _check_path_traversal("example.com")
        self.assertTrue(all(u.startswith("https://") for u in calls))

    def test_stops_after_confirmed(self):
        confirmed_count = [0]

        def fake_get(url, timeout=6.0):
            if "passwd" in url.lower():
                confirmed_count[0] += 1
                return 200, "root:x:0:0:root:/root:/bin/bash"
            return 200, "normal"

        with patch("cai.tools.web.path_traversal._get", side_effect=fake_get):
            findings = _check_path_traversal("https://example.com/view")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        # Should stop after first confirmed target's probes, not run all targets
        self.assertEqual(len(confirmed), 1)


class TestRunPathTraversal(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_path_traversal("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_path_traversal("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = TraversalFinding("https://example.com", "", "none", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.path_traversal._check_path_traversal", return_value=[safe]):
            out = _run_path_traversal("https://example.com")
        self.assertIn("Summary:", out)

    def test_confirmed_shown_in_output(self):
        vuln = TraversalFinding(
            "https://example.com/view?file=../../etc/passwd",
            "../../../../etc/passwd",
            "plain",
            "CRITICAL",
            "CONFIRMED",
            "root: found in response",
        )
        with patch("cai.tools.web.path_traversal._check_path_traversal", return_value=[vuln]):
            out = _run_path_traversal("https://example.com")
        self.assertIn("CONFIRMED", out)
        self.assertIn("CRITICAL", out)

    def test_note_shown_when_confirmed(self):
        vuln = TraversalFinding(
            "u", "../../../../etc/passwd", "plain", "CRITICAL", "CONFIRMED", "root: found"
        )
        with patch("cai.tools.web.path_traversal._check_path_traversal", return_value=[vuln]):
            out = _run_path_traversal("https://example.com")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = TraversalFinding("u", "", "none", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.path_traversal._check_path_traversal", return_value=[safe]) as m:
            _run_path_traversal("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("path_traversal", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
