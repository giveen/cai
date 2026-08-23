"""Tests for XXE injection probe tool."""
import unittest
from unittest.mock import patch

from cai.tools.web.xxe_probe import (
    XXEFinding,
    _check_xxe,
    _run_xxe_probe,
)


class TestCheckXXE(unittest.TestCase):
    def _make_post(self, responses: dict):
        """Build a side_effect for _post: maps (url_substr, ctype) -> (status, body, elapsed)."""
        call_count = [0]

        def side_effect(url, body, content_type, timeout):
            call_count[0] += 1
            for key, val in responses.items():
                if key in body or key in url:
                    return val
            return 200, "normal response", 0.1

        return side_effect

    def test_confirmed_when_passwd_in_body(self):
        def fake_post(url, body, content_type, timeout):
            if "xxe" in body.lower() and "passwd" in body.lower():
                return 200, "root:x:0:0:root:/root:/bin/bash\ndaemon:x:1:", 0.1
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            findings = _check_xxe("https://api.example.com/xml")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)
        self.assertEqual(confirmed[0].severity, "CRITICAL")

    def test_confirmed_when_hosts_in_body(self):
        def fake_post(url, body, content_type, timeout):
            if "hosts" in body.lower() and "xxe" in body.lower():
                return 200, "127.0.0.1 localhost", 0.1
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            findings = _check_xxe("https://api.example.com/xml")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)

    def test_safe_when_no_signature_found(self):
        with patch("cai.tools.web.xxe_probe._post", return_value=(200, "normal content no secrets", 0.1)):
            findings = _check_xxe("https://api.example.com/xml")
        bad = [f for f in findings if f.status in ("CONFIRMED", "BLIND")]
        self.assertEqual(bad, [])

    def test_blind_xxe_when_server_error_on_payload(self):
        call_count = [0]

        def fake_post(url, body, content_type, timeout):
            call_count[0] += 1
            if call_count[0] == 1:  # baseline
                return 200, "ok", 0.1
            if "xxe" in body.lower():
                return 500, "internal server error", 0.1
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            findings = _check_xxe("https://api.example.com/xml")
        blind = [f for f in findings if f.status == "BLIND"]
        self.assertTrue(len(blind) > 0)
        self.assertEqual(blind[0].severity, "HIGH")

    def test_safe_when_connection_fails(self):
        with patch("cai.tools.web.xxe_probe._post", return_value=(-1, "", 0.0)):
            findings = _check_xxe("https://unreachable.example.com/xml")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")

    def test_safe_when_endpoint_returns_415(self):
        with patch("cai.tools.web.xxe_probe._post", return_value=(415, "unsupported media type", 0.1)):
            findings = _check_xxe("https://api.example.com/noxml")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "SAFE")
        self.assertIn("415", findings[0].detail)

    def test_adds_https_scheme(self):
        calls = []

        def fake_post(url, body, content_type, timeout):
            calls.append(url)
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            _check_xxe("api.example.com/xml")
        self.assertTrue(all(u.startswith("https://") for u in calls))

    def test_stops_after_first_confirmed(self):
        confirmed_count = [0]

        def fake_post(url, body, content_type, timeout):
            if "passwd" in body.lower() and "xxe" in body.lower():
                confirmed_count[0] += 1
                return 200, "root:x:0:0:root:/root:/bin/bash", 0.1
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            findings = _check_xxe("https://api.example.com/xml")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertEqual(len(confirmed), 1)

    def test_soap_payload_tried(self):
        bodies_sent = []

        def fake_post(url, body, content_type, timeout):
            bodies_sent.append(body)
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            _check_xxe("https://api.example.com/xml")
        soap_payloads = [b for b in bodies_sent if "soapenv" in b.lower()]
        self.assertTrue(len(soap_payloads) > 0)

    def test_win_ini_signature_detection(self):
        def fake_post(url, body, content_type, timeout):
            if "win.ini" in body.lower() and "xxe" in body.lower():
                return 200, "[fonts]\r\n[extensions]\r\n", 0.1
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            findings = _check_xxe("https://api.example.com/xml")
        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        self.assertTrue(len(confirmed) > 0)

    def test_fallback_to_http(self):
        https_calls = [0]

        def fake_post(url, body, content_type, timeout):
            if url.startswith("https://"):
                https_calls[0] += 1
                return -1, "", 0.0
            return 200, "ok", 0.1

        with patch("cai.tools.web.xxe_probe._post", side_effect=fake_post):
            findings = _check_xxe("https://api.example.com/xml")
        self.assertTrue(https_calls[0] >= 1)


class TestRunXXEProbe(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_xxe_probe("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_xxe_probe("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = XXEFinding("https://example.com", "baseline", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[safe]):
            out = _run_xxe_probe("https://example.com")
        self.assertIn("Summary:", out)

    def test_confirmed_shown_in_output(self):
        vuln = XXEFinding(
            "https://example.com/xml",
            "passwd-entity",
            "CRITICAL",
            "CONFIRMED",
            "/etc/passwd content found",
        )
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[vuln]):
            out = _run_xxe_probe("https://example.com")
        self.assertIn("CONFIRMED", out)
        self.assertIn("CRITICAL", out)

    def test_note_shown_when_confirmed_or_blind(self):
        vuln = XXEFinding("u", "passwd-entity", "CRITICAL", "CONFIRMED", "found")
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[vuln]):
            out = _run_xxe_probe("https://example.com")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = XXEFinding("u", "baseline", "INFO", "SAFE", "ok")
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[safe]) as m:
            _run_xxe_probe("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("xxe_probe", TOOL_REGISTRY._tools)

    def test_safe_shown_in_output(self):
        safe = XXEFinding("https://example.com", "baseline", "INFO", "SAFE", "no XXE")
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[safe]):
            out = _run_xxe_probe("https://example.com")
        self.assertIn("SAFE", out)

    def test_blind_counted_in_summary(self):
        blind = XXEFinding("u", "passwd-entity", "HIGH", "BLIND", "server error on payload")
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[blind]):
            out = _run_xxe_probe("https://example.com")
        self.assertIn("BLIND", out)

    def test_note_shown_when_blind(self):
        blind = XXEFinding("u", "passwd-entity", "HIGH", "BLIND", "blind XXE")
        with patch("cai.tools.web.xxe_probe._check_xxe", return_value=[blind]):
            out = _run_xxe_probe("https://example.com")
        self.assertIn("Note:", out)


if __name__ == "__main__":
    unittest.main()
