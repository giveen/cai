"""QueueMixin — task queue and broadcast prompt methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

from textual import work, on
from textual.widgets import Button, Static, ListView, ListItem, Label, Input

from cai.tui.components.terminal import TerminalPanel


class QueueMixin:
    """Mixin providing task queue management and broadcast helpers."""

    def _parse_broadcast_suffix(self, text: str) -> tuple[str, bool]:
        raw = str(text or "").strip()
        if not raw:
            return "", False
        lower = raw.lower()
        if lower.endswith(" all"):
            return raw[:-4].rstrip(), True
        return raw, False

    async def _broadcast_prompt(self, text: str, source_tid: int | None = None) -> None:
        message = str(text or "").strip()
        if not message:
            return
        panels = sorted(list(self.query(TerminalPanel)), key=lambda p: p._term_id)
        for panel in panels[:4]:
            try:
                await panel.dispatch(message)
            except Exception:
                continue
        try:
            self._log_to_active_terminal(
                f"[broadcast] sent to {min(4, len(panels))} terminals: {message[:120]}",
                style="#00ff00",
            )
        except Exception:
            pass
        if source_tid is not None:
            try:
                self._set_active_terminal(source_tid)
            except Exception:
                pass

    def _queue_item_label(self, idx: int, item: dict) -> str:
        status = str(item.get("status", "pending"))
        marker = "○"
        if status == "running":
            marker = "▶"
        elif status == "completed":
            marker = "✓"
        elif status == "error":
            marker = "✗"

        broadcast = bool(item.get("broadcast", False))
        mode_tag = " [ALL]" if broadcast else ""
        text = str(item.get("text", ""))
        return f"{marker} [{idx + 1}]{mode_tag} {text}"

    def _sync_queue_broadcast_button(self) -> None:
        try:
            btn = self.query_one("#queue-broadcast-mode", Button)
            if self._queue_broadcast_mode:
                btn.label = "Broadcast: ON"
                btn.add_class("-active-team")
            else:
                btn.label = "Broadcast: OFF"
                btn.remove_class("-active-team")
        except Exception:
            return

    def _update_queue_status(self) -> None:
        pending = sum(1 for i in self._queue_items if i.get("status") == "pending")
        running = sum(1 for i in self._queue_items if i.get("status") == "running")
        completed = sum(1 for i in self._queue_items if i.get("status") == "completed")
        errors = sum(1 for i in self._queue_items if i.get("status") == "error")
        mode = "ON" if self._queue_broadcast_mode else "OFF"
        run_state = "running" if self._queue_running else "idle"
        if self._responsive_mode == "small":
            text = f"Q p:{pending} r:{running} d:{completed} e:{errors} b:{mode} {run_state}"
        else:
            text = (
                f"Queue: {pending} pending · {running} running · {completed} done · {errors} errors · "
                f"broadcast {mode} · {run_state}"
            )
        try:
            self.query_one("#queue-status", Static).update(text)
        except Exception:
            pass

    def _update_queue_view(self) -> None:
        try:
            lv = self.query_one("#queue-list", ListView)
        except Exception:
            return

        for child in list(lv.children):
            try:
                child.remove()
            except Exception:
                pass

        for idx, item in enumerate(self._queue_items):
            label = self._queue_item_label(idx, item)
            lv.mount(ListItem(Label(label), id=f"queue-item-{idx}"))

        self._update_queue_status()

    def _toggle_queue_broadcast_mode(self) -> None:
        self._queue_broadcast_mode = not self._queue_broadcast_mode
        self._sync_queue_broadcast_button()
        self._update_queue_status()
        self._log_to_active_terminal(
            f"[queue] broadcast mode {'ON' if self._queue_broadcast_mode else 'OFF'}"
        )

    def _add_from_queue_input(self) -> None:
        try:
            inp = self.query_one("#queue-input", Input)
            raw = inp.value.strip()
            if not raw:
                return
            inp.clear()
        except Exception:
            return

        self._add_queue_item(raw)

    @on(ListView.Highlighted, "#queue-list")
    def _on_queue_highlighted(self, event: ListView.Highlighted) -> None:
        try:
            self._queue_selected_idx = int(getattr(event.list_view, "index", -1))
        except Exception:
            self._queue_selected_idx = None

    def _selected_queue_index(self) -> int | None:
        idx = self._queue_selected_idx
        try:
            lv = self.query_one("#queue-list", ListView)
            current_idx = int(getattr(lv, "index", -1))
            if current_idx >= 0:
                idx = current_idx
        except Exception:
            pass

        if idx is None:
            return None
        if idx < 0 or idx >= len(self._queue_items):
            return None
        return idx

    def _delete_selected_queue_item(self) -> None:
        idx = self._selected_queue_index()
        if idx is None:
            self._log_to_active_terminal("[queue] no selected prompt to delete", style="#ff6600")
            return
        try:
            removed = self._queue_items.pop(idx)
            self._queue_selected_idx = None
            self._update_queue_view()
            self._log_to_active_terminal(f"[queue] deleted: {removed.get('text', '')[:120]}")
        except Exception:
            pass

    def _clear_queue(self) -> None:
        self._queue_items = []
        self._queue_selected_idx = None
        self._update_queue_view()
        self._log_to_active_terminal("[queue] cleared all queued prompts")

    @work(exclusive=True)
    async def _run_queue_worker(self) -> None:
        # Delegate queue execution to the controller which runs a background worker.
        try:
            self.controller.run_queue()
        except Exception:
            pass

    def _add_queue_item(self, text: str) -> None:
        msg, explicit_broadcast = self._parse_broadcast_suffix(text)
        if not msg:
            return
        self._queue_items.append(
            {
                "text": msg,
                "status": "pending",
                "broadcast": bool(explicit_broadcast),
            }
        )
        self._update_queue_view()
