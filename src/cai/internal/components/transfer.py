"""
System data transfer utilities (telemetry disabled)
"""


def process(path: str, endpoint: str, identifier: str | None = None) -> bool:
    """Telemetry uploads are disabled."""
    return False
