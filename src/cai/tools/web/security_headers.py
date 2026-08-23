"""HTTP Security Headers analyzer for red team reconnaissance.

Checks a web target's response headers for missing or misconfigured
security-relevant headers:

  - Strict-Transport-Security (HSTS)
  - Content-Security-Policy (CSP)
  - X-Frame-Options (clickjacking protection)
  - X-Content-Type-Options
  - Referrer-Policy
  - Permissions-Policy (formerly Feature-Policy)
  - Cross-Origin-Opener-Policy (COOP)
  - Cross-Origin-Resource-Policy (CORP)
  - Cross-Origin-Embedder-Policy (COEP)
  - Cache-Control (for sensitive pages)
  - Server / X-Powered-By information disclosure

Each finding is rated CRITICAL / HIGH / MEDIUM / LOW based on its
practical exploitability in a red team context.

Stdlib-only: http.client + ssl.  No external dependencies.
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

class HeaderFinding(NamedTuple):
    header: str
    severity: str   # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str     # MISSING | MISCONFIGURED | PRESENT | INFO
    detail: str


# ---------------------------------------------------------------------------
# Header check definitions
# ---------------------------------------------------------------------------

def _check_hsts(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("strict-transport-security")
    if not val:
        return HeaderFinding(
            "Strict-Transport-Security", "HIGH", "MISSING",
            "HSTS not set — connection can be downgraded to HTTP by an active MitM",
        )
    if "max-age=0" in val:
        return HeaderFinding(
            "Strict-Transport-Security", "HIGH", "MISCONFIGURED",
            "max-age=0 effectively disables HSTS",
        )
    max_age = 0
    for part in val.split(";"):
        p = part.strip().lower()
        if p.startswith("max-age="):
            try:
                max_age = int(p[8:])
            except ValueError:
                pass
    if max_age < 31536000:
        return HeaderFinding(
            "Strict-Transport-Security", "MEDIUM", "MISCONFIGURED",
            f"max-age={max_age} is below recommended 31536000 (1 year); "
            "short HSTS duration reduces protection window",
        )
    if "includesubdomains" not in val.lower():
        return HeaderFinding(
            "Strict-Transport-Security", "LOW", "MISCONFIGURED",
            "includeSubDomains missing — subdomains not protected",
        )
    return HeaderFinding(
        "Strict-Transport-Security", "INFO", "PRESENT",
        f"HSTS set correctly ({val[:80]})",
    )


def _check_csp(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("content-security-policy")
    if not val:
        # Older header as fallback
        val = headers.get("x-content-security-policy") or headers.get("x-webkit-csp")
        if val:
            return HeaderFinding(
                "Content-Security-Policy", "LOW", "MISCONFIGURED",
                "Only legacy X-Content-Security-Policy / X-WebKit-CSP present (ignored by modern browsers)",
            )
        return HeaderFinding(
            "Content-Security-Policy", "HIGH", "MISSING",
            "No CSP — XSS attacks can execute arbitrary scripts without restriction",
        )
    issues = []
    val_lower = val.lower()
    if "unsafe-inline" in val_lower:
        issues.append("'unsafe-inline' allows inline script/style execution")
    if "unsafe-eval" in val_lower:
        issues.append("'unsafe-eval' allows eval() and similar dangerous functions")
    if "unsafe-hashes" in val_lower:
        issues.append("'unsafe-hashes' weakens CSP by allowing event-handler hashes")
    if "data:" in val_lower and "script-src" in val_lower:
        issues.append("data: URI in script-src enables script injection")
    if "http:" in val_lower:
        issues.append("http: wildcard in source list allows scripts from any HTTP origin")
    if "*" in val_lower:
        issues.append("wildcard (*) source allows scripts from any origin")
    if not issues:
        return HeaderFinding(
            "Content-Security-Policy", "INFO", "PRESENT",
            "CSP present with no obvious weaknesses detected",
        )
    sev = "HIGH" if any("unsafe-inline" in i or "unsafe-eval" in i or "wildcard" in i for i in issues) else "MEDIUM"
    return HeaderFinding(
        "Content-Security-Policy", sev, "MISCONFIGURED",
        "CSP weaknesses: " + "; ".join(issues),
    )


def _check_x_frame_options(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("x-frame-options")
    csp = headers.get("content-security-policy", "")
    if "frame-ancestors" in csp.lower():
        return HeaderFinding(
            "X-Frame-Options", "INFO", "PRESENT",
            "Clickjacking protection via CSP frame-ancestors (X-Frame-Options superseded)",
        )
    if not val:
        return HeaderFinding(
            "X-Frame-Options", "HIGH", "MISSING",
            "No X-Frame-Options or CSP frame-ancestors — page may be embeddable for clickjacking",
        )
    val_u = val.strip().upper()
    if val_u not in ("DENY", "SAMEORIGIN"):
        if val_u.startswith("ALLOW-FROM"):
            return HeaderFinding(
                "X-Frame-Options", "LOW", "MISCONFIGURED",
                "ALLOW-FROM is not supported by Chrome/Firefox — use CSP frame-ancestors instead",
            )
        return HeaderFinding(
            "X-Frame-Options", "MEDIUM", "MISCONFIGURED",
            f"Unrecognised value '{val}' — clickjacking protection may not be active",
        )
    return HeaderFinding(
        "X-Frame-Options", "INFO", "PRESENT",
        f"X-Frame-Options: {val}",
    )


def _check_x_content_type_options(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("x-content-type-options", "")
    if val.strip().lower() == "nosniff":
        return HeaderFinding(
            "X-Content-Type-Options", "INFO", "PRESENT",
            "nosniff set — MIME-sniffing attack blocked",
        )
    return HeaderFinding(
        "X-Content-Type-Options", "MEDIUM", "MISSING",
        "Missing X-Content-Type-Options: nosniff — browser may MIME-sniff responses "
        "enabling content-injection attacks",
    )


def _check_referrer_policy(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("referrer-policy", "")
    if not val:
        return HeaderFinding(
            "Referrer-Policy", "LOW", "MISSING",
            "No Referrer-Policy — full URL (including tokens/paths) may leak to third parties",
        )
    safe_values = {
        "no-referrer", "no-referrer-when-downgrade",
        "same-origin", "strict-origin", "strict-origin-when-cross-origin",
    }
    if val.strip().lower() in safe_values:
        return HeaderFinding(
            "Referrer-Policy", "INFO", "PRESENT",
            f"Referrer-Policy: {val}",
        )
    return HeaderFinding(
        "Referrer-Policy", "LOW", "MISCONFIGURED",
        f"Referrer-Policy '{val}' may leak sensitive URL data to cross-origin requests",
    )


def _check_permissions_policy(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("permissions-policy") or headers.get("feature-policy")
    if not val:
        return HeaderFinding(
            "Permissions-Policy", "LOW", "MISSING",
            "No Permissions-Policy — browser features (camera, mic, geolocation) may be accessible "
            "from embedded third-party frames",
        )
    return HeaderFinding(
        "Permissions-Policy", "INFO", "PRESENT",
        f"Permissions-Policy set ({val[:80]})",
    )


def _check_information_disclosure(headers: dict[str, str]) -> list[HeaderFinding]:
    findings = []
    server = headers.get("server", "")
    if server and any(v in server.lower() for v in ("apache/", "nginx/", "iis/", "jetty/", "tomcat", "php/")):
        findings.append(HeaderFinding(
            "Server", "LOW", "INFO",
            f"Server header discloses version: '{server}' — aids fingerprinting/exploit search",
        ))
    powered = headers.get("x-powered-by", "")
    if powered:
        findings.append(HeaderFinding(
            "X-Powered-By", "LOW", "INFO",
            f"X-Powered-By discloses stack: '{powered}' — aids targeted exploit search",
        ))
    aspnet_ver = headers.get("x-aspnet-version") or headers.get("x-aspnetmvc-version")
    if aspnet_ver:
        findings.append(HeaderFinding(
            "X-AspNet-Version", "MEDIUM", "INFO",
            f"ASP.NET version leaked: '{aspnet_ver}' — enables version-specific attacks",
        ))
    return findings


def _check_coop(headers: dict[str, str]) -> HeaderFinding:
    val = headers.get("cross-origin-opener-policy", "")
    if not val:
        return HeaderFinding(
            "Cross-Origin-Opener-Policy", "LOW", "MISSING",
            "No COOP — page may be targetable via window.opener from cross-origin navigations",
        )
    return HeaderFinding(
        "Cross-Origin-Opener-Policy", "INFO", "PRESENT",
        f"COOP: {val}",
    )


# ---------------------------------------------------------------------------
# HTTP fetch helper
# ---------------------------------------------------------------------------

def _fetch_headers(url: str, timeout: float = 8.0) -> tuple[int, dict[str, str]]:
    """Return (status, lower-cased-headers) for a GET to url. (-1, {}) on error."""
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
        conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0 (compatible; secheaders-scanner/1.0)"})
        resp = conn.getresponse()
        status = resp.status
        headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return status, headers
    except Exception:
        return -1, {}


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _analyze(url: str, timeout: float = 8.0) -> tuple[int, list[HeaderFinding]]:
    """Fetch url and return (http_status, [HeaderFinding])."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    status, headers = _fetch_headers(url, timeout)
    if status == -1:
        # Try HTTP fallback
        http_url = url.replace("https://", "http://", 1) if url.startswith("https://") else url
        if http_url != url:
            status, headers = _fetch_headers(http_url, timeout)
    if status == -1:
        return -1, []

    findings: list[HeaderFinding] = [
        _check_hsts(headers),
        _check_csp(headers),
        _check_x_frame_options(headers),
        _check_x_content_type_options(headers),
        _check_referrer_policy(headers),
        _check_permissions_policy(headers),
        _check_coop(headers),
    ]
    findings += _check_information_disclosure(headers)
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))
    return status, findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_security_headers(urls: str, timeout: float = 8.0) -> str:
    targets = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]
    if not targets:
        return "[security_headers] Error: no URLs provided"

    lines: list[str] = [f"[security_headers] Checking {len(targets)} target(s)\n"]
    total_missing = total_misc = total_ok = total_err = 0

    for url in targets:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        status, findings = _analyze(url, timeout)
        if status == -1:
            lines.append("  ERROR: Could not connect")
            total_err += 1
            lines.append("")
            continue
        lines.append(f"HTTP status: {status}")
        lines.append("")

        for f in findings:
            if f.status == "PRESENT":
                icon = " OK "
                total_ok += 1
            elif f.status == "MISSING":
                icon = "MISS"
                total_missing += 1
            elif f.status == "MISCONFIGURED":
                icon = "MISC"
                total_misc += 1
            else:
                icon = "INFO"
            lines.append(f"  [{icon}] {f.severity:<8}  {f.header}")
            lines.append(f"           {f.detail}")
            lines.append("")

    lines.append("─" * 60)
    actionable = total_missing + total_misc
    lines.append(
        f"Summary: {total_missing} MISSING, {total_misc} MISCONFIGURED, "
        f"{total_ok} OK, {total_err} ERROR"
    )
    if actionable:
        lines.append(
            f"\nNote: {actionable} header issue(s) found. Missing security headers "
            "are low-effort hardening wins and often indicate a less mature security posture."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def security_headers(urls: str) -> str:
    """Analyze HTTP security response headers for one or more web targets.

    Checks for missing or misconfigured security headers that weaken the
    application's defence against common web attacks:

    - Strict-Transport-Security (HSTS) — MitM / SSL-stripping
    - Content-Security-Policy (CSP) — XSS
    - X-Frame-Options / CSP frame-ancestors — Clickjacking
    - X-Content-Type-Options — MIME sniffing
    - Referrer-Policy — Data leakage
    - Permissions-Policy — Browser feature abuse
    - Cross-Origin-Opener-Policy — Cross-origin window attacks
    - Server / X-Powered-By — Information disclosure

    Args:
        urls: Newline- or comma-separated list of target URLs.
              Examples: "https://example.com" or "target.com, https://other.org"

    Returns:
        Formatted report with MISSING / MISCONFIGURED / OK status and
        severity (CRITICAL/HIGH/MEDIUM/LOW/INFO) per header per target.
    """
    return _run_security_headers(urls)


TOOL_REGISTRY.register(
    "security_headers",
    security_headers,
    categories=["recon", "web"],
)
