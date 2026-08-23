"""Open Redirect vulnerability checker for red team web assessments.

Tests URL parameters for open redirect vulnerabilities by injecting
a canary destination URL and checking whether the server issues a 3xx
redirect pointing to that destination.

Probing strategy:
  1. Discover redirect parameters from the URL query string.
  2. Inject common redirect parameter names if none found.
  3. Send GET requests; detect 3xx responses and inspect Location header.
  4. Classify by severity: CONFIRMED (Location matches payload),
     PROBABLE (3xx but Location differs), POTENTIAL (form-redirect).

Stdlib-only: http.client + urllib. No external dependencies.
"""

from __future__ import annotations

import http.client
import ssl
import urllib.parse
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# Common redirect parameter names to probe
# ---------------------------------------------------------------------------

_REDIRECT_PARAMS = [
    "redirect", "redirect_to", "redirect_url", "redirectUrl", "redirectTo",
    "url", "next", "next_url", "nextUrl", "return", "return_to", "returnTo",
    "returnUrl", "return_url", "goto", "go", "target", "dest", "destination",
    "forward", "location", "path", "continue", "ref", "referrer", "referer",
    "out", "view", "to", "link", "back", "backUrl",
]

_CANARY_HOST = "evil.example.com"
_CANARY_URL = f"https://{_CANARY_HOST}/"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class RedirectFinding(NamedTuple):
    url: str
    param: str
    payload: str
    verdict: str   # CONFIRMED | PROBABLE | POTENTIAL | SAFE
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW | INFO
    detail: str


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 8.0, follow: bool = False) -> tuple[int, dict[str, str]]:
    """GET url, return (status, lowercase_headers). Optionally follow one redirect.

    Returns (-1, {}) on connection failure.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc or parsed.path
        path = (parsed.path or "/")
        if parsed.query:
            path += "?" + parsed.query

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(host, timeout=timeout, **({"context": ctx} if is_https else {}))
        conn.request(
            "GET", path,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; open-redirect-checker/1.0)",
                "Accept": "*/*",
            },
        )
        resp = conn.getresponse()
        resp.read(1024)  # drain
        status = resp.status
        headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()

        if follow and status in (301, 302, 303, 307, 308) and "location" in headers:
            next_url = headers["location"]
            if not next_url.startswith(("http://", "https://")):
                parsed2 = urllib.parse.urlparse(url)
                next_url = f"{parsed2.scheme}://{parsed2.netloc}{next_url}"
            return _get(next_url, timeout, follow=False)

        return status, headers
    except Exception:
        return -1, {}


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def _inject_param(url: str, param: str, value: str) -> str:
    """Return url with param set to value (replaces existing or appends)."""
    parsed = urllib.parse.urlparse(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    qs[param] = value
    new_query = urllib.parse.urlencode(qs)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def _check_param(base_url: str, param: str, timeout: float) -> RedirectFinding | None:
    """Probe a single parameter. Return None if no redirect was triggered."""
    test_url = _inject_param(base_url, param, _CANARY_URL)
    status, headers = _get(test_url, timeout)
    if status == -1:
        return None  # connection failed — skip rather than report error
    if status not in (301, 302, 303, 307, 308):
        return None  # not a redirect

    location = headers.get("location", "")
    if _CANARY_HOST in location:
        return RedirectFinding(
            url=test_url,
            param=param,
            payload=_CANARY_URL,
            verdict="CONFIRMED",
            severity="HIGH",
            detail=(
                f"HTTP {status} redirect to attacker-controlled URL "
                f"confirmed: Location: {location[:120]}"
            ),
        )
    if location:
        return RedirectFinding(
            url=test_url,
            param=param,
            payload=_CANARY_URL,
            verdict="PROBABLE",
            severity="MEDIUM",
            detail=(
                f"HTTP {status} redirect triggered but Location differs from payload: "
                f"{location[:120]} — may be filtered or normalised"
            ),
        )
    return RedirectFinding(
        url=test_url,
        param=param,
        payload=_CANARY_URL,
        verdict="POTENTIAL",
        severity="LOW",
        detail=f"HTTP {status} redirect but no Location header returned",
    )


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def _check_open_redirect(url: str, timeout: float = 8.0) -> list[RedirectFinding]:
    """Probe url for open redirect vulnerabilities. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Determine params to probe
    parsed = urllib.parse.urlparse(url)
    existing_params = [k for k, _ in urllib.parse.parse_qsl(parsed.query)]

    # Probe existing params first, then common names not already in URL
    params_to_try = list(dict.fromkeys(
        existing_params + [p for p in _REDIRECT_PARAMS if p not in existing_params]
    ))

    findings: list[RedirectFinding] = []
    for param in params_to_try:
        finding = _check_param(url, param, timeout)
        if finding and finding.verdict in ("CONFIRMED", "PROBABLE"):
            findings.append(finding)
            if finding.verdict == "CONFIRMED":
                break  # one confirmed finding is enough; stop probing this URL

    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_open_redirect(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[open_redirect] Error: no targets provided"

    lines: list[str] = [f"[open_redirect] Checking {len(items)} target(s)\n"]
    total_confirmed = total_probable = total_safe = total_err = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")

        try:
            findings = _check_open_redirect(url, timeout)
        except Exception as exc:
            lines.append(f"  ERROR: {exc}")
            total_err += 1
            lines.append("")
            continue

        if not findings:
            lines.append("  SAFE — No open redirect parameters triggered a redirect to the canary URL")
            total_safe += 1
        else:
            for f in findings:
                icon = "!!!" if f.verdict == "CONFIRMED" else " ! "
                lines.append(f"  [{icon}] {f.severity:<8}  param='{f.param}'  {f.verdict}")
                lines.append(f"           {f.detail}")
                if f.verdict == "CONFIRMED":
                    total_confirmed += 1
                else:
                    total_probable += 1
        lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_confirmed} CONFIRMED, {total_probable} PROBABLE, "
        f"{total_safe} SAFE, {total_err} ERROR"
    )

    if total_confirmed or total_probable:
        lines.append(
            "\nNote: Confirmed open redirects enable phishing (credible trusted-domain links), "
            "OAuth token hijacking, and SSRF in some frameworks. "
            "Validate redirect destinations against an allowlist."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def open_redirect(targets: str) -> str:
    """Test web targets for open redirect vulnerabilities.

    Probes common redirect parameters (redirect, url, next, return_to,
    goto, etc.) by injecting a canary URL and checking whether the server
    issues an HTTP 3xx redirect pointing to the canary host.

    Verdicts:
      CONFIRMED — Location header contains the canary host
      PROBABLE  — redirect triggered but Location differs (may be filtered)
      SAFE      — no redirect to canary URL found

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 Include query parameters to narrow which params are probed.
                 Examples:
                   "https://example.com/login?next=/"
                   "target.com/redirect?url=home"
                   "https://a.com, https://b.org"

    Returns:
        Formatted report showing CONFIRMED / PROBABLE / SAFE per target,
        with the triggering parameter and Location header value.
    """
    return _run_open_redirect(targets)


TOOL_REGISTRY.register(
    "open_redirect",
    open_redirect,
    categories=["recon", "web"],
)
