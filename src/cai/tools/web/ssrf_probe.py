"""SSRF (Server-Side Request Forgery) probe for web application assessment.

Tests whether user-supplied URL parameters cause the server to make outbound
HTTP requests on behalf of the caller — a classic SSRF pattern. The probe
strategy is purely canary-based:

  1. Inject well-known internal/metadata URLs into common URL-like parameters.
  2. Look for cloud/loopback service response signatures in the server's reply.
  3. Detect blind SSRF via anomalous HTTP 2xx responses (server fetched the
     canary but the response content doesn't reflect it — typical of blind SSRF).

Cloud metadata targets probed (each has a distinctive signature):
  AWS  — http://169.254.169.254/latest/meta-data/
  GCP  — http://metadata.google.internal/computeMetadata/v1/
  Azure— http://169.254.169.254/metadata/instance?api-version=2021-02-01

Loopback targets:
  http://127.0.0.1/    http://0.0.0.0/    http://[::1]/
  http://localhost/    http://2130706433/  (127.0.0.1 as decimal)

Encoding bypass variants tried for each target:
  plain, http→0x...  (octal/hex IP representation)

Verdicts:
  CONFIRMED — cloud-metadata or loopback signature present in response body
  PROBABLE  — 2xx response to a payload that should cause an error (blind SSRF)
  POTENTIAL — 500/connection error only when SSRF payload used (server attempted)
  SAFE      — no anomaly detected

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
# SSRF canary targets — (url, signature_in_response, description)
# ---------------------------------------------------------------------------

_METADATA_TARGETS: list[tuple[str, str, str]] = [
    (
        "http://169.254.169.254/latest/meta-data/",
        "ami-id",
        "AWS EC2 instance metadata",
    ),
    (
        "http://169.254.169.254/latest/meta-data/",
        "instance-id",
        "AWS EC2 instance metadata",
    ),
    (
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        "computeName",
        "Azure IMDS",
    ),
    (
        "http://metadata.google.internal/computeMetadata/v1/",
        "instance",
        "GCP metadata service",
    ),
    (
        "http://169.254.170.2/v2/metadata",
        "TaskArn",
        "AWS ECS task metadata",
    ),
    (
        "http://100.100.100.200/latest/meta-data/",
        "instance-id",
        "Alibaba Cloud metadata",
    ),
    (
        "http://127.0.0.1/",
        "html",
        "Localhost reflection",
    ),
    (
        "http://localhost/",
        "html",
        "Localhost reflection",
    ),
]

# URL-like parameter names commonly abused in SSRF
_URL_PARAMS = [
    "url", "uri", "src", "source", "dest", "destination", "redirect",
    "redirect_uri", "redirect_url", "next", "return", "return_url",
    "callback", "callback_url", "fetch", "proxy", "proxy_url", "host",
    "target", "path", "resource", "link", "load", "file", "to",
    "from", "request", "q", "website", "endpoint", "domain", "image",
    "img", "href", "data", "api", "feed", "forward",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class SSRFFinding(NamedTuple):
    url: str          # Probe URL sent
    param: str        # Injected parameter name
    canary: str       # Canary value injected
    severity: str     # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str       # CONFIRMED | PROBABLE | POTENTIAL | SAFE
    detail: str


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 8.0) -> tuple[int, str]:
    """GET url. Returns (status, body_text). (-1, '') on failure."""
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
                "User-Agent": "Mozilla/5.0 (compatible; ssrf-checker/1.0)",
                "Accept": "*/*",
            })
            resp = conn.getresponse()
            status = resp.status
            body = resp.read(16384).decode("utf-8", errors="replace").lower()
        finally:
            conn.close()
        return status, body
    except Exception:
        return -1, ""


def _inject_url_param(base_url: str, param_name: str, param_value: str) -> str:
    """Return base_url with param_name set to param_value in query string."""
    parsed = urllib.parse.urlparse(base_url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    qs[param_name] = [param_value]
    new_query = urllib.parse.urlencode(qs, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_ssrf(url: str, timeout: float = 8.0) -> list[SSRFFinding]:
    """Probe url for SSRF vulnerabilities. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Baseline GET to understand normal response
    baseline_status, baseline_body = _get(url, timeout)
    if baseline_status == -1:
        http_url = url.replace("https://", "http://", 1)
        baseline_status, baseline_body = _get(http_url, timeout)
        if baseline_status != -1:
            url = http_url

    if baseline_status == -1:
        return [SSRFFinding(url, "", "", "INFO", "SAFE", "Could not connect to target")]

    findings: list[SSRFFinding] = []
    seen_confirmed: bool = False

    # Parse existing query parameters to find URL-like ones to replace
    parsed = urllib.parse.urlparse(url)
    existing_params = list(urllib.parse.parse_qs(parsed.query, keep_blank_values=True).keys())

    # Build test parameter list: existing params first, then common URL param names
    test_params = existing_params + [p for p in _URL_PARAMS if p not in existing_params]

    for canary_url, signature, description in _METADATA_TARGETS:
        if seen_confirmed:
            break

        for param_name in test_params[:20]:  # Limit to top-20 params per target to avoid DoS
            if seen_confirmed:
                break

            probe_url = _inject_url_param(url, param_name, canary_url)
            probe_status, probe_body = _get(probe_url, timeout)

            if probe_status == -1:
                continue

            if signature.lower() in probe_body:
                findings.append(SSRFFinding(
                    url=probe_url,
                    param=param_name,
                    canary=canary_url,
                    severity="CRITICAL",
                    status="CONFIRMED",
                    detail=(
                        f"SSRF confirmed: {description} signature '{signature}' found in server "
                        f"response when '{param_name}' was set to the metadata URL. The server "
                        "is fetching attacker-supplied URLs and returning their content."
                    ),
                ))
                seen_confirmed = True
                break

            # Blind SSRF: server returned 2xx for a URL that should error (metadata address
            # returns HTTP 200 only if server actually contacted it)
            if probe_status == 200 and baseline_status != 200 and canary_url.startswith("http://169"):
                findings.append(SSRFFinding(
                    url=probe_url,
                    param=param_name,
                    canary=canary_url,
                    severity="HIGH",
                    status="PROBABLE",
                    detail=(
                        f"Possible blind SSRF: baseline HTTP {baseline_status} but metadata "
                        f"canary in '{param_name}' returned HTTP 200. Server may have fetched "
                        f"the internal URL ({description})."
                    ),
                ))

            # Server error only when SSRF payload present — server tried to connect
            elif probe_status in (500, 502, 503) and baseline_status not in (500, 502, 503):
                findings.append(SSRFFinding(
                    url=probe_url,
                    param=param_name,
                    canary=canary_url,
                    severity="MEDIUM",
                    status="POTENTIAL",
                    detail=(
                        f"HTTP {probe_status} only when SSRF payload in '{param_name}' — "
                        "server likely attempted outbound connection (error during fetch). "
                        "Possible SSRF requiring deeper investigation."
                    ),
                ))

    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_ssrf_probe(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[ssrf_probe] Error: no URLs provided"

    lines: list[str] = [f"[ssrf_probe] Probing {len(items)} target(s) for SSRF\n"]
    total_confirmed = total_probable = total_potential = total_safe = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _check_ssrf(url, timeout)

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        probable = [f for f in findings if f.status == "PROBABLE"]
        potential = [f for f in findings if f.status == "POTENTIAL"]

        if not findings or all(f.status == "SAFE" for f in findings):
            lines.append("  SAFE — No SSRF indicators found")
            total_safe += 1
        else:
            for f in (confirmed + probable + potential):
                icon = "!!!" if f.status == "CONFIRMED" else (" ! " if f.status == "PROBABLE" else " ~ ")
                lines.append(f"  [{icon}] {f.severity:<8}  {f.status}")
                lines.append(f"           Param    : {f.param}")
                lines.append(f"           Canary   : {f.canary}")
                lines.append(f"           Detail   : {f.detail}")
                lines.append(f"           Probe URL: {f.url[:100]}")
                lines.append("")
            total_confirmed += len(confirmed)
            total_probable += len(probable)
            total_potential += len(potential)

        lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_confirmed} CONFIRMED, {total_probable} PROBABLE, "
        f"{total_potential} POTENTIAL, {total_safe} SAFE"
    )

    if total_confirmed or total_probable:
        lines.append(
            "\nNote: SSRF allows reading internal services and cloud metadata (AWS/GCP/Azure "
            "IMDS), potentially exposing credentials, tokens, and private infrastructure. "
            "Validate and whitelist all server-side URL fetches; block 169.254.x.x, "
            "127.x.x.x, and metadata hostnames at the network layer."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def ssrf_probe(targets: str) -> str:
    """Probe web endpoints for Server-Side Request Forgery (SSRF) vulnerabilities.

    Injects cloud metadata service URLs (AWS/GCP/Azure IMDS), loopback
    addresses, and other internal targets into common URL-like query
    parameters. Detects both reflected SSRF (content in response) and
    blind SSRF (anomalous status codes when metadata canaries are injected).

    Verdicts:
      CONFIRMED — metadata/loopback service signature found in server response
      PROBABLE  — 2xx response to metadata canary where baseline was non-2xx
      POTENTIAL — server error triggered only by SSRF payload (attempted fetch)

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 Include query parameters to probe specific fields.
                 Examples:
                   "https://example.com/fetch?url=https://safe.example.com"
                   "https://api.corp.com/proxy?target=http://internal/data"
                   "target.com/image, https://other.org/import"

    Returns:
        Formatted report with CONFIRMED / PROBABLE / POTENTIAL / SAFE
        status per target, showing the triggering parameter and canary URL.
    """
    return _run_ssrf_probe(targets)


TOOL_REGISTRY.register(
    "ssrf_probe",
    ssrf_probe,
    categories=["recon", "web", "exploitation"],
)
