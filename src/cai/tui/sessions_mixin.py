"""SessionsMixin — session discovery, preview, and CRUD methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

import os
from typing import Any, cast

from textual import work
from textual.widgets import Static, ListItem
from rich.text import Text as RichText

from cai.tui.screens.common import ConfirmModal, PromptModal
from cai.tui.components.header import _pretty_name
from cai.tui.components.sidebar import Sidebar


class SessionsMixin:
    """Mixin providing session discovery, preview, and lifecycle helpers."""

    async def _populate_sessions_list(self) -> None:
        """Discover JSONL session files in `logs/` and delegate rendering to Sidebar.SessionsTab."""
        logs_dir = "logs"
        try:
            raw_names = [f for f in os.listdir(logs_dir) if f.endswith(".jsonl")]
        except Exception:
            raw_names = []

        files = []
        for fname in raw_names:
            path = os.path.join(logs_dir, fname)
            try:
                mtime_ts = os.path.getmtime(path)
            except Exception:
                mtime_ts = 0
            files.append((fname, mtime_ts))

        files_sorted = sorted(files, key=lambda t: t[1], reverse=True)

        # Build mapping and an ordered list of session paths
        self._session_files = {}
        ordered: list[str] = []
        for idx, (fname, _mtime) in enumerate(files_sorted):
            path = os.path.join(logs_dir, fname)
            self._session_files[idx] = path
            ordered.append(path)

        try:
            sidebar = cast(Any, self.query_one(Sidebar))
            sidebar.set_sessions(ordered)
        except Exception:
            pass

    @work(exclusive=True)
    async def _populate_sessions_list_worker(self) -> None:
        await self._populate_sessions_list()

    @work(exclusive=False)
    async def _session_open_worker(self, idx: int) -> None:
        try:
            self.controller.session_open(idx)
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_delete_worker(self, idx: int) -> None:
        # Defer the file removal and refresh to the controller worker; but keep the confirm modal here.
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            result = await self.push_screen_wait(ConfirmModal(f"Delete {os.path.basename(path)}?"))
            if not result:
                return
            try:
                self.controller.session_delete(idx)
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_rename_worker(self, idx: int) -> None:
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            default = os.path.basename(path)
            result = await self.push_screen_wait(PromptModal("Rename file to:", default))
            if not result:
                return
            try:
                # Delegate rename to controller
                self.controller.session_rename(idx, result)
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_resume_worker(self, idx: int) -> None:
        try:
            self.controller.session_resume(idx)
        except Exception:
            pass

    def _set_selected_session(self, idx: int | None) -> None:
        """Set the currently selected session index and update visuals."""
        try:
            self._session_selected_idx = idx
        except Exception:
            self._session_selected_idx = None

        # Update visual selection for list items
        try:
            for k in list(self._session_files.keys()):
                try:
                    li = self.query_one(f"#session-item-{k}", ListItem)
                    if k == idx:
                        li.add_class("-selected")
                    else:
                        li.remove_class("-selected")
                except Exception:
                    pass
        except Exception:
            pass

        # Update preview for the newly selected session
        try:
            self._update_session_preview(self._session_selected_idx)
        except Exception:
            pass

    def _toggle_session_actions(self, idx: int) -> None:
        """Toggle the visibility of the action buttons for a session list item.

        Collapses all other session action areas and toggles the chosen one.
        Selecting a session also updates the preview and visual selection.
        """
        try:
            if not hasattr(self, "_session_action_containers"):
                return
            for k, container in self._session_action_containers.items():
                try:
                    if k == idx:
                        # toggle this container
                        current = bool(getattr(container, "display", False))
                        container.display = not current
                    else:
                        container.display = False
                except Exception:
                    pass
            # Update selection/preview as if the session was selected
            try:
                self._set_selected_session(idx)
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_export_worker(self, idx: int) -> None:
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            default = os.path.basename(path)
            result = await self.push_screen_wait(
                PromptModal("Export to (path or directory):", default)
            )
            if not result:
                return
            try:
                self.controller.session_export(idx, os.path.expanduser(result))
            except Exception:
                pass
        except Exception:
            pass

    def _update_session_preview(self, idx: int | None) -> None:
        """Update the `#session-preview` Static with token/cost summary and a snippet."""
        try:
            preview = self.query_one("#session-preview", Static)
        except Exception:
            return

        if idx is None:
            try:
                preview.update("")
            except Exception:
                pass
            return

        path = self._session_files.get(idx)
        if not path or not os.path.exists(path):
            try:
                preview.update("[dim]No preview available")
            except Exception:
                pass
            return

        # Import helpers if available; avoid leaving names unbound for static analysis
        get_token_stats_fn = None
        try:
            from cai.sdk.agents.run_to_jsonl import (
                get_token_stats as _get_token_stats,
                load_history_from_jsonl,
            )

            get_token_stats_fn = _get_token_stats
            messages = load_history_from_jsonl(path)
        except Exception:
            messages = []

        # Try to get token/cost stats
        try:
            if get_token_stats_fn is not None:
                model_name, prompt_t, completion_t, total_cost, active, idle = get_token_stats_fn(
                    path
                )
                stats = f"Model: {model_name or 'unknown'} · prompt:{prompt_t} completion:{completion_t} cost:{total_cost}"
            else:
                raise Exception("token stats unavailable")
        except Exception:
            stats = "Model: ? · prompt:? completion:? cost:?"

        # Build snippet: last 6 non-system messages
        snippet_lines = []
        try:
            filtered = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
            for m in filtered[-6:]:
                role = m.get("role", "")
                content = m.get("content") or ""
                if not content:
                    # handle nested message structures
                    content = str(m)
                # single-line snippet
                line = content.strip().splitlines()[0][:160]
                snippet_lines.append(f"{role}: {line}")
        except Exception:
            snippet_lines = []

        snippet = "\n".join(snippet_lines) or "(no preview)"

        body = f"[bold]{os.path.basename(path)}[/bold]\n{stats}\n\n{snippet}"
        try:
            preview.update(RichText.from_markup(body))
        except Exception:
            try:
                preview.update(body)
            except Exception:
                pass

    def _infer_agent_from_session_messages(self, messages: list) -> str | None:
        """Try to infer the best-matching available agent key from session messages.

        Heuristic:
        - Collect candidate agent identifiers from message fields: `agent_name`, `name`, `sender`.
        - Normalize and score available agents by matching candidate identifiers against
          agent keys and display names (`agent.name` or `_pretty_name(key)`).
        - Return the agent key with the highest score, or None if no confident match.
        """
        try:
            from collections import Counter
        except Exception:
            Counter = None

        if not messages:
            return None

        # Collect candidate identifiers from messages
        candidates = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            for k in ("agent_name", "name", "sender"):
                v = msg.get(k)
                if v:
                    # Remove instance suffixes like ' #1'
                    try:
                        v2 = v.split(" #")[0]
                    except Exception:
                        v2 = v
                    candidates.append(str(v2))

        if not candidates:
            return None

        counts = Counter(candidates) if Counter is not None else {}

        # Score each available agent
        best_key = None
        best_score = 0

        for key, agent in self._available_agents.items():
            # Build match variants
            display = getattr(agent, "name", None) or _pretty_name(key)
            variants = {
                key,
                key.replace("_agent", "").replace("_pattern", "").replace("_swarm", ""),
            }
            variants.add(display)
            variants.add(display.replace(" ", ""))

            score = 0
            for cand, cnt in counts.items() if counts else []:
                cand_n = "".join([c for c in cand.lower() if c.isalnum()])
                for v in variants:
                    try:
                        v_n = "".join([c for c in str(v).lower() if c.isalnum()])
                    except Exception:
                        v_n = ""
                    if not v_n or not cand_n:
                        continue
                    if cand_n == v_n:
                        score += 10 * cnt
                    elif cand_n in v_n or v_n in cand_n:
                        score += 5 * cnt
                    else:
                        # partial word overlap
                        if any(part in v_n for part in cand_n.split() if len(part) > 3):
                            score += 2 * cnt

            if score > best_score:
                best_score = score
                best_key = key

        # Require some minimal confidence to accept match
        if best_score >= 5:
            return best_key
        return None
