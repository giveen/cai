"""
Matrix-themed Textual TUI for CAI.

Activated with:
    CAI_TUI=true cai
    cai --tui

Layout mirrors the CAI PRO screenshot; colours are classic
Matrix green-on-black throughout.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Optional

from textual import work, on
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from rich.text import Text as RichText

# ---------------------------------------------------------------------------
# ASCII banner – Matrix green applied via Rich Text styles at render time
# ---------------------------------------------------------------------------
_BANNER_LINES = [
    "          CCCCCCCCCCCCC      +++++++++    +++++++++     IIIIIIIIII",
    "        CCC::::::::::::C  ++++++++++     ++++++++++     I::::::::I",
    "       CC:::::::::::::::C++++++++++       ++++++++++    I::::::::I",
    "      C:::::CCCCCCCC::::C+++++++++   ++    +++++++++    II::::::II",
    "     C:::::C       CCCCCC +++++++  +++++    +++++++       I::::I  ",
    "     C:::::C               +++++  +++++++    +++++        I::::I  ",
    "     C:::::C               ++++    +++++++    ++++        I::::I  ",
    "     C:::::C                ++      +++++++    ++         I::::I  ",
    "     C:::::C                 +   +++++++++++   +          I::::I  ",
    "     C:::::C                   +++++++++++++              I::::I  ",
    "     C:::::C                    +++++++++++               I::::I  ",
    "     C:::::C       CCCCCC        +++++++++                I::::I  ",
    "      C:::::CCCCCCCC::::C         +++++++              II::::::II ",
    "       CC:::::::::::::::C           ++++               I::::::::I ",
    "         CCC::::::::::::C             ++               I::::::::I ",
    "            CCCCCCCCCCCCC               +              IIIIIIIIII ",
    "",
    "               Cybersecurity AI (CAI)   ·   Bug bounty-ready AI  ",
]

# ---------------------------------------------------------------------------
# Embedded CSS – everything Matrix green-on-black
# ---------------------------------------------------------------------------
_CSS = """
/* ═══════════════ MATRIX THEME ═══════════════ */
Screen {
    background: #000000;
    color: #00ff00;
}

/* ─── Header row ─── */
CaiHeader {
    height: 1;
    dock: top;
    background: #001a00;
    layout: horizontal;
}

#header-left {
    width: 1fr;
    height: 1;
    content-align: left middle;
    padding: 0 1;
    color: #00ff00;
}

#header-right {
    width: auto;
    height: 1;
    content-align: right middle;
    padding: 0 1;
    color: #00cc00;
}

/* ─── Main body split: sidebar + main area ─── */
#body {
    layout: horizontal;
    height: 1fr;
}

/* ─── Left sidebar ─── */
#sidebar {
    width: 28;
    min-width: 24;
    background: #000000;
    border-right: solid #003300;
    height: 100%;
    layout: vertical;
}

#sidebar TabbedContent {
    background: #000000;
    height: 1fr;
    color: #00ff00;
}

#sidebar TabbedContent Tabs {
    background: #001a00;
    height: 3;
    border-bottom: solid #003300;
}

#sidebar TabbedContent Tab {
    background: #001a00;
    color: #006600;
    padding: 0 2;
}

#sidebar TabbedContent Tab.-active {
    background: #003300;
    color: #00ff00;
    text-style: bold;
}

#sidebar TabbedContent Tab:hover {
    background: #002200;
    color: #00cc00;
}

#sidebar TabbedContent ContentSwitcher {
    height: 1fr;
    background: #000000;
}

/* Agents pane */
#agents-pane {
    height: 1fr;
    background: #000000;
    layout: vertical;
}

#agents-scroll {
    height: 1fr;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

.agent-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #00cc00;
    border: none;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

.agent-btn:hover {
    background: #001a00;
    color: #00ff00;
}

.agent-btn.-active-agent {
    background: #002800;
    color: #00ff00;
    text-style: bold;
}

#teams-section {
    height: auto;
    max-height: 18;
    background: #000000;
    border-top: solid #003300;
    layout: vertical;
}

#teams-label {
    height: 1;
    background: #001a00;
    color: #006600;
    padding: 0 1;
    text-style: bold;
}

#teams-scroll {
    height: 1fr;
    max-height: 14;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

.team-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #008800;
    border: none;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

.team-btn:hover {
    background: #001a00;
    color: #00ff00;
}

.team-btn.-active-team {
    background: #002800;
    color: #00ff00;
    text-style: bold;
}

#new-team-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #004400;
    border: none;
    border-top: solid #002200;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

#new-team-btn:hover {
    background: #001a00;
    color: #00aa00;
}

/* Queue pane */
#queue-pane {
    height: 1fr;
    background: #000000;
    layout: vertical;
}

#queue-list {
    height: 1fr;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

#queue-list > ListItem {
    background: #000000;
    color: #00cc00;
    padding: 0 1;
    height: 1;
    border-bottom: solid #001a00;
}

#queue-list > ListItem:hover {
    background: #001a00;
    color: #00ff00;
}

#queue-list > ListItem.--highlight {
    background: #002800;
    color: #00ff00;
}

#queue-list Label {
    background: transparent;
    color: #00cc00;
    width: 1fr;
}

#queue-input-row {
    height: 3;
    layout: horizontal;
    background: #000000;
    border-top: solid #003300;
}

#queue-prefix {
    width: 4;
    height: 3;
    content-align: left middle;
    color: #004400;
    padding: 0 1;
    background: #000000;
}

#queue-input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

#queue-input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

#queue-input > .input--placeholder {
    color: #004400;
}

/* ─── Right area: output + status + terminal input ─── */
#main-area {
    width: 1fr;
    height: 100%;
    layout: vertical;
}

/* ─── Scrollable output log ─── */
#output-log {
    height: 1fr;
    background: #000000;
    color: #00ff00;
    border: none;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
    padding: 0 1;
}

/* ─── One-line status / thinking bar ─── */
#status-bar {
    height: 1;
    background: #001a00;
    color: #00aa00;
    padding: 0 1;
    border-top: solid #003300;
}

/* ─── Input row ─── */
#input-row {
    height: 3;
    layout: horizontal;
    background: #000000;
    border-top: solid #003300;
}

#input-prefix {
    width: 7;
    height: 3;
    content-align: left middle;
    color: #00ff00;
    padding: 0 1;
    background: #000000;
}

#user-input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

#user-input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

#user-input > .input--placeholder {
    color: #004400;
}

/* ─── Footer ─── */
Footer {
    background: #001a00;
    color: #00aa00;
}

Footer > .footer--key {
    background: #003300;
    color: #00ff00;
}

Footer > .footer--description {
    color: #00aa00;
}

Footer > .footer--spacer {
    background: #001a00;
}

/* ─── Terminals container ─── */
#terminals {
    height: 1fr;
    layout: horizontal;
    background: #000000;
}

TerminalPanel {
    width: 1fr;
    height: 100%;
    layout: vertical;
    border: solid #003300;
    background: #000000;
}

TerminalPanel.-active-panel {
    border: solid #00ff00;
}

.term-header {
    height: 1;
    background: #001a00;
    color: #00cc00;
    padding: 0 1;
    border-bottom: solid #003300;
}

.term-log {
    height: 1fr;
    background: #000000;
    color: #00ff00;
    border: none;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
    padding: 0 1;
}

.term-status {
    height: 1;
    background: #001a00;
    color: #00aa00;
    padding: 0 1;
}

.term-input-row {
    height: 3;
    layout: horizontal;
    background: #000000;
    border-top: solid #003300;
}

.term-input-prefix {
    width: 7;
    height: 3;
    content-align: left middle;
    color: #00ff00;
    padding: 0 1;
    background: #000000;
}

.term-input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

.term-input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

.term-input > .input--placeholder {
    color: #004400;
}

/* ─── Agent action modal ─── */
AgentModal {
    align: center middle;
    background: rgba(0, 0, 0, 0.8);
}

#modal-dialog {
    width: 32;
    height: auto;
    background: #001a00;
    border: solid #00ff00;
    padding: 1 2;
    layout: vertical;
}

#modal-agent-label {
    height: 2;
    color: #00ff00;
    text-style: bold;
    content-align: center middle;
    border-bottom: solid #003300;
    margin-bottom: 1;
}

.modal-btn {
    width: 1fr;
    height: 3;
    background: #002800;
    color: #00cc00;
    border: solid #003300;
    margin-bottom: 1;
    text-align: center;
}

.modal-btn:hover {
    background: #003300;
    color: #00ff00;
}

.modal-btn:focus {
    border: solid #00ff00;
    color: #00ff00;
}

.modal-btn--cancel {
    background: #050000;
    color: #446644;
    border: solid #002200;
    margin-bottom: 0;
}

.modal-btn--cancel:hover {
    background: #110000;
    color: #00aa44;
}
"""


# ---------------------------------------------------------------------------
# Preset team compositions (label, list of agent-type strings)
# ---------------------------------------------------------------------------
TEAM_PRESETS = [
    ("2 Red + 2 Blue",  ["redteam_agent", "redteam_agent", "blueteam_agent",   "blueteam_agent"]),
    ("1 Red Solo",      ["redteam_agent"]),
    ("2 Red + 2 Bug",   ["redteam_agent", "redteam_agent", "bug_bounter_agent", "bug_bounter_agent"]),
    ("2 Blue + 2 Red",  ["blueteam_agent", "blueteam_agent", "redteam_agent",   "redteam_agent"]),
    ("Red + Blue",      ["redteam_agent", "blueteam_agent"]),
    ("4 Red",           ["redteam_agent"] * 4),
    ("4 Blue",          ["blueteam_agent"] * 4),
    ("4 Bug",           ["bug_bounter_agent"] * 4),
    ("Mixed 4",         ["redteam_agent", "blueteam_agent", "bug_bounter_agent", "retester_agent"]),
]


# ---------------------------------------------------------------------------
# Agent-selection modal
# ---------------------------------------------------------------------------
class AgentModal(ModalScreen):
    """Pop-up shown when the user clicks an agent button.

    Dismissed with:
      ('update', agent_name)  – re-assign the current active terminal
      ('new',    agent_name)  – open a new terminal panel for this agent
      None                    – cancelled
    """

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, agent_name: str, active_term_label: str) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._active_term_label = active_term_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(
                f"Agent: [bold]{self._agent_name}[/bold]",
                id="modal-agent-label",
            )
            yield Button(
                f"Update {self._active_term_label}",
                id="modal-update",
                classes="modal-btn",
            )
            yield Button(
                "New Terminal",
                id="modal-new",
                classes="modal-btn",
            )
            yield Button(
                "Cancel",
                id="modal-cancel",
                classes="modal-btn modal-btn--cancel",
            )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "modal-update":
            self.dismiss(("update", self._agent_name))
        elif btn_id == "modal-new":
            self.dismiss(("new", self._agent_name))
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Terminal panel widget  (one per open terminal)
# ---------------------------------------------------------------------------
class TerminalPanel(Widget):
    """Self-contained chat terminal: header bar + RichLog + status + input."""

    # Sent to the App when this panel is clicked so the app can mark it active
    class Activated(Message):
        def __init__(self, term_id: int) -> None:
            super().__init__()
            self.term_id = term_id

    # Sent when the close button is clicked
    class CloseRequested(Message):
        def __init__(self, term_id: int) -> None:
            super().__init__()
            self.term_id = term_id

    def __init__(
        self,
        term_id: int,
        agent,
        agent_name: str,
        model_name: str,
    ) -> None:
        super().__init__(id=f"terminal-panel-{term_id}")
        self._term_id = term_id
        self._agent = agent
        self._agent_name = agent_name
        self._model_name = model_name

    # header text helper
    def _header_text(self) -> str:
        return (
            f"[bold #00ff00]T{self._term_id}[/bold #00ff00]"
            f"[#004400] | [/#004400]"
            f"[#00cc00]{self._agent_name}[/#00cc00]"
            f"[#004400] ▼  [/#004400]"
            f"[#00aa00]{self._model_name}[/#00aa00]"
            f"[#004400] ▼  container ▼  [/#004400]"
            f"[bold #00ff00]●[/bold #00ff00]"
            f"  [dim #666600]×[/dim #666600]"
        )

    def compose(self) -> ComposeResult:
        yield Static(
            self._header_text(),
            id=f"term-header-{self._term_id}",
            classes="term-header",
        )
        yield RichLog(
            id=f"term-log-{self._term_id}",
            classes="term-log",
            highlight=False,
            markup=True,
            wrap=True,
        )
        yield Static("", id=f"term-status-{self._term_id}", classes="term-status")
        with Horizontal(classes="term-input-row"):
            yield Static("CAI>", classes="term-input-prefix")
            yield Input(
                placeholder="Type a message…",
                id=f"term-input-{self._term_id}",
                classes="term-input",
            )

    async def on_mount(self) -> None:
        from rich.text import Text as RichText
        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        for line in _BANNER_LINES:
            log.write(RichText(line, style="#00ff00"))
        log.write(RichText("", style=""))
        log.write(RichText(
            f"T{self._term_id} ready — agent: {self._agent_name}",
            style="#006600",
        ))
        log.write(RichText("", style=""))

    def on_click(self) -> None:
        self.post_message(self.Activated(self._term_id))

    def update_agent(self, agent, agent_name: str) -> None:
        """Hot-swap the agent for this terminal and refresh the header."""
        self._agent = agent
        self._agent_name = agent_name
        try:
            self.query_one(
                f"#term-header-{self._term_id}", Static
            ).update(self._header_text())
        except Exception:
            pass

    # ──────────────────────────────────────────────────────── dispatch / worker

    async def dispatch(self, text: str) -> None:
        from rich.text import Text as RichText
        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        log.write(RichText(f"> {text}", style="bold #00ff00"))

        cmd = text.lower().split()[0] if text.startswith("/") else ""
        if cmd in ("/exit", "/quit"):
            self.app.exit()
            return
        if cmd == "/help":
            log.write(RichText(
                "  /exit /quit     Exit the TUI\n"
                "  /clear          Clear this terminal\n"
                "  /help           Show this message\n"
                "\n"
                "  ^q  Exit    ^l  Clear    ^s  Sidebar\n"
                "  Esc Cancel",
                style="#00cc00",
            ))
            return
        if cmd in ("/clear", "/cls"):
            log.clear()
            return

        if self._agent is None:
            log.write(RichText(
                "  No agent loaded. Select one from the sidebar.",
                style="#ff4444",
            ))
            return

        ts = datetime.now().strftime("%H:%M:%S")
        self._set_status(f"T{self._term_id}> [{ts}] ⟳ Thinking…")
        self._run_agent(text)

    @work(exclusive=True)
    async def _run_agent(self, text: str) -> None:
        from rich.text import Text as RichText
        from cai.sdk.agents import Runner
        from cai.sdk.agents.stream_events import RunItemStreamEvent
        from cai.sdk.agents.items import ToolCallOutputItem

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        stream_iter = None
        try:
            result = Runner.run_streamed(self._agent, text)
            stream_iter = result.stream_events()

            async for event in stream_iter:
                if not isinstance(event, RunItemStreamEvent):
                    continue
                ev_name = event.name
                item = event.item

                if ev_name == "message_output_created":
                    try:
                        from cai.sdk.agents.items import ItemHelpers
                        content = ItemHelpers.text_message_output(item)
                    except Exception:
                        content = str(getattr(item, "content", ""))
                    if content:
                        for line in content.splitlines():
                            log.write(RichText(f"CAI>  {line}", style="#00ff00"))

                elif ev_name == "tool_called":
                    raw = getattr(item, "raw_item", item)
                    fn_name = getattr(
                        raw, "name",
                        getattr(getattr(raw, "function", None), "name", "tool"),
                    )
                    fn_args = str(getattr(raw, "arguments", "…"))
                    if len(fn_args) > 80:
                        fn_args = fn_args[:80] + "…"
                    log.write(RichText(f"  ↳ {fn_name}({fn_args})", style="#006600"))

                elif ev_name == "tool_output":
                    if isinstance(item, ToolCallOutputItem):
                        lines = str(item.output).splitlines()
                        for line in lines[:15]:
                            log.write(RichText(f"    {line}", style="#00aa00"))
                        if len(lines) > 15:
                            log.write(RichText(
                                f"    … {len(lines) - 15} more lines …",
                                style="#004400",
                            ))

        except asyncio.CancelledError:
            log.write(RichText("  [cancelled]", style="#ff6600"))
        except Exception as exc:
            log.write(RichText(f"  [error] {exc}", style="#ff4444"))
        finally:
            if stream_iter is not None:
                try:
                    await stream_iter.aclose()
                except Exception:
                    pass
            self._set_status("")

    def _set_status(self, text: str) -> None:
        try:
            self.query_one(f"#term-status-{self._term_id}", Static).update(text)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Custom header widget
# ---------------------------------------------------------------------------
class CaiHeader(Widget):
    """Single-line status bar: tab labels on the left, agent/model on right."""

    DEFAULT_CSS = """
    CaiHeader {
        height: 1;
        dock: top;
        background: #001a00;
        layout: horizontal;
    }
    """

    def __init__(
        self,
        agent_name: str = "one_tool_agent",
        model: str = "alias1",
        ctx: str = "container",
    ) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._model = model
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #00ff00]T1[/bold #00ff00]"
            "[#004400] | [/#004400]"
            "[#00ff00]Terminal[/#00ff00]"
            "[#003300]  Add  Graph  Help[/#003300]",
            id="header-left",
        )
        yield Static(
            f"[#00cc00]{self._agent_name}[/#00cc00][#004400] ▼ [/#004400]"
            f"[#00cc00]{self._model}[/#00cc00][#004400] ▼ [/#004400]"
            f"[#006600]{self._ctx}[/#006600][#004400] ▼  [/#004400]"
            "[bold #00ff00]●[/bold #00ff00]",
            id="header-right",
        )


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class CAIApp(App):
    """Matrix-themed TUI with multi-terminal sidebar."""

    TITLE = "CAI"

    BINDINGS = [
        Binding("ctrl+q", "quit",           "Exit",    show=True),
        Binding("ctrl+l", "clear_active",   "Clear",   show=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar", show=True),
        Binding("escape", "cancel_active",  "Cancel",  show=True),
        Binding("ctrl+p", "command_palette","Palette", show=True),
    ]

    CSS = _CSS

    def __init__(
        self,
        agent=None,
        initial_prompt: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._agent_name: str = (
            getattr(agent, "name", "one_tool_agent") if agent else "one_tool_agent"
        )
        self._model_name: str = os.getenv("CAI_MODEL", "alias1")
        self._initial_prompt = initial_prompt
        self._sidebar_visible = True
        self._available_agents: dict = {}
        self._active_team: Optional[int] = None
        self._queue_items: list[tuple[bool, str]] = []
        # terminal tracking
        self._next_term_id = 1
        self._active_term_id = 1

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield CaiHeader(
            agent_name=self._agent_name,
            model=self._model_name,
        )
        with Horizontal(id="body"):
            # ── Left sidebar ─────────────────────────────────────────────
            with Vertical(id="sidebar"):
                with TabbedContent(id="sidebar-tabs"):
                    with TabPane("Agents", id="tab-agents"):
                        with Vertical(id="agents-pane"):
                            with ScrollableContainer(id="agents-scroll"):
                                pass  # populated in on_mount
                            with Vertical(id="teams-section"):
                                yield Static("Teams", id="teams-label")
                                with ScrollableContainer(id="teams-scroll"):
                                    for i, (label, _) in enumerate(TEAM_PRESETS):
                                        yield Button(
                                            f"#{i + 1} {label}",
                                            id=f"team-{i}",
                                            classes="team-btn",
                                        )
                                yield Button("+ Create Team", id="new-team-btn")
                    with TabPane("Queue", id="tab-queue"):
                        with Vertical(id="queue-pane"):
                            yield ListView(id="queue-list")
                            with Horizontal(id="queue-input-row"):
                                yield Static("+", id="queue-prefix")
                                yield Input(
                                    placeholder="Add task / command…",
                                    id="queue-input",
                                )
            # ── Right: horizontal strip of terminal panels ────────────────
            with Horizontal(id="terminals"):
                pass  # first panel added in on_mount
        yield Footer()

    # ------------------------------------------------------------------ lifecycle

    async def on_mount(self) -> None:
        # Load available agents
        try:
            from cai.agents import get_available_agents
            self._available_agents = get_available_agents()
        except Exception:
            self._available_agents = {}

        scroll = self.query_one("#agents-scroll", ScrollableContainer)
        for name in sorted(self._available_agents.keys()):
            label = name if len(name) <= 20 else name[:18] + ".."
            await scroll.mount(Button(label, id=f"agent-{name}", classes="agent-btn"))

        self._highlight_active_agent(self._agent_name)

        # Spawn the first terminal panel
        first = TerminalPanel(
            term_id=1,
            agent=self._agent,
            agent_name=self._agent_name,
            model_name=self._model_name,
        )
        await self.query_one("#terminals", Horizontal).mount(first)
        self._set_active_terminal(1)

        # Focus the first input
        try:
            self.query_one("#term-input-1", Input).focus()
        except Exception:
            pass

        if self._initial_prompt:
            await first.dispatch(self._initial_prompt)

    # ------------------------------------------------------------------ terminal panel messages

    def on_terminal_panel_activated(self, event: TerminalPanel.Activated) -> None:
        self._set_active_terminal(event.term_id)

    def on_terminal_panel_close_requested(self, event: TerminalPanel.CloseRequested) -> None:
        panels = list(self.query(TerminalPanel))
        if len(panels) <= 1:
            self.exit()
            return
        try:
            panel = self.query_one(f"#terminal-panel-{event.term_id}", TerminalPanel)
            panel.remove()
        except Exception:
            pass
        remaining = list(self.query(TerminalPanel))
        if remaining:
            self._set_active_terminal(remaining[-1]._term_id)

    # ------------------------------------------------------------------ terminal management

    def _set_active_terminal(self, term_id: int) -> None:
        self._active_term_id = term_id
        for panel in self.query(TerminalPanel):
            if panel._term_id == term_id:
                panel.add_class("-active-panel")
            else:
                panel.remove_class("-active-panel")
        # Update header to reflect active terminal
        try:
            panel = self.query_one(f"#terminal-panel-{term_id}", TerminalPanel)
            self.query_one(CaiHeader).query_one("#header-right", Static).update(
                f"[bold #00ff00]T{term_id}[/bold #00ff00]"
                f"[#004400] | [/#004400]"
                f"[#00cc00]{panel._agent_name}[/#00cc00]"
                f"[#004400] ▼ [/#004400]"
                f"[#00aa00]{self._model_name}[/#00aa00]"
                f"[#004400] ▼ [/#004400]"
                f"[bold #00ff00]●[/bold #00ff00]"
            )
        except Exception:
            pass

    async def _add_terminal(self, agent, agent_name: str) -> None:
        self._next_term_id += 1
        tid = self._next_term_id
        panel = TerminalPanel(
            term_id=tid,
            agent=agent,
            agent_name=agent_name,
            model_name=self._model_name,
        )
        await self.query_one("#terminals", Horizontal).mount(panel)
        self._set_active_terminal(tid)
        try:
            self.query_one(f"#term-input-{tid}", Input).focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ button events

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""

        if btn_id.startswith("agent-"):
            agent_name = btn_id[len("agent-"):]
            if agent_name in self._available_agents:
                active_label = f"T{self._active_term_id}"
                self.push_screen(
                    AgentModal(agent_name, active_label),
                    self._handle_agent_modal,
                )
            return

        if btn_id.startswith("team-"):
            self._activate_team(int(btn_id[len("team-"):]))
            return

        if btn_id == "new-team-btn":
            self._prompt_new_team()
            return

    # ------------------------------------------------------------------ modal callback

    async def _handle_agent_modal(self, result) -> None:
        if result is None:
            return  # cancelled
        action, agent_name = result
        new_agent = self._available_agents.get(agent_name)
        if new_agent is None:
            return

        if action == "update":
            try:
                panel = self.query_one(
                    f"#terminal-panel-{self._active_term_id}", TerminalPanel
                )
                panel.update_agent(new_agent, agent_name)
                self._set_active_terminal(self._active_term_id)
            except Exception:
                pass
            self._highlight_active_agent(agent_name)
        elif action == "new":
            await self._add_terminal(new_agent, agent_name)
            self._highlight_active_agent(agent_name)

    # ------------------------------------------------------------------ input events

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id or ""
        text = event.value.strip()
        event.input.clear()

        if input_id == "queue-input":
            if text:
                self._add_queue_item(text)
            return

        if input_id.startswith("term-input-"):
            tid = int(input_id[len("term-input-"):])
            self._set_active_terminal(tid)
            if text:
                try:
                    panel = self.query_one(f"#terminal-panel-{tid}", TerminalPanel)
                    await panel.dispatch(text)
                except Exception:
                    pass
            return

    # ------------------------------------------------------------------ queue

    def _add_queue_item(self, text: str) -> None:
        idx = len(self._queue_items)
        self._queue_items.append((False, text))
        item = ListItem(Label(f"○ {text}"), id=f"queue-item-{idx}")
        self.query_one("#queue-list", ListView).mount(item)

    # ------------------------------------------------------------------ teams

    def _activate_team(self, idx: int) -> None:
        label, agent_types = TEAM_PRESETS[idx]
        if self._active_team is not None:
            try:
                self.query_one(f"#team-{self._active_team}", Button).remove_class("-active-team")
            except Exception:
                pass
        self._active_team = idx
        try:
            self.query_one(f"#team-{idx}", Button).add_class("-active-team")
        except Exception:
            pass
        # Log to active terminal instead of a now-removed output-log
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            panel.query_one(f"#term-log-{self._active_term_id}", RichLog).write(
                RichText.from_markup(
                    f"  [dim]Team [bold]#{idx + 1} {label}[/bold] — "
                    f"{len(agent_types)} agents: {', '.join(agent_types)}[/dim]"
                )
            )
        except Exception:
            pass

    def _prompt_new_team(self) -> None:
        try:
            inp = self.query_one("#queue-input", Input)
            inp.placeholder = "team: agent1 agent2 agent3…"
            inp.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ agent highlight

    def _highlight_active_agent(self, name: str) -> None:
        for btn in self.query(".agent-btn"):
            btn.remove_class("-active-agent")
        try:
            self.query_one(f"#agent-{name}", Button).add_class("-active-agent")
        except Exception:
            pass

    # ------------------------------------------------------------------ actions

    def action_clear_active(self) -> None:
        try:
            self.query_one(
                f"#term-log-{self._active_term_id}", RichLog
            ).clear()
        except Exception:
            pass

    def action_cancel_active(self) -> None:
        try:
            panel = self.query_one(
                f"#terminal-panel-{self._active_term_id}", TerminalPanel
            )
            panel._set_status("")
            # Cancel the panel's worker if running
            for w in panel._workers:
                w.cancel()
        except Exception:
            pass

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        self._sidebar_visible = not self._sidebar_visible
        sidebar.display = self._sidebar_visible

    def action_command_palette(self) -> None:
        pass  # Reserved


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_tui(agent=None, initial_prompt: Optional[str] = None) -> None:
    """Launch the Matrix TUI (blocks until the user exits with ^q or /exit)."""
    CAIApp(agent=agent, initial_prompt=initial_prompt).run()

