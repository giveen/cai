"""Tests for the port_scanner tool."""

import socket
import threading
from contextlib import contextmanager

import pytest

from cai.tools.network.port_scanner import (
    _expand_ports,
    _guess_service,
    _run_port_scan,
    _tcp_connect,
)


# ---------------------------------------------------------------------------
# Helper: spin up a temporary TCP server on an ephemeral port
# ---------------------------------------------------------------------------

@contextmanager
def _tcp_server(banner: bytes = b""):
    """Context manager that yields a (host, port) with an open TCP listener."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(2)
    host, port = srv.getsockname()

    def _accept():
        try:
            conn, _ = srv.accept()
            if banner:
                conn.sendall(banner)
            conn.close()
        except Exception:
            pass

    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    try:
        yield host, port
    finally:
        srv.close()
        t.join(timeout=1)


# ---------------------------------------------------------------------------
# _expand_ports
# ---------------------------------------------------------------------------

def test_expand_single_port():
    assert _expand_ports("80") == [80]


def test_expand_range():
    assert _expand_ports("8000-8005") == [8000, 8001, 8002, 8003, 8004, 8005]


def test_expand_mixed():
    result = _expand_ports("22,80,443,8000-8002")
    assert result == [22, 80, 443, 8000, 8001, 8002]


def test_expand_deduplicates():
    result = _expand_ports("80,80,80")
    assert result == [80]


def test_expand_ignores_invalid():
    result = _expand_ports("abc,80,xyz")
    assert result == [80]


def test_expand_out_of_range_ignored():
    result = _expand_ports("0,80,65536")
    assert result == [80]


# ---------------------------------------------------------------------------
# _guess_service
# ---------------------------------------------------------------------------

def test_guess_service_well_known():
    assert _guess_service(22, "") == "ssh"
    assert _guess_service(80, "") == "http"
    assert _guess_service(443, "") == "https"
    assert _guess_service(3306, "") == "mysql"


def test_guess_service_ssh_banner():
    assert _guess_service(9999, "SSH-2.0-OpenSSH_8.0") == "ssh"


def test_guess_service_http_banner():
    assert _guess_service(9999, "HTTP/1.1 200 OK") == "http"


# ---------------------------------------------------------------------------
# _tcp_connect against a real ephemeral server
# ---------------------------------------------------------------------------

def test_tcp_connect_open_port():
    with _tcp_server(b"SSH-2.0-TestServer\r\n") as (host, port):
        result = _tcp_connect(host, port, timeout=2.0)
    assert result.state == "open"
    assert result.port == port


def test_tcp_connect_closed_port():
    # Find a port that is definitely closed (bind then close immediately)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    result = _tcp_connect("127.0.0.1", port, timeout=1.0)
    assert result.state in ("closed", "filtered")


# ---------------------------------------------------------------------------
# _run_port_scan (integration, no network needed)
# ---------------------------------------------------------------------------

def test_scan_open_port():
    with _tcp_server() as (host, port):
        result = _run_port_scan(host, ports=str(port), timeout=2.0, threads=1, nmap_fallback=False)
    assert str(port) in result
    assert "open" in result


def test_scan_closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    result = _run_port_scan("127.0.0.1", ports=str(port), timeout=1.0, threads=1, nmap_fallback=False)
    assert "No open ports found" in result or str(port) not in result or "closed" in result


def test_scan_invalid_host():
    result = _run_port_scan("not-a-valid-hostname-xyz123.invalid", ports="80", timeout=0.5, nmap_fallback=False)
    assert "Cannot resolve" in result or "No open" in result


def test_scan_top1000_syntax():
    # Just ensure top1000 doesn't crash (scan localhost, very short timeout)
    result = _run_port_scan("127.0.0.1", ports="top1000", timeout=0.1, threads=200, nmap_fallback=False)
    assert isinstance(result, str)
    assert "127.0.0.1" in result


def test_scan_port_range_syntax():
    with _tcp_server() as (host, port):
        lo = max(1, port - 2)
        hi = port + 2
        result = _run_port_scan(host, ports=f"{lo}-{hi}", timeout=2.0, threads=10, nmap_fallback=False)
    assert "open" in result


def test_scan_empty_port_spec():
    result = _run_port_scan("127.0.0.1", ports="abc", timeout=0.5, nmap_fallback=False)
    assert "No valid ports" in result
