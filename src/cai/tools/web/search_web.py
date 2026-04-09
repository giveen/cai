"""
DuckDuckGo-backed web search tools for CAI.

This module provides a small, robust wrapper around the community
`ddgs` / `duckduckgo_search` libraries. It performs lazy imports so the
package is optional at import-time; callers will receive a clear error
message if the dependency is missing and the function is invoked.

Usage:
  - Install the preferred package: `pip install ddgs` (the package was
    previously named `duckduckgo_search`).

Functions exposed as agent tools:
  - `duckduckgo_search_text(query, max_results=5)` — returns a plain-text
     summary of the top results (suitable for agent consumption).
  - `duckduckgo_search_raw(query, max_results=10)` — returns the raw list
     of result dicts when available.

The implementations try a few compatible import patterns so they work with
either the new `ddgs` package or the older `duckduckgo_search` package.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from cai.agents.guardrails import sanitize_external_content
from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)


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
    """Coerce a list of result dicts into a plain-text summary.

    The formatting is intentionally lightweight so the agent can read it
    and extract key information: rank, title, url, and a short snippet.
    """
    out_lines: List[str] = []
    for i, r in enumerate(results[:max_results]):
        title = r.get("title") or r.get("heading") or r.get("text") or "(no title)"
        url = r.get("href") or r.get("url") or r.get("link") or r.get("source") or ""
        snippet = r.get("body") or r.get("snippet") or r.get("excerpt") or r.get("text") or ""
        snippet = " ".join(str(snippet).split())[:600]
        block = f"{i+1}. {title}\n{url}\n{snippet}"
        out_lines.append(block)
    return "\n\n".join(out_lines)


@function_tool
def duckduckgo_search_text(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return a sanitized plain-text summary.

    This function is suitable as an agent tool because it returns a
    concise text blob. If the DuckDuckGo client library is not installed
    a helpful error message is returned instead.
    """
    if not query or not str(query).strip():
        return ""
    try:
        results = _query_duckduckgo(query, max_results=max_results)
    except Exception as exc:
        logger.exception("DuckDuckGo search failed: %s", exc)
        return sanitize_external_content(str(exc))

    text = _format_results_text(results, max_results=max_results)
    return sanitize_external_content(text)


@function_tool
def duckduckgo_search_raw(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Return the raw result dictionaries from the DuckDuckGo backend.

    Useful for tools that want structured output (urls, titles, snippets).
    If the backend is unavailable this raises a RuntimeError.
    """
    if not query or not str(query).strip():
        return []
    results = _query_duckduckgo(query, max_results=max_results)
    # Sanitize textual fields to avoid injecting unsafe markup
    for r in results:
        for k, v in list(r.items()):
            if isinstance(v, str):
                r[k] = sanitize_external_content(v)
    return results
