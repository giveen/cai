"""
System endpoint configuration (telemetry disabled)
"""
from typing import Optional

def process(suffix: Optional[str] = None) -> None:
    """Telemetry uploads are disabled."""
    return None 