"""A minimal Textual-based TUI prototype for CAI.

This module exports `run_tui(agent, initial_prompt=None)` which will try
to start a Textual app. If Textual is not installed or fails to start,
it will fall back to a simple Rich `Live` panel so the feature can be
used for experimentation without hard dependency failures.

The implementation is intentionally small and non-invasive: it does not
import Textual at module-import time (only inside the function) so the
rest of the application is unaffected when the TUI is unused.
"""
from __future__ import annotations

import time
from typing import Optional


def run_tui(agent, initial_prompt: Optional[str] = None) -> bool | None:
    """Start a minimal TUI.

    Returns True/None on success, False on explicit failure to start any UI.
    """
    # Try Textual first (non-fatal import)
    try:
        from textual.app import App
        from textual.widgets import Header, Footer, ScrollView

        class CaiTUI(App):
            async def on_mount(self) -> None:  # type: ignore[override]
                header = Header()
                footer = Footer()
                self.body = ScrollView()

                # Dock header/footer and main scrolling body
                await self.view.dock(header, edge="top")
                await self.view.dock(footer, edge="bottom")
                await self.view.dock(self.body, edge="left")

                # Seed content
                await self.body.update("[bold]CAI Textual TUI[/bold]\n\nStreaming output will appear here...\n")

                # Start a small timer to simulate streaming updates so the
                # developer can see the live panel behavior.
                self.set_interval(0.5, self._tick)

            def _tick(self) -> None:
                import datetime

                try:
                    new_line = f"[{datetime.datetime.now().isoformat()}] · heartbeat\n"

                    # Append new content to the body
                    current = ""
                    try:
                        # ScrollView.update expects a renderable; use string
                        current = self.body.renderable or ""
                    except Exception:
                        current = ""

                    # Use call_later to schedule UI update from the timer
                    self.call_later(lambda: self.body.update(str(current) + new_line))
                except Exception:
                    # Keep TUI resilient to intermittent update errors
                    pass

        # Launch the Textual app (blocks until exit)
        CaiTUI().run()
        return None

    except Exception:
        # If Textual isn't available or fails at runtime, fall back to a
        # simple Rich Live panel so users may still experiment with a
        # terminal-based streaming display.
        try:
            from rich.live import Live
            from rich.panel import Panel
            from rich.console import Console

            console = Console()
            with Live(Panel("CAI TUI (fallback) - starting..."), refresh_per_second=4, console=console) as live:
                for i in range(20):
                    time.sleep(0.25)
                    live.update(Panel(f"Streaming line {i}"))
            return True
        except Exception:
            print("Textual and Rich live fallbacks failed; cannot start TUI.")
            return False
