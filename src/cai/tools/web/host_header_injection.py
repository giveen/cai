"""Host Header Injection checker for web application assessment.

Tests whether a target web server is vulnerable to Host Header Injection:
a class of attacks where a malicious Host header value is reflected in
responses, used in password-reset link poisoning, cache poisoning,
web cache deception, or SSRF.

Probe strategy:
  1. Baseline GET with the correct Host header.
  2. Inject several malicious Host header variants.
  3. Check: reflected in body / Location redirect / different status code.

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
# Probe payloads
# ---------------------------------------------------------------------------

_CANARY_HOST = "evil.attacker.example.com"

_PROBES: list[tuple[str, str]] = [
    # (description, Host header value)
    ("arbitrary domain",        _CANARY_HOST),
    ("X-Forwarded-Host header", ""),          # special case — sent as X-Forwarded-Host
    ("double host",             f"real.host, {_CANARY_HOST}"),
    ("port injection",          f"real.host:{_CANARY_HOST}"),
    ("subpath injection",       f"real.host/{_CANARY_HOST}"),
    ("null byte",               f"real.host\x00{_CANARY_HOST}"),
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class HHIFinding(NamedTuple):
    probe: str
    severity: str   # HIGH | MEDIUM | LOW | INFO
    verdict: str    # REFLECTED | REDIRECT | STATUS_CHANGE | SAFE
    detail: str


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(
    url: str,
    host_override: str = "",
    extra_header: tuple[str, str] | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, str], str]:
    """GET url with optional Host override. Returns (status, headers, body_lower).

    Returns (-1, {}, "") on failure.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        real_host = parsed.netloc or parsed.path
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(real_host, timeout=timeout, **({"context": ctx} if is_https else {}))

        headers: dict[str, str] = {
            "Host": host_override if host_override else real_host,
            "User-Agent": "Mozilla/5.0 (compatible; hhi-checker/1.0)",
            "Accept": "text/html,*/*",
        }
        if extra_header:
            headers[extra_header[0]] = extra_header[1]

        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            body = resp.read(8192).decode("utf-8", errors="replace").lower()
        finally:
            conn.close()
        return status, resp_headers, body
    except Exception:
        return -1, {}, ""


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_probe(
    url: str,
    probe_desc: str,
    host_value: str,
    baseline_status: int,
    baseline_body: str,
    timeout: float,
    extra_header: tuple[str, str] | None = None,
) -> HHIFinding:
    """Run one probe and classify the result."""
    status, headers, body = _get(url, host_override=host_value, extra_header=extra_header, timeout=timeout)
    if status == -1:
        return HHIFinding(
            probe=probe_desc, severity="INFO", verdict="SAFE",
            detail="Connection failed (server may have rejected malformed Host)",
        )

    canary_lower = _CANARY_HOST.lower()
    location = headers.get("location", "")

    # Check body reflection
    if canary_lower in body:
        return HHIFinding(
            probe=probe_desc,
            severity="HIGH",
            verdict="REFLECTED",
            detail=(
                f"Canary host '{_CANARY_HOST}' reflected in response body — "
                "password-reset link poisoning and cache poisoning are possible"
            ),
        )

    # Check Location header
    if canary_lower in location.lower():
        return HHIFinding(
            probe=probe_desc,
            severity="HIGH",
            verdict="REDIRECT",
            detail=(
                f"Server issued a redirect to '{location}' — "
                "canary host appeared in Location header"
            ),
        )

    # Check significant status change
    if status != baseline_status and status in (301, 302, 303, 307, 308, 400, 403, 500):
        if baseline_status not in (301, 302, 303, 307, 308):
            return HHIFinding(
                probe=probe_desc,
                severity="LOW",
                verdict="STATUS_CHANGE",
                detail=(
                    f"Status changed from {baseline_status} to {status} with injected Host — "
                    "may indicate Host-header-dependent routing"
                ),
            )

    return HHIFinding(
        probe=probe_desc, severity="INFO", verdict="SAFE",
        detail=f"No reflection or redirect (HTTP {status})",
    )


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_host_header_injection(url: str, timeout: float = 8.0) -> list[HHIFinding]:
    """Probe url for Host Header Injection. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Baseline
    baseline_status, _, baseline_body = _get(url, timeout=timeout)
    if baseline_status == -1:
        return [HHIFinding("baseline", "INFO", "SAFE", "Could not reach the target")]

    parsed = urllib.parse.urlparse(url)
    real_host = parsed.netloc

    findings: list[HHIFinding] = []
    for desc, host_val in _PROBES:
        if desc == "X-Forwarded-Host header":
            # Send real Host but add X-Forwarded-Host with canary
            f = _check_probe(
                url, "X-Forwarded-Host injection", real_host,
                baseline_status, baseline_body, timeout,
                extra_header=("X-Forwarded-Host", _CANARY_HOST),
            )
        else:
            f = _check_probe(url, desc, host_val, baseline_status, baseline_body, timeout)
        findings.append(f)

    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_host_header_injection(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[host_header_injection] Error: no URLs provided"

    lines: list[str] = [f"[host_header_injection] Checking {len(items)} target(s)\n"]
    total_reflected = total_redirect = total_status_change = total_safe = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _check_host_header_injection(url, timeout)
        for f in findings:
            if f.verdict == "REFLECTED":
                icon = "!!!"
                total_reflected += 1
            elif f.verdict == "REDIRECT":
                icon = " R "
                total_redirect += 1
            elif f.verdict == "STATUS_CHANGE":
                icon = " S "
                total_status_change += 1
            else:
                icon = "   "
                total_safe += 1
            lines.append(f"  [{icon}] {f.severity:<8}  {f.probe}")
            lines.append(f"           {f.detail}")
            lines.append("")

    lines.append("─" * 60)
    actionable = total_reflected + total_redirect + total_status_change
    lines.append(
        f"Summary: {total_reflected} REFLECTED, {total_redirect} REDIRECT, "
        f"{total_status_change} STATUS_CHANGE, {total_safe} SAFE"
    )

    if actionable:
        lines.append(
            "\nNote: Host header injection may enable password-reset link poisoning "
            "(send a reset, victim clicks attacker's link), web cache poisoning, "
            "or SSRF via misconfigured proxy forwarding."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def host_header_injection(targets: str) -> str:
    """Test web targets for Host Header Injection vulnerabilities.

    Sends GET requests with forged Host header values and checks whether
    the canary host appears in the response body, Location redirect, or
    triggers anomalous behaviour. Also probes X-Forwarded-Host injection.

    Common impact:
      - Password-reset link poisoning (send reset, victim clicks attacker URL)
      - Web cache poisoning (server caches forged-Host response)
      - SSRF via reverse proxy Host forwarding

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 Examples:
                   "https://example.com/forgot-password"
                   "target.com, https://other.org"

    Returns:
        Formatted report with REFLECTED / REDIRECT / STATUS_CHANGE / SAFE
        per probe per target.
    """
    return _run_host_header_injection(targets)


TOOL_REGISTRY.register(
    "host_header_injection",
    host_header_injection,
    categories=["recon", "web"],
)
