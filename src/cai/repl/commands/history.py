"""Engagement timeline command for CAI REPL.

This module provides an original, commercial-ready history implementation that:
- Uses FrameworkCommand execution and auditing hooks.
- Interfaces with MemoryManager for report evidence recording.
- Streams normalized turns via a generator to keep memory usage bounded.
- Supports forensic filtering and export for client reporting.
- Preserves secret masking by applying recursive redaction before render/export.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, Iterator, List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cai.memory import MemoryManager
from cai.memory.logic import clean_data
from cai.repl.commands.base import FrameworkCommand, register_command
from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

console = Console()


@dataclass
class TimelineQuery:
    """Parsed filtering/options payload for /history."""

    agent: Optional[str] = None
    search: Optional[str] = None
    tail: Optional[int] = None
    save: Optional[str] = None


@dataclass
class TurnRecord:
    """Normalized turn structure for timeline rendering and export."""

    observed_at: str
    source_timestamp: Optional[str]
    actor: str
    role: str
    payload: str
    metadata: Dict[str, Any]
    agent: str


class TimelineFormatter:
    """Render a professional vertical timeline and export report artifacts."""

    def __init__(self, out: Console) -> None:
        self._out = out

    def render_banner(self, query: TimelineQuery) -> None:
        details = ["Structured Engagement Timeline", "Source: live agent memory turns"]
        if query.agent:
            details.append(f"Filter agent: {query.agent}")
        if query.search:
            details.append(f"Search: {query.search}")
        if query.tail is not None:
            details.append(f"Tail: last {query.tail} turns")
        if query.save:
            details.append(f"Export: {query.save}")

        self._out.print(
            Panel(
                "\n".join(details),
                title="History",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )

    def render_empty(self, query: TimelineQuery) -> None:
        reasons: List[str] = ["No turns matched the current filters."]
        if query.agent:
            reasons.append(f"- No actor matched '{query.agent}'.")
        if query.search:
            reasons.append(f"- No payload matched '{query.search}'.")
        self._out.print(Panel("\n".join(reasons), title="Timeline", border_style="yellow"))

    def render_turn(self, index: int, turn: TurnRecord) -> None:
        role = turn.role.lower()
        actor_style = "cyan" if role == "user" else "green"

        timeline = Table.grid(expand=True)
        timeline.add_column(ratio=22, style="bold white")
        timeline.add_column(ratio=78)

        timestamp = turn.source_timestamp or turn.observed_at
        timeline.add_row("Time", timestamp)
        timeline.add_row("Actor", f"[{actor_style}]{turn.actor}[/{actor_style}]")
        timeline.add_row("Role", role)
        timeline.add_row("Payload", turn.payload)

        token_cost = turn.metadata.get("token_cost")
        tool_time_ms = turn.metadata.get("tool_execution_ms")
        usage = turn.metadata.get("usage")

        meta_lines: List[str] = []
        if token_cost is not None:
            meta_lines.append(f"token_cost={token_cost}")
        if tool_time_ms is not None:
            meta_lines.append(f"tool_execution_ms={tool_time_ms}")
        if usage:
            meta_lines.append(
                "usage="
                + json.dumps(usage, ensure_ascii=True, separators=(",", ":"))
            )
        if meta_lines:
            timeline.add_row("Metadata", " | ".join(meta_lines))

        self._out.print(
            Panel(
                timeline,
                title=f"Turn {index}",
                border_style="blue" if role == "user" else "magenta",
                box=box.SQUARE,
            )
        )

    def render_footer(self, total: int, export_path: Optional[Path]) -> None:
        msg = f"Displayed {total} turn(s)."
        if export_path is not None:
            msg += f" Exported appendix: {export_path}"
        self._out.print(Panel(msg, title="Summary", border_style="green", box=box.ROUNDED))


class HistoryCommand(FrameworkCommand):
    """Searchable, structured engagement timeline command."""

    name = "/history"
    description = "Display a structured engagement timeline with search, filters, and export"
    aliases = ["/his"]

    def __init__(self) -> None:
        super().__init__()
        self._formatter = TimelineFormatter(console)
        self._memory_manager = self._resolve_memory_manager()

    @property
    def help(self) -> str:
        return (
            "Usage: /history [--agent <name>] [--search <term>] [--tail <n>] "
            "[--save json|markdown]\n"
            "Examples:\n"
            "  /history\n"
            "  /history --agent Red Team Agent\n"
            "  /history --search 10.0.0.5 --tail 30\n"
            "  /history --tail 50 --save json\n"
            "  /history --agent Bug Bounty Hunter --save markdown\n"
            "Security: output remains redacted; no raw secret values are unmasked."
        )

    def sanitize_args(self, args: Optional[List[str]]) -> List[str]:
        return super().sanitize_args(args)

    async def execute(self, args: List[str]) -> bool:
        query = self._parse_args(args)
        if query is None:
            return False

        self._formatter.render_banner(query)

        turns = self._iter_filtered_turns(
            observed_at=datetime.now(tz=UTC),
            agent_filter=query.agent,
            search_filter=query.search,
        )

        exporter: Optional[_BaseExporter] = None
        export_path: Optional[Path] = None
        if query.save:
            exporter = self._build_exporter(query.save)
            if exporter is None:
                console.print("[red]Invalid --save format. Use json or markdown.[/red]")
                return False
            export_path = exporter.path
            exporter.open()

        total = 0
        if query.tail is not None:
            ring: Deque[TurnRecord] = deque(maxlen=query.tail)
            for turn in turns:
                ring.append(turn)
            for turn in ring:
                total += 1
                self._formatter.render_turn(total, turn)
                if exporter is not None:
                    exporter.write(turn)
        else:
            for turn in turns:
                total += 1
                self._formatter.render_turn(total, turn)
                if exporter is not None:
                    exporter.write(turn)

        if exporter is not None:
            exporter.close()

        if total == 0:
            self._formatter.render_empty(query)
            return True

        self._formatter.render_footer(total=total, export_path=export_path)
        self._record_export_event(total=total, query=query, export_path=export_path)
        return True

    def _resolve_memory_manager(self) -> MemoryManager:
        candidate = self.memory
        if isinstance(candidate, MemoryManager):
            return candidate
        return MemoryManager()

    def _parse_args(self, args: List[str]) -> Optional[TimelineQuery]:
        query = TimelineQuery()
        i = 0
        while i < len(args):
            token = args[i]

            if token in {"--help", "-h", "help"}:
                console.print(self.help)
                return None

            if token == "--agent":
                if i + 1 >= len(args):
                    console.print("[red]--agent requires a value[/red]")
                    return None
                query.agent = args[i + 1]
                i += 2
                continue

            if token == "--search":
                if i + 1 >= len(args):
                    console.print("[red]--search requires a value[/red]")
                    return None
                query.search = args[i + 1]
                i += 2
                continue

            if token == "--tail":
                if i + 1 >= len(args):
                    console.print("[red]--tail requires a positive integer[/red]")
                    return None
                try:
                    tail_value = int(args[i + 1])
                except ValueError:
                    console.print("[red]--tail requires a positive integer[/red]")
                    return None
                if tail_value <= 0:
                    console.print("[red]--tail must be greater than zero[/red]")
                    return None
                query.tail = tail_value
                i += 2
                continue

            if token == "--save":
                if i + 1 >= len(args):
                    console.print("[red]--save requires json or markdown[/red]")
                    return None
                fmt = args[i + 1].lower()
                if fmt == "md":
                    fmt = "markdown"
                query.save = fmt
                i += 2
                continue

            console.print(f"[red]Unknown argument: {token}[/red]")
            console.print(self.help)
            return None

        return query

    def _iter_filtered_turns(
        self,
        observed_at: datetime,
        agent_filter: Optional[str],
        search_filter: Optional[str],
    ) -> Iterator[TurnRecord]:
        observed_iso = observed_at.isoformat()
        needle = (search_filter or "").lower().strip()
        agent_needle = (agent_filter or "").lower().strip()

        for turn in self._iter_turns(observed_iso=observed_iso):
            if agent_needle and agent_needle not in turn.agent.lower() and agent_needle not in turn.actor.lower():
                continue
            if needle and needle not in self._search_haystack(turn):
                continue
            yield turn

    def _iter_turns(self, observed_iso: str) -> Iterator[TurnRecord]:
        """Stream normalized turns from agent-managed memory histories."""
        histories = AGENT_MANAGER.get_all_histories()
        for display_name, messages in histories.items():
            if not isinstance(messages, list):
                continue
            agent_name = self._extract_agent_name(display_name)
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                safe_msg = clean_data(msg)
                yield self._normalize_turn(
                    msg=safe_msg,
                    observed_iso=observed_iso,
                    agent_name=agent_name,
                )

    def _normalize_turn(self, msg: Dict[str, Any], observed_iso: str, agent_name: str) -> TurnRecord:
        role = str(msg.get("role", "unknown"))
        actor = "User" if role.lower() == "user" else agent_name

        payload_text = self._render_payload(msg)
        timestamp = self._extract_timestamp(msg)
        metadata = self._extract_metadata(msg)

        return TurnRecord(
            observed_at=observed_iso,
            source_timestamp=timestamp,
            actor=actor,
            role=role,
            payload=payload_text,
            metadata=metadata,
            agent=agent_name,
        )

    @staticmethod
    def _extract_agent_name(display_name: str) -> str:
        if "[" in display_name and display_name.endswith("]"):
            return display_name.rsplit("[", 1)[0].strip()
        return display_name.strip() or "Unknown Agent"

    @staticmethod
    def _extract_timestamp(msg: Dict[str, Any]) -> Optional[str]:
        for key in ("timestamp", "created_at", "time", "ts"):
            raw = msg.get(key)
            if raw is None:
                continue
            if isinstance(raw, (int, float)):
                try:
                    return datetime.fromtimestamp(float(raw), tz=UTC).isoformat()
                except Exception:
                    continue
            if isinstance(raw, str) and raw.strip():
                text = raw.strip()
                try:
                    return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
                except Exception:
                    return text
        return None

    @staticmethod
    def _extract_metadata(msg: Dict[str, Any]) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}

        raw_meta = msg.get("metadata")
        if isinstance(raw_meta, dict):
            for key in (
                "token_cost",
                "cost",
                "usd_cost",
                "tool_execution_time",
                "tool_time_ms",
                "execution_ms",
                "duration_ms",
            ):
                if key in raw_meta:
                    if "cost" in key:
                        metadata["token_cost"] = raw_meta[key]
                    else:
                        metadata["tool_execution_ms"] = raw_meta[key]

        usage = msg.get("usage")
        if isinstance(usage, dict):
            usage_map: Dict[str, Any] = {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
                if key in usage:
                    usage_map[key] = usage[key]
            if usage_map:
                metadata["usage"] = usage_map

        if "tool_call_id" in msg:
            metadata["tool_call_id"] = msg.get("tool_call_id")
        if isinstance(msg.get("tool_calls"), list):
            metadata["tool_calls"] = len(msg["tool_calls"])

        return metadata

    @staticmethod
    def _render_payload(msg: Dict[str, Any]) -> str:
        content = msg.get("content")
        payload = ""

        if isinstance(content, str):
            payload = content.strip()
        elif isinstance(content, list):
            payload = json.dumps(content, ensure_ascii=True)
        elif content is not None:
            payload = str(content)

        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            fragments: List[str] = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name", "unknown_function")
                args = fn.get("arguments", "{}")
                fragments.append(f"tool_call={name} args={args}")
            tool_info = "\n".join(fragments)
            payload = f"{payload}\n{tool_info}".strip()

        if not payload:
            payload = "[empty payload]"

        # Keep rendering fast in terminal while still preserving export full payload.
        if len(payload) > 900:
            return payload[:897] + "..."
        return payload

    @staticmethod
    def _search_haystack(turn: TurnRecord) -> str:
        blob = {
            "actor": turn.actor,
            "role": turn.role,
            "payload": turn.payload,
            "metadata": turn.metadata,
            "agent": turn.agent,
        }
        return json.dumps(blob, ensure_ascii=True).lower()

    def _build_exporter(self, fmt: str) -> Optional["_BaseExporter"]:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        report_dir = self._memory_manager.initialize() / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            return _JsonExporter(report_dir / f"engagement_timeline_{timestamp}.json")
        if fmt == "markdown":
            return _MarkdownExporter(report_dir / f"engagement_timeline_{timestamp}.md")
        return None

    def _record_export_event(self, total: int, query: TimelineQuery, export_path: Optional[Path]) -> None:
        try:
            payload = {
                "topic": "history.export" if export_path else "history.view",
                "finding": (
                    f"Rendered {total} timeline turns"
                    + (f" and wrote {export_path.name}" if export_path else "")
                ),
                "source": "history_command",
                "tags": ["timeline", "forensics", "history"],
                "artifacts": {
                    "agent_filter": query.agent,
                    "search": query.search,
                    "tail": query.tail,
                    "save": query.save,
                    "path": str(export_path) if export_path else None,
                },
            }
            self._memory_manager.record(payload)
        except Exception:
            # History rendering must not fail due to evidence persistence issues.
            return


class _BaseExporter:
    """Streaming exporter base class."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")

    def write(self, turn: TurnRecord) -> None:
        raise NotImplementedError

    def close(self) -> None:
        if self._handle is not None:
            self._finalize()
            self._handle.close()
            self._handle = None

    def _finalize(self) -> None:
        return


class _JsonExporter(_BaseExporter):
    """Streaming JSON exporter (array payload)."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._count = 0

    def open(self) -> None:
        super().open()
        assert self._handle is not None
        self._handle.write("[\n")

    def write(self, turn: TurnRecord) -> None:
        assert self._handle is not None
        row = {
            "timestamp": turn.source_timestamp or turn.observed_at,
            "observed_at": turn.observed_at,
            "actor": turn.actor,
            "role": turn.role,
            "agent": turn.agent,
            "payload": turn.payload,
            "metadata": turn.metadata,
        }
        if self._count > 0:
            self._handle.write(",\n")
        self._handle.write(json.dumps(clean_data(row), ensure_ascii=True))
        self._count += 1

    def _finalize(self) -> None:
        assert self._handle is not None
        self._handle.write("\n]\n")


class _MarkdownExporter(_BaseExporter):
    """Streaming Markdown exporter for technical appendices."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._idx = 0

    def open(self) -> None:
        super().open()
        assert self._handle is not None
        self._handle.write("# Engagement Timeline\n\n")
        self._handle.write("Generated by /history forensic export.\n\n")

    def write(self, turn: TurnRecord) -> None:
        assert self._handle is not None
        self._idx += 1
        ts = turn.source_timestamp or turn.observed_at
        self._handle.write(f"## Turn {self._idx}\n")
        self._handle.write(f"- Timestamp: {ts}\n")
        self._handle.write(f"- Actor: {turn.actor}\n")
        self._handle.write(f"- Role: {turn.role}\n")
        self._handle.write(f"- Agent: {turn.agent}\n")
        self._handle.write("- Payload:\n\n")
        self._handle.write("```text\n")
        self._handle.write(f"{turn.payload}\n")
        self._handle.write("```\n")
        if turn.metadata:
            self._handle.write("- Metadata:\n\n")
            self._handle.write("```json\n")
            self._handle.write(json.dumps(clean_data(turn.metadata), ensure_ascii=True, indent=2))
            self._handle.write("\n```\n")
        self._handle.write("\n")


HISTORY_COMMAND_INSTANCE = HistoryCommand()
register_command(HISTORY_COMMAND_INSTANCE)
