"""Rate limit probe for web endpoint assessment.

Sends a configurable burst of identical or slightly-varied requests
to a target endpoint and measures whether the server enforces any
kind of rate limiting (HTTP 429, 503, Retry-After headers, etc.).

Use cases:
  - Check if login/auth endpoints have brute-force protection
  - Verify that API endpoints honour per-IP / per-user rate limits
  - Identify lack of bot protection on sensitive resources

Stdlib-only: http.client + threading. No external dependencies.
"""

from __future__ import annotations

import http.client
import ssl
import threading
import time
import urllib.parse
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class _Result(NamedTuple):
    seq: int
    status: int
    elapsed_ms: float
    retry_after: str
    rate_limit_headers: dict[str, str]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_RATE_LIMIT_RESPONSE_CODES = {429, 503}

_RATE_LIMIT_HEADER_PREFIXES = (
    "x-ratelimit",
    "x-rate-limit",
    "retry-after",
    "x-retry-after",
    "ratelimit-",
    "x-rl-",
    "x-limit",
    "cf-ratelimit",
)

_SENTINEL_HEADERS = {
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}


def _is_rl_header(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(p) for p in _RATE_LIMIT_HEADER_PREFIXES)


def _build_conn(host: str, is_https: bool, timeout: float) -> http.client.HTTPConnection:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if is_https:
        return http.client.HTTPSConnection(host, timeout=timeout, context=ctx)
    return http.client.HTTPConnection(host, timeout=timeout)


def _single_request(
    host: str,
    is_https: bool,
    path: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
    seq: int,
) -> _Result:
    t0 = time.monotonic()
    try:
        conn = _build_conn(host, is_https, timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers_raw = resp.getheaders()
            resp.read(1024)
        finally:
            conn.close()
    except Exception:
        elapsed = (time.monotonic() - t0) * 1000
        return _Result(seq=seq, status=-1, elapsed_ms=elapsed, retry_after="", rate_limit_headers={})

    elapsed = (time.monotonic() - t0) * 1000
    rl_hdrs = {k.lower(): v for k, v in resp_headers_raw if _is_rl_header(k)}
    retry_after = rl_hdrs.get("retry-after", "")
    return _Result(seq=seq, status=status, elapsed_ms=elapsed, retry_after=retry_after, rate_limit_headers=rl_hdrs)


# ---------------------------------------------------------------------------
# Core probe
# ---------------------------------------------------------------------------

def _probe_rate_limit(
    url: str,
    method: str,
    count: int,
    concurrency: int,
    delay_ms: float,
    post_body: str,
    req_timeout: float,
) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    is_https = parsed.scheme == "https"
    host = parsed.netloc or parsed.path
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    method = method.upper()
    body_bytes: bytes | None = None
    base_headers: dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (compatible; rate-limit-probe/1.0)",
        "Accept": "*/*",
        "Connection": "close",
    }
    if method in ("POST", "PUT", "PATCH") and post_body:
        body_bytes = post_body.encode("utf-8")
        base_headers["Content-Type"] = "application/x-www-form-urlencoded"
        base_headers["Content-Length"] = str(len(body_bytes))

    results: list[_Result | None] = [None] * count
    lock = threading.Lock()

    def do_request(seq: int) -> None:
        r = _single_request(host, is_https, path, method, body_bytes, base_headers, req_timeout, seq)
        with lock:
            results[seq] = r

    lines: list[str] = [
        f"[rate_limit_probe] {method} {url}",
        f"  Requests: {count}  Concurrency: {concurrency}  Delay: {delay_ms:.0f}ms between waves",
        "",
    ]

    delay_s = delay_ms / 1000.0
    sent = 0
    wave = 0
    while sent < count:
        batch_size = min(concurrency, count - sent)
        threads = [
            threading.Thread(target=do_request, args=(sent + i,), daemon=True)
            for i in range(batch_size)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sent += batch_size
        wave += 1
        if delay_s > 0 and sent < count:
            time.sleep(delay_s)

    # Analyse results
    valid = [r for r in results if r is not None and r.status != -1]
    errors = [r for r in results if r is None or r.status == -1]
    status_counts: dict[int, int] = {}
    for r in valid:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    rl_responses = [r for r in valid if r.status in _RATE_LIMIT_RESPONSE_CODES]
    first_rl = rl_responses[0] if rl_responses else None
    first_success = next((r for r in valid if 200 <= r.status < 300), None)

    # Collect all rate-limit headers seen
    all_rl_headers: dict[str, str] = {}
    for r in valid:
        all_rl_headers.update(r.rate_limit_headers)

    # Timing analysis
    latencies = [r.elapsed_ms for r in valid]
    avg_ms = sum(latencies) / len(latencies) if latencies else 0
    max_ms = max(latencies) if latencies else 0

    # Verdict
    has_rl_code = bool(rl_responses)
    has_rl_headers = bool(all_rl_headers)
    first_rl_seq = first_rl.seq if first_rl else None

    if has_rl_code:
        verdict = "RATE_LIMITED"
        severity = "INFO"
        desc = (
            f"Server returned HTTP {first_rl.status} after {first_rl_seq + 1} request(s). "
            "Rate limiting is enforced."
        )
    elif has_rl_headers:
        verdict = "RATE_LIMITED"
        severity = "INFO"
        desc = "Server sends rate-limit headers (no 429/503 seen in this burst, but controls exist)."
    else:
        verdict = "NO_RATE_LIMIT_DETECTED"
        severity = "HIGH"
        desc = (
            f"All {len(valid)} requests received 2xx/3xx without any rate-limit response or headers. "
            "The endpoint may lack brute-force / bot protection."
        )

    lines.append(f"  Verdict  : {verdict}")
    lines.append(f"  Severity : {severity}")
    lines.append(f"  Detail   : {desc}")
    lines.append("")

    # Status distribution
    lines.append("  Status code distribution:")
    for code, cnt in sorted(status_counts.items()):
        label = ""
        if code in _RATE_LIMIT_RESPONSE_CODES:
            label = "  ← rate limit"
        elif 200 <= code < 300:
            label = "  ← success"
        elif code in (301, 302, 307, 308):
            label = "  ← redirect"
        elif code >= 400:
            label = "  ← client/server error"
        lines.append(f"    HTTP {code}: {cnt:4d} requests{label}")
    if errors:
        lines.append(f"    Timeout/connect error: {len(errors):4d} requests")

    # Rate-limit headers
    if all_rl_headers:
        lines.append("")
        lines.append("  Rate-limit response headers observed:")
        for k, v in sorted(all_rl_headers.items()):
            lines.append(f"    {k}: {v}")

    # Timing
    lines.append("")
    lines.append(f"  Latency : avg={avg_ms:.1f}ms  max={max_ms:.1f}ms  samples={len(latencies)}")

    # Where rate limiting kicked in
    if first_rl_seq is not None:
        lines.append(f"  Rate limit triggered on request #{first_rl_seq + 1} of {count}")
        if first_rl.retry_after:
            lines.append(f"  Retry-After: {first_rl.retry_after}")

    if verdict == "NO_RATE_LIMIT_DETECTED":
        lines.append("")
        lines.append(
            "  Recommendation: Protect this endpoint with rate limiting (e.g. NGINX limit_req,")
        lines.append(
            "  Cloudflare Rate Rules, or application-level token bucket) to prevent brute-force attacks.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def rate_limit_probe(
    targets: str,
    method: str = "GET",
    count: int = 30,
    concurrency: int = 5,
    delay_ms: float = 0.0,
    post_body: str = "",
) -> str:
    """Probe web endpoints for missing or weak rate limiting.

    Sends a configurable burst of requests to each target and checks for:
    - HTTP 429 (Too Many Requests) or 503 responses that signal rate limiting
    - Rate-limit response headers (X-RateLimit-*, Retry-After, etc.)
    - Absence of any rate-limit signal despite a large burst

    Common use cases:
    - Login form brute-force protection check (POST to /login)
    - API endpoint abuse prevention (GET/POST to API route)
    - OTP / password-reset endpoint limit verification

    Args:
        targets:     Newline- or comma-separated list of target URLs.
        method:      HTTP method — GET, POST, PUT, HEAD (default: GET).
        count:       Total number of requests to send per target (default: 30).
        concurrency: Concurrent threads per wave (default: 5). Increase for
                     heavier load; keep low to avoid false-negative from
                     connection limits.
        delay_ms:    Milliseconds to wait between concurrent waves (default: 0).
                     Set to 1000 to simulate 1-second-spaced requests.
        post_body:   URL-encoded body for POST/PUT requests
                     (e.g. "username=admin&password=test").

    Returns:
        Formatted report per endpoint with verdict, status distribution,
        rate-limit headers observed, and latency stats.

    Examples:
        rate_limit_probe("https://example.com/login", method="POST",
                         count=50, post_body="user=a&pass=x")
        rate_limit_probe("https://api.example.com/reset-password", count=20)
    """
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[rate_limit_probe] Error: no targets provided"

    parts: list[str] = []
    for url in items:
        try:
            parts.append(_probe_rate_limit(url, method, count, concurrency, delay_ms, post_body, 8.0))
        except Exception as exc:
            parts.append(f"[rate_limit_probe] Error probing {url}: {exc}")
        parts.append("")

    return "\n".join(parts).rstrip()


TOOL_REGISTRY.register(
    "rate_limit_probe",
    rate_limit_probe,
    categories=["recon", "web"],
)
