"""
System endpoint configuration
"""
from typing import Optional


def process(suffix: Optional[str] = None) -> str:
    """Return the telemetry endpoint.

    The endpoint is intentionally explicit (no obfuscation). If a suffix
    is provided, it will be appended with a slash.
    """
    base = "https://logs.aliasrobotics.com/upload"
    if suffix:
        return f"{base}/{suffix}"
    return base