"""OS command injection (CMDi) probe for web application assessment.

Tests URL query-string parameters for OS command injection vulnerabilities
using time-based blind detection. Since command injection is rarely reflected
in responses (unlike SQLi error-based), time delay is the most reliable
passive canary:

  - Inject sleep commands for Unix (sleep 5) and Windows (ping -n 6 127.0.0.1)
  - Separate the payload with common shell metacharacters: ; & | ` $() { }
  - Measure response time against the baseline

Payloads tested (Unix + Windows, multiple separators):
  ; sleep 5       && sleep 5      || sleep 5
  | sleep 5       `sleep 5`       $(sleep 5)
  ; ping -n 6 127.0.0.1          && ping -n 6 127.0.0.1

Severity:
  CRITICAL — confirmed time delay (≥4s, ≥5× baseline) using a sleep payload
  HIGH     — probable delay (≥3.5s, ≥4× baseline)
  MEDIUM   — possible delay (≥3s, ≥3× baseline) — noise/load possible

Note: A delay baseline_elapsed > 1.5s skips the target parameter (unreliable).

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
# Payloads
# ---------------------------------------------------------------------------

_SLEEP_SECONDS = 5
_PING_COUNT = 6  # ping -n 6 delays about 5 seconds

_UNIX_SLEEP = f"sleep {_SLEEP_SECONDS}"
_WIN_PING = f"ping -n {_PING_COUNT} 127.0.0.1"

# Each payload is (separator, command, platform)
_PAYLOADS: list[tuple[str, str, str]] = [
    # Unix separators
    ("; ",    _UNIX_SLEEP, "unix"),
    (" && ",  _UNIX_SLEEP, "unix"),
    (" || ",  _UNIX_SLEEP, "unix"),
    (" | ",   _UNIX_SLEEP, "unix"),
    ("`",     _UNIX_SLEEP + "`", "unix-backtick"),  # prefix only; value = cmd`
    ("$(",    _UNIX_SLEEP + ")", "unix-subshell"),   # value = $(sleep N)
    # Windows separators
    ("& ",    _WIN_PING, "windows"),
    ("&& ",   _WIN_PING, "windows"),
    ("| ",    _WIN_PING, "windows"),
]

# Minimum expected delay in seconds to flag as injection
_MIN_DELAY = _SLEEP_SECONDS - 1  # 4 seconds


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class CMDiFinding(NamedTuple):
    url: str
    param: str
    payload: str
    severity: str     # CRITICAL | HIGH | MEDIUM | INFO
    status: str       # CONFIRMED | PROBABLE | POTENTIAL | SAFE
    elapsed: float
    baseline: float
    detail: str


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str, timeout: float = 15.0) -> tuple[int, float]:
    """GET url. Returns (status, elapsed_seconds). (-1, 0) on failure."""
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
        t0 = time.monotonic()
        conn.request("GET", path, headers={
            "User-Agent": "Mozilla/5.0 (compatible; cmdi-checker/1.0)",
            "Accept": "*/*",
        })
        resp = conn.getresponse()
        status = resp.status
        resp.read(4096)
        elapsed = time.monotonic() - t0
        conn.close()
        return status, elapsed
    except Exception:
        return -1, 0.0


def _inject_param(base_url: str, param_name: str, param_value: str) -> str:
    """Return base_url with param_name replaced by param_value in the query string."""
    parsed = urllib.parse.urlparse(base_url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    qs[param_name] = [param_value]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs, doseq=True)))


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

def _check_cmdi(url: str, timeout: float = 15.0) -> list[CMDiFinding]:
    """Probe url for OS command injection. Returns all findings."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Baseline
    baseline_status, baseline_elapsed = _get(url, timeout=timeout)
    if baseline_status == -1:
        http_url = url.replace("https://", "http://", 1)
        baseline_status, baseline_elapsed = _get(http_url, timeout=timeout)
        if baseline_status != -1:
            url = http_url

    if baseline_status == -1:
        return [CMDiFinding(url, "", "", "INFO", "SAFE", 0, 0, "Could not connect to target")]

    # Extract parameters
    parsed = urllib.parse.urlparse(url)
    params = list(urllib.parse.parse_qs(parsed.query, keep_blank_values=True).keys())

    if not params:
        return [CMDiFinding(
            url, "", "", "INFO", "SAFE", 0, 0,
            "No query parameters found. Supply parameters via URL "
            "(e.g. https://example.com/run?cmd=ls)"
        )]

    findings: list[CMDiFinding] = []

    for param in params:
        # Skip params where baseline is already slow
        if baseline_elapsed > 1.5:
            findings.append(CMDiFinding(
                url, param, "", "INFO", "SAFE", 0, baseline_elapsed,
                f"Baseline response too slow ({baseline_elapsed:.1f}s) — time-delay detection unreliable for '{param}'"
            ))
            continue

        confirmed = False
        for sep, cmd, platform in _PAYLOADS:
            if confirmed:
                break

            # Build payload: original_value + separator + command
            payload_str = f"test{sep}{cmd}"
            probe_url = _inject_param(url, param, payload_str)
            probe_timeout = timeout + _SLEEP_SECONDS + 3
            _, elapsed = _get(probe_url, timeout=probe_timeout)

            if elapsed <= 0:
                continue

            ratio = elapsed / max(baseline_elapsed, 0.05)

            if elapsed >= _MIN_DELAY and ratio >= 5:
                findings.append(CMDiFinding(
                    url=probe_url,
                    param=param,
                    payload=payload_str,
                    severity="CRITICAL",
                    status="CONFIRMED",
                    elapsed=elapsed,
                    baseline=baseline_elapsed,
                    detail=(
                        f"Command injection confirmed in '{param}' ({platform}): "
                        f"response took {elapsed:.1f}s ({ratio:.1f}× baseline {baseline_elapsed:.2f}s) "
                        f"when `{cmd}` was appended via '{sep.strip()}' separator."
                    ),
                ))
                confirmed = True

            elif elapsed >= _SLEEP_SECONDS * 0.7 and ratio >= 4:
                findings.append(CMDiFinding(
                    url=probe_url,
                    param=param,
                    payload=payload_str,
                    severity="HIGH",
                    status="PROBABLE",
                    elapsed=elapsed,
                    baseline=baseline_elapsed,
                    detail=(
                        f"Probable command injection in '{param}' ({platform}): "
                        f"response took {elapsed:.1f}s ({ratio:.1f}× baseline) — "
                        "likely executed sleep command."
                    ),
                ))

            elif elapsed >= _SLEEP_SECONDS * 0.6 and ratio >= 3:
                findings.append(CMDiFinding(
                    url=probe_url,
                    param=param,
                    payload=payload_str,
                    severity="MEDIUM",
                    status="POTENTIAL",
                    elapsed=elapsed,
                    baseline=baseline_elapsed,
                    detail=(
                        f"Possible command injection in '{param}' ({platform}): "
                        f"response took {elapsed:.1f}s ({ratio:.1f}× baseline) — "
                        "could be server load rather than injection."
                    ),
                ))

    return findings


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_cmdi_probe(targets: str, timeout: float = 15.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[cmdi_probe] Error: no URLs provided"

    lines: list[str] = [f"[cmdi_probe] Probing {len(items)} target(s) for OS command injection\n"]
    total_confirmed = total_probable = total_potential = total_safe = 0

    for url in items:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        lines.append("─" * 60)
        lines.append(f"URL: {url}")
        lines.append("")

        findings = _check_cmdi(url, timeout)

        confirmed = [f for f in findings if f.status == "CONFIRMED"]
        probable = [f for f in findings if f.status == "PROBABLE"]
        potential = [f for f in findings if f.status == "POTENTIAL"]

        if not findings or all(f.status == "SAFE" for f in findings):
            lines.append("  SAFE — No command injection indicators found")
            total_safe += 1
        else:
            for f in (confirmed + probable + potential):
                icon = "!!!" if f.status == "CONFIRMED" else (" ! " if f.status == "PROBABLE" else " ~ ")
                lines.append(f"  [{icon}] {f.severity:<8}  {f.status}")
                lines.append(f"           Param    : {f.param}")
                lines.append(f"           Payload  : {f.payload[:80]}")
                lines.append(f"           Timing   : {f.elapsed:.1f}s (baseline {f.baseline:.2f}s)")
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
            "\nNote: OS command injection allows arbitrary command execution with "
            "web-server privileges. Impact ranges from data exfiltration to full "
            "server compromise. Never pass user input to shell commands; use "
            "subprocess with argument lists, or dedicated APIs that don't invoke a shell."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def cmdi_probe(targets: str) -> str:
    """Probe web endpoints for OS command injection (CMDi) vulnerabilities.

    Injects shell metacharacters (; & | ` $()) followed by sleep/ping commands
    into URL query-string parameters. Detects injection via time delay: if the
    server executes the sleep command, the response will be delayed by
    ~5 seconds. Both Unix (sleep) and Windows (ping) payloads are tried.

    Verdicts:
      CONFIRMED — strong time delay (≥4s and ≥5× baseline)
      PROBABLE  — probable delay (≥3.5s and ≥4× baseline)
      POTENTIAL — moderate delay (≥3s and ≥3× baseline) — could be load

    Args:
        targets: Newline- or comma-separated list of target URLs.
                 URLs must include query parameters to test.
                 Examples:
                   "https://example.com/ping?host=8.8.8.8"
                   "https://app.corp.com/convert?format=pdf&file=report"
                   "target.com/run?cmd=ls, https://other.org/execute?input=test"

    Returns:
        Per-parameter verdict and timing details, with remediation note.
    """
    return _run_cmdi_probe(targets)


TOOL_REGISTRY.register(
    "cmdi_probe",
    cmdi_probe,
    categories=["recon", "web", "exploitation"],
)
