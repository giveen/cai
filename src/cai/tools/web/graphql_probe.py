"""GraphQL endpoint discovery and introspection probe for red team reconnaissance.

Discovers GraphQL endpoints on a target host and probes them for:
  - Introspection enabled (schema enumeration)
  - Query depth limit absent (DoS via deep nesting)
  - Mutation exposure (write access without auth)
  - Batch query support (rate-limit bypass)
  - Error verbosity (stack traces, internal paths leaked)

Stdlib-only: http.client + json.  No external dependencies.
"""

from __future__ import annotations

import http.client
import json
import ssl
import urllib.parse
from typing import NamedTuple

from cai.sdk.agents import function_tool
from cai.tool_registry import TOOL_REGISTRY


# ---------------------------------------------------------------------------
# Common GraphQL endpoint paths to probe
# ---------------------------------------------------------------------------

_COMMON_PATHS = [
    "/graphql",
    "/graphiql",
    "/api/graphql",
    "/v1/graphql",
    "/v2/graphql",
    "/query",
    "/gql",
    "/graph",
    "/api/query",
    "/api/v1/graphql",
    "/api/v2/graphql",
    "/playground",
    "/graphql/v1",
]

_INTROSPECTION_QUERY = json.dumps({
    "query": (
        "{ __schema { queryType { name } mutationType { name } "
        "subscriptionType { name } types { name kind } } }"
    )
})

_MUTATION_PROBE_QUERY = json.dumps({
    "query": "{ __schema { mutationType { name fields { name } } } }"
})

_DEPTH_BOMB_QUERY = json.dumps({
    "query": "{ __type(name: \"Query\") { fields { type { fields { type { fields { name } } } } } } }"
})

_BATCH_QUERY = json.dumps([
    {"query": "{ __typename }"},
    {"query": "{ __typename }"},
])

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; graphql-probe/1.0)",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class GraphQLFinding(NamedTuple):
    check: str
    severity: str   # CRITICAL | HIGH | MEDIUM | LOW | INFO
    status: str     # VULNERABLE | EXPOSED | NOT_FOUND | SAFE | ERROR
    detail: str


class GraphQLResult(NamedTuple):
    url: str
    endpoint_found: bool
    findings: list[GraphQLFinding]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _post(url: str, body: str, timeout: float = 8.0) -> tuple[int, str]:
    """POST JSON body to url. Returns (status, response_text). (-1, err) on failure."""
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(host, timeout=timeout, **({"context": ctx} if is_https else {}))
        encoded = body.encode("utf-8")
        hdrs = dict(_HEADERS)
        hdrs["Content-Length"] = str(len(encoded))
        conn.request("POST", path, body=encoded, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read(32768)
        conn.close()
        return resp.status, raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return -1, str(exc)


def _get(url: str, timeout: float = 8.0) -> tuple[int, str]:
    """GET url. Returns (status, body). (-1, err) on failure."""
    try:
        parsed = urllib.parse.urlparse(url)
        is_https = parsed.scheme == "https"
        host = parsed.netloc
        path = parsed.path or "/"

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        conn_cls = http.client.HTTPSConnection if is_https else http.client.HTTPConnection
        conn = conn_cls(host, timeout=timeout, **({"context": ctx} if is_https else {}))
        conn.request("GET", path, headers={"User-Agent": _HEADERS["User-Agent"], "Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read(16384)
        conn.close()
        return resp.status, raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return -1, str(exc)


def _is_graphql_response(body: str) -> bool:
    """Return True if body looks like a GraphQL response."""
    body_l = body.lower()
    return '"data"' in body_l or '"errors"' in body_l or "__schema" in body_l


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_introspection(endpoint_url: str, timeout: float) -> GraphQLFinding:
    status, body = _post(endpoint_url, _INTROSPECTION_QUERY, timeout)
    if status == -1:
        return GraphQLFinding("Introspection", "HIGH", "ERROR", f"Request failed: {body[:120]}")
    if status not in (200, 201) or not _is_graphql_response(body):
        return GraphQLFinding(
            "Introspection", "HIGH", "SAFE",
            f"Introspection blocked or non-GraphQL response (HTTP {status})",
        )
    if '"__schema"' in body:
        # Count type names
        try:
            data = json.loads(body)
            types = data.get("data", {}).get("__schema", {}).get("types", [])
            type_count = len(types)
            has_mutation = data.get("data", {}).get("__schema", {}).get("mutationType") is not None
        except (json.JSONDecodeError, AttributeError, TypeError):
            type_count = -1
            has_mutation = "mutationType" in body and '"name"' in body
        detail = (
            f"Introspection enabled — full schema exposed ({type_count} types). "
            "Attackers can enumerate all queries, mutations, and data types."
        )
        if has_mutation:
            detail += " Mutations also exposed."
        return GraphQLFinding("Introspection", "HIGH", "VULNERABLE", detail)
    return GraphQLFinding(
        "Introspection", "HIGH", "SAFE",
        "Introspection not returning schema data",
    )


def _check_batch_queries(endpoint_url: str, timeout: float) -> GraphQLFinding:
    status, body = _post(endpoint_url, _BATCH_QUERY, timeout)
    if status == -1:
        return GraphQLFinding("Batch queries", "MEDIUM", "ERROR", f"Request failed: {body[:80]}")
    if status == 200 and body.strip().startswith("["):
        return GraphQLFinding(
            "Batch queries", "MEDIUM", "VULNERABLE",
            "Batch query support enabled — can be used to bypass per-request rate limits "
            "by bundling many queries in one HTTP request",
        )
    return GraphQLFinding(
        "Batch queries", "MEDIUM", "SAFE",
        f"Batch queries not accepted (HTTP {status})",
    )


def _check_error_verbosity(endpoint_url: str, timeout: float) -> GraphQLFinding:
    # Send a broken query to trigger an error
    bad_query = json.dumps({"query": "{ thisFieldDefinitelyDoesNotExist123 }"})
    status, body = _post(endpoint_url, bad_query, timeout)
    if status == -1:
        return GraphQLFinding("Error verbosity", "LOW", "ERROR", f"Request failed: {body[:80]}")
    body_l = body.lower()
    leak_markers = [
        "exception", "traceback", "stack trace", "stacktrace",
        "at line", "file \"", "internal server error",
        "syntax error", "undefined field",
    ]
    leaks = [m for m in leak_markers if m in body_l]
    if leaks:
        return GraphQLFinding(
            "Error verbosity", "LOW", "EXPOSED",
            f"Verbose errors returned — leaked markers: {', '.join(leaks[:4])}. "
            "Internal paths/types may be revealed to unauthenticated callers.",
        )
    return GraphQLFinding(
        "Error verbosity", "LOW", "SAFE",
        "Error messages appear generic (no obvious stack trace or path leakage)",
    )


def _check_depth_limit(endpoint_url: str, timeout: float) -> GraphQLFinding:
    status, body = _post(endpoint_url, _DEPTH_BOMB_QUERY, timeout)
    if status == -1:
        return GraphQLFinding("Query depth limit", "MEDIUM", "ERROR", f"Request failed: {body[:80]}")
    if status == 200 and _is_graphql_response(body) and '"data"' in body:
        return GraphQLFinding(
            "Query depth limit", "MEDIUM", "VULNERABLE",
            "Deeply nested introspection query accepted — no depth limiting detected. "
            "Crafted deep queries may cause excessive server load (DoS).",
        )
    return GraphQLFinding(
        "Query depth limit", "MEDIUM", "SAFE",
        f"Depth-nested query rejected (HTTP {status}) or returned only errors",
    )


# ---------------------------------------------------------------------------
# Endpoint discovery
# ---------------------------------------------------------------------------

def _find_endpoint(base_url: str, paths: list[str], timeout: float) -> str | None:
    """Try each path; return the first that responds as a GraphQL endpoint, or None."""
    base_url = base_url.rstrip("/")
    for path in paths:
        url = base_url + path
        # Try a lightweight GET first (many endpoints show GraphiQL on GET)
        status_g, body_g = _get(url, timeout)
        if status_g not in (-1,) and ("graphql" in body_g.lower() or "graphiql" in body_g.lower()):
            return url
        # Try a POST with a minimal query
        status_p, body_p = _post(url, '{"query":"{ __typename }"}', timeout)
        if status_p in (200, 201) and _is_graphql_response(body_p):
            return url
    return None


# ---------------------------------------------------------------------------
# Main probe
# ---------------------------------------------------------------------------

def _probe(base_url: str, timeout: float = 8.0, custom_path: str = "") -> GraphQLResult:
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url

    paths = [custom_path] if custom_path else _COMMON_PATHS
    endpoint = _find_endpoint(base_url, paths, timeout)

    if not endpoint:
        # Try HTTP fallback
        if base_url.startswith("https://"):
            http_base = base_url.replace("https://", "http://", 1)
            endpoint = _find_endpoint(http_base, paths, timeout)

    if not endpoint:
        return GraphQLResult(
            url=base_url,
            endpoint_found=False,
            findings=[GraphQLFinding(
                "Endpoint discovery", "INFO", "NOT_FOUND",
                f"No GraphQL endpoint found at {len(paths)} common path(s)",
            )],
        )

    findings = [
        _check_introspection(endpoint, timeout),
        _check_batch_queries(endpoint, timeout),
        _check_error_verbosity(endpoint, timeout),
        _check_depth_limit(endpoint, timeout),
    ]
    _SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))
    return GraphQLResult(url=endpoint, endpoint_found=True, findings=findings)


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def _run_graphql_probe(targets: str, timeout: float = 8.0) -> str:
    items = [t.strip() for t in targets.replace(",", "\n").splitlines() if t.strip()]
    if not items:
        return "[graphql_probe] Error: no targets provided"

    lines: list[str] = [f"[graphql_probe] Probing {len(items)} target(s)\n"]
    total_vuln = total_exposed = total_safe = total_not_found = total_err = 0

    for target in items:
        # Allow "host|/custom/path" syntax
        custom_path = ""
        if "|" in target:
            target, custom_path = target.split("|", 1)
            target = target.strip()
            custom_path = custom_path.strip()

        result = _probe(target, timeout, custom_path)
        lines.append("─" * 60)
        lines.append(f"Target : {target}")
        if result.endpoint_found:
            lines.append(f"Endpoint: {result.url}")
        lines.append("")

        for f in result.findings:
            if f.status == "VULNERABLE":
                icon = "!!!"
                total_vuln += 1
            elif f.status == "EXPOSED":
                icon = " ! "
                total_exposed += 1
            elif f.status == "SAFE":
                icon = "   "
                total_safe += 1
            elif f.status == "NOT_FOUND":
                icon = " - "
                total_not_found += 1
            else:
                icon = " ? "
                total_err += 1
            lines.append(f"  [{icon}] {f.severity:<8}  {f.check:<22}  {f.status}")
            lines.append(f"           {f.detail}")
            lines.append("")

    lines.append("─" * 60)
    lines.append(
        f"Summary: {total_vuln} VULNERABLE, {total_exposed} EXPOSED, "
        f"{total_safe} SAFE, {total_not_found} NOT_FOUND, {total_err} ERROR"
    )

    if total_vuln or total_exposed:
        lines.append(
            "\nNote: Introspection + verbose errors allow full schema reconnaissance. "
            "Disable introspection in production and suppress stack traces."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Function tool
# ---------------------------------------------------------------------------

@function_tool
def graphql_probe(targets: str) -> str:
    """Discover GraphQL endpoints and probe for common misconfigurations.

    Finds GraphQL APIs at well-known paths (e.g. /graphql, /api/graphql,
    /graphiql) and checks for:

    - Introspection enabled — attackers can enumerate the full schema
    - Batch query support — can bypass per-request rate limits
    - Verbose error messages — stack traces or internal paths leaked
    - Missing query depth limit — deeply nested queries may cause DoS

    Args:
        targets: Newline- or comma-separated list of base URLs or hostnames.
                 Append "|/custom/path" to probe a specific endpoint path.
                 Examples:
                   "https://api.example.com"
                   "target.com, https://other.org"
                   "https://api.corp.com|/internal/graphql"

    Returns:
        Formatted report with VULNERABLE / EXPOSED / SAFE / NOT_FOUND status
        per check per target.
    """
    return _run_graphql_probe(targets)


TOOL_REGISTRY.register(
    "graphql_probe",
    graphql_probe,
    categories=["recon", "web"],
)
