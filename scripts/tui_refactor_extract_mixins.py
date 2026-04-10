#!/usr/bin/env python3
"""Extract mixin classes from app_impl.py into separate focused modules.

Extraction map (1-based line numbers from original 3874-line app_impl.py):
  - teams.py      : lines 176-211  (TEAM_PRESETS, TEAM_PLAYBOOK_HINTS constants)
  - telemetry.py  : lines 312-1264 (TelemetryMixin)
  - layout.py     : lines 1287-1488 (ResponsiveMixin)
  - tools_mixin.py: lines 1713-2123 (ToolsMixin)
  - queue_mixin.py: lines 2850-3028 (QueueMixin)
  - sessions_mixin.py: lines 3103-3414 (SessionsMixin)
"""

import os

APP_IMPL = "src/cai/tui/app_impl.py"
TUI_DIR = "src/cai/tui"

# Read current file
with open(APP_IMPL, encoding="utf-8") as f:
    orig_lines = f.readlines()

print(f"Total lines: {len(orig_lines)}")


def get_range(start_1: int, end_1: int) -> str:
    """Return lines start_1..end_1 (1-based, inclusive) joined as a string."""
    return "".join(orig_lines[start_1 - 1 : end_1])


# ---------------------------------------------------------------------------
# Step 1 — Create teams.py
# ---------------------------------------------------------------------------
teams_content = (
    '"""Team preset constants for CAI TUI.\n'
    "Extracted from app_impl.py for use by layout.py and other modules.\n"
    '"""\n'
    "\n"
    "from __future__ import annotations\n"
    "\n"
    + get_range(176, 211)
    + "\n"
)

with open(os.path.join(TUI_DIR, "teams.py"), "w", encoding="utf-8") as f:
    f.write(teams_content)
print(f"Created teams.py ({len(teams_content.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 2 — Create telemetry.py (TelemetryMixin)
# ---------------------------------------------------------------------------
telemetry_header = '''\
"""TelemetryMixin — metrics, cost tracking, and context snapshot methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
import time
from typing import Any, cast

from textual import work
from textual.widgets import Static, TextArea, RichLog
from rich.text import Text as RichText

from cai.config import CAI_CTX_LIMIT
from cai.tui.screens.common import ContextUsageModal
from cai.tui.components.terminal import TerminalPanel


class TelemetryMixin:
    """Mixin providing telemetry, cost tracking, and context snapshot methods."""

'''

telemetry_content = telemetry_header + get_range(312, 1264)

with open(os.path.join(TUI_DIR, "telemetry.py"), "w", encoding="utf-8") as f:
    f.write(telemetry_content)
print(f"Created telemetry.py ({len(telemetry_content.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 3 — Create layout.py (ResponsiveMixin)
# ---------------------------------------------------------------------------
layout_header = '''\
"""ResponsiveMixin — responsive layout and tab-navigation methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

from typing import Any, cast

import textual.containers as _containers
from textual.containers import Horizontal, Vertical, ScrollableContainer

try:
    TabbedContent = _containers.TabbedContent  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    TabbedContent = cast(Any, object)

from textual.widgets import Button, Static

from cai.tui.components.terminal import TerminalPanel
from cai.tui.teams import TEAM_PRESETS


class ResponsiveMixin:
    """Mixin providing responsive layout and tab-navigation helpers."""

'''

layout_content = layout_header + get_range(1287, 1488)

with open(os.path.join(TUI_DIR, "layout.py"), "w", encoding="utf-8") as f:
    f.write(layout_content)
print(f"Created layout.py ({len(layout_content.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 4 — Create tools_mixin.py (ToolsMixin)
# ---------------------------------------------------------------------------
tools_header = '''\
"""ToolsMixin — tool registry, call history, and inject-mode methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, cast

from textual import work
from textual.containers import ScrollableContainer
from textual.widgets import Button, Static, RichLog, TextArea
from rich.text import Text as RichText

from cai.tui.components.terminal import TerminalPanel
from cai.tui.components.sidebar import Sidebar
from cai.tui.screens.common import PromptModal


class ToolsMixin:
    """Mixin providing tool registry, history, and inject-mode helpers."""

'''

tools_content = tools_header + get_range(1713, 2123)

with open(os.path.join(TUI_DIR, "tools_mixin.py"), "w", encoding="utf-8") as f:
    f.write(tools_content)
print(f"Created tools_mixin.py ({len(tools_content.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 5 — Create queue_mixin.py (QueueMixin)
# ---------------------------------------------------------------------------
queue_header = '''\
"""QueueMixin — task queue and broadcast prompt methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

from textual import work, on
from textual.widgets import Button, Static, ListView, ListItem, Label, Input

from cai.tui.components.terminal import TerminalPanel


class QueueMixin:
    """Mixin providing task queue management and broadcast helpers."""

'''

queue_content = queue_header + get_range(2850, 3028)

with open(os.path.join(TUI_DIR, "queue_mixin.py"), "w", encoding="utf-8") as f:
    f.write(queue_content)
print(f"Created queue_mixin.py ({len(queue_content.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 6 — Create sessions_mixin.py (SessionsMixin)
# ---------------------------------------------------------------------------
sessions_header = '''\
"""SessionsMixin — session discovery, preview, and CRUD methods for CAI TUI.

Extracted from app_impl.py. All methods operate on ``self`` and are designed to
be composed into ``CAIApp`` via multiple inheritance.
"""

from __future__ import annotations

import os
from typing import Any, cast

from textual import work
from textual.widgets import Static, RichLog, ListItem
from rich.text import Text as RichText

from cai.tui.screens.common import ConfirmModal, PromptModal
from cai.tui.components.header import _pretty_name
from cai.tui.components.sidebar import Sidebar


class SessionsMixin:
    """Mixin providing session discovery, preview, and lifecycle helpers."""

'''

sessions_content = sessions_header + get_range(3103, 3414)

with open(os.path.join(TUI_DIR, "sessions_mixin.py"), "w", encoding="utf-8") as f:
    f.write(sessions_content)
print(f"Created sessions_mixin.py ({len(sessions_content.splitlines())} lines)")

# ---------------------------------------------------------------------------
# Step 7 — Transform app_impl.py
# ---------------------------------------------------------------------------
content = "".join(orig_lines)

# 7a. Remove extracted method blocks (from bottom to top to preserve offsets)
# Each block is replaced by a single comment explaining where they went.
REMOVALS = [
    # (start_1, end_1, replacement_comment)
    (
        3101, 3414,
        "    # Sessions methods → cai.tui.sessions_mixin.SessionsMixin\n",
    ),
    (
        2848, 3028,
        "    # Queue/broadcast methods → cai.tui.queue_mixin.QueueMixin\n",
    ),
    (
        1713, 2123,
        "    # Tool registry/history methods → cai.tui.tools_mixin.ToolsMixin\n",
    ),
    (
        1285, 1488,
        "    # Responsive layout methods → cai.tui.layout.ResponsiveMixin\n",
    ),
    (
        312, 1264,
        "    # Telemetry/metrics methods → cai.tui.telemetry.TelemetryMixin\n",
    ),
    (
        176, 212,
        "# Team presets — defined in cai.tui.teams, imported for backward compat.\n"
        "from cai.tui.teams import TEAM_PRESETS, TEAM_PLAYBOOK_HINTS\n",
    ),
]

# Work on list of lines for precise slice replacement
lines = list(orig_lines)
for start_1, end_1, replacement in REMOVALS:
    lines[start_1 - 1 : end_1] = [replacement]

content = "".join(lines)

# 7b. Add mixin imports right after the TerminalPanel import
terminal_import_line = "from cai.tui.components.terminal import TerminalPanel\n"
mixin_imports = (
    "from cai.tui.components.terminal import TerminalPanel\n"
    "from cai.tui.telemetry import TelemetryMixin\n"
    "from cai.tui.layout import ResponsiveMixin\n"
    "from cai.tui.tools_mixin import ToolsMixin\n"
    "from cai.tui.queue_mixin import QueueMixin\n"
    "from cai.tui.sessions_mixin import SessionsMixin\n"
)
content = content.replace(terminal_import_line, mixin_imports, 1)

# 7c. Update the class definition
content = content.replace(
    "class CAIApp(App):\n",
    "class CAIApp(TelemetryMixin, ResponsiveMixin, ToolsMixin, QueueMixin, SessionsMixin, App):\n",
    1,
)

# ---------------------------------------------------------------------------
# Smoke checks
# ---------------------------------------------------------------------------
checks = [
    ("def _now_ms", "telemetry methods removed"),
    ("def _responsive_mode_for_size", "layout methods removed"),
    ("def _build_tool_registry", "tools methods removed"),
    ("def _parse_broadcast_suffix", "queue methods removed"),
    ("def _populate_sessions_list\n", "sessions methods removed"),
    ("TelemetryMixin", "TelemetryMixin imported"),
    ("ResponsiveMixin", "ResponsiveMixin imported"),
    ("ToolsMixin", "ToolsMixin imported"),
    ("QueueMixin", "QueueMixin imported"),
    ("SessionsMixin", "SessionsMixin imported"),
    ("TEAM_PRESETS", "TEAM_PRESETS reference present"),
]
for pattern, label in checks:
    found = pattern in content
    if label.endswith("removed"):
        status = "FAIL (still present!)" if found else "OK"
    else:
        status = "OK" if found else "FAIL (missing!)"
    print(f"  [{status}] {label}")

# Write result
with open(APP_IMPL, "w", encoding="utf-8") as f:
    f.write(content)

final_lines = content.count("\n")
print(f"\nFinal app_impl.py: {final_lines} lines (was {len(orig_lines)})")
print("Transformation complete.")
