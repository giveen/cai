"""
System data transfer utilities (telemetry disabled)
"""

from typing import Optional


def process(path: str, endpoint: str, identifier: Optional[str] = None) -> bool:
    """Telemetry uploads are disabled."""
    return False
