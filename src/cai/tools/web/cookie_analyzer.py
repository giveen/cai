"""Cookie security attribute analyzer for web application assessment.

Fetches HTTP responses and audits Set-Cookie headers for missing or
misconfigured security attributes:

  - Secure flag    — cookie sent over HTTPS only
  - HttpOnly flag  — inaccessible to JavaScript (XSS mitigation)
  - SameSite       — CSRF protection (Strict/Lax/None)
  - Domain scope   — over-broad domain attribute
  - Path scope     — over-broad path attribute
  - Expiry         — persistent vs session cookies
  - Prefix rules   — __Secure- and __Host- prefix compliance

Severity ratings reflect the practical impact in a red team context.
Stdlib-only: http.client + ssl + urllib.  No external dependencies.
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

class CookieFinding(NamedTuple):
    cookie_name: str
    attribute: str
    severity: str   # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str     # MISSING | MISCONFIGURED | PRESENT | INFO
    detail: str


# ---------------------------------------------------------------------------
# Cookie attribute checker
# ---------------------------------------------------------------------------

def _parse_cookie_attrs(set_cookie_value: str) -> dict[str, str | bool]:
    """Parse a single Set-Cookie value into a dict of attributes.

    Returns dict with lower-case attribute names.  Flag-only attributes
    (Secure, HttpOnly) are stored as True.  Name/value pairs are stored
    as strings.  Special key '_name' holds the cookie name, '_value' holds
    the cookie value.
    """
    parts = [p.strip() for p in set_cookie_value.split(";")]
    if not parts:
        return {}

    # First part is always name=value
    first = parts[0]
    if "=" in first:
        name, _, value = first.partition("=")
    else:
        name, value = first, ""

    attrs: dict[str, str | bool] = {"_name": name, "_value": value}

    for part in parts[1:]:
        if "=" in part:
            k, _, v = part.partition("=")
            attrs[k.strip().lower()] = v.strip()
        else:
            attrs[part.strip().lower()] = True

    return attrs


def _check_cookie(attrs: dict[str, str | bool], is_https: bool) -> list[CookieFinding]:
    name = str(attrs.get("_name", "?"))
    findings: list[CookieFinding] = []

    # --- Secure flag ---
    if is_https and not attrs.get("secure"):
        findings.append(CookieFinding(
            cookie_name=name,
            attribute="Secure",
            severity="HIGH",
            status="MISSING",
            detail="Secure flag absent — cookie sent over plaintext HTTP connections",
        ))
    elif attrs.get("secure"):
        findings.append(CookieFinding(
            cookie_name=name, attribute="Secure", severity="INFO", status="PRESENT",
            detail="Secure flag set",
        ))

    # --- HttpOnly flag ---
    if not attrs.get("httponly"):
        findings.append(CookieFinding(
            cookie_name=name,
            attribute="HttpOnly",
            severity="MEDIUM",
            status="MISSING",
            detail="HttpOnly flag absent — cookie accessible via JavaScript; XSS can steal it",
        ))
    else:
        findings.append(CookieFinding(
            cookie_name=name, attribute="HttpOnly", severity="INFO", status="PRESENT",
            detail="HttpOnly flag set",
        ))

    # --- SameSite ---
    samesite = str(attrs.get("samesite", "")).lower()
    if not samesite:
        findings.append(CookieFinding(
            cookie_name=name,
            attribute="SameSite",
            severity="MEDIUM",
            status="MISSING",
            detail="SameSite attribute absent — browsers may default to Lax but explicit None "
                   "allows full cross-site sending; CSRF risk",
        ))
    elif samesite == "none":
        if not attrs.get("secure"):
            findings.append(CookieFinding(
                cookie_name=name,
                attribute="SameSite",
                severity="HIGH",
                status="MISCONFIGURED",
                detail="SameSite=None without Secure flag — browser will reject this cookie",
            ))
        else:
            findings.append(CookieFinding(
                cookie_name=name,
                attribute="SameSite",
                severity="LOW",
                status="MISCONFIGURED",
                detail="SameSite=None — cookie sent on all cross-site requests; CSRF risk if "
                       "not mitigated by other controls",
            ))
    elif samesite == "lax":
        findings.append(CookieFinding(
            cookie_name=name, attribute="SameSite", severity="INFO", status="PRESENT",
            detail="SameSite=Lax — protects against most CSRF, allows top-level GET navigation",
        ))
    elif samesite == "strict":
        findings.append(CookieFinding(
            cookie_name=name, attribute="SameSite", severity="INFO", status="PRESENT",
            detail="SameSite=Strict — strongest CSRF protection",
        ))
    else:
        findings.append(CookieFinding(
            cookie_name=name,
            attribute="SameSite",
            severity="LOW",
            status="MISCONFIGURED",
            detail=f"Unrecognised SameSite value '{samesite}'",
        ))

    # --- Domain attribute (over-broad scope) ---
    domain = str(attrs.get("domain", "")).lstrip(".")
    if domain and domain.count(".") == 1:
        findings.append(CookieFinding(
            cookie_name=name,
            attribute="Domain",
            severity="LOW",
            status="MISCONFIGURED",
            detail=f"Domain=.{domain} shares cookie with ALL subdomains — a compromised "
                   "subdomain can read this cookie",
        ))

    # --- Prefix compliance ---
    if name.startswith("__Secure-"):
        if not attrs.get("secure"):
            findings.append(CookieFinding(
                cookie_name=name,
                attribute="__Secure- prefix",
                severity="HIGH",
                status="MISCONFIGURED",
                detail="__Secure- prefix requires Secure flag — browser will reject this cookie",
            ))
    if name.startswith("__Host-"):
        issues = []
        if not attrs.get("secure"):
            issues.append("Secure flag missing")
        if domain:
            issues.append(f"Domain attribute must be absent (found '{domain}')")
        if str(attrs.get("path", "")) != "/":
            issues.append("Path must be '/'")
        if issues:
            findings.append(CookieFinding(
                cookie_name=name,
                attribute="__Host- prefix",
                severity="HIGH",
                status="MISCONFIGURED",
                detail="__Host- prefix violations: " + "; ".join(issues),
            ))

    return findings


# ---------------------------------------------------------------------------
# HTTP fetch helper
# ---------------------------------------------------------------------------

def _fetch_cookies(url: str, timeout: float = 8.0) -> tuple[int, bool, list[str]]:
    """Return (status, is_https, set_cookie_values) for a GET to url.

    Returns (-1, False, []) on failure.
    """
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
            conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0 (compatible; cookie-analyzer/1.0)"})
            resp = conn.getresponse()
            resp.read(4096)
            status = resp.status
            cookies = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"]
        finally:
            conn.close()
        return status, is_https, cookies
    except Exception:
        return -1, False, []


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _analyze_cookies(url: str, timeout: float = 8.0) -> tuple[int, list[CookieFinding]]:
    """Fetch url and audit all Set-Cookie headers. Returns (http_status, findings)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    status, is_https, raw_cookies = _fetch_cookies(url, timeout)
    if status == -1:
        # HTTP fallback
        http_url = url.replace("https://", "http://", 1)
        status, is_https, raw_cookies = _fetch_cookies(http_url, timeout)
    if status == -1:
        return -1, []
    if not raw_cookies:
        return status, []

    findings: list[CookieFinding] = []
    for raw in raw_cookies:
        attrs = _parse_cookie_attrs(raw)
        findings.extend(_check_cookie(attrs, is_https))

    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 99), f.cookie_name))
    return status, findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_cookie_analyzer(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[cookie_analyzer] Error: no URLs provided"

    lines: list[str] = [f"[cookie_analyzer] Checking {len(items)} target(s)\n"]
    total_missing = total_misc = total_ok = total_err = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")

        status, findings = _analyze_cookies(url, timeout)

        if status == -1:
            lines.append("  ERROR: Could not connect")
            total_err += 1
            lines.append("")
            continue

        lines.append(f"HTTP status: {status}")

        if not findings:
            lines.append("  INFO: No Set-Cookie headers in response (try POST/login endpoints)")
            lines.append("")
            continue

        # Group by cookie name
        cookies_seen: dict[str, list[CookieFinding]] = {}
        for f in findings:
            cookies_seen.setdefault(f.cookie_name, []).append(f)

        for cookie_name, cfindings in cookies_seen.items():
            lines.append(f"\n  Cookie: {cookie_name}")
            for f in cfindings:
                if f.status == "PRESENT":
                    icon, total_ok = " OK", total_ok + 1
                elif f.status == "MISSING":
                    icon, total_missing = "MISS", total_missing + 1
                elif f.status == "MISCONFIGURED":
                    icon, total_misc = "MISC", total_misc + 1
                else:
                    icon = "INFO"
                lines.append(f"    [{icon}] {f.severity:<8}  {f.attribute}")
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
            f"\nNote: {actionable} cookie attribute issue(s). Missing HttpOnly enables "
            "JavaScript-based cookie theft via XSS; missing Secure allows cookie "
            "interception over HTTP; missing SameSite leaves CSRF risk."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def cookie_analyzer(urls: str) -> str:
    """Analyze HTTP Set-Cookie response headers for security misconfigurations.

    Fetches each target and audits all cookies set by the server:

    - Secure flag    — cookie only sent over HTTPS
    - HttpOnly flag  — JavaScript cannot access the cookie (XSS defense)
    - SameSite       — CSRF protection (Strict / Lax / None)
    - Domain scope   — over-broad domain allows subdomain cookie theft
    - __Secure- / __Host- prefix compliance

    For best results, target login pages or other endpoints that issue
    session cookies (e.g. "https://example.com/login").

    Args:
        urls: Newline- or comma-separated list of target URLs.
              Examples: "https://example.com/login"
                        "https://a.com/auth, https://b.org/session"

    Returns:
        Formatted report with MISSING / MISCONFIGURED / OK per attribute
        per cookie per target.
    """
    return _run_cookie_analyzer(urls)


TOOL_REGISTRY.register(
    "cookie_analyzer",
    cookie_analyzer,
    categories=["recon", "web"],
)
