"""Tests for the dns_enum tool."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from cai.tools.reconnaissance.dns_enum import (
    DnsRecord,
    DnsResult,
    _query_dig,
    _reverse_lookup,
    _run_dns_enum,
)


# ---------------------------------------------------------------------------
# _query_dig (subprocess fallback)
# ---------------------------------------------------------------------------

def test_query_dig_returns_list():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="1.2.3.4\n5.6.7.8\n", returncode=0)
        result = _query_dig("example.com", "A")
    assert result == ["1.2.3.4", "5.6.7.8"]


def test_query_dig_empty_output():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = _query_dig("nxdomain.invalid", "A")
    assert result == []


def test_query_dig_missing_binary():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = _query_dig("example.com", "A")
    assert result == []


# ---------------------------------------------------------------------------
# _reverse_lookup
# ---------------------------------------------------------------------------

def test_reverse_lookup_success():
    with patch("socket.gethostbyaddr", return_value=("host.example.com", [], ["1.2.3.4"])):
        assert _reverse_lookup("1.2.3.4") == "host.example.com"


def test_reverse_lookup_failure():
    with patch("socket.gethostbyaddr", side_effect=socket.herror):
        assert _reverse_lookup("1.2.3.4") == ""


# ---------------------------------------------------------------------------
# _run_dns_enum (integration, mocked backend)
# ---------------------------------------------------------------------------

def _make_dnspython_mock(records: dict[str, list[str]]):
    """Build a mock dns.resolver.resolve that returns fake records."""
    import dns.resolver

    def fake_resolve(domain, rtype, **kwargs):
        values = records.get(rtype, [])
        if not values:
            raise dns.resolver.NoAnswer()
        answers = []
        for v in values:
            m = MagicMock()
            m.to_text.return_value = v
            answers.append(m)
        return answers

    return fake_resolve


@pytest.fixture(autouse=True)
def _no_zone_transfer(monkeypatch):
    """Suppress actual zone transfer attempts in all tests."""
    monkeypatch.setattr(
        "cai.tools.reconnaissance.dns_enum._try_zone_transfer",
        lambda domain, ns: "",
    )


def test_run_dns_enum_basic():
    fake_records = {"A": ["93.184.216.34"], "NS": ["ns1.example.com."]}
    with patch("dns.resolver.resolve", side_effect=_make_dnspython_mock(fake_records)):
        result = _run_dns_enum("example.com", record_types="A,NS",
                               zone_transfer=False, reverse_lookup=False)
    assert "93.184.216.34" in result
    assert "ns1.example.com" in result


def test_run_dns_enum_no_records():
    with patch("dns.resolver.resolve") as mock_resolve:
        import dns.resolver
        mock_resolve.side_effect = dns.resolver.NoAnswer()
        result = _run_dns_enum("nxdomain.invalid", record_types="A",
                               zone_transfer=False, reverse_lookup=False)
    assert "No records found" in result


def test_run_dns_enum_all_types_header():
    """'all' should query all standard types without error."""
    with patch("cai.tools.reconnaissance.dns_enum._query_dnspython", return_value=[]):
        result = _run_dns_enum("example.com", record_types="all",
                               zone_transfer=False, reverse_lookup=False)
    assert "example.com" in result


def test_run_dns_enum_reverse_lookup():
    fake_records = {"A": ["1.2.3.4"]}
    with (
        patch("dns.resolver.resolve", side_effect=_make_dnspython_mock(fake_records)),
        patch("socket.gethostbyaddr", return_value=("host.example.com", [], ["1.2.3.4"])),
    ):
        result = _run_dns_enum("example.com", record_types="A",
                               zone_transfer=False, reverse_lookup=True)
    assert "1.2.3.4 → host.example.com" in result


def test_run_dns_enum_strips_trailing_dot():
    """Domain should have trailing dot stripped before querying."""
    called_with = []

    def capture(domain, rtype, **kw):
        called_with.append(domain)
        import dns.resolver
        raise dns.resolver.NoAnswer()

    with patch("dns.resolver.resolve", side_effect=capture):
        _run_dns_enum("example.com.", record_types="A",
                      zone_transfer=False, reverse_lookup=False)
    assert all(d == "example.com" for d in called_with)


def test_run_dns_enum_txt_records():
    fake_records = {"TXT": ['"v=spf1 include:_spf.google.com ~all"']}
    with patch("dns.resolver.resolve", side_effect=_make_dnspython_mock(fake_records)):
        result = _run_dns_enum("example.com", record_types="TXT",
                               zone_transfer=False, reverse_lookup=False)
    assert "spf1" in result


def test_run_dns_enum_mx_records():
    fake_records = {"MX": ["10 mail.example.com."]}
    with patch("dns.resolver.resolve", side_effect=_make_dnspython_mock(fake_records)):
        result = _run_dns_enum("example.com", record_types="MX",
                               zone_transfer=False, reverse_lookup=False)
    assert "mail.example.com" in result
