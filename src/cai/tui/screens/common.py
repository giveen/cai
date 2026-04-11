"""Common small modal screens used throughout the CAI TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual import on
from textual.widgets import Static, Button, Input

from cai.tui.components.header import _pretty_name


# ---------------------------------------------------------------------------
# Agent-selection modal
# ---------------------------------------------------------------------------
class AgentModal(ModalScreen):
    """Pop-up shown when the user clicks an agent button.

    Dismissed with:
      ('update', agent_name)  – re-assign the current active terminal
      ('new',    agent_name)  – open a new terminal panel for this agent
      None                    – cancelled
    """

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, agent_name: str, active_term_label: str, at_max: bool = False) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._active_term_label = active_term_label
        self._at_max = at_max

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(
                f"Agent: [bold]{_pretty_name(self._agent_name)}[/bold]",
                id="modal-agent-label",
            )
            yield Button(
                f"Update {self._active_term_label}",
                id="modal-update",
                classes="modal-btn",
            )
            yield Button(
                "New Terminal" if not self._at_max else "New Terminal (max 4 reached)",
                id="modal-new",
                classes="modal-btn" + (" modal-btn--cancel" if self._at_max else ""),
                disabled=self._at_max,
            )
            yield Button(
                "Cancel",
                id="modal-cancel",
                classes="modal-btn modal-btn--cancel",
            )

    @on(Button.Pressed, "#modal-update")
    def on_modal_update(self, event: Button.Pressed) -> None:
        self.dismiss(("update", self._agent_name))

    @on(Button.Pressed, "#modal-new")
    def on_modal_new(self, event: Button.Pressed) -> None:
        self.dismiss(("new", self._agent_name))

    @on(Button.Pressed, "#modal-cancel")
    def on_modal_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Small prompt modal used for rename/export inputs
# ---------------------------------------------------------------------------
class PromptModal(ModalScreen):
    """Simple input modal. Returns the entered string, or None if cancelled."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, prompt: str, default: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._prompt, id="modal-agent-label")
            yield Input(value=self._default, id="prompt-input")
            yield Button("OK", id="prompt-ok", classes="modal-btn")
            yield Button("Cancel", id="prompt-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#prompt-ok")
    def on_prompt_ok(self, event: Button.Pressed) -> None:
        try:
            val = self.query_one("#prompt-input", Input).value
        except Exception:
            val = self._default
        self.dismiss(val)

    @on(Button.Pressed, "#prompt-cancel")
    def on_prompt_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Simple confirmation modal. Returns True if confirmed, else None."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._message, id="modal-agent-label")
            yield Button("Delete", id="confirm-ok", classes="modal-btn modal-btn--cancel")
            yield Button("Cancel", id="confirm-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#confirm-ok")
    def on_confirm_ok(self, event: Button.Pressed) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-cancel")
    def on_confirm_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ConfigModal(ModalScreen):
    """Modal to confirm opening a Config section. Returns ('open', action_key) or None."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, action_key: str, display_label: str) -> None:
        super().__init__()
        self._action_key = action_key
        self._display = display_label

    def compose(self) -> ComposeResult:
        # Mark this dialog as the config modal so we can animate it on mount
        with Vertical(id="modal-dialog", classes="modal-config"):
            yield Static(f"Open config: [bold]{self._display}[/bold]", id="modal-agent-label")
            yield Button("Open", id="config-open", classes="modal-btn")
            yield Button("Cancel", id="config-cancel", classes="modal-btn modal-btn--cancel")

    def on_mount(self) -> None:
        """Trigger a slide-in transition from the right for the Config modal.

        We add a transient class after mount so the CSS transition runs.
        """
        try:
            dlg = self.query_one("#modal-dialog")

            # Small call_later to allow the initial mount styles to be applied
            def _add_slide() -> None:
                try:
                    dlg.add_class("-slide-in")
                except Exception:
                    pass

            try:
                # Use call_later so the transition occurs after layout
                self.call_later(_add_slide)
            except Exception:
                _add_slide()
        except Exception:
            pass

    @on(Button.Pressed, "#config-open")
    def on_config_open(self, event: Button.Pressed) -> None:
        self.dismiss(("open", self._action_key))

    @on(Button.Pressed, "#config-cancel")
    def on_config_cancel(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ContextUsageModal(ModalScreen):
    """Context usage menu modal.

    Returns one of:
      ("refresh",)
      ("copy", summary_text)
      ("inject", summary_text)
      ("jump_metrics",)
      None
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, title: str, content: str, summary_text: str) -> None:
        super().__init__()
        self._title = title
        self._content = content
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._title, id="modal-agent-label")
            yield Static(self._content)
            with Horizontal():
                yield Button("Refresh", id="ctx-refresh", classes="modal-btn")
                yield Button("Copy To Input", id="ctx-copy", classes="modal-btn")
                yield Button("Inject Command", id="ctx-inject", classes="modal-btn")
            with Horizontal():
                yield Button("Jump Metrics", id="ctx-jump", classes="modal-btn")
                yield Button("Close", id="ctx-close", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed, "#ctx-refresh")
    def on_ctx_refresh(self, event: Button.Pressed) -> None:
        self.dismiss(("refresh",))

    @on(Button.Pressed, "#ctx-copy")
    def on_ctx_copy(self, event: Button.Pressed) -> None:
        self.dismiss(("copy", self._summary_text))

    @on(Button.Pressed, "#ctx-inject")
    def on_ctx_inject(self, event: Button.Pressed) -> None:
        self.dismiss(("inject", self._summary_text))

    @on(Button.Pressed, "#ctx-jump")
    def on_ctx_jump(self, event: Button.Pressed) -> None:
        self.dismiss(("jump_metrics",))

    @on(Button.Pressed, "#ctx-close")
    def on_ctx_close(self, event: Button.Pressed) -> None:
        self.dismiss(None)
