"""High-Signal DDG: Keyword-triggered DuckDuckGo search for CAI.

Search DuckDuckGo for real-time technical data, CVE details, exploit
writeups, or documentation. Keywords are more effective than questions.
Use this tool when local reconnaissance (nmap/gobuster) identifies a
specific service or version that requires external research.

This module exposes a single agent tool `duckduckgo_web_search` which
supports both a sanitized, high-signal plain-text summary and an option
to return the raw result dictionaries. The implementation performs
lazy imports (works with `ddgs` or `duckduckgo_search`), sanitizes
conversational queries, and prioritizes results from known high-signal
domains or those containing technical indicators (CVE-, exploit, GitHub,
default credentials, etc.).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Union, Tuple

from cai.agents.guardrails import sanitize_external_content
from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)

# Patterns and domains considered "high-signal" for technical research.
_INDICATOR_PATTERNS = [
    re.compile(r"CVE-\d{4}-\d+", re.I),
    re.compile(r"\bexploit\b", re.I),
    re.compile(r"\bproof of concept\b", re.I),
    re.compile(r"\bpoc\b", re.I),
    re.compile(r"\bdefault credential\b", re.I),
    re.compile(r"\bdefault password\b", re.I),
    re.compile(r"\bgithub\b", re.I),
    re.compile(r"\brce\b", re.I),
]

_HIGH_SIGNAL_DOMAINS = [
    "github.com",
    "exploit-db.org",
    "exploit-db.com",
    "hacktricks.xyz",
    "packetstormsecurity.com",
    "nvd.nist.gov",
    "cvedetails.com",
    "vulners.com",
]


def _query_duckduckgo(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Perform a DuckDuckGo search using available libraries.

    Tries in this order:
      1. `ddgs.DDGS` (preferred, new package)
      2. `duckduckgo_search.DDGS` (older API)
      3. `duckduckgo_search.ddg` convenience function

    The import is lazy and any ImportError is deferred until this function
    is called. The returned list contains dictionaries; keys vary by
    provider but commonly include `title`, `href`/`url`, and `body`/`snippet`.
    """
    # Try the new `ddgs` package first
    try:
        try:
            from ddgs import DDGS  # type: ignore

            with DDGS() as ddgs_client:
                results: List[Dict[str, Any]] = []
                # prefer `text` or `search` iterator helpers if available
                func = getattr(ddgs_client, "text", None) or getattr(ddgs_client, "search", None)
                if func is None:
                    raise RuntimeError("DDGS client missing expected interface")
                for i, item in enumerate(func(query)):
                    if i >= max_results:
                        break
                    if isinstance(item, dict):
                        results.append(item)
                    else:
                        results.append({"raw": str(item)})
                return results
        except Exception as exc:
            logger.debug("ddgs.DDGS attempt failed: %s", exc)

        # Fallback to duckduckgo_search.DDGS if available
        try:
            from duckduckgo_search import DDGS as DuckDDGS  # type: ignore

            with DuckDDGS() as ddgs_client:
                results = []
                func = getattr(ddgs_client, "text", None) or getattr(ddgs_client, "search", None)
                if func is None:
                    raise RuntimeError("duckduckgo_search DDGS missing expected interface")
                for i, item in enumerate(func(query)):
                    if i >= max_results:
                        break
                    if isinstance(item, dict):
                        results.append(item)
                    else:
                        results.append({"raw": str(item)})
                return results
        except Exception as exc:
            logger.debug("duckduckgo_search.DDGS attempt failed: %s", exc)

        # Final fallback to duckduckgo_search.ddg convenience function
        try:
            from duckduckgo_search import ddg  # type: ignore

            res = ddg(query, max_results=max_results)
            return list(res or [])
        except Exception as exc:
            logger.debug("duckduckgo_search.ddg attempt failed: %s", exc)
    except Exception:
        # top-level safety: fall through to error below
        pass

    raise RuntimeError(
        "No DuckDuckGo search backend available. Install the 'ddgs' package (pip install ddgs)"
    )


def _format_results_text(results: List[Dict[str, Any]], max_results: int = 5) -> str:
    """Format results into a plain-text summary and prioritize high-signal hits.

    Reorders results so that items mentioning technical indicators or whose
    URLs belong to known high-signal domains appear first, preserving the
    original rank as a secondary sort key. Returns a concise text summary
    suitable for agent consumption.
    """
    if not results:
        return ""

    scored: List[Tuple[int, int, int, Dict[str, Any]]] = []
    for idx, r in enumerate(results):
        title = str(r.get("title") or r.get("heading") or r.get("text") or "(no title)")
        url = str(r.get("href") or r.get("url") or r.get("link") or r.get("source") or "")
        snippet = str(r.get("body") or r.get("snippet") or r.get("excerpt") or r.get("text") or "")
        combined = " ".join([title, snippet, url])

        indicator_score = 1 if any(p.search(combined) for p in _INDICATOR_PATTERNS) else 0
        domain_score = 1 if any(d in url for d in _HIGH_SIGNAL_DOMAINS) else 0

        # Higher scores first; keep original index as tie-breaker.
        scored.append((-(domain_score + indicator_score), -indicator_score, idx, r))

    scored.sort()

    out_lines: List[str] = []
    for out_rank, (_, _, _, r) in enumerate(scored[:max_results]):
        title = r.get("title") or r.get("heading") or r.get("text") or "(no title)"
        url = r.get("href") or r.get("url") or r.get("link") or r.get("source") or ""
        snippet = r.get("body") or r.get("snippet") or r.get("excerpt") or r.get("text") or ""
        snippet = " ".join(str(snippet).split())[:600]
        block = f"{out_rank+1}. {title}\n{url}\n{snippet}"
        out_lines.append(block)

    return "\n\n".join(out_lines)


def _sanitize_query(query: str) -> str:
    """Small query optimizer: strip conversational filler and normalize common patterns.

    Examples:
      - "Search for the default password for Edukate CMS" -> "Edukate CMS default password"
    """
    q = str(query or "").strip()
    if not q:
        return q

    # Remove leading conversational prefixes
    q = re.sub(r"(?i)^(please\s+|could you\s+|can you\s+)", "", q)
    q = re.sub(r"(?i)^(search for|find|look up|look for|search|what is|whats|who is|how to|how do i)\s+", "", q)
    q = q.strip(" ?.")

    # Re-order "default password for X" -> "X default password"
    m = re.search(r"(?i)(?:default\s+password\s+for|password\s+for)\s+(?P<name>.+)$", q)
    if m:
        name = m.group("name").strip()
        name = re.sub(r"(?i)^the\s+", "", name)
        return f"{name} default password"

    # Remove leading definite/indefinite articles
    q = re.sub(r"(?i)^the\s+", "", q)
    q = re.sub(r"\s+", " ", q)
    return q


@function_tool
def duckduckgo_web_search(query: str, max_results: int = 5, return_raw: bool = False) -> Union[str, List[Dict[str, Any]]]:
    """Search DuckDuckGo and return a high-signal summary or raw results.

    Keyword Triggers: Search DuckDuckGo for real-time technical data, CVE
    details, exploit writeups, or documentation. Keywords are more
    effective than questions. Use this tool when local reconnaissance
    (nmap/gobuster) identifies a specific service or version that
    requires external research.

    Parameters:
      - `query` (str): The search keywords or question.
      - `max_results` (int): Maximum results to consider for the summary.
      - `return_raw` (bool): If True, return the raw list of result
        dictionaries (sanitized). Otherwise return a concise text summary.
    """
    if not query or not str(query).strip():
        return [] if return_raw else ""

    optimized = _sanitize_query(query)

    try:
        results = _query_duckduckgo(optimized, max_results=max_results)
    except Exception as exc:
        logger.exception("DuckDuckGo search failed: %s", exc)
        return sanitize_external_content(str(exc)) if not return_raw else []

    # If provider returned zero hits, give the agent an actionable hint.
    if not results:
        hint = (
            "Search returned 0 results. Try broadening your keywords "
            "(e.g., remove version numbers or use the software name only)."
        )
        return [] if return_raw else sanitize_external_content(hint)

    if return_raw:
        # Sanitize textual fields to avoid injecting unsafe markup
        for r in results:
            for k, v in list(r.items()):
                if isinstance(v, str):
                    r[k] = sanitize_external_content(v)
        return results

    # Produce a prioritized text summary for agent consumption.
    text = _format_results_text(results, max_results=max_results)
    return sanitize_external_content(text)
