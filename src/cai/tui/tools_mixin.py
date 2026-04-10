"""ToolsMixin — tool registry, call history, and inject-mode methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, cast

from textual import work
from textual.containers import ScrollableContainer
from textual.widgets import Button, Static, RichLog, TextArea
from rich.text import Text as RichText

from cai.tui.components.terminal import TerminalPanel
from cai.tui.components.sidebar import Sidebar
from cai.tui.screens.common import PromptModal


class ToolsMixin:
    """Mixin providing tool registry, history, and inject-mode helpers."""

    def _build_tool_registry(self) -> dict[str, dict]:
        """Build TUI tool registry from built-ins plus active terminal agent tools."""
        from cai.sdk.agents.run_context import RunContextWrapper
        from cai.sdk.agents.tool import FunctionTool

        def _tool_echo(params: dict) -> dict:
            text = str(params.get("text", ""))
            return {"output": text, "length": len(text)}

        def _tool_now(_: dict) -> dict:
            return {"now": datetime.now().isoformat(timespec="seconds")}

        def _tool_list_agents(_: dict) -> dict:
            keys = sorted(list(self._available_agents.keys()))
            return {"count": len(keys), "agents": keys}

        def _tool_last_tool_call(_: dict) -> dict:
            if not self._tool_call_history:
                return {"has_calls": False}
            last = self._tool_call_history[-1]
            return {
                "has_calls": True,
                "last_call_id": last.get("call_id"),
                "tool_id": last.get("tool_id"),
                "timestamp": last.get("timestamp"),
            }

        registry = {
            "echo": {
                "name": "Echo",
                "description": "Return provided text as tool output.",
                "schema": {"text": "string"},
                "runner": _tool_echo,
            },
            "now": {
                "name": "Current Time",
                "description": "Return local timestamp.",
                "schema": {},
                "runner": _tool_now,
            },
            "list_agents": {
                "name": "List Agents",
                "description": "Show currently available CAI agents.",
                "schema": {},
                "runner": _tool_list_agents,
            },
            "last_tool_call": {
                "name": "Last Tool Call",
                "description": "Inspect metadata of the most recent tool call.",
                "schema": {},
                "runner": _tool_last_tool_call,
            },
        }

        # Pull real function tools from the currently active terminal's agent.
        active_agent = None
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            active_agent = panel._agent
        except Exception:
            active_agent = self._agent

        for tool in list(getattr(active_agent, "tools", []) or []):
            try:
                tool_name = getattr(tool, "name", None)
                if not tool_name:
                    continue

                if isinstance(tool, FunctionTool):

                    async def _invoke_function_tool(params: dict, fn_tool=tool):
                        ctx = RunContextWrapper(context=None)
                        tool_input = json.dumps(params or {}, ensure_ascii=True)
                        output = await fn_tool.on_invoke_tool(ctx, tool_input)
                        return {"output": str(output)}

                    registry[f"agent::{tool_name}"] = {
                        "name": f"{tool_name} (agent)",
                        "description": getattr(tool, "description", "") or "Agent function tool",
                        "schema": getattr(tool, "params_json_schema", {}) or {},
                        "runner": _invoke_function_tool,
                        "async_runner": True,
                        "source": "agent",
                    }
                else:
                    # Hosted tools are inspect-only in this panel for now.
                    registry[f"agent::{tool_name}"] = {
                        "name": f"{tool_name} (inspect)",
                        "description": "Hosted tool (inspect only in TUI panel)",
                        "schema": {},
                        "runner": None,
                        "source": "agent",
                    }
            except Exception:
                continue

        return registry

    async def _populate_tools_list(self) -> None:
        """Render tool buttons in the Tools tab."""
        # Delegate rendering to the Sidebar's ToolsTab when available.
        try:
            sidebar = cast(Any, self.query_one(Sidebar))
        except Exception:
            return

        try:
            sidebar.set_tools(self._tool_registry or {})
        except Exception:
            pass

        # Keep existing behaviour to refresh preview/history when possible
        try:
            if self._tool_registry:
                self._selected_tool_id = sorted(self._tool_registry.keys())[0]
                self._highlight_active_tool(self._selected_tool_id)
                self._sync_inject_mode_button()
                self._update_tools_preview()
                await self._refresh_tools_history_ui()
        except Exception:
            pass

    def _highlight_active_tool(self, tool_id: str) -> None:
        for btn in self.query(".tool-btn"):
            btn.remove_class("-active-tool")
        try:
            btn_id = self._tool_tool_id_to_button_id.get(tool_id)
            if not btn_id:
                return
            self.query_one(f"#{btn_id}", Button).add_class("-active-tool")
        except Exception:
            pass

    def _append_tool_call(
        self, tool_id: str, inputs: dict, output: dict, replayed: bool = False
    ) -> dict:
        call_id = f"tool-{len(self._tool_call_history) + 1}"
        record = {
            "call_id": call_id,
            "tool_id": tool_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "inputs": inputs,
            "output": output,
            "replayed": replayed,
        }
        self._tool_call_history.append(record)
        self._persist_tool_call_record(record)
        self._selected_tool_call_idx = len(self._tool_call_history) - 1
        return record

    def _load_tool_call_history(self) -> list[dict]:
        records: list[dict] = []
        try:
            paths: list[str] = []
            # oldest backup first, then newest/current file
            for i in range(self._tool_calls_max_backups, 0, -1):
                backup_path = f"{self._tool_calls_file}.{i}"
                if os.path.exists(backup_path):
                    paths.append(backup_path)
            if os.path.exists(self._tool_calls_file):
                paths.append(self._tool_calls_file)

            for path in paths:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            item = json.loads(raw)
                            if isinstance(item, dict):
                                records.append(item)
                        except Exception:
                            continue
        except Exception:
            return []
        return records

    def _rotate_tool_calls_if_needed(self) -> None:
        """Rotate `tui_tool_calls.jsonl` when the file grows beyond max bytes."""
        try:
            if not os.path.exists(self._tool_calls_file):
                return
            if os.path.getsize(self._tool_calls_file) <= self._tool_calls_max_bytes:
                return

            max_backups = max(1, int(self._tool_calls_max_backups))

            # Drop the oldest backup if needed.
            oldest = f"{self._tool_calls_file}.{max_backups}"
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except Exception:
                    pass

            # Shift existing backups up: .1 -> .2, etc.
            for i in range(max_backups - 1, 0, -1):
                src = f"{self._tool_calls_file}.{i}"
                dst = f"{self._tool_calls_file}.{i + 1}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass

            # Rotate current file to .1
            try:
                os.replace(self._tool_calls_file, f"{self._tool_calls_file}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _persist_tool_call_record(self, record: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._tool_calls_file), exist_ok=True)
            self._rotate_tool_calls_if_needed()
            with open(self._tool_calls_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            pass

    async def _refresh_tools_history_ui(self) -> None:
        """Render tool call history entries as buttons."""
        try:
            scroll = self.query_one("#tools-history-scroll", ScrollableContainer)
        except Exception:
            return

        for child in list(scroll.children):
            try:
                await child.remove()
            except Exception:
                pass

        # Show newest first and keep recent window to avoid overgrowing the UI.
        history_window = list(enumerate(self._tool_call_history[-20:]))
        history_window.reverse()
        base_idx = max(0, len(self._tool_call_history) - 20)
        for local_idx, record in history_window:
            actual_idx = base_idx + local_idx
            call_id = record.get("call_id", f"call-{actual_idx + 1}")
            tool_id = record.get("tool_id", "?")
            stamp = record.get("timestamp", "")
            tag = " (replay)" if record.get("replayed") else ""
            label = f"{call_id} · {tool_id} · {stamp}{tag}"
            await scroll.mount(Button(label, id=f"tool-call-{actual_idx}", classes="team-btn"))

    def _update_tools_preview(self) -> None:
        try:
            preview = self.query_one("#tools-preview", Static)
        except Exception:
            return

        tool_id = self._selected_tool_id
        if not tool_id or tool_id not in self._tool_registry:
            preview.update("Select a tool to inspect or run.")
            return

        meta = self._tool_registry.get(tool_id, {})
        lines = [
            f"[bold]{meta.get('name', tool_id)}[/bold] ({tool_id})",
            meta.get("description", ""),
            "",
            f"Schema: {json.dumps(meta.get('schema', {}), ensure_ascii=True)}",
            f"Inject mode: {self._inject_mode}",
        ]

        if self._selected_tool_call_idx is not None:
            try:
                rec = self._tool_call_history[self._selected_tool_call_idx]
                lines.extend(
                    [
                        "",
                        f"Last selected call: {rec.get('call_id')} @ {rec.get('timestamp')}",
                        f"Inputs: {json.dumps(rec.get('inputs', {}), ensure_ascii=True)}",
                        f"Output: {json.dumps(rec.get('output', {}), ensure_ascii=True)[:220]}",
                    ]
                )
            except Exception:
                pass

        try:
            preview.update(RichText.from_markup("\n".join(lines)))
        except Exception:
            preview.update("\n".join(lines))

    def _log_to_active_terminal(self, line: str, style: str = "#00aa00") -> None:
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
            log.write(RichText(line, style=style))
        except Exception:
            pass

    def _sync_inject_mode_button(self) -> None:
        try:
            btn = self.query_one("#tools-inject-mode", Button)
            btn.label = f"Mode: {self._inject_mode}"
            btn.remove_class("-inject-input")
            btn.remove_class("-inject-command")
            if self._inject_mode == "command":
                btn.add_class("-inject-command")
            else:
                btn.add_class("-inject-input")
        except Exception:
            pass

    def _toggle_inject_mode(self) -> None:
        self._inject_mode = "command" if self._inject_mode == "input" else "input"
        self._persist_inject_mode_pref()
        self._sync_inject_mode_button()
        self._update_tools_preview()
        self._log_to_active_terminal(f"[inject] mode set to {self._inject_mode}")

    @work(exclusive=False)
    async def _run_selected_tool_worker(self) -> None:
        # UI interaction (prompt for args) remains in App; delegate execution to controller
        tool_id = self._selected_tool_id
        if not tool_id or tool_id not in self._tool_registry:
            return

        raw_args = await self.push_screen_wait(PromptModal("Tool JSON args:", "{}"))
        if raw_args is None:
            return

        try:
            params = json.loads(raw_args) if str(raw_args).strip() else {}
            if not isinstance(params, dict):
                params = {}
        except Exception:
            self._log_to_active_terminal(
                "[tool] Invalid JSON args; expected an object.", style="#ff4444"
            )
            return

        try:
            # Controller executes the runner in a background worker
            self.controller.run_tool(tool_id, params)
        except Exception:
            pass

    @work(exclusive=False)
    async def _replay_selected_tool_call_worker(self) -> None:
        # Prompt for edited params (UI) then delegate replay execution to controller
        idx = self._selected_tool_call_idx
        if idx is None:
            return
        try:
            record = self._tool_call_history[idx]
        except Exception:
            return

        default_params = record.get("inputs", {})
        try:
            default_json = json.dumps(default_params, ensure_ascii=True)
        except Exception:
            default_json = "{}"

        edited = await self.push_screen_wait(
            PromptModal("Replay JSON args (edit or keep):", default_json)
        )
        if edited is None:
            return

        try:
            params = json.loads(edited) if str(edited).strip() else {}
            if not isinstance(params, dict):
                params = {}
        except Exception:
            self._log_to_active_terminal(
                "[tool] Replay args must be valid JSON object.", style="#ff4444"
            )
            return

        try:
            self.controller.replay_tool_call(idx, params)
        except Exception:
            pass

    @work(exclusive=False)
    async def _inject_selected_tool_output(self) -> None:
        idx = self._selected_tool_call_idx
        if idx is None:
            return
        try:
            record = self._tool_call_history[idx]
            output_obj = record.get("output", {})
            payload = json.dumps(output_obj, ensure_ascii=True)

            if self._inject_mode == "command":
                self._log_to_active_terminal(f"[inject/command] {payload}", style="#00ff00")
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    await panel.dispatch(payload)
                except Exception as exc:
                    self._log_to_active_terminal(
                        f"[inject] command dispatch failed: {exc}", style="#ff4444"
                    )
            else:
                self._log_to_active_terminal(f"[inject/input] {payload}", style="#00ff00")
                inp = self.query_one(f"#term-input-{self._active_term_id}", TextArea)
                if hasattr(inp, "load_text"):
                    inp.load_text(payload)
                else:
                    cast(Any, inp).value = payload
                inp.focus()
        except Exception:
            pass
