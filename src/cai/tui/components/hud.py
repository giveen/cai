"""HUDWidget — heads-up telemetry display for the CAI TUI.

Behaviour
---------
* **Local GPU mode** — when ``LOCAL_API_BASE`` contains ``localhost`` or
  ``127.0.0.1`` *and* ``pynvml`` is importable the primary sparkline shows
  VRAM usage (%) and the status line shows ``VRAM used/total · Load%``.
  Amber (``#ffaa00``) fires when VRAM exceeds 90 %.

* **Remote mode** — for any other backend the sparkline shows average
  request latency (ms) and the status line shows TPS and remaining
  context-window percent.  Amber fires when latency exceeds 2 000 ms.

* **Offline / no-data fallback** — when neither GPU nor remote stats are
  available the widget renders ``OFFLINE`` / ``NO_DATA`` instead of raising
  an exception.

The widget polls its own ``get_telemetry_data()`` every 2 seconds via
``set_interval``.  For remote metrics it reads
``self.app._telemetry_stats_by_term`` live (populated by
``TelemetryMixin``), so no extra wiring is needed in ``CAIApp``.
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Sparkline, Static

from cai.config import CAI_CTX_LIMIT

# ---------------------------------------------------------------------------
# Optional pynvml — graceful fallback if not installed
# ---------------------------------------------------------------------------
try:
    import pynvml  # type: ignore[import]

    _PYNVML_AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore[assignment]
    _PYNVML_AVAILABLE = False

_HISTORY_LEN = 60
_GPU_VRAM_WARN_PCT = 90.0
_REMOTE_LATENCY_WARN_MS = 2_000.0

_COLOR_GREEN = "#00ff00"
_COLOR_AMBER = "#ffaa00"
_COLOR_DIM = "#555555"


def _is_local_backend() -> bool:
    """Return True when LOCAL_API_BASE points at a localhost endpoint."""
    base = os.getenv("LOCAL_API_BASE", "").strip()
    return bool(base) and ("127.0.0.1" in base or "localhost" in base)


# ---------------------------------------------------------------------------
# HUDWidget
# ---------------------------------------------------------------------------
class HUDWidget(Widget):
    """Live telemetry widget: GPU stats (local) or request metrics (remote).

    Compose layout::

        ┌─ #hud-label ─────────────── [VRAM | LATENCY | NO_DATA] ─┐
        │ #hud-sparkline                                            │
        │ #hud-status ─── VRAM 3 421MB/8 192MB · Load 47%         │
        └───────────────────────────────────────────────────────────┘
    """

    DEFAULT_CSS = """
    HUDWidget {
        height: auto;
        width: 100%;
        background: #000800;
        border: solid #003300;
        padding: 0 1;
    }

    HUDWidget .hud-label {
        height: 1;
        color: #00aa00;
        background: #000800;
        margin-top: 1;
        text-style: bold;
    }

    HUDWidget #hud-sparkline {
        height: 5;
        background: #000800;
        border: solid #003300;
        margin-bottom: 0;
    }

    HUDWidget .hud-status {
        height: 1;
        color: #00cc00;
        background: #000800;
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._history: deque[float] = deque([0.0] * _HISTORY_LEN, maxlen=_HISTORY_LEN)
        self._nvml_init: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("TELEMETRY", id="hud-label", classes="hud-label")
        yield Sparkline(
            list(self._history),
            id="hud-sparkline",
            summary_function=max,
        )
        yield Static("Initialising…", id="hud-status", classes="hud-status")

    def on_mount(self) -> None:
        if _PYNVML_AVAILABLE and _is_local_backend():
            try:
                pynvml.nvmlInit()
                self._nvml_init = True
            except Exception:
                pass
        self.set_interval(2.0, self._tick)

    def on_unmount(self) -> None:
        if self._nvml_init:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_telemetry_data(self) -> dict:
        """Return a normalised telemetry snapshot.

        Returns a ``dict`` with keys:

        ``mode``
            ``"local_gpu"``, ``"remote"``, or ``"offline"``
        ``primary``
            Numeric value fed to the sparkline (VRAM % or latency ms).
        ``label``
            Short label rendered above the sparkline.
        ``color``
            Hex colour string — green for healthy, amber for warning.
        ``status``
            One-line secondary info string.
        """
        if self._nvml_init and _is_local_backend():
            return self._gather_gpu_stats()

        if not _is_local_backend():
            remote = self._gather_remote_stats()
            if remote is not None:
                return remote

        return {
            "mode": "offline",
            "primary": 0.0,
            "label": "NO_DATA",
            "color": _COLOR_DIM,
            "status": "OFFLINE — no GPU and no remote metrics available",
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _gather_gpu_stats(self) -> dict:
        """Read VRAM and utilisation from the first NVML device."""
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)

            vram_pct = float(mem.used) / float(max(mem.total, 1)) * 100.0
            gpu_load = float(util.gpu)
            used_mb = mem.used // (1024 * 1024)
            total_mb = mem.total // (1024 * 1024)

            color = _COLOR_AMBER if vram_pct > _GPU_VRAM_WARN_PCT else _COLOR_GREEN
            status = f"VRAM {used_mb}MB/{total_mb}MB · Load {gpu_load:.0f}%"

            return {
                "mode": "local_gpu",
                "primary": vram_pct,
                "label": "VRAM",
                "color": color,
                "status": status,
            }
        except Exception:
            return {
                "mode": "offline",
                "primary": 0.0,
                "label": "VRAM",
                "color": _COLOR_DIM,
                "status": "GPU ERROR — NVML query failed",
            }

    def _gather_remote_stats(self) -> dict | None:
        """Read latency / TPS / context from the app's live telemetry map.

        Returns ``None`` when no runs have been recorded yet so the caller
        can fall back to the offline placeholder.
        """
        try:
            stats_map: dict = getattr(self.app, "_telemetry_stats_by_term", {})
            if not stats_map:
                return None

            total_last_ms = 0
            count = 0
            total_out_tokens = 0
            total_in_tokens = 0
            total_sum_latency_ms = 0

            for s in stats_map.values():
                lat = s.get("last_total_latency_ms")
                if lat is not None:
                    total_last_ms += int(lat)
                    count += 1
                total_out_tokens += int(s.get("output_tokens", 0) or 0)
                total_in_tokens += int(s.get("input_tokens", 0) or 0)
                total_sum_latency_ms += int(s.get("sum_total_latency_ms", 0) or 0)

            if count == 0:
                return None

            avg_latency_ms = float(total_last_ms) / count
            tps = float(total_out_tokens) / max(float(total_sum_latency_ms) / 1_000.0, 0.001)
            ctx_remaining_pct = max(
                0.0,
                (1.0 - float(total_in_tokens) / max(float(CAI_CTX_LIMIT), 1.0)) * 100.0,
            )

            color = _COLOR_AMBER if avg_latency_ms > _REMOTE_LATENCY_WARN_MS else _COLOR_GREEN
            status = f"TPS {tps:.1f} · CTX remaining {ctx_remaining_pct:.0f}%"

            return {
                "mode": "remote",
                "primary": avg_latency_ms,
                "label": "LATENCY",
                "color": color,
                "status": status,
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Timer tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Sample telemetry and refresh all child widgets."""
        data = self.get_telemetry_data()
        primary = float(data.get("primary", 0.0))
        label = str(data.get("label", "TELEMETRY"))
        color = str(data.get("color", _COLOR_GREEN))
        status = str(data.get("status", ""))

        self._history.append(primary)

        try:
            self.query_one("#hud-label", Static).update(f"[{color}]{label}[/{color}]")
        except Exception:
            pass

        try:
            self.query_one("#hud-sparkline", Sparkline).data = list(self._history)
        except Exception:
            pass

        try:
            self.query_one("#hud-status", Static).update(f"[{color}]{status}[/{color}]")
        except Exception:
            pass
