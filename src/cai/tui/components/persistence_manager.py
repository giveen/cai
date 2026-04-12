"""PersistenceManager widget for the CAI TUI.

Shows a disk icon that turns green when the workspace journal is present/updated,
and provides a manual `RE-SYNC FROM DISK` button to reload the journal and
refresh the sidebar Target Summary.
"""
from __future__ import annotations

import traceback
from typing import Any

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static, Button

import cai.orchestration.persistence as persistence


class PersistenceManager(Widget):
    """Widget that displays disk sync status and a resync button."""

    DEFAULT_CSS = """
    PersistenceManager {
        dock: top;
        padding: 0 0 1 0;
    }
    """

    _synced: reactive[bool] = reactive(False)
    _last_updated: reactive[str | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static("[dim]Disk:[/dim] [dim]—[/dim]", id="persistence-disk")
        yield Button("♻️  RE-SYNC FROM DISK", id="persistence-resync", classes="team-btn")
        yield Button("🔓 CLEAR ANCHORS", id="persistence-clear-anchors", classes="team-btn")

    def on_mount(self) -> None:
        # Initial load
        try:
            self._refresh_from_disk()
        except Exception:
            # swallow; widget should be resilient
            pass
        # Poll for updates from disk periodically
        try:
            self.set_interval(5.0, self._refresh_from_disk)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        bid = event.button.id or ""
        if bid == "persistence-resync":
            try:
                self._do_manual_resync()
            except Exception:
                # best-effort: show trace in disk status
                try:
                    self.query_one("#persistence-disk", Static).update("[red]Resync failed[/red]")
                except Exception:
                    pass
        elif bid == "persistence-clear-anchors":
            try:
                # Best-effort: clear all anchors in workspace journal
                cleared = persistence.clear_all_anchors()
                # Refresh UI regardless
                self._do_manual_resync()
                # Provide quick feedback in the disk status widget
                try:
                    disk = self.query_one("#persistence-disk", Static)
                    if cleared:
                        disk.update("[bold yellow]Anchors cleared[/bold yellow]")
                    else:
                        disk.update("[dim]No anchors to clear[/dim]")
                except Exception:
                    pass
            except Exception:
                try:
                    self.query_one("#persistence-disk", Static).update("[red]Clear anchors failed[/red]")
                except Exception:
                    pass

    def _do_manual_resync(self) -> None:
        """Force a reload from disk and update the UI."""
        try:
            journal = persistence.read_journal()
            # Update last-updated marker
            meta = journal.get("meta", {}) or {}
            upd = meta.get("updated_at") or (journal.get("entries", [])[-1]["timestamp"] if journal.get("entries") else None)
            self._last_updated = upd
            self._synced = bool(upd)
            self._update_disk_icon()
            # Also refresh the Target Summary if present in the sidebar
            try:
                summary = persistence.summarize_journal()
                tgt = self.app.query_one("#target-summary", Static)
                tgt.update(summary)
            except Exception:
                # ignore if sidebar not mounted
                pass
        except Exception:
            # Indicate degraded data state rather than raising traceback in the UI
            try:
                disk = self.query_one("#persistence-disk", Static)
                disk.update("[bold red]Data Degraded[/bold red]")
            except Exception:
                pass
            self._synced = False
            self._last_updated = None

    def _refresh_from_disk(self) -> None:
        try:
            journal = persistence.read_journal()
            meta = journal.get("meta", {}) or {}
            upd = meta.get("updated_at")
            if upd and upd != self._last_updated:
                # New update on disk
                self._last_updated = upd
                self._synced = True
                # update sidebar target summary
                try:
                    summary = persistence.summarize_journal()
                    tgt = self.app.query_one("#target-summary", Static)
                    tgt.update(summary)
                except Exception:
                    pass
            else:
                # keep synced if we have any journal
                self._synced = bool(journal.get("entries"))

            self._update_disk_icon()
        except Exception:
            # If reading fails, mark as degraded and update UI accordingly
            self._synced = False
            self._last_updated = None
            try:
                disk = self.query_one("#persistence-disk", Static)
                disk.update("[bold red]Data Degraded[/bold red]")
            except Exception:
                pass
            try:
                self._update_disk_icon()
            except Exception:
                pass

    def _update_disk_icon(self) -> None:
        try:
            disk = self.query_one("#persistence-disk", Static)
        except Exception:
            return
        try:
            if self._synced:
                disk.update("[bold #00ff00]💾 Synced[/bold #00ff00]")
            else:
                disk.update("[dim]💾 No journal[/dim]")
        except Exception:
            pass
