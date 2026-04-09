"""Browser automation tool — playwright-based headless browser for CAI agents.

Exposes a single ``browser_navigate`` tool that drives a headless Chromium
browser via the Playwright Python library.  Screenshots are saved to
``logs/screenshots/`` and optionally described by a local vision model
(set ``CAI_VISION_MODEL`` to a litellm-compatible model name such as
``ollama/llava`` or an openai-compatible vision endpoint).

If Playwright is not installed the tool returns an actionable install hint
rather than crashing at import time — the import is intentionally lazy.

Command schema (``commands`` argument):
    A JSON-encoded list of action dicts.  Supported actions:

    * ``{"action": "goto", "url": "https://example.com"}``
    * ``{"action": "click", "selector": "#submit"}``
    * ``{"action": "fill", "selector": "#username", "value": "admin"}``
    * ``{"action": "type", "selector": "#q", "text": "exploit"}``
    * ``{"action": "wait", "selector": "#result"}``      wait for element
    * ``{"action": "wait", "timeout": 2000}``             wait ms
    * ``{"action": "scroll", "direction": "down", "amount": 500}``
    * ``{"action": "screenshot"}``                        force a capture
    * ``{"action": "evaluate", "script": "document.title"}``
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SELECTOR_RE = re.compile(r"^[A-Za-z0-9 \-_#\.\[\]=':,>~+*^$|()\"]{1,256}$")
_URL_RE = re.compile(r"^https?://[^\s]{1,2048}$")

# Commands that must carry a "selector" key
_SELECTOR_REQUIRED = {"click", "fill", "type", "wait"}


def _screenshots_dir() -> Path:
    """Return (and create) the screenshots output directory."""
    d = Path("logs") / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_screenshot_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return _screenshots_dir() / f"browser_{ts}.png"


def _validate_commands(commands: Any) -> tuple[list, str | None]:
    """Parse + validate the commands list. Returns (cmds, error_str|None)."""
    if isinstance(commands, str):
        try:
            commands = json.loads(commands)
        except json.JSONDecodeError as exc:
            return [], f"commands is not valid JSON: {exc}"

    if not isinstance(commands, list):
        return [], "commands must be a JSON array"

    allowed_actions = {
        "goto", "click", "fill", "type", "wait",
        "scroll", "screenshot", "evaluate",
    }

    validated: list[dict] = []
    for idx, cmd in enumerate(commands):
        if not isinstance(cmd, dict):
            return [], f"commands[{idx}] is not an object"
        action = cmd.get("action", "")
        if action not in allowed_actions:
            return [], f"commands[{idx}] unknown action {action!r}"

        # Validate URL for goto
        if action == "goto":
            url = cmd.get("url", "")
            if not _URL_RE.match(url):
                return [], f"commands[{idx}] goto.url is not a valid http/https URL"

        # Validate selector
        if action in _SELECTOR_REQUIRED and "selector" not in cmd:
            return [], f"commands[{idx}] action '{action}' requires 'selector'"
        if "selector" in cmd and not _SELECTOR_RE.match(str(cmd["selector"])):
            return [], f"commands[{idx}] selector contains forbidden characters"

        # Validate JavaScript for evaluate (not empty, not excessively long)
        if action == "evaluate":
            script = str(cmd.get("script", ""))
            if not script.strip():
                return [], f"commands[{idx}] evaluate.script is empty"
            if len(script) > 4096:
                return [], f"commands[{idx}] evaluate.script exceeds 4096 chars"

        validated.append(cmd)

    return validated, None


# ---------------------------------------------------------------------------
# Vision sitrep
# ---------------------------------------------------------------------------

def _get_visual_sitrep(screenshot_path: str) -> str | None:
    """Call the configured vision model and return a text description.

    Returns ``None`` when ``CAI_VISION_MODEL`` is not set or the call fails.
    """
    model = os.getenv("CAI_VISION_MODEL", "").strip()
    if not model:
        return None

    try:
        import base64

        img_bytes = Path(screenshot_path).read_bytes()
        b64 = base64.b64encode(img_bytes).decode()
        data_url = f"data:image/png;base64,{b64}"

        import litellm  # type: ignore

        resp = litellm.completion(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Provide a concise security-focused description of this web page screenshot. "
                                "Note: visible forms, login fields, error messages, version strings, "
                                "technology indicators, interesting URLs, or any security-relevant content. "
                                "Keep it under 120 words."
                            ),
                        },
                    ],
                }
            ],
            max_tokens=200,
        )
        return resp.choices[0].message.content or None
    except Exception as exc:
        logger.debug("VLM sitrep failed (%s): %s", model, exc)
        return None


# ---------------------------------------------------------------------------
# Core Playwright runner
# ---------------------------------------------------------------------------

async def _run_playwright(
    url: str,
    commands: list[dict],
    headless: bool,
    timeout_ms: int,
) -> Dict[str, Any]:
    """Execute commands using Playwright and return a result dict."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        return {
            "error": (
                "Playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
        }

    screenshot_paths: list[str] = []
    evaluate_results: list[Any] = []
    page_title = ""
    final_url = url

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            # Always navigate to the initial URL first
            await page.goto(url, wait_until="domcontentloaded")

            for cmd in commands:
                action = cmd["action"]

                if action == "goto":
                    await page.goto(cmd["url"], wait_until="domcontentloaded")

                elif action == "click":
                    await page.click(cmd["selector"])

                elif action == "fill":
                    await page.fill(cmd["selector"], cmd.get("value", ""))

                elif action == "type":
                    await page.type(cmd["selector"], cmd.get("text", ""))

                elif action == "wait":
                    if "selector" in cmd:
                        await page.wait_for_selector(cmd["selector"])
                    elif "timeout" in cmd:
                        # cap at 30s to prevent tool hanging
                        ms = min(int(cmd["timeout"]), 30_000)
                        await page.wait_for_timeout(ms)

                elif action == "scroll":
                    direction = cmd.get("direction", "down")
                    amount = int(cmd.get("amount", 300))
                    if direction == "up":
                        amount = -amount
                    await page.evaluate(f"window.scrollBy(0, {amount})")

                elif action == "screenshot":
                    spath = str(_new_screenshot_path())
                    await page.screenshot(path=spath, full_page=False)
                    screenshot_paths.append(spath)

                elif action == "evaluate":
                    result = await page.evaluate(cmd["script"])
                    evaluate_results.append(result)

            # Always take a final screenshot
            final_spath = str(_new_screenshot_path())
            await page.screenshot(path=final_spath, full_page=False)
            screenshot_paths.append(final_spath)

            page_title = await page.title()
            final_url = page.url

        finally:
            await context.close()
            await browser.close()

    return {
        "title": page_title,
        "url": final_url,
        "screenshots": screenshot_paths,
        "evaluate_results": evaluate_results,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Exported tool
# ---------------------------------------------------------------------------

@function_tool
async def browser_navigate(
    url: str,
    commands: str = "[]",
    headless: bool = True,
    timeout: int = 30,
) -> str:
    """Navigate a headless Chromium browser, execute actions, and capture screenshots.

    Args:
        url: The starting URL (must begin with http:// or https://).
        commands: JSON array of action objects.  Each object must have an
            ``action`` key.  Supported actions: goto, click, fill, type,
            wait, scroll, screenshot, evaluate.  Example::

                [
                  {"action": "fill", "selector": "#q", "value": "admin"},
                  {"action": "click", "selector": "#login"},
                  {"action": "screenshot"}
                ]

        headless: Run the browser without a visible window (default: true).
        timeout: Per-action timeout in seconds (max: 60, default: 30).

    Returns:
        JSON string with keys: title, url, screenshots (list of paths),
        evaluate_results, sitrep (VLM visual description if available), error.
    """
    # ── Input validation ──────────────────────────────────────────────────
    if not _URL_RE.match(url):
        return json.dumps({"error": "url must be a valid http/https URL"})

    cmds, err = _validate_commands(commands)
    if err:
        return json.dumps({"error": err})

    timeout = max(5, min(int(timeout), 60))
    timeout_ms = timeout * 1000

    # ── Notify TUI: browser is working ───────────────────────────────────
    try:
        from cai.util import notify_tool_loading
        notify_tool_loading(True)
    except Exception:
        pass

    result: Dict[str, Any] = {}
    try:
        import asyncio as _asyncio

        result = await _asyncio.wait_for(
            _run_playwright(url, cmds, headless, timeout_ms),
            timeout=timeout + 10,
        )
    except TimeoutError:
        result = {"error": f"Browser timed out after {timeout + 10}s"}
    except Exception as exc:
        result = {"error": f"Browser error: {exc}"}
    finally:
        try:
            from cai.util import notify_tool_loading
            notify_tool_loading(False)
        except Exception:
            pass

    if result.get("error"):
        return json.dumps(result)

    screenshots = result.get("screenshots", [])

    # ── Notify TUI screenshot writer ──────────────────────────────────────
    if screenshots:
        last_shot = screenshots[-1]
        try:
            from cai.util import notify_screenshot
            notify_screenshot(last_shot)
        except Exception:
            pass

        # ── VLM visual sitrep ─────────────────────────────────────────────
        sitrep = _get_visual_sitrep(last_shot)
        result["sitrep"] = sitrep
    else:
        result["sitrep"] = None

    # Sanitize external content before returning to avoid prompt injection
    try:
        from cai.agents.guardrails import sanitize_external_content as _sanitize
        title_safe = _sanitize(result.get("title", ""))
        url_safe = _sanitize(result.get("url", ""))
    except Exception:
        title_safe = str(result.get("title", ""))[:200]
        url_safe = str(result.get("url", ""))[:300]

    out = {
        "title": title_safe,
        "url": url_safe,
        "screenshots": screenshots,
        "evaluate_results": result.get("evaluate_results", []),
        "sitrep": result.get("sitrep"),
        "error": None,
    }

    if out.get("sitrep"):
        out["sitrep"] = f"[Visual SitRep] {out['sitrep']}"

    return json.dumps(out, indent=2)
