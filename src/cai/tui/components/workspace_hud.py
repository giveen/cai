"""WorkspaceHUD — shows active workspace and state.json summary in the TUI.

Displays:
 - Active workspace name
 - Disk Sync icon that pulses green briefly when `state.json` is updated
 - Summary line: "4 Hosts Discovered | 2 Creds Found"
 - Button to open the workspace folder in the OS file manager

The widget polls the workspace/state file every second and is best-effort.
"""

from __future__ import annotations

import os
import time
import subprocess
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static, Button

from cai.tools.common import _get_workspace_dir
from cai.session import _read_state


class WorkspaceHUD(Widget):
    """Small HUD displaying workspace info and a quick state summary."""

    DEFAULT_CSS = """
    WorkspaceHUD {
        height: auto;
        width: 100%;
        background: #000800;
        border: solid #003300;
        padding: 0 1;
        margin-top: 1;
    }

    WorkspaceHUD .ws-name {
        color: #00ff88;
        text-style: bold;
    }

    WorkspaceHUD .ws-sync {
        color: #00ff00;
    }

    WorkspaceHUD #workspace-hud-summary {
        color: #00cc00;
        margin-top: 0;
        margin-bottom: 1;
    }

    WorkspaceHUD Button {
        width: auto;
        padding: 0 1;
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._last_mtime: float = 0.0
        self._pulse_until: float = 0.0
        self._workspace_dir: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="workspace-hud"):
            with Horizontal():
                yield Static("Workspace: —", id="workspace-hud-name", classes="ws-name")
                yield Static("[dim]●[/dim]", id="workspace-hud-sync", classes="ws-sync")
            yield Static("Loading state…", id="workspace-hud-summary")
            yield Button("📂 Open Workspace Folder", id="workspace-hud-open", classes="agent-btn")

    def on_mount(self) -> None:
        # Poll workspace state periodically and refresh UI
        try:
            self.set_interval(1.0, self._tick)
        except Exception:
            pass

    def _tick(self) -> None:
        try:
            wdir = _get_workspace_dir()
            self._workspace_dir = wdir
            display_name = os.path.basename(wdir) or wdir
            try:
                self.query_one("#workspace-hud-name", Static).update(f"Workspace: {display_name}")
            except Exception:
                pass

            # Read state.json via session helper (best-effort)
            try:
                state = _read_state(wdir)
            except Exception:
                state = {}

            hosts = len(state.get("targets") or [])
            creds = len(state.get("credentials") or {})
            summary = f"{hosts} Hosts Discovered | {creds} Creds Found" if (hosts or creds) else "No discoveries yet"
            try:
                self.query_one("#workspace-hud-summary", Static).update(summary)
            except Exception:
                pass

            # Pulse the sync icon when state.json mtime changes
            state_path = os.path.join(wdir, "state.json")
            try:
                mtime = os.path.getmtime(state_path) if os.path.exists(state_path) else 0.0
            except Exception:
                mtime = 0.0

            if mtime and mtime != self._last_mtime:
                self._last_mtime = mtime
                self._pulse_until = time.time() + 1.8

            active = time.time() < self._pulse_until
            icon = "[green]●[/green]" if active else "[dim]●[/dim]"
            try:
                self.query_one("#workspace-hud-sync", Static).update(icon)
            except Exception:
                pass
        except Exception:
            # Swallow errors to keep UI stable
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""
        if bid != "workspace-hud-open":
            return
        wdir = self._workspace_dir or _get_workspace_dir()
        try:
            if os.name == "posix":
                # Use xdg-open where available
                try:
                    subprocess.Popen(["xdg-open", wdir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    subprocess.Popen(["xdg-open", wdir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif os.name == "nt":
                os.startfile(wdir)
            else:
                subprocess.Popen(["open", wdir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if getattr(self.app, "_log_to_active_terminal", None):
                    self.app._log_to_active_terminal(f"[workspace] opened folder: {wdir}", style="#00aa00")
            except Exception:
                pass
        except Exception as exc:
            try:
                if getattr(self.app, "_log_to_active_terminal", None):
                    self.app._log_to_active_terminal(f"[workspace] open failed: {exc}", style="#ff4444")
            except Exception:
                pass
