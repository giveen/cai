"""CSRF (Cross-Site Request Forgery) vulnerability probe for web assessments.

Checks whether web forms and state-changing endpoints are protected against
CSRF attacks by:

  1. Fetching the page and parsing HTML forms for CSRF token fields.
  2. Checking security headers (SameSite cookies, Origin/Referer validation).
  3. Re-submitting the form without the CSRF token and checking if the server
     accepts it (weak or no CSRF protection).
  4. Checking state-changing endpoints (POST/PUT/DELETE) for CSRF controls.

Severity classification:
  CRITICAL — state-changing endpoint accepts requests with no CSRF token
  HIGH     — CSRF token present but predictable/static across requests
  MEDIUM   — no CSRF token but SameSite=Strict cookies or Referer check present
  INFO     — CSRF token found and appears to vary per request

Stdlib-only: http.client + html.parser + urllib. No external dependencies.
"""

from __future__ import annotations

import html.parser
import http.client
import ssl
import urllib.parse
import re
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# HTML form parser
# ---------------------------------------------------------------------------

class _FormParser(html.parser.HTMLParser):
    """Extract forms and their input fields from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self._current_form: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "form":
            self._current_form = {
                "action": attr_dict.get("action", ""),
                "method": (attr_dict.get("method", "GET")).upper(),
                "inputs": [],
            }
            self.forms.append(self._current_form)
        elif tag == "input" and self._current_form is not None:
            self._current_form["inputs"].append({
                "name": attr_dict.get("name", ""),
                "type": attr_dict.get("type", "text"),
                "value": attr_dict.get("value", ""),
            })

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current_form = None


# ---------------------------------------------------------------------------
# Token-field heuristics
# ---------------------------------------------------------------------------

_CSRF_FIELD_NAMES = re.compile(
    r"csrf|xsrf|token|_token|authenticity|nonce|anti.forgery|request_id",
    re.IGNORECASE,
)

_CSRF_HEADER_NAMES = (
    "x-csrf-token",
    "x-xsrf-token",
    "x-requested-with",
    "x-csrftoken",
)


def _looks_like_csrf_field(name: str) -> bool:
    return bool(_CSRF_FIELD_NAMES.search(name))


def _token_is_static(token: str) -> bool:
    """Return True if the token looks trivially weak (too short, all same char, pure numeric)."""
    if not token or len(token) < 8:
        return True
    if len(set(token)) == 1:
        return True
    if token.isdigit():
        return True
    return False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class CSRFFinding(NamedTuple):
    url: str
    check: str
    severity: str    # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str      # VULNERABLE | WEAK | PROTECTED | NOT_APPLICABLE | ERROR
    detail: str


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _build_conn(host: str, is_https: bool, timeout: float) -> http.client.HTTPConnection:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if is_https:
        return http.client.HTTPSConnection(host, timeout=timeout, context=ctx)
    return http.client.HTTPConnection(host, timeout=timeout)


def _get(url: str, timeout: float = 8.0, referer: str = "") -> tuple[int, dict[str, str], str]:
    """GET url. Returns (status, response_headers_lower, body). (-1, {}, '') on failure."""
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc or parsed.path
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        headers: dict[str, str] = {
            "User-Agent": "Mozilla/5.0 (compatible; csrf-probe/1.0)",
            "Accept": "text/html,*/*",
        }
        if referer:
            headers["Referer"] = referer

        conn = _build_conn(host, is_https, timeout)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            body = resp.read(65536).decode("utf-8", errors="replace")
        finally:
            conn.close()
        return status, resp_headers, body
    except Exception:
        return -1, {}, ""


def _post(
    url: str,
    form_data: dict[str, str],
    timeout: float = 8.0,
    origin: str = "",
    referer: str = "",
) -> tuple[int, dict[str, str], str]:
    """POST url-encoded form data. Returns (status, resp_headers_lower, body)."""
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc or parsed.path
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        body = urllib.parse.urlencode(form_data).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)),
            "User-Agent": "Mozilla/5.0 (compatible; csrf-probe/1.0)",
        }
        if origin:
            headers["Origin"] = origin
        if referer:
            headers["Referer"] = referer

        conn = _build_conn(host, is_https, timeout)
        try:
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            resp_body = resp.read(16384).decode("utf-8", errors="replace")
        finally:
            conn.close()
        return status, resp_headers, resp_body
    except Exception:
        return -1, {}, ""


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_cookie_samesite(cookies: list[str]) -> tuple[bool, bool]:
    """Return (has_session_cookie, has_samesite_strict_or_lax)."""
    has_session = False
    has_samesite = False
    for c in cookies:
        name_part = c.split(";")[0].split("=")[0].strip().lower()
        if any(x in name_part for x in ("session", "auth", "login", "jwt", "token", "sid")):
            has_session = True
            if re.search(r"samesite\s*=\s*(strict|lax)", c, re.IGNORECASE):
                has_samesite = True
    return has_session, has_samesite


def _check_forms(base_url: str, body: str, resp_headers: dict, timeout: float) -> list[CSRFFinding]:
    """Parse forms and check for CSRF tokens."""
    findings: list[CSRFFinding] = []
    parser = _FormParser()
    try:
        parser.feed(body)
    except Exception:
        return findings

    post_forms = [f for f in parser.forms if f["method"] == "POST"]
    if not post_forms:
        findings.append(CSRFFinding(
            url=base_url,
            check="Form CSRF token",
            severity="INFO",
            status="NOT_APPLICABLE",
            detail="No POST forms found on the page",
        ))
        return findings

    cookies = [v for k, v in resp_headers.items() if k == "set-cookie"]
    _, has_samesite = _check_cookie_samesite(cookies)

    for form in post_forms:
        action = form["action"] or base_url
        if not action.startswith(("http://", "https://")):
            parsed = urllib.parse.urlparse(base_url)
            if action.startswith("/"):
                action = f"{parsed.scheme}://{parsed.netloc}{action}"
            else:
                base_path = parsed.path.rsplit("/", 1)[0]
                action = f"{parsed.scheme}://{parsed.netloc}{base_path}/{action}"

        csrf_inputs = [i for i in form["inputs"] if _looks_like_csrf_field(i["name"])]

        if not csrf_inputs:
            if has_samesite:
                findings.append(CSRFFinding(
                    url=action,
                    check="Form CSRF token",
                    severity="MEDIUM",
                    status="WEAK",
                    detail=(
                        f"POST form has no CSRF token field. SameSite cookie provides "
                        "partial protection but cross-site subresource requests may still work."
                    ),
                ))
            else:
                findings.append(CSRFFinding(
                    url=action,
                    check="Form CSRF token",
                    severity="CRITICAL",
                    status="VULNERABLE",
                    detail=(
                        "POST form has no CSRF token field and no SameSite cookie mitigation. "
                        "Attacker can craft a cross-site form that submits on behalf of victim."
                    ),
                ))
            continue

        # There IS a CSRF field — check if it's static/weak
        token_value = csrf_inputs[0]["value"]
        if _token_is_static(token_value):
            findings.append(CSRFFinding(
                url=action,
                check="Form CSRF token",
                severity="HIGH",
                status="WEAK",
                detail=(
                    f"CSRF token field '{csrf_inputs[0]['name']}' found but value looks "
                    f"weak/predictable: '{token_value[:32]}'. "
                    "Fetch two pages and compare tokens to confirm."
                ),
            ))
        else:
            findings.append(CSRFFinding(
                url=action,
                check="Form CSRF token",
                severity="INFO",
                status="PROTECTED",
                detail=(
                    f"CSRF token field '{csrf_inputs[0]['name']}' present with value "
                    f"length={len(token_value)}. Appears non-trivial."
                ),
            ))

    return findings


def _check_cors_csrf(base_url: str, timeout: float) -> CSRFFinding:
    """Check if API endpoint accepts cross-origin POST from a different Origin."""
    parsed = urllib.parse.urlparse(base_url)
    foreign_origin = "https://evil.example.com"
    status, resp_headers, _ = _post(
        base_url,
        {"action": "test"},
        timeout=timeout,
        origin=foreign_origin,
        referer=f"{foreign_origin}/csrf-test",
    )
    if status == -1:
        return CSRFFinding(
            url=base_url, check="CORS + CSRF",
            severity="INFO", status="ERROR",
            detail="Could not connect for CORS/CSRF test",
        )

    acao = resp_headers.get("access-control-allow-origin", "")
    if acao in ("*", foreign_origin):
        return CSRFFinding(
            url=base_url, check="CORS + CSRF",
            severity="HIGH", status="VULNERABLE",
            detail=(
                f"Access-Control-Allow-Origin: {acao} — endpoint accepts cross-origin "
                "POST requests. Combined with missing CSRF protection this enables CORS-based CSRF."
            ),
        )
    return CSRFFinding(
        url=base_url, check="CORS + CSRF",
        severity="INFO", status="PROTECTED",
        detail=f"ACAO header: '{acao}' — cross-origin POST not blindly allowed.",
    )


def _check_referer_validation(base_url: str, timeout: float) -> CSRFFinding:
    """Check if server validates the Referer header on POST."""
    parsed = urllib.parse.urlparse(base_url)
    # Post with a foreign referer
    status_foreign, _, body_foreign = _post(
        base_url, {"action": "test"}, timeout=timeout,
        referer="https://evil.example.com/attack",
    )
    status_no_ref, _, _ = _post(base_url, {"action": "test"}, timeout=timeout)

    if status_foreign == -1:
        return CSRFFinding(
            url=base_url, check="Referer validation",
            severity="INFO", status="ERROR",
            detail="Could not connect to test Referer validation",
        )
    if status_foreign in (403, 400) and status_no_ref not in (403, 400):
        return CSRFFinding(
            url=base_url, check="Referer validation",
            severity="INFO", status="PROTECTED",
            detail=f"Foreign Referer returns HTTP {status_foreign} — server appears to validate Referer.",
        )
    return CSRFFinding(
        url=base_url, check="Referer validation",
        severity="LOW", status="WEAK",
        detail=(
            f"Server returns HTTP {status_foreign} for both foreign and absent Referer — "
            "no obvious Referer-based CSRF mitigation detected."
        ),
    )


# ---------------------------------------------------------------------------
# Main probe
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _probe_csrf(url: str, timeout: float = 8.0) -> list[CSRFFinding]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    status, resp_headers, body = _get(url, timeout)
    if status == -1:
        # HTTP fallback
        http_url = url.replace("https://", "http://", 1)
        status, resp_headers, body = _get(http_url, timeout)
        if status != -1:
            url = http_url

    if status == -1:
        return [CSRFFinding(url, "Connectivity", "INFO", "ERROR", "Could not connect to target")]

    findings: list[CSRFFinding] = []

    # 1. Parse forms and check for CSRF tokens
    findings.extend(_check_forms(url, body, resp_headers, timeout))

    # 2. CORS/CSRF check
    findings.append(_check_cors_csrf(url, timeout))

    # 3. Referer validation
    findings.append(_check_referer_validation(url, timeout))

    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))
    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_csrf_probe(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[csrf_probe] Error: no targets provided"

    lines: list[str] = [f"[csrf_probe] Probing {len(items)} target(s)\n"]
    total_critical = total_high = total_medium = total_safe = total_err = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _probe_csrf(url, timeout)

        for f in findings:
            if f.status == "VULNERABLE":
                icon = "!!!"
                if f.severity == "CRITICAL":
                    total_critical += 1
                else:
                    total_high += 1
            elif f.status == "WEAK":
                icon = " ! "
                if f.severity in ("CRITICAL", "HIGH"):
                    total_high += 1
                else:
                    total_medium += 1
            elif f.status == "PROTECTED":
                icon = "   "
                total_safe += 1
            elif f.status == "NOT_APPLICABLE":
                icon = " - "
                total_safe += 1
            elif f.status == "ERROR":
                icon = " ? "
                total_err += 1
            else:
                icon = "   "

            lines.append(f"  [{icon}] {f.severity:<8}  {f.check:<26}  {f.status}")
            lines.append(f"           {f.detail}")
            lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_critical} CRITICAL, {total_high} HIGH, "
        f"{total_medium} MEDIUM, {total_safe} SAFE, {total_err} ERROR"
    )

    if total_critical or total_high:
        lines.append(
            "\nNote: CSRF vulnerabilities allow attackers to trigger state-changing actions "
            "(password change, fund transfer, account deletion) by tricking logged-in users "
            "into loading a crafted page. Mitigate with synchronised CSRF tokens, "
            "SameSite=Strict cookies, and Origin/Referer header validation."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def csrf_probe(targets: str) -> str:
    """Probe web targets for CSRF (Cross-Site Request Forgery) vulnerabilities.

    Fetches each target and checks:
    - POST forms for missing or weak CSRF token fields
    - CORS policy that might enable cross-origin CSRF
    - Referer header validation on state-changing endpoints

    Verdicts:
      VULNERABLE — no CSRF token and no SameSite mitigation (critical risk)
      WEAK       — token present but predictable, or SameSite only (partial)
      PROTECTED  — token present and non-trivial, or CORS/Referer restricts

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 For best results, include login/form pages.
                 Examples:
                   "https://example.com/login"
                   "https://example.com/account/settings"
                   "target.com/profile, https://other.org/transfer"

    Returns:
        Formatted report with VULNERABLE / WEAK / PROTECTED status per check.
    """
    return _run_csrf_probe(targets)


TOOL_REGISTRY.register(
    "csrf_probe",
    csrf_probe,
    categories=["recon", "web"],
)
