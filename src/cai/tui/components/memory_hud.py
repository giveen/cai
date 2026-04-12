"""MemoryHUD — visualises the Virtual Context Manager (VCM) state.

Displays a compact heat-map of resident pages, a skill/pages count,
and a Manual Prune button that evicts least-recently-used non-pinned
pages until active usage drops below a conservative threshold.

This widget polls the VCM every second (best-effort) and exposes a
simple action for manual pruning.
"""

from __future__ import annotations

import time
import logging
import asyncio
import threading
from typing import Any, List
from textual.containers import Horizontal

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Button

from cai.memory.paging import list_pages, page_out, page_in, VCM

logger = logging.getLogger(__name__)


class MemoryHUD(Widget):
    """Compact HUD showing VCM residency and a Manual Prune action."""

    DEFAULT_CSS = """
    MemoryHUD {
        height: auto;
        width: 100%;
        background: #000800;
        border: solid #003300;
        padding: 0 1;
        margin-top: 1;
    }

    MemoryHUD .mh-label {
        color: #00ff88;
        text-style: bold;
    }

    MemoryHUD #memory-hud-heatmap {
        color: #00cc00;
        margin-top: 0;
        margin-bottom: 0;
    }

    MemoryHUD #memory-hud-status {
        color: #00cc00;
        margin-bottom: 1;
    }

    MemoryHUD Button {
        width: auto;
        padding: 0 1;
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._last_update: float = 0.0
        self._status: str = "idle"
        self._prune_armed: bool = False
        self._prune_armed_until: float = 0.0
        # Map button id -> page name for click handling
        self._page_button_map: dict[str, str] = {}
        # Guard to prevent re-entrant tick runs
        self._tick_running: bool = False

    def compose(self) -> ComposeResult:
        yield Static("MEMORY", id="memory-hud-label", classes="mh-label")
        # Container that will hold per-page buttons (heatmap)
        yield Horizontal(id="memory-hud-heatmap")
        yield Static("", id="memory-hud-status")
        yield Static("Pages: 0 · Skills: 0", id="memory-hud-summary")
        yield Button("🧹 Manual Prune", id="memory-prune", classes="agent-btn")

    def on_mount(self) -> None:
        try:
            self.set_interval(1.0, self._tick)
        except Exception:
            pass

    def _tick(self) -> None:
        # Prevent re-entrant runs which can cause widget-ghosting
        if getattr(self, "_tick_running", False):
            return
        self._tick_running = True
        try:
            pages = list_pages() or []
            total_pages = len(pages)
            # Active tokens and max budget
            try:
                max_tokens = int(getattr(VCM, "max_active_tokens", 393216))
            except Exception:
                max_tokens = 393216

            active_tokens = sum([int(p.get("tokens", 0) or 0) for p in pages if p.get("in_gpu")])
            pct = (active_tokens / max_tokens) * 100.0 if max_tokens else 0.0

            # Build per-page buttons (heatmap) with colour coding:
            # - Hot (in_gpu): bright green (#00ff00)
            # - Warm (in RAM, not in_gpu): dim green (#006600)
            # - Cold (no in-memory content -> on disk): black (#000000)
            try:
                container = self.query_one("#memory-hud-heatmap", Horizontal)
            except Exception:
                container = None

            # Rebuild mapping and children each tick (keeps UI simple)
            self._page_button_map.clear()
            if container is not None:
                # Remove existing children (best-effort)
                try:
                    for child in list(container.children):
                        try:
                            child.remove()
                        except Exception:
                            pass
                except Exception:
                    pass

            for p in sorted(pages, key=lambda x: x.get("last_access", 0), reverse=True):
                name = str(p.get("name") or "?")
                tokens = int(p.get("tokens", 0) or 0)
                in_gpu = bool(p.get("in_gpu"))
                pinned = bool(p.get("pinned"))
                has_content = tokens > 0

                if in_gpu:
                    color = "#00ff00"  # bright green
                else:
                    if has_content:
                        color = "#006600"  # dim green
                    else:
                        color = "#000000"  # cold / on-disk

                tag = "🔒" if pinned else " "
                short = name if len(name) <= 12 else name[:11] + "…"
                label = f"[{color}]{short}{tag}[/{color}]"

                # Create a stable button id from name (hex hash)
                try:
                    import hashlib

                    hid = hashlib.sha1(name.encode("utf-8")).hexdigest()
                except Exception:
                    hid = name.replace(" ", "_")[:16]
                btn_id = f"memory-page-{hid}"
                self._page_button_map[btn_id] = name
                if container is not None:
                    try:
                        container.mount(Button(label, id=btn_id, classes="memory-page-btn"))
                    except Exception:
                        # Fallback: append as plain text
                        try:
                            container.mount(Static(label))
                        except Exception:
                            pass

            # Compute a crude skill count: pages with tag 'skill' (if present)
            skills = 0
            try:
                skills = sum(1 for p in pages if "skill" in (p.get("tags") or []))
            except Exception:
                skills = 0

            # UI updated via container children; nothing to update here

            try:
                status = f"Active: {active_tokens} / {max_tokens} ({pct:.0f}%)"
                self.query_one("#memory-hud-status", Static).update(status)
            except Exception:
                pass

            try:
                self.query_one("#memory-hud-summary", Static).update(f"Pages: {total_pages} · Skills: {skills}")
            except Exception:
                pass

            self._last_update = time.time()
            # Clear prune-armed state when timeout elapses
            try:
                if self._prune_armed and time.time() > self._prune_armed_until:
                    self._prune_armed = False
                    try:
                        self.query_one("#memory-hud-status", Static).update("")
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            # Swallow to keep UI stable
            logger.exception("MemoryHUD tick failed")
        finally:
            self._tick_running = False

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""

        # Manual prune button: two-step confirmation
        if bid == "memory-prune":
            if not self._prune_armed:
                self._prune_armed = True
                self._prune_armed_until = time.time() + 3.0
                try:
                    self.query_one("#memory-hud-status", Static).update("Press again within 3s to confirm prune")
                except Exception:
                    pass
                return
            # Confirmed: perform prune asynchronously to avoid blocking the UI
            self._prune_armed = False
            self._prune_armed_until = 0.0
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._manual_prune_async())
            except Exception:
                # Fallback to thread
                try:
                    t = threading.Thread(target=self._manual_prune)
                    t.daemon = True
                    t.start()
                except Exception:
                    pass
            return

        # Per-page button toggles (page_in / page_out)
        if bid.startswith("memory-page-"):
            try:
                name = self._page_button_map.get(bid)
                if not name:
                    return
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._page_toggle_async(name, bid))
                except Exception:
                    # Fallback: run in thread
                    try:
                        t = threading.Thread(target=self._page_toggle_sync, args=(name, bid))
                        t.daemon = True
                        t.start()
                    except Exception:
                        pass
            except Exception:
                pass

    def _manual_prune(self, target_pct: float = 65.0) -> None:
        # Synchronous convenience wrapper for background threads
        return self._manual_prune_sync(target_pct)

    def _manual_prune_sync(self, target_pct: float = 65.0) -> None:
        try:
            pages = list_pages() or []
            try:
                max_tokens = int(getattr(VCM, "max_active_tokens", 393216))
            except Exception:
                max_tokens = 393216

            active_tokens = sum([int(p.get("tokens", 0) or 0) for p in pages if p.get("in_gpu")])
            if max_tokens:
                current_pct = (active_tokens / max_tokens) * 100.0
            else:
                current_pct = 0.0

            evicted: List[str] = []
            # Sort LRU (oldest first)
            candidates = sorted([p for p in pages if p.get("in_gpu") and not p.get("pinned")], key=lambda x: x.get("last_access", 0))
            for p in candidates:
                if current_pct <= target_pct:
                    break
                name = p.get("name")
                if not name:
                    continue
                try:
                    res = page_out(name)
                    if isinstance(res, dict) and res.get("status") in ("paged_out", "already_paged_out"):
                        evicted.append(name)
                        # Recompute active tokens
                        pages = list_pages() or []
                        active_tokens = sum([int(q.get("tokens", 0) or 0) for q in pages if q.get("in_gpu")])
                        current_pct = (active_tokens / max_tokens) * 100.0 if max_tokens else 0.0
                except Exception:
                    # Non-fatal
                    logger.exception("MemoryHUD prune page_out failed for %s", name)

            # Update status and UI promptly
            try:
                summary = f"Pruned: {len(evicted)} pages · Active: {active_tokens}/{max_tokens} ({current_pct:.0f}%)"
                self.query_one("#memory-hud-status", Static).update(summary)
            except Exception:
                pass
        except Exception:
            logger.exception("MemoryHUD manual_prune failed")

    async def _manual_prune_async(self, target_pct: float = 65.0) -> None:
        # Run the blocking prune logic in a thread then refresh the UI on completion
        try:
            await asyncio.to_thread(self._manual_prune_sync, target_pct)
        except Exception:
            logger.exception("async manual prune failed")
        try:
            # schedule a prompt UI refresh on the main loop
            self.set_timer(0.1, self._tick)
        except Exception:
            pass

    def _page_toggle_sync(self, name: str, bid: str | None = None) -> None:
        # Blocking version used in thread fallback
        try:
            pages = list_pages() or []
            page = next((p for p in pages if p.get("name") == name), None)
            if not page:
                try:
                    self.query_one("#memory-hud-status", Static).update(f"Page not found: {name}")
                except Exception:
                    pass
                return
            if page.get("in_gpu"):
                res = page_out(name)
            else:
                res = page_in(name)
                if isinstance(res, dict) and res.get("status") == "error":
                    try:
                        res2 = page_in(name, force=True)
                        res = res2 or res
                    except Exception:
                        pass
            try:
                st = res.get("status") if isinstance(res, dict) else str(res)
            except Exception:
                st = str(res)
            try:
                self.query_one("#memory-hud-status", Static).update(f"{name}: {st}")
            except Exception:
                pass
            try:
                self.set_timer(0.1, self._tick)
            except Exception:
                pass
        except Exception:
            pass

    async def _page_toggle_async(self, name: str, bid: str | None = None) -> None:
        try:
            # Execute blocking page operations in a thread
            await asyncio.to_thread(self._page_toggle_sync, name, bid)
        except Exception:
            logger.exception("page toggle async failed for %s", name)
