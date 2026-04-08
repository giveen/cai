"""Simple in-memory metrics collector for RAG ingestion and retrieval.

This lightweight collector is intentionally dependency-free so it can
be used in CI and development. It provides counters, gauges, and
simple histograms (as lists) that can be exported for monitoring.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = {}
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, List[float]] = {}

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] = int(self.counters.get(name, 0)) + int(amount)

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self.gauges[name] = float(value)

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self.histograms.setdefault(name, []).append(float(value))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": dict(self.gauges),
                "histograms": {k: list(v) for k, v in self.histograms.items()},
            }


# Global collector instance
_COLLECTOR = MetricsCollector()


def collector() -> MetricsCollector:
    return _COLLECTOR


def export_metrics() -> Dict[str, Any]:
    """Return a snapshot of current metrics."""
    return _COLLECTOR.snapshot()


__all__ = ["collector", "export_metrics", "MetricsCollector"]
