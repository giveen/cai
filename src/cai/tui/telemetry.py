"""TelemetryMixin — metrics, cost tracking, and context snapshot methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
import time
from typing import Any, cast

from textual import work
from textual.widgets import Static, TextArea
from rich.text import Text as RichText

from cai.config import CAI_CTX_LIMIT
from cai.tui.screens.common import ContextUsageModal
from cai.tui.components.terminal import TerminalPanel


class TelemetryMixin:
    """Mixin providing telemetry, cost tracking, and context snapshot methods."""

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _ensure_term_stats(self, term_id: int) -> dict:
        stats = self._telemetry_stats_by_term.get(term_id)
        if stats is None:
            stats = {
                "runs": 0,
                "errors": 0,
                "cancelled": 0,
                "tool_calls": 0,
                "retrieval_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0.0,
                "last_cost": 0.0,
                "model": self._model_name,
                "input_price_per_token": 0.0,
                "output_price_per_token": 0.0,
                "sum_first_token_ms": 0,
                "sum_total_latency_ms": 0,
                "last_first_token_ms": None,
                "last_total_latency_ms": None,
                "last_status": "idle",
            }
            self._telemetry_stats_by_term[term_id] = stats
        return stats

    def _is_retrieval_tool(self, tool_name: str) -> bool:
        name = (tool_name or "").lower()
        retrieval_markers = (
            "search",
            "retriev",
            "rag",
            "file_search",
            "web_search",
            "google",
            "shodan",
            "mcp",
        )
        return any(marker in name for marker in retrieval_markers)

    def _rotate_telemetry_if_needed(self) -> None:
        try:
            if not os.path.exists(self._telemetry_file):
                return
            if os.path.getsize(self._telemetry_file) <= self._telemetry_max_bytes:
                return

            max_backups = max(1, int(self._telemetry_max_backups))
            oldest = f"{self._telemetry_file}.{max_backups}"
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except Exception:
                    pass

            for i in range(max_backups - 1, 0, -1):
                src = f"{self._telemetry_file}.{i}"
                dst = f"{self._telemetry_file}.{i + 1}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass

            try:
                os.replace(self._telemetry_file, f"{self._telemetry_file}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _persist_telemetry_record(self, record: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._telemetry_file), exist_ok=True)
            self._rotate_telemetry_if_needed()
            with open(self._telemetry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            pass

    def _emit_telemetry(self, term_id: int, agent_name: str, event_type: str, data: dict) -> None:
        rec = {
            "event": event_type,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ts_ms": self._now_ms(),
            "terminal_id": term_id,
            "agent_name": agent_name,
            "data": data,
        }
        self._persist_telemetry_record(rec)

    def _load_recent_telemetry_events(self, limit: int = 20) -> list[dict]:
        records: list[dict] = []
        try:
            paths: list[str] = []
            for i in range(self._telemetry_max_backups, 0, -1):
                p = f"{self._telemetry_file}.{i}"
                if os.path.exists(p):
                    paths.append(p)
            if os.path.exists(self._telemetry_file):
                paths.append(self._telemetry_file)

            for path in paths:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                            if isinstance(obj, dict):
                                records.append(obj)
                        except Exception:
                            continue
        except Exception:
            return []
        if limit <= 0:
            return records
        return records[-limit:]

    def _render_metrics_summary_text(self) -> str:
        if not self._telemetry_stats_by_term:
            return "No stats yet. Run a prompt to collect cost and usage metrics."

        def _fmt_cost(value: float) -> str:
            return f"${float(value):.2f}"

        def _fmt_elapsed(seconds: float) -> str:
            total = max(0, int(seconds))
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            if h > 0:
                return f"{h}h {m}m {s}s"
            if m > 0:
                return f"{m}m {s}s"
            return f"{s}s"

        total_runs = 0
        total_tokens = 0
        total_tools = 0
        total_retr = 0
        total_errors = 0
        total_cost = 0.0
        active_terms = 0

        lines: list[str] = ["[bold]Stats[/bold]"]
        lines.append(f"Total Cost: {_fmt_cost(self._session_cost_total())}")
        lines.append("═══════════════════════")

        pricing_lines: list[str] = []

        for term_id in sorted(self._telemetry_stats_by_term.keys()):
            s = self._telemetry_stats_by_term[term_id]
            total_runs += int(s.get("runs", 0) or 0)
            total_tokens += int(s.get("total_tokens", 0) or 0)
            total_tools += int(s.get("tool_calls", 0) or 0)
            total_retr += int(s.get("retrieval_calls", 0) or 0)
            total_errors += int(s.get("errors", 0) or 0)
            term_cost = float(s.get("cost_total", 0.0) or 0.0)
            total_cost += term_cost
            if s.get("last_status") == "running":
                active_terms += 1

            lines.append(f"Terminal {term_id}: {_fmt_cost(term_cost)}")

            model_name = str(s.get("model", self._model_name) or self._model_name)
            in_price = float(s.get("input_price_per_token", 0.0) or 0.0)
            out_price = float(s.get("output_price_per_token", 0.0) or 0.0)
            pricing_lines.append(
                f"T{term_id}: model={model_name} in={in_price:.8f} out={out_price:.8f}"
            )

        if active_terms <= 0:
            try:
                active_terms = len(list(self.query(TerminalPanel)))
            except Exception:
                active_terms = 0

        avg_cost_per_turn = total_cost / max(1, total_runs)
        elapsed = _fmt_elapsed(time.time() - self._stats_started_ts)

        lines.append("")
        lines.append("[bold]Usage Metrics[/bold]")
        lines.append(f"Interactions: {total_runs}")
        lines.append(f"Total tokens: {total_tokens}")
        lines.append(f"Average cost per turn: {_fmt_cost(avg_cost_per_turn)}")
        lines.append(f"Time elapsed: {elapsed}")
        lines.append(f"Active terminals: {active_terms}")
        lines.append("")
        lines.append("[bold]Model pricing details[/bold]")
        lines.extend(pricing_lines)
        lines.append("")

        price_limit, enabled = self._get_price_limit()
        if enabled:
            pct = (total_cost / max(price_limit, 1e-9)) * 100.0
            state = "PAUSED" if self._price_limit_paused else ("WARNING" if pct >= 80.0 else "OK")
            lines.append(
                f"Cost limit: CAI_PRICE_LIMIT={price_limit:.2f} · used={_fmt_cost(total_cost)} ({pct:.1f}%) · {state}"
            )
        else:
            lines.append("Cost limit: disabled (set CAI_PRICE_LIMIT > 0 to enable)")

        lines.append(f"Diagnostics: tools={total_tools} retr={total_retr} errors={total_errors}")
        return "\n".join(lines)

    def _get_price_limit(self) -> tuple[float, bool]:
        raw = os.getenv("CAI_PRICE_LIMIT", "").strip()
        if not raw:
            return (0.0, False)
        try:
            value = float(raw)
            if value <= 0:
                return (0.0, False)
            return (value, True)
        except Exception:
            return (0.0, False)

    def _session_cost_total(self) -> float:
        total = 0.0
        for s in self._telemetry_stats_by_term.values():
            try:
                total += float(s.get("cost_total", 0.0) or 0.0)
            except Exception:
                continue
        return total

    def _refresh_price_limit_state(self, emit_logs: bool = True) -> None:
        limit, enabled = self._get_price_limit()
        if not enabled:
            self._price_limit_warned = False
            self._price_limit_paused = False
            return

        total_cost = self._session_cost_total()
        ratio = total_cost / max(limit, 1e-9)

        if ratio >= 1.0:
            if emit_logs and not self._price_limit_paused:
                self._log_to_active_terminal(
                    f"[cost] limit exceeded ({total_cost:.4f}/{limit:.4f}). Auto-pausing new prompts.",
                    style="#ff4444",
                )
            self._price_limit_paused = True
            return

        self._price_limit_paused = False
        if ratio >= 0.8 and not self._price_limit_warned:
            self._price_limit_warned = True
            if emit_logs:
                self._log_to_active_terminal(
                    f"[cost] warning: approaching limit ({total_cost:.4f}/{limit:.4f}).",
                    style="#ffcc00",
                )
        elif ratio < 0.8:
            self._price_limit_warned = False

    def _can_dispatch_prompt(self) -> bool:
        self._refresh_price_limit_state(emit_logs=True)
        return not self._price_limit_paused

    def _render_metrics_events_text(self, limit: int = 20) -> str:
        events = self._load_recent_telemetry_events(limit=limit)
        if not events:
            return "No telemetry events yet."

        lines: list[str] = []
        for rec in events[-limit:]:
            ts = rec.get("timestamp", "?")
            terminal = rec.get("terminal_id", "?")
            event_name = rec.get("event", "?")
            data = rec.get("data", {}) or {}
            if isinstance(data, dict):
                if "tool_name" in data:
                    detail = f" tool={data.get('tool_name')}"
                elif "latency_ms" in data:
                    detail = f" latency={data.get('latency_ms')}ms"
                elif "status" in data:
                    detail = f" status={data.get('status')}"
                else:
                    detail = ""
            else:
                detail = ""
            lines.append(f"{ts} · T{terminal} · {event_name}{detail}")
        return "\n".join(lines)

    def _update_metrics_view(self) -> None:
        try:
            summary = self.query_one("#metrics-summary", Static)
            summary_text = self._render_metrics_summary_text()
            try:
                summary.update(RichText.from_markup(summary_text))
            except Exception:
                summary.update(summary_text)
        except Exception:
            pass

        try:
            events = self.query_one("#metrics-events", Static)
            events_text = self._render_metrics_events_text(limit=20)
            events.update(events_text)
        except Exception:
            pass

    @work(exclusive=False)
    async def _refresh_metrics_view_worker(self) -> None:
        self._update_metrics_view()

    def _run_key(self, term_id: int) -> int:
        return term_id

    def _tool_key(self, term_id: int, call_id: str) -> str:
        return f"{term_id}:{call_id}"

    def _estimate_tokens_from_text(self, text: str) -> int:
        raw = str(text or "")
        # Fast and deterministic token estimate for UI-only attribution.
        return max(0, (len(raw) + 3) // 4)

    def _format_k_tokens(self, count: int) -> str:
        value = int(count or 0)
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)

    def _usage_field(self, obj, key: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _usage_details_to_dict(self, details) -> dict:
        if details is None:
            return {}
        if isinstance(details, dict):
            return details
        if hasattr(details, "model_dump"):
            try:
                dumped = details.model_dump()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
        if hasattr(details, "__dict__"):
            try:
                as_dict = dict(details.__dict__)
                if isinstance(as_dict, dict):
                    return as_dict
            except Exception:
                pass
        return {}

    def _extract_usage_detail_totals(self, usage) -> dict:
        """Extract normalized usage totals from provider responses when available."""
        input_tokens = int(
            self._usage_field(usage, "input_tokens", 0)
            or self._usage_field(usage, "prompt_tokens", 0)
            or 0
        )
        output_tokens = int(
            self._usage_field(usage, "output_tokens", 0)
            or self._usage_field(usage, "completion_tokens", 0)
            or 0
        )
        total_tokens = int(self._usage_field(usage, "total_tokens", 0) or 0)
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens

        input_details = self._usage_details_to_dict(
            self._usage_field(usage, "input_tokens_details", None)
            or self._usage_field(usage, "prompt_tokens_details", None)
        )
        output_details = self._usage_details_to_dict(
            self._usage_field(usage, "output_tokens_details", None)
            or self._usage_field(usage, "completion_tokens_details", None)
        )

        cached_tokens = int(input_details.get("cached_tokens", 0) or 0)
        reasoning_tokens = int(
            output_details.get("reasoning_tokens", 0)
            or self._usage_field(usage, "reasoning_tokens", 0)
            or 0
        )

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
        }

    def _context_usage_level(self, pct: float) -> tuple[str, str]:
        if pct < 50.0:
            return ("GREEN", "green")
        if pct < 80.0:
            return ("YELLOW", "yellow")
        return ("RED", "red")

    def _resolve_model_context_max_tokens(self, model_name: str) -> int:
        name = str(model_name or "").lower().strip()
        try:
            from cai.util import get_model_input_tokens

            max_tokens = int(get_model_input_tokens(name) or 0)
            if max_tokens > 0:
                return max_tokens
        except Exception:
            pass

        # Fallback map for safety when util lookup is unavailable. Use the
        # central `CAI_CTX_LIMIT` as a conservative default so the UI and
        # tooling share a single source of truth for maximum context size.
        fallback_map = {
            "alias1": CAI_CTX_LIMIT,
            "gpt-4": CAI_CTX_LIMIT,
            "gpt-4o": CAI_CTX_LIMIT,
            "gpt-5": CAI_CTX_LIMIT,
            "claude": CAI_CTX_LIMIT,
            "sonnet": CAI_CTX_LIMIT,
        }
        for key, value in fallback_map.items():
            if key in name:
                return int(value)
        return int(CAI_CTX_LIMIT)

    def _context_categories_blank(self) -> dict:
        return {
            "system_prompt_tokens": 0,
            "tool_definitions_tokens": 0,
            "memory_rag_tokens": 0,
            "user_prompt_tokens": 0,
            "assistant_response_tokens": 0,
            "tool_calls_tokens": 0,
            "tool_results_tokens": 0,
        }

    def _context_snapshot_summary_text(self, snapshot: dict) -> str:
        used = int(snapshot.get("used_tokens", 0) or 0)
        max_tokens = int(snapshot.get("max_tokens", 0) or 0)
        pct = float(snapshot.get("pct_used", 0.0) or 0.0)
        free = int(snapshot.get("free_tokens", 0) or 0)
        last_input = int(snapshot.get("last_input_tokens", 0) or 0)
        return (
            f"Context usage T{snapshot.get('terminal_id', '?')}: "
            f"used {used}/{max_tokens} ({pct:.1f}%), free {free}, last_input {last_input}"
        )

    def _render_context_usage_menu_text(self, snapshot: dict) -> str:
        if not snapshot:
            return "No context data yet. Run one prompt to initialize."

        used = int(snapshot.get("used_tokens", 0) or 0)
        max_tokens = int(snapshot.get("max_tokens", 0) or 0)
        free = int(snapshot.get("free_tokens", 0) or 0)
        pct = float(snapshot.get("pct_used", 0.0) or 0.0)
        last_input = int(snapshot.get("last_input_tokens", 0) or 0)
        categories = snapshot.get("categories", {}) or {}

        bar_width = 28
        fill = 0
        if max_tokens > 0:
            fill = min(bar_width, max(0, int(round((used / max_tokens) * bar_width))))
        bar = ("#" * fill) + ("-" * (bar_width - fill))

        lines: list[str] = [
            f"[bold]Context Usage · T{snapshot.get('terminal_id', '?')}[/bold]",
            f"Model: {snapshot.get('model', '?')} · Agent: {snapshot.get('agent_name', '?')}",
            f"Used: {self._format_k_tokens(used)} / {self._format_k_tokens(max_tokens)} ({pct:.1f}%)",
            f"Free: {self._format_k_tokens(free)} · Last input: {self._format_k_tokens(last_input)}",
            f"[{bar}]",
            "",
            "[bold]Context Level[/bold]",
            f"Level: [{self._context_usage_level(pct)[1]}]{self._context_usage_level(pct)[0]}[/{self._context_usage_level(pct)[1]}]",
            "Legend: [green]GREEN[/green] < 50% · [yellow]YELLOW[/yellow] 50-79% · [red]RED[/red] >= 80%",
            "",
            "[bold]Category Breakdown[/bold]",
        ]

        ordered = [
            ("System prompt", "system_prompt_tokens"),
            ("Tool definitions", "tool_definitions_tokens"),
            ("Memory / RAG", "memory_rag_tokens"),
            ("User prompts", "user_prompt_tokens"),
            ("Assistant responses", "assistant_response_tokens"),
            ("Tool calls", "tool_calls_tokens"),
            ("Tool results", "tool_results_tokens"),
        ]
        denom = max(1, used)
        for label, key in ordered:
            value = int(categories.get(key, 0) or 0)
            cpct = (value / denom) * 100.0
            lines.append(f"- {label}: {self._format_k_tokens(value)} ({cpct:.1f}%)")

        lines.append("")
        lines.append(f"Updated: {snapshot.get('timestamp', '?')}")
        return "\n".join(lines)

    def _build_context_snapshot(
        self,
        term_id: int,
        agent_name: str,
        model_name: str,
        run_data: dict,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        usage_details: dict | None = None,
    ) -> dict:
        categories = self._context_categories_blank()
        details = usage_details or {}

        run_categories = (run_data or {}).get("categories", {}) or {}
        for key in categories.keys():
            categories[key] = int(run_categories.get(key, 0) or 0)

        # Prefer usage-reported values when available.
        if input_tokens > 0:
            categories["user_prompt_tokens"] = input_tokens
        if output_tokens > 0:
            categories["assistant_response_tokens"] = output_tokens

        # Provider-side details (when available) refine attribution.
        # cached_tokens are usually prompt prefix/system/tool material reused across calls.
        # reasoning_tokens are part of assistant generation budget.
        cached_tokens = int(details.get("cached_tokens", 0) or 0)
        reasoning_tokens = int(details.get("reasoning_tokens", 0) or 0)
        if cached_tokens > 0:
            categories["system_prompt_tokens"] += cached_tokens
            categories["user_prompt_tokens"] = max(
                0, int(categories["user_prompt_tokens"]) - cached_tokens
            )
        if reasoning_tokens > 0:
            categories["assistant_response_tokens"] += reasoning_tokens

        used_tokens = int(total_tokens or 0)
        estimated_sum = sum(int(v or 0) for v in categories.values())
        if used_tokens <= 0:
            used_tokens = estimated_sum

        # Keep breakdown bounded to used tokens for sane percentages.
        fixed = (
            int(categories["system_prompt_tokens"])
            + int(categories["tool_definitions_tokens"])
            + int(categories["memory_rag_tokens"])
            + int(categories["user_prompt_tokens"])
            + int(categories["assistant_response_tokens"])
        )
        remaining = max(0, used_tokens - fixed)
        categories["tool_calls_tokens"] = min(int(categories["tool_calls_tokens"]), remaining)
        remaining -= int(categories["tool_calls_tokens"])
        categories["tool_results_tokens"] = min(int(categories["tool_results_tokens"]), remaining)

        max_tokens = max(1, self._resolve_model_context_max_tokens(model_name))
        free_tokens = max(0, max_tokens - used_tokens)
        pct_used = min(100.0, (used_tokens / max_tokens) * 100.0)

        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "terminal_id": term_id,
            "agent_name": agent_name,
            "model": model_name,
            "used_tokens": used_tokens,
            "max_tokens": max_tokens,
            "pct_used": round(pct_used, 2),
            "free_tokens": free_tokens,
            "last_input_tokens": int(categories["user_prompt_tokens"]),
            "provider_usage_details": {
                "cached_tokens": cached_tokens,
                "reasoning_tokens": reasoning_tokens,
            },
            "categories": categories,
        }

    def _rotate_context_snapshots_if_needed(self) -> None:
        try:
            if not os.path.exists(self._context_snapshots_file):
                return
            if os.path.getsize(self._context_snapshots_file) <= self._context_snapshots_max_bytes:
                return

            max_backups = max(1, int(self._context_snapshots_max_backups))
            oldest = f"{self._context_snapshots_file}.{max_backups}"
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except Exception:
                    pass

            for i in range(max_backups - 1, 0, -1):
                src = f"{self._context_snapshots_file}.{i}"
                dst = f"{self._context_snapshots_file}.{i + 1}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass

            try:
                os.replace(self._context_snapshots_file, f"{self._context_snapshots_file}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _persist_context_snapshot(self, snapshot: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._context_snapshots_file), exist_ok=True)
            self._rotate_context_snapshots_if_needed()
            with open(self._context_snapshots_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=True) + "\n")
        except Exception:
            pass

    def _load_context_snapshots_latest_by_term(self) -> dict[int, dict]:
        latest: dict[int, dict] = {}
        try:
            paths: list[str] = []
            for i in range(self._context_snapshots_max_backups, 0, -1):
                p = f"{self._context_snapshots_file}.{i}"
                if os.path.exists(p):
                    paths.append(p)
            if os.path.exists(self._context_snapshots_file):
                paths.append(self._context_snapshots_file)

            for path in paths:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                            if not isinstance(obj, dict):
                                continue
                            term_id = int(obj.get("terminal_id", 0) or 0)
                            if term_id <= 0:
                                continue
                            latest[term_id] = obj
                        except Exception:
                            continue
        except Exception:
            return {}
        return latest

    def _get_context_snapshot(self, term_id: int | None = None) -> dict | None:
        tid = int(term_id or self._active_term_id)
        snap = self._context_snapshot_by_term.get(tid)
        if snap is not None:
            return snap
        # Fallback to latest from any terminal if active has no data yet.
        if self._context_snapshot_by_term:
            return self._context_snapshot_by_term[sorted(self._context_snapshot_by_term.keys())[-1]]
        return None

    @work(exclusive=False)
    async def _open_context_usage_menu_worker(self) -> None:
        while True:
            snapshot = self._get_context_snapshot(self._active_term_id)
            if snapshot is None:
                title = "Context Usage"
                content = "No context data yet. Run one prompt to initialize."
                summary_text = ""
            else:
                title = f"Context Usage · T{snapshot.get('terminal_id', '?')}"
                content = self._render_context_usage_menu_text(snapshot)
                summary_text = self._context_snapshot_summary_text(snapshot)

            result = await self.push_screen_wait(ContextUsageModal(title, content, summary_text))
            if not result:
                return

            action = result[0] if isinstance(result, (tuple, list)) and result else None
            if action == "refresh":
                self._update_metrics_view()
                continue
            if action == "copy":
                payload = result[1] if len(result) > 1 else ""
                try:
                    inp = self.query_one(f"#term-input-{self._active_term_id}", TextArea)
                    if hasattr(inp, "load_text"):
                        inp.load_text(str(payload))
                    else:
                        cast(Any, inp).value = str(payload)
                    inp.focus()
                    self._log_to_active_terminal(
                        "[context] copied summary to input", style="#00ff00"
                    )
                except Exception:
                    pass
                continue
            if action == "inject":
                payload = result[1] if len(result) > 1 else ""
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    await panel.dispatch(str(payload))
                except Exception as exc:
                    self._log_to_active_terminal(f"[context] inject failed: {exc}", style="#ff4444")
                continue
            if action == "jump_metrics":
                self._switch_top_tab("tab-metrics")
                continue
            return

    def _telemetry_run_started(self, term_id: int, agent_name: str, prompt: str) -> None:
        stats = self._ensure_term_stats(term_id)
        stats["runs"] += 1
        stats["last_status"] = "running"
        self._telemetry_pending_runs[self._run_key(term_id)] = {
            "start_ms": self._now_ms(),
            "first_token_ms": None,
            "agent_name": agent_name,
            "prompt_chars": len(prompt or ""),
            "categories": {
                "system_prompt_tokens": 0,
                "tool_definitions_tokens": 0,
                "memory_rag_tokens": 0,
                "user_prompt_tokens": self._estimate_tokens_from_text(prompt),
                "assistant_response_tokens": 0,
                "tool_calls_tokens": 0,
                "tool_results_tokens": 0,
            },
        }
        self._emit_telemetry(
            term_id, agent_name, "run_started", {"prompt_chars": len(prompt or "")}
        )

    def _telemetry_first_token(self, term_id: int, agent_name: str) -> None:
        run = self._telemetry_pending_runs.get(self._run_key(term_id))
        if not run:
            return
        if run.get("first_token_ms") is not None:
            return
        now_ms = self._now_ms()
        first_ms = max(0, now_ms - int(run.get("start_ms", now_ms)))
        run["first_token_ms"] = first_ms
        stats = self._ensure_term_stats(term_id)
        stats["sum_first_token_ms"] += first_ms
        stats["last_first_token_ms"] = first_ms
        self._emit_telemetry(term_id, agent_name, "first_token", {"latency_ms": first_ms})

    def _telemetry_tool_called(
        self,
        term_id: int,
        agent_name: str,
        tool_name: str,
        call_id: str,
        args_preview: str,
    ) -> None:
        stats = self._ensure_term_stats(term_id)
        stats["tool_calls"] += 1
        self._telemetry_pending_tool_calls[self._tool_key(term_id, call_id)] = {
            "start_ms": self._now_ms(),
            "tool_name": tool_name,
            "is_retrieval": self._is_retrieval_tool(tool_name),
        }
        payload = {
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "args_preview": args_preview[:200],
        }
        self._emit_telemetry(term_id, agent_name, "tool_called", payload)
        run = self._telemetry_pending_runs.get(self._run_key(term_id))
        if run and isinstance(run.get("categories"), dict):
            run["categories"]["tool_calls_tokens"] += self._estimate_tokens_from_text(args_preview)
        if self._is_retrieval_tool(tool_name):
            stats["retrieval_calls"] += 1
            if run and isinstance(run.get("categories"), dict):
                # Best-effort attribution: retrieval calls often pull memory/RAG context.
                run["categories"]["memory_rag_tokens"] += self._estimate_tokens_from_text(
                    args_preview
                )
            self._emit_telemetry(
                term_id,
                agent_name,
                "retrieval_called",
                {"tool_name": tool_name, "tool_call_id": call_id},
            )

    def _telemetry_tool_output(
        self,
        term_id: int,
        agent_name: str,
        call_id: str,
        output_preview: str,
    ) -> None:
        key = self._tool_key(term_id, call_id)
        pending = self._telemetry_pending_tool_calls.get(key, {})
        start_ms = int(pending.get("start_ms", self._now_ms()))
        duration_ms = max(0, self._now_ms() - start_ms)
        tool_name = str(pending.get("tool_name", "tool"))
        self._emit_telemetry(
            term_id,
            agent_name,
            "tool_output",
            {
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "duration_ms": duration_ms,
                "output_preview": output_preview[:200],
            },
        )
        run = self._telemetry_pending_runs.get(self._run_key(term_id))
        if run and isinstance(run.get("categories"), dict):
            run["categories"]["tool_results_tokens"] += self._estimate_tokens_from_text(
                output_preview
            )
        if pending.get("is_retrieval"):
            if run and isinstance(run.get("categories"), dict):
                run["categories"]["memory_rag_tokens"] += self._estimate_tokens_from_text(
                    output_preview
                )
            self._emit_telemetry(
                term_id,
                agent_name,
                "retrieval_output",
                {
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "duration_ms": duration_ms,
                },
            )
        if key in self._telemetry_pending_tool_calls:
            self._telemetry_pending_tool_calls.pop(key, None)

    def _telemetry_run_finished(
        self,
        term_id: int,
        agent_name: str,
        result,
        status: str,
        error_text: str | None = None,
    ) -> str:
        run = self._telemetry_pending_runs.pop(self._run_key(term_id), None)
        stats = self._ensure_term_stats(term_id)
        now_ms = self._now_ms()
        total_latency_ms = 0
        if run:
            total_latency_ms = max(0, now_ms - int(run.get("start_ms", now_ms)))
        stats["sum_total_latency_ms"] += total_latency_ms
        stats["last_total_latency_ms"] = total_latency_ms
        stats["last_status"] = status

        if status == "error":
            stats["errors"] += 1
        elif status == "cancelled":
            stats["cancelled"] += 1

        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        usage_detail_totals = {"cached_tokens": 0, "reasoning_tokens": 0}
        try:
            if result is not None:
                for resp in list(getattr(result, "raw_responses", []) or []):
                    usage = getattr(resp, "usage", None)
                    if usage is None:
                        continue
                    usage_totals = self._extract_usage_detail_totals(usage)
                    input_tokens += int(usage_totals.get("input_tokens", 0) or 0)
                    output_tokens += int(usage_totals.get("output_tokens", 0) or 0)
                    total_tokens += int(usage_totals.get("total_tokens", 0) or 0)
                    usage_detail_totals["cached_tokens"] += int(
                        usage_totals.get("cached_tokens", 0) or 0
                    )
                    usage_detail_totals["reasoning_tokens"] += int(
                        usage_totals.get("reasoning_tokens", 0) or 0
                    )
        except Exception:
            pass

        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["total_tokens"] += total_tokens

        payload = {
            "status": status,
            "latency_ms_total": total_latency_ms,
            "latency_ms_first_token": run.get("first_token_ms") if run else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "usage_details": usage_detail_totals,
            "categories": (run or {}).get("categories", {}),
        }
        if error_text:
            payload["error"] = str(error_text)[:300]
        self._emit_telemetry(term_id, agent_name, "run_finished", payload)

        interaction_cost = 0.0
        input_price = 0.0
        output_price = 0.0
        model_name = self._model_name
        try:
            from cai.util import COST_TRACKER

            interaction_cost = float(
                COST_TRACKER.calculate_cost(
                    model_name, input_tokens, output_tokens, label="TUI_STATS"
                )
            )
            input_price, output_price = COST_TRACKER.get_model_pricing(model_name)
        except Exception:
            interaction_cost = 0.0
            input_price, output_price = (0.0, 0.0)

        stats["cost_total"] = float(stats.get("cost_total", 0.0) or 0.0) + interaction_cost
        stats["last_cost"] = interaction_cost
        stats["model"] = model_name
        stats["input_price_per_token"] = float(input_price or 0.0)
        stats["output_price_per_token"] = float(output_price or 0.0)

        try:
            snapshot = self._build_context_snapshot(
                term_id=term_id,
                agent_name=agent_name,
                model_name=self._model_name,
                run_data=(run or {}),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                usage_details=usage_detail_totals,
            )
            self._context_snapshot_by_term[term_id] = snapshot
            self._persist_context_snapshot(snapshot)
            self._emit_telemetry(term_id, agent_name, "context_snapshot", snapshot)
        except Exception:
            pass

        try:
            self._refresh_price_limit_state(emit_logs=True)
        except Exception:
            pass

        # Refresh Metrics tab widgets if present.
        try:
            self._update_metrics_view()
        except Exception:
            pass

        avg_total = int(stats["sum_total_latency_ms"] / max(1, stats["runs"]))
        avg_first = int(stats["sum_first_token_ms"] / max(1, stats["runs"]))
        return (
            f"lat(avg {avg_total}ms / first {avg_first}ms) · "
            f"tok {stats['total_tokens']} · tools {stats['tool_calls']} · "
            f"retr {stats['retrieval_calls']} · {status}"
        )
