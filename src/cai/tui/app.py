"""Cerebro Command Dashboard built with Textual."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from contextlib import suppress
import os
from pathlib import Path
import re
import resource
import sys
from typing import Any, Iterable, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, RichLog, Static, Tree

from cai.tools.workspace import get_project_space


ASCII_HEADER = r"""
   ____ _____ ____  _____ ____  ____   ____        _
  / ___| ____|  _ \| ____| __ )|  _ \ / ___|      / \
 | |   |  _| | |_) |  _| |  _ \| |_) | |   _____ /  /
 | |___| |___|  _ <| |___| |_) |  _ <| |__|_____/\_/ 
  \____|_____|_| \_\_____|____/|_| \_\\____|

 CEREBRO-AI: SYSTEMIC DEFENSE ENGINE
""".strip("\n")

THEME_CSS = """
Screen {
    background: #0b1118;
    color: #d8dee9;
}

#root {
    layout: vertical;
    height: 100%;
}

#header {
    height: 8;
    border: heavy #5e81ac;
    background: #101824;
    color: #88c0d0;
    padding: 0 1;
}

#main {
    layout: horizontal;
    height: 1fr;
}

#left-sidebar {
    width: 32;
    border: round #3b4252;
    background: #0f1722;
    padding: 0 1;
}

#center-pane {
    width: 1fr;
    border: round #5e81ac;
    background: #0e1621;
    padding: 0 1;
}

#center-pane.critique-pulse {
    border: heavy #ebcb8b;
}

#right-sidebar {
    width: 34;
    border: round #3b4252;
    background: #0f1722;
    padding: 0 1;
}

.section-title {
    color: #8fbcbb;
    text-style: bold;
    padding-top: 1;
}

#sessions-list, #loot-list {
    height: 8;
    border: round #2e3440;
    background: #0b1118;
}

#workspace-tree {
    height: 1fr;
    border: round #2e3440;
    background: #0b1118;
}

#feed-title {
    color: #81a1c1;
    text-style: bold;
    height: 1;
}

#agent-feed, #forensic-feed {
    height: 1fr;
    border: round #2e3440;
    background: #0b1118;
}

#forensic-feed {
    display: none;
}

#telemetry-box {
    height: 1fr;
    border: round #2e3440;
    background: #0b1118;
    padding: 0 1;
}

.telemetry-value {
    color: #a3be8c;
    text-style: bold;
}

#bottom-bar {
    height: 3;
    border: heavy #4c566a;
    background: #101824;
    layout: horizontal;
    padding: 0 1;
}

#redaction-state {
    width: 28;
    color: #a3be8c;
}

#command-input {
    width: 1fr;
    background: #0b1118;
    border: none;
    color: #eceff4;
}

#runtime-state {
    width: 32;
    content-align: right middle;
    color: #88c0d0;
}
"""


class CommandInput(Input):
    """Input with app-level tab completion."""

    def key_tab(self) -> None:
        app = self.app
        if isinstance(app, CerebroApp):
            app.complete_command_input()


@dataclass(slots=True)
class FeedEvent:
    category: str
    message: str
    timestamp: str


class RedactionEngine:
    """Lightweight display redaction used before rendering."""

    _patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s]+"), r"\1=[REDACTED]"),
        (re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b"), "[REDACTED_EMAIL]"),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    )

    def scrub(self, text: str) -> str:
        value = text
        for pattern, replacement in self._patterns:
            value = pattern.sub(replacement, value)
        return value


class CerebroApp(App):
    """Professional forensic dashboard for CAI operator workflows."""

    CSS = THEME_CSS
    TITLE = "Cerebro Command Dashboard"
    SUB_TITLE = "Forensic Operator Mode"

    BINDINGS = [
        Binding("f2", "toggle_forensic", "Forensic Log"),
        Binding("ctrl+l", "clear_feed", "Clear Feed"),
        Binding("ctrl+r", "refresh_workspace", "Refresh Workspace"),
    ]

    show_forensic = reactive(False)
    critique_mode = reactive(False)
    token_counter = reactive(0)

    class DispatchFeed(Message):
        def __init__(self, event: FeedEvent) -> None:
            super().__init__()
            self.event = event

    def __init__(self, agent: Any = None, initial_prompt: Optional[str] = None) -> None:
        super().__init__()
        self._agent = agent
        self._initial_prompt = initial_prompt or ""
        self._workspace = get_project_space().ensure_initialized().resolve()
        self._evidence_root = (self._workspace / "evidence").resolve()
        self._audit_dir = (self._workspace / ".cai" / "audit").resolve()
        self._redactor = RedactionEngine()
        self._feed_queue: asyncio.Queue[FeedEvent] = asyncio.Queue()
        self._tool_suggestions = [
            "nmap",
            "wget",
            "netcat",
            "shodan_search",
            "filesystem",
            "exec_code",
            "session_checkpoint",
            "session_resume",
            "forensic_tail",
            "help",
        ]
        self._evidence_snapshot: set[str] = set()
        self._last_audit_size = 0
        self._pulse_state = False

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(ASCII_HEADER, id="header"),
            Horizontal(
                Vertical(
                    Label("Navigation", classes="section-title"),
                    Static("Sessions", classes="section-title"),
                    RichLog(id="sessions-list", highlight=False, wrap=False),
                    Static("Loot Gallery", classes="section-title"),
                    RichLog(id="loot-list", highlight=False, wrap=False),
                    Static("Workspace Explorer", classes="section-title"),
                    Tree("workspace", id="workspace-tree"),
                    id="left-sidebar",
                ),
                Vertical(
                    Static("Agent Activity Feed", id="feed-title"),
                    RichLog(id="agent-feed", highlight=False, wrap=True),
                    RichLog(id="forensic-feed", highlight=False, wrap=False),
                    id="center-pane",
                ),
                Vertical(
                    Label("Telemetry", classes="section-title"),
                    Static(id="telemetry-box"),
                    id="right-sidebar",
                ),
                id="main",
            ),
            Horizontal(
                Static("Redaction: ACTIVE", id="redaction-state"),
                CommandInput(placeholder="Type command (Tab for tool completion)", id="command-input"),
                Static("status: nominal", id="runtime-state"),
                id="bottom-bar",
            ),
            id="root",
        )

    async def on_mount(self) -> None:
        self._init_logs()
        self._render_workspace_tree()
        self._refresh_sidebar_lists()
        self._refresh_telemetry()

        self.run_worker(self._feed_pump(), exclusive=False)
        self.set_interval(1.0, self._refresh_workspace_if_changed)
        self.set_interval(1.0, self._refresh_telemetry)
        self.set_interval(0.75, self._tail_forensic_audit)
        self.set_interval(0.4, self._critique_pulse_tick)

        await self.enqueue_event("reasoning", "Cerebro dashboard initialized.")
        if self._initial_prompt:
            await self.enqueue_event("reasoning", f"Initial prompt loaded: {self._initial_prompt}")

    async def enqueue_event(self, category: str, message: str) -> None:
        await self._feed_queue.put(
            FeedEvent(category=category, message=self._redactor.scrub(message), timestamp=datetime.now(tz=UTC).strftime("%H:%M:%S"))
        )

    async def _feed_pump(self) -> None:
        while True:
            event = await self._feed_queue.get()
            self.post_message(self.DispatchFeed(event))

    @on(DispatchFeed)
    def _handle_feed_event(self, message: DispatchFeed) -> None:
        feed = self.query_one("#agent-feed", RichLog)
        event = message.event
        styled = self._render_feed_line(event)
        feed.write(styled)

        self.token_counter += max(1, len(event.message) // 4)
        self._refresh_telemetry()

    def _render_feed_line(self, event: FeedEvent) -> str:
        category = event.category.lower().strip()
        prefix = f"[{event.timestamp}]"
        if category == "reasoning":
            return f"[dim italic]{prefix} {event.message}[/]"
        if category == "tool":
            return f"[cyan]{prefix} [TOOL] {event.message}[/]"
        if category in {"finding", "critical"}:
            return f"[bold #d08770]{prefix} [FINDING] {event.message}[/]"
        if category == "critique":
            self.critique_mode = True
            return f"[bold #ebcb8b]{prefix} [CRITIQUE] {event.message}[/]"
        return f"[white]{prefix} {event.message}[/]"

    def action_toggle_forensic(self) -> None:
        self.show_forensic = not self.show_forensic
        feed = self.query_one("#agent-feed", RichLog)
        forensic = self.query_one("#forensic-feed", RichLog)
        if self.show_forensic:
            feed.display = False
            forensic.display = True
            self.query_one("#runtime-state", Static).update("status: forensic-log")
        else:
            forensic.display = False
            feed.display = True
            self.query_one("#runtime-state", Static).update("status: agent-feed")

    def action_clear_feed(self) -> None:
        self.query_one("#agent-feed", RichLog).clear()
        self.query_one("#forensic-feed", RichLog).clear()
        self.token_counter = 0
        self._refresh_telemetry()

    def action_refresh_workspace(self) -> None:
        self._render_workspace_tree()
        self._refresh_sidebar_lists()

    def complete_command_input(self) -> None:
        command_input = self.query_one("#command-input", CommandInput)
        current = command_input.value.strip()
        if not current:
            command_input.value = self._tool_suggestions[0]
            command_input.cursor_position = len(command_input.value)
            return

        matches = [tool for tool in self._tool_suggestions if tool.startswith(current)]
        if not matches:
            return
        command_input.value = matches[0]
        command_input.cursor_position = len(command_input.value)

    @on(Input.Submitted, "#command-input")
    async def _on_command_submitted(self, event: Input.Submitted) -> None:
        command = self._redactor.scrub(event.value.strip())
        event.input.value = ""
        if not command:
            return

        await self.enqueue_event("tool", f"operator command: {command}")
        if command == "help":
            await self.enqueue_event("reasoning", "Commands: help, critique:on, critique:off, forensic_tail, refresh")
            return
        if command == "critique:on":
            self.critique_mode = True
            await self.enqueue_event("critique", "MODE_CRITIQUE engaged")
            return
        if command == "critique:off":
            self.critique_mode = False
            await self.enqueue_event("reasoning", "Critique pulse disabled")
            return
        if command == "refresh":
            self.action_refresh_workspace()
            await self.enqueue_event("reasoning", "Workspace and loot views refreshed")
            return
        if command == "forensic_tail":
            self._tail_forensic_audit(force=True)
            return

        await self.enqueue_event("reasoning", f"No handler for command '{command}'")

    def _init_logs(self) -> None:
        for widget_id in ("#agent-feed", "#forensic-feed", "#sessions-list", "#loot-list"):
            self.query_one(widget_id, RichLog).auto_scroll = True

    def _refresh_sidebar_lists(self) -> None:
        sessions = self.query_one("#sessions-list", RichLog)
        loot = self.query_one("#loot-list", RichLog)
        sessions.clear()
        loot.clear()

        session_env = os.getenv("CAI_WORKSPACE", "default")
        sessions.write(f"[cyan]active workspace:[/] {session_env}")
        sessions.write(f"[cyan]agent id:[/] {os.getenv('CAI_AGENT_ID', 'unknown-agent')}")

        if self._evidence_root.exists():
            items = sorted(path for path in self._evidence_root.rglob("*") if path.is_file())[:80]
            for path in items:
                loot.write(f"[green]{path.relative_to(self._workspace)}[/]")
        else:
            loot.write("[dim]No evidence yet[/]")

    def _render_workspace_tree(self) -> None:
        tree = self.query_one("#workspace-tree", Tree)
        tree.clear()
        root = tree.root
        root.set_label(str(self._workspace.name))
        self._populate_tree(root, self._workspace, depth=0, max_depth=4)
        tree.root.expand()

    def _populate_tree(self, node: Any, path: Path, depth: int, max_depth: int) -> None:
        if depth >= max_depth:
            return
        try:
            children = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except Exception:
            return
        for child in children[:200]:
            if child.name.startswith(".") and child.name not in {".cai"}:
                continue
            label = child.name + ("/" if child.is_dir() else "")
            sub = node.add(label)
            if child.is_dir():
                self._populate_tree(sub, child, depth + 1, max_depth)

    def _refresh_workspace_if_changed(self) -> None:
        current = set()
        if self._evidence_root.exists():
            for path in self._evidence_root.rglob("*"):
                if path.is_file():
                    current.add(str(path.relative_to(self._workspace)))
        if current != self._evidence_snapshot:
            new_items = sorted(current - self._evidence_snapshot)
            self._evidence_snapshot = current
            self._render_workspace_tree()
            self._refresh_sidebar_lists()
            for item in new_items[:10]:
                self.query_one("#agent-feed", RichLog).write(f"[cyan][artifact][/cyan] {item}")

    def _tail_forensic_audit(self, force: bool = False) -> None:
        forensic = self.query_one("#forensic-feed", RichLog)
        if not self._audit_dir.exists():
            return
        log_file = self._audit_dir / "local_exec" / "executions.jsonl"
        if not log_file.exists():
            return

        size = log_file.stat().st_size
        if not force and size <= self._last_audit_size:
            return

        with log_file.open("r", encoding="utf-8", errors="replace") as handle:
            if not force and self._last_audit_size > 0:
                handle.seek(self._last_audit_size)
            data = handle.read()

        self._last_audit_size = size
        for line in data.splitlines()[-120:]:
            forensic.write(self._redactor.scrub(line))

    def _critique_pulse_tick(self) -> None:
        pane = self.query_one("#center-pane", Vertical)
        if self.critique_mode:
            self._pulse_state = not self._pulse_state
            if self._pulse_state:
                pane.add_class("critique-pulse")
            else:
                pane.remove_class("critique-pulse")
        else:
            pane.remove_class("critique-pulse")

    def _refresh_telemetry(self) -> None:
        telemetry = self.query_one("#telemetry-box", Static)
        agent_id = os.getenv("CAI_AGENT_ID", "unknown-agent")
        cpu, mem = self._runner_load_stats()
        redaction = "ACTIVE"

        telemetry.update(
            "\n".join(
                [
                    "[bold #88c0d0]Session Telemetry[/]",
                    f"Agent ID: [#a3be8c]{agent_id}[/]",
                    f"CPU Load: [#a3be8c]{cpu}[/]",
                    f"Memory: [#a3be8c]{mem}[/]",
                    f"Token Counter: [#a3be8c]{self.token_counter}[/]",
                    f"Redaction Engine: [#a3be8c]{redaction}[/]",
                    f"Forensic View: [#a3be8c]{'ON' if self.show_forensic else 'OFF'}[/]",
                    f"Critique Mode: [#a3be8c]{'ON' if self.critique_mode else 'OFF'}[/]",
                ]
            )
        )

    def _runner_load_stats(self) -> tuple[str, str]:
        cpu_text = "n/a"
        mem_text = "n/a"
        with suppress(Exception):
            if hasattr(os, "getloadavg"):
                load = os.getloadavg()[0]
                cpu_text = f"{load:.2f} (1m load)"
        with suppress(Exception):
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                mem_mb = rss_kb / (1024 * 1024)
            else:
                mem_mb = rss_kb / 1024
            mem_text = f"{mem_mb:.1f} MB"
        return cpu_text, mem_text


# Backward-compatible alias for existing launch paths.
CAIApp = CerebroApp


def run_tui(agent: Any = None, initial_prompt: Optional[str] = None) -> None:
    """Launch Cerebro dashboard (blocking)."""

    CerebroApp(agent=agent, initial_prompt=initial_prompt).run()


__all__ = ["CerebroApp", "CAIApp", "run_tui"]
