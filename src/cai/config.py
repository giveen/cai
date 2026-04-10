"""Central configuration helpers for runtime-wide CAI settings.

Expose small, import-safe constants derived from the environment so other
modules can consistently reference limits like `CAI_CTX_LIMIT` and the
auto-compact threshold.

This module intentionally has no other dependencies to avoid import cycles.
"""

from __future__ import annotations

import os


# Default maximum context token limit used across the application.
# Can be overridden via the CAI_CTX_LIMIT environment variable.
def _read_int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except Exception:
        return default


# Default to 393,216 tokens unless overridden by environment.
CAI_CTX_LIMIT: int = _read_int_env("CAI_CTX_LIMIT", 393216)

# CAI_AUTO_COMPACT_THRESHOLD may be provided either as an absolute token
# count (e.g. "353894") or as a fractional percentage (e.g. "0.9").
# If unset, default to 90% of `CAI_CTX_LIMIT`.
_raw_auto = os.environ.get("CAI_AUTO_COMPACT_THRESHOLD", "")
if _raw_auto:
    # Percent-style value (0.x) -> convert relative to CAI_CTX_LIMIT
    try:
        f = float(_raw_auto)
        if 0.0 < f <= 1.0:
            CAI_AUTO_COMPACT_THRESHOLD: int = int(CAI_CTX_LIMIT * f)
        else:
            CAI_AUTO_COMPACT_THRESHOLD = int(f)
    except Exception:
        CAI_AUTO_COMPACT_THRESHOLD = int(CAI_CTX_LIMIT * 0.9)
else:
    CAI_AUTO_COMPACT_THRESHOLD = int(CAI_CTX_LIMIT * 0.9)

__all__ = ["CAI_CTX_LIMIT", "CAI_AUTO_COMPACT_THRESHOLD"]
