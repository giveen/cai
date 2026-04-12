"""ResponsiveMixin — responsive layout and tab-navigation methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

from typing import Any, cast

import textual.containers as _containers
from textual.containers import Vertical, ScrollableContainer

try:
    from textual.widgets import TabbedContent  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    try:
        TabbedContent = _containers.TabbedContent  # type: ignore[attr-defined]
    except Exception:
        TabbedContent = cast(Any, object)

from textual.widgets import Button, Static

from cai.tui.components.terminal import TerminalPanel
from cai.tui.teams import TEAM_PRESETS


class ResponsiveMixin:
    """Mixin providing responsive layout and tab-navigation helpers."""

    def _responsive_mode_for_size(self, width: int, height: int) -> str:
        if width < 120 or height < 40:
            return "small"
        if width < 160 or height < 50:
            return "medium"
        return "large"

    def _truncate_label(self, text: str, max_len: int) -> str:
        raw = str(text or "")
        if len(raw) <= max_len:
            return raw
        if max_len <= 3:
            return raw[:max_len]
        return raw[: max_len - 1] + "…"

    def _responsive_capacity(self, mode: str) -> int:
        if mode == "small":
            return 1
        if mode == "medium":
            return 3
        return 4

    def _visible_panel_ids_for_mode(self, mode: str) -> set[int]:
        panels = sorted(list(self.query(TerminalPanel)), key=lambda p: p._term_id)
        if not panels:
            return set()
        capacity = self._responsive_capacity(mode)
        if capacity >= len(panels):
            return {p._term_id for p in panels}
        if mode == "small":
            return {self._active_term_id}

        selected = [p._term_id for p in panels[:capacity]]
        if self._active_term_id not in selected:
            selected[-1] = self._active_term_id
        return set(selected)

    def _apply_terminal_visibility(self, mode: str) -> None:
        visible_ids = self._visible_panel_ids_for_mode(mode)
        for panel in self.query(TerminalPanel):
            panel.display = panel._term_id in visible_ids

        # With TerminalGrid the row containers are gone; the grid itself is
        # always visible (the individual panels are shown/hidden above).
        try:
            from cai.tui.components.terminal_grid import TerminalGrid

            grid = self.query_one("#terminal-grid", TerminalGrid)
            grid.display = bool(visible_ids)
        except Exception:
            pass

    def _apply_responsive_labels(self, mode: str) -> None:
        if mode == "small":
            max_len = 12
        elif mode == "medium":
            max_len = 24
        else:
            max_len = 40

        for btn in self.query(Button):
            bid = btn.id or ""
            if not bid:
                continue
            if bid not in self._responsive_label_cache:
                self._responsive_label_cache[bid] = str(btn.label)
            base = self._responsive_label_cache.get(bid, str(btn.label))

            if mode == "small" and bid.startswith("team-"):
                compact = self._truncate_label(base.replace("#", "T"), 8)
                btn.label = compact
            elif mode == "small" and bid.startswith("agent-"):
                btn.label = self._truncate_label(base, max_len)
            elif mode == "small" and bid in {
                "sessions-refresh",
                "sessions-load",
                "sessions-resume",
                "sessions-export",
                "sessions-rename",
                "sessions-delete",
                "queue-run",
                "queue-delete",
                "queue-clear",
                "queue-broadcast-mode",
                "tools-run",
                "tools-inspect",
                "tools-replay",
                "tools-inject",
                "tools-inject-mode",
            }:
                btn.label = self._truncate_label(base, 10)
            else:
                btn.label = self._truncate_label(base, max_len)

            try:
                if mode == "large":
                    if bid.startswith("team-"):
                        idx = int(bid.split("-")[-1])
                        label, composition = TEAM_PRESETS[idx]
                        btn.tooltip = (
                            self._team_tooltip_text(idx, label, composition) + "\nViewport: large"
                        )
                    elif bid.startswith("agent-"):
                        btn.tooltip = f"Agent: {base}"
                elif mode == "small" and not btn.tooltip:
                    btn.tooltip = base
            except Exception:
                pass

    def _apply_responsive_chrome(self, mode: str, width: int, height: int) -> None:
        try:
            sidebar = self.query_one("#sidebar", Vertical)
            if mode == "small":
                sidebar.styles.width = 12
            elif mode == "medium":
                sidebar.styles.width = 32
            else:
                sidebar.styles.width = 36
        except Exception:
            pass

        try:
            playbook = self.query_one("#team-playbook-preview", Static)
            playbook.display = mode != "small"
        except Exception:
            pass

        try:
            tools_history = self.query_one("#tools-history-scroll", ScrollableContainer)
            tools_history.display = mode != "small"
        except Exception:
            pass

        try:
            metrics_events = self.query_one("#metrics-events-scroll", ScrollableContainer)
            metrics_events.display = mode != "small"
        except Exception:
            pass

        try:
            header_left = self.query_one("#header-left-text", Static)
            if mode == "small":
                header_left.update("[#00ff00]T[/#00ff00]")
            elif mode == "medium":
                header_left.update("[#00ff00]Terminal[/#00ff00]")
            else:
                header_left.update("[#00ff00]Terminal[/#00ff00]")
        except Exception:
            pass

        if width < 80 or height < 24:
            self._log_to_active_terminal(
                f"[layout] terminal below minimum {width}x{height} (recommended >= 80x24)",
                style="#ff6600",
            )

    def _apply_responsive_layout(self, width: int, height: int) -> None:
        mode = self._responsive_mode_for_size(width, height)
        self._responsive_mode = mode
        self.remove_class("-small-screen")
        self.remove_class("-medium-screen")
        self.remove_class("-large-screen")
        self.add_class(f"-{mode}-screen")
        self._apply_terminal_visibility(mode)
        self._apply_responsive_chrome(mode, width, height)
        self._apply_responsive_labels(mode)

    def _set_top_nav_active(self, tab_id: str) -> None:
        target = str(tab_id or "")
        for btn in self.query(".top-nav-btn"):
            btn.remove_class("-active-top-nav")
        map_to_btn = {
            "tab-terminal": "top-nav-terminal",
            "tab-agents": "top-nav-agents",
            "tab-queue": "top-nav-queue",
            "tab-sessions": "top-nav-sessions",
            "tab-config": "top-nav-config",
            "tab-tools": "top-nav-tools",
            "tab-metrics": "top-nav-metrics",
        }
        btn_id = map_to_btn.get(target)
        if not btn_id:
            return
        try:
            self.query_one(f"#{btn_id}", Button).add_class("-active-top-nav")
        except Exception:
            pass

    def _switch_top_tab(self, tab_id: str) -> None:
        try:
            tabs = self.query_one("#sidebar-tabs", TabbedContent)
            tabs.active = tab_id
            self._set_top_nav_active(tab_id)
        except Exception:
            pass
