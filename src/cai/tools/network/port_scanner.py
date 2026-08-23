"""Structured port scanner for red team reconnaissance.

Provides a fast, dependency-free TCP connect scanner with an optional
nmap fallback for service/version detection.  Returns structured text
that agents can parse without secondary tool calls.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import socket
import subprocess
from dataclasses import dataclass, field

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

@dataclass
class PortResult:
    port: int
    state: str  # "open" | "closed" | "filtered"
    service: str = ""
    banner: str = ""

    def as_text(self) -> str:
        parts = [f"{self.port}/tcp  {self.state:<8}  {self.service or 'unknown'}"]
        if self.banner:
            safe = self.banner[:120].replace("\n", " ").replace("\r", "")
            parts.append(f"    banner: {safe}")
        return "\n".join(parts)


def _tcp_connect(host: str, port: int, timeout: float) -> PortResult:
    """Single TCP connect probe; grabs a short banner if the port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            try:
                # Nudge HTTP/SMTP/FTP servers that speak first
                sock.sendall(b"\r\n")
                raw = sock.recv(256)
                banner = raw.decode(errors="replace").strip()
            except Exception:
                banner = ""
            service = _guess_service(port, banner)
            return PortResult(port=port, state="open", service=service, banner=banner)
    except ConnectionRefusedError:
        return PortResult(port=port, state="closed")
    except (socket.timeout, OSError):
        return PortResult(port=port, state="filtered")


def _guess_service(port: int, banner: str) -> str:
    """Return a best-guess service name from the well-known-ports table or banner."""
    _WELL_KNOWN: dict[int, str] = {
        21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns",
        80: "http", 110: "pop3", 143: "imap", 443: "https",
        445: "smb", 465: "smtps", 587: "submission", 993: "imaps",
        995: "pop3s", 1433: "mssql", 1521: "oracle", 3306: "mysql",
        3389: "rdp", 5432: "postgresql", 5900: "vnc", 6379: "redis",
        8080: "http-alt", 8443: "https-alt", 8888: "http-proxy",
        9200: "elasticsearch", 27017: "mongodb",
    }
    if port in _WELL_KNOWN:
        return _WELL_KNOWN[port]
    b = banner.lower()
    if b.startswith("ssh"):
        return "ssh"
    if "220 " in b or "ftp" in b:
        return "ftp"
    if "http/" in b or "html" in b:
        return "http"
    if b.startswith("220") and "smtp" in b:
        return "smtp"
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return ""


def _expand_ports(port_spec: str) -> list[int]:
    """Parse a port specification like '22,80,443,8000-8100' into a sorted list."""
    ports: set[int] = set()
    for part in port_spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            try:
                ports.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        else:
            try:
                p = int(part)
                if 1 <= p <= 65535:
                    ports.add(p)
            except ValueError:
                pass
    return sorted(ports)


_TOP_1000 = (
    "1,3,4,6,7,9,13,17,19,20,21,22,23,24,25,26,30,32,33,37,42,43,49,53,70,"
    "79,80,81,82,83,84,85,88,89,90,99,100,106,109,110,111,113,119,125,135,"
    "139,143,144,146,161,163,179,199,211,212,222,254,255,256,259,264,280,301,"
    "306,311,340,366,389,406,407,416,417,425,427,443,444,445,458,464,465,481,"
    "497,500,512,513,514,515,524,541,543,544,545,548,554,555,563,587,593,616,"
    "617,625,631,636,646,648,666,667,668,683,687,691,700,705,711,714,720,722,"
    "726,749,765,777,783,787,800,801,808,843,873,880,888,898,900,901,902,903,"
    "911,912,981,987,990,992,993,995,999,1000,1001,1002,1007,1009,1010,1011,"
    "1021,1022,1023,1024,1025,1026,1027,1028,1029,1030,1110,1234,1433,1443,"
    "1521,1720,1723,1755,1900,2000,2001,2049,2121,2181,2222,2375,2376,3000,"
    "3306,3389,3690,4000,4443,4444,4848,5000,5432,5900,6379,6667,7001,7080,"
    "8000,8008,8080,8081,8443,8888,9000,9090,9200,9300,10000,27017"
)


def _run_nmap(host: str, ports: list[int], timeout: int) -> str | None:
    """Try nmap for service/version detection; returns None if nmap is absent."""
    port_arg = ",".join(str(p) for p in ports[:500])
    try:
        result = subprocess.run(
            ["nmap", "-sV", "--open", "-T4", f"--host-timeout={timeout}s",
             "-p", port_arg, host],
            capture_output=True, text=True, timeout=timeout + 10,
        )
        return result.stdout
    except FileNotFoundError:
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------

def _run_port_scan(
    target: str,
    ports: str = "top1000",
    timeout: float = 1.0,
    threads: int = 100,
    nmap_fallback: bool = True,
) -> str:
    """Core scanning logic (callable directly in tests)."""
    # Validate target
    host = target.strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            host = socket.gethostbyname(host)
        except socket.gaierror:
            return f"[port_scanner] Cannot resolve host: {target!r}"

    # Parse port list
    if ports.lower() == "top1000":
        port_list = _expand_ports(_TOP_1000)
    elif ports.lower() == "all":
        port_list = list(range(1, 65536))
    else:
        port_list = _expand_ports(ports)

    if not port_list:
        return "[port_scanner] No valid ports specified."

    # TCP connect scan
    results: list[PortResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(threads, len(port_list))) as pool:
        futures = {pool.submit(_tcp_connect, host, p, timeout): p for p in port_list}
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                pass

    results.sort(key=lambda r: r.port)
    open_ports = [r for r in results if r.state == "open"]

    lines = [f"[port_scanner] Scan of {target} ({host}) — {len(port_list)} ports\n"]

    if not open_ports:
        lines.append("No open ports found.")
        return "\n".join(lines)

    # Try nmap service/version detection on the open ports only
    if nmap_fallback and open_ports:
        nmap_out = _run_nmap(host, [r.port for r in open_ports], int(timeout * len(open_ports) + 30))
        if nmap_out:
            lines.append("=== nmap service detection ===")
            # Filter nmap output to just the relevant lines
            for line in nmap_out.splitlines():
                if re.match(r"\d+/tcp", line) or line.startswith("PORT") or line.startswith("Nmap scan"):
                    lines.append(line)
            lines.append("")

    lines.append(f"=== TCP connect scan: {len(open_ports)} open port(s) ===")
    for r in open_ports:
        lines.append(r.as_text())

    filtered = sum(1 for r in results if r.state == "filtered")
    if filtered:
        lines.append(f"\n{filtered} port(s) filtered (no response within {timeout}s timeout)")

    return "\n".join(lines)


@function_tool
def port_scanner(
    target: str,
    ports: str = "top1000",
    timeout: float = 1.0,
    threads: int = 100,
    nmap_fallback: bool = True,
) -> str:
    """Scan TCP ports on a host and report open services.

    Performs a fast TCP-connect scan, then optionally runs nmap -sV on the
    discovered open ports for service/version fingerprinting.

    Args:
        target: Hostname or IP address to scan.
        ports: Port specification. Use "top1000" (default) for the 1000 most
               common ports, "all" for 1-65535, or a comma-separated list with
               optional ranges e.g. "22,80,443,8000-8100".
        timeout: Per-port connect timeout in seconds (default 1.0).
                 Lower values are faster but may miss slow hosts.
        threads: Maximum concurrent probes (default 100). Increase for faster
                 scans on well-connected networks.
        nmap_fallback: When True (default), run nmap -sV on open ports for
                       service/version detection if nmap is available.

    Returns:
        A formatted report listing open ports with service guesses and banners,
        plus nmap version-detection output when available.
    """
    return _run_port_scan(target, ports, timeout, threads, nmap_fallback)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "port_scanner",
    port_scanner,
    categories=["network", "recon"],
)
