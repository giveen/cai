"""Lightweight CAI Textual App — glue layer for components and controller.

This file intentionally keeps the application small: it composes the
`Header` and minimal containers, instantiates the `TuiController`, and
exposes a small compatibility shim used by components (e.g. screenshot
notifications and simple broadcasting helpers).
"""

# ruff: noqa

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Tuple, cast
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import (
    Vertical,
    Horizontal,
    ScrollableContainer,
)
import textual.containers as _containers
from textual import events, on, work

# TabbedContent/TabPane live in textual.widgets in modern Textual.
try:
    from textual.widgets import TabbedContent, TabPane  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - fallback for older Textual versions
    try:
        TabbedContent = _containers.TabbedContent  # type: ignore[attr-defined]
        TabPane = _containers.TabPane  # type: ignore[attr-defined]
    except Exception:
        TabbedContent = cast(Any, object)
        TabPane = cast(Any, object)
from textual.binding import Binding
from textual.widgets import (
    Static,
    Button,
    Input,
    TextArea,
    ProgressBar,
    Sparkline,
    Footer,
    ListView,
    ListItem,
)
from textual.widget import Widget

from cai.tui.components.header import _pretty_name, _BANNER_LINES
from cai.tui.components.workspace_hud import WorkspaceHUD
from cai.tui.components.memory_hud import MemoryHUD


logger = logging.getLogger(__name__)

# Small on-disk config used by the TUI config screens (kept here for
# backward compatibility with a few helper screens).
CONFIG_FILE = os.path.join(os.getcwd(), "tui_config.json")

# Default on-disk files and rotation limits used by various TUI helpers.
# Tests and scripts may overwrite these on a per-run basis.
TOOL_CALLS_FILE = os.path.join(os.getcwd(), "tui_tool_calls.log")
TOOL_CALLS_MAX_BYTES = 1024 * 1024
TOOL_CALLS_MAX_BACKUPS = 5

TELEMETRY_FILE = os.path.join(os.getcwd(), "tui_telemetry.log")
TELEMETRY_MAX_BYTES = 1024 * 1024
TELEMETRY_MAX_BACKUPS = 5

CONTEXT_SNAPSHOTS_FILE = os.path.join(os.getcwd(), "tui_context_snapshots.jsonl")
CONTEXT_SNAPSHOTS_MAX_BYTES = 1024 * 1024
CONTEXT_SNAPSHOTS_MAX_BACKUPS = 5

# Placeholder used by the ConfigOverview screen — populated later as needed.
# Add core context / compaction variables so they appear in the TUI /config table.
CONFIG_VARIABLES: list[dict] = [
    {
        "name": "CAI_AUTO_COMPACT",
        "default": "false",
        "description": "Enable background conversation history compaction.",
        "category": "memory",
    },
    {
        "name": "CAI_CTX_LIMIT",
        "default": 393216,
        "description": "Total context token limit for the local 5090 model.",
        "category": "memory",
    },
    {
        "name": "CAI_AUTO_COMPACT_THRESHOLD",
        "default": 0.9,
        "description": "Threshold (fraction or count) to trigger auto-compaction.",
        "category": "memory",
    },
    {
        "name": "CAI_CONTEXT_USAGE",
        "default": 0.0,
        "description": "Fraction of context allowed for memory budgeting (0.0-1.0).",
        "category": "memory",
    },
]


def _load_tui_config() -> dict:
    # Load persisted JSON config (best-effort) then overlay env and cai.config
    cfg: dict = {}
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}

    # Start from any persisted env mapping then prefer real ENV values
    env_map = dict(cfg.get("env", {}) or {})

    # Helpful defaults if not set in ENV or persisted config
    defaults = {
        "CAI_AUTO_COMPACT": "false",
        "CAI_CTX_LIMIT": "393216",
        "CAI_AUTO_COMPACT_THRESHOLD": "0.9",
        "CAI_CONTEXT_USAGE": "0.0",
    }

    # Try to pull canonical values from cai.config for CTX and threshold
    try:
        import cai.config as _cconf

        # Prefer explicit env if set; else use values derived from cai.config.
        if os.environ.get("CAI_CTX_LIMIT", "") != "":
            env_map["CAI_CTX_LIMIT"] = os.environ.get("CAI_CTX_LIMIT")
        else:
            try:
                env_map["CAI_CTX_LIMIT"] = str(getattr(_cconf, "CAI_CTX_LIMIT", int(defaults["CAI_CTX_LIMIT"])))
            except Exception:
                env_map["CAI_CTX_LIMIT"] = defaults["CAI_CTX_LIMIT"]

        if os.environ.get("CAI_AUTO_COMPACT_THRESHOLD", "") != "":
            env_map["CAI_AUTO_COMPACT_THRESHOLD"] = os.environ.get("CAI_AUTO_COMPACT_THRESHOLD")
        else:
            try:
                thr_tokens = getattr(_cconf, "CAI_AUTO_COMPACT_THRESHOLD", None)
                ctx = getattr(_cconf, "CAI_CTX_LIMIT", int(defaults["CAI_CTX_LIMIT"]))
                if thr_tokens is not None and float(ctx) > 0:
                    frac = float(thr_tokens) / float(ctx)
                    env_map["CAI_AUTO_COMPACT_THRESHOLD"] = str(frac)
                else:
                    env_map["CAI_AUTO_COMPACT_THRESHOLD"] = defaults["CAI_AUTO_COMPACT_THRESHOLD"]
            except Exception:
                env_map["CAI_AUTO_COMPACT_THRESHOLD"] = defaults["CAI_AUTO_COMPACT_THRESHOLD"]
    except Exception:
        # No cai.config available; fall back to environment or hard defaults
        pass

    # Ensure simple env-driven keys are captured (respect real OS env when present)
    for k, d in defaults.items():
        if os.environ.get(k, "") != "":
            env_map[k] = os.environ.get(k)
        else:
            env_map.setdefault(k, env_map.get(k, d))

    cfg["env"] = env_map
    return cfg


def _save_tui_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


class CaiHeader(Widget):
    """Single-line status bar: tab labels on the left, agent/model on the right."""

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
        with Horizontal(id="header-left"):
            yield Static(
                "[bold #00ff00]T1[/bold #00ff00][#004400] | [/#004400][#00ff00]Terminal[/#00ff00]",
                id="header-left-text",
            )
            with Horizontal(id="header-nav"):
                yield Button("Terminal", id="top-nav-terminal", classes="top-nav-btn")
                yield Button("Agents", id="top-nav-agents", classes="top-nav-btn")
                yield Button("Queue", id="top-nav-queue", classes="top-nav-btn")
                yield Button("Sessions", id="top-nav-sessions", classes="top-nav-btn")
                yield Button("Config", id="top-nav-config", classes="top-nav-btn")
                yield Button("Tools", id="top-nav-tools", classes="top-nav-btn")
                yield Button("Stats", id="top-nav-metrics", classes="top-nav-btn")
                yield Button("Menu", id="header-menu")
        yield Static(
            f"[#00cc00]{self._agent_name}[/#00cc00][#004400] ▼ [/#004400]"
            f"[#00cc00]{self._model}[/#00cc00][#004400] ▼ [/#004400]"
            f"[#006600]{self._ctx}[/#006600][#004400] ▼  [/#004400]"
            "[bold #00ff00]●[/bold #00ff00]",
            id="header-right",
        )


__all__ = ["run_tui", "CAIApp", "CONFIG_FILE", "_pretty_name", "_BANNER_LINES"]

# Legacy monolith removed — see src/cai/tui/components/ for widgets.


# ---------------------------------------------------------------------------
# Preset team compositions (label, list of agent-type strings)
# ---------------------------------------------------------------------------
# Team presets — defined in cai.tui.teams, imported for backward compat.
from cai.tui.teams import TEAM_PRESETS, TEAM_PLAYBOOK_HINTS

# ---------------------------------------------------------------------------
# Modals and config screens — extracted to sub-modules for maintainability.
# All names remain importable from app_impl for backward compatibility.
# ---------------------------------------------------------------------------
from cai.tui.screens.common import (
    AgentModal,
    PromptModal,
    ConfirmModal,
    ConfigModal,
    ContextUsageModal,
)
from cai.tui.screens.command_palette import CommandPaletteModal
from cai.tui.screens.config import (
    ProvidersScreen,
    ModelParamsScreen,
    MemoryInspectorScreen,
    ExportImportScreen,
    EnvScreen,
    SessionRecordingScreen,
    ResetDefaultsScreen,
    ConfigOverviewScreen,
)
from cai.tui.components.browser import BrowserPreview
from cai.tui.components.terminal import TerminalPanel
from cai.tui.components.terminal_grid import TerminalGrid
from cai.tui.components.persistence_manager import PersistenceManager
from cai.tui.components.sidebar import ConfigTab, SessionsTab, ToolsTab
from cai.tui.telemetry import TelemetryMixin
from cai.tui.layout import ResponsiveMixin
from cai.tui.tools_mixin import ToolsMixin
from cai.tui.queue_mixin import QueueMixin
from cai.tui.sessions_mixin import SessionsMixin
from cai.tui.controller import TuiController


# ---------------------------------------------------------------------------
# Custom header widget
# ---------------------------------------------------------------------------
class CAIApp(TelemetryMixin, ResponsiveMixin, ToolsMixin, QueueMixin, SessionsMixin, App):
    """CAI Textual Application — full terminal interface."""

    CSS_PATH = str(Path(__file__).parent / "styles" / "matrix.tcss")
    TITLE = "CAI TUI"

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Palette", show=True),
        Binding("ctrl+n", "new_agent", "New Agent", show=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar", show=True),
        Binding("f11", "maximize_terminal", "Maximize", show=True),
        Binding("ctrl+l", "clear_active", "Clear", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    # Class-level type annotations for static-analysis tools.
    _session_files: dict[int, Any] | None = None
    _tool_registry: dict[str, Any]
    _selected_tool_id: str | None = None
    _session_action_containers: Any | None = None
    _screenshot_path: str | None = None

    def __init__(
        self,
        agent: Any = None,
        initial_prompt: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._agent = agent
        self._agent_name: str = (
            getattr(agent, "name", "one_tool_agent") if agent else "one_tool_agent"
        )
        self._model_name: str = os.getenv("CAI_MODEL", "alias1")
        self._initial_prompt = initial_prompt
        self._sidebar_visible = True
        self._available_agents: dict = {}
        self._active_team: int | None = None
        self._queue_items: list[dict] = []
        self._queue_selected_idx: int | None = None
        self._queue_running: bool = False
        self._queue_broadcast_mode: bool = False
        self._tool_registry = {}
        self._tool_call_history: list[dict] = []
        self._selected_tool_id: str | None = None
        self._selected_tool_call_idx: int | None = None
        self._tool_button_id_to_tool_id: dict[str, str] = {}
        self._tool_tool_id_to_button_id: dict[str, str] = {}
        self._tool_calls_file: str = TOOL_CALLS_FILE
        self._tool_calls_max_bytes: int = TOOL_CALLS_MAX_BYTES
        self._tool_calls_max_backups: int = TOOL_CALLS_MAX_BACKUPS
        self._inject_mode: str = "input"
        self._telemetry_file: str = TELEMETRY_FILE
        self._telemetry_max_bytes: int = TELEMETRY_MAX_BYTES
        self._telemetry_max_backups: int = TELEMETRY_MAX_BACKUPS
        self._context_snapshots_file: str = CONTEXT_SNAPSHOTS_FILE
        self._context_snapshots_max_bytes: int = CONTEXT_SNAPSHOTS_MAX_BYTES
        self._context_snapshots_max_backups: int = CONTEXT_SNAPSHOTS_MAX_BACKUPS
        self._telemetry_pending_runs: dict[int, dict] = {}
        self._telemetry_pending_tool_calls: dict[str, dict] = {}
        self._telemetry_stats_by_term: dict[int, dict] = {}
        self._context_snapshot_by_term: dict[int, dict] = (
            self._load_context_snapshots_latest_by_term()
        )
        self._stats_started_ts: float = time.time()
        self._price_limit_warned: bool = False
        self._price_limit_paused: bool = False
        self._responsive_mode: str = "medium"
        self._responsive_label_cache: dict[str, str] = {}
        self._command_palette_recent: list[str] = []
        self._next_term_id = 1
        self._active_term_id = 1
        self._screenshot_path: str | None = None
        self._cpu_history: list[float] = []
        self._ctx_history: list[float] = []
        self._active_progress_tools: dict[str, str] = {}  # call_id → tool name
        self.controller = TuiController(self)
        # Start background skill miner and crystallizer (best-effort)
        try:
            from cai.memory.skills import start_skill_miner

            try:
                start_skill_miner()
            except Exception:
                pass
        except Exception:
            pass

        try:
            from cai.skills.crystallizer import start_crystallizer

            try:
                start_crystallizer()
            except Exception:
                pass
        except Exception:
            pass
        # Swarm/shared-credential state: team_idx -> set(agent_names)
        self._swarm_shared_flags: dict[int, set[str]] = {}

    def compose(self) -> ComposeResult:
        yield from self.compose_main()

    # Telemetry/metrics methods → cai.tui.telemetry.TelemetryMixin
    def _load_inject_mode_pref(self) -> str:
        """Load persisted inject mode from TUI config."""
        try:
            cfg = _load_tui_config()
            mode = str(cfg.get("tools", {}).get("inject_mode", "input")).strip().lower()
            if mode in ("input", "command"):
                return mode
        except Exception:
            pass
        return "input"

    def _persist_inject_mode_pref(self) -> None:
        """Persist current inject mode to TUI config."""
        try:
            cfg = _load_tui_config()
            cfg.setdefault("tools", {})["inject_mode"] = self._inject_mode
            _save_tui_config(cfg)
        except Exception:
            pass

    # Responsive layout methods → cai.tui.layout.ResponsiveMixin
    def compose_main(self) -> ComposeResult:
        yield CaiHeader(
            agent_name=self._agent_name,
            model=self._model_name,
        )
        with Vertical(id="body"):
            with TabbedContent(id="sidebar-tabs"):
                with TabPane("Terminal", id="tab-terminal"):
                    yield TerminalGrid(id="terminal-grid")
                    yield BrowserPreview(id="browser-preview")
                with TabPane("Agents", id="tab-agents"):
                    with Horizontal(id="agents-body"):
                        with Vertical(id="agents-left"):
                            with ScrollableContainer(id="agents-scroll"):
                                pass  # populated in on_mount
                            with Vertical(id="teams-section"):
                                yield Static("Teams", id="teams-label")
                                with ScrollableContainer(id="teams-scroll"):
                                    for i, (label, _) in enumerate(TEAM_PRESETS):
                                        yield Button(
                                            f"#{i + 1}: {label}",
                                            id=f"team-{i}",
                                            classes="team-btn",
                                        )
                                yield Static(
                                    "Select a team to see strategy hints.", id="team-playbook-preview"
                                )
                                yield Button("+ Create Team", id="new-team-btn")
                        with Vertical(id="agents-right"):
                            yield Static("Agent Intelligence Brief", id="agent-intel-header")
                            yield Static("Hover or focus an agent to see details.", id="agent-intel-brief")
                            # Active Swarm overlay: small connection map + sync status
                            yield Static("No active swarm.", id="active-swarm-overlay")
                            yield PersistenceManager(id="persistence-manager")
                            yield WorkspaceHUD(id="workspace-hud")
                            yield MemoryHUD(id="memory-hud")
                with TabPane("Queue", id="tab-queue"):
                    with Vertical(id="queue-pane"):
                        yield ListView(id="queue-list")
                        yield Static("Queue: 0 pending · broadcast OFF", id="queue-status")
                        with Horizontal(id="queue-actions"):
                            yield Button("Run Queue", id="queue-run", classes="agent-btn")
                            yield Button("Delete Selected", id="queue-delete", classes="team-btn")
                            yield Button(
                                "Clear All", id="queue-clear", classes="modal-btn modal-btn--cancel"
                            )
                            yield Button(
                                "Broadcast: OFF", id="queue-broadcast-mode", classes="team-btn"
                            )
                        with Horizontal(id="queue-input-row"):
                            yield Static("+", id="queue-prefix")
                            yield Input(
                                placeholder="Add task / command… (append ' all' to broadcast)",
                                id="queue-input",
                            )
                            yield Button("Add", id="queue-add", classes="team-btn")
                with TabPane("Sessions", id="tab-sessions"):
                    with Vertical(id="sessions-pane"):
                        with ScrollableContainer(id="sessions-scroll"):
                            pass  # populated in on_mount
                        yield Static("", id="session-preview")
                        with Horizontal(id="sessions-controls"):
                            yield Button("Refresh", id="sessions-refresh", classes="team-btn")
                            yield Button("Load Selected", id="sessions-load", classes="agent-btn")
                            yield Button(
                                "Resume Selected", id="sessions-resume", classes="agent-btn"
                            )
                            yield Button(
                                "Export Selected", id="sessions-export", classes="agent-btn"
                            )
                            yield Button(
                                "Rename Selected", id="sessions-rename", classes="agent-btn"
                            )
                            yield Button(
                                "Delete Selected",
                                id="sessions-delete",
                                classes="modal-btn modal-btn--cancel",
                            )
                with TabPane("Config", id="tab-config"):
                    yield ConfigTab(id="config-tab-widget")
                with TabPane("Tools", id="tab-tools"):
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
                with TabPane("Stats", id="tab-metrics"):
                    with Vertical(id="metrics-pane"):
                        with Vertical(id="sparkline-section"):
                            yield Static(
                                "CPU Usage (%)", id="sparkline-cpu-label", classes="sparkline-label"
                            )
                            yield Sparkline(
                                [],
                                id="sparkline-cpu",
                                summary_function=max,
                            )
                            yield Static(
                                "Context Window (%)",
                                id="sparkline-ctx-label",
                                classes="sparkline-label",
                            )
                            yield Sparkline(
                                [],
                                id="sparkline-ctx",
                                summary_function=max,
                            )
                        yield Static("", id="metrics-summary")
                        with ScrollableContainer(id="metrics-events-scroll"):
                            yield Static("", id="metrics-events")
                        with Horizontal(id="metrics-actions"):
                            yield Button("Refresh", id="metrics-refresh", classes="team-btn")
                            yield Button("Context", id="metrics-context", classes="agent-btn")
        yield ProgressBar(total=100, show_eta=False, id="tool-progress-bar")
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
        # Clear any existing children and build a categorized grid HUD
        try:
            for child in list(scroll.children):
                try:
                    child.remove()
                except Exception:
                    pass
        except Exception:
            pass
        # Build a categorized grid HUD in the agents scroll area
        def _categorize_agent(aname: str, aobj: object) -> str:
            txt = f"{aname} {(getattr(aobj, 'description', '') or '')} {(getattr(aobj, 'name', '') or '')}".lower()
            if any(k in txt for k in ("red", "pentest", "exploit", "attack", "replay", "retester", "bug", "htb", "web_pentester")):
                return "red"
            if any(k in txt for k in ("blue", "dfir", "forensic", "defend", "monitor", "analyzer", "memory", "investig")):
                return "blue"
            if any(k in txt for k in ("intel", "recon", "threat", "osint", "enumerat", "traffic", "network")):
                return "intel"
            return "spec"

        cats = [
            ("red", "Offensive / Red"),
            ("blue", "Defensive / Blue"),
            ("intel", "Intelligence / Recon"),
            ("spec", "Specialized"),
        ]

        buckets: dict[str, list[str]] = {k: [] for k, _ in cats}
        for name, agent in sorted(self._available_agents.items()):
            cat = _categorize_agent(name, agent)
            buckets.setdefault(cat, []).append(name)

        cols = Horizontal(id="agents-columns")
        await scroll.mount(cols)
        for key, label in cats:
            col = Vertical(id=f"agents-col-{key}", classes="agents-col")
            await cols.mount(col)
            # Column header
            await col.mount(Static(f"{label}", classes="agent-col-label"))
            names = sorted(buckets.get(key, []))
            if not names:
                await col.mount(Static("(none)", classes="agent-col-empty"))
            for name in names:
                label = _pretty_name(name)
                # Keep legacy id format so app-level handlers still work
                await col.mount(Button(label, id=f"agent-{name}", classes="agent-btn agent-tile"))
    

        self._inject_mode = self._load_inject_mode_pref()
        self._tool_call_history = self._load_tool_call_history()
        self._tool_registry = self._build_tool_registry()
        await self._populate_tools_list()
        self._update_metrics_view()
        self._refresh_price_limit_state(emit_logs=False)
        self._sync_team_buttons_metadata()
        self._update_team_playbook_preview(None)
        self._sync_queue_broadcast_button()
        self._update_queue_view()

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
        await self.query_one("#terminal-grid", TerminalGrid).add_panel(first)
        self._set_active_terminal(1)
        self._switch_top_tab("tab-terminal")
        try:
            size = self.size
            self._apply_responsive_layout(int(size.width), int(size.height))
        except Exception:
            pass

        # Focus the first input
        try:
            self.query_one("#term-input-1", TextArea).focus()
        except Exception:
            pass

        # Wire the global screenshot notifier to the BrowserPreview widget
        try:
            from cai.util import set_screenshot_writer

            browser_preview = self.query_one("#browser-preview", BrowserPreview)

            def _writer(path: str, interactive_map: object | None = None) -> None:
                # Ensure the app handles the screenshot on the main thread via
                # a dedicated handler so the UI can react consistently.
                try:
                    self.call_from_thread(self.on_browser_screenshot, path, interactive_map)
                except Exception:
                    # Best-effort: fall back to direct writer
                    try:
                        browser_preview.set_screenshot(path, interactive_map)
                    except Exception:
                        pass

            set_screenshot_writer(_writer)
        except Exception:
            pass

        if self._initial_prompt:
            await first.dispatch(self._initial_prompt)

        # Start the background timer that feeds CPU/context sparklines (2s tick)
        self.set_interval(2.0, self._tick_sparklines)

    def on_enter(self, event: events.Enter) -> None:
        """Hover/focus handler: update intelligence brief when hovering agents."""
        node = getattr(event, "node", None)
        if not isinstance(node, Button):
            return
        bid = node.id or ""
        if not bid.startswith("agent-"):
            return
        agent_name = bid[len("agent-") :]
        try:
            self._render_agent_intel(agent_name)
        except Exception:
            pass

    def _render_agent_intel(self, agent_name: str) -> None:
        """Render brief info for the given agent into the right-hand pane."""
        try:
            intel = self.query_one("#agent-intel-brief", Static)
        except Exception:
            return
        agent = self._available_agents.get(agent_name)
        if not agent:
            intel.update("[dim]Unknown agent[/dim]")
            return
        try:
            title = getattr(agent, "name", agent_name)
            desc = (getattr(agent, "description", "") or "").strip()
            # Short description first line
            desc_line = desc.splitlines()[0] if desc else "No description."
            tools = getattr(agent, "tools", []) or []
            tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]
            tool_sample = ", ".join(tool_names[:6]) or "—"
            lines = [
                f"[bold #00ff00]{title}[/bold #00ff00]",
                "",
                f"{desc_line}",
                "",
                f"Tools: [#00cc00]{tool_sample}[/#00cc00]",
                "",
                "[dim #005500]Press Enter to open agent details[/dim #005500]",
            ]
            intel.update("\n".join(lines))
        except Exception:
            try:
                intel.update("[dim]Unable to render agent info[/dim]")
            except Exception:
                pass

    def _render_active_swarm(self) -> None:
        """Render the Active Swarm overlay showing a small connection map and Sync Status.

        Uses `self._active_team` and `TEAM_PRESETS` as the source of truth and
        `self._swarm_shared_flags` to show which agents have been marked as
        having shared credentials.
        """
        try:
            overlay = self.query_one("#active-swarm-overlay", Static)
        except Exception:
            return

        if self._active_team is None:
            try:
                overlay.update("[dim]No active swarm.[/dim]")
            except Exception:
                pass
            return

        try:
            label, composition = TEAM_PRESETS[self._active_team]
        except Exception:
            try:
                overlay.update("[dim]Invalid team[/dim]")
            except Exception:
                pass
            return

        # Ensure we have four placeholders for the 2x2 layout
        comp4 = list(composition[:4])
        while len(comp4) < 4:
            comp4.append("(empty)")

        # Simple ASCII connection map (2x2)
        try:
            d0 = _pretty_name(comp4[0])
            d1 = _pretty_name(comp4[1])
            d2 = _pretty_name(comp4[2])
            d3 = _pretty_name(comp4[3])
        except Exception:
            d0 = comp4[0]
            d1 = comp4[1]
            d2 = comp4[2]
            d3 = comp4[3]

        diagram = (
            f" [{d0}] ── [{d1}]\n"
            f"   │        │\n"
            f" [{d2}] ── [{d3}]\n"
        )

        flags = self._swarm_shared_flags.get(self._active_team, set())
        lines = [f"[bold]Active Swarm[/bold] #{self._active_team + 1}: {label}", "", diagram]
        for i, name in enumerate(comp4, start=1):
            shared = name in flags
            status = "[bold #00ff00]● Shared[/bold #00ff00]" if shared else "[dim]—[/dim]"
            try:
                lines.append(f"T{i}: [#00cc00]{_pretty_name(name)}[/#00cc00] {status}")
            except Exception:
                lines.append(f"T{i}: {name} {status}")

        try:
            overlay.update("\n".join(lines))
        except Exception:
            pass

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(int(event.size.width), int(event.size.height))

    def on_unmount(self) -> None:
        try:
            from cai.util import set_screenshot_writer

            set_screenshot_writer(None)
        except Exception:
            pass

    def on_browser_screenshot(self, path: str, interactive_map: list | None = None) -> None:
        """Handle a browser screenshot notification routed from the tool layer.

        This method runs on the main thread (it is invoked via
        `call_from_thread`) and updates the BrowserPreview widget directly.
        """
        try:
            browser_preview = self.query_one("#browser-preview", BrowserPreview)
            # Call the internal setter directly since we're already on the main thread.
            try:
                browser_preview._set_screenshot_path(path, interactive_map)
            except Exception:
                # Fall back to the public setter which is thread-safe.
                try:
                    browser_preview.set_screenshot(path, interactive_map)
                except Exception:
                    pass
        except Exception:
            pass

    # Tool registry/history methods → cai.tui.tools_mixin.ToolsMixin
    # ------------------------------------------------------------------ sparklines & progress

    def _tick_sparklines(self) -> None:
        """Sample CPU/context usage every 2 s and push data to sparkline widgets."""
        import re as _re

        # --- CPU usage ---
        try:
            import psutil  # type: ignore[import]

            cpu = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu = 0.0

        self._cpu_history.append(cpu)
        if len(self._cpu_history) > 60:
            self._cpu_history = self._cpu_history[-60:]

        # --- Context window utilisation ---
        total_input = sum(
            int(s.get("input_tokens", 0) or 0) for s in self._telemetry_stats_by_term.values()
        )
        try:
            from cai.config import CAI_CTX_LIMIT
        except Exception:
            CAI_CTX_LIMIT = int(os.getenv("CAI_CTX_LIMIT", "393216"))
        ctx_pct = min(100.0, float(total_input) / max(float(CAI_CTX_LIMIT), 1.0) * 100.0)
        self._ctx_history.append(ctx_pct)
        if len(self._ctx_history) > 60:
            self._ctx_history = self._ctx_history[-60:]

        # Push data into the Sparkline widgets (they live in the Stats tab)
        try:
            self.query_one("#sparkline-cpu", Sparkline).data = list(self._cpu_history)
        except Exception:
            pass
        try:
            self.query_one("#sparkline-ctx", Sparkline).data = list(self._ctx_history)
        except Exception:
            pass

        # Update progress bar visibilty based on active crawler/vault calls
        self._sync_tool_progress_bar()

    def _sync_tool_progress_bar(self) -> None:
        """Show the ProgressBar when a crawler/vault tool is active; hide it otherwise."""
        _PROGRESS_TOOLS = ("local_crawler", "ingest_vault", "ingest", "crawl")
        active = any(
            any(marker in str(v.get("tool_name", "")).lower() for marker in _PROGRESS_TOOLS)
            for v in self._telemetry_pending_tool_calls.values()
        )
        try:
            bar = self.query_one("#tool-progress-bar", ProgressBar)
            if active and not bar.display:
                bar.display = True
            elif not active and bar.display:
                # Leave the bar visible until _hide_tool_progress_bar fires
                pass
        except Exception:
            pass

    def _hide_tool_progress_bar(self) -> None:
        """Hide the tool progress bar and reset it (called after a short delay)."""
        try:
            if not self._active_progress_tools:
                bar = self.query_one("#tool-progress-bar", ProgressBar)
                bar.display = False
                bar.update(progress=0)
        except Exception:
            pass

    # ------------------------------------------------------------------ telemetry overrides

    def _telemetry_tool_called(
        self,
        term_id: int,
        agent_name: str,
        tool_name: str,
        call_id: str,
        args_preview: str,
    ) -> None:
        """Extend base telemetry to show the progress bar for crawler/vault tools."""
        super()._telemetry_tool_called(term_id, agent_name, tool_name, call_id, args_preview)
        _PROGRESS_TOOLS = ("local_crawler", "ingest_vault", "ingest", "crawl")
        if any(marker in tool_name.lower() for marker in _PROGRESS_TOOLS):
            self._active_progress_tools[call_id] = tool_name
            try:
                bar = self.query_one("#tool-progress-bar", ProgressBar)
                bar.display = True
                bar.update(progress=0)
            except Exception:
                pass

    def _telemetry_tool_output(
        self,
        term_id: int,
        agent_name: str,
        call_id: str,
        output_preview: str,
    ) -> None:
        """Extend base telemetry to parse progress from crawler/vault tool output."""
        super()._telemetry_tool_output(term_id, agent_name, call_id, output_preview)
        # --- Active Swarm: detect credential findings and mark shared status ---
        try:
            import re as _re

            if self._active_team is not None:
                try:
                    _, comp = TEAM_PRESETS[self._active_team]
                except Exception:
                    comp = []
                # Only consider the first four team slots (T1-T4)
                comp4 = comp[:4]
                if agent_name in comp4 and output_preview:
                    # Look for credential-like tokens in the preview
                    if _re.search(r"\b(credentials?|username|user|password|passwd|creds)\b", output_preview, _re.I):
                        s = self._swarm_shared_flags.setdefault(self._active_team, set())
                        for member in comp4:
                            if member != agent_name:
                                s.add(member)
                        try:
                            self._render_active_swarm()
                        except Exception:
                            pass
        except Exception:
            pass

        if call_id not in self._active_progress_tools:
            return
        self._active_progress_tools.pop(call_id, None)

        # Try to extract a progress percentage from the output text
        import re as _re

        pct: float | None = None
        m = _re.search(r"(\d+)\s*/\s*(\d+)", output_preview)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            if total > 0:
                pct = min(100.0, done / total * 100.0)
        if pct is None:
            m2 = _re.search(r"(\d+(?:\.\d+)?)\s*%", output_preview)
            if m2:
                pct = min(100.0, float(m2.group(1)))

        try:
            bar = self.query_one("#tool-progress-bar", ProgressBar)
            if pct is not None:
                bar.update(progress=pct)
            # Mark completion and schedule hide after a short grace period
            bar.update(progress=100)
            self.set_timer(1.5, self._hide_tool_progress_bar)
        except Exception:
            pass

    # ------------------------------------------------------------------ terminal panel messages

    def on_terminal_panel_activated(self, event: TerminalPanel.Activated) -> None:
        self._set_active_terminal(event.term_id)

    def on_terminal_panel_close_requested(self, event: TerminalPanel.CloseRequested) -> None:
        panels = list(self.query(TerminalPanel))
        if len(panels) <= 1:
            self.exit()
            return
        try:
            grid = self.query_one("#terminal-grid", TerminalGrid)
            self.call_later(grid.remove_panel, event.term_id)
        except Exception:
            try:
                self.query_one(f"#terminal-panel-{event.term_id}", TerminalPanel).remove()
            except Exception:
                pass
        remaining = [p for p in self.query(TerminalPanel) if p._term_id != event.term_id]
        if remaining:
            self._set_active_terminal(remaining[-1]._term_id)

    # ------------------------------------------------------------------ terminal management

    def _set_active_terminal(self, term_id: int) -> None:
        self._active_term_id = term_id
        for panel in self.query(TerminalPanel):
            if panel._term_id == term_id:
                panel.add_class("-active-panel")
                panel.remove_class("-inactive-panel")
            else:
                panel.remove_class("-active-panel")
                panel.add_class("-inactive-panel")
        # Keep .-focused-terminal in sync with the active selection.
        try:
            self.query_one("#terminal-grid", TerminalGrid).set_focused_panel(term_id)
        except Exception:
            pass
        try:
            size = self.size
            self._apply_terminal_visibility(
                self._responsive_mode_for_size(int(size.width), int(size.height))
            )
        except Exception:
            pass
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
        # Rebuild tool registry from the active terminal's agent and refresh UI.
        try:
            self._tool_registry = self._build_tool_registry()
            self._populate_tools_list_worker()
        except Exception:
            pass

    @work(exclusive=True)
    async def _populate_tools_list_worker(self) -> None:
        await self._populate_tools_list()

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
        grid = self.query_one("#terminal-grid", TerminalGrid)
        await grid.add_panel(panel)
        self._set_active_terminal(tid)
        try:
            size = self.size
            self._apply_responsive_layout(int(size.width), int(size.height))
        except Exception:
            pass
        try:
            self.query_one(f"#term-input-{tid}", TextArea).focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ button events
    @on(Button.Pressed, "#top-nav-terminal")
    def on_top_nav_terminal(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-terminal")

    @on(Button.Pressed, "#top-nav-agents")
    def on_top_nav_agents(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-agents")

    @on(Button.Pressed, "#top-nav-queue")
    def on_top_nav_queue(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-queue")

    @on(Button.Pressed, "#top-nav-sessions")
    def on_top_nav_sessions(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-sessions")

    @on(Button.Pressed, "#top-nav-config")
    def on_top_nav_config(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-config")

    @on(Button.Pressed, "#top-nav-tools")
    def on_top_nav_tools(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-tools")

    @on(Button.Pressed, "#top-nav-metrics")
    def on_top_nav_metrics(self, event: Button.Pressed) -> None:
        self._switch_top_tab("tab-metrics")

    @on(Button.Pressed, "#header-menu")
    def on_header_menu_pressed(self, event: Button.Pressed) -> None:
        self.action_command_palette()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""

        # Sidebar now emits Session/Tool messages; ignore raw session/tool button events here
        if (
            btn_id.startswith("session-")
            or btn_id.startswith("tool-")
            or btn_id.startswith("sessions-")
            or btn_id.startswith("tools-")
        ):
            return

        if btn_id.startswith("agent-"):
            agent_name = btn_id[len("agent-") :]
            if agent_name in self._available_agents:
                self._open_agent_modal(agent_name)
            return

        if btn_id.startswith("tool-select-"):
            tool_id = self._tool_button_id_to_tool_id.get(btn_id)
            if tool_id in self._tool_registry:
                self._selected_tool_id = tool_id
                self._highlight_active_tool(tool_id)
                self._update_tools_preview()
            return

        if btn_id.startswith("tool-call-"):
            try:
                idx = int(btn_id[len("tool-call-") :])
                if idx < 0 or idx >= len(self._tool_call_history):
                    return
                self._selected_tool_call_idx = idx
                self._selected_tool_id = self._tool_call_history[idx].get("tool_id")
                if self._selected_tool_id:
                    self._highlight_active_tool(self._selected_tool_id)
                self._update_tools_preview()
            except Exception:
                pass
            return

        if btn_id == "tools-run":
            self._run_selected_tool_worker()
            return

        if btn_id == "tools-inspect":
            self._update_tools_preview()
            self._log_to_active_terminal("[tool] Updated tool inspection view.")
            return

        if btn_id == "tools-replay":
            self._replay_selected_tool_call_worker()
            return

        if btn_id == "tools-inject":
            self._inject_selected_tool_output()
            return

        if btn_id == "tools-inject-mode":
            self._toggle_inject_mode()
            return

        if btn_id == "metrics-refresh":
            self._refresh_metrics_view_worker()
            return

        if btn_id == "metrics-context":
            self._open_context_usage_menu_worker()
            return

        if btn_id.startswith("team-"):
            self._activate_team(int(btn_id[len("team-") :]))
            return

        if btn_id == "queue-add":
            self._add_from_queue_input()
            return

        if btn_id == "queue-run":
            self._run_queue_worker()
            return

        if btn_id == "queue-delete":
            self._delete_selected_queue_item()
            return

        if btn_id == "queue-clear":
            self._clear_queue()
            return

        if btn_id == "queue-broadcast-mode":
            self._toggle_queue_broadcast_mode()
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
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_open_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-resume":
            try:
                if selected is not None:
                    self._session_resume_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_resume_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-export":
            try:
                if selected is not None:
                    self._session_export_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_export_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-rename":
            try:
                if selected is not None:
                    self._session_rename_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_rename_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-delete":
            try:
                if selected is not None:
                    self._session_delete_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_delete_worker(int(first))
            except Exception:
                pass
            return

        if btn_id.startswith("session-toggle-"):
            try:
                idx = int(btn_id.rsplit("-", 1)[-1])
            except Exception:
                return
            try:
                self._toggle_session_actions(idx)
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

        # Config menu buttons (only handle known sidebar config buttons)
        if btn_id in (
            "config-providers",
            "config-model-params",
            "config-memory",
            "config-export-import",
            "config-env",
            "config-reset-defaults",
        ):
            try:
                action_key = btn_id[len("config-") :]
                # Open the full config screen directly (avoid an extra confirm modal)
                try:
                    self._open_config_screen(action_key)
                except Exception:
                    # Fallback to the confirm modal if scheduling the worker fails
                    try:
                        self._open_config_modal(action_key)
                    except Exception:
                        pass
            except Exception:
                pass
            return

    @work(exclusive=False)
    async def _open_agent_modal(self, agent_name: str) -> None:
        """Open the agent modal from a worker so push_screen_wait is valid."""
        active_label = f"T{self._active_term_id}"
        at_max = len(list(self.query(TerminalPanel))) >= 4
        result = await self.push_screen_wait(AgentModal(agent_name, active_label, at_max=at_max))
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

        # Log for easier debugging when screens are opened
        try:
            self.log.info(f"_open_config_screen: {action_key}")
        except Exception:
            pass

        cfg = _load_tui_config()

        screen_map = {
            "providers": ProvidersScreen(cfg),
            "model-params": ModelParamsScreen(cfg),
            "memory": MemoryInspectorScreen(cfg),
            "export-import": ExportImportScreen(cfg),
            "env": EnvScreen(cfg),
            "session-recording": SessionRecordingScreen(cfg),
            "reset-defaults": ResetDefaultsScreen(),
            "full-config": None,
        }

        # Special-case: full-config overview requires a loop to allow edits
        if action_key == "full-config":
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
            except Exception:
                panel = None
                log = None
            # Interactive loop: show overview, handle edit/reset, and re-show until closed
            while True:
                screen = ConfigOverviewScreen(CONFIG_VARIABLES)
                result = await self.push_screen_wait(screen)
                if not result:
                    break
                try:
                    action = result[0]
                    if action == "edit":
                        idx = result[1]
                        if idx is None or idx < 0 or idx >= len(CONFIG_VARIABLES):
                            continue
                        var = CONFIG_VARIABLES[idx]
                        name = str(var.get("name") or "")
                        cur_cfg = _load_tui_config()
                        current = (
                            os.environ.get(name)
                            or cur_cfg.get("env", {}).get(name)
                            or var.get("default", "")
                        )
                        newval = await self.push_screen_wait(
                            PromptModal(f"Set value for {name} (empty to unset):", str(current))
                        )
                        if newval is None:
                            continue
                        # Apply change
                        try:
                            cfg2 = _load_tui_config()
                            if newval == "":
                                os.environ.pop(name, None)
                                if "env" in cfg2 and name in cfg2["env"]:
                                    cfg2["env"].pop(name, None)
                            else:
                                os.environ[name] = newval
                                cfg2.setdefault("env", {})[name] = newval
                            _save_tui_config(cfg2)
                            if log:
                                log.write(
                                    RichText.from_markup(f"[green]Set {name} = {newval}[/green]")
                                )
                        except Exception as e:
                            if log:
                                log.write(
                                    RichText.from_markup(f"[red]Failed to set {name}: {e}[/red]")
                                )
                        continue

                    elif action == "reset":
                        idx = result[1]
                        if idx is None or idx < 0 or idx >= len(CONFIG_VARIABLES):
                            continue
                        var = CONFIG_VARIABLES[idx]
                        name = str(var.get("name") or "")
                        default = var.get("default")
                        try:
                            cfg2 = _load_tui_config()
                            if default in (None, "Not set"):
                                os.environ.pop(name, None)
                                if "env" in cfg2 and name in cfg2["env"]:
                                    cfg2["env"].pop(name, None)
                            else:
                                os.environ[name] = str(default)
                                cfg2.setdefault("env", {})[name] = str(default)
                            _save_tui_config(cfg2)
                            if log:
                                log.write(
                                    RichText.from_markup(
                                        f"[green]Reset {name} to {default}[/green]"
                                    )
                                )
                        except Exception as e:
                            if log:
                                log.write(
                                    RichText.from_markup(f"[red]Failed to reset {name}: {e}[/red]")
                                )
                        continue
                    else:
                        # Unknown action – break
                        break
                except Exception:
                    break
            return

        screen = screen_map.get(action_key)
        if screen is None:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                    RichText.from_markup(f"[red]Unknown config section: {action_key}[/red]")
                )
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
                    log.write(
                        RichText.from_markup(
                            f"[dim]Provider test requested for {name} (not implemented)[/dim]"
                        )
                    )

            elif key == "save_model_params":
                _, params = result
                cfg = _load_tui_config()
                cfg["model_params"] = params
                _save_tui_config(cfg)
                if log:
                    log.write(RichText.from_markup("[green]Saved model parameters[/green]"))

            elif key == "rebuild_memory":
                if log:
                    log.write(
                        RichText.from_markup(
                            "[dim]Rebuild memory requested (not implemented)[/dim]"
                        )
                    )

            elif key == "evict_memory":
                if log:
                    log.write(
                        RichText.from_markup("[dim]Evict memory requested (not implemented)[/dim]")
                    )

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
                        with open(path) as f:
                            imported = json.load(f)
                        cfg = _load_tui_config()
                        cfg.update(imported)
                        _save_tui_config(cfg)
                        if log:
                            log.write(
                                RichText.from_markup(f"[green]Imported config from {path}[/green]")
                            )
                    else:
                        if log:
                            log.write(
                                RichText.from_markup(f"[red]Import path not found: {path}[/red]")
                            )
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
                        log.write(
                            RichText.from_markup(
                                "[green]Reset TUI config to defaults (config file removed)[/green]"
                            )
                        )
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
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
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

    # Queue/broadcast methods → cai.tui.queue_mixin.QueueMixin
    # ------------------------------------------------------------------ teams

    def _team_tooltip_text(self, idx: int, label: str, agent_types: list[str]) -> str:
        parts: list[str] = []
        for agent_name in (
            "redteam_agent",
            "blueteam_agent",
            "bug_bounter_agent",
            "retester_agent",
        ):
            count = agent_types.count(agent_name)
            if count > 0:
                parts.append(f"{count} {agent_name}")
        composition = f"#{idx + 1}: " + " + ".join(parts)
        lines = [composition]
        for i, name in enumerate(agent_types[:4], start=1):
            lines.append(f"T{i}: {name}")
        hint = self._team_playbook_hint(idx)
        if hint:
            lines.append(f"Best for: {hint}")
        return "\n".join(lines)

    def _team_playbook_hint(self, idx: int) -> str:
        if 0 <= idx < len(TEAM_PLAYBOOK_HINTS):
            return TEAM_PLAYBOOK_HINTS[idx]
        return ""

    def _update_team_playbook_preview(self, idx: int | None) -> None:
        try:
            preview = self.query_one("#team-playbook-preview", Static)
        except Exception:
            return

        if idx is None or idx < 0 or idx >= len(TEAM_PRESETS):
            preview.update("Select a team to see strategy hints.")
            try:
                # Clear overlay when no active team
                overlay = self.query_one("#active-swarm-overlay", Static)
                overlay.update("[dim]No active swarm.[/dim]")
            except Exception:
                pass
            return

        label, composition = TEAM_PRESETS[idx]
        hint = self._team_playbook_hint(idx)
        text = f"[bold]Team #{idx + 1}: {label}[/bold]\nT1-T4: {', '.join(composition[:4])}\n{hint}"
        try:
            preview.update(RichText.from_markup(text))
        except Exception:
            preview.update(text)
        # Reset swarm shared state for this team and update overlay
        try:
            self._swarm_shared_flags.setdefault(idx, set())
            self._render_active_swarm()
        except Exception:
            pass

    def _sync_team_buttons_metadata(self) -> None:
        for i, (label, agent_types) in enumerate(TEAM_PRESETS):
            try:
                btn = self.query_one(f"#team-{i}", Button)
                btn.label = f"#{i + 1}: {label}"
                btn.tooltip = self._team_tooltip_text(i, label, agent_types)
            except Exception:
                pass

    @work(exclusive=True)
    async def _activate_team_worker(self, idx: int) -> None:
        try:
            self.controller.activate_team(idx)
        except Exception:
            pass

    def _activate_team(self, idx: int) -> None:
        self._activate_team_worker(idx)

    def _prompt_new_team(self) -> None:
        try:
            inp = self.query_one("#queue-input", Input)
            inp.placeholder = "team: agent1 agent2 agent3…"
            inp.focus()
        except Exception:
            pass

    # Sessions methods → cai.tui.sessions_mixin.SessionsMixin
    # ------------------------------------------------------------------ agent highlight

    def _highlight_active_agent(self, name: str) -> None:
        for btn in self.query(".agent-btn"):
            btn.remove_class("-active-agent")
        try:
            self.query_one(f"#agent-{name}", Button).add_class("-active-agent")
        except Exception:
            pass

    def _render_config_table(self, width: int = 120) -> str:
        """Render the configuration variables as a table string using Rich."""
        import io

        from rich.console import Console
        from rich.table import Table

        cfg = _load_tui_config()

        table = Table(show_header=True, header_style="bold #00ff00")
        table.add_column("#", width=3)
        table.add_column("Variable", width=40)
        table.add_column("Value", width=20)
        table.add_column("Default", width=12)
        table.add_column("Description", width=60)

        for idx, v in enumerate(CONFIG_VARIABLES):
            name = str(v.get("name") or "")
            default = v.get("default", "")
            desc = v.get("description", "")
            raw = os.environ.get(name)
            if raw is None:
                raw = cfg.get("env", {}).get(name)
            if raw is None:
                raw = default

            # Format values for specific variables
            value_s = ""
            try:
                if name == "CAI_CTX_LIMIT":
                    try:
                        limit = int(float(raw))
                    except Exception:
                        limit = int(v.get("default", 393216))
                    used_raw = (
                        os.environ.get("CAI_CONTEXT_USAGE")
                        or cfg.get("env", {}).get("CAI_CONTEXT_USAGE")
                        or 0.0
                    )
                    try:
                        used_frac = float(used_raw)
                    except Exception:
                        used_frac = 0.0
                    pct = max(0.0, min(100.0, used_frac * 100.0))
                    # small ascii bar (20 chars)
                    filled = int(pct / 5)
                    bar = "█" * filled + "░" * (20 - filled)
                    value_s = f"{limit:,} {bar} {pct:.0f}%"

                elif name == "CAI_AUTO_COMPACT_THRESHOLD":
                    try:
                        f = float(raw)
                        if 0.0 < f <= 1.0:
                            pct = f * 100.0
                        else:
                            ctx_raw = (
                                os.environ.get("CAI_CTX_LIMIT")
                                or cfg.get("env", {}).get("CAI_CTX_LIMIT")
                                or v.get("default", 393216)
                            )
                            try:
                                ctx = float(ctx_raw)
                                pct = min(100.0, float(f) / max(1.0, ctx) * 100.0)
                            except Exception:
                                pct = float(v.get("default", 0.9)) * 100.0
                    except Exception:
                        pct = float(v.get("default", 0.9)) * 100.0
                    value_s = f"{pct:.0f}%"

                elif name == "CAI_CONTEXT_USAGE":
                    try:
                        used = float(raw)
                    except Exception:
                        used = float(v.get("default", 0.0))
                    value_s = f"{used * 100.0:.1f}%"

                elif name == "CAI_AUTO_COMPACT":
                    sval = str(raw).strip().lower()
                    if sval in {"1", "true", "yes", "on"}:
                        value_s = "true"
                    else:
                        value_s = "false"

                else:
                    value_s = str(raw)
            except Exception:
                value_s = str(raw)

            table.add_row(str(idx + 1), name, value_s, str(default), desc)

        buf = io.StringIO()
        console = Console(file=buf, width=width, color_system=None)
        console.print(table)
        return buf.getvalue()

    def _display_config_table(self) -> None:
        """Write the rendered config table into the active terminal's RichLog."""
        from rich.text import Text as RichText

        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
        except Exception:
            return

        try:
            table_width = 100
            try:
                table_width = max(60, min(160, int(self.size.width) - 6))
            except Exception:
                pass
            table_str = self._render_config_table(width=table_width)
            # Write the table as a multiline RichText so it preserves formatting
            log.write(RichText(table_str))
        except Exception:
            try:
                log.write(RichText("Failed to render config table"))
            except Exception:
                pass

    # ── Static template map for tool command suggestions ────────────────────
    _TOOL_TEMPLATES: dict[str, str] = {
        # Reconnaissance / Linux
        "generic_linux_command": "{command}",
        "execute_code":          "#!/bin/bash\n{command}",
        "ldap_search":           "{host} '{base_dn}' '{filter}'",
        "nmap":                  "nmap -T4 --top-ports 1000 {target}",
        "netcat":                "nc -zv {host} {port}",
        "netstat":               "netstat -tuln",
        "curl":                  "curl -sL -o /dev/null -w '%{http_code}' {url}",
        "wget":                  "wget -q -O - {url}",
        # SMB
        "smb_list_shares":       "//{host}",
        "smb_run_smbclient":     "//{host}/{share}",
        "smb_download_file":     "//{host}/{share} {remote_path} {local_dest}",
        # Filesystem
        "cat_file":              "{path}",
        "find_file":             "{directory} {filename_pattern}",
        "list_dir":              "{directory}",
        "pwd_command":           "",
        # Crypto helpers
        "strings_command":       "{binary_file}",
        "decode64":              "{base64_string}",
        "decode_hex_bytes":      "{hex_string}",
        # Blue-team
        "blue_team_safe_command": "{command}",
        # C2 / lateral movement
        "run_ssh_command_with_credentials": "{user}@{host} {password} {command}",
        "capture_remote_traffic":  "{host} {interface} {duration_s}",
        "remote_capture_session_tool": "{session_id}",
        "impacket_executor":     "{module} {target} {args}",
        "netexec_executor":      "smb {target} -u {username} -p {password}",
        "ligolo_executor":       "{command}",
        # Web
        "web_request_framework": "{url}",
        "js_surface_mapper":     "{url}",
        "duckduckgo_web_search": "{query}",
        "sqlmap":                "sqlmap -u {target_url} --level 3 --risk 2 --dbs",
        "cewl":                  "cewl {url} -d 2 -m 5 -o wordlist.txt",
        "cve_search_lookup":     "{cve_id}",
        "cve_search_product":    "{vendor} {product}",
        "cve_search_last":       "{n}",
        "cve_search_browse":     "{page}",
        "cve_search_db_info":    "",
        "github_poc_search":     "{cve_id_or_keyword}",
        "browser_navigate":      "{url}",
        "deep_crawl":            "{url}",
        "local_crawler":         "{directory_path}",
        # Knowledge / sessions
        "query_knowledge_base":  "{question}",
        "set_session_cookie":    "{name} {value} {url}",
        "get_pinned_session_cookie": "",
        "unpin_session_cookie":  "",
        # Execution / scripting
        "execute_python_code":   "print('hello world')",
        "scripting_tool":        "#!/bin/bash\necho 'Hello'",
        "execute_cli_command":   "{shell_command}",
        # Reasoning / memory
        "thought":               "{observation_text}",
        "think":                 "{problem_statement}",
        "write_key_findings":    "{findings_text}",
        "read_key_findings":     "",
        "query_memory":          "{query}",
        "add_to_memory_episodic":  "{content}",
        "add_to_memory_semantic":  "{content}",
        "get_rag_status":        "",
        # OSINT (conditional)
        "shodan_search":         "{query}",
        "shodan_host_info":      "{ip_address}",
        "make_google_search":    "{query}",
    }

    def _command_palette_commands(self) -> list[dict]:
        """Return all palette entries: app commands, tools, and agents."""
        cmds: list[dict] = []

        # ── App-level commands ────────────────────────────────────────────
        app_entries = [
            ("clear", "Clear terminal output",  "app", "Ctrl+L"),
            ("save",  "Save current session",   "app", ""),
            ("load",  "Load previous session",  "app", ""),
            ("export","Export conversation",     "app", ""),
            ("reset", "Reset agent context",    "app", ""),
            ("help",  "Show help information",  "app", ""),
        ]
        for cid, desc, ctype, shortcut in app_entries:
            cmds.append({
                "id":          cid,
                "name":        cid,
                "type":        ctype,
                "description": desc,
                "shortcut":    shortcut,
                "action":      "run",
                "payload":     cid,
                "category":    shortcut,
            })

        # ── Tools from ALL_TOOLS ──────────────────────────────────────────
        try:
            from cai.tools.all_tools import ALL_TOOLS as _all_tools

            # Deduplicate by tool name in case the list has duplicates
            seen_tools: set[str] = set()
            for t in _all_tools:
                tool_name = getattr(t, "name", None) or getattr(t, "__name__", "")
                if not tool_name or tool_name in seen_tools:
                    continue
                seen_tools.add(tool_name)
                tool_desc = (getattr(t, "description", "") or "").strip()
                # Grab first sentence for brevity
                if ". " in tool_desc:
                    tool_desc = tool_desc.split(". ")[0] + "."
                if len(tool_desc) > 80:
                    tool_desc = tool_desc[:77] + "…"
                template = self._TOOL_TEMPLATES.get(tool_name, f"{{{tool_name}_args}}")
                # Derive a rough category from module path
                module = getattr(getattr(t, "on_invoke_tool", None), "__module__", "") or ""
                cat = (module.split(".")[-2] if "." in module else "tool").replace("_", "-")
                cmds.append({
                    "id":          f"tool::{tool_name}",
                    "name":        tool_name,
                    "type":        "tool",
                    "description": tool_desc or "CAI function tool",
                    "shortcut":    "",
                    "category":    cat,
                    "action":      "fill",
                    "payload":     template,
                    "template":    template,
                })
        except Exception:
            pass

        # ── Agents ───────────────────────────────────────────────────────
        try:
            agents = dict(self._available_agents)
        except Exception:
            agents = {}
        for agent_name, agent_obj in sorted(agents.items()):
            instructions = ""
            try:
                instructions = str(getattr(agent_obj, "instructions", "") or "").strip()
                if ". " in instructions:
                    instructions = instructions.split(". ")[0] + "."
                if len(instructions) > 80:
                    instructions = instructions[:77] + "…"
            except Exception:
                pass
            display = _pretty_name(agent_name)
            cmds.append({
                "id":          f"agent::{agent_name}",
                "name":        display,
                "type":        "agent",
                "description": instructions or "CAI agent",
                "shortcut":    "",
                "category":    "agent",
                "action":      "agent",
                "payload":     agent_name,
            })

        return cmds

    def _record_palette_recent(self, cmd_id: str) -> None:
        key = str(cmd_id or "").strip()
        if not key:
            return
        self._command_palette_recent = [k for k in self._command_palette_recent if k != key]
        self._command_palette_recent.insert(0, key)
        self._command_palette_recent = self._command_palette_recent[:20]

    def _selected_or_latest_session_idx(self) -> int | None:
        selected = getattr(self, "_session_selected_idx", None)
        if (
            selected is not None
            and hasattr(self, "_session_files")
            and selected in self._session_files
        ):
            return selected
        try:
            if hasattr(self, "_session_files") and self._session_files:
                return sorted(self._session_files.keys())[0]
        except Exception:
            pass
        return None

    @work(exclusive=False)
    async def _execute_palette_command_worker(self, cmd_id: str) -> None:
        cmd = str(cmd_id or "").strip().lower()
        if not cmd:
            return

        self._record_palette_recent(cmd)

        if cmd == "clear":
            self.action_clear_active()
            self._log_to_active_terminal("[palette] cleared terminal output")
            return

        if cmd == "save":
            try:
                from cai.sdk.agents.run_to_jsonl import get_session_recorder

                recorder = get_session_recorder()
                filename = str(getattr(recorder, "filename", ""))
                self._populate_sessions_list_worker()
                if filename:
                    self._log_to_active_terminal(f"[palette] session recorder active: {filename}")
                else:
                    self._log_to_active_terminal("[palette] session persisted", style="#00aa00")
            except Exception as exc:
                self._log_to_active_terminal(f"[palette] save failed: {exc}", style="#ff4444")
            return

        if cmd == "load":
            idx = self._selected_or_latest_session_idx()
            if idx is None:
                self._log_to_active_terminal("[palette] no session found to load", style="#ff6600")
                return
            self._session_open_worker(idx)
            return

        if cmd == "export":
            idx = self._selected_or_latest_session_idx()
            if idx is None:
                self._log_to_active_terminal(
                    "[palette] no session found to export", style="#ff6600"
                )
                return
            self._session_export_worker(idx)
            return

        if cmd == "reset":
            try:
                from cai.sdk.agents.models.openai_chatcompletions import (
                    ACTIVE_MODEL_INSTANCES,
                    PERSISTENT_MESSAGE_HISTORIES,
                )
                from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                current_agent_name = AGENT_MANAGER._active_agent_name
                if current_agent_name:
                    AGENT_MANAGER._message_history[current_agent_name] = []
                    PERSISTENT_MESSAGE_HISTORIES[current_agent_name] = []
                    for (name, _inst_id), model_ref in list(ACTIVE_MODEL_INSTANCES.items()):
                        if name != current_agent_name:
                            continue
                        try:
                            model = model_ref() if model_ref else None
                            if model is not None and hasattr(model, "message_history"):
                                model.message_history.clear()
                        except Exception:
                            continue
                    os.environ["CAI_CONTEXT_USAGE"] = "0.0"
                self._log_to_active_terminal("[palette] agent context reset")
            except Exception as exc:
                self._log_to_active_terminal(f"[palette] reset failed: {exc}", style="#ff4444")
            return

        if cmd == "help":
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                await panel.dispatch("/help")
            except Exception as exc:
                self._log_to_active_terminal(f"[palette] help failed: {exc}", style="#ff4444")
            return

        self._log_to_active_terminal(f"[palette] unknown command: {cmd}", style="#ff6600")

    def _fill_active_terminal_input(self, template: str) -> None:
        """Place *template* into the active terminal input and focus it."""
        try:
            panel = self.query_one(
                f"#terminal-panel-{self._active_term_id}", TerminalPanel
            )
            panel._set_input_text(template)
            inp = panel._get_input_widget()
            if inp is not None:
                inp.focus()
        except Exception:
            pass
        # Switch to terminal tab so the user can see the filled input
        try:
            self._switch_top_tab("tab-terminal")
        except Exception:
            pass

    @work(exclusive=False)
    async def _switch_agent_worker(self, agent_name: str) -> None:
        """Directly switch the active terminal to *agent_name* without a modal."""
        new_agent = self._available_agents.get(agent_name)
        if new_agent is None:
            self._log_to_active_terminal(
                f"[palette] agent not found: {agent_name}", style="#ff6600"
            )
            return
        try:
            panel = self.query_one(
                f"#terminal-panel-{self._active_term_id}", TerminalPanel
            )
            panel.update_agent(new_agent, agent_name)
            self._set_active_terminal(self._active_term_id)
        except Exception:
            pass
        self._highlight_active_agent(agent_name)
        self._record_palette_recent(f"agent::{agent_name}")
        self._log_to_active_terminal(
            f"[palette] switched to agent: {agent_name}", style="#00aa00"
        )

    @work(exclusive=False)
    async def _open_command_palette_worker(self) -> None:
        result = await self.push_screen_wait(
            CommandPaletteModal(
                commands=self._command_palette_commands(),
                recent=self._command_palette_recent,
            )
        )
        if not result or not isinstance(result, (tuple, list)) or len(result) < 2:
            return
        action, payload = str(result[0]), str(result[1])
        if action == "run":
            self._execute_palette_command_worker(payload)
        elif action == "fill":
            self._record_palette_recent(payload)
            self._fill_active_terminal_input(payload)
        elif action == "agent":
            self._switch_agent_worker(payload)

    # ------------------------------------------------------------------ actions

    def action_clear_active(self) -> None:
        try:
            self.query_one(f"#term-log-{self._active_term_id}", RichLog).clear()
        except Exception:
            pass

    def action_cancel_active(self) -> None:
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            if not panel.cancel_active_run():
                panel._set_status("")
        except Exception:
            pass

    def action_toggle_sidebar(self) -> None:
        try:
            tabs = self.query_one("#sidebar-tabs", TabbedContent)
            current = str(getattr(tabs, "active", "tab-terminal") or "tab-terminal")
            self._switch_top_tab("tab-agents" if current == "tab-terminal" else "tab-terminal")
        except Exception:
            pass

    def action_command_palette(self) -> None:
        self._open_command_palette_worker()

    def action_new_agent(self) -> None:
        """Ctrl+N — open the add-agent modal (reuses the sidebar agent-add flow)."""
        try:
            # Focus the agents tab; the user can pick an agent from there
            self._switch_top_tab("tab-agents")
        except Exception:
            pass

    def action_cycle_terminal_next(self) -> None:
        """Cycle focus to the next terminal panel (Tab / Ctrl+Right)."""
        try:
            grid = self.query_one("#terminal-grid", TerminalGrid)
            tid = grid.focus_next_panel()
            if tid is not None:
                self._set_active_terminal(tid)
                try:
                    self.query_one(f"#term-input-{tid}", TextArea).focus()
                except Exception:
                    pass
        except Exception:
            pass

    def action_cycle_terminal_prev(self) -> None:
        """Cycle focus to the previous terminal panel (Ctrl+Left)."""
        try:
            grid = self.query_one("#terminal-grid", TerminalGrid)
            tid = grid.focus_prev_panel()
            if tid is not None:
                self._set_active_terminal(tid)
                try:
                    self.query_one(f"#term-input-{tid}", TextArea).focus()
                except Exception:
                    pass
        except Exception:
            pass

    def action_maximize_terminal(self) -> None:
        """Toggle F11 full-screen overlay for the active terminal."""
        try:
            grid = self.query_one("#terminal-grid", TerminalGrid)
            is_max = grid.toggle_maximize(self._active_term_id)
            # Notify the user about the mode change via the status bar.
            try:
                panel = self.query_one(
                    f"#terminal-panel-{self._active_term_id}", TerminalPanel
                )
                panel._set_status(
                    "[F11] Maximised — press F11 to restore" if is_max else ""
                )
            except Exception:
                pass
        except Exception:
            pass

    @on(events.Key)
    async def on_key(self, event: events.Key) -> None:
        """Handle global keybindings via an event handler (modern @on style).

        This mirrors the behaviour previously exposed via `BINDINGS`/`action_*`
        but routes the logic through a single event handler so callers can
        rely on the same entrypoint for unit tests and for the TUI.
        """
        k = getattr(event, "key", "") or ""
        kstr = str(k).lower()
        ctrl = getattr(event, "ctrl", False) or kstr.startswith("ctrl+")

        # Quit: Ctrl+Q
        if (kstr == "q" and ctrl) or kstr.startswith("ctrl+q"):
            try:
                event.stop()
            except Exception:
                pass
            try:
                # Prefer the App-level exit()
                self.exit()
            except Exception:
                try:
                    await self.action_quit()
                except Exception:
                    pass
            return

        # Clear: Ctrl+L
        if (kstr == "l" and ctrl) or kstr.startswith("ctrl+l"):
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_clear_active()
            except Exception:
                pass
            return

        # Cancel: Ctrl+C or Escape
        if ((kstr == "c" and ctrl) or kstr.startswith("ctrl+c")) or kstr == "escape":
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_cancel_active()
            except Exception:
                pass
            return

        # Sidebar toggle: Ctrl+S
        if (kstr == "s" and ctrl) or kstr.startswith("ctrl+s"):
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_toggle_sidebar()
            except Exception:
                pass
            return

        # Command palette: Ctrl+P
        if (kstr == "p" and ctrl) or kstr.startswith("ctrl+p"):
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_command_palette()
            except Exception:
                pass
            return

        # Cycle terminals forward: Tab key (only when no modal is on screen)
        if kstr == "tab" and not ctrl:
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_cycle_terminal_next()
            except Exception:
                pass
            return

        # Cycle terminals forward: Ctrl+Right
        if (kstr in ("ctrl+right", "right") and ctrl) or kstr == "ctrl+right":
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_cycle_terminal_next()
            except Exception:
                pass
            return

        # Cycle terminals backward: Ctrl+Left
        if (kstr in ("ctrl+left", "left") and ctrl) or kstr == "ctrl+left":
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_cycle_terminal_prev()
            except Exception:
                pass
            return

        # Maximise / restore active terminal: F11
        if kstr == "f11":
            try:
                event.stop()
            except Exception:
                pass
            try:
                self.action_maximize_terminal()
            except Exception:
                pass
            return

    # ------------------------------------------------------------------ Sidebar message handlers

    def on_sessions_tab_session_action(self, event: SessionsTab.SessionAction) -> None:  # type: ignore[override]
        """Handle session actions emitted by the SessionsTab widget."""
        try:
            action = (event.action or "").strip().lower()
            idx = event.index
            if action == "refresh":
                try:
                    self._populate_sessions_list_worker()
                except Exception:
                    pass
                return

            if action == "select" and idx is not None:
                try:
                    self._set_selected_session(int(idx))
                except Exception:
                    pass
                return

            if action == "open":
                try:
                    if idx is not None:
                        self._session_open_worker(int(idx))
                except Exception:
                    pass
                return

            if action == "resume":
                try:
                    if idx is not None:
                        self._session_resume_worker(int(idx))
                except Exception:
                    pass
                return

            if action == "export":
                try:
                    if idx is not None:
                        self._session_export_worker(int(idx))
                except Exception:
                    pass
                return

            if action == "rename":
                try:
                    if idx is not None:
                        self._session_rename_worker(int(idx))
                except Exception:
                    pass
                return

            if action == "delete":
                try:
                    if idx is not None:
                        self._session_delete_worker(int(idx))
                except Exception:
                    pass
                return
        except Exception:
            pass

    def on_tools_tab_tool_action(self, event: ToolsTab.ToolAction) -> None:  # type: ignore[override]
        """Handle tool actions emitted by the ToolsTab widget."""
        try:
            action = (event.action or "").strip().lower()
            tool_id = event.tool_id
            if action == "select":
                try:
                    self._selected_tool_id = tool_id
                    if tool_id:
                        self._highlight_active_tool(tool_id)
                    self._update_tools_preview()
                except Exception:
                    pass
                return

            if action == "run":
                try:
                    tid = tool_id or getattr(self, "_selected_tool_id", None)
                    if tid:
                        self._selected_tool_id = tid
                        self._run_selected_tool_worker()
                except Exception:
                    pass
                return

            if action == "inspect":
                try:
                    self._update_tools_preview()
                except Exception:
                    pass
                return

            if action == "replay":
                try:
                    self._replay_selected_tool_call_worker()
                except Exception:
                    pass
                return

            if action == "inject":
                try:
                    self._inject_selected_tool_output()
                except Exception:
                    pass
                return

            if action == "toggle_mode":
                try:
                    self._toggle_inject_mode()
                except Exception:
                    pass
                return
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_tui(agent=None, initial_prompt: str | None = None) -> None:
    """Launch the Matrix TUI (blocks until the user exits with ^q or /exit)."""
    # Guarantee .env is loaded and LOCAL_* vars are propagated to OPENAI_* before
    # the first agent request fires — even when the TUI is invoked directly without
    # going through cli.py (which normally calls initialize_env() at import time).
    try:
        from cai.bootstrap import initialize_env

        initialize_env()
    except Exception:
        pass
    CAIApp(agent=agent, initial_prompt=initial_prompt).run()
