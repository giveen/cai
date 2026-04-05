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
    """Matrix-themed TUI – mirrors the CAI PRO layout in green-on-black."""

    TITLE = "CAI"

    BINDINGS = [
        Binding("ctrl+q", "quit",           "Exit",           show=True),
        Binding("ctrl+l", "clear_log",      "Clear",          show=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar",        show=True),
        Binding("escape", "cancel_run",     "Cancel",         show=True),
        Binding("shift+a", "focus_prompt",  "Prompt Alt",     show=True),
        Binding("ctrl+p", "command_palette","Command Palette", show=True),
    ]

    CSS = _CSS

    def __init__(
        self,
        agent=None,
        initial_prompt: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._initial_prompt = initial_prompt
        self._agent_name = (
            getattr(agent, "name", "one_tool_agent") if agent else "one_tool_agent"
        )
        self._model_name = os.getenv("CAI_MODEL", "alias1")
        self._current_worker = None
        self._sidebar_visible = True
        # Queue items: list of (done: bool, text: str)
        self._queue_items: list[tuple[bool, str]] = []
        # Available agents populated on mount
        self._available_agents: dict = {}
        # Active team index or None
        self._active_team: Optional[int] = None

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
                    # ── Agents tab ───────────────────────────────────────
                    with TabPane("Agents", id="tab-agents"):
                        with Vertical(id="agents-pane"):
                            with ScrollableContainer(id="agents-scroll"):
                                # Agent buttons are populated in on_mount
                                pass
                            # Teams sub-section
                            with Vertical(id="teams-section"):
                                yield Static("Teams", id="teams-label")
                                with ScrollableContainer(id="teams-scroll"):
                                    for i, (label, _) in enumerate(TEAM_PRESETS):
                                        yield Button(
                                            f"#{i + 1} {label}",
                                            id=f"team-{i}",
                                            classes="team-btn",
                                        )
                                yield Button(
                                    "+ Create Team",
                                    id="new-team-btn",
                                )
                    # ── Queue tab ────────────────────────────────────────
                    with TabPane("Queue", id="tab-queue"):
                        with Vertical(id="queue-pane"):
                            yield ListView(id="queue-list")
                            with Horizontal(id="queue-input-row"):
                                yield Static("+", id="queue-prefix")
                                yield Input(
                                    placeholder="Add task / command…",
                                    id="queue-input",
                                )
            # ── Right main area ──────────────────────────────────────────
            with Vertical(id="main-area"):
                yield RichLog(
                    id="output-log",
                    highlight=False,
                    markup=True,
                    wrap=True,
                )
                yield Static("", id="status-bar")
                with Horizontal(id="input-row"):
                    yield Static("CAI>", id="input-prefix")
                    yield Input(
                        placeholder="Type a message, /help for commands…",
                        id="user-input",
                    )
        yield Footer()

    # ------------------------------------------------------------------ lifecycle

    async def on_mount(self) -> None:
        # Populate agent buttons
        try:
            from cai.agents import get_available_agents
            self._available_agents = get_available_agents()
        except Exception:
            self._available_agents = {}

        scroll = self.query_one("#agents-scroll", ScrollableContainer)
        for agent_name in sorted(self._available_agents.keys()):
            label = agent_name if len(agent_name) <= 20 else agent_name[:18] + ".."
            btn = Button(label, id=f"agent-{agent_name}", classes="agent-btn")
            await scroll.mount(btn)

        # Mark active agent
        self._highlight_active_agent(self._agent_name)

        # Banner + welcome
        log = self.query_one("#output-log", RichLog)
        for line in _BANNER_LINES:
            log.write(RichText(line, style="#00ff00"))
        log.write(RichText("", style=""))
        log.write(RichText(
            "Terminal 1 ready — Type /help for commands",
            style="#006600",
        ))
        log.write(RichText("", style=""))

        self.query_one("#user-input", Input).focus()
        if self._initial_prompt:
            await self._dispatch(self._initial_prompt)

    # ------------------------------------------------------------------ button events

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""

        # Agent button pressed → switch active agent
        if btn_id.startswith("agent-"):
            agent_name = btn_id[len("agent-"):]
            new_agent = self._available_agents.get(agent_name)
            if new_agent is not None:
                self._agent = new_agent
                self._agent_name = agent_name
                self._highlight_active_agent(agent_name)
                self._log_info(f"Switched to agent: [bold]{agent_name}[/bold]")
                # Update header
                try:
                    header = self.query_one(CaiHeader)
                    header._agent_name = agent_name
                    header.query_one("#header-right", Static).update(
                        f"[#00cc00]{agent_name}[/#00cc00][#004400] ▼ [/#004400]"
                        f"[#00cc00]{self._model_name}[/#00cc00][#004400] ▼ [/#004400]"
                        "[bold #00ff00]●[/bold #00ff00]"
                    )
                except Exception:
                    pass
            return

        # Team button pressed → load team preset
        if btn_id.startswith("team-"):
            idx = int(btn_id[len("team-"):])
            self._activate_team(idx)
            return

        # Create-team button
        if btn_id == "new-team-btn":
            self._prompt_new_team()
            return

    # ------------------------------------------------------------------ input events

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id
        text = event.value.strip()
        event.input.clear()

        if input_id == "queue-input":
            # Add item to queue
            if text:
                self._add_queue_item(text)
            return

        # Main terminal input
        if text:
            await self._dispatch(text)

    # ------------------------------------------------------------------ queue

    def _add_queue_item(self, text: str) -> None:
        self._queue_items.append((False, text))
        idx = len(self._queue_items) - 1
        label_text = f"○ {text}"
        item = ListItem(Label(label_text), id=f"queue-item-{idx}")
        self.query_one("#queue-list", ListView).mount(item)

    def _mark_queue_item_done(self, idx: int) -> None:
        if 0 <= idx < len(self._queue_items):
            done, text = self._queue_items[idx]
            if not done:
                self._queue_items[idx] = (True, text)
                try:
                    item = self.query_one(f"#queue-item-{idx}", ListItem)
                    item.query_one(Label).update(f"[strike #004400]✓ {text}[/strike #004400]")
                except Exception:
                    pass

    # ------------------------------------------------------------------ teams

    def _activate_team(self, idx: int) -> None:
        label, agent_types = TEAM_PRESETS[idx]
        # Deactivate previous
        if self._active_team is not None:
            try:
                old = self.query_one(f"#team-{self._active_team}", Button)
                old.remove_class("-active-team")
            except Exception:
                pass
        self._active_team = idx
        try:
            self.query_one(f"#team-{idx}", Button).add_class("-active-team")
        except Exception:
            pass
        self._log_info(
            f"Team [bold]#{idx + 1} {label}[/bold] selected — "
            f"{len(agent_types)} agents: {', '.join(agent_types)}"
        )

    def _prompt_new_team(self) -> None:
        """Redirect to queue input with a new-team prompt."""
        inp = self.query_one("#queue-input", Input)
        inp.placeholder = "team: agent1 agent2 agent3…"
        inp.focus()
        self._log_info(
            "Type agent names in the Queue input to define a new team, e.g. "
            "[#006600]redteam_agent blueteam_agent[/#006600]",
        )

    # ------------------------------------------------------------------ agent highlight

    def _highlight_active_agent(self, name: str) -> None:
        for btn in self.query(".agent-btn"):
            btn.remove_class("-active-agent")
        try:
            self.query_one(f"#agent-{name}", Button).add_class("-active-agent")
        except Exception:
            pass

    # ------------------------------------------------------------------ actions

    def action_clear_log(self) -> None:
        self.query_one("#output-log", RichLog).clear()

    def action_cancel_run(self) -> None:
        if self._current_worker is not None:
            self._current_worker.cancel()
            self._current_worker = None
        self._set_status("")

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        self._sidebar_visible = not self._sidebar_visible
        sidebar.display = self._sidebar_visible

    def action_focus_prompt(self) -> None:
        self.query_one("#user-input", Input).focus()

    def action_command_palette(self) -> None:
        pass  # Reserved

    # ------------------------------------------------------------------ dispatch

    async def _dispatch(self, text: str) -> None:
        log = self.query_one("#output-log", RichLog)
        log.write(RichText(f"> {text}", style="bold #00ff00"))

        cmd = text.lower().split()[0] if text.startswith("/") else ""
        if cmd in ("/exit", "/quit"):
            self.exit()
            return
        if cmd == "/help":
            log.write(RichText(
                "  /exit /quit     Exit the TUI\n"
                "  /clear          Clear the output log\n"
                "  /help           Show this message\n"
                "\n"
                "  ^q  Exit    ^l  Clear    ^s  Toggle Sidebar\n"
                "  Esc Cancel  shift+a  Focus prompt",
                style="#00cc00",
            ))
            return
        if cmd in ("/clear", "/cls"):
            log.clear()
            return

        if self._agent is None:
            log.write(RichText(
                "  No agent loaded. Check CAI_AGENT_TYPE and restart.",
                style="#ff4444",
            ))
            return

        self._set_status(
            f"CAI> [{datetime.now().strftime('%H:%M:%S')}] * Thinking…"
        )
        self._current_worker = self._stream_agent(text)

    # ------------------------------------------------------------------ agent worker

    @work(exclusive=True)
    async def _stream_agent(self, text: str) -> None:
        """Run the CAI agent in a Textual worker; stream output into the log."""
        log = self.query_one("#output-log", RichLog)
        stream_iter = None

        try:
            from cai.sdk.agents import Runner
            from cai.sdk.agents.stream_events import RunItemStreamEvent
            from cai.sdk.agents.items import ToolCallOutputItem

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
                        raw,
                        "name",
                        getattr(getattr(raw, "function", None), "name", "tool"),
                    )
                    fn_args = str(getattr(raw, "arguments", "…"))
                    if len(fn_args) > 80:
                        fn_args = fn_args[:80] + "…"
                    log.write(RichText(f"  ↳ {fn_name}({fn_args})", style="#006600"))

                elif ev_name == "tool_output":
                    if isinstance(item, ToolCallOutputItem):
                        output_lines = str(item.output).splitlines()
                        for line in output_lines[:15]:
                            log.write(RichText(f"    {line}", style="#00aa00"))
                        if len(output_lines) > 15:
                            log.write(RichText(
                                f"    … {len(output_lines) - 15} more lines …",
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

    # ------------------------------------------------------------------ helpers

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(text)
        except Exception:
            pass

    def _log_info(self, markup: str) -> None:
        try:
            self.query_one("#output-log", RichLog).write(
                RichText.from_markup(f"  [dim]{markup}[/dim]")
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_tui(agent=None, initial_prompt: Optional[str] = None) -> None:
    """Launch the Matrix TUI (blocks until the user exits with ^q or /exit)."""
    CAIApp(agent=agent, initial_prompt=initial_prompt).run()

