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
import json
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
# Agent name prettifier
# ---------------------------------------------------------------------------
_ACRONYMS = {
    "sast", "dfir", "dns", "smtp", "sdr", "ctf", "mcp",
    "api", "iot", "ai", "ml", "ids", "ips", "osint",
}

# Suffix → short distinguishing label appended after the base name
_SUFFIX_LABELS: list[tuple[str, str]] = [
    ("_swarm_pattern", " ↺"),   # swarm
    ("_swarm_pa",      " ↺"),
    ("_pattern",       " ⊕"),   # non-swarm pattern
    ("_agent",         ""),      # plain agent – strip cleanly
]


def _pretty_name(raw: str) -> str:
    """Convert internal agent name → display label.

    android_sast_agent            → Android SAST
    redteam_agent                 → Redteam
    redteam_swarm_pattern         → Redteam ↺
    bug_bounter_agent             → Bug Bounter
    dns_smtp_agent                → DNS SMTP
    blue_team_red_team_shared_..  → Blue/Red Shared
    """
    name = raw
    suffix_label = ""
    for suffix, label in _SUFFIX_LABELS:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            suffix_label = label
            break
    # Abbreviate long blue/red team names to fit the sidebar
    name = (
        name.replace("blue_team_red_team_shared_context", "Blue/Red Shared")
            .replace("blue_team_red_team_split_context",  "Blue/Red Split")
            .replace("blue_team_red_team",                "Blue/Red Team")
    )
    if "_" not in name and " " in name:
        # Already replaced with a friendly string above
        return name + suffix_label
    parts = []
    for part in name.split("_"):
        if not part:
            continue
        parts.append(part.upper() if part.lower() in _ACRONYMS else part.capitalize())
    return " ".join(parts) + suffix_label or raw


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
    width: 32;
    min-width: 26;
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

/* ─── Terminals container: Vertical, two rows max ─── */
#terminals {
    height: 1fr;
    layout: vertical;
    background: #000000;
}

.term-row {
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
# Add small selection styles for the Sessions list
_CSS += """
#sessions-list > ListItem.-selected {
    background: #002800;
    color: #00ff00;
    border-left: solid #00ff00;
}
#sessions-list > ListItem > Button {
    margin-left: 1;
}
# session preview area
#session-preview {
    height: 7;
    padding: 0 1;
    background: #001400;
    color: #00ff00;
    border-top: solid #003300;
}
"""

# Simple on-disk config used by the TUI config screens.
CONFIG_FILE = os.path.join(os.getcwd(), "tui_config.json")

def _load_tui_config() -> dict:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_tui_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


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

    def __init__(self, agent_name: str, active_term_label: str, at_max: bool = False) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._active_term_label = active_term_label
        self._at_max = at_max

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(
                f"Agent: [bold]{_pretty_name(self._agent_name)}[/bold]",
                id="modal-agent-label",
            )
            yield Button(
                f"Update {self._active_term_label}",
                id="modal-update",
                classes="modal-btn",
            )
            yield Button(
                "New Terminal" if not self._at_max else "New Terminal (max 4 reached)",
                id="modal-new",
                classes="modal-btn" + (" modal-btn--cancel" if self._at_max else ""),
                disabled=self._at_max,
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
# Small prompt modal used for rename/export inputs
# ---------------------------------------------------------------------------
class PromptModal(ModalScreen):
    """Simple input modal. Returns the entered string, or None if cancelled."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, prompt: str, default: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._prompt, id="modal-agent-label")
            yield Input(value=self._default, id="prompt-input")
            yield Button("OK", id="prompt-ok", classes="modal-btn")
            yield Button("Cancel", id="prompt-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "prompt-ok":
            try:
                val = self.query_one("#prompt-input", Input).value
            except Exception:
                val = self._default
            self.dismiss(val)
        else:
            self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Simple confirmation modal. Returns True if confirmed, else None."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._message, id="modal-agent-label")
            yield Button("Delete", id="confirm-ok", classes="modal-btn modal-btn--cancel")
            yield Button("Cancel", id="confirm-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "confirm-ok":
            self.dismiss(True)
        else:
            self.dismiss(None)


class ConfigModal(ModalScreen):
    """Modal to confirm opening a Config section. Returns ('open', action_key) or None."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, action_key: str, display_label: str) -> None:
        super().__init__()
        self._action_key = action_key
        self._display = display_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(f"Open config: [bold]{self._display}[/bold]", id="modal-agent-label")
            yield Button("Open", id="config-open", classes="modal-btn")
            yield Button("Cancel", id="config-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "config-open":
            self.dismiss(("open", self._action_key))
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Full-screen Config screens
# ---------------------------------------------------------------------------
class ProvidersScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Providers Configuration", id="modal-agent-label")
            yield Static("Existing providers:", id="providers-label")
            yield ListView(id="providers-list")
            yield Input(placeholder="provider name", id="providers-name")
            yield Input(placeholder="api key", id="providers-key")
            with Horizontal():
                yield Button("Save", id="providers-save", classes="modal-btn")
                yield Button("Test", id="providers-test", classes="modal-btn")
                yield Button("Close", id="providers-cancel", classes="modal-btn modal-btn--cancel")

    async def on_mount(self) -> None:
        try:
            lv = self.query_one("#providers-list", ListView)
            providers = self._config.get("providers", {})
            for name, key in providers.items():
                await lv.mount(ListItem(Label(f"{name}: {key[:6]}..."), id=f"provider-item-{name}"))
        except Exception:
            pass

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "providers-save":
            try:
                name = self.query_one("#providers-name", Input).value.strip()
                key = self.query_one("#providers-key", Input).value.strip()
            except Exception:
                name = ""
                key = ""
            if not name:
                self.dismiss(None)
                return
            self.dismiss(("save_provider", name, key))
        elif btn == "providers-test":
            try:
                name = self.query_one("#providers-name", Input).value.strip()
            except Exception:
                name = ""
            self.dismiss(("test_provider", name))
        else:
            self.dismiss(None)


class ModelParamsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        mp = self._config.get("model_params", {})
        with Vertical(id="modal-dialog"):
            yield Static("Model Parameters", id="modal-agent-label")
            yield Input(value=str(mp.get("temperature", "0.0")), id="model-temp")
            yield Input(value=str(mp.get("max_tokens", "1024")), id="model-max-tokens")
            yield Input(value=str(mp.get("system_prompt", "")), id="model-system")
            with Horizontal():
                yield Button("Save", id="model-save", classes="modal-btn")
                yield Button("Close", id="model-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "model-save":
            try:
                temp = float(self.query_one("#model-temp", Input).value)
            except Exception:
                temp = 0.0
            try:
                max_t = int(self.query_one("#model-max-tokens", Input).value)
            except Exception:
                max_t = 1024
            try:
                sys = self.query_one("#model-system", Input).value
            except Exception:
                sys = ""
            self.dismiss(("save_model_params", {"temperature": temp, "max_tokens": max_t, "system_prompt": sys}))
        else:
            self.dismiss(None)


class MemoryInspectorScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Memory / RAG Inspector", id="modal-agent-label")
            yield Static("Operations:", id="memory-ops")
            with Horizontal():
                yield Button("Rebuild Index", id="memory-rebuild", classes="modal-btn")
                yield Button("Evict All", id="memory-evict", classes="modal-btn modal-btn--cancel")
                yield Button("Close", id="memory-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "memory-rebuild":
            self.dismiss(("rebuild_memory",))
        elif btn == "memory-evict":
            self.dismiss(("evict_memory",))
        else:
            self.dismiss(None)


class ExportImportScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Export / Import Workspace", id="modal-agent-label")
            yield Input(placeholder="path to export/import", id="export-import-path")
            with Horizontal():
                yield Button("Export", id="export-do", classes="modal-btn")
                yield Button("Import", id="import-do", classes="modal-btn")
                yield Button("Close", id="export-import-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "export-do":
            try:
                path = self.query_one("#export-import-path", Input).value.strip()
            except Exception:
                path = ""
            self.dismiss(("export_config", path))
        elif btn == "import-do":
            try:
                path = self.query_one("#export-import-path", Input).value.strip()
            except Exception:
                path = ""
            self.dismiss(("import_config", path))
        else:
            self.dismiss(None)


class EnvScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Environment Variables (CAI_*)", id="modal-agent-label")
            yield ListView(id="env-list")
            yield Input(placeholder="VAR_NAME (no CAI_ prefix)", id="env-name")
            yield Input(placeholder="value", id="env-value")
            with Horizontal():
                yield Button("Set", id="env-set", classes="modal-btn")
                yield Button("Unset", id="env-unset", classes="modal-btn modal-btn--cancel")
                yield Button("Close", id="env-cancel", classes="modal-btn modal-btn--cancel")

    async def on_mount(self) -> None:
        try:
            lv = self.query_one("#env-list", ListView)
            for k in sorted([k for k in os.environ if k.startswith("CAI_")]):
                await lv.mount(ListItem(Label(f"{k}={os.environ.get(k)}")))
        except Exception:
            pass

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "env-set":
            try:
                name = self.query_one("#env-name", Input).value.strip()
                val = self.query_one("#env-value", Input).value
            except Exception:
                name = ""
                val = ""
            if not name:
                self.dismiss(None)
                return
            self.dismiss(("set_env", f"CAI_{name}", val))
        elif btn == "env-unset":
            try:
                name = self.query_one("#env-name", Input).value.strip()
            except Exception:
                name = ""
            if not name:
                self.dismiss(None)
                return
            self.dismiss(("unset_env", f"CAI_{name}"))
        else:
            self.dismiss(None)


class SessionRecordingScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        cur = os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() == "true"
        status = "disabled" if cur else "enabled"
        with Vertical(id="modal-dialog"):
            yield Static(f"Session recording is currently: [bold]{status}[/bold]", id="modal-agent-label")
            with Horizontal():
                yield Button("Toggle", id="session-toggle", classes="modal-btn")
                yield Button("Close", id="session-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "session-toggle":
            self.dismiss(("toggle_session_recording",))
        else:
            self.dismiss(None)


class ResetDefaultsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Reset all TUI config to defaults?", id="modal-agent-label")
            yield Button("Reset", id="reset-do", classes="modal-btn modal-btn--cancel")
            yield Button("Cancel", id="reset-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset-do":
            self.dismiss(("reset_defaults", True))
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
        display = _pretty_name(self._agent_name)
        return (
            f"[bold #00ff00]T{self._term_id}[/bold #00ff00]"
            f"[#004400] | [/#004400]"
            f"[#00cc00]{display}[/#00cc00]"
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
            f"T{self._term_id} ready — {_pretty_name(self._agent_name)}",
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
                    with TabPane("Sessions", id="tab-sessions"):
                        with Vertical(id="sessions-pane"):
                            with ScrollableContainer(id="sessions-scroll"):
                                pass  # populated in on_mount
                            yield Static("", id="session-preview")
                            with Horizontal(id="sessions-controls"):
                                yield Button("Refresh", id="sessions-refresh", classes="team-btn")
                                yield Button("Load Selected", id="sessions-load", classes="agent-btn")
                                yield Button("Resume Selected", id="sessions-resume", classes="agent-btn")
                                yield Button("Export Selected", id="sessions-export", classes="agent-btn")
                                yield Button("Rename Selected", id="sessions-rename", classes="agent-btn")
                                yield Button("Delete Selected", id="sessions-delete", classes="modal-btn modal-btn--cancel")
                    with TabPane("Config", id="tab-config"):
                        with Vertical(id="config-pane"):
                            yield Button("Providers", id="config-providers", classes="menu-btn")
                            yield Button("Model Params", id="config-model-params", classes="menu-btn")
                            yield Button("Memory / RAG", id="config-memory", classes="menu-btn")
                            yield Button("Export / Import", id="config-export-import", classes="menu-btn")
                            yield Button("Environment", id="config-env", classes="menu-btn")
                            yield Button("Toggle Session Recording", id="config-session-recording", classes="menu-btn")
                            yield Button("Reset Defaults", id="config-reset-defaults", classes="menu-btn")
            # ── Right: vertical stack of (up to 2) terminal rows ─────────
            with Vertical(id="terminals"):
                with Horizontal(id="term-row-top", classes="term-row"):
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
            label = _pretty_name(name)
            await scroll.mount(Button(label, id=f"agent-{name}", classes="agent-btn"))

        self._highlight_active_agent(self._agent_name)

        # Populate sessions list in the Sessions tab
        try:
            await self._populate_sessions_list()
        except Exception:
            pass

        # Spawn the first terminal panel (into top row)
        first = TerminalPanel(
            term_id=1,
            agent=self._agent,
            agent_name=self._agent_name,
            model_name=self._model_name,
        )
        await self.query_one("#term-row-top", Horizontal).mount(first)
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
        # Remove empty bottom row if all its panels were closed
        try:
            bottom = self.query_one("#term-row-bottom", Horizontal)
            if not list(bottom.query(TerminalPanel)):
                bottom.remove()
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
        panels = list(self.query(TerminalPanel))
        if len(panels) >= 4:
            return  # hard cap — modal already disables the button
        self._next_term_id += 1
        tid = self._next_term_id
        panel = TerminalPanel(
            term_id=tid,
            agent=agent,
            agent_name=agent_name,
            model_name=self._model_name,
        )
        # Panels 1 & 2 go in the top row; 3 & 4 go in the bottom row
        if tid <= 2:
            row = self.query_one("#term-row-top", Horizontal)
        else:
            # Create bottom row on first use
            try:
                row = self.query_one("#term-row-bottom", Horizontal)
            except Exception:
                row = Horizontal(id="term-row-bottom", classes="term-row")
                await self.query_one("#terminals", Vertical).mount(row)
        await row.mount(panel)
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
                self._open_agent_modal(agent_name)
            return

        if btn_id.startswith("team-"):
            self._activate_team(int(btn_id[len("team-"):]))
            return

        if btn_id == "new-team-btn":
            self._prompt_new_team()
            return
        # Sessions controls / per-item buttons
        if btn_id == "sessions-refresh":
            self._populate_sessions_list_worker()
            return

        selected = getattr(self, "_session_selected_idx", None)

        if btn_id == "sessions-load":
            # Load selected session, fallback to newest
            try:
                if selected is not None:
                    self._session_open_worker(int(selected))
                elif hasattr(self, '_session_files') and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_open_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-resume":
            try:
                if selected is not None:
                    self._session_resume_worker(int(selected))
                elif hasattr(self, '_session_files') and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_resume_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-export":
            try:
                if selected is not None:
                    self._session_export_worker(int(selected))
                elif hasattr(self, '_session_files') and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_export_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-rename":
            try:
                if selected is not None:
                    self._session_rename_worker(int(selected))
                elif hasattr(self, '_session_files') and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_rename_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-delete":
            try:
                if selected is not None:
                    self._session_delete_worker(int(selected))
                elif hasattr(self, '_session_files') and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_delete_worker(int(first))
            except Exception:
                pass
            return

        if btn_id.startswith("session-select-"):
            try:
                idx = int(btn_id.rsplit("-", 1)[-1])
                self._set_selected_session(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-open-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_open_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-export-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_export_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-resume-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_resume_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-rename-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_rename_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-delete-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_delete_worker(idx)
            except Exception:
                pass
            return

        # Config menu buttons
        if btn_id.startswith("config-"):
            try:
                action_key = btn_id[len("config-"):]
                self._open_config_modal(action_key)
            except Exception:
                pass
            return

    @work(exclusive=False)
    async def _open_agent_modal(self, agent_name: str) -> None:
        """Open the agent modal from a worker so push_screen_wait is valid."""
        active_label = f"T{self._active_term_id}"
        at_max = len(list(self.query(TerminalPanel))) >= 4
        result = await self.push_screen_wait(
            AgentModal(agent_name, active_label, at_max=at_max)
        )
        await self._handle_agent_modal(result)

    @work(exclusive=False)
    async def _open_config_modal(self, action_key: str) -> None:
        """Open a small modal to confirm opening the chosen Config section."""
        mapping = {
            "providers": "Providers",
            "model-params": "Model Params",
            "memory": "Memory / RAG",
            "export-import": "Export / Import",
            "env": "Environment",
            "session-recording": "Session Recording",
            "reset-defaults": "Reset Defaults",
        }
        display = mapping.get(action_key, action_key)
        result = await self.push_screen_wait(ConfigModal(action_key, display))
        # Do not await the handler because it will schedule a worker; call it synchronously.
        self._handle_config_action(result)

    def _handle_config_action(self, result) -> None:
        """Open the corresponding full-screen config editor for the chosen item."""
        if not result:
            return
        action = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else None
        if not action:
            return

        # Schedule the full-screen config worker (do not await the Worker object).
        try:
            self._open_config_screen(action)
        except Exception:
            pass

    @work(exclusive=False)
    async def _open_config_screen(self, action_key: str) -> None:
        """Push the full-screen config modal for `action_key` and handle its result."""
        from rich.text import Text as RichText

        cfg = _load_tui_config()

        screen_map = {
            "providers": ProvidersScreen(cfg),
            "model-params": ModelParamsScreen(cfg),
            "memory": MemoryInspectorScreen(cfg),
            "export-import": ExportImportScreen(cfg),
            "env": EnvScreen(cfg),
            "session-recording": SessionRecordingScreen(cfg),
            "reset-defaults": ResetDefaultsScreen(),
        }

        screen = screen_map.get(action_key)
        if not screen:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(RichText.from_markup(f"[red]Unknown config section: {action_key}[/red]"))
            except Exception:
                pass
            return

        result = await self.push_screen_wait(screen)
        if not result:
            return

        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
        except Exception:
            log = None

        try:
            key = result[0] if isinstance(result, (list, tuple)) else result

            if key == "save_provider":
                _, name, secret = result
                cfg = _load_tui_config()
                cfg.setdefault("providers", {})[name] = secret
                _save_tui_config(cfg)
                if log:
                    log.write(RichText.from_markup(f"[green]Saved provider {name}[/green]"))

            elif key == "test_provider":
                _, name = result
                if log:
                    log.write(RichText.from_markup(f"[dim]Provider test requested for {name} (not implemented)[/dim]"))

            elif key == "save_model_params":
                _, params = result
                cfg = _load_tui_config()
                cfg["model_params"] = params
                _save_tui_config(cfg)
                if log:
                    log.write(RichText.from_markup("[green]Saved model parameters[/green]"))

            elif key == "rebuild_memory":
                if log:
                    log.write(RichText.from_markup("[dim]Rebuild memory requested (not implemented)[/dim]"))

            elif key == "evict_memory":
                if log:
                    log.write(RichText.from_markup("[dim]Evict memory requested (not implemented)[/dim]"))

            elif key == "export_config":
                _, path = result
                dest = path or os.path.join(os.getcwd(), "tui_config_export.json")
                try:
                    cfg = _load_tui_config()
                    if os.path.isdir(dest):
                        dest = os.path.join(dest, "tui_config_export.json")
                    with open(dest, "w") as f:
                        json.dump(cfg, f, indent=2)
                    if log:
                        log.write(RichText.from_markup(f"[green]Exported config to {dest}[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Export failed: {e}[/red]"))

            elif key == "import_config":
                _, path = result
                try:
                    if path and os.path.exists(path):
                        with open(path, "r") as f:
                            imported = json.load(f)
                        cfg = _load_tui_config()
                        cfg.update(imported)
                        _save_tui_config(cfg)
                        if log:
                            log.write(RichText.from_markup(f"[green]Imported config from {path}[/green]"))
                    else:
                        if log:
                            log.write(RichText.from_markup(f"[red]Import path not found: {path}[/red]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Import failed: {e}[/red]"))

            elif key == "set_env":
                _, var, val = result
                try:
                    os.environ[var] = val
                    cfg = _load_tui_config()
                    cfg.setdefault("env", {})[var] = val
                    _save_tui_config(cfg)
                    if log:
                        log.write(RichText.from_markup(f"[green]Set {var}[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Set env failed: {e}[/red]"))

            elif key == "unset_env":
                _, var = result
                try:
                    os.environ.pop(var, None)
                    cfg = _load_tui_config()
                    if "env" in cfg and var in cfg["env"]:
                        cfg["env"].pop(var, None)
                    _save_tui_config(cfg)
                    if log:
                        log.write(RichText.from_markup(f"[green]Unset {var}[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Unset env failed: {e}[/red]"))

            elif key == "toggle_session_recording":
                cur = os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() == "true"
                if cur:
                    os.environ.pop("CAI_DISABLE_SESSION_RECORDING", None)
                    state = "enabled"
                else:
                    os.environ["CAI_DISABLE_SESSION_RECORDING"] = "true"
                    state = "disabled"
                if log:
                    log.write(RichText.from_markup(f"[green]Session recording {state}[/green]"))

            elif key == "reset_defaults":
                try:
                    if os.path.exists(CONFIG_FILE):
                        os.remove(CONFIG_FILE)
                    if log:
                        log.write(RichText.from_markup("[green]Reset TUI config to defaults (config file removed)[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Reset failed: {e}[/red]"))

        except Exception:
            if log:
                log.write(RichText.from_markup("[red]Error handling config action[/red]"))

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

    # ------------------------------------------------------------------ sessions

    async def _populate_sessions_list(self) -> None:
        """Discover JSONL session files in `logs/` and populate the Sessions list view."""
        try:
            scroll = self.query_one("#sessions-scroll", ScrollableContainer)
        except Exception:
            return
        # Ensure a ListView exists (create and mount if missing)
        try:
            lv = self.query_one("#sessions-list", ListView)
        except Exception:
            lv = ListView(id="sessions-list")
            await scroll.mount(lv)

        # Collect current selection path so we can preserve it if possible
        prev_selected_path = None
        try:
            prev_idx = getattr(self, "_session_selected_idx", None)
            if prev_idx is not None and hasattr(self, "_session_files"):
                prev_selected_path = self._session_files.get(prev_idx)
        except Exception:
            prev_selected_path = None

        # Discover files and their mtimes safely
        logs_dir = "logs"
        try:
            raw_names = [f for f in os.listdir(logs_dir) if f.endswith(".jsonl")]
        except Exception:
            raw_names = []

        files = []
        for fname in raw_names:
            path = os.path.join(logs_dir, fname)
            try:
                mtime_ts = os.path.getmtime(path)
            except Exception:
                mtime_ts = 0
            files.append((fname, mtime_ts))

        files_sorted = sorted(files, key=lambda t: t[1], reverse=True)

        # Clear existing items only after we successfully computed the new list
        for child in list(lv.query(ListItem)):
            try:
                child.remove()
            except Exception:
                pass

        self._session_files = {}
        for idx, (fname, mtime_ts) in enumerate(files_sorted):
            path = os.path.join(logs_dir, fname)
            try:
                mtime = datetime.fromtimestamp(mtime_ts).strftime("%Y-%m-%d %H:%M") if mtime_ts else "?"
            except Exception:
                mtime = "?"
            display = f"{fname}  ({mtime})"
            item = ListItem(Label(display), id=f"session-item-{idx}")
            await lv.mount(item)
            # Per-item action & selection buttons
            await item.mount(Button("Select", id=f"session-select-{idx}", classes="team-btn"))
            await item.mount(Button("Open", id=f"session-open-{idx}", classes="agent-btn"))
            await item.mount(Button("Resume", id=f"session-resume-{idx}", classes="agent-btn"))
            await item.mount(Button("Export", id=f"session-export-{idx}", classes="agent-btn"))
            await item.mount(Button("Rename", id=f"session-rename-{idx}", classes="team-btn"))
            await item.mount(Button("Delete", id=f"session-delete-{idx}", classes="modal-btn modal-btn--cancel"))

            self._session_files[idx] = path

        # Preserve previous selection if possible, otherwise pick the first
        if self._session_files:
            new_sel = None
            if prev_selected_path:
                for k, p in self._session_files.items():
                    if p == prev_selected_path:
                        new_sel = k
                        break
            if new_sel is None:
                new_sel = min(self._session_files.keys())
            try:
                self._session_selected_idx = new_sel
                self._set_selected_session(new_sel)
            except Exception:
                self._session_selected_idx = None
        else:
            self._session_selected_idx = None

    @work(exclusive=False)
    async def _populate_sessions_list_worker(self) -> None:
        await self._populate_sessions_list()

    @work(exclusive=False)
    async def _session_open_worker(self, idx: int) -> None:
        """Load messages from a session JSONL into the current active agent's history."""
        from rich.text import Text as RichText
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl

            messages = load_history_from_jsonl(path)

            # Merge into active agent similar to /load behavior
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER
            from cai.sdk.agents.models.openai_chatcompletions import ACTIVE_MODEL_INSTANCES, PERSISTENT_MESSAGE_HISTORIES
            from cai.repl.commands.parallel import ParallelCommand

            parallel_cmd = ParallelCommand()
            current_agent = AGENT_MANAGER.get_active_agent()
            current_agent_name = AGENT_MANAGER._active_agent_name
            if not current_agent_name:
                return

            current_history = AGENT_MANAGER.get_message_history(current_agent_name) or []
            original_signatures = set()
            for msg in current_history:
                try:
                    sig = parallel_cmd._get_message_signature(msg)
                    if sig:
                        original_signatures.add(sig)
                except Exception:
                    continue

            unique_messages = []
            for msg in messages:
                try:
                    sig = parallel_cmd._get_message_signature(msg)
                except Exception:
                    sig = None
                if sig and sig not in original_signatures:
                    unique_messages.append(msg)
                    original_signatures.add(sig)

            if not unique_messages:
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(RichText.from_markup(f"[dim]No new messages to add from {os.path.basename(path)}[/dim]"))
                except Exception:
                    pass
                return

            final_history = current_history + unique_messages

            # Find active model instance
            model_instance = None
            for (name, inst_id), model_ref in ACTIVE_MODEL_INSTANCES.items():
                try:
                    if name == current_agent_name:
                        model_instance = model_ref() if model_ref else None
                        break
                except Exception:
                    continue

            if model_instance:
                model_instance.message_history.clear()
                os.environ['CAI_CONTEXT_USAGE'] = '0.0'
                for msg in final_history:
                    try:
                        model_instance.add_to_message_history(msg)
                    except Exception:
                        pass
            else:
                PERSISTENT_MESSAGE_HISTORIES[current_agent_name] = final_history

            AGENT_MANAGER._message_history[current_agent_name] = final_history

            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(RichText.from_markup(f"[green]Loaded {len(unique_messages)} messages into {current_agent_name} from {os.path.basename(path)}[/green]"))
            except Exception:
                pass

        except Exception as e:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(RichText.from_markup(f"[red]Error loading session: {e}[/red]"))
            except Exception:
                pass

    @work(exclusive=False)
    async def _session_delete_worker(self, idx: int) -> None:
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            # Confirm deletion with modal
            result = await self.push_screen_wait(ConfirmModal(f"Delete {os.path.basename(path)}?"))
            if not result:
                return
            try:
                os.remove(path)
            except Exception:
                pass
            await self._populate_sessions_list()
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_rename_worker(self, idx: int) -> None:
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            default = os.path.basename(path)
            result = await self.push_screen_wait(PromptModal("Rename file to:", default))
            if not result:
                return
            dest = os.path.join("logs", result)
            if not dest.endswith(".jsonl"):
                dest += ".jsonl"
            try:
                os.rename(path, dest)
            except Exception:
                pass
            await self._populate_sessions_list()
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_resume_worker(self, idx: int) -> None:
        try:
            # Try to infer which agent this session belongs to, open a terminal for it,
            # then load the session into that new terminal (so resume maps to the right agent).
            path = self._session_files.get(idx)
            if not path:
                return

            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl
            messages = load_history_from_jsonl(path)

            # Infer agent key from messages
            best_key = self._infer_agent_from_session_messages(messages)

            # If we have a good match among available agents, open a terminal for it
            opened_terminal = False
            if best_key and best_key in self._available_agents:
                try:
                    agent_obj = self._available_agents.get(best_key)
                    await self._add_terminal(agent_obj, best_key)
                    opened_terminal = True
                except Exception:
                    opened_terminal = False

            # If no mapping found, fall back to opening a terminal for the active panel's agent
            if not opened_terminal:
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    agent = panel._agent
                    agent_name = panel._agent_name
                    await self._add_terminal(agent, agent_name)
                except Exception:
                    pass

            # Schedule the session load into the (newly) active terminal
            # Use the existing worker that merges messages into the active agent
            self._session_open_worker(idx)
        except Exception:
            pass

    def _set_selected_session(self, idx: Optional[int]) -> None:
        """Set the currently selected session index and update visuals."""
        try:
            self._session_selected_idx = idx
        except Exception:
            self._session_selected_idx = None

        # Update visual selection for list items
        try:
            for k in list(self._session_files.keys()):
                try:
                    li = self.query_one(f"#session-item-{k}", ListItem)
                    if k == idx:
                        li.add_class("-selected")
                    else:
                        li.remove_class("-selected")
                except Exception:
                    pass
        except Exception:
            pass

        # Update preview for the newly selected session
        try:
            self._update_session_preview(self._session_selected_idx)
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_export_worker(self, idx: int) -> None:
        """Export a session file to an arbitrary location chosen by the user."""
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            default = os.path.basename(path)
            result = await self.push_screen_wait(PromptModal("Export to (path or directory):", default))
            if not result:
                return
            dest = os.path.expanduser(result)
            import shutil
            if os.path.isdir(dest):
                dest_path = os.path.join(dest, os.path.basename(path))
            else:
                parent = os.path.dirname(dest)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                dest_path = dest
            shutil.copy2(path, dest_path)
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(RichText.from_markup(f"[green]Exported {os.path.basename(path)} to {dest_path}[/green]"))
            except Exception:
                pass
        except Exception as e:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(RichText.from_markup(f"[red]Export failed: {e}[/red]"))
            except Exception:
                pass

    def _update_session_preview(self, idx: Optional[int]) -> None:
        """Update the `#session-preview` Static with token/cost summary and a snippet."""
        try:
            preview = self.query_one("#session-preview", Static)
        except Exception:
            return

        if idx is None:
            try:
                preview.update("")
            except Exception:
                pass
            return

        path = self._session_files.get(idx)
        if not path or not os.path.exists(path):
            try:
                preview.update("[dim]No preview available")
            except Exception:
                pass
            return

        try:
            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl, get_token_stats
            messages = load_history_from_jsonl(path)
        except Exception:
            messages = []

        # Try to get token/cost stats
        try:
            model_name, prompt_t, completion_t, total_cost, active, idle = get_token_stats(path)
            stats = f"Model: {model_name or 'unknown'} · prompt:{prompt_t} completion:{completion_t} cost:{total_cost}"
        except Exception:
            stats = "Model: ? · prompt:? completion:? cost:?"

        # Build snippet: last 6 non-system messages
        snippet_lines = []
        try:
            filtered = [m for m in messages if isinstance(m, dict) and m.get('role') != 'system']
            for m in filtered[-6:]:
                role = m.get('role', '')
                content = (m.get('content') or '')
                if not content:
                    # handle nested message structures
                    content = str(m)
                # single-line snippet
                line = content.strip().splitlines()[0][:160]
                snippet_lines.append(f"{role}: {line}")
        except Exception:
            snippet_lines = []

        snippet = "\n".join(snippet_lines) or "(no preview)"

        body = f"[bold]{os.path.basename(path)}[/bold]\n{stats}\n\n{snippet}"
        try:
            preview.update(RichText.from_markup(body))
        except Exception:
            try:
                preview.update(body)
            except Exception:
                pass

    def _infer_agent_from_session_messages(self, messages: list) -> Optional[str]:
        """Try to infer the best-matching available agent key from session messages.

        Heuristic:
        - Collect candidate agent identifiers from message fields: `agent_name`, `name`, `sender`.
        - Normalize and score available agents by matching candidate identifiers against
          agent keys and display names (`agent.name` or `_pretty_name(key)`).
        - Return the agent key with the highest score, or None if no confident match.
        """
        try:
            from collections import Counter
        except Exception:
            Counter = None

        if not messages:
            return None

        # Collect candidate identifiers from messages
        candidates = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            for k in ("agent_name", "name", "sender"):
                v = msg.get(k)
                if v:
                    # Remove instance suffixes like ' #1'
                    try:
                        v2 = v.split(" #")[0]
                    except Exception:
                        v2 = v
                    candidates.append(str(v2))

        if not candidates:
            return None

        counts = Counter(candidates) if Counter is not None else {}

        # Score each available agent
        best_key = None
        best_score = 0

        for key, agent in self._available_agents.items():
            # Build match variants
            display = getattr(agent, "name", None) or _pretty_name(key)
            variants = {key, key.replace("_agent", "").replace("_pattern", "").replace("_swarm", "")}
            variants.add(display)
            variants.add(display.replace(" ", ""))

            score = 0
            for cand, cnt in (counts.items() if counts else []):
                cand_n = "".join([c for c in cand.lower() if c.isalnum()])
                for v in variants:
                    try:
                        v_n = "".join([c for c in str(v).lower() if c.isalnum()])
                    except Exception:
                        v_n = ""
                    if not v_n or not cand_n:
                        continue
                    if cand_n == v_n:
                        score += 10 * cnt
                    elif cand_n in v_n or v_n in cand_n:
                        score += 5 * cnt
                    else:
                        # partial word overlap
                        if any(part in v_n for part in cand_n.split() if len(part) > 3):
                            score += 2 * cnt

            if score > best_score:
                best_score = score
                best_key = key

        # Require some minimal confidence to accept match
        if best_score >= 5:
            return best_key
        return None

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

