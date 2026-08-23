"""HTTP technology fingerprinter for web security assessments.

Identifies server software, frameworks, CMS platforms, languages, and
security posture from HTTP headers, cookies, and common path probes.
Uses stdlib only (http.client, ssl, urllib.parse).
"""

from __future__ import annotations

import http.client
import re
import ssl
import urllib.parse
from typing import Any


# ─── HTTP helper ─────────────────────────────────────────────────────────────

def _make_connection(parsed: urllib.parse.ParseResult, timeout: float = 10):
    host = parsed.netloc or parsed.hostname or ""
    if parsed.scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return http.client.HTTPSConnection(host, context=ctx, timeout=timeout)
    return http.client.HTTPConnection(host, timeout=timeout)


def _fetch(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> tuple[int, dict[str, str], str]:
    """Return (status, lowercase_headers, body_preview)."""
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    req_headers = {
        "Host": parsed.hostname or "",
        "User-Agent": "Mozilla/5.0 (compatible; CAI-TechFingerprint/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "close",
    }
    if headers:
        req_headers.update(headers)
    conn = _make_connection(parsed, timeout=timeout)
    try:
        conn.request(method, path, headers=req_headers)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        body = resp.read(8192).decode("utf-8", errors="replace")
        return resp.status, resp_headers, body
    finally:
        conn.close()


# ─── Fingerprint rules ────────────────────────────────────────────────────────

# (header_name_lower, pattern, technology, category)
_HEADER_RULES: list[tuple[str, str | None, str, str]] = [
    # Web servers
    ("server", r"(?i)\bnginx\b", "nginx", "server"),
    ("server", r"(?i)\bapache\b", "Apache", "server"),
    ("server", r"(?i)\bMicrosoft-IIS\b", "IIS", "server"),
    ("server", r"(?i)\blighttpd\b", "lighttpd", "server"),
    ("server", r"(?i)\bcaddy\b", "Caddy", "server"),
    ("server", r"(?i)\bgws\b", "Google Web Server", "server"),
    ("server", r"(?i)\bopenresty\b", "OpenResty/nginx", "server"),
    ("server", r"(?i)\btomcat\b", "Apache Tomcat", "server"),
    ("server", r"(?i)\bJetty\b", "Jetty", "server"),
    ("server", r"(?i)\bGunicorn\b", "Gunicorn", "server"),
    ("server", r"(?i)\buvicorn\b", "Uvicorn", "server"),
    ("server", r"(?i)\bwerkzeug\b", "Werkzeug (Flask dev)", "server"),
    ("server", r"(?i)\bcowboy\b", "Cowboy (Erlang)", "server"),
    ("server", r"(?i)\bKestrel\b", "Kestrel (.NET)", "server"),
    # Frameworks / languages
    ("x-powered-by", r"(?i)\bPHP/", "PHP", "language"),
    ("x-powered-by", r"(?i)\bASP\.NET\b", "ASP.NET", "language"),
    ("x-powered-by", r"(?i)\bExpress\b", "Express (Node.js)", "framework"),
    ("x-powered-by", r"(?i)\bNext\.js\b", "Next.js", "framework"),
    ("x-powered-by", r"(?i)\bPlesk\b", "Plesk", "hosting"),
    ("x-powered-by", r"(?i)\bCPanel\b", "cPanel", "hosting"),
    ("x-generator", None, None, "cms"),  # handled separately
    ("x-drupal-cache", None, "Drupal", "cms"),
    ("x-wordpress-loaded", None, "WordPress", "cms"),
    ("x-craft-cms", None, "Craft CMS", "cms"),
    ("x-typo3-powered", None, "TYPO3", "cms"),
    ("x-joomla-version", None, "Joomla", "cms"),
    # CDN / hosting
    ("x-cache", r"(?i)\bcloudflar", "Cloudflare CDN", "cdn"),
    ("cf-ray", None, "Cloudflare CDN", "cdn"),
    ("server", r"(?i)\bcloudflar", "Cloudflare", "cdn"),
    ("x-amz-cf-id", None, "Amazon CloudFront", "cdn"),
    ("x-amz-request-id", None, "Amazon AWS", "cdn"),
    ("x-azure-ref", None, "Microsoft Azure", "cdn"),
    ("x-fastly-request-id", None, "Fastly CDN", "cdn"),
    ("via", r"(?i)\bakamai\b", "Akamai CDN", "cdn"),
    ("x-cache", r"(?i)\bvarnish\b", "Varnish Cache", "cache"),
    # Frameworks (via specific headers)
    ("x-rails-version", None, "Ruby on Rails", "framework"),
    ("x-rack-cache", None, "Rack (Ruby)", "framework"),
    ("x-django-version", None, "Django", "framework"),
    ("x-laravel-session", None, "Laravel (PHP)", "framework"),
    ("x-turbo-boost", None, "Turbo (Rails)", "framework"),
    # Security headers (reported as presence/absence)
    ("strict-transport-security", None, "HSTS enabled", "security+"),
    ("content-security-policy", None, "CSP header present", "security+"),
    ("x-frame-options", None, "X-Frame-Options present", "security+"),
    ("x-content-type-options", None, "X-Content-Type-Options present", "security+"),
    ("referrer-policy", None, "Referrer-Policy present", "security+"),
    ("permissions-policy", None, "Permissions-Policy present", "security+"),
]

_MISSING_SECURITY_HEADERS = [
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
]

# Cookie name → technology
_COOKIE_SIGNATURES: list[tuple[str, str, str]] = [
    (r"(?i)^PHPSESSID$", "PHP", "language"),
    (r"(?i)^JSESSIONID$", "Java EE / Servlet", "language"),
    (r"(?i)^ASP\.NET_SessionId$", "ASP.NET", "language"),
    (r"(?i)^ci_session$", "CodeIgniter (PHP)", "framework"),
    (r"(?i)^laravel_session$", "Laravel (PHP)", "framework"),
    (r"(?i)^_session_id$", "Ruby on Rails", "framework"),
    (r"(?i)^__utma$|^_ga$", "Google Analytics", "analytics"),
    (r"(?i)^wordpress_logged_in", "WordPress", "cms"),
    (r"(?i)^wp-settings", "WordPress", "cms"),
    (r"(?i)^Drupal\.visitor\.", "Drupal", "cms"),
    (r"(?i)^fe_typo_user$", "TYPO3", "cms"),
    (r"(?i)^XSRF-TOKEN$|^csrf_token$", "CSRF protection", "security+"),
    (r"(?i)^connect\.sid$", "Express/Node.js", "framework"),
    (r"(?i)^django_language$|^csrftoken$", "Django (Python)", "framework"),
]

# Body pattern probes (applied to homepage body)
_BODY_SIGNATURES: list[tuple[str, str, str]] = [
    (r"(?i)wp-content/|wp-includes/", "WordPress", "cms"),
    (r"(?i)/sites/default/files/|drupal\.js", "Drupal", "cms"),
    (r"(?i)/media/jui/|Joomla!", "Joomla", "cms"),
    (r"(?i)Powered by TYPO3", "TYPO3", "cms"),
    (r"(?i)generator.*Magento", "Magento", "ecommerce"),
    (r"(?i)Shopify\.routes", "Shopify", "ecommerce"),
    (r"(?i)ng-version=|angular\.js", "Angular", "framework"),
    (r"(?i)__nuxt|nuxt\.js", "Nuxt.js", "framework"),
    (r"(?i)__NEXT_DATA__", "Next.js", "framework"),
    (r"(?i)react-dom\.", "React", "framework"),
    (r"(?i)vue\.js|v-bind:", "Vue.js", "framework"),
    (r"(?i)jquery[.-]\d", "jQuery", "library"),
    (r"(?i)bootstrap\.min\.(js|css)", "Bootstrap", "library"),
    (r"(?i)laravel/public", "Laravel", "framework"),
    (r"(?i)rails\.js|data-remote=", "Ruby on Rails", "framework"),
    (r"(?i)django-messages|csrfmiddlewaretoken", "Django", "framework"),
    (r"(?i)flask\.wtf|wtf\.html", "Flask", "framework"),
    (r"(?i)fastapi|starlette", "FastAPI/Starlette", "framework"),
]

# Extra paths to probe (path → what technology it indicates)
_PATH_PROBES: list[tuple[str, str, str, int]] = [
    ("/wp-login.php", "WordPress", "cms", 200),
    ("/wp-json/", "WordPress REST API", "cms", 200),
    ("/administrator/", "Joomla admin", "cms", 200),
    ("/user/login", "Drupal", "cms", 200),
    ("/phpmyadmin/", "phpMyAdmin", "tool", 200),
    ("/server-status", "Apache mod_status", "server", 200),
    ("/phpinfo.php", "PHP info page exposed", "exposure", 200),
    ("/.env", ".env file exposed", "exposure", 200),
    ("/actuator", "Spring Boot Actuator", "framework", 200),
    ("/actuator/health", "Spring Boot Actuator health", "framework", 200),
    ("/console", "Dev console", "exposure", 200),
    ("/.git/HEAD", "Git repo exposed", "exposure", 200),
    ("/web.config", "IIS web.config", "server", 200),
    ("/robots.txt", None, None, 200),  # parsed separately
]


def _extract_version(value: str, tech_name: str) -> str:
    """Try to extract a version string from a header value."""
    m = re.search(r"[\d]+\.[\d]+(?:\.[\d]+)?", value)
    if m:
        return f"{tech_name}/{m.group(0)}"
    return tech_name


def _run_tech_fingerprint(url: str, probe_paths: bool = True) -> str:
    """Identify technologies used by a web application.

    Args:
        url: Target URL (e.g. https://example.com or http://10.0.0.1:8080)
        probe_paths: Whether to probe common paths for additional fingerprinting

    Returns:
        str: Technology findings grouped by category.
    """
    if not url.strip():
        return "Error: URL is required."

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    findings: dict[str, set[str]] = {}
    missing_security: list[str] = []
    details: list[str] = []

    def _add(category: str, tech: str) -> None:
        findings.setdefault(category, set()).add(tech)

    # ─── Step 1: Fetch homepage ───────────────────────────────────────────────
    try:
        status, resp_headers, body = _fetch(url)
        details.append(f"Homepage [{status}]: {len(body)} bytes")
    except Exception as e:
        return f"Error fetching {url}: {e}"

    # ─── Step 2: Analyze response headers ────────────────────────────────────
    for header_lower, pattern, tech, category in _HEADER_RULES:
        val = resp_headers.get(header_lower)
        if val is None:
            continue
        if pattern is None:
            # Presence-based detection
            if category == "cms" and header_lower == "x-generator":
                # Extract generator name
                gen = val.strip()
                _add(category, f"Generator: {gen[:60]}")
            elif tech:
                _add(category, tech)
        else:
            if re.search(pattern, val):
                # Try to extract version for server/language headers
                if category in ("server", "language") and "/" in val:
                    _add(category, _extract_version(val, tech))
                else:
                    _add(category, tech)

    # ─── Step 3: Analyze Set-Cookie headers ──────────────────────────────────
    # http.client returns multiple Set-Cookie as separate headers
    # but the dict approach only keeps the last one; parse the raw list
    all_cookies: list[str] = []
    for k, v in resp_headers.items():
        if k == "set-cookie":
            all_cookies.append(v.split(";")[0].strip())  # just name=value

    for cookie_kv in all_cookies:
        cookie_name = cookie_kv.split("=", 1)[0].strip()
        for pattern, tech, category in _COOKIE_SIGNATURES:
            if re.match(pattern, cookie_name):
                _add(category, tech)

    # ─── Step 4: Analyze response body ───────────────────────────────────────
    for pattern, tech, category in _BODY_SIGNATURES:
        if re.search(pattern, body):
            _add(category, tech)

    # ─── Step 5: Check for missing security headers ───────────────────────────
    for hdr in _MISSING_SECURITY_HEADERS:
        if hdr not in resp_headers:
            missing_security.append(hdr)

    # ─── Step 6: Optional path probes ────────────────────────────────────────
    if probe_paths:
        for path, tech, category, expected_status in _PATH_PROBES:
            try:
                probe_url = base_url + path
                pstatus, pheaders, pbody = _fetch(probe_url, timeout=6)
                if path == "/robots.txt" and pstatus == 200:
                    # Scan robots.txt for CMS hints
                    for pattern, rt, rc in _BODY_SIGNATURES:
                        if re.search(pattern, pbody):
                            _add(rc, rt + " (robots.txt)")
                    # Flag disallowed paths
                    disallowed = re.findall(r"(?i)Disallow:\s*(.+)", pbody)
                    if disallowed:
                        details.append(f"robots.txt Disallow: {', '.join(disallowed[:8])}")
                elif pstatus == expected_status and tech:
                    if "exposure" in category:
                        _add("exposure", f"EXPOSED: {path} → {tech}")
                    else:
                        _add(category, tech)
            except Exception:
                pass  # Path probe failure is non-fatal

    # ─── Format output ────────────────────────────────────────────────────────
    lines: list[str] = [f"\n=== Technology Fingerprint: {url} ===\n"]

    category_order = [
        ("server", "Web Server"),
        ("language", "Language / Runtime"),
        ("framework", "Framework"),
        ("cms", "CMS / Platform"),
        ("ecommerce", "E-Commerce"),
        ("cdn", "CDN / Proxy"),
        ("cache", "Cache"),
        ("analytics", "Analytics"),
        ("library", "JS Libraries"),
        ("hosting", "Hosting Panel"),
        ("tool", "Exposed Tools"),
        ("exposure", "⚠ Security Exposures"),
        ("security+", "Security Headers (present)"),
    ]

    found_any = False
    for cat_key, cat_label in category_order:
        items = sorted(findings.get(cat_key, set()))
        if items:
            found_any = True
            lines.append(f"{cat_label}:")
            for item in items:
                lines.append(f"  • {item}")

    if not found_any:
        lines.append("No technology signatures detected.")

    # Missing security headers section
    if missing_security:
        lines.append("\nMissing Security Headers:")
        for h in missing_security:
            lines.append(f"  ✗ {h}")
    else:
        lines.append("\nAll baseline security headers present.")

    # Header dump (subset)
    interesting = [
        "server", "x-powered-by", "x-generator", "via",
        "x-frame-options", "strict-transport-security",
        "content-security-policy", "referrer-policy",
        "x-content-type-options", "permissions-policy",
    ]
    header_lines = []
    for h in interesting:
        v = resp_headers.get(h)
        if v:
            header_lines.append(f"  {h}: {v[:120]}")
    if header_lines:
        lines.append("\nKey Headers:")
        lines.extend(header_lines)

    if details:
        lines.append("\nProbe Details:")
        for d in details:
            lines.append(f"  {d}")

    lines.append(
        "\nNote: Fingerprinting is heuristic — always verify. "
        "Version disclosure via headers should be treated as an information leak."
    )

    return "\n".join(lines)


# ─── SDK integration ──────────────────────────────────────────────────────────
from cai.sdk.agents import function_tool  # noqa: E402


@function_tool(strict_mode=False)
def tech_fingerprint(url: str = "", probe_paths: bool = True) -> str:
    """Identify technologies used by a web application via HTTP fingerprinting.

    Detects web servers, languages, frameworks, CMS platforms, CDNs, caches,
    analytics tools, and exposed sensitive paths. Also checks for missing
    security headers.

    Fingerprinting sources:
    - Response headers (Server, X-Powered-By, X-Generator, CDN headers, etc.)
    - Cookie names (PHPSESSID → PHP, JSESSIONID → Java, etc.)
    - Response body patterns (CMS paths, JS framework signatures, etc.)
    - Common path probes: /wp-login.php, /.env, /.git/HEAD, /actuator, etc.

    Args:
        url: Target URL (e.g. https://example.com or http://10.0.0.1:8080)
        probe_paths: Probe common paths for deeper detection (default True).
                     Set False for a quieter single-request fingerprint.

    Returns:
        str: Findings grouped by category (server, framework, CMS, CDN,
             security exposure, missing security headers).
    """
    return _run_tech_fingerprint(url, probe_paths=probe_paths)


# ─── Auto-register with ToolRegistry ─────────────────────────────────────────
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "tech_fingerprint",
    tech_fingerprint,
    categories=["recon", "web"],
)
