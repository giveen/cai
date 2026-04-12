"""TUI controller to encapsulate session orchestration and long-running tasks.

This module provides a minimal `TuiController` class used by the Textual
application to offload blocking or long-running operations (agent runs,
nmap scans, Crawl4AI processing) into background workers via Textual's
`@work` decorator.

The implementation is intentionally lightweight: it exposes a small public
API consumed by the UI (`start_session`, `stop_session`) and provides
example methods decorated with `@work` that the app can call or pass
callables into.
"""

from __future__ import annotations

import asyncio
import logging
import json
import inspect
import os
from typing import Any, Callable

from rich.text import Text as RichText
from textual.widgets import RichLog, Button
from textual import work
from cai.session import auto_commit_from_tool, commit_objective_state, _read_state
from cai.tools.common import _should_skip_recon
from cai.sdk.agents.shutdown_coordinator import SHUTDOWN_COORDINATOR

logger = logging.getLogger(__name__)


def _looks_like_recon_call(tool_id: str | None, meta: dict | None, params: dict | None, text: str | None = None) -> bool:
    """Heuristic: detect whether a tool call or prompt appears reconnaissance-related.

    This is intentionally conservative: it performs a simple substring check
    against a short keyword list. Returns True if any keyword is present.
    """
    try:
        parts: list[str] = []
        if tool_id:
            parts.append(str(tool_id))
        if isinstance(meta, dict):
            parts.append(str(meta.get("name", "")))
            parts.append(str(meta.get("description", "")))
        if params:
            try:
                parts.append(json.dumps(params, ensure_ascii=False))
            except Exception:
                parts.append(str(params))
        if text:
            parts.append(str(text))
        combined = " ".join([p.lower() for p in parts if p])
        if not combined:
            return False
        keywords = (
            "nmap",
            "masscan",
            "linpeas",
            "lin_peas",
            "recon",
            "scan",
            "shodan",
            "sqlmap",
            "cewl",
            "netcat",
            "netstat",
            "smb",
            "ldap",
            "curl",
            "wget",
            "smbclient",
            "enum",
            "enumerat",
            "reconnaissance",
        )
        return any(k in combined for k in keywords)
    except Exception:
        return False


class TuiController:
    """Controller that orchestrates long-running TUI tasks.

    The controller is intended to be instantiated by the Textual App and
    receive a reference to the app instance so its worker methods can access
    the UI when needed.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        # Best-effort: import persisted VCM state from workspace on startup
        try:
            try:
                from cai.session import import_vcm_state

                import_vcm_state()
            except Exception:
                pass
        except Exception:
            pass
        # session_id -> Worker handle returned by a @work-decorated call
        self._workers: dict[str, Any] = {}
        # Register shutdown hook to persist objective/next_steps
        try:
            SHUTDOWN_COORDINATOR.register(self._shutdown_commit_objective)
        except Exception:
            pass
        # Start a periodic background committer for objectives (best-effort)
        try:
            worker = self.periodic_commit_objective(30)
            self._workers["objective_committer"] = worker
        except Exception:
            pass

    def start_session(
        self, session_id: str, runner: Callable[..., Any] | None = None, **opts
    ) -> Any:
        """Start a session run in the background.

        If *runner* is provided it will be invoked inside the background
        worker. The returned value is the Textual Worker handle (or whatever
        the @work decorator returns) so the caller may cancel it.
        """
        worker = self._run_session(session_id, runner, opts)
        self._workers[session_id] = worker
        return worker

    def stop_session(self, session_id: str) -> None:
        """Request cancellation of an active session worker, if any."""
        worker = self._workers.get(session_id)
        if not worker:
            return
        try:
            # Textual Worker provides `cancel()`; fall back to task cancel.
            if hasattr(worker, "cancel"):
                worker.cancel()
            else:
                worker.cancel()
        except Exception:
            logger.exception("Error cancelling worker for session %s", session_id)
        finally:
            self._workers.pop(session_id, None)

    @work(exclusive=True)
    async def _run_session(
        self, session_id: str, runner: Callable[..., Any] | None, opts: dict
    ) -> None:
        """Background worker that runs a session.

        This method is intentionally generic: if a `runner` callable is
        supplied it will be awaited/called; otherwise the worker will perform
        a no-op sleep loop to illustrate background execution.
        """
        logger.info("TuiController: starting session %s", session_id)
        try:
            if runner is not None:
                # Allow both coroutine functions and regular callables
                result = (
                    runner(**opts)
                    if not asyncio.iscoroutinefunction(runner)
                    else await runner(**opts)
                )
                logger.info("TuiController: session %s finished; result=%r", session_id, result)
            else:
                # Placeholder work: keep the worker alive for the session
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info("TuiController: session %s cancelled", session_id)
            raise
        except Exception:
            logger.exception("TuiController: session %s failed", session_id)
        finally:
            # Clean up stored worker handle if present
            try:
                self._workers.pop(session_id, None)
            except Exception:
                pass

    def _shutdown_commit_objective(self) -> None:
        """Synchronous shutdown callback to persist objective/next_steps."""
        try:
            current_objective = ""
            next_steps: list[str] = []
            try:
                from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                agent_name = AGENT_MANAGER._active_agent_name
                if agent_name:
                    history = AGENT_MANAGER.get_message_history(agent_name) or []
                    for m in reversed(history):
                        try:
                            if isinstance(m, dict) and m.get("role") == "user":
                                cur = m.get("content") or m.get("text") or ""
                                current_objective = cur if isinstance(cur, str) else str(cur)
                                break
                        except Exception:
                            continue
            except Exception:
                pass

            try:
                commit_objective_state(current_objective, next_steps)
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def periodic_commit_objective(self, interval_seconds: int = 30) -> None:
        """Background worker that periodically persists current objective and next steps.

        This is best-effort: it tries to extract a sensible short objective from the
        active agent message history or the app and writes it to `state.json`.
        """
        try:
            while True:
                try:
                    current_objective = ""
                    next_steps: list[str] = []
                    try:
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                        agent_name = AGENT_MANAGER._active_agent_name
                        if agent_name:
                            history = AGENT_MANAGER.get_message_history(agent_name) or []
                            for m in reversed(history):
                                try:
                                    if isinstance(m, dict) and m.get("role") == "user":
                                        cur = m.get("content") or m.get("text") or ""
                                        current_objective = cur if isinstance(cur, str) else str(cur)
                                        break
                                except Exception:
                                    continue
                    except Exception:
                        # Fallback to app-level hints
                        try:
                            last = getattr(self.app, "_last_user_input", None)
                            if last:
                                current_objective = str(last)
                        except Exception:
                            pass

                    try:
                        commit_objective_state(current_objective or "", next_steps or [])
                    except Exception:
                        pass
                except Exception:
                    logger.exception("periodic_commit_objective error")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("periodic_commit_objective crashed")

    # ------------------------------------------------------------------
    # Tool / queue / session orchestration moved from the App into the
    # controller so long-running work runs under @work and the App can
    # remain a lightweight router. These methods intentionally reference
    # `self.app` to update UI state and logs.
    # ------------------------------------------------------------------
    @work(exclusive=False)
    async def run_tool(self, tool_id: str, params: dict) -> None:
        """Execute a tool runner (sync or async) and persist the call."""
        app = self.app
        try:
            if not tool_id or not getattr(app, "_tool_registry", None):
                return
            meta = (app._tool_registry or {}).get(tool_id, {}) or {}
            # If we are resuming and resume-skip flag is set, avoid running
            # reconnaissance-like tools by default.
            try:
                if getattr(app, "_resume_skip_recon", False):
                    try:
                        params_use = params or {}
                        if _looks_like_recon_call(tool_id, meta, params_use):
                            try:
                                app._log_to_active_terminal(
                                    f"[tool] Skipping {tool_id} due to resume-skip-recon flag (use force to override).",
                                    style="#ffaa00",
                                )
                            except Exception:
                                pass
                            return
                    except Exception:
                        pass
            except Exception:
                pass
            runner = meta.get("runner")
            try:
                # If this looks like an aggressive tool, run the ToolTree planner
                from cai.agents.tooltree import is_aggressive_tool, plan_and_select

                try:
                    if is_aggressive_tool(tool_id, meta, params, None):
                        try:
                            params_selected, plan_record = await plan_and_select(tool_id, params, None)
                            params = params_selected or params
                            try:
                                app._log_to_active_terminal(
                                    f"[tool-planner] Selected branch {plan_record.get('selected', {}).get('id')} (score={plan_record.get('selected', {}).get('score')})",
                                    style="#88ff88",
                                )
                            except Exception:
                                pass
                        except Exception:
                            logger.exception("ToolTree planning failed")
                except Exception:
                    pass
            except Exception:
                # best-effort: do not fail if planner import errors
                pass
            if not callable(runner):
                try:
                    app._log_to_active_terminal("[tool] Runner unavailable.", style="#ff4444")
                except Exception:
                    pass
                return

            output_or_awaitable = runner(params or {})
            if inspect.isawaitable(output_or_awaitable):
                output = await output_or_awaitable
            else:
                output = output_or_awaitable
            if not isinstance(output, dict):
                output = {"output": str(output)}

            record = app._append_tool_call(tool_id, params or {}, output)
            try:
                app._log_to_active_terminal(
                    f"[tool] {record['call_id']} {tool_id} -> {json.dumps(output, ensure_ascii=True)[:220]}"
                )
            except Exception:
                pass

            try:
                await app._refresh_tools_history_ui()
            except Exception:
                pass
            try:
                app._update_tools_preview()
            except Exception:
                pass
            try:
                # Schedule an async state commit for significant tool findings.
                # This runs under Textual's @work so it won't block the UI.
                self.commit_state_worker(tool_id, output)
            except Exception:
                pass
            async def commit_state_worker(self, tool_id: str, output: Any) -> None:
                """Background worker that attempts to auto-commit findings to state.json.

                Runs in a Textual worker thread and is best-effort; failures are logged
                but do not propagate to the caller.
                """
                try:
                    try:
                        result = auto_commit_from_tool(tool_id, output)
                    except Exception:
                        result = {"status": "error"}

                    # Log a short summary to the active terminal if available
                    try:
                        app = self.app
                        if app and getattr(app, "_log_to_active_terminal", None):
                            try:
                                app._log_to_active_terminal(f"[auto-commit] {tool_id} -> {result.get('status')}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception:
                    logger.exception("commit_state_worker failed for %s", tool_id)
        except Exception:
            try:
                app._log_to_active_terminal("[tool] Execution failed", style="#ff4444")
            except Exception:
                pass

    @work(exclusive=False)
    async def replay_tool_call(self, idx: int, params: dict | None = None) -> None:
        app = self.app
        try:
            if idx is None:
                return
            record = (app._tool_call_history or [])[idx]
        except Exception:
            return

        tool_id = record.get("tool_id")
        if not tool_id or tool_id not in (app._tool_registry or {}):
            try:
                app._log_to_active_terminal(
                    "[tool] Replay failed: tool not found.", style="#ff4444"
                )
            except Exception:
                pass
            return

        runner = (app._tool_registry or {}).get(tool_id, {}).get("runner")
        if not callable(runner):
            return

        try:
            params_use = params if params is not None else record.get("inputs", {})
            # Replay-time recon-skip enforcement
            try:
                if getattr(app, "_resume_skip_recon", False):
                    try:
                        params_use_check = params_use or {}
                        replay_meta = (app._tool_registry or {}).get(tool_id, {}) or {}
                        if _looks_like_recon_call(tool_id, replay_meta, params_use_check):
                            try:
                                app._log_to_active_terminal(
                                    f"[tool] Replay skipped for {tool_id} due to resume-skip-recon flag (use force to override).",
                                    style="#ffaa00",
                                )
                            except Exception:
                                pass
                            return
                    except Exception:
                        pass
            except Exception:
                pass
            output_or_awaitable = runner(params_use or {})
            if inspect.isawaitable(output_or_awaitable):
                output = await output_or_awaitable
            else:
                output = output_or_awaitable
            if not isinstance(output, dict):
                output = {"output": str(output)}
            replay_record = app._append_tool_call(tool_id, params_use or {}, output, replayed=True)
            try:
                app._log_to_active_terminal(
                    f"[tool] Replayed {record.get('call_id')} as {replay_record['call_id']}"
                )
            except Exception:
                pass
            try:
                await app._refresh_tools_history_ui()
            except Exception:
                pass
            try:
                app._update_tools_preview()
            except Exception:
                pass
        except Exception:
            try:
                app._log_to_active_terminal("[tool] Replay failed", style="#ff4444")
            except Exception:
                pass

    @work(exclusive=True)
    async def run_queue(self) -> None:
        app = self.app
        if getattr(app, "_queue_running", False):
            return

        pending = [i for i in (app._queue_items or []) if i.get("status") == "pending"]
        if not pending:
            try:
                app._log_to_active_terminal("[queue] no pending prompts to run", style="#ff6600")
            except Exception:
                pass
            return

        app._queue_running = True
        try:
            app._update_queue_status()
        except Exception:
            pass

        total = len(pending)
        current = 0

        try:
            for item in list(app._queue_items or []):
                if item.get("status") != "pending":
                    continue
                current += 1
                item["status"] = "running"
                try:
                    app._update_queue_view()
                except Exception:
                    pass

                text = str(item.get("text", "")).strip()
                try:
                    broadcast = bool(item.get("broadcast", False) or app._queue_broadcast_mode)
                    if broadcast:
                        await app._broadcast_prompt(text)
                    else:
                        # Dispatch into the active terminal panel
                        from cai.tui.components.terminal import TerminalPanel

                        panel = app.query_one(
                            f"#terminal-panel-{app._active_term_id}", TerminalPanel
                        )
                        await panel.dispatch(text)
                    item["status"] = "completed"
                    try:
                        app._log_to_active_terminal(
                            f"[queue] ({current}/{total}) completed: {text[:120]}"
                        )
                    except Exception:
                        pass
                except Exception as exc:
                    item["status"] = "error"
                    item["error"] = str(exc)
                    try:
                        app._log_to_active_terminal(
                            f"[queue] ({current}/{total}) failed: {text[:120]} · {exc}",
                            style="#ff4444",
                        )
                    except Exception:
                        pass

                try:
                    app._update_queue_view()
                except Exception:
                    pass
                await asyncio.sleep(0)
        finally:
            app._queue_running = False
            try:
                app._update_queue_status()
            except Exception:
                pass

    @work(exclusive=True)
    async def activate_team(self, idx: int) -> None:
        app = self.app
        try:
            if idx < 0 or idx >= len(app.TEAM_PRESETS):
                return
        except Exception:
            return

        label, agent_types = app.TEAM_PRESETS[idx]
        previous_active = app._active_term_id

        # Ensure we have 4 terminals available.
        try:
            from cai.tui.components.terminal import TerminalPanel

            panels = sorted(list(app.query(TerminalPanel)), key=lambda p: p._term_id)
        except Exception:
            panels = []

        while len(panels) < 4:
            next_slot = len(panels)
            agent_name = agent_types[min(next_slot, len(agent_types) - 1)]
            agent_obj = app._available_agents.get(agent_name, app._agent)
            try:
                await app._add_terminal(agent_obj, agent_name)
            except Exception:
                pass
            try:
                panels = sorted(list(app.query(TerminalPanel)), key=lambda p: p._term_id)
            except Exception:
                panels = []

        # Apply the team mapping to T1..T4 preserving terminal logs/history.
        for slot in range(min(4, len(panels))):
            try:
                panel = panels[slot]
                target_agent_name = agent_types[slot]
                target_agent_obj = app._available_agents.get(target_agent_name)
                if target_agent_obj is None:
                    continue
                panel.update_agent(target_agent_obj, target_agent_name)
            except Exception:
                pass

        try:
            if app._active_team is not None:
                try:
                    app.query_one(f"#team-{app._active_team}", Button).remove_class("-active-team")
                except Exception:
                    pass
            app._active_team = idx
            try:
                app.query_one(f"#team-{idx}", Button).add_class("-active-team")
            except Exception:
                pass
            app._update_team_playbook_preview(idx)
        except Exception:
            pass

        try:
            app._set_active_terminal(previous_active)
        except Exception:
            pass

        try:
            panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
            try:
                panel.query_one(f"#term-log-{app._active_term_id}", RichLog).write(
                    RichText.from_markup(
                        f"  [dim]Applied Team [bold]#{idx + 1}: {label}[/bold] to T1-T4 (history preserved)\n"
                        f"  Playbook: {app._team_playbook_hint(idx)}[/dim]"
                    )
                )
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def session_open(self, idx: int) -> None:
        app = self.app
        try:
            path = (app._session_files or {}).get(idx)
            if not path:
                return
            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl

            messages = load_history_from_jsonl(path)

            # Merge into active agent similar to /load behavior
            from cai.repl.commands.parallel import ParallelCommand
            from cai.sdk.agents.models.openai_chatcompletions import (
                ACTIVE_MODEL_INSTANCES,
                PERSISTENT_MESSAGE_HISTORIES,
            )
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            parallel_cmd = ParallelCommand()
            _current_agent = AGENT_MANAGER.get_active_agent()
            current_agent_name = AGENT_MANAGER._active_agent_name
            if not current_agent_name:
                return

            current_history = AGENT_MANAGER.get_message_history(current_agent_name) or []
            original_signatures = set()
            for msg in current_history:
                try:
                    sig = parallel_cmd._get_message_signature(msg)
                    if sig:
                        original_signatures.add(sig)
                except Exception:
                    continue

            unique_messages = []
            for msg in messages:
                try:
                    sig = parallel_cmd._get_message_signature(msg)
                except Exception:
                    sig = None
                if sig and sig not in original_signatures:
                    unique_messages.append(msg)
                    original_signatures.add(sig)

            if not unique_messages:
                try:
                    panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                    try:
                        panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                            RichText.from_markup(
                                f"[dim]No new messages to add from {os.path.basename(path)}[/dim]"
                            )
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
                return

            final_history = current_history + unique_messages

            # Find active model instance
            model_instance = None
            for (name, inst_id), model_ref in (ACTIVE_MODEL_INSTANCES or {}).items():
                try:
                    if name == current_agent_name:
                        model_instance = model_ref() if model_ref else None
                        break
                except Exception:
                    continue

            if model_instance:
                try:
                    model_instance.message_history.clear()
                    os.environ["CAI_CONTEXT_USAGE"] = "0.0"
                    for msg in final_history:
                        try:
                            model_instance.add_to_message_history(msg)
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                PERSISTENT_MESSAGE_HISTORIES[current_agent_name] = final_history

            AGENT_MANAGER._message_history[current_agent_name] = final_history

            try:
                panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                try:
                    panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                        RichText.from_markup(
                            f"[green]Loaded {len(unique_messages)} messages into {current_agent_name} from {os.path.basename(path)}[/green]"
                        )
                    )
                except Exception:
                    pass
            except Exception:
                pass

        except Exception:
            try:
                panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                try:
                    panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                        RichText.from_markup("[red]Error loading session[/red]")
                    )
                except Exception:
                    pass
            except Exception:
                pass

    @work(exclusive=False)
    async def session_delete(self, idx: int) -> None:
        app = self.app
        try:
            path = (app._session_files or {}).get(idx)
            if not path:
                return
            try:
                os.remove(path)
            except Exception:
                pass
            try:
                # Collapse any expanded action areas
                if hasattr(app, "_session_action_containers"):
                    for k, cont in list(app._session_action_containers.items()):
                        try:
                            cont.display = False
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                app._session_selected_idx = None
                app._update_session_preview(None)
            except Exception:
                pass
            try:
                app._populate_sessions_list_worker()
            except Exception:
                try:
                    await app._populate_sessions_list()
                except Exception:
                    pass
        except Exception:
            pass

    @work(exclusive=False)
    async def session_rename(self, idx: int, new_basename: str) -> None:
        app = self.app
        try:
            path = (app._session_files or {}).get(idx)
            if not path:
                return
            dest = os.path.join("logs", new_basename)
            if not dest.endswith(".jsonl"):
                dest += ".jsonl"
            try:
                os.rename(path, dest)
            except Exception:
                pass
            try:
                await app._populate_sessions_list()
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def session_resume(self, idx: int) -> None:
        app = self.app
        try:
            path = (app._session_files or {}).get(idx)
            if not path:
                return
            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl

            messages = load_history_from_jsonl(path)
            best_key = app._infer_agent_from_session_messages(messages)

            opened_terminal = False
            if best_key and best_key in app._available_agents:
                try:
                    agent_obj = app._available_agents.get(best_key)
                    await app._add_terminal(agent_obj, best_key)
                    opened_terminal = True
                except Exception:
                    opened_terminal = False

            if not opened_terminal:
                try:
                    panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                    agent = getattr(panel, "_agent", None)
                    agent_name = getattr(panel, "_agent_name", None)
                    await app._add_terminal(agent, agent_name)
                except Exception:
                    pass

            try:
                app._session_open_worker(idx)
            except Exception:
                try:
                    await app._session_open_worker(idx)
                except Exception:
                    pass
            # If we already have targets in state.json, avoid re-running reconnaissance by default.
            try:
                st = _read_state()
                if st and st.get("targets"):
                    try:
                        app._resume_skip_recon = True
                    except Exception:
                        pass
                    try:
                        panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                        try:
                            panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                                RichText.from_markup(
                                    f"[dim]Resume: {len(st.get('targets', []))} targets present — reconnaissance will be skipped unless forced[/dim]"
                                )
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass

            # Deep resume: if intelligence.json recorded an active VPN, offer to reconnect.
            try:
                from cai.orchestration.persistence import _read_journal
                from cai.tools.common import _get_workspace_dir
                ws = _get_workspace_dir()
                j = _read_journal(ws)
                vpn_entries = [
                    e for e in j.get("entries", [])
                    if (e.get("fact") or {}).get("type") == "vpn_connected"
                ]
                if vpn_entries:
                    latest = vpn_entries[-1]
                    cfg_path = (latest.get("fact") or {}).get("config_path")
                    if cfg_path and os.path.exists(cfg_path):
                        from cai.tui.screens.config import VpnResumeModal
                        reconnect = await app.push_screen_wait(VpnResumeModal(cfg_path))
                        if reconnect:
                            from cai.network.vpn_manager import get_manager
                            mgr = get_manager()
                            ok, err = mgr.load_config(cfg_path)
                            if ok:
                                if mgr.needs_auth():
                                    from cai.tui.screens.config import VpnCredentialScreen
                                    cred = await app.push_screen_wait(VpnCredentialScreen())
                                    if cred and cred[0] == "credentials":
                                        ok2, err2 = mgr.connect(auth_creds=(cred[1], cred[2]))
                                    else:
                                        ok2, err2 = False, "Cancelled"
                                else:
                                    ok2, err2 = mgr.connect()
                                if ok2:
                                    app.notify("VPN reconnecting from session state…", severity="information")
                                else:
                                    app.notify(f"VPN reconnect failed: {err2}", severity="warning")
                            else:
                                app.notify(f"VPN config no longer valid: {err}", severity="warning")
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def session_export(self, idx: int, dest_path: str | None = None) -> None:
        app = self.app
        try:
            path = (app._session_files or {}).get(idx)
            if not path:
                return
            dest = dest_path or os.path.join(os.getcwd(), "tui_config_export.json")
            try:
                import shutil

                if os.path.isdir(dest):
                    dest_path2 = os.path.join(dest, os.path.basename(path))
                else:
                    parent = os.path.dirname(dest)
                    if parent and not os.path.exists(parent):
                        os.makedirs(parent, exist_ok=True)
                    dest_path2 = dest
                shutil.copy2(path, dest_path2)
                try:
                    panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                    try:
                        panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                            RichText.from_markup(
                                f"[green]Exported {os.path.basename(path)} to {dest_path2}[/green]"
                            )
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                try:
                    panel = app.query_one(f"#terminal-panel-{app._active_term_id}")
                    try:
                        panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                            RichText.from_markup("[red]Export failed[/red]")
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

    @work(exclusive=True)
    async def nmap_scan(self, target: str, timeout: int = 60) -> dict:
        """Example long-running network scan worker (skeleton).

        Replace the internals with the real nmap invocation or tool call.
        """
        logger.info("TuiController: starting nmap scan %s", target)
        try:
            # Respect resume-skip flag to avoid re-running reconnaissance by default.
            try:
                app = self.app
                if getattr(app, "_resume_skip_recon", False):
                    try:
                        app._log_to_active_terminal(
                            f"[nmap] Skipping scan for {target} due to resume-skip-recon flag (use force to override).",
                            style="#ffaa00",
                        )
                    except Exception:
                        pass
                    return {"target": target, "status": "skipped", "reason": "resume_skip_recon"}
            except Exception:
                pass
            # Placeholder implementation: simulate work
            await asyncio.sleep(min(timeout, 5))
            return {"target": target, "status": "ok"}
        except asyncio.CancelledError:
            logger.info("TuiController: nmap scan %s cancelled", target)
            raise
        except Exception:
            logger.exception("TuiController: nmap scan %s failed", target)
            return {"target": target, "status": "error"}

    @work(exclusive=False)
    async def process_crawl4ai(self, payload: dict) -> dict:
        """Example background processor for Crawl4AI results (skeleton).

        Replace with the real fetch/processing pipeline.
        """
        logger.info("TuiController: processing Crawl4AI payload")
        try:
            await asyncio.sleep(0.1)
            return {"ok": True}
        except asyncio.CancelledError:
            logger.info("TuiController: Crawl4AI processing cancelled")
            raise
        except Exception:
            logger.exception("TuiController: Crawl4AI processing failed")
            return {"ok": False}
