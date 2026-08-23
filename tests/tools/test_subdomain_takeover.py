"""Tests for subdomain takeover checker."""
import unittest
from unittest.mock import patch, MagicMock
import socket

from cai.tools.web.subdomain_takeover import (
    _run_subdomain_takeover,
    _check_one,
    _resolve_cname_chain,
    _nxdomain,
)


class TestResolveCnameChain(unittest.TestCase):
    def test_no_cname_returns_domain_itself(self):
        with patch("socket.getfqdn", return_value="example.com"):
            chain = _resolve_cname_chain("example.com")
        self.assertIn("example.com", chain)

    def test_single_hop_cname(self):
        def fake_getfqdn(host):
            if host == "sub.example.com":
                return "target.github.io"
            return host

        with patch("socket.getfqdn", side_effect=fake_getfqdn):
            chain = _resolve_cname_chain("sub.example.com")
        self.assertIn("sub.example.com", chain)
        self.assertIn("target.github.io", chain)

    def test_cycle_protection(self):
        def fake_getfqdn(host):
            return "a.example.com" if host == "b.example.com" else "b.example.com"

        with patch("socket.getfqdn", side_effect=fake_getfqdn):
            chain = _resolve_cname_chain("a.example.com", max_hops=5)
        # Must terminate without infinite loop
        self.assertLessEqual(len(chain), 5)

    def test_dns_error_stops_chain(self):
        with patch("socket.getfqdn", side_effect=socket.gaierror("fail")):
            chain = _resolve_cname_chain("nxdomain.example.com")
        # origin hostname is always included; gaierror stops further hops
        self.assertEqual(chain, ["nxdomain.example.com"])


class TestNxdomain(unittest.TestCase):
    def test_nxdomain_true_on_name_not_known(self):
        err = socket.gaierror(8, "Name or service not known")
        with patch("socket.getaddrinfo", side_effect=err):
            self.assertTrue(_nxdomain("nope.invalid"))

    def test_nxdomain_false_when_resolves(self):
        with patch("socket.getaddrinfo", return_value=[("AF_INET", None, None, "", ("1.2.3.4", 0))]):
            self.assertFalse(_nxdomain("exists.example.com"))


class TestCheckOne(unittest.TestCase):
    def _mock_chain(self, final_cname, mock_body=""):
        """Patch _resolve_cname_chain to return a simple two-hop chain."""
        def fake_chain(domain, **kwargs):
            return [domain, final_cname]

        def fake_body(url, timeout=8.0):
            return mock_body

        return fake_chain, fake_body

    def test_safe_when_no_cname_match(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["sub.example.com", "some.random.host"],
        ), patch(
            "cai.tools.web.subdomain_takeover._nxdomain", return_value=False
        ):
            result = _check_one("sub.example.com", 5.0)
        self.assertEqual(result.status, "SAFE")

    def test_nxdomain_reported(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["sub.example.com", "orphan.someservice.net"],
        ), patch(
            "cai.tools.web.subdomain_takeover._nxdomain", return_value=True
        ):
            result = _check_one("sub.example.com", 5.0)
        self.assertEqual(result.status, "NXDOMAIN")

    def test_vulnerable_github_pages(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["sub.example.com", "myorg.github.io"],
        ), patch(
            "cai.tools.web.subdomain_takeover._http_body",
            return_value="There isn't a GitHub Pages site here.",
        ):
            result = _check_one("sub.example.com", 5.0)
        self.assertEqual(result.status, "VULNERABLE")
        self.assertIn("GitHub Pages", result.matched_service)

    def test_vulnerable_s3(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["assets.example.com", "assets.example.com.s3.amazonaws.com"],
        ), patch(
            "cai.tools.web.subdomain_takeover._http_body",
            return_value="<Code>NoSuchBucket</Code>",
        ):
            result = _check_one("assets.example.com", 5.0)
        self.assertEqual(result.status, "VULNERABLE")
        self.assertIn("S3", result.matched_service)

    def test_probable_when_http_unreachable(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["sub.example.com", "myapp.herokuapp.com"],
        ), patch(
            "cai.tools.web.subdomain_takeover._http_body",
            return_value="",  # fetch failed
        ):
            result = _check_one("sub.example.com", 5.0)
        self.assertEqual(result.status, "PROBABLE")
        self.assertIn("Heroku", result.matched_service)

    def test_safe_when_service_is_claimed(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["sub.example.com", "myapp.herokuapp.com"],
        ), patch(
            "cai.tools.web.subdomain_takeover._http_body",
            return_value="<html><body>Welcome to my Heroku app!</body></html>",
        ):
            result = _check_one("sub.example.com", 5.0)
        self.assertEqual(result.status, "SAFE")

    def test_probable_no_http_fingerprint_service(self):
        with patch(
            "cai.tools.web.subdomain_takeover._resolve_cname_chain",
            return_value=["sub.example.com", "something.trafficmanager.net"],
        ):
            result = _check_one("sub.example.com", 5.0)
        self.assertEqual(result.status, "PROBABLE")
        self.assertIn("Traffic Manager", result.matched_service)


class TestRunSubdomainTakeover(unittest.TestCase):
    def test_empty_input(self):
        out = _run_subdomain_takeover("")
        self.assertIn("Error", out)

    def test_whitespace_only_input(self):
        out = _run_subdomain_takeover("   \n  ")
        self.assertIn("Error", out)

    def test_summary_line_present(self):
        with patch(
            "cai.tools.web.subdomain_takeover._check_one",
            return_value=MagicMock(
                domain="a.example.com",
                cname_chain=["a.example.com"],
                matched_service="",
                status="SAFE",
                detail="No issues",
            ),
        ):
            out = _run_subdomain_takeover("a.example.com")
        self.assertIn("Summary:", out)
        self.assertIn("SAFE", out)

    def test_comma_separated_domains(self):
        results = [
            MagicMock(
                domain=d,
                cname_chain=[d],
                matched_service="",
                status="SAFE",
                detail="ok",
            )
            for d in ["a.example.com", "b.example.com"]
        ]
        with patch(
            "cai.tools.web.subdomain_takeover._check_one",
            side_effect=results,
        ):
            out = _run_subdomain_takeover("a.example.com, b.example.com")
        self.assertIn("a.example.com", out)
        self.assertIn("b.example.com", out)

    def test_vulnerable_triggers_recommendation(self):
        with patch(
            "cai.tools.web.subdomain_takeover._check_one",
            return_value=MagicMock(
                domain="vuln.example.com",
                cname_chain=["vuln.example.com", "myorg.github.io"],
                matched_service="GitHub Pages",
                status="VULNERABLE",
                detail="fingerprint matched",
            ),
        ):
            out = _run_subdomain_takeover("vuln.example.com")
        self.assertIn("VULNERABLE", out)
        self.assertIn("Recommendation", out)

    def test_newline_separated_domains(self):
        results = [
            MagicMock(
                domain=d,
                cname_chain=[d],
                matched_service="",
                status="SAFE",
                detail="ok",
            )
            for d in ["x.example.com", "y.example.com"]
        ]
        with patch(
            "cai.tools.web.subdomain_takeover._check_one",
            side_effect=results,
        ):
            out = _run_subdomain_takeover("x.example.com\ny.example.com")
        self.assertIn("x.example.com", out)
        self.assertIn("y.example.com", out)

    def test_tool_registered(self):
        from cai.tool_registry import TOOL_REGISTRY
        self.assertIn("subdomain_takeover_checker", TOOL_REGISTRY._tools)


if __name__ == "__main__":
    unittest.main()
