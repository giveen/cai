"""TerminalGrid — flexible grid container for TerminalPanel widgets.

Layout rules driven by ``agent_count`` reactive:

* **1 agent** — single panel spans both columns (full width).
* **2 agents** — two panels side-by-side in one row (1 column each).
* **3-4 agents** — standard 2 × N grid (1 column each, rows auto).

A CSS ``transition: width 300ms linear;`` on ``TerminalPanel`` children
ensures panels animate smoothly whenever a terminal is added or removed.

Active-selection features:
* ``focus_next_panel()`` / ``focus_prev_panel()`` — cycle the active
  ``.-focused-terminal`` marker across mounted panels.
* ``toggle_maximize(term_id)`` — overlays a single panel over the grid
  using ``layer`` / ``offset`` / ``width`` / ``height``; calling it again
  restores the original layout.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget

from cai.tui.components.terminal import TerminalPanel


class TerminalGrid(Widget):
    """Auto-sizing 2-column grid that adjusts spans based on panel count."""

    DEFAULT_CSS = """
    TerminalGrid {
        layout: grid;
        grid-size: 2;
        height: 1fr;
        background: #000000;
    }

    TerminalGrid TerminalPanel {
        height: 1fr;
        transition: width 300ms linear;
    }

    TerminalGrid TerminalPanel.-full-width {
        column-span: 2;
    }
    """

    # Reactive agent count — changing it triggers watch_agent_count which
    # re-applies column spans to all children without remounting anything.
    agent_count: reactive[int] = reactive(0, layout=True)

    # ── Internal state ───────────────────────────────────────────────────────

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # term_id of the currently maximised panel, or None.
        self._maximised_id: int | None = None

    # ── Composition ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Panels are mounted dynamically; nothing to compose statically."""
        return iter([])

    # ── Reactive watcher ─────────────────────────────────────────────────────

    def watch_agent_count(self, count: int) -> None:
        """Recompute column spans whenever the number of panels changes."""
        panels = list(self.query(TerminalPanel))
        if count <= 1:
            # Single terminal takes full width.
            for panel in panels:
                panel.add_class("-full-width")
        else:
            # 2, 3, or 4 terminals: each occupies one column (auto rows).
            for panel in panels:
                panel.remove_class("-full-width")

    # ── Focus cycling ────────────────────────────────────────────────────────

    def _panels_ordered(self) -> list[TerminalPanel]:
        """Return panels sorted by term_id for deterministic cycling."""
        return sorted(self.query(TerminalPanel), key=lambda p: p._term_id)

    def _focused_index(self) -> int:
        """Return the list index of the currently .-focused-terminal panel, or -1."""
        for idx, panel in enumerate(self._panels_ordered()):
            if panel.has_class("-focused-terminal"):
                return idx
        return -1

    def set_focused_panel(self, term_id: int) -> None:
        """Apply .-focused-terminal to the panel with *term_id*; clear all others."""
        for panel in self.query(TerminalPanel):
            if panel._term_id == term_id:
                panel.add_class("-focused-terminal")
            else:
                panel.remove_class("-focused-terminal")

    def focus_next_panel(self) -> int | None:
        """Move .-focused-terminal to the next panel and return its term_id."""
        panels = self._panels_ordered()
        if not panels:
            return None
        idx = self._focused_index()
        next_idx = (idx + 1) % len(panels)
        self.set_focused_panel(panels[next_idx]._term_id)
        return panels[next_idx]._term_id

    def focus_prev_panel(self) -> int | None:
        """Move .-focused-terminal to the previous panel and return its term_id."""
        panels = self._panels_ordered()
        if not panels:
            return None
        idx = self._focused_index()
        prev_idx = (idx - 1) % len(panels)
        self.set_focused_panel(panels[prev_idx]._term_id)
        return panels[prev_idx]._term_id

    # ── Maximize / restore ───────────────────────────────────────────────────

    def toggle_maximize(self, term_id: int) -> bool:
        """Toggle the overlay-maximise state for the panel with *term_id*.

        When maximised the panel receives ``.-maximised`` which sets
        ``offset: 0 0; width: 100%; height: 100%;`` via CSS, floating it
        above all siblings.  Returns ``True`` when maximised, ``False``
        when restored.
        """
        try:
            panel = self.query_one(f"#terminal-panel-{term_id}", TerminalPanel)
        except Exception:
            return False

        if self._maximised_id == term_id:
            # ── Restore ──
            panel.remove_class("-maximised")
            # Re-show every sibling that was hidden during maximise.
            for sib in self.query(TerminalPanel):
                sib.display = True
            self._maximised_id = None
            return False
        else:
            # ── Maximise ──
            # If another panel was already maximised, restore it first.
            if self._maximised_id is not None:
                try:
                    old = self.query_one(f"#terminal-panel-{self._maximised_id}", TerminalPanel)
                    old.remove_class("-maximised")
                    old.display = True
                except Exception:
                    pass
            # Hide every sibling so the layered panel really covers everything.
            for sib in self.query(TerminalPanel):
                if sib._term_id != term_id:
                    sib.display = False
            panel.add_class("-maximised")
            self._maximised_id = term_id
            return True

    # ── Public API ───────────────────────────────────────────────────────────

    async def add_panel(self, panel: TerminalPanel) -> None:
        """Mount *panel* into the grid and recompute column spans."""
        await self.mount(panel)
        self.agent_count = len(list(self.query(TerminalPanel)))
        # First panel automatically receives focus highlight.
        if self.agent_count == 1:
            panel.add_class("-focused-terminal")

    async def remove_panel(self, term_id: int) -> None:
        """Remove the panel with *term_id* and recompute column spans.

        The count is updated via ``call_after_refresh`` so the DOM reflects
        the removal before ``watch_agent_count`` queries it.
        """
        if self._maximised_id == term_id:
            self.toggle_maximize(term_id)  # restore before removing
        try:
            panel = self.query_one(f"#terminal-panel-{term_id}", TerminalPanel)
            panel.remove()
        except Exception:
            pass
        # Defer recount until the removed widget has left the DOM.
        self.call_after_refresh(self._sync_count)

    def _sync_count(self) -> None:
        """Update agent_count to match the live panel count in the DOM."""
        count = len(list(self.query(TerminalPanel)))
        if self.agent_count != count:
            self.agent_count = count

    @property
    def panel_count(self) -> int:
        """Live count of mounted TerminalPanel children."""
        return len(list(self.query(TerminalPanel)))
