"""TerminalPanel component extracted from cai.tui.app.

This file contains the `TerminalPanel` widget originally defined in
`src/cai/tui/app.py`. It imports a small set of helpers from the app
module to avoid duplicating display strings.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import textwrap
from datetime import datetime
from typing import Any, cast

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text as RichText
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import LoadingIndicator, RichLog, Static
from textual.widgets._text_area import TextArea

# Import small helpers from the dedicated header module. This avoids
# depending on the large `app.py` during imports and centralises banner
# presentation/formatting for reuse across components.
from cai.tui.components.header import _pretty_name, _BANNER_LINES

import logging
from cai.tools.common import _should_skip_recon

logger = logging.getLogger(__name__)


class TerminalPanel(Widget):
    """Self-contained chat terminal: header bar + RichLog + status + input."""

    # Expose the `app` attribute as `Any` for static analysis — the
    # Textual runtime injects the `app` reference at mount time.
    app: Any

    # Sent to the App when this panel is clicked so the app can mark it active
    class Activated(Message):
        def __init__(self, term_id: int) -> None:
            super().__init__()
            self.term_id = term_id

    # Sent when the close button is clicked
    class CloseRequested(Message):
        def __init__(self, term_id: int) -> None:
            super().__init__()
            self.term_id = term_id

    def __init__(
        self,
        term_id: int,
        agent,
        agent_name: str,
        model_name: str,
    ) -> None:
        super().__init__(id=f"terminal-panel-{term_id}")
        self._term_id = term_id
        self._agent = agent
        self._agent_name = agent_name
        self._model_name = model_name
        self._tool_outputs_by_call_id: dict[str, str] = {}
        self._active_tool_calls: dict[str, dict] = {}
        self._busy: bool = False
        self._last_prompt_text: str = ""
        self._run_worker = None
        self._workers: list[Any] = []
        self._prompt_history: list[str] = []
        self._history_index: int | None = None
        self._last_input_text: str = ""
        self._suppress_next_enter_submit: bool = False

    def _get_input_widget(self):
        try:
            return self.query_one(f"#term-input-{self._term_id}", TextArea)
        except Exception:
            return None

    def _set_input_text(self, text: str) -> None:
        inp = self._get_input_widget()
        if inp is None:
            return
        try:
            if hasattr(inp, "load_text"):
                inp.load_text(text)
            else:
                cast(Any, inp).value = text
            self._last_input_text = str(text or "")
        except Exception:
            pass

    def _get_input_text(self) -> str:
        inp = self._get_input_widget()
        if inp is None:
            return ""
        try:
            if hasattr(inp, "text"):
                return str(inp.text)
            return str(cast(Any, inp).value)
        except Exception:
            return ""

    def _infer_input_language(self, text: str) -> str:
        raw = str(text or "")
        first = raw.strip().splitlines()[0] if raw.strip() else ""
        m = re.match(r"^```([A-Za-z0-9_+-]+)", first)
        if m:
            return m.group(1).lower()
        if re.search(r"\b(def|class|import|from|async|await)\b", raw):
            return "python"
        if re.search(r"\b(function|const|let|var|=>)\b", raw):
            return "javascript"
        return "markdown"

    def _resize_input_for_text(self, text: str) -> None:
        lines = max(1, len(str(text or "").splitlines()))
        visible_lines = min(8, max(1, lines))
        input_height = visible_lines + 1
        row_height = input_height + 1
        try:
            inp = self._get_input_widget()
            if inp is not None:
                inp.styles.height = input_height
                try:
                    inp.scroll_end(animate=False)
                except Exception:
                    pass
            row = self.query_one(".term-input-row", Horizontal)
            row.styles.height = row_height
            prefix = self.query_one(".term-input-prefix", Static)
            prefix.styles.height = row_height
        except Exception:
            pass

    def _update_input_meta(self, text: str) -> None:
        count = len(str(text or ""))
        mode = "multi-line" if "\n" in str(text or "") else "single-line"
        color = "#006600"
        if count >= 3000:
            color = "#ff6666"
        elif count >= 1200:
            color = "#ffcc66"
        try:
            self.query_one(f"#term-input-meta-{self._term_id}", Static).update(
                f"[{color}]{count} chars · {mode} · Enter send · Shift+Enter newline · Ctrl+Enter send multiline · Ctrl+U clear · Up/Down history[/{color}]"
            )
        except Exception:
            pass

    async def _submit_from_input_widget(self) -> None:
        text = self._get_input_text().strip()
        if not text:
            return

        if not self._prompt_history or self._prompt_history[-1] != text:
            self._prompt_history.append(text)
            if len(self._prompt_history) > 200:
                self._prompt_history = self._prompt_history[-200:]
        self._history_index = None

        self._set_input_text("")
        self._resize_input_for_text("")
        self._update_input_meta("")
        # Safely call CAIApp-specific helpers on the textual App instance
        app_obj = getattr(self, "app", None)
        if app_obj and hasattr(app_obj, "_parse_broadcast_suffix"):
            try:
                msg, is_broadcast = cast(Any, app_obj)._parse_broadcast_suffix(text)
            except Exception:
                msg, is_broadcast = text, False
        else:
            msg, is_broadcast = text, False

        if is_broadcast and msg:
            if app_obj and hasattr(app_obj, "_broadcast_prompt"):
                try:
                    await cast(Any, app_obj)._broadcast_prompt(msg, source_tid=self._term_id)
                except Exception:
                    await self.dispatch(text)
            else:
                await self.dispatch(text)
        else:
            await self.dispatch(text)

    def _history_nav(self, direction: int) -> bool:
        if not self._prompt_history:
            return False
        if self._history_index is None:
            self._history_index = len(self._prompt_history)

        self._history_index = max(
            0, min(len(self._prompt_history), self._history_index + direction)
        )
        if self._history_index >= len(self._prompt_history):
            self._set_input_text("")
            self._update_input_meta("")
            return True

        self._set_input_text(self._prompt_history[self._history_index])
        self._resize_input_for_text(self._prompt_history[self._history_index])
        self._update_input_meta(self._prompt_history[self._history_index])
        return True

    def _set_visual_state(self, state: str) -> None:
        """Apply CSS classes and input behavior for terminal visual states."""
        normalized = state if state in {"ready", "busy", "error"} else "ready"
        self.remove_class("-busy-panel")
        self.remove_class("-error-panel")
        if normalized == "busy":
            self.add_class("-busy-panel")
        elif normalized == "error":
            self.add_class("-error-panel")
        try:
            inp = self.query_one(f"#term-input-{self._term_id}", TextArea)
            inp.disabled = normalized == "busy"
        except Exception:
            pass

    def cancel_active_run(self) -> bool:
        """Cancel the in-flight run if present and return whether cancel occurred."""
        cancelled = False
        try:
            if self._run_worker is not None:
                self._run_worker.cancel()
                cancelled = True
        except Exception:
            pass
        if not cancelled:
            try:
                for worker in list(self._workers):
                    worker.cancel()
                    cancelled = True
            except Exception:
                pass
        if cancelled:
            self._busy = False
            self._set_visual_state("ready")
            self._set_status(f"T{self._term_id}> cancelled")
            self._write_system_message("progress", "run cancelled (Ctrl+C / Esc)", style="#ffaa00")
        return cancelled

    def _write_system_message(self, msg_type: str, text: str, style: str = "#00aa00") -> None:
        try:
            log = self.query_one(f"#term-log-{self._term_id}", RichLog)
            log.write(RichText(f"[system/{msg_type}] {text}", style=style))
        except Exception:
            pass

    def _render_structured_json(self, payload: str) -> bool:
        try:
            obj = json.loads(payload)
        except Exception:
            return False

        try:
            log = self.query_one(f"#term-log-{self._term_id}", RichLog)
            if isinstance(obj, dict):
                table = Table(
                    title="Structured Output", show_header=True, header_style="bold #00ff00"
                )
                table.add_column("Key", style="#00cc00")
                table.add_column("Value", style="#00ff00")
                for key, value in list(obj.items())[:30]:
                    table.add_row(str(key), json.dumps(value, ensure_ascii=True)[:220])
                log.write(table)
                return True
            if isinstance(obj, list):
                table = Table(
                    title=f"Structured Output ({len(obj)} items)",
                    show_header=True,
                    header_style="bold #00ff00",
                )
                table.add_column("Index", style="#00cc00")
                table.add_column("Value", style="#00ff00")
                for idx, value in enumerate(obj[:30]):
                    table.add_row(str(idx), json.dumps(value, ensure_ascii=True)[:220])
                log.write(table)
                return True
        except Exception:
            return False
        return False

    def _render_agent_message(self, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        log = self.query_one(f"#term-log-{self._term_id}", RichLog)

        # Prefer structured table rendering when message is pure JSON.
        if self._render_structured_json(text):
            return

        # Detect fenced code blocks and syntax-highlight when present.
        code_match = re.search(r"```(\w+)?\n([\s\S]*?)```", text)
        if code_match:
            lang = (code_match.group(1) or "text").strip()
            code = code_match.group(2)
            prefix = text[: code_match.start()].strip()
            suffix = text[code_match.end() :].strip()
            if prefix:
                log.write(Markdown(prefix))
            log.write(Syntax(code, lang, line_numbers=False, word_wrap=True))
            if suffix:
                log.write(Markdown(suffix))
            return

        # Fallback to markdown render for rich formatting and tables.
        # Wrap long lines to avoid overflowing the terminal panel width and then
        # prefer Markdown rendering; fall back to plain RichText if Markdown fails.
        try:
            size = getattr(self, "size", None) or getattr(self.app, "size", None)
            w = int(getattr(size, "width", 0) or 0)
            wrap_width = max(40, w - 8) if w > 0 else 80
        except Exception:
            wrap_width = 80
        wrapped = self._wrap_text_for_width(text, wrap_width)
        try:
            log.write(Markdown(wrapped))
        except Exception:
            try:
                log.write(RichText(wrapped))
            except Exception:
                log.write(wrapped)

    def _format_tool_output_preview(self, output: str) -> tuple[str, bool]:
        text = str(output or "")
        lines = text.splitlines()
        max_lines = 14
        max_chars = 1200
        collapsed = False

        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines])
            collapsed = True
        if len(text) > max_chars:
            text = text[:max_chars]
            collapsed = True
        # Wrap long lines to avoid overflowing the terminal panel width.
        try:
            size = getattr(self, "size", None) or getattr(self.app, "size", None)
            w = int(getattr(size, "width", 0) or 0)
            wrap_width = max(40, w - 8) if w > 0 else 80
        except Exception:
            wrap_width = 80
        text = self._wrap_text_for_width(text, wrap_width)
        return text, collapsed

    def _wrap_text_for_width(self, text: str, width: int) -> str:
        """Return text with long lines wrapped to `width` while preserving indentation.

        Uses textwrap.fill with break_long_words so extremely long tokens still break
        and won't overflow the panel box.
        """
        if not text:
            return text
        out_lines: list[str] = []
        for line in str(text).splitlines():
            if not line.strip():
                out_lines.append(line)
                continue
            # preserve leading indentation
            leading = len(line) - len(line.lstrip(" "))
            indent = " " * leading
            body = line.lstrip(" ")
            try:
                wrapped = textwrap.fill(
                    body,
                    width=max(20, width - leading),
                    replace_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=True,
                )
            except Exception:
                wrapped = body
            # reapply indentation to each wrapped sub-line
            wrapped = "\n".join(indent + ln for ln in wrapped.splitlines())
            out_lines.append(wrapped)
        return "\n".join(out_lines)

    def _extract_stream_text_delta(self, raw_data) -> str:
        data = raw_data
        if hasattr(data, "model_dump"):
            try:
                data = data.model_dump()
            except Exception:
                pass

        if isinstance(data, dict):
            event_type = str(data.get("type", ""))
            if "output_text" in event_type and "delta" in event_type:
                return str(data.get("delta", "") or "")
            if "delta" in data and isinstance(data.get("delta"), str):
                return str(data.get("delta") or "")
            return ""

        event_type = str(getattr(data, "type", ""))
        if "output_text" in event_type and "delta" in event_type:
            return str(getattr(data, "delta", "") or "")
        return str(getattr(data, "delta", "") or "")

    # header text helper
    def _header_text(self) -> str:
        display = _pretty_name(self._agent_name)
        return (
            f"[bold #00ff00]T{self._term_id}[/bold #00ff00]"
            f"[#004400] | [/#004400]"
            f"[#00cc00]{display}[/#00cc00]"
            f"[#004400] ▼  [/#004400]"
            f"[#00aa00]{self._model_name}[/#00aa00]"
            f"[#004400] ▼  container ▼  [/#004400]"
            f"[bold #00ff00]●[/bold #00ff00]"
            f"  [dim #666600]×[/dim #666600]"
        )

    def compose(self) -> ComposeResult:
        yield Static(
            self._header_text(),
            id=f"term-header-{self._term_id}",
            classes="term-header",
        )
        yield RichLog(
            id=f"term-log-{self._term_id}",
            classes="term-log",
            highlight=False,
            markup=True,
            wrap=True,
        )
        yield Static("", id=f"term-status-{self._term_id}", classes="term-status")
        yield LoadingIndicator(
            id=f"term-loading-{self._term_id}",
            classes="term-loading",
        )
        with Horizontal(classes="term-input-row"):
            yield Static("CAI>", classes="term-input-prefix")
            with Vertical(classes="term-input-column"):
                yield TextArea(
                    text="",
                    language="markdown",
                    soft_wrap=True,
                    show_line_numbers=False,
                    compact=True,
                    placeholder="Type a prompt… Enter=send | Shift+Enter=new line | Ctrl+Enter=send multiline | Ctrl+U=clear | Up/Down=history",
                    id=f"term-input-{self._term_id}",
                    classes="term-input",
                )
                yield Static(
                    "0 chars · single-line",
                    id=f"term-input-meta-{self._term_id}",
                    classes="term-input-meta",
                )

    async def on_mount(self) -> None:
        from rich.text import Text as RichText

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        self._set_visual_state("ready")
        for line in _BANNER_LINES:
            log.write(RichText(line, style="#00ff00"))
        log.write(RichText("", style=""))
        log.write(
            RichText(
                f"T{self._term_id} ready — {_pretty_name(self._agent_name)}",
                style="#006600",
            )
        )
        self._write_system_message(
            "init",
            f"agent={self._agent_name} model={self._model_name}",
            style="#0088aa",
        )
        log.write(RichText("", style=""))
        self._resize_input_for_text("")
        self._update_input_meta("")

    @on(TextArea.Changed)
    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if (event.text_area.id or "") != f"term-input-{self._term_id}":
            return
        text = str(getattr(event.text_area, "text", "") or "")
        prev_text = self._last_input_text
        self._last_input_text = text

        # ── Enter-key detection ───────────────────────────────────────────────
        # In Textual 8+, TextArea._on_key intercepts ALL printable characters
        # (including Enter) with event.stop() + event.prevent_default() so the
        # key event NEVER bubbles to TerminalPanel.on_key.  For Enter it inserts
        # '\n' into the document before stopping propagation.
        #
        # We detect the resulting document state: text ends with exactly one '\n'
        # and has NO embedded '\n' (was single-line before the keypress) → that
        # means a plain Enter was just pressed while in single-line mode → submit.
        if not self._busy and text.endswith("\n") and text[:-1] == prev_text:
            if self._suppress_next_enter_submit:
                self._suppress_next_enter_submit = False
            elif prev_text.strip():
                # Enter appended a trailing newline to existing text. Submit
                # regardless of embedded newlines so paste+Enter works.
                await self._submit_from_input_widget()
                return
            else:
                # Empty Enter press → discard the newline, keep input blank
                self._set_input_text("")
                self._resize_input_for_text("")
                self._update_input_meta("")
                return

        # Normal change: resize the input to fit the content and update metadata
        self._resize_input_for_text(text)
        self._update_input_meta(text)
        try:
            lang = self._infer_input_language(text)
            if getattr(event.text_area, "language", None) != lang:
                event.text_area.language = lang
        except Exception:
            pass

    async def on_key(self, event: events.Key) -> None:
        focused = getattr(self.app.screen, "focused", None)
        if focused is None or (focused.id or "") != f"term-input-{self._term_id}":
            return

        key = event.key
        text = self._get_input_text()

        if key == "ctrl+u":
            event.prevent_default()
            event.stop()
            self._set_input_text("")
            self._resize_input_for_text("")
            self._update_input_meta("")
            return

        if key == "up" and "\n" not in text:
            event.prevent_default()
            event.stop()
            self._history_nav(-1)
            return

        if key == "down" and "\n" not in text:
            event.prevent_default()
            event.stop()
            self._history_nav(1)
            return

        if key == "ctrl+enter":
            event.prevent_default()
            event.stop()
            await self._submit_from_input_widget()
            return

        if key == "shift+enter":
            # If this event bubbles in the current Textual build, preserve a
            # single newline insertion rather than treating it as submit.
            self._suppress_next_enter_submit = True
            return

        if key == "enter":
            # Safety net: in Textual 8+ this branch is unreachable because
            # TextArea._on_key consumes Enter with event.stop() before it bubbles.
            # Kept for forward-compatibility if Textual changes this behaviour.
            effective_text = text.rstrip("\n")
            if "\n" not in effective_text and effective_text.strip():
                if text != effective_text:
                    self._set_input_text(effective_text)
                event.prevent_default()
                event.stop()
                await self._submit_from_input_widget()
            return

    def on_click(self) -> None:
        self.post_message(self.Activated(self._term_id))

    def update_agent(self, agent, agent_name: str) -> None:
        """Hot-swap the agent for this terminal and refresh the header."""
        self._agent = agent
        self._agent_name = agent_name
        try:
            self.query_one(f"#term-header-{self._term_id}", Static).update(self._header_text())
        except Exception:
            pass

    # ──────────────────────────────────────────────────────── dispatch / worker

    async def dispatch(self, text: str) -> None:
        from rich.text import Text as RichText

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        log.write(RichText(f"> {text}", style="bold #00ff00"))

        cmd = text.lower().split()[0] if text.startswith("/") else ""
        if cmd in ("/exit", "/quit"):
            self.app.exit()
            return
        if cmd == "/help":
            log.write(
                RichText(
                    "  /exit /quit     Exit the TUI\n"
                    "  /clear          Clear this terminal\n"
                    "  /retry          Retry last prompt after an error\n"
                    "  /cancel         Cancel current run (same as Ctrl+C / Esc)\n"
                    "  /help           Show this message\n"
                    "  tip             Append ' all' to broadcast a prompt to T1-T4\n"
                    "\n"
                    "  ^q  Exit    ^l  Clear    ^c  Cancel    ^s  Sidebar\n"
                    "  Esc Cancel",
                    style="#00cc00",
                )
            )
            return
        if cmd in ("/clear", "/cls"):
            log.clear()
            self._write_system_message("context", "terminal output reset", style="#ffaa00")
            return

        if cmd == "/expand":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                log.write(RichText("  usage: /expand <tool_call_id>", style="#ffcc00"))
                return
            call_id = parts[1].strip()
            full = self._tool_outputs_by_call_id.get(call_id)
            if full is None:
                log.write(RichText(f"  no stored output for call_id={call_id}", style="#ff6600"))
                return
            log.write(RichText(f"  [expanded output] call_id={call_id}", style="#00cc00"))
            self._render_agent_message(full)
            return

        if cmd in ("/cancel", "/stop"):
            if self.cancel_active_run():
                log.write(RichText("  cancelled active run", style="#ffaa00"))
            else:
                log.write(RichText("  no active run", style="#ffcc00"))
            return

        if cmd == "/retry":
            if self._busy:
                log.write(RichText("  terminal is busy; retry unavailable", style="#ffcc00"))
                return
            if not self._last_prompt_text:
                log.write(RichText("  no previous prompt to retry", style="#ffcc00"))
                return
            log.write(RichText(f"  retrying: {self._last_prompt_text[:120]}", style="#00cc00"))
            self._set_visual_state("ready")
            self._set_status(f"T{self._term_id}> retrying previous prompt")
            self._busy = True
            self._set_visual_state("busy")
            self._run_worker = self._run_agent(self._last_prompt_text)
            return

        if self._busy:
            log.write(
                RichText(
                    "  [busy] Working... cancel with Ctrl+C, Esc, or /cancel",
                    style="#ffcc00",
                )
            )
            return

        if not cmd:
            try:
                try:
                    app_obj = getattr(self, "app", None)
                    can_dispatch = True
                    if app_obj and hasattr(app_obj, "_can_dispatch_prompt"):
                        can_dispatch = cast(Any, app_obj)._can_dispatch_prompt()
                    if not can_dispatch:
                        log.write(
                            RichText(
                                "  [paused] Price limit exceeded. Increase CAI_PRICE_LIMIT or start a new session.",
                                style="#ff4444",
                            )
                        )
                        return
                except Exception:
                    pass
            except Exception:
                pass

        if self._agent is None:
            log.write(
                RichText(
                    "  No agent loaded. Select one from the sidebar.",
                    style="#ff4444",
                )
            )
            return

        # Open full config overview with /config
        if cmd == "/config":
            try:
                # Display a full config table in the active terminal's right-hand area
                app_obj = getattr(self, "app", None)
                try:
                    getattr(cast(Any, app_obj), "_display_config_table", lambda: None)()
                except Exception:
                    pass
                # Also schedule the interactive full-config worker (overview/edit loop)
                try:
                    getattr(cast(Any, app_obj), "_open_config_screen", lambda *a, **k: None)(
                        "full-config"
                    )
                except Exception:
                    pass
            except Exception:
                pass
            return

        ts = datetime.now().strftime("%H:%M:%S")
        self._last_prompt_text = text
        self._busy = True
        self._set_visual_state("busy")
        self._set_status(f"T{self._term_id}> [{ts}] ⟳ Working… (Ctrl+C to cancel)")
        # Mandatory handoff trace — visible on stderr even when Textual occupies
        # the terminal.  Confirms input crossed the UI→Runner boundary.
        try:
            import sys as _sys

            _sys.stderr.write(
                f"[CAI-TUI] handoff: T{self._term_id} agent={self._agent_name!r} "
                f"text_len={len(text)} preview={text[:80]!r}\n"
            )
            _sys.stderr.flush()
            logger.info(
                "[TUI-handoff] term=%d agent=%r text_len=%d",
                self._term_id,
                self._agent_name,
                len(text),
            )
        except Exception:
            pass
        self._run_worker = self._run_agent(text)

    @work(exclusive=True)
    async def _run_agent(self, text: str) -> None:
        from rich.text import Text as RichText

        from cai.sdk.agents import Runner
        from cai.sdk.agents.items import ToolCallOutputItem
        from cai.sdk.agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        stream_iter = None
        result = None
        run_status = "completed"
        run_error: str | None = None
        streamed_chars = 0

        # Temporary debug tracing for stream event diagnosis
        try:
            import sys as _sys

            _sys.stderr.write(
                f"[cai-tui-debug] _run_agent start: term={self._term_id} "
                f"agent={self._agent_name} model={self._model_name} "
                f"text_len={len(str(text or ''))}\n"
            )
            _sys.stderr.flush()
            logger.debug(
                "_run_agent start: term=%s agent=%s model=%s text_len=%d",
                self._term_id,
                self._agent_name,
                self._model_name,
                len(str(text or "")),
            )
        except Exception:
            pass
        try:
            getattr(cast(Any, self.app), "_telemetry_run_started", lambda *a, **k: None)(
                self._term_id, self._agent_name, text
            )
        except Exception:
            pass
        try:
            try:
                import sys as _sys

                _sys.stderr.write(
                    f"[cai-tui-debug] calling Runner.run_streamed term={self._term_id}\n"
                )
                _sys.stderr.flush()
                logger.debug("calling Runner.run_streamed for term=%s", self._term_id)
            except Exception:
                pass

            # If we are resuming and resume-skip flag is set, avoid running
            # automatic reconnaissance steps triggered by free-form prompts.
            try:
                if getattr(self.app, "_resume_skip_recon", False):
                    txt = str(text or "").lower()
                    # If the user typed 'force' in the prompt, treat it as an explicit override
                    force_in_text = "force" in txt
                    tool_args = {"_force": True} if force_in_text else None
                    if _should_skip_recon(None, tool_args, txt):
                        try:
                            log.write(RichText("[dim]Resume: reconnaissance keywords detected and resume-skip is active — skipping automatic reconnaissance. Add 'force' to override.[/dim]"))
                        except Exception:
                            pass
                        self._busy = False
                        self._set_visual_state("ready")
                        self._set_status(f"T{self._term_id}> reconnaissance skipped")
                        try:
                            self._write_system_message("progress", "recon skipped due to resume flag", style="#ffaa00")
                        except Exception:
                            pass
                        return
            except Exception:
                pass

            # Suppress CAI_STREAM and CAI_STREAM_DEBUG while the TUI worker
            # runs so the underlying model driver doesn't try to render its
            # own Rich streaming panel (which would bleed into the RichLog).
            # The TUI renders the final message via message_output_created /
            # _render_agent_message — it does not need nor want the raw delta
            # debug lines that stream_debug produces.
            _prev_cai_stream = os.environ.get("CAI_STREAM")
            _prev_cai_stream_debug = os.environ.get("CAI_STREAM_DEBUG")
            try:
                os.environ["CAI_STREAM"] = "false"
                os.environ["CAI_STREAM_DEBUG"] = "0"
            except Exception:
                pass

            result = Runner.run_streamed(self._agent, text)
            stream_iter = result.stream_events()

            # Register a TUI-aware progress writer so that auto-compact
            # status messages (e.g. "Generating summary…") appear in the
            # RichLog instead of stdout.
            def _tui_progress_writer(msg: str, style: str | None = None) -> None:
                try:
                    from rich.markup import escape as _re

                    plain = _re(str(msg))
                    colour = style or "bold yellow"
                    self._write_system_message("compact", plain, style=colour)
                    # Keep the status bar updated during long summarisation calls
                    short = plain if len(plain) <= 72 else plain[:71] + "…"
                    self._set_status(f"T{self._term_id}> {short}")
                except Exception:
                    pass

            try:
                from cai.util import set_progress_writer

                set_progress_writer(_tui_progress_writer)
            except Exception:
                pass

            # Register a TUI-aware panel writer so that Rich Panels (e.g. the
            # "Intelligence Panel" from display_agent_analysis) are written to
            # the RichLog widget instead of stdout.
            def _tui_panel_writer(renderable: Any) -> None:
                try:
                    log = self.query_one(f"#term-log-{self._term_id}", RichLog)
                    log.write(renderable)
                except Exception:
                    pass

            try:
                from cai.util import set_panel_writer

                set_panel_writer(_tui_panel_writer)
            except Exception:
                pass

            # Register a loading-state writer so that long-running tools
            # (e.g. cewl wordlist generation) can show/hide the LoadingIndicator.
            def _tui_loading_writer(visible: bool) -> None:
                try:
                    indicator = self.query_one(f"#term-loading-{self._term_id}", LoadingIndicator)
                    indicator.display = visible
                except Exception:
                    pass

            try:
                from cai.util import set_tool_loading_writer

                set_tool_loading_writer(_tui_loading_writer)
            except Exception:
                pass

            try:
                import sys as _sys

                _sys.stderr.write(
                    f"[cai-tui-debug] stream iterator obtained term={self._term_id}\n"
                )
                _sys.stderr.flush()
                logger.debug("stream iterator obtained for term=%s", self._term_id)
            except Exception:
                pass

            # stream_debug is always False in the TUI — the TUI renders the
            # final agent reply via _render_agent_message (message_output_created).
            stream_debug = False
            _stream_line_buf: str = ""

            async for event in stream_iter:
                try:
                    logger.debug("received stream event type=%s", type(event).__name__)
                except Exception:
                    pass

                if isinstance(event, RawResponsesStreamEvent):
                    delta = self._extract_stream_text_delta(event.data)
                    try:
                        import sys as _sys

                        _sys.stderr.write(
                            f"[cai-tui-debug] RAW_EVENT type={getattr(event.data, 'type', type(event.data).__name__)} delta_len={len(delta or '')}\n"
                        )
                        _sys.stderr.flush()
                        logger.debug(
                            "raw delta len=%d preview=%r",
                            len(delta or ""),
                            (delta or "")[:200],
                        )
                    except Exception:
                        pass
                    if delta:
                        streamed_chars += len(delta)
                        self._set_status(f"T{self._term_id}> ⟳ Streaming… {streamed_chars} chars")
                        if stream_debug:
                            # Accumulate into line buffer; flush only on newlines
                            # so each log.write() renders a complete line of text
                            # rather than a single token.
                            _stream_line_buf += str(delta)
                            while "\n" in _stream_line_buf:
                                line, _stream_line_buf = _stream_line_buf.split("\n", 1)
                                if line:  # skip blank splits
                                    try:
                                        log.write(
                                            RichText(
                                                f"[stream:{self._term_id}] {line}",
                                                style="#3399ff",
                                            )
                                        )
                                    except Exception:
                                        pass
                    continue

                if not isinstance(event, RunItemStreamEvent):
                    continue
                ev_name = event.name
                item = event.item
                try:
                    logger.debug(
                        "run item event: name=%s item_type=%s",
                        ev_name,
                        getattr(item, "type", "<unknown>"),
                    )
                except Exception:
                    pass

                try:
                    getattr(cast(Any, self.app), "_emit_telemetry", lambda *a, **k: None)(
                        self._term_id,
                        self._agent_name,
                        "stream_event",
                        {"name": ev_name, "item_type": getattr(item, "type", "unknown")},
                    )
                except Exception:
                    pass

                if ev_name == "message_output_created":
                    try:
                        getattr(
                            cast(Any, self.app), "_telemetry_first_token", lambda *a, **k: None
                        )(self._term_id, self._agent_name)
                    except Exception:
                        pass
                    try:
                        from cai.sdk.agents.items import ItemHelpers

                        content = ItemHelpers.text_message_output(cast(Any, item))
                    except Exception:
                        content = str(getattr(item, "content", ""))
                    if content:
                        self._render_agent_message(content)

                elif ev_name == "reasoning_item_created":
                    # Render reasoning / thought content as a panel (if available)
                    try:
                        from cai.sdk.agents.items import ItemHelpers

                        content = ItemHelpers.text_message_output(cast(Any, item))
                    except Exception:
                        content = str(getattr(item, "content", "") or "")

                    if content:
                        try:
                            from cai.repl.ui.renderers import display_agent_thought

                            agent_name = getattr(self, "_agent_name", None)
                            display_agent_thought(content, agent_name)
                        except Exception:
                            self._write_system_message(
                                "progress", "reasoning step created", style="#00cc88"
                            )
                    else:
                        self._write_system_message(
                            "progress", "reasoning step created", style="#00cc88"
                        )

                elif ev_name == "tool_called":
                    raw = getattr(item, "raw_item", item)
                    fn_name = getattr(
                        raw,
                        "name",
                        getattr(getattr(raw, "function", None), "name", "tool"),
                    )
                    fn_args = str(getattr(raw, "arguments", "…"))
                    call_id = str(getattr(raw, "call_id", "unknown"))
                    if len(fn_args) > 80:
                        fn_args = fn_args[:80] + "…"
                    self._active_tool_calls[call_id] = {
                        "name": str(fn_name),
                        "args": fn_args,
                    }
                    try:
                        from cai.util import write_panel
                        from rich.panel import Panel as _Panel
                        from rich import box as _box

                        agent_name = getattr(self, "_agent_name", "Agent")
                        fn_args_disp_safe = fn_args.replace("[", "\\[")
                        body = (
                            f"[bold bright_yellow]⚡ {fn_name}[/bold bright_yellow]  "
                            f"[dim yellow]{fn_args_disp_safe}[/dim yellow]"
                        )
                        write_panel(
                            _Panel(
                                body,
                                box=_box.HEAVY,
                                border_style="bold yellow",
                                title=f"[bold yellow]🛠  {agent_name}[/bold yellow]",
                                padding=(0, 1),
                                expand=True,
                            )
                        )
                    except Exception:
                        log.write(RichText(f"  ▶ {fn_name}({fn_args}) [running]", style="#006600"))
                    try:
                        getattr(
                            cast(Any, self.app), "_telemetry_tool_called", lambda *a, **k: None
                        )(
                            self._term_id,
                            self._agent_name,
                            str(fn_name),
                            call_id,
                            str(getattr(raw, "arguments", "")),
                        )
                    except Exception:
                        pass

                elif ev_name == "tool_output":
                    if isinstance(item, ToolCallOutputItem):
                        call_id = "unknown"
                        try:
                            raw_item = getattr(item, "raw_item", {}) or {}
                            if isinstance(raw_item, dict):
                                call_id = str(raw_item.get("call_id", "unknown"))
                            else:
                                call_id = str(getattr(raw_item, "call_id", "unknown"))
                        except Exception:
                            call_id = "unknown"

                        full_output = str(item.output)
                        self._tool_outputs_by_call_id[call_id] = full_output
                        preview, collapsed = self._format_tool_output_preview(full_output)
                        name = self._active_tool_calls.get(call_id, {}).get("name", "tool")
                        log.write(
                            RichText(f"  ✓ {name} [success] call_id={call_id}", style="#00cc00")
                        )
                        self._render_agent_message(preview)
                        if collapsed:
                            remaining = max(
                                0, len(full_output.splitlines()) - len(preview.splitlines())
                            )
                            log.write(
                                RichText(
                                    f"    … collapsed {remaining} lines, use /expand {call_id} for full output",
                                    style="#004400",
                                )
                            )
                        try:
                            out_preview = preview.splitlines()[0] if preview else full_output
                            getattr(
                                cast(Any, self.app), "_telemetry_tool_output", lambda *a, **k: None
                            )(
                                self._term_id,
                                self._agent_name,
                                call_id,
                                out_preview,
                            )
                        except Exception:
                            pass
                        if call_id in self._active_tool_calls:
                            self._active_tool_calls.pop(call_id, None)

        except asyncio.CancelledError:
            run_status = "cancelled"
            log.write(RichText("  [cancelled]", style="#ff6600"))
            self._write_system_message("progress", "run cancelled", style="#ffaa00")
        except Exception as exc:
            # ContextCompactedError is a resumption signal, not a fatal error.
            # The context window was auto-compacted; reload the agent and
            # re-submit with a continuation prompt, exactly as the CLI does.
            try:
                from cai.sdk.agents.exceptions import ContextCompactedError as _CCE

                _is_compact = isinstance(exc, _CCE)
            except Exception:
                _is_compact = "ContextCompactedError" in type(exc).__name__

            if _is_compact:
                try:
                    run_status = "completed"
                    self._write_system_message(
                        "progress",
                        "✓ Context window compacted — resuming task",
                        style="#00cc88",
                    )
                    # Reload agent via AGENT_MANAGER in case it was refreshed.
                    try:
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER as _AM

                        _reloaded = _AM.get_active_agent()
                        if _reloaded is not None:
                            self._agent = _reloaded
                    except Exception:
                        pass
                    _continuation = (
                        f"{self._last_prompt_text}\n\n"
                        "IMPORTANT: Your context window was just compacted. "
                        "Your session memory is already loaded above. "
                        "Review the 'Exhausted Approaches' section in your memory and "
                        "DO NOT repeat any technique, command, URL, port scan, or login "
                        "attempt already listed there. "
                        "Pick up exactly where you left off using only NEW approaches."
                    )
                    # Schedule a fresh run with the continuation prompt.
                    self.call_after_refresh(lambda: self._run_worker.__class__)  # keep linter happy
                    self._run_worker = self._run_agent(_continuation)
                    return  # skip the error-state path below
                except Exception as _retry_exc:
                    # If the retry scheduling itself fails, fall through to
                    # the generic error handler so the user is informed.
                    import sys as _sys

                    _sys.stderr.write(f"[cai-tui-debug] compact retry failed: {_retry_exc!r}\n")
                    _sys.stderr.flush()

            run_status = "error"
            run_error = str(exc)
            try:
                import sys as _sys

                _sys.stderr.write(f"[cai-tui-debug] EXCEPTION in _run_agent: {exc!r}\n")
                _sys.stderr.flush()
            except Exception:
                pass
            # Mark any running tool calls as errored in the log.
            for call_id, meta in list(self._active_tool_calls.items()):
                log.write(
                    RichText(
                        f"  ✗ {meta.get('name', 'tool')} [error] call_id={call_id}",
                        style="#ff4444",
                    )
                )
            self._active_tool_calls.clear()
            # Render a single clean error panel — avoids printing the same
            # exception message twice (once raw, once via _write_system_message).
            try:
                from rich.panel import Panel as _Panel

                log.write(
                    _Panel(
                        f"[bold]{type(exc).__name__}[/bold]: {exc}",
                        title="[bold red]Error[/bold red]",
                        border_style="red",
                        padding=(0, 1),
                    )
                )
            except Exception:
                log.write(RichText(f"  [error] {exc}", style="#ff4444"))
            log.write(RichText("  hint: use /retry to run the last prompt again", style="#ff6666"))
        finally:
            self._busy = False
            self._run_worker = None
            # Unregister the TUI progress writer so CLI mode gets its console back.
            try:
                from cai.util import set_progress_writer

                set_progress_writer(None)
            except Exception:
                pass
            # Unregister the TUI panel writer so CLI mode gets its console back.
            try:
                from cai.util import set_panel_writer

                set_panel_writer(None)
            except Exception:
                pass
            # Unregister the loading writer and ensure the indicator is hidden.
            try:
                from cai.util import set_tool_loading_writer

                set_tool_loading_writer(None)
            except Exception:
                pass
            try:
                indicator = self.query_one(f"#term-loading-{self._term_id}", LoadingIndicator)
                indicator.display = False
            except Exception:
                pass
            # Restore CAI_STREAM / CAI_STREAM_DEBUG to their original values.
            try:
                if _prev_cai_stream is None:
                    os.environ.pop("CAI_STREAM", None)
                else:
                    os.environ["CAI_STREAM"] = _prev_cai_stream
                if _prev_cai_stream_debug is None:
                    os.environ.pop("CAI_STREAM_DEBUG", None)
                else:
                    os.environ["CAI_STREAM_DEBUG"] = _prev_cai_stream_debug
            except Exception:
                pass
            # Flush any partial line left in the streaming buffer (no-op when stream_debug=False)
            try:
                if stream_debug and _stream_line_buf.strip():
                    log.write(
                        RichText(
                            f"[stream:{self._term_id}] {_stream_line_buf}",
                            style="#3399ff",
                        )
                    )
            except Exception:
                pass
            if stream_iter is not None:
                try:
                    await cast(Any, stream_iter).aclose()
                except Exception:
                    pass
            try:
                status_text = getattr(
                    cast(Any, self.app), "_telemetry_run_finished", lambda *a, **k: ""
                )(
                    self._term_id,
                    self._agent_name,
                    result,
                    run_status,
                    run_error,
                )
            except Exception:
                status_text = ""
            self._set_status(status_text)
            if run_status == "error":
                self._set_visual_state("error")
            else:
                self._set_visual_state("ready")

    def _set_status(self, text: str) -> None:
        try:
            out = str(text or "")
            try:
                if getattr(self.app, "_responsive_mode", "medium") == "small" and len(out) > 52:
                    out = out[:51] + "…"
            except Exception:
                pass
            self.query_one(f"#term-status-{self._term_id}", Static).update(out)
        except Exception:
            pass
