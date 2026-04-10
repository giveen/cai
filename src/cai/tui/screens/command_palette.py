"""Command palette modal for the CAI TUI.

Supports three entry types:
  - ``type="app"``   — built-in TUI commands (clear, save, …)  → dismisses ("run", cmd_id)
  - ``type="tool"``  — CAI function tools with usage template  → dismisses ("fill", template)
  - ``type="agent"`` — available CAI agents                    → dismisses ("agent", agent_name)

Navigation:
  ↑/↓    move selection
  Enter  execute / fill selected entry
  Tab    cycle type filter: All → App Commands → Tools → Agents → All
  Esc    close
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, ScrollableContainer
from textual.screen import ModalScreen
from textual import events, on
from textual.widgets import Static, Button, Input

# ── Type metadata ────────────────────────────────────────────────────────────
_TYPE_COLOR: dict[str, str] = {
    "app":   "#00ff00",
    "tool":  "#00dd88",
    "agent": "#55aaff",
}
_TYPE_LABEL: dict[str, str] = {
    "app":   "App Commands",
    "tool":  "Tools",
    "agent": "Agents",
}
_TYPE_SORT_KEY: dict[str, int] = {"app": 0, "tool": 1, "agent": 2}
# Tab cycles through these filter states
_FILTER_CYCLE: list[str | None] = [None, "app", "tool", "agent"]


def _build_label(cmd: dict) -> str:
    """Format one palette row: name (fixed width, colored) · dim description."""
    name = str(cmd.get("name", cmd.get("id", "")))
    desc = str(cmd.get("description", ""))
    ctype = str(cmd.get("type", "app"))
    color = _TYPE_COLOR.get(ctype, "#00ff00")
    if len(desc) > 52:
        desc = desc[:49] + "…"
    return (
        f"[bold {color}]{name:<38}[/bold {color}]"
        f" [dim #005500]{desc}[/dim #005500]"
    )


class CommandPaletteModal(ModalScreen):
    """Ctrl+P command palette: fuzzy-search tools, agents, and app commands."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, commands: list[dict], recent: list[str]) -> None:
        super().__init__()
        self._commands = list(commands)
        self._recent = list(recent)
        self._selected_cmd_idx: int = 0
        self._visible_commands: list[dict] = []
        self._filter_type: str | None = None

    # ── Compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-wrap"):
            yield Static("", id="palette-title")
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
                "[dim #006600]Tab[/dim #006600] filter type  "
                "[dim #006600]Esc[/dim #006600] close",
                id="palette-help",
            )

    def _update_title(self) -> None:
        """Refresh the palette title to reflect the active type filter."""
        if self._filter_type is None:
            filter_str = (
                "[dim #006600]tools[/dim #006600] · "
                "[dim #006600]agents[/dim #006600] · "
                "[dim #006600]commands[/dim #006600]"
            )
        else:
            color = _TYPE_COLOR.get(self._filter_type, "#00ff00")
            label = _TYPE_LABEL.get(self._filter_type, self._filter_type.title())
            filter_str = (
                f"[bold {color}]{label}[/bold {color}]"
                "  [dim #004400](Tab to change)[/dim #004400]"
            )
        try:
            self.query_one("#palette-title", Static).update(
                f"[bold #00ff00]⌨  Command Palette[/bold #00ff00]  {filter_str}"
            )
        except Exception:
            pass

    # ── Fuzzy matching ────────────────────────────────────────────────────────

    def _fuzzy_score(self, query: str, text: str) -> int:
        """Return a positive match score, or -1 if query does not match."""
        q = query.lower().strip()
        t = text.lower()
        if not q:
            return 1
        if q in t:
            return 100 + len(q)
        score = 0
        pos = 0
        for ch in q:
            found = t.find(ch, pos)
            if found < 0:
                return -1
            score += 3 if found == pos else 1
            pos = found + 1
        return score

    # ── Result list management ────────────────────────────────────────────────

    async def _refresh_results(self) -> None:
        query = ""
        try:
            query = self.query_one("#palette-search", Input).value
        except Exception:
            pass

        ranked: list[tuple[int, dict]] = []
        for cmd in self._commands:
            # Apply type filter before scoring
            if self._filter_type and str(cmd.get("type", "app")) != self._filter_type:
                continue
            searchable = " ".join(
                str(cmd.get(k, ""))
                for k in ("id", "name", "description", "type", "category", "template")
            )
            score = self._fuzzy_score(query, searchable)
            if score < 0:
                continue
            try:
                recency = self._recent.index(str(cmd.get("id")))
                score += max(0, 20 - recency)
            except Exception:
                pass
            ranked.append((score, cmd))

        ranked.sort(
            key=lambda x: (
                -x[0],
                _TYPE_SORT_KEY.get(str(x[1].get("type", "app")), 9),
                str(x[1].get("name", "")),
            )
        )
        self._visible_commands = [cmd for _, cmd in ranked]
        self._selected_cmd_idx = 0
        self._update_title()

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
            await holder.mount(Static("  No matching results", classes="palette-empty"))
            return

        # Use section headers when showing multiple categories, or on initial load
        shown_types = list(dict.fromkeys(
            str(c.get("type", "app")) for c in self._visible_commands
        ))
        use_headers = len(shown_types) > 1 or (not query and self._filter_type is None)

        current_section: str | None = None
        for cmd_idx, cmd in enumerate(self._visible_commands):
            ctype = str(cmd.get("type", "app"))
            # Insert a section divider whenever the type group changes
            if use_headers and ctype != current_section:
                current_section = ctype
                color = _TYPE_COLOR.get(ctype, "#00ff00")
                label = _TYPE_LABEL.get(ctype, ctype.title())
                await holder.mount(
                    Static(
                        f" [bold {color}]── {label} ──[/bold {color}]",
                        classes="palette-section-header",
                    )
                )
            label_str = _build_label(cmd)
            btn = Button(label_str, id=f"palette-cmd-{cmd_idx}", classes="palette-cmd")
            if cmd_idx == self._selected_cmd_idx:
                btn.add_class("-selected")
            await holder.mount(btn)

    def _move_selection(self, delta: int) -> None:
        if not self._visible_commands:
            return
        old_idx = self._selected_cmd_idx
        self._selected_cmd_idx = (
            self._selected_cmd_idx + delta
        ) % len(self._visible_commands)
        try:
            self.query_one(f"#palette-cmd-{old_idx}").remove_class("-selected")
        except Exception:
            pass
        try:
            new_btn = self.query_one(f"#palette-cmd-{self._selected_cmd_idx}")
            new_btn.add_class("-selected")
            try:
                new_btn.scroll_visible(animate=False)
            except Exception:
                pass
        except Exception:
            pass

    # ── Dismiss helpers ───────────────────────────────────────────────────────

    def _dismiss_at(self, cmd_idx: int) -> None:
        if cmd_idx < 0 or cmd_idx >= len(self._visible_commands):
            return
        cmd = self._visible_commands[cmd_idx]
        action = str(cmd.get("action", "run"))
        payload = str(cmd.get("payload", cmd.get("id", "")))
        self.dismiss((action, payload))

    def _run_selected(self) -> None:
        self._dismiss_at(self._selected_cmd_idx)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        self._update_title()
        await self._refresh_results()
        try:
            self.query_one("#palette-search", Input).focus()
        except Exception:
            pass

    # ── Event handlers ────────────────────────────────────────────────────────

    async def on_input_changed(self, event: Input.Changed) -> None:
        if (event.input.id or "") != "palette-search":
            return
        self._selected_cmd_idx = 0
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
        elif event.key == "tab":
            event.stop()
            cur = _FILTER_CYCLE.index(self._filter_type)
            self._filter_type = _FILTER_CYCLE[(cur + 1) % len(_FILTER_CYCLE)]
            self._selected_cmd_idx = 0
            await self._refresh_results()

    @on(Button.Pressed, ".palette-cmd")
    def on_palette_command_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("palette-cmd-"):
            return
        try:
            cmd_idx = int(bid[len("palette-cmd-"):])
        except ValueError:
            return
        self._dismiss_at(cmd_idx)
