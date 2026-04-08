"""
Network utilities (telemetry disabled)
"""

def process() -> dict:
    """Telemetry uploads are disabled; always report offline."""
    return {"status": False, "mode": "disabled"}
