"""Subdomain brute-force discovery tool.

Resolves candidate subdomains from a wordlist using DNS.  Reports IPs
and optionally checks whether HTTP/HTTPS is listening.  Uses stdlib only
(socket + concurrent.futures); dnspython is used when available for
wildcard-card detection.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import socket
import ssl
import os
from dataclasses import dataclass, field
from typing import Sequence

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Built-in compact wordlist
# ---------------------------------------------------------------------------
_BUILTIN_SUBS: list[str] = [
    "www", "mail", "ftp", "smtp", "pop", "pop3", "imap", "ns1", "ns2",
    "webmail", "admin", "portal", "vpn", "remote", "api", "app", "dev",
    "staging", "test", "beta", "demo", "blog", "forum", "shop", "store",
    "cdn", "static", "assets", "img", "images", "media", "upload",
    "dashboard", "manage", "management", "panel", "control",
    "internal", "intranet", "corp", "corporate", "extranet",
    "auth", "login", "sso", "oauth", "id", "identity",
    "git", "gitlab", "github", "ci", "jenkins", "jira", "confluence",
    "db", "database", "mysql", "postgres", "mongo", "redis",
    "backup", "backups", "archive", "data", "logs", "log",
    "monitor", "grafana", "kibana", "prometheus",
    "old", "legacy", "v1", "v2",
]


@dataclass
class _SubResult:
    subdomain: str
    ips: list[str] = field(default_factory=list)
    http_status: int | None = None
    cname: str = ""


def _resolve(subdomain: str) -> tuple[list[str], str]:
    """Return (ips, cname_or_empty) for *subdomain*."""
    try:
        infos = socket.getaddrinfo(subdomain, None)
        ips = list({info[4][0] for info in infos})
        try:
            _, aliases, _ = socket.gethostbyname_ex(subdomain)
            cname = aliases[0] if aliases else ""
        except Exception:
            cname = ""
        return ips, cname
    except (socket.gaierror, OSError):
        return [], ""


def _http_probe(host: str, use_https: bool = False, timeout: float = 3.0) -> int | None:
    """Return HTTP status code for a quick probe, or None on failure."""
    port = 443 if use_https else 80
    try:
        if use_https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("HEAD", "/", headers={"Host": host, "User-Agent": "CAI-SubBrute/1.0"})
        resp = conn.getresponse()
        return resp.status
    except Exception:
        return None


def _detect_wildcard(domain: str) -> list[str] | None:
    """Return wildcard IPs if the domain has wildcard DNS, else None."""
    probe = f"cai-wildcard-probe-99999.{domain}"
    ips, _ = _resolve(probe)
    return ips if ips else None


def _load_wordlist(path: str | None) -> list[str]:
    if not path:
        return _BUILTIN_SUBS
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return _BUILTIN_SUBS


def _run_subdomain_bruteforce(
    domain: str,
    wordlist: str | None = None,
    threads: int = 30,
    timeout: float = 3.0,
    http_probe: bool = False,
    max_results: int = 500,
) -> str:
    """Core subdomain brute-force logic (callable directly in tests)."""
    domain = domain.strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return "[subdomain_bruteforce] Error: domain must be a valid FQDN (e.g. example.com)"

    words = _load_wordlist(wordlist)
    threads = max(1, min(threads, 100))

    # Wildcard check
    wildcard_ips: list[str] | None = _detect_wildcard(domain)
    wildcard_set: set[str] = set(wildcard_ips) if wildcard_ips else set()

    candidates = [f"{w}.{domain}" for w in words]
    found: list[_SubResult] = []

    def _worker(subdomain: str) -> _SubResult | None:
        ips, cname = _resolve(subdomain)
        if not ips:
            return None
        if wildcard_set and set(ips) == wildcard_set:
            return None  # matches wildcard — skip
        return _SubResult(subdomain=subdomain, ips=ips, cname=cname)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {ex.submit(_worker, sub): sub for sub in candidates}
        for fut in concurrent.futures.as_completed(futures):
            result = fut.result()
            if result is None:
                continue
            if http_probe:
                for use_https in (True, False):
                    status = _http_probe(result.subdomain, use_https=use_https, timeout=timeout)
                    if status is not None:
                        result.http_status = status
                        break
            found.append(result)
            if len(found) >= max_results:
                for pending in futures:
                    pending.cancel()
                break

    found.sort(key=lambda r: r.subdomain)

    # Report
    lines = [
        f"[subdomain_bruteforce] Target: {domain}",
        f"  Wordlist : {wordlist or f'built-in ({len(_BUILTIN_SUBS)} words)'}",
        f"  Threads  : {threads}  Timeout: {timeout}s",
    ]
    if wildcard_set:
        lines.append(f"  WARNING  : Wildcard DNS detected ({', '.join(sorted(wildcard_set))})"
                     " — responses matching wildcard IPs are suppressed")
    lines.append(f"  Found    : {len(found)} subdomains\n")

    if not found:
        lines.append("No subdomains discovered.")
        return "\n".join(lines)

    col = 40
    header = f"{'Subdomain':<{col}} IPs"
    if http_probe:
        header += "  HTTP"
    lines.append(header)
    lines.append("-" * 70)

    for r in found:
        ips_str = ", ".join(r.ips)
        suffix = ""
        if r.cname:
            suffix = f"  (CNAME: {r.cname})"
        http_str = f"  {r.http_status}" if http_probe and r.http_status else ""
        lines.append(f"{r.subdomain:<{col}} {ips_str}{http_str}{suffix}")

    return "\n".join(lines)


@function_tool
def subdomain_bruteforce(
    domain: str,
    wordlist: str = "",
    threads: int = 30,
    timeout: float = 3.0,
    http_probe: bool = False,
    max_results: int = 500,
) -> str:
    """Discover subdomains of a target domain by DNS brute-force.

    Resolves candidate subdomains using the system DNS resolver.
    Automatically detects and suppresses wildcard DNS responses so you
    only see genuinely distinct subdomains.

    Args:
        domain: The target base domain (e.g. ``example.com``).
        wordlist: Path to a newline-delimited subdomain wordlist.
            Defaults to a built-in list of ~65 common prefixes.
        threads: Concurrent DNS resolvers (1–100, default 30).
        timeout: Per-probe timeout in seconds (default 3.0).
        http_probe: When True, also attempt an HTTP/HTTPS HEAD request
            against each discovered subdomain and report the status code.
        max_results: Stop after this many discoveries (default 500).

    Returns:
        Formatted table of discovered subdomains with their resolved IPs,
        and optionally HTTP status codes.
    """
    return _run_subdomain_bruteforce(
        domain,
        wordlist=wordlist or None,
        threads=threads,
        timeout=timeout,
        http_probe=http_probe,
        max_results=max_results,
    )


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "subdomain_bruteforce",
    subdomain_bruteforce,
    categories=["recon", "network"],
)
