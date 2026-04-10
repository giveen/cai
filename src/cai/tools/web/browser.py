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
from typing import Any

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
        "goto",
        "click",
        "fill",
        "type",
        "wait",
        "scroll",
        "screenshot",
        "evaluate",
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
        try:
            choices = getattr(resp, "choices", None)
            if choices and len(choices) > 0:
                first = choices[0]
                msg = getattr(first, "message", None)
                if msg is not None:
                    return getattr(msg, "content", None) or None
                return getattr(first, "content", None) or None
        except Exception:
            return None
        return None
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
) -> dict[str, Any]:
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

            # Build an initial interactive map for the loaded page so numeric
            # selectors (e.g. "click 5") can be resolved against the current
            # page state during subsequent commands.
            interactive_map = []
            try:
                interactive_map = await page.evaluate(
                    r"""() => {
                    const uniqueSelector = (el) => {
                        if (!el || el.nodeType !== 1) return "";
                        if (el.id) return `#${el.id}`;
                        const parts = [];
                        while (el && el.nodeType === 1) {
                            const tag = el.tagName.toLowerCase();
                            let nth = 1;
                            let sib = el;
                            while ((sib = sib.previousElementSibling) != null) {
                                if (sib.tagName === el.tagName) nth++;
                            }
                            parts.unshift(`${tag}:nth-of-type(${nth})`);
                            el = el.parentElement;
                        }
                        return parts.join(' > ');
                    };

                    const getName = (el) => {
                        if (!el) return '';
                        try {
                            const ariaLabel = el.getAttribute && el.getAttribute('aria-label');
                            if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
                            const labelledby = el.getAttribute && el.getAttribute('aria-labelledby');
                            if (labelledby) {
                                const ids = labelledby.split(/\s+/);
                                let txt = '';
                                for (const id of ids) {
                                    const ref = document.getElementById(id);
                                    if (ref) txt += ' ' + (ref.innerText || ref.textContent || '');
                                }
                                if (txt.trim()) return txt.trim();
                            }
                            if (el.value) return String(el.value);
                            const text = (el.innerText || el.textContent || '').trim();
                            if (text) return text;
                        } catch (e) {
                            // ignore
                        }
                        return '';
                    };

                    const interactiveRoles = new Set([
                        'button','link','textbox','checkbox','combobox','menuitem','option',
                        'tab','switch','slider','searchbox','radio','menuitemcheckbox','menuitemradio'
                    ]);

                    const isInteractive = (el) => {
                        if (!el || el.nodeType !== 1) return false;
                        const tag = el.tagName.toLowerCase();
                        if (['a','button','input','select','textarea'].includes(tag)) return true;
                        try {
                            const role = (el.getAttribute && el.getAttribute('role')) || '';
                            if (role && interactiveRoles.has(role.toLowerCase())) return true;
                            if (el.getAttribute && (el.getAttribute('onclick') || el.hasAttribute('contenteditable'))) return true;
                        } catch (e) {
                            // ignore
                        }
                        return false;
                    };

                    const collectAll = (root) => {
                        const out = [];
                        const stack = [root];
                        while (stack.length) {
                            const node = stack.shift();
                            if (!node) continue;
                            if (node.nodeType === 1) {
                                out.push(node);
                                try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                stack.unshift(...Array.from(node.children || []));
                            }
                        }
                        return out;
                    };
                    const all = collectAll(document);
                    const els = all.filter(isInteractive).slice(0, 200);
                    return els.map((el, idx) => {
                        let rect = null;
                        try {
                            const r = el.getBoundingClientRect();
                            rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                        } catch (e) {
                            rect = null;
                        }
                        const role = (el.getAttribute && el.getAttribute('role')) || el.tagName.toLowerCase();
                        const name = getName(el) || '';
                        const id = el.id || '';
                        const classes = el.className || '';
                        return { map_index: idx+1, role, name: (name||'').replace(/\s+/g,' ').slice(0,200), id, classes, tag: el.tagName.toLowerCase(), selector: uniqueSelector(el), rect };
                    });
                }"""
                )
            except Exception:
                interactive_map = []

            for cmd in commands:
                action = cmd["action"]

                if action == "goto":
                    await page.goto(cmd["url"], wait_until="domcontentloaded")
                    # Rebuild interactive map for the newly loaded page
                    try:
                        interactive_map = await page.evaluate(
                            r"""() => {
                            const uniqueSelector = (el) => {
                                if (!el || el.nodeType !== 1) return "";
                                if (el.id) return `#${el.id}`;
                                const parts = [];
                                while (el && el.nodeType === 1) {
                                    const tag = el.tagName.toLowerCase();
                                    let nth = 1;
                                    let sib = el;
                                    while ((sib = sib.previousElementSibling) != null) {
                                        if (sib.tagName === el.tagName) nth++;
                                    }
                                    parts.unshift(`${tag}:nth-of-type(${nth})`);
                                    el = el.parentElement;
                                }
                                return parts.join(' > ');
                            };

                            const getName = (el) => {
                                if (!el) return '';
                                try {
                                    const ariaLabel = el.getAttribute && el.getAttribute('aria-label');
                                    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
                                    const labelledby = el.getAttribute && el.getAttribute('aria-labelledby');
                                    if (labelledby) {
                                        const ids = labelledby.split(/\s+/);
                                        let txt = '';
                                        for (const id of ids) {
                                            const ref = document.getElementById(id);
                                            if (ref) txt += ' ' + (ref.innerText || ref.textContent || '');
                                        }
                                        if (txt.trim()) return txt.trim();
                                    }
                                    if (el.value) return String(el.value);
                                    const text = (el.innerText || el.textContent || '').trim();
                                    if (text) return text;
                                } catch (e) {
                                    // ignore
                                }
                                return '';
                            };

                            const interactiveRoles = new Set([
                                'button','link','textbox','checkbox','combobox','menuitem','option',
                                'tab','switch','slider','searchbox','radio','menuitemcheckbox','menuitemradio'
                            ]);

                            const isInteractive = (el) => {
                                if (!el || el.nodeType !== 1) return false;
                                const tag = el.tagName.toLowerCase();
                                if (['a','button','input','select','textarea'].includes(tag)) return true;
                                try {
                                    const role = (el.getAttribute && el.getAttribute('role')) || '';
                                    if (role && interactiveRoles.has(role.toLowerCase())) return true;
                                    if (el.getAttribute && (el.getAttribute('onclick') || el.hasAttribute('contenteditable'))) return true;
                                } catch (e) {
                                    // ignore
                                }
                                return false;
                            };

                            const collectAll = (root) => {
                                const out = [];
                                const stack = [root];
                                while (stack.length) {
                                    const node = stack.shift();
                                    if (!node) continue;
                                    if (node.nodeType === 1) {
                                        out.push(node);
                                        try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                        stack.unshift(...Array.from(node.children || []));
                                    }
                                }
                                return out;
                            };
                            const all = collectAll(document);
                            const els = all.filter(isInteractive).slice(0, 200);
                            return els.map((el, idx) => {
                                let rect = null;
                                try {
                                    const r = el.getBoundingClientRect();
                                    rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                                } catch (e) {
                                    rect = null;
                                }
                                const role = (el.getAttribute && el.getAttribute('role')) || el.tagName.toLowerCase();
                                const name = getName(el) || '';
                                const id = el.id || '';
                                const classes = el.className || '';
                                return { map_index: idx+1, role, name: (name||'').replace(/\s+/g,' ').slice(0,200), id, classes, tag: el.tagName.toLowerCase(), selector: uniqueSelector(el), rect };
                            });
                        }"""
                        )
                    except Exception:
                        interactive_map = []

                elif action == "click":
                    selector_to_use = cmd.get("selector")
                    # If a numeric selector is provided, prefer resolving as a map index
                    if isinstance(selector_to_use, (int,)) or (
                        isinstance(selector_to_use, str) and str(selector_to_use).isdigit()
                    ):
                        id_str = str(selector_to_use)
                        idx0 = int(id_str)
                        tried = False
                        # Try zero-based first, then 1-based
                        for candidate in (idx0, idx0 - 1):
                            if 0 <= candidate < len(interactive_map):
                                tried = True
                                try:
                                    js = r"""(i) => {
                                        const collectAll = (root) => {
                                            const out = [];
                                            const stack = [root];
                                            while (stack.length) {
                                                const node = stack.shift();
                                                if (!node) continue;
                                                if (node.nodeType === 1) {
                                                    out.push(node);
                                                    try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                                    stack.unshift(...Array.from(node.children || []));
                                                }
                                            }
                                            return out;
                                        };
                                        const interactiveRoles = new Set(['button','link','textbox','checkbox','combobox','menuitem','option','tab','switch','slider','searchbox','radio','menuitemcheckbox','menuitemradio']);
                                        const els = collectAll(document).filter(el => {
                                            try {
                                                if (!el) return false;
                                                const tag = el.tagName.toLowerCase();
                                                if (['a','button','input','select','textarea'].includes(tag)) return true;
                                                const role = (el.getAttribute && el.getAttribute('role')) || '';
                                                if (role && interactiveRoles.has(role.toLowerCase())) return true;
                                                if (el.getAttribute && (el.getAttribute('onclick') || el.hasAttribute('contenteditable'))) return true;
                                            } catch(e) {}
                                            return false;
                                        });
                                        const el = els[i];
                                        if (!el) return false;
                                        try { el.scrollIntoView({behavior:'auto', block:'center'}); } catch(e) {}
                                        try { el.click(); return true; } catch (e) { try { const ev = document.createEvent('MouseEvents'); ev.initEvent('click', true, true); el.dispatchEvent(ev); return true; } catch (e2) { return false; } }
                                    }"""
                                    ok = await page.evaluate(js, candidate)
                                    if not ok:
                                        # Fallback to CSS selector if evaluate didn't work
                                        sel = interactive_map[candidate].get("selector") or (
                                            "#" + (interactive_map[candidate].get("id") or "")
                                        )
                                        if sel:
                                            await page.click(sel)
                                    break
                                except Exception:
                                    continue
                        if not tried:
                            raise ValueError(
                                f"interactive map index out of range: {selector_to_use}"
                            )
                    else:
                        await page.click(selector_to_use)

                elif action == "fill":
                    selector_to_use = cmd.get("selector")
                    value = cmd.get("value", "")
                    if isinstance(selector_to_use, (int,)) or (
                        isinstance(selector_to_use, str) and str(selector_to_use).isdigit()
                    ):
                        id_str = str(selector_to_use)
                        idx0 = int(id_str)
                        tried = False
                        for candidate in (idx0, idx0 - 1):
                            if 0 <= candidate < len(interactive_map):
                                tried = True
                                try:
                                    js = r"""(i, v) => {
                                        const collectAll = (root) => {
                                            const out = [];
                                            const stack = [root];
                                            while (stack.length) {
                                                const node = stack.shift();
                                                if (!node) continue;
                                                if (node.nodeType === 1) {
                                                    out.push(node);
                                                    try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                                    stack.unshift(...Array.from(node.children || []));
                                                }
                                            }
                                            return out;
                                        };
                                        const els = collectAll(document).filter(el => el && el.nodeType === 1);
                                        const el = els[i];
                                        if (!el) return false;
                                        try { el.focus(); } catch(e) {}
                                        try { el.value = v; } catch(e) {}
                                        try { el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); } catch(e) {}
                                        return true;
                                    }"""
                                    await page.evaluate(js, candidate, value)
                                    break
                                except Exception:
                                    continue
                        if not tried:
                            raise ValueError(
                                f"interactive map index out of range: {selector_to_use}"
                            )
                    else:
                        await page.fill(selector_to_use, value)

                elif action == "type":
                    selector_to_use = cmd.get("selector")
                    text_val = cmd.get("text", "")
                    if isinstance(selector_to_use, (int,)) or (
                        isinstance(selector_to_use, str) and str(selector_to_use).isdigit()
                    ):
                        id_str = str(selector_to_use)
                        idx0 = int(id_str)
                        tried = False
                        for candidate in (idx0, idx0 - 1):
                            if 0 <= candidate < len(interactive_map):
                                tried = True
                                try:
                                    js = r"""(i, v) => {
                                        const collectAll = (root) => {
                                            const out = [];
                                            const stack = [root];
                                            while (stack.length) {
                                                const node = stack.shift();
                                                if (!node) continue;
                                                if (node.nodeType === 1) {
                                                    out.push(node);
                                                    try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                                    stack.unshift(...Array.from(node.children || []));
                                                }
                                            }
                                            return out;
                                        };
                                        const els = collectAll(document).filter(el => el && el.nodeType === 1);
                                        const el = els[i];
                                        if (!el) return false;
                                        try { el.focus(); } catch(e) {}
                                        try { el.value = (el.value || '') + v; } catch(e) {}
                                        try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch(e) {}
                                        return true;
                                    }"""
                                    await page.evaluate(js, candidate, text_val)
                                    break
                                except Exception:
                                    continue
                        if not tried:
                            raise ValueError(
                                f"interactive map index out of range: {selector_to_use}"
                            )
                    else:
                        await page.type(selector_to_use, text_val)

                elif action == "wait":
                    if "selector" in cmd:
                        selector_to_use = cmd.get("selector")
                        if isinstance(selector_to_use, (int,)) or (
                            isinstance(selector_to_use, str) and str(selector_to_use).isdigit()
                        ):
                            id_str = str(selector_to_use)
                            idx0 = int(id_str)
                            tried = False
                            for candidate in (idx0, idx0 - 1):
                                if 0 <= candidate < len(interactive_map):
                                    tried = True
                                    try:
                                        fn = r"""(i) => {
                                            const collectAll = (root) => {
                                                const out = [];
                                                const stack = [root];
                                                while (stack.length) {
                                                    const node = stack.shift();
                                                    if (!node) continue;
                                                    if (node.nodeType === 1) {
                                                        out.push(node);
                                                        try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                                        stack.unshift(...Array.from(node.children || []));
                                                    }
                                                }
                                                return out;
                                            };
                                            const els = collectAll(document).filter(el => el && el.nodeType === 1);
                                            return !!els[i];
                                        }"""
                                        await page.wait_for_function(fn, candidate)
                                        break
                                    except Exception:
                                        continue
                            if not tried:
                                raise ValueError(
                                    f"interactive map index out of range: {selector_to_use}"
                                )
                        else:
                            await page.wait_for_selector(selector_to_use)
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

            # Attempt to build a simplified interactive (ARIA) map of the page.
            # This returns a list of interactive elements with role, accessible
            # name, id/classes, a best-effort unique selector, and bounding rect.
            interactive_map = []
            try:
                interactive_map = await page.evaluate(
                    r"""() => {
                    const uniqueSelector = (el) => {
                        if (!el || el.nodeType !== 1) return "";
                        if (el.id) return `#${el.id}`;
                        const parts = [];
                        while (el && el.nodeType === 1) {
                            const tag = el.tagName.toLowerCase();
                            let nth = 1;
                            let sib = el;
                            while ((sib = sib.previousElementSibling) != null) {
                                if (sib.tagName === el.tagName) nth++;
                            }
                            parts.unshift(`${tag}:nth-of-type(${nth})`);
                            el = el.parentElement;
                        }
                        return parts.join(' > ');
                    };

                    const getName = (el) => {
                        if (!el) return '';
                        try {
                            const ariaLabel = el.getAttribute && el.getAttribute('aria-label');
                            if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
                            const labelledby = el.getAttribute && el.getAttribute('aria-labelledby');
                            if (labelledby) {
                                const ids = labelledby.split(/\s+/);
                                let txt = '';
                                for (const id of ids) {
                                    const ref = document.getElementById(id);
                                    if (ref) txt += ' ' + (ref.innerText || ref.textContent || '');
                                }
                                if (txt.trim()) return txt.trim();
                            }
                            if (el.value) return String(el.value);
                            const text = (el.innerText || el.textContent || '').trim();
                            if (text) return text;
                        } catch (e) {
                            // ignore
                        }
                        return '';
                    };

                    const interactiveRoles = new Set([
                        'button','link','textbox','checkbox','combobox','menuitem','option',
                        'tab','switch','slider','searchbox','radio','menuitemcheckbox','menuitemradio'
                    ]);

                    const isInteractive = (el) => {
                        if (!el || el.nodeType !== 1) return false;
                        const tag = el.tagName.toLowerCase();
                        if (['a','button','input','select','textarea'].includes(tag)) return true;
                        try {
                            const role = (el.getAttribute && el.getAttribute('role')) || '';
                            if (role && interactiveRoles.has(role.toLowerCase())) return true;
                            if (el.getAttribute && (el.getAttribute('onclick') || el.hasAttribute('contenteditable'))) return true;
                        } catch (e) {
                            // ignore
                        }
                        return false;
                    };

                    const collectAll = (root) => {
                        const out = [];
                        const stack = [root];
                        while (stack.length) {
                            const node = stack.shift();
                            if (!node) continue;
                            if (node.nodeType === 1) {
                                out.push(node);
                                try { if (node.shadowRoot) stack.unshift(...Array.from(node.shadowRoot.children)); } catch(e) {}
                                stack.unshift(...Array.from(node.children || []));
                            }
                        }
                        return out;
                    };
                    const all = collectAll(document);
                    const els = all.filter(isInteractive).slice(0, 200);
                    return els.map((el, idx) => {
                        let rect = null;
                        try {
                            const r = el.getBoundingClientRect();
                            rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
                        } catch (e) {
                            rect = null;
                        }
                        const role = (el.getAttribute && el.getAttribute('role')) || el.tagName.toLowerCase();
                        const name = getName(el) || '';
                        const id = el.id || '';
                        const classes = el.className || '';
                        return { map_index: idx+1, index: idx, role, name: (name||'').replace(/\s+/g,' ').slice(0,200), id, classes, tag: el.tagName.toLowerCase(), selector: uniqueSelector(el), rect };
                    });
                }"""
                )
            except Exception:
                interactive_map = []

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
        "interactive_map": interactive_map,
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
        evaluate_results, interactive_map (list of actionable ARIA elements),
        sitrep (VLM visual description if available), error.
    """
    # ── Input validation ──────────────────────────────────────────────────
    if not _URL_RE.match(url):
        return json.dumps({"error": "url must be a valid http/https URL"})

    # Support a compact shorthand like "click 5" or "goto https://..."
    if isinstance(commands, str) and commands.strip() and not commands.strip().startswith("["):
        m = re.match(r"^(?P<action>\w+)\s*(?P<arg>.*)$", commands.strip())
        if m:
            action = m.group("action").lower()
            arg = m.group("arg").strip()
            if action in {
                "click",
                "fill",
                "type",
                "wait",
                "goto",
                "screenshot",
                "evaluate",
                "scroll",
            }:
                if action == "screenshot":
                    commands = json.dumps([{"action": "screenshot"}])
                elif action == "goto":
                    commands = json.dumps([{"action": "goto", "url": arg}])
                elif action in {"click", "wait"}:
                    commands = json.dumps([{"action": action, "selector": arg}])
                elif action == "fill":
                    parts = arg.split(None, 1)
                    sel = parts[0] if parts else ""
                    val = parts[1] if len(parts) > 1 else ""
                    commands = json.dumps([{"action": "fill", "selector": sel, "value": val}])
                elif action == "type":
                    parts = arg.split(None, 1)
                    sel = parts[0] if parts else ""
                    txt = parts[1] if len(parts) > 1 else ""
                    commands = json.dumps([{"action": "type", "selector": sel, "text": txt}])
                elif action == "evaluate":
                    commands = json.dumps([{"action": "evaluate", "script": arg}])

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

    result: dict[str, Any] = {}
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

    # Sanitize external content before returning to avoid prompt injection
    try:
        from cai.agents.guardrails import sanitize_external_content as _sanitize

        title_safe = _sanitize(result.get("title", ""))
        url_safe = _sanitize(result.get("url", ""))
    except Exception:
        title_safe = str(result.get("title", ""))[:200]
        url_safe = str(result.get("url", ""))[:300]

    # Sanitize interactive map entries when possible
    interactive_map = result.get("interactive_map", []) or []
    try:
        from cai.agents.guardrails import sanitize_external_content as _sanitize

        sanitized_map: list[dict] = []
        for node in interactive_map:
            if not isinstance(node, dict):
                continue
            sanitized_map.append(
                {
                    "map_index": node.get("map_index") if node.get("map_index") else None,
                    "index": node.get("index") if node.get("index") is not None else None,
                    "role": _sanitize(node.get("role", "")) if node.get("role") else "",
                    "name": _sanitize(node.get("name", "")) if node.get("name") else "",
                    "id": _sanitize(node.get("id", "")) if node.get("id") else "",
                    "classes": _sanitize(node.get("classes", "")) if node.get("classes") else "",
                    "tag": _sanitize(node.get("tag", "")) if node.get("tag") else "",
                    "selector": _sanitize(node.get("selector", "")) if node.get("selector") else "",
                    "rect": node.get("rect"),
                }
            )
    except Exception:
        sanitized_map = interactive_map

    # ── Notify TUI screenshot writer (include sanitized map) ────────────
    if screenshots:
        last_shot = screenshots[-1]
        try:
            from cai.util import notify_screenshot

            notify_screenshot(last_shot, sanitized_map)
        except Exception:
            pass

        # ── VLM visual sitrep ─────────────────────────────────────────────
        sitrep = _get_visual_sitrep(last_shot)
        result["sitrep"] = sitrep
    else:
        result["sitrep"] = None

    out = {
        "title": title_safe,
        "url": url_safe,
        "screenshots": screenshots,
        "evaluate_results": result.get("evaluate_results", []),
        "interactive_map": sanitized_map,
        "sitrep": result.get("sitrep"),
        "error": None,
    }

    if out.get("sitrep"):
        out["sitrep"] = f"[Visual SitRep] {out['sitrep']}"

    return json.dumps(out, indent=2)
