"""HTTP method enumeration tool for web application reconnaissance.

Tests which HTTP methods a web endpoint accepts by sending OPTIONS
and then probing individual methods. Dangerous/unexpected allowed
methods (PUT, DELETE, TRACE, CONNECT) are security findings.

  OPTIONS  — server advertises allowed methods (may not be enforced)
  GET/HEAD — standard read; HEAD differences can indicate auth issues
  POST     — write endpoint
  PUT      — file upload / object replace (CRITICAL if unauthenticated)
  DELETE   — resource deletion (CRITICAL if unauthenticated)
  PATCH    — partial update
  TRACE    — reflects request back (XSS via TRACE if cookies present)
  CONNECT  — proxy tunnelling (should never be allowed externally)

Stdlib-only: http.client + ssl + urllib. No external dependencies.
"""

from __future__ import annotations

import http.client
import ssl
import urllib.parse
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class MethodResult(NamedTuple):
    method: str
    status: int         # HTTP status code, -1 if connection failed
    allowed: bool
    severity: str       # CRITICAL | HIGH | MEDIUM | LOW | INFO
    note: str


# ---------------------------------------------------------------------------
# Method classifications
# ---------------------------------------------------------------------------

_DANGEROUS: dict[str, tuple[str, str]] = {
    "PUT":     ("CRITICAL", "Unauthenticated PUT may allow remote file upload / object replacement"),
    "DELETE":  ("CRITICAL", "Unauthenticated DELETE may allow resource deletion"),
    "TRACE":   ("HIGH",     "TRACE reflects full request headers — enables XSS via cross-site tracing (XST)"),
    "CONNECT": ("HIGH",     "CONNECT enables proxy tunnelling — should never be allowed externally"),
    "PATCH":   ("MEDIUM",   "Unauthenticated PATCH may allow partial resource modification"),
    "MOVE":    ("HIGH",     "WebDAV MOVE may allow file/directory renaming without auth"),
    "COPY":    ("MEDIUM",   "WebDAV COPY may allow unauthenticated resource duplication"),
    "PROPFIND":("MEDIUM",   "WebDAV PROPFIND leaks filesystem structure"),
    "MKCOL":   ("HIGH",     "WebDAV MKCOL allows unauthenticated directory creation"),
}

_STANDARD_SAFE = {"GET", "HEAD", "POST", "OPTIONS"}

_ALL_METHODS = [
    "GET", "HEAD", "OPTIONS", "POST", "PUT", "DELETE",
    "PATCH", "TRACE", "CONNECT", "MOVE", "COPY", "PROPFIND", "MKCOL",
]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _send(url: str, method: str, timeout: float = 6.0) -> int:
    """Send an HTTP request with given method. Returns HTTP status or -1."""
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc or parsed.path
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(host, timeout=timeout, **({"context": ctx} if is_https else {}))
        try:
            conn.request(
                method, path,
                headers={
                    "Host": host,
                    "User-Agent": "Mozilla/5.0 (compatible; http-methods-checker/1.0)",
                    "Content-Length": "0",
                },
            )
            resp = conn.getresponse()
            status = resp.status
            resp.read(512)
        finally:
            conn.close()
        return status
    except Exception:
        return -1


def _parse_allow_header(url: str, timeout: float) -> list[str]:
    """Return methods advertised in Allow/Access-Control-Allow-Methods via OPTIONS."""
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc or parsed.path
        path = parsed.path or "/"

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(host, timeout=timeout, **({"context": ctx} if is_https else {}))
        try:
            conn.request("OPTIONS", path, headers={"Host": host, "User-Agent": "http-methods-checker/1.0"})
            resp = conn.getresponse()
            resp.read(512)
            allow_raw = ""
            for name, value in resp.getheaders():
                if name.lower() in ("allow", "access-control-allow-methods"):
                    allow_raw += "," + value
        finally:
            conn.close()
        return [m.strip().upper() for m in allow_raw.split(",") if m.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_methods(url: str, timeout: float = 6.0, probe_all: bool = False) -> list[MethodResult]:
    """Test HTTP methods on url. Returns list of MethodResult."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # First get baseline status for GET
    baseline = _send(url, "GET", timeout)
    if baseline == -1:
        # Try HTTP
        http_url = url.replace("https://", "http://", 1)
        baseline = _send(http_url, "GET", timeout)
        if baseline != -1:
            url = http_url

    # Advertised methods via OPTIONS
    advertised = set(_parse_allow_header(url, timeout))

    # Methods to test: always test dangerous ones; standard ones only if advertised or probe_all
    methods_to_test = list(_ALL_METHODS) if probe_all else (
        list(_STANDARD_SAFE | set(_DANGEROUS.keys())) + list(advertised)
    )
    # Deduplicate preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for m in methods_to_test:
        if m not in seen:
            seen.add(m)
            ordered.append(m)

    results: list[MethodResult] = []
    for method in ordered:
        status = _send(url, method, timeout)
        if status == -1:
            results.append(MethodResult(method, -1, False, "INFO", "Connection failed"))
            continue

        # A method is "allowed" if the server doesn't return 405/501/400
        blocked_statuses = {405, 501}
        allowed = status not in blocked_statuses and status != -1

        if method in _DANGEROUS and allowed:
            sev, note = _DANGEROUS[method]
            # Require authentication check: 401/403 = blocked despite being allowed
            if status in (401, 403):
                sev = "LOW"
                note = f"{_DANGEROUS[method][1]} — but authentication required (HTTP {status})"
        elif method in _DANGEROUS and not allowed:
            sev, note = "INFO", f"Method rejected (HTTP {status})"
        elif method in _STANDARD_SAFE:
            sev = "INFO"
            note = f"Standard method, HTTP {status}"
        else:
            sev = "INFO"
            note = f"HTTP {status}"

        results.append(MethodResult(method, status, allowed, sev, note))

    return results


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_http_methods(targets: str, timeout: float = 6.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[http_methods] Error: no URLs provided"

    lines: list[str] = [f"[http_methods] Checking {len(items)} target(s)\n"]
    total_critical = total_high = total_medium = total_low = total_info = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        results = _check_methods(url, timeout)
        for r in results:
            if r.status == -1:
                continue
            icon = "!!!" if r.severity in ("CRITICAL", "HIGH") and r.allowed else (
                " ! " if r.severity == "MEDIUM" and r.allowed else "   "
            )
            status_str = str(r.status) if r.status != -1 else "ERR"
            lines.append(f"  [{icon}] {r.severity:<8}  {r.method:<10}  HTTP {status_str}")
            if r.severity != "INFO" or r.allowed:
                lines.append(f"           {r.note}")
            lines.append("")

            if r.severity == "CRITICAL":
                total_critical += 1
            elif r.severity == "HIGH":
                total_high += 1
            elif r.severity == "MEDIUM":
                total_medium += 1
            elif r.severity == "LOW":
                total_low += 1
            else:
                total_info += 1

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_critical} CRITICAL, {total_high} HIGH, "
        f"{total_medium} MEDIUM, {total_low} LOW, {total_info} INFO"
    )

    if total_critical or total_high:
        lines.append(
            "\nNote: Dangerous methods (PUT/DELETE/TRACE) accepted without authentication "
            "can lead to remote code execution, data destruction, or XSS. "
            "Restrict to GET/POST/OPTIONS via server configuration."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def http_methods(targets: str) -> str:
    """Enumerate HTTP methods accepted by web endpoints.

    Tests a range of HTTP methods including dangerous ones (PUT, DELETE,
    TRACE, CONNECT, WebDAV verbs) to identify over-permissive server
    configurations. Also reads the Allow header from OPTIONS.

    Risk classifications:
      CRITICAL — PUT/DELETE accepted without auth (file upload, deletion)
      HIGH     — TRACE (XST) or CONNECT (proxy tunnel) allowed
      MEDIUM   — PATCH/PROPFIND without auth
      LOW      — Method allowed but auth required (401/403)

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 Examples:
                   "https://example.com/api/v1/users/1"
                   "target.com/upload, https://other.org/files"

    Returns:
        Per-method status and severity for each target, with a summary
        of dangerous findings.
    """
    return _run_http_methods(targets)


TOOL_REGISTRY.register(
    "http_methods",
    http_methods,
    categories=["recon", "web"],
)
