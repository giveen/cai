"""Header component: Matrix banner and agent-name prettifier.

This module centralises the ASCII banner and `_pretty_name` helper so
components can import them without depending on the large `app.py`.
"""

from __future__ import annotations

from typing import List, Optional

from textual.widgets import Static
from textual.reactive import reactive
from rich.text import Text as RichText

# ---------------------------------------------------------------------------
# ASCII banner – Matrix green applied via Rich Text styles at render time
# ---------------------------------------------------------------------------
_BANNER_LINES: List[str] = [
    "          CCCCCCCCCCCCC      +++++++++    +++++++++     IIIIIIIIII",
    "        CCC::::::::::::C  ++++++++++     ++++++++++     I::::::::I",
    "       CC:::::::::::::::C++++++++++       ++++++++++    I::::::::I",
    "      C:::::CCCCCCCC::::C+++++++++   ++    +++++++++    II::::::II",
    "     C:::::C       CCCCCC +++++++  +++++    +++++++       I::::I  ",
    "     C:::::C               +++++  +++++++    +++++        I::::I  ",
    "     C:::::C               ++++    +++++++    ++++        I::::I  ",
    "     C:::::C                ++      +++++++    ++         I::::I  ",
    "     C:::::C                 +   +++++++++++   +          I::::I  ",
    "     C:::::C                   +++++++++++++              I::::I  ",
    "     C:::::C                    +++++++++++               I::::I  ",
    "     C:::::C       CCCCCC        +++++++++                I::::I  ",
    "      C:::::CCCCCCCC::::C         +++++++              II::::::II ",
    "       CC:::::::::::::::C           ++++               I::::::::I ",
    "         CCC::::::::::::C             ++               I::::::::I ",
    "            CCCCCCCCCCCCC               +              IIIIIIIIII ",
    "",
    "               Cybersecurity AI (CAI)   ·   Bug bounty-ready AI  ",
]

# ---------------------------------------------------------------------------
# Agent name prettifier
# ---------------------------------------------------------------------------
_ACRONYMS = {
    "sast",
    "dfir",
    "dns",
    "smtp",
    "sdr",
    "ctf",
    "mcp",
    "api",
    "iot",
    "ai",
    "ml",
    "ids",
    "ips",
    "osint",
}

# Suffix → short distinguishing label appended after the base name
_SUFFIX_LABELS = [
    ("_swarm_pattern", " ↺"),  # swarm
    ("_swarm_pa", " ↺"),
    ("_pattern", " ⊕"),  # non-swarm pattern
    ("_agent", ""),  # plain agent – strip cleanly
]


def _pretty_name(raw: str) -> str:
    """Convert internal agent name → display label.

    Examples:
        android_sast_agent            → Android SAST
        redteam_agent                 → Redteam
        redteam_swarm_pattern         → Redteam ↺
        dns_smtp_agent                → DNS SMTP
    """
    name = raw
    suffix_label = ""
    for suffix, label in _SUFFIX_LABELS:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            suffix_label = label
            break
    # Abbreviate long blue/red team names to fit the sidebar
    name = (
        name.replace("blue_team_red_team_shared_context", "Blue/Red Shared")
        .replace("blue_team_red_team_split_context", "Blue/Red Split")
        .replace("blue_team_red_team", "Blue/Red Team")
    )
    if "_" not in name and " " in name:
        return name + suffix_label
    parts: list[str] = []
    for part in name.split("_"):
        if not part:
            continue
        parts.append(part.upper() if part.lower() in _ACRONYMS else part.capitalize())
    return (" ".join(parts) + suffix_label) or raw


class MatrixHeader(Static):
    """Matrix-style header widget rendering the ASCII banner.

    The header exposes a reactive `challenge_name` property so external
    code (for example, the TUI controller) can update the current
    CTF/Challenge name and the header will reflect it automatically.
    """

    challenge_name: Optional[str] = reactive(None)

    def __init__(self, banner_lines: Optional[List[str]] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.banner_lines = banner_lines or _BANNER_LINES

    def render(self) -> RichText:
        # Render banner with bold green styling applied to the whole block.
        text = RichText("\n".join(self.banner_lines), style="bold green")
        if self.challenge_name:
            text.append("\n")
            text.append(f" Challenge: {self.challenge_name}", style="bold green")
        return text


# Compatibility: existing code imports `Header` — point it at our new class.
Header = MatrixHeader

__all__ = ["Header", "MatrixHeader", "_BANNER_LINES", "_pretty_name"]
