"""Sidebar component: Sessions and Tools tabs emitting messages to the App.

This module provides a `Sidebar` container which hosts a TabbedContent
including `SessionsTab` and `ToolsTab`. The tabs emit message objects
instead of calling app-level methods directly so the App or controller
can react to user actions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.widgets import TabbedContent, TabPane, Button, ListItem, ListView, Static
from textual.widget import Widget


class SessionsTab(Widget):
    """Sessions tab UI that lists session files and emits actions."""

    # Runtime-provided `app` reference — declare for static analysis.
    app: Any

    class SessionAction(Message):
        def __init__(self, sender: Widget, action: str, index: int | None = None) -> None:
            super().__init__(sender)
            self.action = action
            self.index = index

    def compose(self) -> ComposeResult:
        with Vertical(id="sessions-pane"):
            with ScrollableContainer(id="sessions-scroll"):
                yield ListView(id="sessions-list")
            yield Static("", id="session-preview")
            with Horizontal(id="sessions-controls"):
                yield Button("Refresh", id="sessions-refresh", classes="team-btn")
                yield Button("Load Selected", id="sessions-load", classes="agent-btn")
                yield Button("Resume Selected", id="sessions-resume", classes="agent-btn")
                yield Button("Export Selected", id="sessions-export", classes="agent-btn")
                yield Button("Rename Selected", id="sessions-rename", classes="team-btn")
                yield Button(
                    "Delete Selected", id="sessions-delete", classes="modal-btn modal-btn--cancel"
                )

    def set_sessions(self, paths: List[str]) -> None:
        """Populate the sessions list with an ordered list of file paths."""
        try:
            lv = self.query_one("#sessions-list", ListView)
        except Exception:
            return

        # Clear existing items
        for child in list(lv.children):
            try:
                child.remove()
            except Exception:
                pass

        self._session_files = list(paths or [])
        for idx, p in enumerate(self._session_files):
            item = ListItem(id=f"session-item-{idx}")
            lv.mount(item)
            # Header toggles action area
            item.mount(Button(p, id=f"session-toggle-{idx}", classes="agent-btn"))
            actions = Vertical(id=f"session-actions-{idx}")
            actions.display = False
            item.mount(actions)
            actions.mount(Button("Select", id=f"session-select-{idx}", classes="team-btn"))
            actions.mount(Button("Open", id=f"session-open-{idx}", classes="agent-btn"))
            actions.mount(Button("Resume", id=f"session-resume-{idx}", classes="agent-btn"))
            actions.mount(Button("Export", id=f"session-export-{idx}", classes="agent-btn"))
            actions.mount(Button("Rename", id=f"session-rename-{idx}", classes="team-btn"))
            actions.mount(
                Button("Delete", id=f"session-delete-{idx}", classes="modal-btn modal-btn--cancel")
            )

    def update_preview(self, text: str) -> None:
        try:
            preview = self.query_one("#session-preview", Static)
            preview.update(text or "")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""
        if bid == "sessions-refresh":
            self.post_message(self.SessionAction(self, "refresh", None))
            return

        if bid == "sessions-load":
            self.post_message(self.SessionAction(self, "load", None))
            return

        if bid == "sessions-resume":
            self.post_message(self.SessionAction(self, "resume", None))
            return

        if bid == "sessions-export":
            self.post_message(self.SessionAction(self, "export", None))
            return

        if bid == "sessions-rename":
            self.post_message(self.SessionAction(self, "rename", None))
            return

        if bid == "sessions-delete":
            self.post_message(self.SessionAction(self, "delete", None))
            return

        # Per-item action buttons
        if bid.startswith("session-toggle-"):
            try:
                idx = int(bid.rsplit("-", 1)[-1])
            except Exception:
                return
            try:
                cont = self.query_one(f"#session-actions-{idx}", Vertical)
                cont.display = not bool(getattr(cont, "display", False))
            except Exception:
                pass
            return

        for prefix, action in (
            ("session-select-", "select"),
            ("session-open-", "open"),
            ("session-resume-", "resume"),
            ("session-export-", "export"),
            ("session-rename-", "rename"),
            ("session-delete-", "delete"),
        ):
            if bid.startswith(prefix):
                try:
                    idx = int(bid.split("-")[-1])
                except Exception:
                    idx = None
                self.post_message(self.SessionAction(self, action, idx))
                return


class ToolsTab(Widget):
    """Tools tab UI that lists tools and emits run/inspect actions."""

    # Runtime-provided `app` reference — declare for static analysis.
    app: Any

    class ToolAction(Message):
        def __init__(
            self,
            sender: Widget,
            action: str,
            tool_id: Optional[str] = None,
            index: Optional[int] = None,
        ) -> None:
            super().__init__(sender)
            self.action = action
            self.tool_id = tool_id
            self.index = index

    def compose(self) -> ComposeResult:
        with Vertical(id="tools-pane"):
            with ScrollableContainer(id="tools-list-scroll"):
                pass
            with Horizontal(id="tools-actions"):
                yield Button("Run", id="tools-run", classes="agent-btn")
                yield Button("Inspect", id="tools-inspect", classes="team-btn")
                yield Button("Replay", id="tools-replay", classes="agent-btn")
                yield Button("Inject", id="tools-inject", classes="team-btn")
                yield Button("Mode: input", id="tools-inject-mode", classes="team-btn")
            yield Static("", id="tools-preview")
            with ScrollableContainer(id="tools-history-scroll"):
                pass

    def set_tools(self, registry: Dict[str, Dict[str, Any]]) -> None:
        try:
            scroll = self.query_one("#tools-list-scroll", ScrollableContainer)
        except Exception:
            return

        for child in list(scroll.children):
            try:
                child.remove()
            except Exception:
                pass

        self._tool_ids: List[str] = []
        for idx, tool_id in enumerate(sorted(registry.keys()), start=1):
            meta = registry.get(tool_id, {}) or {}
            name = meta.get("name", tool_id)
            btn_id = f"tool-select-{idx}"
            self._tool_ids.append(tool_id)
            scroll.mount(Button(name, id=btn_id, classes="tool-btn"))

    def update_preview(self, text: str) -> None:
        try:
            preview = self.query_one("#tools-preview", Static)
            preview.update(text or "")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""
        if bid == "tools-run":
            self.post_message(self.ToolAction(self, "run", None))
            return
        if bid == "tools-inspect":
            self.post_message(self.ToolAction(self, "inspect", None))
            return
        if bid == "tools-replay":
            self.post_message(self.ToolAction(self, "replay", None))
            return
        if bid == "tools-inject":
            self.post_message(self.ToolAction(self, "inject", None))
            return
        if bid == "tools-inject-mode":
            self.post_message(self.ToolAction(self, "toggle_mode", None))
            return

        if bid.startswith("tool-select-"):
            try:
                idx = int(bid.split("-")[-1]) - 1
            except Exception:
                return
            tool_id = None
            try:
                tool_id = self._tool_ids[idx]
            except Exception:
                tool_id = None
            self.post_message(self.ToolAction(self, "select", tool_id, idx))
            return


class Sidebar(Widget):
    """Container hosting the main TabbedContent with Sessions and Tools tabs."""

    # Runtime-provided `app` reference — declare for static analysis.
    app: Any

    def compose(self) -> ComposeResult:
        with TabbedContent(id="sidebar-tabs"):
            # Terminal tab placeholder – actual terminals are mounted by the app/controller
            with TabPane("Terminal", id="tab-terminal"):
                with Vertical(id="terminals"):
                    with Horizontal(id="term-row-top"):
                        pass
                yield Static("", id="browser-preview-placeholder")

            # Agents tab placeholder (populated by the App)
            with TabPane("Agents", id="tab-agents"):
                with Vertical(id="agents-pane"):
                    with ScrollableContainer(id="agents-scroll"):
                        pass

            # Queue tab placeholder
            with TabPane("Queue", id="tab-queue"):
                with Vertical(id="queue-pane"):
                    yield ListView(id="queue-list")

            # Sessions tab: use our SessionsTab widget
            with TabPane("Sessions", id="tab-sessions"):
                yield SessionsTab()

            # Config placeholder
            with TabPane("Config", id="tab-config"):
                yield Static("Config")

            # Tools tab: use our ToolsTab widget
            with TabPane("Tools", id="tab-tools"):
                yield ToolsTab()

            # Metrics/Stats tab placeholder
            with TabPane("Stats", id="tab-metrics"):
                yield Static("Stats")

    def set_sessions(self, paths: List[str]) -> None:
        try:
            tab = self.query_one(SessionsTab)
            tab.set_sessions(paths)
        except Exception:
            pass

    def set_tools(self, registry: Dict[str, Dict[str, Any]]) -> None:
        try:
            tab = self.query_one(ToolsTab)
            tab.set_tools(registry)
        except Exception:
            pass
