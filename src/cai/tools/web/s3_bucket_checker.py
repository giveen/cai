"""S3 bucket misconfiguration checker for red team reconnaissance.

Tests publicly accessible S3 buckets for common misconfigurations:
- Public listing (LIST/GetBucket): anyone can enumerate bucket contents
- Public read (GET): anyone can download arbitrary objects
- Public write (PUT): anyone can upload objects
- Policy exposure (?policy): bucket policy visible without credentials
- ACL exposure (?acl): ACL visible without credentials

Stdlib-only: urllib + http.client for raw HTTP.
No AWS credentials required — this tests for *unauthenticated* access.
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY

# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; s3-checker/1.0)",
}

_PROBE_OBJECT_KEY = ".well-known/s3-checker-probe-read-test.txt"
_PROBE_WRITE_CONTENT = b"s3-checker-write-test"
_PROBE_WRITE_KEY = "s3-checker-write-probe-do-not-use.txt"


def _http(method: str, url: str, body: bytes | None = None, timeout: float = 8.0) -> tuple[int, str]:
    """Return (status_code, body_text) for an HTTP request.

    Returns (-1, error_message) on connection/timeout failure.
    """
    parsed = urllib.parse.urlparse(url)
    is_https = parsed.scheme == "https"
    host = parsed.netloc
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    try:
        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(host, timeout=timeout)
        headers: dict[str, str] = dict(_HEADERS)
        if body is not None:
            headers["Content-Length"] = str(len(body))
            headers["Content-Type"] = "application/octet-stream"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(8192)
        return resp.status, raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return -1, str(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bucket URL helpers
# ---------------------------------------------------------------------------

def _bucket_urls(bucket_name: str) -> list[str]:
    """Return candidate bucket root URLs to probe (path-style + virtual-hosted)."""
    safe = urllib.parse.quote(bucket_name)
    return [
        f"https://{bucket_name}.s3.amazonaws.com/",
        f"https://s3.amazonaws.com/{safe}/",
        f"http://{bucket_name}.s3.amazonaws.com/",
    ]


def _first_reachable(bucket_name: str, timeout: float) -> tuple[str, int, str]:
    """Return (url, status, body) for the first reachable bucket URL, or ("", -1, error)."""
    for url in _bucket_urls(bucket_name):
        status, body = _http("GET", url, timeout=timeout)
        if status != -1:
            return url, status, body
    return "", -1, "All URLs unreachable"


# ---------------------------------------------------------------------------
# Misconfiguration checks
# ---------------------------------------------------------------------------

class S3Finding(NamedTuple):
    check: str
    severity: str   # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str     # VULNERABLE | SAFE | ERROR
    detail: str


def _check_public_list(base_url: str, timeout: float) -> S3Finding:
    """Can an anonymous user list bucket contents?"""
    status, body = _http("GET", base_url, timeout=timeout)
    if status == 200 and "<ListBucketResult" in body:
        key_count = body.count("<Key>")
        return S3Finding(
            check="Public LIST",
            severity="HIGH",
            status="VULNERABLE",
            detail=f"Bucket contents enumerable without credentials (~{key_count} keys visible in first 8KB)",
        )
    if status in (403, 400):
        return S3Finding(
            check="Public LIST",
            severity="HIGH",
            status="SAFE",
            detail=f"Listing denied (HTTP {status})",
        )
    if status == 301:
        return S3Finding(
            check="Public LIST",
            severity="HIGH",
            status="SAFE",
            detail="Redirect — bucket exists but listing denied or redirected",
        )
    return S3Finding(
        check="Public LIST",
        severity="HIGH",
        status="ERROR",
        detail=f"Unexpected HTTP {status}: {body[:120]}",
    )


def _check_public_read(base_url: str, timeout: float) -> S3Finding:
    """Can an anonymous user read an arbitrary object?"""
    url = base_url.rstrip("/") + "/" + _PROBE_OBJECT_KEY
    status, body = _http("GET", url, timeout=timeout)
    # 404 means the bucket exists but the object doesn't — readable if it existed
    # 403 means denied; 200 means the object exists and is readable
    if status == 200:
        return S3Finding(
            check="Public READ",
            severity="HIGH",
            status="VULNERABLE",
            detail="Object readable without credentials (probe object exists and returned 200)",
        )
    if status == 403:
        return S3Finding(
            check="Public READ",
            severity="HIGH",
            status="SAFE",
            detail="Read denied (HTTP 403) — bucket exists, objects not publicly readable",
        )
    if status == 404:
        # Key doesn't exist, but check if error says "NoSuchBucket" vs "NoSuchKey"
        if "NoSuchBucket" in body:
            return S3Finding(
                check="Public READ",
                severity="HIGH",
                status="SAFE",
                detail="Bucket does not exist (NoSuchBucket) — no misconfiguration",
            )
        return S3Finding(
            check="Public READ",
            severity="HIGH",
            status="SAFE",
            detail="Object not found (bucket exists, but probe key absent — likely read-protected or empty)",
        )
    return S3Finding(
        check="Public READ",
        severity="HIGH",
        status="ERROR",
        detail=f"HTTP {status}: {body[:120]}",
    )


def _check_public_write(base_url: str, timeout: float) -> S3Finding:
    """Can an anonymous user upload an object?"""
    url = base_url.rstrip("/") + "/" + _PROBE_WRITE_KEY
    status, _body = _http("PUT", url, body=_PROBE_WRITE_CONTENT, timeout=timeout)
    if status in (200, 204):
        # Attempt cleanup (best effort — don't block on failure)
        _http("DELETE", url, timeout=timeout)
        return S3Finding(
            check="Public WRITE",
            severity="CRITICAL",
            status="VULNERABLE",
            detail="Object uploaded without credentials (HTTP %d) — bucket is publicly writable" % status,
        )
    if status in (403, 400, 405):
        return S3Finding(
            check="Public WRITE",
            severity="CRITICAL",
            status="SAFE",
            detail=f"Write denied (HTTP {status})",
        )
    return S3Finding(
        check="Public WRITE",
        severity="CRITICAL",
        status="ERROR",
        detail=f"HTTP {status}: unexpected response to unauthenticated PUT",
    )


def _check_policy_exposure(base_url: str, timeout: float) -> S3Finding:
    """Is the bucket policy readable without credentials?"""
    url = base_url.rstrip("/") + "/?policy"
    status, body = _http("GET", url, timeout=timeout)
    if status == 200 and body.strip().startswith("{"):
        return S3Finding(
            check="Policy exposure",
            severity="MEDIUM",
            status="VULNERABLE",
            detail="Bucket policy returned without credentials — review for over-permissive statements",
        )
    if status in (403, 405):
        return S3Finding(
            check="Policy exposure",
            severity="MEDIUM",
            status="SAFE",
            detail=f"Policy endpoint denied (HTTP {status})",
        )
    return S3Finding(
        check="Policy exposure",
        severity="MEDIUM",
        status="SAFE",
        detail=f"HTTP {status} — policy not exposed",
    )


def _check_acl_exposure(base_url: str, timeout: float) -> S3Finding:
    """Is the bucket ACL readable without credentials?"""
    url = base_url.rstrip("/") + "/?acl"
    status, body = _http("GET", url, timeout=timeout)
    if status == 200 and "<AccessControlPolicy" in body:
        public_read = "http://acs.amazonaws.com/groups/global/AllUsers" in body
        sev = "HIGH" if public_read else "MEDIUM"
        note = "AllUsers group has access — bucket is public" if public_read else "ACL readable without credentials"
        return S3Finding(
            check="ACL exposure",
            severity=sev,
            status="VULNERABLE",
            detail=note,
        )
    if status in (403, 405):
        return S3Finding(
            check="ACL exposure",
            severity="MEDIUM",
            status="SAFE",
            detail=f"ACL endpoint denied (HTTP {status})",
        )
    return S3Finding(
        check="ACL exposure",
        severity="MEDIUM",
        status="SAFE",
        detail=f"HTTP {status}",
    )


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _run_s3_bucket_check(buckets: str, timeout: float = 8.0, skip_write: bool = True) -> str:
    """Core check callable directly in tests."""
    targets = [b.strip() for b in buckets.replace(",", "\n").splitlines() if b.strip()]
    if not targets:
        return "[s3_bucket_checker] Error: no bucket names provided"

    output: list[str] = [f"[s3_bucket_checker] Checking {len(targets)} bucket(s)\n"]

    total_vuln = total_safe = total_error = 0

    for bucket in targets:
        output.append(f"{'─' * 60}")
        output.append(f"Bucket: {bucket}")

        base_url, init_status, init_body = _first_reachable(bucket, timeout)
        if init_status == -1:
            output.append(f"  ERROR: {init_body}")
            total_error += 1
            output.append("")
            continue

        if "NoSuchBucket" in init_body:
            output.append(f"  SAFE  — Bucket does not exist (HTTP {init_status} NoSuchBucket)")
            total_safe += 1
            output.append("")
            continue

        output.append(f"  Endpoint: {base_url}")
        output.append("")

        checks = [
            _check_public_list(base_url, timeout),
            _check_public_read(base_url, timeout),
            _check_acl_exposure(base_url, timeout),
            _check_policy_exposure(base_url, timeout),
        ]
        if not skip_write:
            checks.append(_check_public_write(base_url, timeout))

        # Sort by severity
        checks.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))

        for finding in checks:
            icon = "!!!" if finding.status == "VULNERABLE" else ("   " if finding.status == "SAFE" else " ? ")
            output.append(f"  [{icon}] {finding.severity:<8}  {finding.check:<20}  {finding.status}")
            output.append(f"           {finding.detail}")
            output.append("")
            if finding.status == "VULNERABLE":
                total_vuln += 1
            elif finding.status == "SAFE":
                total_safe += 1
            else:
                total_error += 1

    output.append("─" * 60)
    output.append(f"Summary: {total_vuln} VULNERABLE, {total_safe} SAFE, {total_error} ERROR/UNKNOWN")

    if total_vuln:
        output.append(
            "\nRecommendation: disable public access via S3 Block Public Access settings "
            "and audit bucket policies and ACLs."
        )
    if not skip_write:
        output.append("\nNote: write-probe mode was active — a test object was uploaded and removed.")

    return "\n".join(output)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def s3_bucket_checker(
    buckets: str,
    skip_write_probe: bool = True,
) -> str:
    """Check one or more S3 bucket names for public misconfiguration.

    Tests without AWS credentials — detects unauthenticated access only:
    - Public LIST: can anonymous users enumerate bucket contents?
    - Public READ: can anonymous users download objects?
    - ACL exposure: is the bucket ACL readable without auth?
    - Policy exposure: is the bucket policy readable without auth?
    - Public WRITE (opt-in): can anonymous users upload objects?
      (disabled by default to avoid unintended writes on live buckets)

    Args:
        buckets: Newline- or comma-separated list of bucket names (not URLs).
                 Examples: "my-corp-assets" or "logs-prod,backups-staging"
        skip_write_probe: When True (default), skip the PUT write probe.
                          Set False only when explicitly authorised to test write access.

    Returns:
        A formatted report with VULNERABLE / SAFE / ERROR status for each check,
        plus a remediation recommendation.
    """
    return _run_s3_bucket_check(buckets, skip_write=skip_write_probe)


TOOL_REGISTRY.register(
    "s3_bucket_checker",
    s3_bucket_checker,
    categories=["recon", "web", "cloud"],
)
