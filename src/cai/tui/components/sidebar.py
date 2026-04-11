"""Sidebar component: Sessions and Tools tabs emitting messages to the App.

This module provides a `Sidebar` container which hosts a TabbedContent
including `SessionsTab` and `ToolsTab`. The tabs emit message objects
instead of calling app-level methods directly so the App or controller
can react to user actions.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from textual.app import ComposeResult
import cai.orchestration.persistence as persistence
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    TabbedContent,
    TabPane,
    Button,
    ListItem,
    ListView,
    Static,
    Switch,
    Sparkline,
    ProgressBar,
)
from textual.widget import Widget
import hashlib
import re
import json

from cai.memory.paging import register_page, page_in


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


class ConfigTab(Widget):
    """Matrix-style config tab: two-column grid with live status badges.

    Left column — category buttons with icon + live badge.
    Right column — quick-look diagnostic pane that updates on hover/focus.

    The header cycles a scan-line sequence every second.
    """

    app: Any

    _SCANLINE_FRAMES = [
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 ]",
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 _]",
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 ░]",
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 ▒]",
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 ▓]",
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 ░]",
        "[ SYSTEM CONFIGURATION :: ACCESS LEVEL 0 _]",
    ]

    _CATEGORIES: list[dict] = [
        {
            "id": "config-providers",
            "icon": "[bold #00ff00]🛰 [/bold #00ff00]",
            "label": "Providers",
            "key": "providers",
            "badge_fn": "_badge_providers",
            "detail_fn": "_detail_providers",
        },
        {
            "id": "config-model-params",
            "icon": "[bold #00dd88]🧠[/bold #00dd88] ",
            "label": "Model Params",
            "key": "model-params",
            "badge_fn": "_badge_model_params",
            "detail_fn": "_detail_model_params",
        },
        {
            "id": "config-memory",
            "icon": "[bold #00ccff]📼[/bold #00ccff] ",
            "label": "Memory / RAG",
            "key": "memory",
            "badge_fn": "_badge_memory",
            "detail_fn": "_detail_memory",
        },
        {
            "id": "config-export-import",
            "icon": "[bold #88ff44]💾[/bold #88ff44] ",
            "label": "Export / Import",
            "key": "export-import",
            "badge_fn": "_badge_export",
            "detail_fn": "_detail_export",
        },
        {
            "id": "config-env",
            "icon": "[bold #ffcc00]🌍[/bold #ffcc00] ",
            "label": "Environment",
            "key": "env",
            "badge_fn": "_badge_env",
            "detail_fn": "_detail_env",
        },
    ]

    _scan_frame: reactive[int] = reactive(0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._focused_key: str | None = None
        self._recording_on: bool = (
            os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() != "true"
        )

    def compose(self) -> ComposeResult:
        yield Static("", id="cfg-header", classes="cfg-header")
        with Horizontal(id="cfg-body"):
            with Vertical(id="cfg-left"):
                for cat in self._CATEGORIES:
                    badge = self._call_badge(cat["badge_fn"])
                    yield Button(
                        f"{cat['icon']}[bold]{cat['label']}[/bold]  "
                        f"[dim #005500]{badge}[/dim #005500]",
                        id=cat["id"],
                        classes="cfg-btn",
                    )
                # Separator
                yield Static("─" * 22, classes="cfg-sep")
                # Session-recording toggle
                with Horizontal(id="cfg-recording-row", classes="cfg-recording-row"):
                    yield Static(
                        "📼 Session Rec",
                        id="cfg-recording-label",
                        classes="cfg-recording-label",
                    )
                    yield Switch(
                        value=self._recording_on,
                        id="cfg-recording-switch",
                        classes="cfg-recording-switch",
                    )
                # Reset button
                yield Button(
                    "⟳  Reset Defaults",
                    id="config-reset-defaults",
                    classes="cfg-btn cfg-btn--danger",
                )
            with ScrollableContainer(id="cfg-right"):
                yield Static("", id="cfg-detail", classes="cfg-detail")
                yield Static("Loading Target Summary…", id="target-summary", classes="cfg-target-summary")

    def on_mount(self) -> None:
        self._render_header()
        self._render_detail(None)
        self.set_interval(1.0, self._tick_scan)
        self.set_interval(5.0, self._refresh_badges)
        # Refresh the persisted Target Summary periodically
        try:
            self.set_interval(5.0, self._refresh_target_summary)
            # Initial render
            self._refresh_target_summary()
        except Exception:
            pass

    # ── Scan-line animation ───────────────────────────────────────────────

    def _tick_scan(self) -> None:
        frames = self._SCANLINE_FRAMES
        self._scan_frame = (self._scan_frame + 1) % len(frames)
        self._render_header()

    def _render_header(self) -> None:
        frame = self._SCANLINE_FRAMES[self._scan_frame % len(self._SCANLINE_FRAMES)]
        try:
            self.query_one("#cfg-header", Static).update(
                f"[bold #00ff00]{frame}[/bold #00ff00]"
            )
        except Exception:
            pass

    # ── Badge helpers (live status text) ─────────────────────────────────

    def _call_badge(self, method_name: str) -> str:
        try:
            return str(getattr(self, method_name)())
        except Exception:
            return ""

    def _badge_providers(self) -> str:
        model = os.environ.get("CAI_MODEL", "alias1")
        backend = os.environ.get("LITELLM_PROVIDER", os.environ.get("LOCAL_API_BASE", ""))
        if backend:
            short = backend.split("/")[-2] if "/" in backend else backend[:16]
            return f"[{short}]"
        return f"[{model}]"

    def _badge_model_params(self) -> str:
        temp = os.environ.get("CAI_TEMPERATURE", "0.7")
        max_t = os.environ.get("CAI_MAX_TOKENS", "—")
        return f"T={temp} max={max_t}"

    def _badge_memory(self) -> str:
        try:
            from cai.config import CAI_CTX_LIMIT
            used_str = os.environ.get("CAI_CONTEXT_USAGE", "0")
            used_k = int(float(used_str) * CAI_CTX_LIMIT) // 1000
            total_k = CAI_CTX_LIMIT // 1000
            return f"{used_k}k/{total_k}k"
        except Exception:
            return ""

    def _badge_export(self) -> str:
        log_dir = os.environ.get("CAI_LOG_DIR", "logs")
        try:
            n = len([f for f in os.listdir(log_dir) if f.endswith(".jsonl")])
            return f"{n} sessions"
        except Exception:
            return ""

    def _badge_env(self) -> str:
        count = sum(
            1 for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
                        "GEMINI_API_KEY", "MISTRAL_API_KEY")
            if os.environ.get(k)
        )
        return f"{count}/5 keys set"

    # ── Detail pane helpers ───────────────────────────────────────────────

    def _render_detail(self, key: str | None) -> None:
        fn_map = {c["key"]: c["detail_fn"] for c in self._CATEGORIES}
        fn_name = fn_map.get(key or "", "_detail_default") if key else "_detail_default"
        text = self._call_detail(fn_name)
        try:
            self.query_one("#cfg-detail", Static).update(text)
        except Exception:
            pass

    def _call_detail(self, method_name: str) -> str:
        try:
            return str(getattr(self, method_name)())
        except Exception:
            return ""

    def _detail_default(self) -> str:
        lines = [
            "[bold #00ff00]Quick-Look Diagnostics[/bold #00ff00]",
            "",
            "[dim #005500]Hover over a category to inspect[/dim #005500]",
            "[dim #005500]its current configuration.[/dim #005500]",
            "",
        ]
        for cat in self._CATEGORIES:
            badge = self._call_badge(cat["badge_fn"])
            icon = cat["icon"]
            label = cat["label"]
            lines.append(f" {icon}[#00cc00]{label:<18}[/#00cc00] [dim]{badge}[/dim]")
        return "\n".join(lines)

    def _detail_providers(self) -> str:
        model = os.environ.get("CAI_MODEL", "alias1")
        base = (
            os.environ.get("OPENAI_API_BASE")
            or os.environ.get("LOCAL_API_BASE")
            or "https://api.openai.com/v1"
        )
        keys_set = [
            k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY")
            if os.environ.get(k)
        ]
        return "\n".join([
            "[bold #00ff00]🛰  Providers[/bold #00ff00]",
            "",
            f"  Model        [#00cc00]{model}[/#00cc00]",
            f"  Base URL     [dim]{base[:40]}[/dim]",
            f"  Keys set     [#00cc00]{', '.join(keys_set) or 'none'}[/#00cc00]",
            "",
            "[dim #005500]Press Enter to open Providers screen[/dim #005500]",
        ])

    def _detail_model_params(self) -> str:
        temp = os.environ.get("CAI_TEMPERATURE", "0.7")
        max_t = os.environ.get("CAI_MAX_TOKENS", "not set")
        top_p = os.environ.get("CAI_TOP_P", "not set")
        model = os.environ.get("CAI_MODEL", "alias1")
        ctx = os.environ.get("CAI_CTX_LIMIT", "393216")
        return "\n".join([
            "[bold #00dd88]🧠 Model Parameters[/bold #00dd88]",
            "",
            f"  Model        [#00cc00]{model}[/#00cc00]",
            f"  Temperature  [#00cc00]{temp}[/#00cc00]",
            f"  Max Tokens   [dim]{max_t}[/dim]",
            f"  Top-P        [dim]{top_p}[/dim]",
            f"  CTX Limit    [dim]{int(ctx):,}[/dim]",
            "",
            "[dim #005500]Press Enter to edit[/dim #005500]",
        ])

    def _detail_memory(self) -> str:
        try:
            from cai.config import CAI_CTX_LIMIT as CTX
        except Exception:
            try:
                CTX = int(os.environ.get("CAI_CTX_LIMIT", "393216"))
            except Exception:
                CTX = 393216

        try:
            used_raw = float(os.environ.get("CAI_CONTEXT_USAGE", "0"))
        except Exception:
            used_raw = 0.0
        used_k = int(used_raw * CTX) // 1000
        total_k = CTX // 1000
        pct = used_raw * 100.0

        # Auto-compact state
        auto_compact_raw = os.environ.get("CAI_AUTO_COMPACT", "false")
        auto_compact = "yes" if str(auto_compact_raw).strip().lower() in ("1", "true", "yes", "on") else "no"

        # Auto-compact threshold: prefer ENV, else derive from cai.config if available
        thr_raw = os.environ.get("CAI_AUTO_COMPACT_THRESHOLD")
        thr_frac = None
        if thr_raw is not None and thr_raw != "":
            try:
                f = float(thr_raw)
                if 0.0 < f <= 1.0:
                    thr_frac = float(f)
                else:
                    # absolute token count → convert to fraction
                    thr_frac = float(f) / float(CTX)
            except Exception:
                thr_frac = None
        if thr_frac is None:
            try:
                import cai.config as _cconf

                thr_tokens = getattr(_cconf, "CAI_AUTO_COMPACT_THRESHOLD", None)
                if thr_tokens is not None:
                    thr_frac = float(thr_tokens) / float(getattr(_cconf, "CAI_CTX_LIMIT", CTX))
            except Exception:
                thr_frac = None
        if thr_frac is None:
            thr_frac = float(0.9)

        thr_pct = max(0.0, min(100.0, thr_frac * 100.0))

        rag = os.environ.get("CAI_RAG", "0")
        bar_filled = int(pct / 5)
        bar = "[#00ff00]" + "█" * bar_filled + "[/#00ff00][dim]" + "░" * (20 - bar_filled) + "[/dim]"
        return "\n".join([
            "[bold #00ccff]📼 Memory / RAG[/bold #00ccff]",
            "",
            f"  Context      {bar} {pct:.1f}%",
            f"  Used / Total [#00cc00]{used_k}k / {total_k}k[/#00cc00]",
            f"  RAG enabled  [#00cc00]{'yes' if rag == '1' else 'no'}[/#00cc00]",
            f"  Auto-compact [#00cc00]{auto_compact}[/#00cc00]",
            f"  Auto-thresh [#00cc00]{thr_pct:.0f}%[/#00cc00]",
            f"  CTX Limit   [#00cc00]{CTX:,}[/#00cc00]",
            "",
            "[dim #005500]Press Enter to open Memory screen[/dim #005500]",
        ])

    def _detail_export(self) -> str:
        log_dir = os.environ.get("CAI_LOG_DIR", "logs")
        try:
            files = sorted([f for f in os.listdir(log_dir) if f.endswith(".jsonl")])
            count = len(files)
            recent = files[-1] if files else "—"
        except Exception:
            count = 0
            recent = "—"
        return "\n".join([
            "[bold #88ff44]💾 Export / Import[/bold #88ff44]",
            "",
            f"  Sessions     [#00cc00]{count}[/#00cc00]",
            f"  Latest       [dim]{recent[:36]}[/dim]",
            f"  Log dir      [dim]{log_dir}[/dim]",
            "",
            "[dim #005500]Press Enter to export / import[/dim #005500]",
        ])

    def _detail_env(self) -> str:
        vars_to_show = [
            "CAI_MODEL", "CAI_TEMPERATURE", "CAI_CTX_LIMIT",
            "CAI_LOG_LEVEL", "CAI_DEBUG", "CAI_DISABLE_SESSION_RECORDING",
        ]
        lines = ["[bold #ffcc00]🌍 Environment[/bold #ffcc00]", ""]
        for v in vars_to_show:
            val = os.environ.get(v, "[dim]—[/dim]")
            lines.append(f"  [dim]{v:<32}[/dim] [#00cc00]{val}[/#00cc00]")
        lines += ["", "[dim #005500]Press Enter to edit env vars[/dim #005500]"]
        return "\n".join(lines)

    # ── Badge refresh ─────────────────────────────────────────────────────

    def _refresh_badges(self) -> None:
        for cat in self._CATEGORIES:
            badge = self._call_badge(cat["badge_fn"])
            try:
                btn = self.query_one(f"#{cat['id']}", Button)
                btn.label = (  # type: ignore[assignment]
                    f"{cat['icon']}[bold]{cat['label']}[/bold]  "
                    f"[dim #005500]{badge}[/dim #005500]"
                )
            except Exception:
                pass
        # Also refresh detail pane if a category is focused
        self._render_detail(self._focused_key)

    def _refresh_target_summary(self) -> None:
        try:
            summary = persistence.summarize_journal()
            try:
                tgt = self.query_one("#target-summary", Static)
                tgt.update(summary)
            except Exception:
                pass
        except Exception:
            pass

    # ── Interactions ──────────────────────────────────────────────────────

    def on_enter(self, event: events.Enter) -> None:
        """Hover effect: update quick-look pane when entering a category button."""
        node = getattr(event, "node", None)
        if not isinstance(node, Button):
            return
        bid = node.id or ""
        for cat in self._CATEGORIES:
            if cat["id"] == bid:
                self._focused_key = cat["key"]
                self._render_detail(cat["key"])
                return

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""
        if bid == "config-session-recording":
            return  # handled by Switch
        if bid in {c["id"] for c in self._CATEGORIES} | {"config-reset-defaults"}:
            # Let the event bubble to App's on_button_pressed handler
            return

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if (event.switch.id or "") != "cfg-recording-switch":
            return
        event.stop()
        self._recording_on = event.value
        if event.value:
            os.environ.pop("CAI_DISABLE_SESSION_RECORDING", None)
        else:
            os.environ["CAI_DISABLE_SESSION_RECORDING"] = "true"
        # Notify the rest of the app via a synthetic button press on the legacy id
        try:
            self.app.call_later(self._notify_recording_changed)
        except Exception:
            pass

    def _notify_recording_changed(self) -> None:
        state = "enabled" if self._recording_on else "disabled"
        try:
            self.app._log_to_active_terminal(  # type: ignore[attr-defined]
                f"[config] Session recording {state}", style="#00aa00"
            )
        except Exception:
            pass


class TopologyTab(Widget):
    """Topology tab: render discovered hosts and allow paging their recon into VCM.

    Heuristics: scan the intelligence journal for entries whose category or
    fact mentions hosts/nmap; extract a host identifier (ip/hostname) and
    show a clickable node per host. Clicking a node registers a `recon:<host>`
    page and attempts to `page_in()` it into the VCM.
    """

    DEFAULT_CSS = """
    TopologyTab {
        background: #000800;
        border: solid #003300;
        padding: 0 1;
    }

    TopologyTab #topo-kali {
        color: #00ff00;
        text-style: bold;
        padding: 0 0 1 0;
    }

    TopologyTab .topo-node Button {
        width: auto;
        padding: 0 1;
        margin: 0 0 0.2 0;
    }

    TopologyTab .topo-conn {
        color: #005500;
        width: 2;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._host_button_map: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="topology-pane"):
            yield Static("[bold #00ff00][K][/bold #00ff00] Kali VM", id="topo-kali")
            with ScrollableContainer(id="topo-host-list"):
                pass
            yield Static("", id="topo-status")

    def on_mount(self) -> None:
        try:
            # Refresh every 5s so new discoveries appear
            self.set_interval(5.0, self._refresh_hosts)
            self._refresh_hosts()
        except Exception:
            pass

    def _extract_host_from_fact(self, fact: Any) -> str | None:
        try:
            if not fact:
                return None
            # If dict, look for common host keys
            if isinstance(fact, dict):
                for k in ("ip", "address", "host", "hostname", "fqdn", "name"):
                    v = fact.get(k)
                    if v:
                        return str(v)
                # If nested keys
                if "target" in fact and isinstance(fact["target"], str):
                    return fact["target"]
                # Fallback: stringify
                s = json.dumps(fact, ensure_ascii=False)
            else:
                s = str(fact)

            # Try to find an IPv4 address first
            m = re.search(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", s)
            if m:
                return m.group(0)

            # Try to find a hostname-like token
            m2 = re.search(r"([a-zA-Z0-9\-_.]+\.[a-zA-Z]{2,})", s)
            if m2:
                return m2.group(1)

            # Last resort: use shortened fact text
            short = s.strip().splitlines()[0]
            if short:
                return short[:64]
        except Exception:
            pass
        return None

    def _refresh_hosts(self) -> None:
        try:
            journal = persistence.read_journal() or {}
            entries = journal.get("entries", []) or []
            hosts: dict[str, dict] = {}

            for e in entries:
                try:
                    cat = (e.get("category") or "").lower()
                    fact = e.get("fact")
                    host = None
                    # Prefer explicit host-related categories
                    if "host" in cat or "nmap" in cat or "scan" in cat:
                        host = self._extract_host_from_fact(fact)
                    else:
                        # Try to parse host-like data from fact text
                        host = self._extract_host_from_fact(fact)

                    if not host:
                        continue

                    # Decide status heuristically
                    fact_text = json.dumps(fact, ensure_ascii=False) if not isinstance(fact, str) else str(fact)
                    status = "discovered"
                    if any(x in fact_text.lower() for x in ("pwned", "compromised", "root", "shell", "exploit", "meterpreter")):
                        status = "pwned"
                    if any(x in fact_text.lower() for x in ("filtered", "ignore", "excluded")):
                        status = "filtered"

                    # Keep the first-seen entry id and fact for quick page-in
                    if host not in hosts:
                        hosts[host] = {"status": status, "entry": e}
                    else:
                        # Upgrade status to pwned if any entry indicates compromise
                        if status == "pwned":
                            hosts[host]["status"] = "pwned"
                except Exception:
                    continue

            # Render hosts
            try:
                container = self.query_one("#topo-host-list", ScrollableContainer)
            except Exception:
                container = None

            if container is not None:
                # Clear children
                try:
                    for child in list(container.children):
                        try:
                            child.remove()
                        except Exception:
                            pass
                except Exception:
                    pass

            self._host_button_map.clear()
            i = 0
            for host, info in sorted(hosts.items()):
                try:
                    hid = hashlib.sha1(host.encode("utf-8")).hexdigest()[:8]
                except Exception:
                    hid = str(i)
                btn_id = f"topo-host-{hid}"
                self._host_button_map[btn_id] = host

                if container is not None:
                    try:
                        row = Horizontal()
                        row.mount(Static("↳", classes="topo-conn"))
                        color = "#00ff00" if info.get("status") == "pwned" else ("#006600" if info.get("status") == "discovered" else "#cc3333")
                        label = f"[{color}][H] {host}[/{color}]"
                        btn = Button(label, id=btn_id, classes="topo-node")
                        row.mount(btn)
                        container.mount(row)
                    except Exception:
                        try:
                            container.mount(Static(f"- {host}"))
                        except Exception:
                            pass
                i += 1

            # Update status
            try:
                self.query_one("#topo-status", Static).update(f"Hosts: {len(hosts)}")
            except Exception:
                pass
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""
        if not bid.startswith("topo-host-"):
            return
        host = self._host_button_map.get(bid)
        if not host:
            return

        # Find a representative journal entry for this host
        content = None
        try:
            journal = persistence.read_journal() or {}
            entries = journal.get("entries", []) or []
            for e in entries:
                try:
                    fact = e.get("fact")
                    hf = self._extract_host_from_fact(fact)
                    if hf and hf == host:
                        content = json.dumps(fact, ensure_ascii=False) if not isinstance(fact, str) else str(fact)
                        break
                except Exception:
                    continue
        except Exception:
            content = None

        page_name = f"recon:{host}"
        try:
            # Register or update the recon page then attempt to page it in
            register_page(page_name, content or f"Recon for {host}", tags=["recon", "host"])  # type: ignore
            res = page_in(page_name)
            if isinstance(res, dict):
                st = res.get("status") or str(res)
            else:
                st = str(res)
            try:
                self.query_one("#topo-status", Static).update(f"{host}: {st}")
            except Exception:
                pass
        except Exception as exc:
            try:
                self.query_one("#topo-status", Static).update(f"Error paging {host}: {exc}")
            except Exception:
                pass


class Sidebar(Widget):
    """Container hosting the main TabbedContent with Sessions and Tools tabs."""

    # Runtime-provided `app` reference — declare for static analysis.
    app: Any

    DEFAULT_CSS = """
    Sidebar #metrics-pane {
        background: #000800;
        border: solid #003300;
        padding: 0 1;
    }

    Sidebar .sparkline-label {
        height: 1;
        color: #00ff00;
        background: #000800;
        margin-top: 0;
    }

    Sidebar Sparkline {
        height: 3;
        background: #002200;
        border: solid #003300;
        margin-bottom: 0;
    }
    """

    def on_mount(self) -> None:
        """Start periodic sampling for the sparklines (5s interval)."""
        # History buffers (keep last 60 samples)
        self._ctx_history: list[float] = []
        self._vram_history: list[float] = []
        self._progress_history: list[float] = []
        try:
            # 5s update as requested
            self.set_interval(5.0, self._tick_sparklines)
            # initial tick
            self._tick_sparklines()
        except Exception:
            pass

    def _tick_sparklines(self) -> None:
        """Gather metrics (from app/controller) and push into sparklines."""
        # --- Context Pressure (%) ---
        try:
            total_input = sum(
                int(s.get("input_tokens", 0) or 0) for s in getattr(self.app, "_telemetry_stats_by_term", {}).values()
            )
            try:
                from cai.config import CAI_CTX_LIMIT

            except Exception:
                CAI_CTX_LIMIT = int(os.getenv("CAI_CTX_LIMIT", "393216"))
            ctx_pct = min(100.0, float(total_input) / max(float(CAI_CTX_LIMIT), 1.0) * 100.0)
        except Exception:
            ctx_pct = 0.0

        self._ctx_history.append(ctx_pct)
        if len(self._ctx_history) > 60:
            self._ctx_history = self._ctx_history[-60:]
        try:
            lbl = self.query_one("#sparkline-ctx-pressure-label", Static)
            lbl.update(f"[#00ff00]Context Pressure[/#00ff00] {ctx_pct:.0f}%")
        except Exception:
            pass
        try:
            self.query_one("#sparkline-ctx-pressure", Sparkline).data = list(self._ctx_history)
        except Exception:
            pass

        # --- 5090 VRAM Usage (%) ---
        vram_pct = 0.0
        try:
            # Try local NVML first (best-effort)
            try:
                import pynvml  # type: ignore[import]

                base = os.getenv("LOCAL_API_BASE", "").strip()
                is_local = bool(base and ("127.0.0.1" in base or "localhost" in base))
                if is_local:
                    try:
                        pynvml.nvmlInit()
                        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        vram_pct = float(mem.used) / float(max(mem.total, 1)) * 100.0
                    except Exception:
                        vram_pct = 0.0
            except Exception:
                vram_pct = 0.0
        except Exception:
            vram_pct = 0.0

        self._vram_history.append(vram_pct)
        if len(self._vram_history) > 60:
            self._vram_history = self._vram_history[-60:]
        try:
            lbl = self.query_one("#sparkline-vram-5090-label", Static)
            lbl.update(f"[#00ff00]5090 VRAM[/#00ff00] {vram_pct:.0f}%")
        except Exception:
            pass
        try:
            self.query_one("#sparkline-vram-5090", Sparkline).data = list(self._vram_history)
        except Exception:
            pass

        # --- Agent Task Progress (%) ---
        prog_pct = 0.0
        try:
            # Prefer reading the tool ProgressBar if present
            try:
                bar = self.app.query_one("#tool-progress-bar", ProgressBar)
                val = getattr(bar, "progress", None)
                if val is None:
                    val = getattr(bar, "value", None)
                if val is None:
                    val = getattr(bar, "_value", 0)
                prog_pct = float(val or 0.0)
            except Exception:
                # Fallback: approximate using controller worker counts vs queued items
                try:
                    workers = getattr(self.app.controller, "_workers", {}) or {}
                    active = len(workers)
                    queue_len = len(getattr(self.app, "_queue_items", []) or [])
                    denom = max(1, max(active, queue_len))
                    prog_pct = min(100.0, (active / denom) * 100.0)
                except Exception:
                    prog_pct = 0.0
        except Exception:
            prog_pct = 0.0

        self._progress_history.append(prog_pct)
        if len(self._progress_history) > 60:
            self._progress_history = self._progress_history[-60:]
        try:
            lbl = self.query_one("#sparkline-agent-progress-label", Static)
            lbl.update(f"[#00ff00]Agent Task Progress[/#00ff00] {prog_pct:.0f}%")
        except Exception:
            pass
        try:
            self.query_one("#sparkline-agent-progress", Sparkline).data = list(self._progress_history)
        except Exception:
            pass

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

            # Config tab: Matrix HUD-style two-column layout
            with TabPane("Config", id="tab-config"):
                yield ConfigTab(id="config-tab-widget")

            # Tools tab: use our ToolsTab widget
            with TabPane("Tools", id="tab-tools"):
                yield ToolsTab()

            # Topology tab: discovered hosts and Kali VM
            with TabPane("Topology", id="tab-topology"):
                yield TopologyTab()

            # Metrics/Stats tab — sparklines for quick telemetry
            with TabPane("Stats", id="tab-metrics"):
                with Vertical(id="metrics-pane"):
                    with Vertical(id="metrics-sparklines"):
                        yield Static(
                            "Context Pressure (%)",
                            id="sparkline-ctx-pressure-label",
                            classes="sparkline-label",
                        )
                        yield Sparkline([], id="sparkline-ctx-pressure", summary_function=max)

                        yield Static(
                            "5090 VRAM (%)",
                            id="sparkline-vram-5090-label",
                            classes="sparkline-label",
                        )
                        yield Sparkline([], id="sparkline-vram-5090", summary_function=max)

                        yield Static(
                            "Agent Task Progress (%)",
                            id="sparkline-agent-progress-label",
                            classes="sparkline-label",
                        )
                        yield Sparkline([], id="sparkline-agent-progress", summary_function=max)

                    yield Static("", id="metrics-summary")

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
