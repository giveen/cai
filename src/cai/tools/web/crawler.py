"""Local crawler tool using Crawl4AI when available, falling back to
requests + BeautifulSoup.

Returns LLM-ready Markdown for a site starting at `url`. For `depth` > 1
the crawler follows same-origin links up to the requested depth and
returns a consolidated report. Includes a `### Site Map` section.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests  # type: ignore

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover - environment may not have bs4
    BeautifulSoup = None  # type: ignore

import asyncio
from datetime import datetime
from pathlib import Path

from cai.agents.guardrails import sanitize_external_content as _sanitize
from cai.sdk.agents import function_tool
from cai.util import notify_tool_loading, write_progress

_BAD_CLASS_RE = re.compile(
    r"cookie|consent|banner|promo|subscribe|modal|advert|ad-|sponsor|popup", re.I
)
_CRAWL4AI_TIMEOUT_SECONDS = 300


def _origin(u: str) -> str:
    p = urlparse(u or "")
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


def _fetch_text(url: str, timeout: int = 10, max_bytes: int = 500_000) -> (str, str | None):
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        data = bytearray()
        for chunk in resp.iter_content(chunk_size=16384):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) >= max_bytes:
                break
        return data.decode(errors="replace"), None
    except Exception as exc:
        return "", f"{url} -> {exc}"


def _clean_html_to_markdown(html: str, page_url: str) -> str:
    if not BeautifulSoup:
        # Minimal fallback: strip tags crudely
        text = re.sub(r"<script.*?>.*?</script>", "", html, flags=re.S | re.I)
        text = re.sub(r"<style.*?>.*?</style>", "", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = re.sub(r"\n{2,}", "\n\n", text).strip()
        return text[:20000]

    soup = BeautifulSoup(html, "html.parser")

    # Remove common cruft: scripts, styles, iframes, noscript
    for tag in soup.find_all(["script", "style", "noscript", "iframe", "svg", "canvas"]):
        try:
            tag.decompose()
        except Exception:
            pass

    # Remove header/footer/nav/aside
    for tag in soup.find_all(["header", "footer", "nav", "aside"]):
        try:
            tag.decompose()
        except Exception:
            pass

    # Remove elements with ad/cookie/banner-like classes or ids
    for el in soup.find_all(True):
        try:
            cls = " ".join(el.get("class") or []) if el.get("class") else ""
            elid = el.get("id") or ""
            if _BAD_CLASS_RE.search(cls) or _BAD_CLASS_RE.search(elid):
                el.decompose()
        except Exception:
            continue

    # Prefer <main> content if available
    main = soup.find("main") or soup.body or soup

    # Build a simple markdown-like text preserving headings and paragraphs
    parts: list[str] = []
    _TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "p", "pre", "code", "li"]
    for node in main.find_all(_TEXT_TAGS, recursive=True):
        try:
            if node.name and node.name.startswith("h"):
                level = int(node.name[1]) if len(node.name) > 1 and node.name[1].isdigit() else 2
                parts.append("#" * min(level, 6) + " " + node.get_text(separator=" ", strip=True))
            elif node.name == "p":
                txt = node.get_text(separator=" ", strip=True)
                if txt:
                    parts.append(txt)
            elif node.name in ("pre", "code"):
                txt = node.get_text(strip=True)
                if txt:
                    parts.append("```")
                    parts.append(txt)
                    parts.append("```")
            elif node.name == "li":
                txt = node.get_text(separator=" ", strip=True)
                if txt:
                    parts.append(f"- {txt}")
        except Exception:
            continue

    content = "\n\n".join(parts).strip()
    # Fallback to body text if nothing useful extracted
    if not content:
        content = main.get_text(separator="\n\n", strip=True)[:20000]

    # Reduce excessive whitespace
    content = re.sub(r"\n{3,}", "\n\n", content)
    # Trim length for safety
    return content[:20000]


@function_tool
def local_crawler(url: str, depth: int = 1) -> str:
    """Crawl starting `url` up to `depth` (default 1) and return Markdown.

    The returned Markdown contains a short report, a "### Site Map" list of
    discovered URLs, and per-page extracted content. Headers/footers/ads are
    removed heuristically.
    """
    url = (url or "").strip()
    if not url:
        return "[ERROR] url parameter is required"

    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "http://" + url
    except Exception:
        url = "http://" + url

    base_origin = _origin(url)
    if not base_origin:
        return "[ERROR] could not parse url"

    max_pages = 50
    visited: set[str] = set()
    discovered: list[str] = []
    pages: list[dict[str, str]] = []

    q = deque()
    q.append((url, 0))

    while q and len(visited) < max_pages:
        cur, lvl = q.popleft()
        norm = cur.split("#")[0]
        if norm in visited:
            continue
        visited.add(norm)
        discovered.append(norm)

        html, err = _fetch_text(norm)
        if err or not html:
            pages.append(
                {"url": norm, "title": "", "content": f"[ERROR] {err or 'empty response'}"}
            )
        else:
            title = ""
            try:
                if BeautifulSoup:
                    soup = BeautifulSoup(html, "html.parser")
                    t = soup.title.string if soup.title and soup.title.string else ""
                    title = (t or "").strip()
                else:
                    title = ""
            except Exception:
                title = ""

            content_md = _clean_html_to_markdown(html, norm)
            pages.append({"url": norm, "title": title or norm, "content": content_md})

            # If we can follow links and depth allows, enqueue same-origin links
            if lvl + 1 < max(1, int(depth)):
                try:
                    if BeautifulSoup:
                        soup = BeautifulSoup(html, "html.parser")
                        for a in soup.find_all("a", href=True):
                            href = a.get("href") or ""
                            if href.startswith("mailto:") or href.startswith("tel:"):
                                continue
                            absu = urljoin(norm, href.strip())
                            if _origin(absu) != base_origin:
                                continue
                            absu = absu.split("#")[0]
                            if absu not in visited and len(visited) + len(q) < max_pages:
                                q.append((absu, lvl + 1))
                except Exception:
                    pass

    # Build final Markdown report
    report: list[str] = []
    report.append(f"# Crawl Report: {_sanitize(url) if url else ''}")
    report.append("")
    report.append("### Site Map")
    report.append("")
    for u in discovered:
        report.append(f"- {_sanitize(u)}")

    report.append("")
    report.append("### Pages")
    report.append("")
    for p in pages:
        title = _sanitize(p.get("title") or p.get("url"))
        report.append(f"## {title}")
        report.append("")
        content = p.get("content") or ""
        report.append(_sanitize(content))
        report.append("")
        report.append(f"[Source]({_sanitize(p.get('url') or '')})")
        report.append("\n---\n")

    out = "\n\n".join(report)
    return out


def _heuristic_detect_cms(html_or_text: str) -> list[str]:
    found: set[str] = set()
    s = (html_or_text or "").lower()
    if "wp-content" in s or "wordpress" in s:
        found.add("WordPress")
    if "drupal" in s:
        found.add("Drupal")
    if "joomla" in s:
        found.add("Joomla")
    # server headers or generator meta tags may include versions; we keep names
    return sorted(found)


def _extract_links_from_html(html: str, base_origin: str) -> (set[str], set[str]):
    """Return (internal_links, external_links) sets extracted from html."""
    intern: set[str] = set()
    extern: set[str] = set()
    if not BeautifulSoup:
        return intern, extern
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if any(href.startswith(pfx) for pfx in ("mailto:", "tel:", "javascript:")):
                continue
            absu = urljoin(base_origin, href.strip()) if not urlparse(href).netloc else href
            absu = absu.split("#")[0]
            if _origin(absu).startswith(base_origin):
                intern.add(absu)
            else:
                extern.add(absu)
    except Exception:
        pass
    return intern, extern


@function_tool
def deep_crawl(
    url: str,
    depth: int = 1,
    max_pages: int = 50,
    use_llm_filter: bool = False,
    llm_model: str | None = None,
) -> str:
    """Deep crawler using Crawl4AI when available, otherwise falls back.

    Parameters:
      - url: start page
      - depth: link-follow depth (default 1)
      - max_pages: absolute page limit
      - use_llm_filter: if True and Crawl4AI provides LLMContentFilter, use it
      - llm_model: optional model path/identifier for the LLM filter

    The function shows a loading indicator via `notify_tool_loading` and
    streams progress via `write_progress`. The full report is saved to
    `{workspace}/recon/{domain}_{timestamp}.md` (workspace-relative when
    ``CAI_WORKSPACE`` / ``CAI_WORKSPACE_DIR`` are set, otherwise ``logs/recon/``)
    and returned as Markdown.
    """
    url = (url or "").strip()
    if not url:
        return "[ERROR] url parameter is required"

    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "http://" + url
    except Exception:
        url = "http://" + url

    base_origin = _origin(url)
    if not base_origin:
        return "[ERROR] could not parse url"

    notify_tool_loading(True)
    start = time.time()
    domain = urlparse(url).netloc.replace(":", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.getenv("CAI_WORKSPACE") or os.getenv("CAI_WORKSPACE_DIR"):
        from cai.tools.common import _get_workspace_dir
        out_path = Path(_get_workspace_dir()) / "recon"
    else:
        out_path = Path("logs") / "recon"
    out_path.mkdir(parents=True, exist_ok=True)
    out_file = out_path / f"{domain}_{ts}.md"

    # Try to use Crawl4AI's AsyncWebCrawler + DefaultMarkdownGenerator when available
    crawl4ai_pkg = None
    try:
        import crawl4ai as crawl4ai_pkg  # type: ignore
    except Exception:
        crawl4ai_pkg = None

    markdown_result = ""
    internal_links: set[str] = set()
    external_links: set[str] = set()
    cms_found: set[str] = set()

    if crawl4ai_pkg is not None:
        # Attempt imports from common locations; fall back silently if API differs
        AsyncWebCrawler = getattr(crawl4ai_pkg, "AsyncWebCrawler", None)
        DefaultMarkdownGenerator = getattr(crawl4ai_pkg, "DefaultMarkdownGenerator", None)
        LLMContentFilter = getattr(crawl4ai_pkg, "LLMContentFilter", None)

        # try deeper imports
        if AsyncWebCrawler is None or DefaultMarkdownGenerator is None:
            try:
                from crawl4ai.crawlers import AsyncWebCrawler  # type: ignore
                from crawl4ai.filters import LLMContentFilter  # type: ignore
                from crawl4ai.generators import DefaultMarkdownGenerator  # type: ignore
            except Exception:
                # best-effort; we'll fallback below
                AsyncWebCrawler = AsyncWebCrawler or None
                DefaultMarkdownGenerator = DefaultMarkdownGenerator or None
                LLMContentFilter = LLMContentFilter or None

        if AsyncWebCrawler and DefaultMarkdownGenerator:
            try:
                gen = DefaultMarkdownGenerator(ignore_links=False, body_width=0)
            except Exception:
                try:
                    gen = DefaultMarkdownGenerator()
                except Exception:
                    gen = None

            filters = []
            if use_llm_filter and LLMContentFilter is not None:
                try:
                    # try common constructor signatures
                    if llm_model:
                        f = LLMContentFilter(model_path=llm_model)  # type: ignore
                    else:
                        f = LLMContentFilter()
                    filters.append(f)
                except Exception:
                    try:
                        f = LLMContentFilter(llm=llm_model)  # type: ignore
                        filters.append(f)
                    except Exception:
                        pass

            # run the async crawler in an event loop and provide a simple progress hook
            async def _run_crawl() -> str:
                try:
                    crawler = (
                        AsyncWebCrawler(generator=gen, max_pages=max_pages)
                        if gen is not None
                        else AsyncWebCrawler(max_pages=max_pages)
                    )
                except Exception:
                    try:
                        crawler = AsyncWebCrawler()
                    except Exception:
                        raise

                # progress callback
                def _on_progress(done: int, total: int, current_url: str | None = None) -> None:
                    try:
                        write_progress(f"Crawled {done}/{total} pages: {current_url or ''}", "cyan")
                    except Exception:
                        pass

                # Try to call a commonly named coroutine; this is best-effort
                run_fn = (
                    getattr(crawler, "arun_many", None)
                    or getattr(crawler, "crawl", None)
                    or getattr(crawler, "run", None)
                    or getattr(crawler, "a_run", None)
                )
                if run_fn is None:
                    raise RuntimeError("AsyncWebCrawler does not expose a known run method")

                try:
                    # Many implementations accept start_urls, max_depth, progress callback
                    result = await run_fn(
                        [url],
                        max_depth=int(depth),
                        max_pages=int(max_pages),
                        progress_callback=_on_progress,
                    )
                except TypeError:
                    # try without named args
                    result = await run_fn([url])

                # Attempt to extract markdown from result
                md_parts: list[str] = []
                try:
                    if isinstance(result, list):
                        for r in result:
                            if isinstance(r, dict) and "markdown" in r:
                                md_parts.append(r.get("markdown") or "")
                            else:
                                md_parts.append(str(r))
                    elif isinstance(result, dict):
                        md_parts.append(result.get("markdown") or json.dumps(result))
                    else:
                        md_parts.append(str(result))
                except Exception:
                    md_parts.append(str(result))

                return "\n\n".join([p for p in md_parts if p])

            def _run_crawl_in_thread() -> str:
                result_box: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

                def _worker() -> None:
                    try:
                        result_box.put(
                            (
                                "result",
                                asyncio.run(
                                    asyncio.wait_for(
                                        _run_crawl(),
                                        timeout=_CRAWL4AI_TIMEOUT_SECONDS,
                                    )
                                ),
                            )
                        )
                    except Exception as exc:
                        result_box.put(("error", exc))

                worker = threading.Thread(target=_worker, daemon=True)
                worker.start()
                worker.join(_CRAWL4AI_TIMEOUT_SECONDS + 5)
                if worker.is_alive():
                    raise TimeoutError(
                        f"Crawl4AI timed out after {_CRAWL4AI_TIMEOUT_SECONDS} seconds"
                    )
                if result_box.empty():
                    raise RuntimeError("Crawl4AI worker exited without a result")

                status, payload = result_box.get_nowait()
                if status == "error":
                    if isinstance(payload, BaseException):
                        raise payload
                    raise RuntimeError(str(payload))
                return str(payload)

            try:
                # If there is already a running event loop (e.g. inside the TUI)
                # run the coroutine in a fresh thread so asyncio.run() gets its
                # own clean loop.
                try:
                    asyncio.get_running_loop()
                    _in_running_loop = True
                except RuntimeError:
                    _in_running_loop = False

                if _in_running_loop:
                    markdown_result = _run_crawl_in_thread()
                else:
                    markdown_result = asyncio.run(
                        asyncio.wait_for(_run_crawl(), timeout=_CRAWL4AI_TIMEOUT_SECONDS)
                    )
            except (asyncio.TimeoutError, TimeoutError) as exc:
                write_progress(
                    f"Crawl4AI execution timed out after {_CRAWL4AI_TIMEOUT_SECONDS}s, "
                    f"falling back: {exc}",
                    "red",
                )
                markdown_result = ""
            except Exception as exc:
                write_progress(f"Crawl4AI execution failed, falling back: {exc}", "red")
                markdown_result = ""

    # If Crawl4AI not available or failed, fallback to local crawler logic (synchronous)
    if not markdown_result:
        visited: set[str] = set()
        discovered: list[str] = []
        pages_raw: list[dict[str, str]] = []
        q = deque()
        q.append((url, 0))
        pages_crawled = 0

        while q and pages_crawled < int(max_pages):
            cur, lvl = q.popleft()
            norm = cur.split("#")[0]
            if norm in visited:
                continue
            visited.add(norm)
            discovered.append(norm)

            html, err = _fetch_text(norm)
            pages_crawled += 1
            write_progress(f"Crawled {pages_crawled}/{max_pages}: {norm}", "cyan")
            if err or not html:
                pages_raw.append(
                    {"url": norm, "html": "", "content": f"[ERROR] {err or 'empty response'}"}
                )
            else:
                title = ""
                try:
                    if BeautifulSoup:
                        soup = BeautifulSoup(html, "html.parser")
                        t = soup.title.string if soup.title and soup.title.string else ""
                        title = (t or "").strip()
                    else:
                        title = ""
                except Exception:
                    title = ""

                content_md = _clean_html_to_markdown(html, norm)
                pages_raw.append(
                    {"url": norm, "html": html, "title": title or norm, "content": content_md}
                )

                # enqueue same-origin links
                if lvl + 1 < max(1, int(depth)):
                    try:
                        if BeautifulSoup:
                            soup = BeautifulSoup(html, "html.parser")
                            for a in soup.find_all("a", href=True):
                                href = a.get("href") or ""
                                if href.startswith("mailto:") or href.startswith("tel:"):
                                    continue
                                absu = urljoin(norm, href.strip())
                                if _origin(absu) != base_origin:
                                    continue
                                absu = absu.split("#")[0]
                                if absu not in visited and len(visited) + len(q) < int(max_pages):
                                    q.append((absu, lvl + 1))
                    except Exception:
                        pass

        # analyze collected pages
        for p in pages_raw:
            html = p.get("html") or ""
            il, el = _extract_links_from_html(html or p.get("content") or "", base_origin)
            internal_links.update(il)
            external_links.update(el)
            cms_found.update(_heuristic_detect_cms(html or p.get("content") or ""))

        # build markdown result
        md_parts: list[str] = []
        md_parts.append(f"# Crawl Report: {_sanitize(url)}")
        md_parts.append("")
        md_parts.append("### High-Signal Findings")
        md_parts.append("")
        md_parts.append("- **Internal Links:**")
        for u in sorted(internal_links)[:200]:
            md_parts.append(f"  - {_sanitize(u)}")
        md_parts.append("")
        md_parts.append("- **External References:**")
        for u in sorted(external_links)[:200]:
            md_parts.append(f"  - {_sanitize(u)}")
        md_parts.append("")
        md_parts.append("- **Detected CMS:**")
        if cms_found:
            for c in sorted(cms_found):
                md_parts.append(f"  - {c}")
        else:
            md_parts.append("  - None detected")

        md_parts.append("")
        md_parts.append("---")
        md_parts.append("")
        md_parts.append("### Pages")
        md_parts.append("")
        for p in pages_raw:
            title = _sanitize(p.get("title") or p.get("url"))
            md_parts.append(f"## {title}")
            md_parts.append("")
            md_parts.append(_sanitize(p.get("content") or ""))
            md_parts.append("")
            md_parts.append(f"[Source]({_sanitize(p.get('url') or '')})")
            md_parts.append("\n---\n")

        markdown_result = "\n\n".join(md_parts)

    # final save and cleanup
    try:
        out_file.write_text(markdown_result, encoding="utf-8")
    except Exception:
        write_progress(f"Failed to write crawl report to {out_file}", "red")

    duration = int(time.time() - start)
    write_progress(f"Crawl finished in {duration}s, saved to {out_file}", "green")
    notify_tool_loading(False)
    return markdown_result
