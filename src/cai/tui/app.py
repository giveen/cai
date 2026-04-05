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

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Footer, Input, RichLog, Static
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

Input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

Input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

Input > .input--placeholder {
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
        Binding("ctrl+s", "toggle_sidebar", "Toggle Sidebar", show=True),
        Binding("escape", "cancel_run",     "Cancel All",     show=True),
        Binding("ctrl+t", "cycle_theme",    "Cycle Theme",    show=True),
        Binding("ctrl+n", "next_tab",       "Next Terminal",  show=True),
        Binding("ctrl+b", "prev_tab",       "Prev Terminal",  show=True),
        Binding("ctrl+p", "command_palette","Command Palette",show=True),
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

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        yield CaiHeader(
            agent_name=self._agent_name,
            model=self._model_name,
        )
        yield RichLog(id="output-log", highlight=False, markup=True, wrap=True)
        yield Static("", id="status-bar")
        with Horizontal(id="input-row"):
            yield Static("CAI>", id="input-prefix")
            yield Input(placeholder="Type a message, /help for commands…", id="user-input")
        yield Footer()

    # ------------------------------------------------------------------ lifecycle

    async def on_mount(self) -> None:
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

    # ------------------------------------------------------------------ input event

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if text:
            await self._dispatch(text)

    # ------------------------------------------------------------------ actions

    def action_clear_log(self) -> None:
        self.query_one("#output-log", RichLog).clear()

    def action_cancel_run(self) -> None:
        if self._current_worker is not None:
            self._current_worker.cancel()
            self._current_worker = None
        self._set_status("")

    def action_toggle_sidebar(self) -> None:
        pass  # Reserved – sidebar coming in a later iteration

    def action_cycle_theme(self) -> None:
        pass  # Reserved

    def action_next_tab(self) -> None:
        pass  # Reserved – multi-terminal tabs

    def action_prev_tab(self) -> None:
        pass  # Reserved

    def action_command_palette(self) -> None:
        pass  # Reserved

    # ------------------------------------------------------------------ dispatch

    async def _dispatch(self, text: str) -> None:
        log = self.query_one("#output-log", RichLog)
        # Echo the user prompt
        log.write(RichText(f"> {text}", style="bold #00ff00"))

        # Local slash commands
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
                "  Esc Cancel  ^t  Cycle Theme   ^n/^b  Tabs",
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
        result = None

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
                    # Final assistant message text
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
            # Clean up async iterator if still open
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_tui(agent=None, initial_prompt: Optional[str] = None) -> None:
    """Launch the Matrix TUI (blocks until the user exits with ^q or /exit)."""
    CAIApp(agent=agent, initial_prompt=initial_prompt).run()
