"""Parallel orchestration command for CAI REPL.

This module provides an async-first concurrent execution engine with:
- batched command execution from inline args or task files
- resource guardrails via max-worker concurrency limiter
- live task status dashboard
- error isolation and per-task summary reporting
- thread-safe memory/cost synchronization with redacted artifacts

Compatibility notes:
- preserves `ParallelConfig`, `PARALLEL_CONFIGS`, and `PARALLEL_AGENT_INSTANCES`
  used by other modules.
- preserves `ParallelCommand._get_message_signature()` used by TUI history merge flow.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from rich import box
from rich.console import Console
from rich.live import Live
from rich.table import Table

from cai.agents import get_available_agents
from cai.memory import MemoryManager
from cai.repl.commands.base import FrameworkCommand, register_command
from cai.repl.commands.cost import USAGE_TRACKER
from cai.tools.workspace import get_project_space


console = Console()


PARALLEL_CONFIGS: List["ParallelConfig"] = []
PARALLEL_AGENT_INSTANCES: Dict[Tuple[str, int], Any] = {}


@dataclass
class ParallelConfig:
    """Parallel agent configuration structure kept for compatibility."""

    agent_name: str
    model: Optional[str] = None
    prompt: Optional[str] = None
    unified_context: bool = False
    id: Optional[str] = None


@dataclass(frozen=True)
class TaskSpec:
    """A unit of concurrent work."""

    task_id: str
    command: str
    source: str


@dataclass
class TaskState:
    """Mutable runtime state for one task."""

    task: TaskSpec
    status: str = "Pending"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    return_code: Optional[int] = None
    output_preview: str = ""
    error: Optional[str] = None


@dataclass
class OrchestrationResult:
    """Summary results for a workflow execution."""

    total: int
    succeeded: int
    failed: int
    states: List[TaskState] = field(default_factory=list)


class WorkflowOrchestrator:
    """Async concurrent execution engine with guardrails and status reporting."""

    _SECRET_PATTERNS: Tuple[re.Pattern[str], ...] = (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*([^\s,;]+)"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{16,}\b"),
    )

    def __init__(self, *, memory: MemoryManager, workspace_root: Path) -> None:
        self._memory = memory
        self._workspace_root = workspace_root.resolve()
        self._memory_lock = asyncio.Lock()
        self._audit_lock = asyncio.Lock()
        self._audit_path = self._workspace_root / ".cai" / "audit" / "parallel_actions.jsonl"

    async def run(self, *, tasks: Sequence[TaskSpec], max_workers: int) -> OrchestrationResult:
        max_workers = max(1, max_workers)
        semaphore = asyncio.Semaphore(max_workers)

        states = [TaskState(task=t) for t in tasks]
        state_by_id = {st.task.task_id: st for st in states}

        dashboard_task = asyncio.create_task(self._dashboard_loop(states))

        async def _runner(spec: TaskSpec) -> None:
            state = state_by_id[spec.task_id]
            async with semaphore:
                await self._execute_one(state)

        try:
            if hasattr(asyncio, "TaskGroup"):
                async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
                    for spec in tasks:
                        tg.create_task(_runner(spec))
            else:
                await asyncio.gather(*[_runner(spec) for spec in tasks], return_exceptions=True)
        finally:
            dashboard_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dashboard_task

        succeeded = sum(1 for st in states if st.status == "Completed")
        failed = sum(1 for st in states if st.status == "Failed")

        return OrchestrationResult(
            total=len(states),
            succeeded=succeeded,
            failed=failed,
            states=states,
        )

    async def _execute_one(self, state: TaskState) -> None:
        task = state.task
        state.status = "Running"
        state.started_at = datetime.now(tz=UTC).isoformat()

        started = datetime.now(tz=UTC)
        try:
            proc = await asyncio.create_subprocess_shell(
                task.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_root),
            )
            out_bytes, err_bytes = await proc.communicate()

            out_text = out_bytes.decode("utf-8", "ignore") if out_bytes else ""
            err_text = err_bytes.decode("utf-8", "ignore") if err_bytes else ""
            merged_text = (out_text + "\n" + err_text).strip()

            state.return_code = int(proc.returncode or 0)
            state.ended_at = datetime.now(tz=UTC).isoformat()
            state.output_preview = self._redact(merged_text)[:500]

            if state.return_code == 0:
                state.status = "Completed"
            else:
                state.status = "Failed"
                state.error = f"exit_code={state.return_code}"

            await self._sync_state(state, started)

        except Exception as exc:  # pylint: disable=broad-except
            state.status = "Failed"
            state.error = str(exc)
            state.ended_at = datetime.now(tz=UTC).isoformat()
            await self._sync_state(state, started)

    async def _sync_state(self, state: TaskState, started: datetime) -> None:
        elapsed_ms = int((datetime.now(tz=UTC) - started).total_seconds() * 1000)

        async with self._memory_lock:
            await asyncio.to_thread(
                self._memory.record,
                {
                    "topic": "parallel.task",
                    "finding": f"Task {state.task.task_id} {state.status.lower()}",
                    "source": "parallel_command",
                    "tags": ["parallel", "orchestration", state.status.lower()],
                    "artifacts": {
                        "task_id": state.task.task_id,
                        "command": self._redact(state.task.command),
                        "source": state.task.source,
                        "status": state.status,
                        "return_code": state.return_code,
                        "elapsed_ms": elapsed_ms,
                        "output_preview": state.output_preview,
                        "error": state.error,
                    },
                },
            )

        # Thread-safe cost ledger synchronization (zero-cost operational record).
        USAGE_TRACKER.record(
            agent_name="parallel-orchestrator",
            model="local",
            input_tokens=0,
            output_tokens=0,
            operation=f"parallel:{state.task.task_id}:{state.status.lower()}",
            cost=Decimal("0"),
        )

        event = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "task_id": state.task.task_id,
            "source": state.task.source,
            "command": self._redact(state.task.command),
            "status": state.status,
            "return_code": state.return_code,
            "error": state.error,
        }
        await self._append_audit(event)

    async def _append_audit(self, payload: Mapping[str, Any]) -> None:
        async with self._audit_lock:
            text = json.dumps(dict(payload), ensure_ascii=True) + "\n"
            await asyncio.to_thread(self._atomic_append_text, self._audit_path, text)

    @staticmethod
    def _atomic_append_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        final = existing + text

        fd, tmp_name = tempfile.mkstemp(prefix=".parallel_audit_", dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(final)
                handle.flush()
            tmp_path.replace(path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    async def _dashboard_loop(self, states: Sequence[TaskState]) -> None:
        with Live(self._build_dashboard(states), refresh_per_second=8, console=console, transient=True) as live:
            while True:
                await asyncio.sleep(0.2)
                live.update(self._build_dashboard(states))

    @staticmethod
    def _build_dashboard(states: Sequence[TaskState]) -> Table:
        table = Table(title="Parallel Task Dashboard", box=box.SIMPLE_HEAVY)
        table.add_column("Task", style="cyan", width=8)
        table.add_column("Status", style="white", width=11)
        table.add_column("Source", style="magenta", width=10)
        table.add_column("Command", style="green")
        table.add_column("Result", style="yellow", width=12)

        for state in states:
            result = "-"
            if state.status == "Completed":
                result = "ok"
            elif state.status == "Failed":
                result = state.error or f"code={state.return_code}"

            cmd_preview = state.task.command if len(state.task.command) <= 72 else state.task.command[:69] + "..."
            table.add_row(state.task.task_id, state.status, state.task.source, cmd_preview, result)

        return table

    def _redact(self, text: str) -> str:
        cleaned = text
        for pattern in self._SECRET_PATTERNS:
            cleaned = pattern.sub("[REDACTED]", cleaned)
        return cleaned


class ParallelCommand(FrameworkCommand):
    """Concurrent execution command for batch task orchestration."""

    name = "/parallel"
    description = "Run commands concurrently with guardrails and live status"
    aliases = ["/par", "/p"]

    def __init__(self) -> None:
        super().__init__()
        self._memory = self._resolve_memory_manager()
        self._workspace_root = get_project_space().ensure_initialized().resolve()
        self._orchestrator = WorkflowOrchestrator(memory=self._memory, workspace_root=self._workspace_root)

        self._subcommands = {
            "add": "Add an agent config to PARALLEL_CONFIGS",
            "list": "List configured parallel agent entries",
            "clear": "Clear configured parallel agent entries",
            "remove": "Remove configured parallel agent by index or ID",
            "help": "Show command usage",
        }

    @property
    def help(self) -> str:
        return (
            "Usage:\n"
            "  /parallel \"scan 192.168.1.1\" \"scan 192.168.1.2\" [--max-workers N]\n"
            "  /parallel --file tasks.txt [--max-workers N]\n"
            "\n"
            "Config compatibility:\n"
            "  /parallel add <agent_name> [--model MODEL] [--prompt PROMPT] [--unified]\n"
            "  /parallel list\n"
            "  /parallel clear\n"
            "  /parallel remove <index|ID>\n"
        )

    async def execute(self, args: List[str]) -> bool:
        if not args:
            console.print(self.help)
            return True

        first = args[0].lower().strip()
        if first in {"help", "--help", "-h"}:
            console.print(self.help)
            return True

        if first in {"add", "list", "clear", "remove"}:
            return await self._execute_config_subcommand(first, args[1:])

        parsed = self._parse_execution_args(args)
        if parsed is None:
            return False

        tasks, max_workers = parsed
        if not tasks:
            console.print("[yellow]No tasks to execute[/yellow]")
            return True

        result = await self._orchestrator.run(tasks=tasks, max_workers=max_workers)
        self._render_summary(result, max_workers=max_workers)
        return result.failed == 0

    async def _execute_config_subcommand(self, sub: str, args: List[str]) -> bool:
        if sub == "add":
            return self._cfg_add(args)
        if sub == "list":
            return self._cfg_list()
        if sub == "clear":
            return self._cfg_clear()
        if sub == "remove":
            return self._cfg_remove(args)
        return False

    def _cfg_add(self, args: List[str]) -> bool:
        if not args:
            console.print("[red]Usage: /parallel add <agent_name> [--model MODEL] [--prompt PROMPT] [--unified][/red]")
            return False

        agent_name = args[0]
        if agent_name not in get_available_agents():
            console.print(f"[red]Unknown agent: {agent_name}[/red]")
            return False

        model: Optional[str] = None
        prompt: Optional[str] = None
        unified = False

        i = 1
        while i < len(args):
            token = args[i]
            if token == "--model" and i + 1 < len(args):
                model = args[i + 1]
                i += 2
                continue
            if token == "--prompt" and i + 1 < len(args):
                prompt = " ".join(args[i + 1 :])
                i = len(args)
                continue
            if token == "--unified":
                unified = True
                i += 1
                continue
            i += 1

        config = ParallelConfig(agent_name=agent_name, model=model, prompt=prompt, unified_context=unified)
        config.id = f"P{len(PARALLEL_CONFIGS) + 1}"
        PARALLEL_CONFIGS.append(config)
        self._sync_env()
        console.print(f"[green]Added parallel agent config {config.id}: {agent_name}[/green]")
        return True

    def _cfg_list(self) -> bool:
        if not PARALLEL_CONFIGS:
            console.print("[yellow]No parallel agent configs defined[/yellow]")
            return True

        table = Table(title="Parallel Agent Configs", box=box.SIMPLE)
        table.add_column("#", style="dim")
        table.add_column("ID", style="magenta")
        table.add_column("Agent", style="cyan")
        table.add_column("Model", style="green")
        table.add_column("Unified", style="yellow")
        table.add_column("Prompt", style="white")

        for idx, cfg in enumerate(PARALLEL_CONFIGS, start=1):
            table.add_row(
                str(idx),
                cfg.id or f"P{idx}",
                cfg.agent_name,
                cfg.model or "default",
                "yes" if cfg.unified_context else "no",
                (cfg.prompt[:60] + "...") if cfg.prompt and len(cfg.prompt) > 60 else (cfg.prompt or ""),
            )

        console.print(table)
        return True

    def _cfg_clear(self) -> bool:
        count = len(PARALLEL_CONFIGS)
        PARALLEL_CONFIGS.clear()
        PARALLEL_AGENT_INSTANCES.clear()
        self._sync_env()
        console.print(f"[green]Cleared {count} parallel agent configs[/green]")
        return True

    def _cfg_remove(self, args: List[str]) -> bool:
        if not args:
            console.print("[red]Usage: /parallel remove <index|ID>[/red]")
            return False

        target = args[0].strip()
        idx_to_remove: Optional[int] = None

        if target.upper().startswith("P") and target[1:].isdigit():
            for idx, cfg in enumerate(PARALLEL_CONFIGS):
                cfg_id = (cfg.id or f"P{idx + 1}").upper()
                if cfg_id == target.upper():
                    idx_to_remove = idx
                    break
        elif target.isdigit():
            one_based = int(target)
            if 1 <= one_based <= len(PARALLEL_CONFIGS):
                idx_to_remove = one_based - 1

        if idx_to_remove is None:
            console.print(f"[red]Could not find config '{target}'[/red]")
            return False

        removed = PARALLEL_CONFIGS.pop(idx_to_remove)
        for pos, cfg in enumerate(PARALLEL_CONFIGS, start=1):
            cfg.id = f"P{pos}"

        self._sync_env()
        console.print(f"[green]Removed parallel agent config {removed.id or '?'} ({removed.agent_name})[/green]")
        return True

    def _sync_env(self) -> None:
        if len(PARALLEL_CONFIGS) >= 2:
            os.environ["CAI_PARALLEL"] = str(len(PARALLEL_CONFIGS))
            os.environ["CAI_PARALLEL_AGENTS"] = ",".join(c.agent_name for c in PARALLEL_CONFIGS)
        else:
            os.environ["CAI_PARALLEL"] = "1"
            os.environ["CAI_PARALLEL_AGENTS"] = ",".join(c.agent_name for c in PARALLEL_CONFIGS)

    def _parse_execution_args(self, args: List[str]) -> Optional[Tuple[List[TaskSpec], int]]:
        max_workers = self._default_max_workers()
        file_path: Optional[str] = None
        inline_commands: List[str] = []

        i = 0
        while i < len(args):
            token = args[i]
            if token == "--max-workers":
                if i + 1 >= len(args):
                    console.print("[red]--max-workers requires an integer[/red]")
                    return None
                try:
                    max_workers = max(1, int(args[i + 1]))
                except ValueError:
                    console.print(f"[red]Invalid --max-workers value: {args[i + 1]}[/red]")
                    return None
                i += 2
                continue

            if token == "--file":
                if i + 1 >= len(args):
                    console.print("[red]--file requires a path[/red]")
                    return None
                file_path = args[i + 1]
                i += 2
                continue

            if token.startswith("--"):
                console.print(f"[red]Unknown option: {token}[/red]")
                return None

            inline_commands.append(token)
            i += 1

        commands: List[Tuple[str, str]] = []
        if file_path:
            file_commands = self._load_tasks_from_file(file_path)
            if file_commands is None:
                return None
            commands.extend((cmd, f"file:{file_path}") for cmd in file_commands)

        commands.extend((cmd, "inline") for cmd in inline_commands)

        tasks = [TaskSpec(task_id=f"T{idx}", command=cmd, source=src) for idx, (cmd, src) in enumerate(commands, start=1)]
        return tasks, max_workers

    def _load_tasks_from_file(self, candidate: str) -> Optional[List[str]]:
        raw = Path(candidate).expanduser()
        if raw.is_absolute():
            resolved = raw.resolve()
        else:
            cwd_candidate = (Path.cwd() / raw).resolve()
            ws_candidate = (self._workspace_root / raw).resolve()
            resolved = cwd_candidate if cwd_candidate.exists() else ws_candidate

        allowed_roots = [self._workspace_root.resolve(), Path.cwd().resolve()]
        if not any(self._is_within_root(resolved, root) for root in allowed_roots):
            console.print(f"[red]Security: task file escapes allowed roots: {resolved}[/red]")
            return None

        if not resolved.exists() or not resolved.is_file():
            console.print(f"[red]Task file not found: {resolved}[/red]")
            return None

        lines = resolved.read_text(encoding="utf-8").splitlines()
        tasks = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
        return tasks

    @staticmethod
    def _is_within_root(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except Exception:
            return False

    @staticmethod
    def _default_max_workers() -> int:
        cpu = os.cpu_count() or 2
        return max(2, min(16, cpu))

    @staticmethod
    def _render_summary(result: OrchestrationResult, *, max_workers: int) -> None:
        table = Table(title="Parallel Execution Summary", box=box.SIMPLE_HEAVY)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Total Tasks", str(result.total))
        table.add_row("Max Workers", str(max_workers))
        table.add_row("Succeeded", str(result.succeeded))
        table.add_row("Failed", str(result.failed))
        console.print(table)

        details = Table(title="Task Outcomes", box=box.SIMPLE)
        details.add_column("Task", style="cyan", width=8)
        details.add_column("Status", style="white", width=11)
        details.add_column("Code", style="yellow", width=6)
        details.add_column("Error", style="red")

        for state in result.states:
            details.add_row(
                state.task.task_id,
                state.status,
                "-" if state.return_code is None else str(state.return_code),
                state.error or "",
            )
        console.print(details)

    def _resolve_memory_manager(self) -> MemoryManager:
        if isinstance(self.memory, MemoryManager):
            return self.memory
        return MemoryManager()

    def _get_message_signature(self, msg: Dict[str, Any]) -> Optional[str]:
        """Compatibility helper used by TUI to deduplicate merged messages."""
        role = msg.get("role")
        if not role:
            return None

        if role in {"user", "system"}:
            content = msg.get("content", "")
            normalized = " ".join(str(content).split()) if content else ""
            return f"{role}:{normalized}"

        if role == "assistant":
            content = msg.get("content", "") or ""
            normalized = " ".join(str(content).split()) if content else ""
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tc_parts = []
                for tc in tool_calls:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    tc_parts.append(f"{fn.get('name', '')}:{fn.get('arguments', '')}")
                return f"assistant:{normalized}:tools:[{';'.join(sorted(tc_parts))}]"
            return f"assistant:{normalized}"

        if role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            content = msg.get("content", "")
            normalized = " ".join(str(content).split()) if content else ""
            return f"tool:{tool_call_id}:{normalized[:200]}"

        return None

PARALLEL_COMMAND_INSTANCE = ParallelCommand()
register_command(PARALLEL_COMMAND_INSTANCE)
