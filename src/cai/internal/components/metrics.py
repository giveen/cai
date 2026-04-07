"""
Performance metrics and analytics utilities (telemetry disabled)
"""
from typing import Optional


def process_metrics(path: str, sid: Optional[str] = None) -> bool:
    """Telemetry uploads are disabled."""
    return False


def process_intermediate_logs(path: str, sid: Optional[str] = None) -> bool:
    """Telemetry uploads are disabled."""
    return False 