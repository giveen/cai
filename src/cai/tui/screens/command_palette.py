"""Command palette modal for the CAI TUI.

Supports three entry types:
  - ``type="app"``   — built-in TUI commands (clear, save, …)  → dismisses ("run", cmd_id)
  - ``type="tool"``  — CAI function tools with usage template  → dismisses ("fill", template)
  - ``type="agent"`` — available CAI agents                    → dismisses ("agent", agent_name)
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual import events, on
from textual.widgets import Static, Button, Input

# ── Category display config ──────────────────────────────────────────────────
_TYPE_TAG: dict[str, str] = {
    "app":   "[#006600]AP[/#006600]",
    "tool":  "[#00aa00]TL[/#00aa00]",
    "agent": "[#0088cc]AG[/#0088cc]",
}
_TYPE_SORT_KEY: dict[str, int] = {"app": 0, "tool": 1, "agent": 2}


def _build_label(cmd: dict) -> str:
    """Format one palette row: TAG  name  ·  description  [shortcut/category]."""
    tag = _TYPE_TAG.get(str(cmd.get("type", "app")), "[#006600]AP[/#006600]")
    name = str(cmd.get("name", cmd.get("id", "")))
    desc = str(cmd.get("description", ""))
    extra = str(cmd.get("shortcut", "") or cmd.get("category", ""))
    # Truncate description so the row fits in typical 80-col palette
    if len(desc) > 50:
        desc = desc[:47] + "…"
    label = f"{tag}  {name:<24} {desc}"
    if extra:
        label += f"  [{extra}]"
    return label


class CommandPaletteModal(ModalScreen):
    """Ctrl+P command palette: fuzzy-search tools, agents, and app commands."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, commands: list[dict], recent: list[str]) -> None:
        super().__init__()
        self._commands = list(commands)
        self._recent = list(recent)
        self._selected_idx = 0
        self._visible_commands: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-wrap"):
            yield Static(
                "[bold #00ff00]⌨  Command Palette[/bold #00ff00]"
                "  [dim #006600](tools · agents · commands)[/dim #006600]",
                id="modal-agent-label",
            )
            yield Input(
                placeholder="fuzzy search — tools, agents, commands …",
                id="palette-search",
            )
            with ScrollableContainer(id="palette-results-scroll"):
                with Vertical(id="palette-results"):
                    pass
            yield Static(
                "[dim #006600]↑/↓[/dim #006600] navigate  "
                "[dim #006600]Enter[/dim #006600] run/fill  "
                "[dim #006600]Esc[/dim #006600] close  "
                "  [dim #004400]TL=tool  AG=agent  AP=app[/dim #004400]",
                id="palette-help",
            )

    # ── Fuzzy matching ───────────────────────────────────────────────────────

    def _fuzzy_score(self, query: str, text: str) -> int:
        """Return a positive match score or -1 when query does not match text."""
        q = query.lower().strip()
        t = text.lower()
        if not q:
            return 1
        # Exact substring → strong bonus
        if q in t:
            return 100 + len(q)
        # Character-by-character fuzzy
        score = 0
        pos = 0
        for ch in q:
            found = t.find(ch, pos)
            if found < 0:
                return -1
            score += 3 if found == pos else 1
            pos = found + 1
        return score

    # ── Result list management ───────────────────────────────────────────────

    async def _refresh_results(self) -> None:
        query = ""
        try:
            query = self.query_one("#palette-search", Input).value
        except Exception:
            pass

        ranked: list[tuple[int, dict]] = []
        for cmd in self._commands:
            searchable = " ".join(
                str(cmd.get(k, ""))
                for k in ("id", "name", "description", "type", "category", "template")
            )
            score = self._fuzzy_score(query, searchable)
            if score < 0:
                continue
            # Recent-use recency boost
            try:
                recency = self._recent.index(str(cmd.get("id")))
                score += max(0, 20 - recency)
            except Exception:
                pass
            ranked.append((score, cmd))

        # Sort: highest score first; within same score preserve type order then name
        ranked.sort(
            key=lambda x: (
                -x[0],
                _TYPE_SORT_KEY.get(str(x[1].get("type", "app")), 9),
                str(x[1].get("name", "")),
            )
        )
        self._visible_commands = [cmd for _, cmd in ranked]

        try:
            holder = self.query_one("#palette-results", Vertical)
        except Exception:
            return

        for child in list(holder.children):
            try:
                await child.remove()
            except Exception:
                pass

        if not self._visible_commands:
            await holder.mount(Static("  No matching results", classes="term-status"))
            self._selected_idx = 0
            return

        self._selected_idx = max(0, min(self._selected_idx, len(self._visible_commands) - 1))
        for idx, cmd in enumerate(self._visible_commands):
            label = _build_label(cmd)
            # Use numeric index as the button ID so any characters are safe in CSS
            btn = Button(label, id=f"palette-cmd-{idx}", classes="palette-cmd")
            if idx == self._selected_idx:
                btn.add_class("-selected")
            await holder.mount(btn)

    def _move_selection(self, delta: int) -> None:
        if not self._visible_commands:
            return
        self._selected_idx = (self._selected_idx + delta) % len(self._visible_commands)
        for i, btn in enumerate(self.query(".palette-cmd")):
            if i == self._selected_idx:
                btn.add_class("-selected")
                try:
                    btn.scroll_visible(animate=False)
                except Exception:
                    pass
            else:
                btn.remove_class("-selected")

    # ── Dismiss helpers ──────────────────────────────────────────────────────

    def _dismiss_at(self, idx: int) -> None:
        """Dismiss the modal with the correct action tuple for the command at *idx*."""
        if idx < 0 or idx >= len(self._visible_commands):
            return
        cmd = self._visible_commands[idx]
        action = str(cmd.get("action", "run"))
        payload = str(cmd.get("payload", cmd.get("id", "")))
        self.dismiss((action, payload))

    def _run_selected(self) -> None:
        self._dismiss_at(self._selected_idx)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        await self._refresh_results()
        try:
            self.query_one("#palette-search", Input).focus()
        except Exception:
            pass

    # ── Event handlers ───────────────────────────────────────────────────────

    async def on_input_changed(self, event: Input.Changed) -> None:
        if (event.input.id or "") != "palette-search":
            return
        self._selected_idx = 0
        await self._refresh_results()

    async def on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.stop()
            self._move_selection(-1)
        elif event.key == "down":
            event.stop()
            self._move_selection(1)
        elif event.key == "enter":
            event.stop()
            self._run_selected()

    @on(Button.Pressed, ".palette-cmd")
    def on_palette_command_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("palette-cmd-"):
            return
        try:
            idx = int(bid[len("palette-cmd-"):])
        except ValueError:
            return
        self._dismiss_at(idx)
