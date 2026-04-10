"""Smoke tests for the web crawler tools (deep_crawl and local_crawler).

These tests run without Playwright or a live network connection.
Network calls are mocked via unittest.mock so the logic is exercised
end-to-end in pure Python.

`asyncio_mode = "auto"` in pyproject.toml means every async def test is
awaited automatically by pytest-asyncio — no extra mark needed.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
  <main>
    <h1>Hello World</h1>
    <p>Some body text.</p>
    <a href="/page2">Internal link</a>
    <a href="https://external.example.com/other">External link</a>
    <div class="cookie-banner">Cookie notice</div>
  </main>
  <footer>Footer content</footer>
</body>
</html>"""

_SAMPLE_WP_HTML = """<html><head><title>WP Site</title></head>
<body><main><p>Hello wp-content/uploads/image.jpg</p></main></body></html>"""


def _mock_requests_get(html: str = _SAMPLE_HTML):
    """Return a mock for requests.get that serves the given html."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.iter_content.return_value = iter([html.encode()])
    return MagicMock(return_value=mock_resp)


def _fake_path_cls(written: dict):
    """Factory for a Path-stub that records write_text calls."""
    class _FakePath:
        def __init__(self, *args):
            pass

        def __truediv__(self, other):
            # Return self so chained /  keeps working
            return self

        def mkdir(self, **kwargs):
            pass

        def write_text(self, content, encoding="utf-8"):
            written["content"] = content

    return _FakePath


# ---------------------------------------------------------------------------
# local_crawler tests  (sync implementation via @function_tool async wrapper)
# ---------------------------------------------------------------------------

class TestLocalCrawler:
    async def test_empty_url_returns_error(self):
        from cai.tools.web.crawler import local_crawler
        result = await local_crawler("")
        assert "[ERROR]" in result

    async def test_basic_crawl_returns_markdown(self):
        from cai.tools.web.crawler import local_crawler

        with patch("cai.tools.web.crawler.requests") as mock_req:
            mock_req.get = _mock_requests_get()
            result = await local_crawler("http://example.com", depth=1)

        assert "# Crawl Report" in result
        assert "### Site Map" in result
        assert "http://example.com" in result

    async def test_returns_page_content(self):
        from cai.tools.web.crawler import local_crawler

        with patch("cai.tools.web.crawler.requests") as mock_req:
            mock_req.get = _mock_requests_get()
            result = await local_crawler("http://example.com", depth=1)

        assert "Hello World" in result or "Test Page" in result

    async def test_adds_scheme_if_missing(self):
        """Should prepend http:// when no scheme given."""
        from cai.tools.web.crawler import local_crawler

        with patch("cai.tools.web.crawler.requests") as mock_req:
            mock_req.get = _mock_requests_get()
            result = await local_crawler("example.com", depth=1)

        assert "[ERROR]" not in result

    async def test_strips_footer_content(self):
        from cai.tools.web.crawler import local_crawler

        with patch("cai.tools.web.crawler.requests") as mock_req:
            mock_req.get = _mock_requests_get()
            result = await local_crawler("http://example.com", depth=1)

        # <footer> is stripped
        assert "Footer content" not in result

    async def test_strips_cookie_banner(self):
        from cai.tools.web.crawler import local_crawler

        with patch("cai.tools.web.crawler.requests") as mock_req:
            mock_req.get = _mock_requests_get()
            result = await local_crawler("http://example.com", depth=1)

        assert "Cookie notice" not in result

    async def test_network_error_shows_error_in_output(self):
        import requests as real_requests

        from cai.tools.web.crawler import local_crawler

        with patch("cai.tools.web.crawler.requests") as mock_req:
            mock_req.get.side_effect = real_requests.exceptions.ConnectionError("refused")
            result = await local_crawler("http://unreachable.test", depth=1)

        assert "[ERROR]" in result


# ---------------------------------------------------------------------------
# deep_crawl tests
# ---------------------------------------------------------------------------

class TestDeepCrawl:
    async def test_empty_url_returns_error(self):
        from cai.tools.web.crawler import deep_crawl
        result = await deep_crawl("")
        assert "[ERROR]" in result

    async def test_fallback_returns_markdown(self):
        from cai.tools.web.crawler import deep_crawl
        written: dict = {}

        with patch.dict(sys.modules, {"crawl4ai": None}), \
             patch("cai.tools.web.crawler.requests") as mock_req, \
             patch("cai.tools.web.crawler.Path", _fake_path_cls(written)):
            mock_req.get = _mock_requests_get()
            result = await deep_crawl("http://example.com", depth=1, max_pages=5)

        assert "# Crawl Report" in result
        assert "### High-Signal Findings" in result
        assert "### Pages" in result

    async def test_site_map_contains_crawled_url(self):
        from cai.tools.web.crawler import deep_crawl
        written: dict = {}

        with patch.dict(sys.modules, {"crawl4ai": None}), \
             patch("cai.tools.web.crawler.requests") as mock_req, \
             patch("cai.tools.web.crawler.Path", _fake_path_cls(written)):
            mock_req.get = _mock_requests_get()
            result = await deep_crawl("http://example.com", depth=1, max_pages=5)

        assert "example.com" in result

    async def test_cms_detection_wordpress(self):
        from cai.tools.web.crawler import deep_crawl
        written: dict = {}

        with patch.dict(sys.modules, {"crawl4ai": None}), \
             patch("cai.tools.web.crawler.requests") as mock_req, \
             patch("cai.tools.web.crawler.Path", _fake_path_cls(written)):
            mock_req.get = _mock_requests_get(_SAMPLE_WP_HTML)
            result = await deep_crawl("http://wpsite.example.com", depth=1, max_pages=3)

        assert "WordPress" in result

    async def test_no_crawl4ai_falls_back(self):
        """deep_crawl must work when crawl4ai is not installed."""
        from cai.tools.web.crawler import deep_crawl
        written: dict = {}

        with patch.dict(sys.modules, {"crawl4ai": None}), \
             patch("cai.tools.web.crawler.requests") as mock_req, \
             patch("cai.tools.web.crawler.Path", _fake_path_cls(written)):
            mock_req.get = _mock_requests_get()
            result = await deep_crawl("http://example.com", depth=1, max_pages=3)

        assert "# Crawl Report" in result

    async def test_max_pages_respected(self):
        """Crawler must stop after max_pages unique fetches."""
        from cai.tools.web.crawler import deep_crawl
        written: dict = {}
        fetch_count = {"n": 0}

        def _counting_get(url, **kwargs):
            fetch_count["n"] += 1
            return _mock_requests_get()()

        with patch.dict(sys.modules, {"crawl4ai": None}), \
             patch("cai.tools.web.crawler.requests") as mock_req, \
             patch("cai.tools.web.crawler.Path", _fake_path_cls(written)):
            mock_req.get.side_effect = _counting_get
            await deep_crawl("http://example.com", depth=3, max_pages=2)

        assert fetch_count["n"] <= 2

    async def test_report_saved_to_file(self):
        """write_text must be called with the returned content."""
        from cai.tools.web.crawler import deep_crawl
        written: dict = {}

        with patch.dict(sys.modules, {"crawl4ai": None}), \
             patch("cai.tools.web.crawler.requests") as mock_req, \
             patch("cai.tools.web.crawler.Path", _fake_path_cls(written)):
            mock_req.get = _mock_requests_get()
            result = await deep_crawl("http://example.com", depth=1, max_pages=3)

        assert written.get("content") == result


# ---------------------------------------------------------------------------
# Pure-Python helper unit tests (no async needed)
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    def test_origin(self):
        from cai.tools.web.crawler import _origin
        assert _origin("http://example.com/path?q=1") == "http://example.com"
        assert _origin("https://sub.example.com") == "https://sub.example.com"
        assert _origin("not-a-url") == ""
        assert _origin("") == ""

    def test_heuristic_detect_cms_wordpress(self):
        from cai.tools.web.crawler import _heuristic_detect_cms
        html = '<link rel="stylesheet" href="/wp-content/themes/main.css">'
        assert "WordPress" in _heuristic_detect_cms(html)

    def test_heuristic_detect_cms_none(self):
        from cai.tools.web.crawler import _heuristic_detect_cms
        assert _heuristic_detect_cms("<html><body>hello</body></html>") == []

    def test_extract_links_splits_internal_external(self):
        from cai.tools.web.crawler import _extract_links_from_html
        html = """<html><body>
        <a href="http://example.com/internal/page">int</a>
        <a href="https://other.example.com/ext">ext</a>
        </body></html>"""
        intern, extern = _extract_links_from_html(html, "http://example.com")
        assert any("example.com" in u for u in intern), f"intern={intern}"
        assert any("other.example.com" in u for u in extern), f"extern={extern}"

    def test_clean_html_strips_script_style(self):
        from cai.tools.web.crawler import _clean_html_to_markdown
        html = "<html><body><script>alert(1)</script><p>Content</p></body></html>"
        md = _clean_html_to_markdown(html, "http://x.com")
        assert "alert" not in md
        assert "Content" in md

    def test_injection_regex_in_netexec(self):
        """Regression test: the injection regex must compile and match correctly."""
        # Import netexec to verify the module-level regex compiles
        from cai.tools.network.netexec import _INJECTION_RE, _check_injection
        assert _INJECTION_RE.search("$(cmd)")       # shell command substitution
        assert _INJECTION_RE.search("; rm -rf /")   # semicolon
        assert _INJECTION_RE.search("cmd | tee /tmp/x")  # pipe
        assert not _INJECTION_RE.search("10.0.0.1")  # safe IP
        assert not _INJECTION_RE.search("smb 445 Windows")  # safe token

        blocked = _check_injection("192.168.1.0/24", "run_args")
        assert blocked is None
        blocked = _check_injection("$(id)", "run_args")
        assert blocked is not None
