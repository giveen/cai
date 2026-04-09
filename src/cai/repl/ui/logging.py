"""
Module for CAI REPL session logging.
"""

from pathlib import Path
import re
import textwrap
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.markup import escape as _escape


def render_tool_output(
    tool_name: str,
    output: str,
    agent_name: Optional[str] = None,
    style: str = "cyan",
    max_chars: int = 4000,
) -> None:
    """Render a tool output in a consistent Panel.

    - `tool_name` becomes the Panel title.
    - `agent_name` becomes the Panel subtitle.
    - Sanitizes ANSI escapes and wraps/truncates the output.
    """
    if output is None:
        return

    try:
        sanitized = str(output)
        sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")

        # remove ANSI escape sequences
        try:
            sanitized = re.sub(r"\x1B[@-_][0-?]*[ -/]*[@-~]", "", sanitized)
        except Exception:
            sanitized = re.sub(r"\x1b\[[0-9;]*[mK]", "", sanitized)

        # drop obvious progress meter header/lines (starts with "% ")
        lines = []
        for ln in sanitized.splitlines():
            s = ln.strip()
            if not s:
                lines.append("")
                continue
            if s.startswith("% ") or s.startswith("%Total") or s.startswith("%\t"):
                continue
            if set(s) <= set("-=.#|<>*%0123456789 ") and len(s) < 120:
                continue
            lines.append(ln)
        sanitized = "\n".join(lines)

        # collapse excessive blank lines
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)

        # truncate
        if len(sanitized) > max_chars:
            sanitized = sanitized[:max_chars] + "…"

        # wrap to terminal width
        try:
            width = Console().size.width or 120
        except Exception:
            width = 120
        wrap_width = max(40, int(width) - 20)
        wrapped = []
        for ln in sanitized.splitlines():
            if len(ln) > wrap_width:
                wrapped.append(textwrap.fill(ln, width=wrap_width))
            else:
                wrapped.append(ln)
        sanitized = "\n".join(wrapped)

        # Render panel
        c = Console()
        title = str(tool_name) if tool_name is not None else "tool"
        subtitle = str(agent_name) if agent_name is not None else ""
        c.print(Panel(sanitized, title=title, subtitle=subtitle, style=style))
    except Exception:
        try:
            print(output)
        except Exception:
            pass


def setup_session_logging():
    """
    Set up session logging.

    Returns:
        Tuple of (history_file, session_log, log_interaction function)
    """
    # Setup history file - use home directory for cross-platform compatibility
    history_dir = Path.home() / ".cai"
    history_dir.mkdir(exist_ok=True, parents=True)
    history_file = history_dir / "history.txt"

    # # Setup session log file
    # session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # session_log = history_dir / f"session_{session_id}.log"

    # # Function to log interactions
    # def log_interaction(role, content):
    #     with open(session_log, "a", encoding="utf-8") as f:
    #         f.write(
    #             f"\n[{
    #                 datetime.datetime.now().strftime('%H:%M:%S')}] {
    #                 role.upper()}:\n")
    #         f.write(f"{content}\n")

    # return history_file, session_log, log_interaction
    return history_file
