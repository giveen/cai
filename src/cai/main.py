"""Cerebro-AI async mission-control entrypoint.

This module provides the framework's pre-flight and mission-control layer:

1. Hardware detection and CUDA environment bootstrap
2. Memory subsystem warm-up (CCMB, CHPE, CADE, vector embeddings)
3. Logistics bootstrap for COST and PathGuard-scoped workspace paths
4. Mission scope hydration into the Cerebro Logic Engine
5. Async agent hand-off loop with transfer dispatch integration
6. Signal-driven graceful shutdown with final snapshotting
"""

from __future__ import annotations

import argparse
import asyncio
from asyncio import Queue
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import shlex
import shutil
import subprocess
import traceback
import types
from typing import Any, Optional

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from cai.memory import (
    CerebroSearchEngine,
    CerebroStorageHandler,
)
from cai.memory.memory import CerebroMemoryBus
from cai.rag.embeddings import EmbeddingsProvider, get_embeddings_provider
from cai.rag.metrics import HardwareSaturationMonitor
from cai.repl.ui.logging import CerebroLogger, get_cerebro_logger
from cai.tools.reconnaissance.filesystem import PathGuard


console = Console()

_MIN_RAM_GB = 240.0
_GPU_NAME_HINT = "5090"
_DASHBOARD_REFRESH_SECONDS = 1.0
_TRANSFER_PREFIX = "orchestrator.transfer.request."


class PreFlightError(RuntimeError):
    """Fatal bootstrap error with explicit diagnostics."""


@dataclass
class HardwareProfile:
    ram_total_gb: float
    ram_available_gb: float
    gpu_name: str
    gpu_vram_mb: float
    cuda_visible_devices: str
    cuda_ready: bool


@dataclass
class MemorySuite:
    bus: CerebroMemoryBus
    storage: CerebroStorageHandler
    search: CerebroSearchEngine
    vector_provider: EmbeddingsProvider


@dataclass
class LogisticsSuite:
    transfer_engine: Any
    transfer_protocol_enum: Any
    path_guard: PathGuard
    staged_dir: Path
    loot_dir: Path
    logs_dir: Path


@dataclass
class MissionScope:
    source: str
    payload: dict[str, Any]


@dataclass
class MissionEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineStatus:
    hardware: str = "pending"
    memory: str = "pending"
    logistics: str = "pending"
    cmcd: str = "pending"
    mission: str = "pending"
    transfers_dispatched: int = 0
    last_error: Optional[str] = None


class SessionLogWriter:
    """PathGuard-scoped session summary writer."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self._guard = PathGuard(self.workspace_root, self._audit)

    def write(self, relative_path: str, lines: list[str]) -> Path:
        resolved = self._guard.validate_path(
            relative_path,
            action="session_log",
            mode="write",
        )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return resolved

    @staticmethod
    def _audit(_event: str, _payload: Any) -> None:
        return


class CerebroMissionControl:
    """Async pre-flight and mission-control orchestrator."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        *,
        init_only: bool = False,
        open_mode: bool = False,
        dry_run: bool = False,
    ) -> None:
        default_workspace = Path(os.getenv("CIR_WORKSPACE", "/workspace"))
        self.workspace_root = (workspace_root or default_workspace).resolve()
        self.init_only = bool(init_only)
        self.open_mode = bool(open_mode)
        self.dry_run = bool(dry_run)
        self.logger: CerebroLogger = get_cerebro_logger()
        self.status = EngineStatus()
        self.stop_event = asyncio.Event()
        self.event_queue: Queue[MissionEvent] = Queue()
        self.hardware_monitor = HardwareSaturationMonitor()
        self.hardware: Optional[HardwareProfile] = None
        self.memory_suite: Optional[MemorySuite] = None
        self.logistics_suite: Optional[LogisticsSuite] = None
        self.scope: Optional[MissionScope] = None
        self.cmcd_path = Path(__file__).resolve().parent / "prompts" / "core" / "system_master_template.md"
        self.cmcd_sha256: Optional[str] = None
        self.session_writer = SessionLogWriter(self.workspace_root)
        self._dashboard_task: Optional[asyncio.Task[Any]] = None
        self._transfer_watch_task: Optional[asyncio.Task[Any]] = None
        self._background_tasks: list[asyncio.Task[Any]] = []

    async def run(self) -> int:
        """Run pre-flight, then enter the mission loop until shutdown."""
        try:
            await self._preflight()
            await self._mission_loop()
            return 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status.last_error = str(exc)
            self.logger.finding(
                "Mission control halted during initialization or runtime",
                actor="main",
                data={"error": str(exc), "traceback": traceback.format_exc()},
                tags=["main", "fatal"],
            )
            raise
        finally:
            await self._shutdown()

    async def _preflight(self) -> None:
        self._ensure_workspace_tree()
        self.hardware = await self._initialize_hardware()
        self.status.hardware = "ready"
        self.memory_suite = await self._initialize_memory()
        self.status.memory = "ready"
        self.logistics_suite = await self._initialize_logistics()
        self.status.logistics = "ready"
        self.scope = await self._load_scope()
        await self._hydrate_ground_truths(self.scope)
        await self._initialize_cmcd()
        if self.dry_run:
            await self._run_dry_run_validation()
        self.status.cmcd = "ready"
        self.status.mission = "initialized" if self.init_only else "active"
        await self.event_queue.put(MissionEvent(kind="mission_bootstrap_complete"))
        if self.init_only:
            self.logger.audit(
                "Init-only pre-flight completed",
                actor="main",
                data={
                    "workspace": str(self.workspace_root),
                    "scope_source": self.scope.source,
                    "cmcd_path": str(self.cmcd_path),
                },
                tags=["main", "bootstrap", "init-only"],
            )
            self.stop_event.set()
            return
        self._dashboard_task = asyncio.create_task(self._dashboard_loop(), name="main-dashboard")
        self._transfer_watch_task = asyncio.create_task(self._transfer_request_watcher(), name="transfer-watch")
        self._background_tasks = [task for task in [self._dashboard_task, self._transfer_watch_task] if task is not None]
        self.logger.audit(
            "Pre-flight completed",
            actor="main",
            data={
                "workspace": str(self.workspace_root),
                "scope_source": self.scope.source,
                "cmcd_path": str(self.cmcd_path),
            },
            tags=["main", "bootstrap"],
        )

    async def _initialize_hardware(self) -> HardwareProfile:
        self._set_cuda_environment()
        strict = os.getenv("CAI_PREFLIGHT_STRICT", "true").lower() != "false"
        if self.open_mode:
            strict = False

        ram_total_gb, ram_available_gb = self._read_ram_profile()
        gpu_name = "unknown"
        gpu_vram_mb = 0.0
        cuda_ready = False
        gpu_error = ""
        try:
            gpu_name, gpu_vram_mb = self._read_gpu_profile()
            cuda_ready = True
        except Exception as exc:
            gpu_error = str(exc)

        if strict and ram_total_gb < _MIN_RAM_GB:
            raise PreFlightError(
                f"RAM check failed: detected {ram_total_gb:.2f} GB; expected at least {_MIN_RAM_GB:.0f} GB"
            )
        if strict and (_GPU_NAME_HINT not in gpu_name and gpu_vram_mb < 30_000):
            raise PreFlightError(
                f"GPU check failed: expected RTX 5090-class GPU, detected '{gpu_name}' ({gpu_vram_mb:.0f} MiB VRAM)"
            )

        if not strict:
            warnings: list[str] = []
            if ram_total_gb < _MIN_RAM_GB:
                warnings.append(
                    f"degraded_ram:{ram_total_gb:.2f}GB<{_MIN_RAM_GB:.0f}GB"
                )
            if _GPU_NAME_HINT not in gpu_name and gpu_vram_mb < 30_000:
                warnings.append(
                    f"degraded_gpu:{gpu_name}:{gpu_vram_mb:.0f}MiB"
                )
            if gpu_error:
                warnings.append(f"gpu_probe_error:{gpu_error}")
            if warnings:
                self.logger.finding(
                    "Hardware pre-flight running in open/degraded mode",
                    actor="main",
                    data={"warnings": warnings},
                    tags=["hardware", "preflight", "degraded"],
                )

        profile = HardwareProfile(
            ram_total_gb=ram_total_gb,
            ram_available_gb=ram_available_gb,
            gpu_name=gpu_name,
            gpu_vram_mb=gpu_vram_mb,
            cuda_visible_devices=os.environ.get("CUDA_VISIBLE_DEVICES", "0"),
            cuda_ready=cuda_ready,
        )
        self.logger.action(
            "Hardware pre-flight complete",
            actor="main",
            data={
                "gpu": profile.gpu_name,
                "gpu_vram_mb": profile.gpu_vram_mb,
                "ram_total_gb": profile.ram_total_gb,
                "ram_available_gb": profile.ram_available_gb,
                "cuda_visible_devices": profile.cuda_visible_devices,
            },
            tags=["hardware", "preflight"],
        )
        return profile

    async def _initialize_memory(self) -> MemorySuite:
        bus = await asyncio.to_thread(
            CerebroMemoryBus.get_instance,
            workspace_root=str(self.workspace_root),
        )
        storage = await asyncio.to_thread(
            CerebroStorageHandler.get_instance,
            workspace_root=str(self.workspace_root),
        )
        search = await asyncio.to_thread(
            CerebroSearchEngine.get_instance,
            workspace_root=str(self.workspace_root),
            storage_backend=storage,
            logic_engine=bus.logic,
        )
        await asyncio.to_thread(storage.start_background_flusher, 1.0)
        await asyncio.to_thread(bus.start_background_saver, 15.0)
        await asyncio.to_thread(search.start_indexer, 15.0)

        vector_provider = await asyncio.to_thread(
            get_embeddings_provider,
            "cuda",
            {
                "model_name": os.getenv("CAI_CUDA_MODEL", "BAAI/bge-m3"),
                "batch_size": 256,
                "normalize": True,
            },
        )
        await asyncio.to_thread(vector_provider.embed_texts, ["cerebro preflight warmup"])

        bus.set_logic(
            "mission.memory.status",
            "hot",
            importance=5,
            meta={"tiers": ["logic", "episodic", "semantic", "vector"]},
            agent_id="main",
        )
        self.logger.action(
            "Memory engines initialized",
            actor="main",
            data={
                "logic_nodes": len(bus.logic.search("")),
                "storage_workspace": str(storage.workspace_root),
                "search_workspace": str(search.workspace_root),
                "vector_provider": vector_provider.__class__.__name__,
            },
            tags=["memory", "bootstrap"],
        )
        return MemorySuite(bus=bus, storage=storage, search=search, vector_provider=vector_provider)

    async def _run_dry_run_validation(self) -> None:
        """Validate resource and tool readiness before simulated execution."""
        from cai.tools.validation import validate_resource_health

        tool_name = os.getenv("CAI_DRY_RUN_TOOL", "python3")
        health = await validate_resource_health(
            min_disk_free_mb=512,
            min_memory_free_mb=1024,
            max_cpu_load_1m=24.0,
        )
        binary_path = shutil.which(tool_name)
        status = {
            "tool": tool_name,
            "binary_path": binary_path,
            "validator_ok": bool(health.get("ok", False)),
            "simulated_execution": bool(binary_path),
        }
        self.memory_suite.bus.set_logic(
            "mission.dry_run.status",
            status,
            importance=4,
            agent_id="main",
        )
        self.logger.audit(
            "Dry-run validation completed",
            actor="main",
            data={"validator": health, "dry_run": status},
            tags=["dry-run", "validator", "catr"],
        )

    async def _initialize_logistics(self) -> LogisticsSuite:
        staged_dir = self.workspace_root / "staged"
        loot_dir = self.workspace_root / "loot"
        logs_dir = self.workspace_root / "logs"
        path_guard = PathGuard(self.workspace_root, self._pathguard_audit)
        for path in (staged_dir, loot_dir, logs_dir):
            resolved = path_guard.validate_path(path, action="bootstrap_dir", mode="write")
            resolved.mkdir(parents=True, exist_ok=True)

        transfer_module = await asyncio.to_thread(
            self._load_cost_runtime_module,
            self.workspace_root,
            staged_dir,
            loot_dir,
            logs_dir / "transfers.json",
        )
        transfer_engine = await asyncio.to_thread(transfer_module.CerebroOpenTransfer)
        self.logger.action(
            "Logistics engine initialized",
            actor="main",
            data={
                "staged_dir": str(staged_dir),
                "loot_dir": str(loot_dir),
                "transfer_engine": transfer_engine.__class__.__name__,
            },
            tags=["logistics", "bootstrap"],
        )
        return LogisticsSuite(
            transfer_engine=transfer_engine,
            transfer_protocol_enum=transfer_module.TransferProtocol,
            path_guard=path_guard,
            staged_dir=staged_dir,
            loot_dir=loot_dir,
            logs_dir=logs_dir,
        )

    async def _load_scope(self) -> MissionScope:
        scope_path = self.workspace_root / "scope.json"
        env_scope = os.getenv("CAI_SCOPE_JSON", "").strip()
        env_scope_source = os.getenv("CAI_SCOPE_SOURCE", "").strip()
        if env_scope and env_scope_source:
            payload = json.loads(env_scope)
            return MissionScope(source=env_scope_source, payload=payload)
        if scope_path.exists():
            payload = json.loads(scope_path.read_text(encoding="utf-8"))
            return MissionScope(source=str(scope_path), payload=payload)
        if env_scope:
            payload = json.loads(env_scope)
            return MissionScope(source="env:CAI_SCOPE_JSON", payload=payload)
        raise PreFlightError(
            "Mission scope missing: provide scope.json in the workspace root or set CAI_SCOPE_JSON"
        )

    async def _hydrate_ground_truths(self, scope: MissionScope) -> None:
        if self.memory_suite is None:
            raise PreFlightError("Memory suite must be initialized before scope hydration")

        bus = self.memory_suite.bus
        payload = scope.payload
        bus.set_logic("mission.scope.source", scope.source, importance=5, agent_id="main")
        bus.set_logic("mission.scope.loaded", True, importance=5, agent_id="main")

        self._flatten_into_logic(bus, payload, prefix="mission.scope")

        targets = self._extract_list(payload, "targets", "target_ips", "hosts")
        cidrs = self._extract_list(payload, "authorized_cidrs", "cidrs", "scope_ranges")
        for target in targets:
            safe = self._logic_safe_key(str(target))
            bus.set_logic(f"host.{safe}.authorized", True, importance=5, agent_id="main")
            bus.set_logic(f"scope.{safe}.status", "in_scope", importance=5, agent_id="main")
        for cidr in cidrs:
            safe = self._logic_safe_key(str(cidr))
            bus.set_logic(f"scope_range.{safe}.status", "authorized", importance=5, agent_id="main")

        await asyncio.to_thread(bus.commit)
        self.logger.audit(
            "Mission scope hydrated into Logic engine",
            actor="main",
            data={"targets": targets, "authorized_cidrs": cidrs},
            tags=["scope", "logic"],
        )

    async def _initialize_cmcd(self) -> None:
        if not self.cmcd_path.exists():
            raise PreFlightError(f"CMCD template missing: {self.cmcd_path}")
        raw = self.cmcd_path.read_text(encoding="utf-8")
        self.cmcd_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if self.memory_suite is None:
            raise PreFlightError("Memory suite unavailable while loading CMCD")
        self.memory_suite.bus.set_logic(
            "mission.cmcd.path",
            str(self.cmcd_path),
            importance=4,
            agent_id="main",
        )
        self.memory_suite.bus.set_logic(
            "mission.cmcd.sha256",
            self.cmcd_sha256,
            importance=4,
            agent_id="main",
        )
        self.logger.audit(
            "CMCD loaded",
            actor="main",
            data={"path": str(self.cmcd_path), "sha256": self.cmcd_sha256},
            tags=["cmcd", "prompt"],
        )

    async def _mission_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if event.kind == "mission_bootstrap_complete":
                self.logger.audit(
                    "Mission loop activated",
                    actor="main",
                    data={"workspace": str(self.workspace_root)},
                    tags=["mission", "loop"],
                )
                continue

            if event.kind == "transfer_required":
                await self._dispatch_transfer(event.payload)
                continue

            if event.kind == "snapshot_requested":
                if self.memory_suite is not None:
                    await asyncio.to_thread(self.memory_suite.bus.commit)
                continue

            if event.kind == "shutdown":
                self.stop_event.set()
                continue

            self.logger.audit(
                "Unhandled mission event",
                actor="main",
                data={"kind": event.kind, "payload": event.payload},
                tags=["mission", "event"],
            )

    async def _dispatch_transfer(self, payload: dict[str, Any]) -> None:
        if self.logistics_suite is None or self.memory_suite is None:
            raise PreFlightError("Transfer requested before logistics or memory suite initialization")

        source = payload.get("source_path")
        destination = payload.get("target_url")
        protocol_name = str(payload.get("protocol", "https")).upper()
        request_id = str(payload.get("request_id", "unknown"))
        if not source or not destination:
            raise PreFlightError(
                f"Transfer request {request_id} missing required fields: source_path and target_url"
            )

        try:
            protocol = self.logistics_suite.transfer_protocol_enum[protocol_name]
        except KeyError as exc:
            raise PreFlightError(
                f"Transfer request {request_id} has unsupported protocol '{protocol_name}'"
            ) from exc

        result = await self.logistics_suite.transfer_engine.dispatch_transfer(
            source_path=source,
            target_url=destination,
            protocol=protocol,
        )
        self.status.transfers_dispatched += 1
        self.memory_suite.bus.set_logic(
            f"orchestrator.transfer.request.{request_id}.status",
            "completed",
            importance=4,
            agent_id="main",
        )
        self.memory_suite.storage.save_state_version(
            {
                "event": "transfer_completed",
                "request_id": request_id,
                "result": result,
            }
        )
        self.logger.action(
            "Transfer dispatched by mission loop",
            actor="main",
            data={"request_id": request_id, "result": result},
            tags=["transfer", "dispatch"],
        )

    async def _transfer_request_watcher(self) -> None:
        if self.memory_suite is None:
            return
        bus = self.memory_suite.bus
        seen_requests: set[str] = set()
        while not self.stop_event.is_set():
            try:
                nodes = bus.logic.search(_TRANSFER_PREFIX)
                request_ids = {
                    key[len(_TRANSFER_PREFIX):].split(".", 1)[0]
                    for key in nodes
                }
                for request_id in sorted(rid for rid in request_ids if rid and rid not in seen_requests):
                    status_key = f"{_TRANSFER_PREFIX}{request_id}.status"
                    if str(nodes.get(status_key, "")).lower() != "queued":
                        continue
                    payload = {
                        "request_id": request_id,
                        "source_path": nodes.get(f"{_TRANSFER_PREFIX}{request_id}.source_path"),
                        "target_url": nodes.get(f"{_TRANSFER_PREFIX}{request_id}.target_url"),
                        "protocol": nodes.get(f"{_TRANSFER_PREFIX}{request_id}.protocol", "https"),
                    }
                    seen_requests.add(request_id)
                    bus.set_logic(status_key, "dispatched", importance=3, agent_id="main")
                    await self.event_queue.put(MissionEvent(kind="transfer_required", payload=payload))
            except Exception as exc:
                self.status.last_error = str(exc)
                self.logger.finding(
                    "Transfer request watcher failure",
                    actor="main",
                    data={"error": str(exc), "traceback": traceback.format_exc()},
                    tags=["watcher", "transfer", "error"],
                )
                raise
            await asyncio.sleep(1.0)

    async def _dashboard_loop(self) -> None:
        if not console.is_terminal:
            return
        with Live(self._render_dashboard(), console=console, refresh_per_second=4) as live:
            while not self.stop_event.is_set():
                live.update(self._render_dashboard())
                await asyncio.sleep(_DASHBOARD_REFRESH_SECONDS)

    def _render_dashboard(self) -> Panel:
        table = Table(title="Cerebro Mission Control", expand=True)
        table.add_column("Subsystem", style="bold cyan")
        table.add_column("Status", style="white")
        table.add_column("Detail", style="white")

        hw = self.hardware_monitor.sample()
        table.add_row(
            "Hardware",
            self.status.hardware,
            self._hardware_detail(hw),
        )
        table.add_row(
            "Memory",
            self.status.memory,
            self._memory_detail(),
        )
        table.add_row(
            "Logistics",
            self.status.logistics,
            self._logistics_detail(),
        )
        table.add_row(
            "CMCD",
            self.status.cmcd,
            self.cmcd_sha256[:16] if self.cmcd_sha256 else "pending",
        )
        table.add_row(
            "Mission",
            self.status.mission,
            f"queue={self.event_queue.qsize()} transfers={self.status.transfers_dispatched}",
        )
        if self.status.last_error:
            table.add_row("Last Error", "fault", self.status.last_error[:120])
        return Panel(table, border_style="cyan")

    def _hardware_detail(self, readings: dict[str, float]) -> str:
        gpu = self.hardware.gpu_name if self.hardware else "pending"
        ram = readings.get("ram_used_gb", 0.0)
        ram_total = readings.get("ram_total_gb", 0.0)
        vram = readings.get("vram_used_mb", 0.0)
        vram_total = readings.get("vram_total_mb", 0.0)
        return f"{gpu} | RAM {ram:.1f}/{ram_total:.1f} GB | VRAM {vram:.0f}/{vram_total:.0f} MiB"

    def _memory_detail(self) -> str:
        if self.memory_suite is None:
            return "pending"
        health = self.memory_suite.bus.health()
        return (
            f"logic={health.get('logic_node_count', 0)} "
            f"episodic={health.get('episodic_short_term_used', 0)}/{health.get('episodic_short_term_max', 0)} "
            f"commits={health.get('commit_count', 0)}"
        )

    def _logistics_detail(self) -> str:
        if self.logistics_suite is None:
            return "pending"
        status = getattr(self.logistics_suite.transfer_engine, "status", None)
        status_value = getattr(status, "value", "pending")
        return f"COST={status_value} staged={self.logistics_suite.staged_dir}"

    async def _shutdown(self) -> None:
        self.stop_event.set()
        for task in self._background_tasks:
            task.cancel()
        if self.memory_suite is not None:
            try:
                await asyncio.to_thread(self.memory_suite.bus.commit)
            except Exception as exc:
                self.logger.finding(
                    "Final CCMB commit failed during shutdown",
                    actor="main",
                    data={"error": str(exc)},
                    tags=["shutdown", "ccmb"],
                )
            await asyncio.to_thread(self.memory_suite.search.close)
            await asyncio.to_thread(self.memory_suite.storage.close)
            await asyncio.to_thread(self.memory_suite.bus.close)
        self._write_session_summary()
        self.logger.audit(
            "Mission control shutdown complete",
            actor="main",
            data={"workspace": str(self.workspace_root)},
            tags=["main", "shutdown"],
        )
        self.logger.close()

    def _write_session_summary(self) -> None:
        health = self.memory_suite.bus.health() if self.memory_suite is not None else {}
        lines = [
            f"timestamp={datetime.now(tz=UTC).isoformat(timespec='seconds')}",
            f"workspace={self.workspace_root}",
            f"hardware_status={self.status.hardware}",
            f"memory_status={self.status.memory}",
            f"logistics_status={self.status.logistics}",
            f"cmcd_status={self.status.cmcd}",
            f"mission_status={self.status.mission}",
            f"transfers_dispatched={self.status.transfers_dispatched}",
            f"logic_node_count={health.get('logic_node_count', 0)}",
            f"commit_count={health.get('commit_count', 0)}",
            f"last_error={self.status.last_error or ''}",
        ]
        self.session_writer.write("logs/session_init.log", lines)

    def _ensure_workspace_tree(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        for rel in ("staged", "loot", "logs", "memory"):
            (self.workspace_root / rel).mkdir(parents=True, exist_ok=True)

    def _set_cuda_environment(self) -> None:
        defaults = {
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "CUDA_VISIBLE_DEVICES": os.getenv("CUDA_VISIBLE_DEVICES", "0"),
            "TOKENIZERS_PARALLELISM": "false",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        }
        for key, value in defaults.items():
            os.environ.setdefault(key, value)

    def _read_ram_profile(self) -> tuple[float, float]:
        if psutil is None:
            raise PreFlightError("psutil is required for hardware memory detection")
        vm = psutil.virtual_memory()
        return vm.total / (1024 ** 3), vm.available / (1024 ** 3)

    def _read_gpu_profile(self) -> tuple[str, float]:
        try:
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                timeout=3,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace").strip()
        except Exception as exc:
            raise PreFlightError(f"nvidia-smi unavailable or failed: {exc}") from exc
        first = output.splitlines()[0] if output else ""
        parts = [p.strip() for p in first.split(",")]
        if len(parts) < 2:
            raise PreFlightError(f"Unexpected nvidia-smi output: {output!r}")
        return parts[0], float(parts[1])

    def _flatten_into_logic(
        self,
        bus: CerebroMemoryBus,
        payload: Any,
        *,
        prefix: str,
    ) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                next_prefix = f"{prefix}.{self._logic_safe_key(str(key))}"
                self._flatten_into_logic(bus, value, prefix=next_prefix)
            return
        if isinstance(payload, list):
            for index, value in enumerate(payload):
                next_prefix = f"{prefix}.{index}"
                self._flatten_into_logic(bus, value, prefix=next_prefix)
            return
        bus.set_logic(prefix, payload, importance=3, agent_id="main")

    def _extract_list(self, payload: dict[str, Any], *keys: str) -> list[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [str(item) for item in value]
            if isinstance(value, str) and value.strip():
                return [item.strip() for item in value.split(",") if item.strip()]
        return []

    @staticmethod
    def _logic_safe_key(raw: str) -> str:
        return raw.replace("/", "_").replace(":", "_").replace(" ", "_")

    @staticmethod
    def _pathguard_audit(_event: str, _payload: Any) -> None:
        return

    @staticmethod
    def _load_cost_runtime_module(
        workspace_root: Path,
        staged_dir: Path,
        loot_dir: Path,
        transfer_log: Path,
    ) -> types.ModuleType:
        """Load the COST runtime without executing the import-unsafe agent footer.

        ``cai.internal.components.transfer`` currently performs heavy agent
        bootstrap and hard-loads a missing prompt template at import time. This
        loader first attempts a normal import; if that fails, it executes only
        the runtime portion of the module up to the agent integration marker.
        """
        try:
            transfer_module = importlib.import_module("cai.internal.components.transfer")
        except Exception:
            transfer_path = Path(__file__).resolve().parent / "internal" / "components" / "transfer.py"
            raw = transfer_path.read_text(encoding="utf-8")
            marker = "# ----------------------------------------------------------------------\n# Agent Integration (COST Agent)"
            if marker in raw:
                raw = raw.split(marker, 1)[0]
            module = types.ModuleType("cai.internal.components.transfer_runtime")
            module.__file__ = str(transfer_path)
            module.__package__ = "cai.internal.components"
            exec(compile(raw, str(transfer_path), "exec"), module.__dict__)
            transfer_module = module

        transfer_module.WORKSPACE_ROOT = workspace_root
        transfer_module.STAGING_DIR = staged_dir
        transfer_module.LOOT_DIR = loot_dir
        transfer_module.TRANSFER_LOGS = transfer_log
        return transfer_module


def _read_cai_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                return stripped.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return "0.0.0"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cai-main",
        description="Cerebro mission-control entrypoint (Open architecture).",
    )
    parser.add_argument("--version", action="store_true", help="Show CAI version and exit")
    parser.add_argument("--init-only", action="store_true", help="Run pre-flight initialization and exit")
    parser.add_argument("--list-tools", action="store_true", help="List host and Docker tool availability via Validator")
    parser.add_argument("--scope", type=str, default="", help="Path to mission scope JSON file")
    parser.add_argument("--workspace", type=str, default=os.getenv("CIR_WORKSPACE", "/workspace"), help="Workspace root path")
    parser.add_argument("--dry-run", action="store_true", help="Run dry-run validator checks without mission execution")
    parser.add_argument("--open-mode", action="store_true", help="Allow degraded hardware mode for initialization")
    return parser


def _register_main_thread_signals(loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event) -> None:
    """Register SIGINT/SIGTERM handlers in the main thread before loop run."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: loop.call_soon_threadsafe(stop_event.set))
        except NotImplementedError:  # pragma: no cover
            signal.signal(sig, lambda *_args: loop.call_soon_threadsafe(stop_event.set))


def _resolve_scope_into_env(scope_path: str, workspace_root: Path) -> None:
    guard = PathGuard(workspace_root, lambda _event, _payload: None)
    resolved = guard.validate_path(scope_path, action="cli_scope", mode="read")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    os.environ["CAI_SCOPE_JSON"] = json.dumps(payload, ensure_ascii=True)
    os.environ["CAI_SCOPE_SOURCE"] = f"cli:{resolved}"


async def _list_tools_with_validator(workspace_root: Path) -> int:
    from cai.tools.validation import validate_resource_health

    tools = [
        "python3", "curl", "nmap", "tcpdump", "sqlmap", "nikto", "ssh", "docker"
    ]
    health = await validate_resource_health(
        min_disk_free_mb=256,
        min_memory_free_mb=512,
        max_cpu_load_1m=32.0,
    )

    docker_binary = shutil.which("docker")
    docker_available = bool(docker_binary)
    docker_check_cmd = "command -v " + " ".join(shlex.quote(tool) for tool in tools)
    docker_presence: dict[str, bool] = {tool: False for tool in tools}
    docker_error = ""

    if docker_available:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "kalilinux/kali-rolling:latest",
                "sh",
                "-lc",
                docker_check_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_raw, stderr_raw = await asyncio.wait_for(proc.communicate(), timeout=20)
            stdout = stdout_raw.decode("utf-8", errors="replace")
            if int(proc.returncode or 1) == 0:
                for line in stdout.splitlines():
                    name = Path(line.strip()).name
                    if name in docker_presence:
                        docker_presence[name] = True
            else:
                docker_error = stderr_raw.decode("utf-8", errors="replace").strip()
        except Exception as exc:
            docker_error = str(exc)

    print(json.dumps({
        "validator_gate": "validate_resource_health",
        "validator": health,
        "tools": [
            {
                "name": tool,
                "host_available": bool(shutil.which(tool)),
                "docker_available": docker_presence.get(tool, False) if docker_available else False,
            }
            for tool in tools
        ],
        "docker_error": docker_error,
        "workspace": str(workspace_root),
    }, indent=2, ensure_ascii=True))
    return 0 if health.get("ok") else 2


async def amain() -> int:
    """Async main entrypoint used by console scripts and tests."""
    controller = CerebroMissionControl()
    return await controller.run()


def main(*, register_main_thread_signals: bool = False) -> int:
    """CLI wrapper around mission control entrypoint."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.version:
        console.print(_read_cai_version())
        return 0

    workspace_root = Path(str(args.workspace)).resolve()
    os.environ["CIR_WORKSPACE"] = str(workspace_root)

    if args.scope:
        try:
            _resolve_scope_into_env(args.scope, workspace_root)
        except Exception as exc:
            console.print(
                Panel(
                    str(exc),
                    title="Scope Validation Failure",
                    border_style="red",
                )
            )
            return 2

    if args.init_only and not args.scope and not os.getenv("CAI_SCOPE_JSON") and not (workspace_root / "scope.json").exists():
        os.environ["CAI_SCOPE_JSON"] = json.dumps(
            {
                "targets": [],
                "authorized_cidrs": [],
                "boundaries": {"mode": "init_only"},
            },
            ensure_ascii=True,
        )

    if args.list_tools:
        try:
            return asyncio.run(_list_tools_with_validator(workspace_root))
        except Exception as exc:
            console.print(
                Panel(
                    str(exc),
                    title="Tool Listing Failure",
                    border_style="red",
                )
            )
            return 1

    try:
        controller = CerebroMissionControl(
            workspace_root=workspace_root,
            init_only=bool(args.init_only),
            open_mode=bool(args.open_mode),
            dry_run=bool(args.dry_run),
        )

        if register_main_thread_signals:
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                _register_main_thread_signals(loop, controller.stop_event)
                return loop.run_until_complete(controller.run())
            finally:
                loop.close()
                asyncio.set_event_loop(None)

        return asyncio.run(controller.run())
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user.[/yellow]")
        return 130
    except Exception as exc:
        console.print(
            Panel(
                str(exc),
                title="Cerebro Mission Control Failure",
                border_style="red",
            )
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(register_main_thread_signals=True))