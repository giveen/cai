"""Tests for open redirect vulnerability checker."""
import unittest
from unittest.mock import patch

from cai.tools.web.open_redirect import (
    RedirectFinding,
    _check_open_redirect,
    _check_param,
    _inject_param,
    _run_open_redirect,
)


class TestInjectParam(unittest.TestCase):
    def test_adds_new_param(self):
        url = _inject_param("https://example.com/login", "next", "https://evil.example.com/")
        self.assertIn("next=", url)
        self.assertIn("evil.example.com", url)

    def test_replaces_existing_param(self):
        url = _inject_param("https://example.com/login?next=%2F", "next", "https://evil.example.com/")
        self.assertIn("evil.example.com", url)
        self.assertEqual(url.count("next="), 1)

    def test_preserves_other_params(self):
        url = _inject_param("https://example.com/login?foo=bar&next=/", "next", "https://evil.example.com/")
        self.assertIn("foo=bar", url)
        self.assertIn("evil.example.com", url)


class TestCheckParam(unittest.TestCase):
    def test_confirmed_when_location_contains_canary(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(302, {"location": "https://evil.example.com/"})):
            f = _check_param("https://example.com/login", "next", 5.0)
        self.assertIsNotNone(f)
        self.assertEqual(f.verdict, "CONFIRMED")
        self.assertEqual(f.severity, "HIGH")
        self.assertIn("evil.example.com", f.detail)

    def test_probable_when_redirect_but_different_location(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(302, {"location": "https://example.com/home"})):
            f = _check_param("https://example.com/login", "next", 5.0)
        self.assertIsNotNone(f)
        self.assertEqual(f.verdict, "PROBABLE")
        self.assertEqual(f.severity, "MEDIUM")

    def test_potential_when_redirect_no_location(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(302, {})):
            f = _check_param("https://example.com/login", "next", 5.0)
        self.assertIsNotNone(f)
        self.assertEqual(f.verdict, "POTENTIAL")

    def test_none_when_no_redirect(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(200, {})):
            f = _check_param("https://example.com/login", "next", 5.0)
        self.assertIsNone(f)

    def test_none_on_connection_failure(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(-1, {})):
            f = _check_param("https://example.com/login", "next", 5.0)
        self.assertIsNone(f)

    def test_confirmed_on_301(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(301, {"location": "https://evil.example.com/"})):
            f = _check_param("https://example.com/redir", "url", 5.0)
        self.assertEqual(f.verdict, "CONFIRMED")

    def test_confirmed_on_307(self):
        with patch("cai.tools.web.open_redirect._get", return_value=(307, {"location": "https://evil.example.com/x"})):
            f = _check_param("https://example.com/redir", "redirect", 5.0)
        self.assertEqual(f.verdict, "CONFIRMED")


class TestCheckOpenRedirect(unittest.TestCase):
    def test_adds_https_scheme_when_missing(self):
        captured = []

        def fake_check_param(url, param, timeout):
            captured.append(url)
            return None

        with patch("cai.tools.web.open_redirect._check_param", side_effect=fake_check_param):
            _check_open_redirect("example.com")
        self.assertTrue(all(u.startswith("https://") for u in captured))

    def test_returns_confirmed_finding(self):
        vuln = RedirectFinding(
            url="https://example.com/login?next=https%3A%2F%2Fevil.example.com%2F",
            param="next",
            payload="https://evil.example.com/",
            verdict="CONFIRMED",
            severity="HIGH",
            detail="redirect confirmed",
        )

        def fake_check(url, param, timeout):
            if param == "next":
                return vuln
            return None

        with patch("cai.tools.web.open_redirect._check_param", side_effect=fake_check):
            findings = _check_open_redirect("https://example.com/login?next=/")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].verdict, "CONFIRMED")

    def test_stops_after_confirmed(self):
        call_count = [0]
        vuln = RedirectFinding("u", "next", "p", "CONFIRMED", "HIGH", "d")

        def fake_check(url, param, timeout):
            call_count[0] += 1
            return vuln if param == "next" else None

        with patch("cai.tools.web.open_redirect._check_param", side_effect=fake_check):
            findings = _check_open_redirect("https://example.com/login?next=/")
        # Should stop after first CONFIRMED, not probe all params
        self.assertEqual(len(findings), 1)
        self.assertEqual(call_count[0], 1)

    def test_returns_empty_when_no_redirect_found(self):
        with patch("cai.tools.web.open_redirect._check_param", return_value=None):
            findings = _check_open_redirect("https://example.com/")
        self.assertEqual(findings, [])

    def test_existing_params_probed_first(self):
        probed = []

        def fake_check(url, param, timeout):
            probed.append(param)
            return None

        with patch("cai.tools.web.open_redirect._check_param", side_effect=fake_check):
            _check_open_redirect("https://example.com/login?myspecialparam=home")
        self.assertEqual(probed[0], "myspecialparam")


class TestRunOpenRedirect(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_open_redirect("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_open_redirect("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        with patch("cai.tools.web.open_redirect._check_open_redirect", return_value=[]):
            out = _run_open_redirect("https://example.com")
        self.assertIn("Summary:", out)

    def test_safe_output_when_no_findings(self):
        with patch("cai.tools.web.open_redirect._check_open_redirect", return_value=[]):
            out = _run_open_redirect("https://example.com")
        self.assertIn("SAFE", out)

    def test_confirmed_shown_in_output(self):
        vuln = RedirectFinding(
            url="https://example.com/login?next=https%3A%2F%2Fevil.example.com%2F",
            param="next",
            payload="https://evil.example.com/",
            verdict="CONFIRMED",
            severity="HIGH",
            detail="redirect confirmed",
        )
        with patch("cai.tools.web.open_redirect._check_open_redirect", return_value=[vuln]):
            out = _run_open_redirect("https://example.com")
        self.assertIn("CONFIRMED", out)
        self.assertIn("next", out)

    def test_note_shown_when_findings_exist(self):
        vuln = RedirectFinding("u", "r", "p", "CONFIRMED", "HIGH", "d")
        with patch("cai.tools.web.open_redirect._check_open_redirect", return_value=[vuln]):
            out = _run_open_redirect("https://example.com")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        with patch("cai.tools.web.open_redirect._check_open_redirect", return_value=[]) as m:
            _run_open_redirect("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("open_redirect", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
