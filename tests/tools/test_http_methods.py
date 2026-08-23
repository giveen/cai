"""Tests for HTTP method enumeration tool."""
import unittest
from unittest.mock import patch

from cai.tools.web.http_methods import (
    MethodResult,
    _check_methods,
    _run_http_methods,
    _send,
)


class TestSend(unittest.TestCase):
    def test_returns_status_on_success(self):
        import http.client
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b""
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp
        with patch("http.client.HTTPSConnection", return_value=mock_conn):
            status = _send("https://example.com", "GET", timeout=5.0)
        self.assertEqual(status, 200)

    def test_returns_minus_one_on_failure(self):
        with patch("http.client.HTTPSConnection", side_effect=ConnectionRefusedError):
            status = _send("https://unreachable.example.com", "GET", timeout=1.0)
        self.assertEqual(status, -1)

    def test_adds_https_scheme(self):
        captured = []

        def fake_conn(host, timeout=None, **kwargs):
            captured.append(host)
            raise ConnectionRefusedError

        with patch("http.client.HTTPSConnection", side_effect=fake_conn):
            _send("example.com", "GET")
        self.assertTrue(len(captured) > 0)


class TestCheckMethods(unittest.TestCase):
    def _fake_send(self, method_responses: dict):
        """Returns a side_effect for _send based on method → status mapping."""
        def side_effect(url, method, timeout=6.0):
            return method_responses.get(method, -1)
        return side_effect

    def test_put_critical_when_allowed(self):
        responses = {
            "GET": 200, "HEAD": 200, "OPTIONS": 200,
            "POST": 200, "PUT": 200, "DELETE": 405,
            "PATCH": 405, "TRACE": 405, "CONNECT": 405,
            "MOVE": 405, "COPY": 405, "PROPFIND": 405, "MKCOL": 405,
        }
        with patch("cai.tools.web.http_methods._send", side_effect=self._fake_send(responses)), \
             patch("cai.tools.web.http_methods._parse_allow_header", return_value=[]):
            results = _check_methods("https://example.com")
        put_result = next((r for r in results if r.method == "PUT"), None)
        self.assertIsNotNone(put_result)
        self.assertEqual(put_result.severity, "CRITICAL")
        self.assertTrue(put_result.allowed)

    def test_delete_critical_when_allowed(self):
        responses = {"GET": 200, "DELETE": 204}
        with patch("cai.tools.web.http_methods._send", side_effect=self._fake_send({**{
                "GET": 200, "HEAD": 200, "OPTIONS": 200, "POST": 200,
                "PUT": 405, "DELETE": 204, "PATCH": 405, "TRACE": 405,
                "CONNECT": 405, "MOVE": 405, "COPY": 405, "PROPFIND": 405, "MKCOL": 405,
            }})), \
             patch("cai.tools.web.http_methods._parse_allow_header", return_value=[]):
            results = _check_methods("https://example.com")
        del_result = next((r for r in results if r.method == "DELETE"), None)
        self.assertIsNotNone(del_result)
        self.assertEqual(del_result.severity, "CRITICAL")

    def test_put_with_401_is_low(self):
        responses = {m: 405 for m in ["HEAD", "OPTIONS", "POST", "DELETE",
                                        "PATCH", "TRACE", "CONNECT", "MOVE", "COPY", "PROPFIND", "MKCOL"]}
        responses["GET"] = 200
        responses["PUT"] = 401
        with patch("cai.tools.web.http_methods._send", side_effect=self._fake_send(responses)), \
             patch("cai.tools.web.http_methods._parse_allow_header", return_value=[]):
            results = _check_methods("https://example.com")
        put_result = next((r for r in results if r.method == "PUT"), None)
        self.assertIsNotNone(put_result)
        self.assertEqual(put_result.severity, "LOW")

    def test_trace_high_when_allowed(self):
        responses = {m: 405 for m in ["HEAD", "OPTIONS", "POST", "PUT", "DELETE",
                                        "PATCH", "CONNECT", "MOVE", "COPY", "PROPFIND", "MKCOL"]}
        responses["GET"] = 200
        responses["TRACE"] = 200
        with patch("cai.tools.web.http_methods._send", side_effect=self._fake_send(responses)), \
             patch("cai.tools.web.http_methods._parse_allow_header", return_value=[]):
            results = _check_methods("https://example.com")
        trace_result = next((r for r in results if r.method == "TRACE"), None)
        self.assertIsNotNone(trace_result)
        self.assertEqual(trace_result.severity, "HIGH")

    def test_safe_when_all_dangerous_rejected(self):
        responses = {m: 405 for m in ["HEAD", "OPTIONS", "POST", "PUT", "DELETE",
                                        "PATCH", "TRACE", "CONNECT", "MOVE", "COPY", "PROPFIND", "MKCOL"]}
        responses["GET"] = 200
        with patch("cai.tools.web.http_methods._send", side_effect=self._fake_send(responses)), \
             patch("cai.tools.web.http_methods._parse_allow_header", return_value=[]):
            results = _check_methods("https://example.com")
        dangerous_allowed = [
            r for r in results
            if r.method in ("PUT", "DELETE", "TRACE", "CONNECT") and r.allowed
        ]
        self.assertEqual(dangerous_allowed, [])

    def test_adds_https_scheme(self):
        calls = []

        def fake_send(url, method, timeout=6.0):
            calls.append(url)
            return 200

        with patch("cai.tools.web.http_methods._send", side_effect=fake_send), \
             patch("cai.tools.web.http_methods._parse_allow_header", return_value=[]):
            _check_methods("example.com")
        self.assertTrue(all(u.startswith("https://") for u in calls))


class TestRunHttpMethods(unittest.TestCase):
    def test_empty_input_returns_error(self):
        out = _run_http_methods("")
        self.assertIn("Error", out)

    def test_whitespace_only_returns_error(self):
        out = _run_http_methods("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        safe = MethodResult("GET", 200, True, "INFO", "ok")
        with patch("cai.tools.web.http_methods._check_methods", return_value=[safe]):
            out = _run_http_methods("https://example.com")
        self.assertIn("Summary:", out)

    def test_critical_shown_in_output(self):
        crit = MethodResult("PUT", 200, True, "CRITICAL",
                            "PUT may allow file upload without credentials")
        with patch("cai.tools.web.http_methods._check_methods", return_value=[crit]):
            out = _run_http_methods("https://example.com")
        self.assertIn("CRITICAL", out)
        self.assertIn("PUT", out)

    def test_note_shown_when_critical_or_high(self):
        crit = MethodResult("DELETE", 204, True, "CRITICAL", "DELETE without auth")
        with patch("cai.tools.web.http_methods._check_methods", return_value=[crit]):
            out = _run_http_methods("https://example.com")
        self.assertIn("Note:", out)

    def test_comma_separated_targets(self):
        safe = MethodResult("GET", 200, True, "INFO", "ok")
        with patch("cai.tools.web.http_methods._check_methods", return_value=[safe]) as m:
            _run_http_methods("https://a.com, https://b.com")
        self.assertEqual(m.call_count, 2)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("http_methods", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
