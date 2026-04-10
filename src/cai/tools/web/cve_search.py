"""CVE Search tool — real-time CVE/NVD data via the cve-search public API.

Wraps the CIRCL cve-search public API at https://cve.circl.lu/api/ to
give agents on-demand access to live CVE records, vendor/product listings,
and recent vulnerability updates without requiring a local database.

Endpoints exposed:
  - cve_search_lookup   — fetch a single CVE record by ID
  - cve_search_product  — list CVEs for a vendor/product pair
  - cve_search_last     — get the most recently updated CVEs
  - cve_search_browse   — browse vendors or products under a vendor
  - cve_search_db_info  — check the database freshness / stats
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from cai.agents.guardrails import sanitize_external_content as _sanitize
from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)

_BASE_URL = "https://cve.circl.lu/api"
_TIMEOUT = 20  # seconds

# Validate CVE IDs: CVE-YYYY-NNNNN (4-digit year, 4+ digit sequence)
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)


def _get(path: str) -> dict | list | None:
    """Perform a GET request to the cve-search API; return parsed JSON or None."""
    try:
        import urllib.request

        url = f"{_BASE_URL}/{path}"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "cai-cve-search/1.0"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("cve_search _get(%r) failed: %s", path, exc)
        return None


def _safe_path_segment(value: str, label: str) -> str | None:
    """Return None if value contains path-traversal or injection chars."""
    if not value or not value.strip():
        return None
    # Allow alphanumeric, dash, underscore, dot — reject everything else
    if re.search(r"[^A-Za-z0-9\-_\.]", value.strip()):
        return None
    return value.strip()


def _extract_cve5_summary(cve5: dict) -> str:
    """Extract English description from CVE 5.x cvelistv5 record."""
    try:
        descs = cve5["containers"]["cna"]["descriptions"]
        for d in descs:
            if d.get("lang", "").startswith("en"):
                return d["value"]
        if descs:
            return descs[0].get("value", "")
    except (KeyError, TypeError, IndexError):
        pass
    return ""


def _extract_nvd_cvss(item: dict) -> tuple[str, str]:
    """Return (score, vector) from an fkie_nvd metrics dict (best available)."""
    metrics = item.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
        entries = metrics.get(key, [])
        if entries:
            data = entries[0].get("cvssData", {})
            score = str(data.get("baseScore", ""))
            vector = data.get("vectorString", "")
            return score, vector
    return "", ""


def _format_nvd_item(item: dict) -> str:
    """Render an fkie_nvd record as Markdown."""
    cve_id = item.get("id", "unknown")
    published = (item.get("published") or "")[:10]
    modified = (item.get("lastModified") or "")[:10]
    status = item.get("vulnStatus", "")

    # Description
    summary = ""
    for d in item.get("descriptions", []):
        if d.get("lang", "").startswith("en"):
            summary = d.get("value", "")
            break

    score, vector = _extract_nvd_cvss(item)

    # Weaknesses / CWEs
    cwes = []
    for w in item.get("weaknesses", []):
        for d in w.get("description", []):
            val = d.get("value", "")
            if val and val != "NVD-CWE-Other":
                cwes.append(val)

    # References
    refs = [r.get("url", "") for r in item.get("references", [])[:10] if r.get("url")]

    lines = [f"## {cve_id}"]
    if published:
        lines.append(f"- **Published:** {published}")
    if modified:
        lines.append(f"- **Last modified:** {modified}")
    if status:
        lines.append(f"- **Status:** {status}")
    if score:
        lines.append(f"- **CVSS score:** {score}")
    if vector:
        lines.append(f"- **CVSS vector:** `{vector}`")
    if cwes:
        lines.append(f"- **CWE:** {', '.join(cwes)}")

    if summary:
        lines += ["", "### Summary", summary]

    if refs:
        lines += ["", "### References (up to 10)"]
        for ref in refs:
            lines.append(f"- {ref}")

    return "\n".join(lines)


def _format_cve5_item(cve5: dict) -> str:
    """Render a CVE 5.x cvelistv5 record as Markdown."""
    meta = cve5.get("cveMetadata", {})
    cve_id = meta.get("cveId", "unknown")
    published = (meta.get("datePublished") or "")[:10]
    modified = (meta.get("dateUpdated") or "")[:10]

    cna = cve5.get("containers", {}).get("cna", {})
    title = cna.get("title", "")
    summary = _extract_cve5_summary(cve5)

    # CVSS from metrics inside cna
    score, vector = "", ""
    for m in cna.get("metrics", []):
        for key in ("cvssV4_0", "cvssV3_1", "cvssV3_0", "cvssV2_0"):
            if key in m:
                score = str(m[key].get("baseScore", ""))
                vector = m[key].get("vectorString", "")
                break
        if score:
            break

    # Affected products
    affected = cna.get("affected", [])

    # References
    refs = [r.get("url", "") for r in cna.get("references", [])[:10] if r.get("url")]

    lines = [f"## {cve_id}"]
    if title:
        lines.append(f"**{title}**")
    if published:
        lines.append(f"- **Published:** {published}")
    if modified:
        lines.append(f"- **Last modified:** {modified}")
    if score:
        lines.append(f"- **CVSS score:** {score}")
    if vector:
        lines.append(f"- **CVSS vector:** `{vector}`")

    if summary:
        lines += ["", "### Summary", summary]

    if affected:
        lines += ["", "### Affected Products"]
        for prod in affected[:10]:
            vendor = prod.get("vendor", "")
            product = prod.get("product", "")
            versions = prod.get("versions", [])
            ver_str = ", ".join(v.get("version", "") for v in versions[:5] if isinstance(v, dict))
            lines.append(f"- **{vendor} {product}** (versions: {ver_str or 'see references'})")

    if refs:
        lines += ["", "### References (up to 10)"]
        for ref in refs:
            lines.append(f"- {ref}")

    return "\n".join(lines)


def _format_cve(data: dict) -> str:
    """Dispatch to the appropriate renderer based on detected record format."""
    if not isinstance(data, dict):
        return str(data)

    # CVE 5.x cvelistv5 format
    if data.get("dataType") == "CVE_RECORD":
        return _format_cve5_item(data)

    # fkie_nvd / NVD format
    if "descriptions" in data and "vulnStatus" in data:
        return _format_nvd_item(data)

    # Legacy format (id, summary, cvss at top level)
    cve_id = data.get("id") or data.get("cveId") or "unknown"
    summary = data.get("summary") or data.get("description") or ""
    cvss = data.get("cvss3") or data.get("cvss") or ""
    published = data.get("Published") or data.get("published") or ""
    refs = data.get("references") or []

    lines = [f"## {cve_id}"]
    if published:
        lines.append(f"- **Published:** {published}")
    if cvss:
        lines.append(f"- **CVSS score:** {cvss}")
    if summary:
        lines += ["", "### Summary", summary]
    if refs:
        lines += ["", "### References (up to 10)"]
        for ref in refs[:10]:
            lines.append(f"- {ref}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 1 — Lookup a single CVE by ID
# ---------------------------------------------------------------------------
@function_tool
def cve_search_lookup(cve_id: str) -> str:
    """Fetch full details for a specific CVE from the live cve-search database.

    Retrieves CVSS score, vector, affected CPEs, CWE, CAPEC attack patterns,
    publication date, and references directly from the CIRCL cve-search API.

    Args:
        cve_id: The CVE identifier in standard form, e.g. "CVE-2021-44228"
                (Log4Shell) or "CVE-2017-0144" (EternalBlue).

    Returns:
        A Markdown-formatted summary of the CVE record.

    Examples:
        cve_search_lookup("CVE-2021-44228")   # Log4Shell
        cve_search_lookup("CVE-2017-0144")    # EternalBlue / WannaCry
        cve_search_lookup("CVE-2014-0160")    # Heartbleed
    """
    if not cve_id or not cve_id.strip():
        return "[ERROR] cve_id is required."

    normed = cve_id.strip().upper()
    if not _CVE_RE.match(normed):
        return (
            f"[ERROR] '{cve_id}' is not a valid CVE ID. "
            "Format must be CVE-YYYY-NNNNN (e.g. CVE-2021-44228)."
        )

    data = _get(f"cve/{normed}")
    if data is None:
        return f"[ERROR] Could not retrieve {normed} — API unreachable or CVE not found."

    if not isinstance(data, dict):
        return f"[ERROR] Unexpected response format for {normed}: {type(data).__name__}."

    if not data:
        return f"[NOT FOUND] {normed} was not found in the cve-search database."

    result = _format_cve(data)
    return _sanitize(result)


# ---------------------------------------------------------------------------
# Tool 2 — Search CVEs for a vendor/product
# ---------------------------------------------------------------------------
@function_tool
def cve_search_product(vendor: str, product: str) -> str:
    """Search the live CVE database for all vulnerabilities affecting a vendor/product.

    Returns a summarised list of CVEs sorted by CVSS score, giving a rapid
    overview of a target's attack surface before or during an engagement.

    Args:
        vendor:  Vendor name as it appears in CPE dictionaries, lowercased.
                 Examples: "apache", "microsoft", "cisco", "log4j"
        product: Product name within that vendor's CPE namespace.
                 Examples: "tomcat", "iis", "ios", "log4j2"

    Returns:
        A numbered Markdown list of CVEs with ID, CVSS score, and one-line
        summary (up to 30 results).

    Examples:
        cve_search_product("apache", "log4j")
        cve_search_product("microsoft", "exchange_server")
        cve_search_product("cisco", "ios")
    """
    safe_vendor = _safe_path_segment(vendor, "vendor")
    safe_product = _safe_path_segment(product, "product")

    if not safe_vendor:
        return "[ERROR] vendor must be a non-empty alphanumeric string."
    if not safe_product:
        return "[ERROR] product must be a non-empty alphanumeric string."

    data = _get(f"search/{quote(safe_vendor)}/{quote(safe_product)}")
    if data is None:
        return "[ERROR] Could not reach the cve-search API."

    # New API returns {"results": {"fkie_nvd": [[key, item], ...], ...}, ...}
    # Prefer fkie_nvd for CVSS; fall back to cvelistv5
    results_map = data.get("results", {}) if isinstance(data, dict) else {}
    total_count = data.get("total_count", "") if isinstance(data, dict) else ""

    items: list[dict] = []
    for source_key in ("fkie_nvd", "cvelistv5", "nvd"):
        entries = results_map.get(source_key, [])
        if entries:
            for entry in entries:
                # Each entry is [key, item_dict]
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    items.append(entry[1])
                elif isinstance(entry, dict):
                    items.append(entry)
            break  # only use the first available source

    # Legacy: flat list of dicts
    if not items and isinstance(data, list):
        items = data

    if not items:
        return f"No CVEs found for {safe_vendor}/{safe_product}."

    # Sort by CVSS score descending
    def _score(item: dict) -> float:
        # fkie_nvd
        metrics = item.get("metrics", {})
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
            entries = metrics.get(key, [])
            if entries:
                try:
                    return float(entries[0]["cvssData"]["baseScore"])
                except (KeyError, TypeError, ValueError):
                    pass
        # legacy
        try:
            return float(item.get("cvss3") or item.get("cvss") or 0)
        except (TypeError, ValueError):
            return 0.0

    items.sort(key=_score, reverse=True)
    total_label = f"{total_count} total, " if total_count else ""
    shown = items[:30]

    header = f"### CVEs for `{safe_vendor}/{safe_product}` ({total_label}top {len(shown)} by CVSS)"
    lines = [header + "\n"]
    for i, item in enumerate(shown, 1):
        # fkie_nvd
        if "descriptions" in item and "vulnStatus" in item:
            cid = item.get("id", "?")
            score_val, _ = _extract_nvd_cvss(item)
            desc = ""
            for d in item.get("descriptions", []):
                if d.get("lang", "").startswith("en"):
                    desc = d.get("value", "")
                    break
        # cvelistv5
        elif item.get("dataType") == "CVE_RECORD":
            cid = item.get("cveMetadata", {}).get("cveId", "?")
            score_val = ""
            for m in item.get("containers", {}).get("cna", {}).get("metrics", []):
                for k in ("cvssV4_0", "cvssV3_1", "cvssV3_0", "cvssV2_0"):
                    if k in m:
                        score_val = str(m[k].get("baseScore", ""))
                        break
                if score_val:
                    break
            desc = _extract_cve5_summary(item)
        else:
            cid = item.get("id") or item.get("cveId") or "?"
            score_val = str(item.get("cvss3") or item.get("cvss") or "N/A")
            desc = item.get("summary") or item.get("description") or ""

        desc = desc[:120] + "…" if len(desc) > 120 else desc
        score_str = score_val if score_val else "N/A"
        lines.append(f"{i}. **{cid}** (CVSS {score_str}) — {desc}")

    return _sanitize("\n".join(lines))


# ---------------------------------------------------------------------------
# Tool 3 — Get the most recently updated CVEs
# ---------------------------------------------------------------------------
@function_tool
def cve_search_last(count: int = 10) -> str:
    """Get the most recently updated CVEs from the live cve-search database.

    Useful for staying current on newly disclosed or updated vulnerabilities
    during a reconnaissance phase or threat-intelligence session.

    Args:
        count: Number of recent CVEs to return (1–30, default 10).
               The API always returns the last 30; this parameter limits
               the output shown.

    Returns:
        A Markdown list of recent CVEs with ID, CVSS score, and summary.

    Examples:
        cve_search_last()          # latest 10
        cve_search_last(count=20)  # latest 20
    """
    count = max(1, min(30, int(count)))

    data = _get("last")
    if data is None:
        return "[ERROR] Could not reach the cve-search API."

    if not isinstance(data, list) or not data:
        return "[ERROR] No recent CVEs returned."

    # The API returns CSAF advisory bundles; each may cover multiple CVEs.
    # Flatten to individual CVE entries.
    entries: list[dict] = []
    for bundle in data:
        if isinstance(bundle, dict) and "vulnerabilities" in bundle:
            doc_notes = bundle.get("document", {}).get("notes", [])
            doc_summary = next(
                (n.get("text", "") for n in doc_notes if n.get("category") == "summary"),
                "",
            )
            for vuln in bundle.get("vulnerabilities", []):
                cve_id = vuln.get("cve", "")
                if not cve_id:
                    continue
                cwe_name = vuln.get("cwe", {}).get("name", "")
                rel_date = (vuln.get("release_date") or vuln.get("discovery_date") or "")[:10]
                # Grab the description from vuln notes (category=description/details)
                vuln_desc = ""
                for n in vuln.get("notes", []):
                    if n.get("category") in ("description", "details", "summary"):
                        vuln_desc = n.get("text", "")
                        break
                if not vuln_desc:
                    vuln_desc = doc_summary
                entries.append(
                    {
                        "id": cve_id,
                        "modified": rel_date,
                        "summary": vuln_desc,
                        "cwe": cwe_name,
                    }
                )
        elif isinstance(bundle, dict):
            # Legacy or direct CVE record
            cid = (
                bundle.get("id")
                or bundle.get("cveId")
                or bundle.get("cveMetadata", {}).get("cveId", "")
            )
            if cid:
                entries.append(bundle)

    if not entries:
        return "[ERROR] Could not parse recent CVEs from API response."

    shown = entries[:count]
    lines = [f"### {len(shown)} Most Recently Updated CVEs\n"]
    for item in shown:
        cid = item.get("id", "?")
        modified = (item.get("modified") or item.get("Modified") or "")[:10]
        cwe = item.get("cwe", "")
        summary = (item.get("summary") or "")[:140]
        if len(summary) == 140:
            summary += "…"
        date_part = f" [{modified}]" if modified else ""
        cwe_part = f" ({cwe})" if cwe else ""
        lines.append(f"- **{cid}**{date_part}{cwe_part}: {summary}")

    return _sanitize("\n".join(lines))


# ---------------------------------------------------------------------------
# Tool 4 — Browse vendors / products
# ---------------------------------------------------------------------------
@function_tool
def cve_search_browse(vendor: str | None = None) -> str:
    """Browse vendors or the products offered by a specific vendor via cve-search.

    Without a vendor argument, returns a list of all vendors in the CVE
    database.  With a vendor name, returns all products associated with
    that vendor — useful for discovering the exact CPE name to pass to
    cve_search_product.

    Args:
        vendor: Optional vendor name (e.g. "microsoft", "apache").
                When omitted, lists all known vendors.

    Returns:
        A newline-separated list of vendor names, or product names under
        the given vendor.

    Examples:
        cve_search_browse()               # all vendors
        cve_search_browse("microsoft")    # all Microsoft products in CPE DB
    """
    if vendor:
        safe = _safe_path_segment(vendor, "vendor")
        if not safe:
            return "[ERROR] vendor must be a non-empty alphanumeric string."
        data = _get(f"browse/{quote(safe)}")
    else:
        data = _get("browse")

    if data is None:
        return "[ERROR] Could not reach the cve-search API."

    # The API returns {"vendor": [...]} or {"product": [...]}
    if isinstance(data, dict):
        items = data.get("vendor") or data.get("product") or list(data.values())
        if items and isinstance(items[0], list):
            items = items[0]
    elif isinstance(data, list):
        items = data
    else:
        return str(data)

    if not items:
        label = f"products for '{vendor}'" if vendor else "vendors"
        return f"No {label} found."

    label = f"Products for '{vendor}'" if vendor else "Vendors"
    header = f"### {label} ({len(items)} entries)\n"
    body = "\n".join(str(i) for i in items[:200])
    suffix = f"\n…and {len(items) - 200} more." if len(items) > 200 else ""
    return _sanitize(header + body + suffix)


# ---------------------------------------------------------------------------
# Tool 5 — Database info / freshness check
# ---------------------------------------------------------------------------
@function_tool
def cve_search_db_info() -> str:
    """Return metadata about the cve-search database.

    Includes source, record counts, and last-updated timestamps.

    Use this tool to verify that the CVE data is fresh before relying on it
    for a time-sensitive assessment.

    Returns:
        A Markdown summary of database sources and their update timestamps.

    Examples:
        cve_search_db_info()
    """
    data = _get("dbInfo")
    if data is None:
        return "[ERROR] Could not reach the cve-search API."

    if not isinstance(data, dict):
        return _sanitize(str(data))

    lines = ["### CVE-Search Database Info\n"]
    for source, info in data.items():
        lines.append(f"**{source}**")
        if isinstance(info, dict):
            for k, v in info.items():
                lines.append(f"  - {k}: {v}")
        else:
            lines.append(f"  - {info}")

    return _sanitize("\n".join(lines))
