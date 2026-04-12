"""WindowsStream — live RDP session display widget for the CAI TUI.

Renders incoming events from the headless RDP executor (rdp_headless.py) in a
blue-on-black panel, visually distinct from the green-on-black Kali terminals.

Event model
-----------
External code (e.g. ``rdp_headless.rdp_exec``) calls :func:`emit_rdp_event`
with a plain dict.  The widget's background drain-loop renders them as they
arrive.

Supported event types
~~~~~~~~~~~~~~~~~~~~~
  ``connect``       – new RDP session; carries ``target`` / ``ip``
  ``disconnect``    – session closed; carries ``target``
  ``command``       – command started; carries ``cmd``
  ``output``        – command stdout; carries ``text``
  ``status``        – informational message; carries ``text``
  ``error``         – error message; carries ``text``
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, Static

# ---------------------------------------------------------------------------
# Module-level event queue — initialised when the widget mounts.
# emit_rdp_event() is safe to call from any coroutine in the same event loop.
# ---------------------------------------------------------------------------

_RDP_EVENT_QUEUE: asyncio.Queue | None = None  # type: ignore[type-arg]


def emit_rdp_event(event: dict) -> None:
    """Put *event* onto the WindowsStream queue.

    If no ``WindowsStream`` widget is currently mounted (TUI not running),
    the event is silently discarded so headless callers are unaffected.
    """
    global _RDP_EVENT_QUEUE
    if _RDP_EVENT_QUEUE is None:
        return
    try:
        _RDP_EVENT_QUEUE.put_nowait(event)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------


class WindowsStream(Widget):
    """Blue-on-black panel that streams live RDP session activity.

    Visibility is driven by the ``panel_visible`` reactive.  When a
    ``connect`` event arrives the widget makes itself visible automatically;
    a ``disconnect`` event does the reverse.
    """

    panel_visible: reactive[bool] = reactive(False)

    # Injected by the Textual runtime at mount time; typed for static analysis.
    app: Any

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #4488ff]■[/bold #4488ff] [#0066ff]Windows RDP[/#0066ff]",
            id="win-stream-header",
        )
        yield RichLog(
            id="win-stream-log",
            highlight=False,
            markup=True,
            wrap=True,
            auto_scroll=True,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        global _RDP_EVENT_QUEUE
        _RDP_EVENT_QUEUE = asyncio.Queue()
        self._start_drain()

    def on_unmount(self) -> None:
        global _RDP_EVENT_QUEUE
        _RDP_EVENT_QUEUE = None

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _log(self, markup: str) -> None:
        """Append a markup-formatted line to the RichLog."""
        try:
            log = self.query_one("#win-stream-log", RichLog)
            log.write(markup)
        except Exception:
            pass

    def _update_header(self, target: str, connected: bool) -> None:
        """Update the title bar to reflect current connection state."""
        try:
            header = self.query_one("#win-stream-header", Static)
            if connected:
                header.update(
                    f"[bold #4488ff]■[/bold #4488ff] "
                    f"[#0066ff]Windows RDP[/#0066ff] "
                    f"[#003399]►[/#003399] "
                    f"[bold #88bbff]{target}[/bold #88bbff] "
                    f"[#00aa44]●[/#00aa44]"
                )
            else:
                header.update(
                    f"[bold #4488ff]■[/bold #4488ff] "
                    f"[#0066ff]Windows RDP[/#0066ff] "
                    f"[#003399]►[/#003399] "
                    f"[#666688]{target}[/#666688] "
                    f"[#aa0000]○[/#aa0000]"
                )
        except Exception:
            pass

    def _render_event(self, event: dict) -> None:
        """Render a single event dict onto the RichLog."""
        kind = event.get("type", "")
        ts = datetime.now().strftime("%H:%M:%S")

        if kind == "connect":
            target = event.get("target", "?")
            ip = event.get("ip", "")
            ip_str = (
                f" [#003399]({ip})[/#003399]"
                if ip and ip != target
                else ""
            )
            self._update_header(target, connected=True)
            self.panel_visible = True
            self._log(
                f"[#004488][{ts}][/#004488] "
                f"[#00aa44]CONNECTED[/#00aa44] "
                f"[#88bbff]{target}[/#88bbff]{ip_str}"
            )

        elif kind == "disconnect":
            target = event.get("target", "?")
            self._update_header(target, connected=False)
            self._log(
                f"[#004488][{ts}][/#004488] "
                f"[#aa4400]DISCONNECTED[/#aa4400] "
                f"[#aaaacc]{target}[/#aaaacc]"
            )

        elif kind == "command":
            cmd = event.get("cmd", "")
            self._log(
                f"[#004488][{ts}][/#004488] "
                f"[#0066ff]CMD[/#0066ff] "
                f"[#ffffff]> {cmd}[/#ffffff]"
            )

        elif kind == "output":
            text = (event.get("text") or "").rstrip()
            if text:
                for line in text.splitlines():
                    self._log(
                        f"[#336699]     [/#336699][#aaccff]{line}[/#aaccff]"
                    )

        elif kind == "status":
            text = event.get("text", "")
            self._log(
                f"[#004488][{ts}][/#004488] "
                f"[#446688]…[/#446688] "
                f"[#5577aa]{text}[/#5577aa]"
            )

        elif kind == "error":
            text = event.get("text", "")
            self._log(
                f"[#004488][{ts}][/#004488] "
                f"[#ff4444]ERROR[/#ff4444] "
                f"[#ff8888]{text}[/#ff8888]"
            )

    # ------------------------------------------------------------------
    # Visibility reactive
    # ------------------------------------------------------------------

    def watch_panel_visible(self, visible: bool) -> None:
        """Animate the panel in/out when visibility changes."""
        if visible:
            self.add_class("-visible")
            self.styles.opacity = 0.0
            try:
                self.animate(
                    "styles.opacity",
                    value=1.0,
                    duration=0.35,
                    easing="out_cubic",
                )
            except Exception:
                self.styles.opacity = 1.0
        else:
            if self.has_class("-visible"):
                def _hide() -> None:
                    try:
                        self.remove_class("-visible")
                        self.styles.opacity = 1.0
                    except Exception:
                        pass

                try:
                    self.animate(
                        "styles.opacity",
                        value=0.0,
                        duration=0.25,
                        easing="in_cubic",
                        on_complete=_hide,
                    )
                except Exception:
                    self.remove_class("-visible")
            else:
                self.remove_class("-visible")

    # ------------------------------------------------------------------
    # Background event drain
    # ------------------------------------------------------------------

    @work(exclusive=True, name="win-stream-drain")
    async def _start_drain(self) -> None:
        """Background worker: drain the RDP event queue and render events."""
        global _RDP_EVENT_QUEUE
        try:
            while True:
                q = _RDP_EVENT_QUEUE
                if q is None:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                    self._render_event(event)
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    return
                except Exception:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return
