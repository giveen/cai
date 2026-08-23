"""
System endpoint configuration
"""

_UPLOAD_ENDPOINT = "https://logs.aliasrobotics.com/upload"


def process(suffix: str = None) -> str:
    """Return the telemetry upload endpoint, with an optional path suffix."""
    if suffix:
        return f"{_UPLOAD_ENDPOINT}/{suffix}"
    return _UPLOAD_ENDPOINT
