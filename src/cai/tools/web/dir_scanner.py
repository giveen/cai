"""HTTP directory and path discovery tool.

Enumerates hidden files and directories on a web server by probing a
wordlist of paths concurrently.  Requires only the stdlib ``http.client``
module; no third-party HTTP library needed.
"""

from __future__ import annotations

import http.client
import os
import urllib.parse
import concurrent.futures
import socket
import ssl
from dataclasses import dataclass, field
from typing import Iterator

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Built-in mini wordlist (common paths used when no external list is provided)
# ---------------------------------------------------------------------------
_BUILTIN_WORDLIST: list[str] = [
    "admin", "login", "panel", "dashboard", "config", "backup",
    "api", "api/v1", "api/v2", "swagger", "docs", "documentation",
    ".git", ".gitignore", ".env", ".htaccess", ".htpasswd",
    "robots.txt", "sitemap.xml", "crossdomain.xml", "security.txt",
    "wp-admin", "wp-login.php", "wp-config.php",
    "phpinfo.php", "info.php", "test.php", "shell.php",
    "upload", "uploads", "files", "assets", "static", "media",
    "images", "img", "css", "js", "include", "includes",
    "src", "source", "old", "backup", "bak", "temp", "tmp",
    "data", "sql", "db", "database", "dump",
    "server-status", "server-info",
    "phpmyadmin", "adminer", "mysql", "pgsql", "postgres",
    "flag", "flag.txt", "secret", "secrets",
]

_DEFAULT_EXTENSIONS: list[str] = ["", ".php", ".html", ".txt", ".bak"]
_DEFAULT_THREADS = 20
_DEFAULT_TIMEOUT = 5
_DEFAULT_MAX_RESULTS = 500


@dataclass
class _Hit:
    path: str
    status: int
    size: int
    redirect: str = ""


def _make_connection(host: str, port: int, use_ssl: bool, timeout: float) -> http.client.HTTPConnection:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if use_ssl:
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _probe(host: str, port: int, use_ssl: bool, path: str, timeout: float) -> _Hit | None:
    """Issue a HEAD (then GET on 405) request for *path* and return a Hit or None."""
    try:
        conn = _make_connection(host, port, use_ssl, timeout)
        try:
            conn.request("HEAD", path, headers={"User-Agent": "CAI-DirScanner/1.0"})
            resp = conn.getresponse()
            status = resp.status
            try:
                size = int(resp.getheader("Content-Length", 0))
            except (ValueError, TypeError):
                size = 0
            location = resp.getheader("Location", "")
        finally:
            conn.close()

        if status == 405:
            conn2 = _make_connection(host, port, use_ssl, timeout)
            try:
                conn2.request("GET", path, headers={"User-Agent": "CAI-DirScanner/1.0"})
                resp2 = conn2.getresponse()
                status = resp2.status
                body = resp2.read(256)
                size = len(body)
                location = resp2.getheader("Location", "")
            finally:
                conn2.close()

        if status not in (404, 400):
            return _Hit(path=path, status=status, size=size, redirect=location)
    except (OSError, socket.timeout, ConnectionRefusedError,
            http.client.HTTPException, ssl.SSLError):
        pass
    return None


def _generate_paths(words: list[str], extensions: list[str]) -> Iterator[str]:
    for word in words:
        for ext in extensions:
            yield f"/{word}{ext}"


def _load_wordlist(wordlist_path: str | None) -> list[str]:
    if not wordlist_path:
        return _BUILTIN_WORDLIST
    try:
        with open(wordlist_path, encoding="utf-8", errors="replace") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError:
        return _BUILTIN_WORDLIST


def _run_dir_scan(
    url: str,
    wordlist: str | None = None,
    extensions: str = "",
    threads: int = _DEFAULT_THREADS,
    timeout: float = _DEFAULT_TIMEOUT,
    max_results: int = _DEFAULT_MAX_RESULTS,
    include_codes: str = "",
) -> str:
    """Core directory scan logic (callable directly in tests)."""
    # Parse the target URL
    parsed = urllib.parse.urlsplit(url)
    scheme = (parsed.scheme or "http").lower()
    use_ssl = scheme == "https"
    host = parsed.hostname or url.split("/")[0]
    port = parsed.port or (443 if use_ssl else 80)
    base_path = parsed.path.rstrip("/") or ""

    if not host:
        return "[dir_scanner] Error: could not parse host from URL"

    # Extension list
    ext_list: list[str] = [""]
    if extensions:
        ext_list = [e if e.startswith(".") else f".{e}" for e in extensions.split(",")]
        if "" not in ext_list:
            ext_list.insert(0, "")

    # Status code filter
    include_set: set[int] = set()
    if include_codes:
        for part in include_codes.split(","):
            part = part.strip()
            if part.isdigit():
                include_set.add(int(part))

    words = _load_wordlist(wordlist)
    paths = [base_path + p for p in _generate_paths(words, ext_list)]

    hits: list[_Hit] = []
    threads = max(1, min(threads, 50))

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        futures = {
            ex.submit(_probe, host, port, use_ssl, path, timeout): path
            for path in paths
        }
        for fut in concurrent.futures.as_completed(futures):
            result = fut.result()
            if result is None:
                continue
            if include_set and result.status not in include_set:
                continue
            hits.append(result)
            if len(hits) >= max_results:
                for pending in futures:
                    pending.cancel()
                break

    # Sort by status, then path
    hits.sort(key=lambda h: (h.status, h.path))

    # Build report
    target_str = f"{scheme}://{host}:{port}{base_path or '/'}"
    lines = [
        f"[dir_scanner] Scanning {target_str}",
        f"  Wordlist  : {wordlist or 'built-in ({} words)'.format(len(_BUILTIN_WORDLIST))}",
        f"  Extensions: {', '.join(ext_list) if extensions else 'none'}",
        f"  Threads   : {threads}  Timeout: {timeout}s",
        f"  Results   : {len(hits)} found (limit {max_results})\n",
    ]

    if not hits:
        lines.append("No interesting paths found.")
        return "\n".join(lines)

    lines.append(f"{'Status':<8} {'Size':<10} Path")
    lines.append("-" * 60)
    for h in hits:
        redir = f"  -> {h.redirect}" if h.redirect else ""
        lines.append(f"{h.status:<8} {h.size:<10} {h.path}{redir}")

    return "\n".join(lines)


@function_tool
def dir_scanner(
    url: str,
    wordlist: str = "",
    extensions: str = "",
    threads: int = 20,
    timeout: float = 5.0,
    max_results: int = 500,
    include_codes: str = "",
) -> str:
    """Enumerate hidden files and directories on a web server.

    Probes a wordlist of paths concurrently using HTTP HEAD requests
    (falls back to GET on 405). Uses a compact built-in wordlist when no
    external list is provided.

    Args:
        url: Target base URL (e.g. ``http://10.0.0.1`` or
            ``https://target.com/app``).
        wordlist: Path to a newline-delimited wordlist file.
            Defaults to a built-in list of ~80 common paths.
        extensions: Comma-separated file extensions to append to each word
            (e.g. ``php,html,txt``). The bare word is always tried too.
        threads: Number of concurrent requests (1–50, default 20).
        timeout: Per-request timeout in seconds (default 5.0).
        max_results: Stop after this many hits (default 500).
        include_codes: Only report these HTTP status codes, comma-separated
            (e.g. ``200,301,302``). Empty string means all except 404/400.

    Returns:
        Formatted table of discovered paths with HTTP status codes and
        response sizes.
    """
    return _run_dir_scan(
        url,
        wordlist=wordlist or None,
        extensions=extensions,
        threads=threads,
        timeout=timeout,
        max_results=max_results,
        include_codes=include_codes,
    )


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "dir_scanner",
    dir_scanner,
    categories=["recon", "web"],
)
