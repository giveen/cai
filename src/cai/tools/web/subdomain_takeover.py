"""Subdomain takeover checker for web reconnaissance.

Detects dangling CNAME records that point to third-party services where
the target resource can be claimed (S3 buckets, Heroku apps, GitHub Pages,
Azure web apps, etc.).  Stdlib-only: socket + urllib.

Methodology:
  1. Resolve the full CNAME chain for each target.
  2. Match the final CNAME against known vulnerable service fingerprints.
  3. For HTTP-reachable targets, fetch the root page and scan the body for
     "unclaimed" / "not found" phrases specific to each provider.
  4. Report VULNERABLE (body fingerprint matched), PROBABLE (CNAME to known
     service but HTTP check failed / timed out), or SAFE.
"""

from __future__ import annotations

import socket
import urllib.request
import urllib.error
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY

# ---------------------------------------------------------------------------
# Fingerprint database
# Each entry: (cname_suffix_or_exact, [http_body_fingerprints], description)
# ---------------------------------------------------------------------------

_FINGERPRINTS: list[tuple[str, list[str], str]] = [
    # AWS S3
    ("s3.amazonaws.com",
     ["NoSuchBucket", "The specified bucket does not exist"],
     "AWS S3 bucket"),
    ("s3-website",
     ["NoSuchBucket", "The specified bucket does not exist"],
     "AWS S3 static website"),
    # GitHub Pages
    ("github.io",
     ["There isn't a GitHub Pages site here.", "For root URLs"],
     "GitHub Pages"),
    # Heroku
    ("herokuapp.com",
     ["No such app", "herokucdn.com/error-pages/no-such-app"],
     "Heroku app"),
    # Azure
    ("azurewebsites.net",
     ["Web App - Unavailable", "404 Web Site not found"],
     "Azure App Service"),
    ("cloudapp.net",
     ["Web App - Unavailable"],
     "Azure Cloud App"),
    ("trafficmanager.net",
     [],
     "Azure Traffic Manager"),
    # Shopify
    ("myshopify.com",
     ["Sorry, this shop is currently unavailable.", "Only one step left"],
     "Shopify store"),
    # Fastly
    ("fastly.net",
     ["Fastly error: unknown domain"],
     "Fastly CDN"),
    # Ghost
    ("ghost.io",
     ["Failed to resolve DNS for"],
     "Ghost blog"),
    # Surge.sh
    ("surge.sh",
     ["project not found"],
     "Surge.sh site"),
    # Zendesk
    ("zendesk.com",
     ["Help Center Closed"],
     "Zendesk"),
    # Tumblr
    ("tumblr.com",
     ["Whatever you were looking for doesn't currently exist at this address"],
     "Tumblr"),
    # Pantheon
    ("pantheonsite.io",
     ["404 error unknown site"],
     "Pantheon"),
    # Readme.io
    ("readme.io",
     ["Project doesnt exist"],
     "Readme.io"),
    # Cargo
    ("cargocollective.com",
     ["404 Not Found"],
     "Cargo Collective"),
    # GitLab Pages
    ("gitlab.io",
     ["404"],
     "GitLab Pages"),
    # Netlify
    ("netlify.app",
     ["Not Found - Request ID"],
     "Netlify"),
    # Vercel
    ("vercel.app",
     ["The deployment could not be found", "DEPLOYMENT_NOT_FOUND"],
     "Vercel"),
    # DigitalOcean App Platform
    ("ondigitalocean.app",
     [],
     "DigitalOcean App Platform"),
    # WP Engine
    ("wpengine.com",
     [],
     "WP Engine"),
    # Bitbucket
    ("bitbucket.io",
     ["Repository not found"],
     "Bitbucket Pages"),
    # Fly.io
    ("fly.dev",
     ["404 - application not found"],
     "Fly.io"),
    # HubSpot
    ("hubspot.net",
     ["Domain not configured"],
     "HubSpot"),
    # Webflow
    ("webflow.io",
     ["The page you are looking for doesn't exist or has been moved"],
     "Webflow"),
]


# ---------------------------------------------------------------------------
# DNS helpers
# ---------------------------------------------------------------------------

def _resolve_cname_chain(hostname: str, max_hops: int = 10) -> list[str]:
    """Return the CNAME chain for *hostname* using getaddrinfo trick.

    Python's stdlib doesn't expose raw CNAME resolution, so we query
    using socket.getfqdn() for each hop.  For a proper chain we attempt
    successive CNAME lookups by resolving canonical names.  This is
    best-effort — it works for most one-hop CNAMEs used by SaaS providers.
    """
    chain: list[str] = []
    current = hostname.lower().rstrip(".")
    seen: set[str] = set()

    for _ in range(max_hops):
        if current in seen:
            break
        seen.add(current)
        chain.append(current)
        try:
            canonical = socket.getfqdn(current).lower().rstrip(".")
            if canonical == current or canonical in seen:
                break
            current = canonical
        except (socket.gaierror, OSError):
            break

    return chain


def _nxdomain(hostname: str, timeout: float = 5.0) -> bool:
    """Return True when *hostname* has no DNS record (NXDOMAIN)."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(hostname, None)
        return False
    except socket.gaierror as exc:
        # errno 8 = NXDOMAIN, 11 = NXDOMAIN on some platforms
        return exc.errno in (8, 11) or "Name or service not known" in str(exc)
    except Exception:
        return False


def _http_body(url: str, timeout: float = 8.0) -> str:
    """Fetch up to 8 KB of the response body; return empty string on error."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; takeover-checker/1.0)",
                "Accept": "text/html,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(8192)
            return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------

class TakeoverResult(NamedTuple):
    domain: str
    cname_chain: list[str]
    matched_service: str
    status: str          # "VULNERABLE" | "PROBABLE" | "SAFE" | "NXDOMAIN"
    detail: str


def _check_one(domain: str, http_timeout: float) -> TakeoverResult:
    chain = _resolve_cname_chain(domain)
    final = chain[-1] if chain else domain

    # Find matching fingerprint
    matched_fp: tuple[str, list[str], str] | None = None
    for suffix, body_fps, desc in _FINGERPRINTS:
        if suffix in final:
            matched_fp = (suffix, body_fps, desc)
            break

    if matched_fp is None:
        # Check for NXDOMAIN on the final hop as a generic signal
        if len(chain) > 1 and _nxdomain(final):
            return TakeoverResult(
                domain=domain,
                cname_chain=chain,
                matched_service="(unknown service)",
                status="NXDOMAIN",
                detail=f"CNAME target {final!r} resolves to NXDOMAIN — potential dangling record",
            )
        return TakeoverResult(
            domain=domain,
            cname_chain=chain,
            matched_service="",
            status="SAFE",
            detail="No known takeover-vulnerable service in CNAME chain",
        )

    _, body_fps, service_name = matched_fp

    # If there's no HTTP fingerprint needed, mark as PROBABLE from CNAME alone
    if not body_fps:
        return TakeoverResult(
            domain=domain,
            cname_chain=chain,
            matched_service=service_name,
            status="PROBABLE",
            detail=f"CNAME points to {service_name} — no HTTP fingerprint available; verify manually",
        )

    # Try HTTP check on the original domain (not the CNAME target)
    for scheme in ("https", "http"):
        body = _http_body(f"{scheme}://{domain}/", http_timeout)
        if body:
            for fp in body_fps:
                if fp.lower() in body.lower():
                    return TakeoverResult(
                        domain=domain,
                        cname_chain=chain,
                        matched_service=service_name,
                        status="VULNERABLE",
                        detail=f'HTTP body contains fingerprint {fp!r} → {service_name} resource is unclaimed',
                    )
            # Body fetched but no fingerprint — service is likely in use
            return TakeoverResult(
                domain=domain,
                cname_chain=chain,
                matched_service=service_name,
                status="SAFE",
                detail=f"CNAME to {service_name} but HTTP response shows no unclaimed-resource signature",
            )

    # Could not fetch HTTP — report as PROBABLE
    return TakeoverResult(
        domain=domain,
        cname_chain=chain,
        matched_service=service_name,
        status="PROBABLE",
        detail=f"CNAME to {service_name} — HTTP unreachable; verify manually",
    )


# ---------------------------------------------------------------------------
# Tool function
# ---------------------------------------------------------------------------

def _run_subdomain_takeover(domains: str, timeout: float = 8.0) -> str:
    """Core takeover check callable directly in tests."""
    targets = [d.strip().lower() for d in domains.replace(",", "\n").splitlines() if d.strip()]
    if not targets:
        return "[subdomain_takeover] Error: no domains provided"

    lines: list[str] = [f"[subdomain_takeover] Checking {len(targets)} domain(s)\n"]

    vuln_count = probable_count = nxd_count = safe_count = 0

    for domain in targets:
        result = _check_one(domain, timeout)
        cname_str = " → ".join(result.cname_chain) if len(result.cname_chain) > 1 else "(no CNAME)"

        status_tag = {
            "VULNERABLE": "!!! VULNERABLE",
            "PROBABLE":   "[?] PROBABLE",
            "NXDOMAIN":   "[~] NXDOMAIN",
            "SAFE":       "    SAFE",
        }.get(result.status, result.status)

        lines.append(f"{status_tag:20s}  {domain}")
        if result.matched_service:
            lines.append(f"{'':22s}Service : {result.matched_service}")
        lines.append(f"{'':22s}CNAME   : {cname_str}")
        lines.append(f"{'':22s}Detail  : {result.detail}")
        lines.append("")

        if result.status == "VULNERABLE":
            vuln_count += 1
        elif result.status == "PROBABLE":
            probable_count += 1
        elif result.status == "NXDOMAIN":
            nxd_count += 1
        else:
            safe_count += 1

    lines.append("─" * 60)
    lines.append(
        f"Summary: {vuln_count} VULNERABLE, {probable_count} PROBABLE, "
        f"{nxd_count} NXDOMAIN, {safe_count} SAFE"
    )
    if vuln_count or probable_count or nxd_count:
        lines.append(
            "\nRecommendation: remove dangling CNAME records or claim the "
            "unclaimed third-party resources to prevent subdomain takeover."
        )

    return "\n".join(lines)


@function_tool
def subdomain_takeover_checker(domains: str) -> str:
    """Check one or more (sub)domains for subdomain takeover vulnerabilities.

    Resolves CNAME chains and checks whether the target resolves to a
    third-party service (S3, Heroku, GitHub Pages, Azure, Netlify, Vercel,
    etc.) that has not been claimed.  For HTTP-reachable targets it fetches
    the response body and checks for provider-specific "unclaimed" fingerprints.

    Args:
        domains: Newline- or comma-separated list of domain names to check.
                 Examples: "sub.example.com" or "a.example.com,b.example.com"

    Returns:
        A formatted report with VULNERABLE / PROBABLE / NXDOMAIN / SAFE status
        for each domain, plus a summary line.
    """
    return _run_subdomain_takeover(domains)


TOOL_REGISTRY.register(
    "subdomain_takeover_checker",
    subdomain_takeover_checker,
    categories=["recon", "web"],
)
