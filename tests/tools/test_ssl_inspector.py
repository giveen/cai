"""Tests for the ssl_inspector tool."""

import datetime
import ssl
import socket
from unittest.mock import MagicMock, patch

import pytest

from cai.tools.web.ssl_inspector import (
    CertInfo,
    _parse_cert,
    _parse_rdn,
    _run_ssl_inspect,
)


# ---------------------------------------------------------------------------
# _parse_rdn
# ---------------------------------------------------------------------------

def test_parse_rdn_basic():
    rdn = (("CN", "example.com"), ("O", "Example Inc"))
    result = _parse_rdn(rdn)
    assert result == {"CN": "example.com", "O": "Example Inc"}


def test_parse_rdn_empty():
    assert _parse_rdn(()) == {}


# ---------------------------------------------------------------------------
# _parse_cert
# ---------------------------------------------------------------------------

def _make_cert(
    cn: str = "example.com",
    org: str = "Example Inc",
    san: list[str] | None = None,
    not_before: str = "Jan  1 00:00:00 2024 GMT",
    not_after: str = "Jan  1 00:00:00 2026 GMT",
    serial: int = 0x1234,
    issuer_cn: str | None = None,
) -> dict:
    issuer_cn = issuer_cn or cn
    return {
        "subject": ((("commonName", cn),), (("organizationName", org),)),
        "issuer": ((("commonName", issuer_cn),), (("organizationName", org),)),
        "subjectAltName": [("DNS", n) for n in (san or [])],
        "notBefore": not_before,
        "notAfter": not_after,
        "serialNumber": serial,
    }


def test_parse_cert_subject_cn():
    cert = _make_cert(cn="test.example.com")
    info = _parse_cert(cert, "test.example.com", 443)
    assert info.subject.get("commonName") == "test.example.com"


def test_parse_cert_self_signed():
    cert = _make_cert(cn="self.example.com", issuer_cn="self.example.com")
    info = _parse_cert(cert, "self.example.com", 443)
    assert info.self_signed is True


def test_parse_cert_not_self_signed():
    cert = _make_cert(cn="leaf.example.com", issuer_cn="Trusted CA")
    info = _parse_cert(cert, "leaf.example.com", 443)
    assert info.self_signed is False


def test_parse_cert_san():
    cert = _make_cert(san=["www.example.com", "mail.example.com"])
    info = _parse_cert(cert, "example.com", 443)
    assert "www.example.com" in info.san
    assert "mail.example.com" in info.san


def test_parse_cert_expiry_days():
    future = datetime.datetime.utcnow() + datetime.timedelta(days=100)
    not_after = future.strftime("%b %d %H:%M:%S %Y GMT")
    cert = _make_cert(not_after=not_after)
    info = _parse_cert(cert, "example.com", 443)
    assert info.expired is False
    assert info.days_until_expiry is not None
    assert 98 < info.days_until_expiry <= 101


def test_parse_cert_expired():
    past = datetime.datetime.utcnow() - datetime.timedelta(days=5)
    not_after = past.strftime("%b %d %H:%M:%S %Y GMT")
    cert = _make_cert(not_after=not_after)
    info = _parse_cert(cert, "example.com", 443)
    assert info.expired is True
    assert info.days_until_expiry < 0


# ---------------------------------------------------------------------------
# _run_ssl_inspect (mocked socket + ssl)
# ---------------------------------------------------------------------------

def _make_ssock_mock(cert: dict, version: str = "TLSv1.3", cipher=("AES256-GCM-SHA384", "TLSv1.3", 256)):
    ssock = MagicMock()
    ssock.getpeercert.return_value = cert
    ssock.version.return_value = version
    ssock.cipher.return_value = cipher
    ssock.__enter__ = lambda s: s
    ssock.__exit__ = MagicMock(return_value=False)
    return ssock


def _patch_ssl_connect(cert: dict, **kwargs):
    """Context manager that patches socket + ssl for _run_ssl_inspect."""
    from contextlib import contextmanager
    import unittest.mock as mock

    @contextmanager
    def _ctx():
        raw_sock = MagicMock()
        raw_sock.__enter__ = lambda s: s
        raw_sock.__exit__ = MagicMock(return_value=False)

        ssock = _make_ssock_mock(cert, **kwargs)

        with (
            mock.patch("socket.create_connection", return_value=raw_sock),
            mock.patch.object(ssl.SSLContext, "wrap_socket", return_value=ssock),
        ):
            yield

    return _ctx()


def test_run_ssl_inspect_basic():
    cert = _make_cert(cn="example.com", san=["www.example.com"])
    with _patch_ssl_connect(cert):
        result = _run_ssl_inspect("example.com", 443)
    assert "example.com" in result
    assert "TLSv1.3" in result
    assert "www.example.com" in result


def test_run_ssl_inspect_expired():
    past = datetime.datetime.utcnow() - datetime.timedelta(days=10)
    not_after = past.strftime("%b %d %H:%M:%S %Y GMT")
    cert = _make_cert(not_after=not_after)
    with _patch_ssl_connect(cert):
        result = _run_ssl_inspect("old.example.com", 443)
    assert "EXPIRED" in result


def test_run_ssl_inspect_expiring_soon():
    soon = datetime.datetime.utcnow() + datetime.timedelta(days=15)
    not_after = soon.strftime("%b %d %H:%M:%S %Y GMT")
    cert = _make_cert(not_after=not_after)
    with _patch_ssl_connect(cert):
        result = _run_ssl_inspect("expiring.example.com", 443)
    assert "EXPIRING SOON" in result


def test_run_ssl_inspect_weak_cipher():
    cert = _make_cert()
    with _patch_ssl_connect(cert, cipher=("RC4-MD5", "TLSv1", 128)):
        result = _run_ssl_inspect("example.com", 443)
    assert "WEAK CIPHER" in result


def test_run_ssl_inspect_deprecated_protocol():
    cert = _make_cert()
    with _patch_ssl_connect(cert, version="TLSv1"):
        result = _run_ssl_inspect("example.com", 443)
    assert "DEPRECATED PROTOCOL" in result


def test_run_ssl_inspect_connect_failure():
    with patch("socket.create_connection", side_effect=OSError("connection refused")):
        result = _run_ssl_inspect("nohost.invalid", 443)
    assert "Cannot connect" in result


def test_run_ssl_inspect_self_signed():
    cert = _make_cert(cn="self.local", issuer_cn="self.local")
    with _patch_ssl_connect(cert):
        result = _run_ssl_inspect("self.local", 443)
    assert "YES" in result or "self" in result.lower()
