"""
Performance metrics and analytics utilities (telemetry disabled)
"""


def process_metrics(path: str, sid: str | None = None) -> bool:
    """Telemetry uploads are disabled."""
    return False


def process_intermediate_logs(path: str, sid: str | None = None) -> bool:
    """Telemetry uploads are disabled."""
    return False
