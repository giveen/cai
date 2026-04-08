"""Session metrics and reporting utilities for the REPL/UI.

Contains `format_time` and `display_session_report` so the CLI can
delegate session-summary rendering to this focused module.
"""

from __future__ import annotations

import logging
from typing import Optional

from rich.console import Console


def format_time(seconds: float) -> str:
    """Format seconds into H:MM:SS string with zero-padded fields."""
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def display_session_report(session_logger, console: Optional[Console] = None) -> dict:
    """Compute and print a session summary panel.

    Returns the computed metrics dictionary for callers that need it.
    This is best-effort and will not raise on errors.
    """
    logger = logging.getLogger(__name__)
    try:
        if console is None:
            console = Console()

        # Import inside function to avoid heavy imports at module import time
        from cai.util import COST_TRACKER, get_active_time_seconds, get_idle_time_seconds

        active_time_seconds = get_active_time_seconds()
        idle_time_seconds = get_idle_time_seconds()
        total_seconds = active_time_seconds + idle_time_seconds

        active_time_formatted = format_time(active_time_seconds)
        idle_time_formatted = format_time(idle_time_seconds)

        metrics = {
            "session_time": format_time(total_seconds),
            "active_time": active_time_formatted,
            "idle_time": idle_time_formatted,
            "llm_time": format_time(active_time_seconds),
            "llm_percentage": round((active_time_seconds / total_seconds) * 100, 1)
            if total_seconds > 0
            else 0.0,
            "session_cost": f"${COST_TRACKER.session_total_cost:.6f}",
        }

        logging_path = (
            session_logger.filename
            if (session_logger and hasattr(session_logger, "filename"))
            else None
        )

        content = []
        content.append(f"Session Time: {metrics['session_time']}")
        content.append(f"Active Time: {metrics['active_time']} ({metrics['llm_percentage']}%)")
        content.append(f"Idle Time: {metrics['idle_time']}")
        content.append(f"Total Session Cost: {metrics['session_cost']}")
        if logging_path:
            content.append(f"Log available at: {logging_path}")

        # Render using Rich to match previous CLI presentation
        try:
            from rich.box import ROUNDED
            from rich.console import Group
            from rich.panel import Panel
            from rich.text import Text

            text_content = []
            for line in content:
                if "Total Session Cost" in line:
                    cost_text = Text()
                    parts = line.split(":", 1)
                    cost_text.append(parts[0] + ":", style="bold")
                    cost_text.append(parts[1], style="bold green")
                    text_content.append(cost_text)
                else:
                    text_content.append(Text(line))

            time_panel = Panel(
                Group(*text_content),
                border_style="blue",
                box=ROUNDED,
                padding=(0, 1),
                title="[bold]Session Summary[/bold]",
                title_align="left",
            )
            console.print(time_panel, end="")
        except Exception:
            # Fall back to plain printing if Rich isn't available or rendering fails
            for line in content:
                print(line)

        return metrics
    except Exception as e:
        logger.debug("Error generating session report: %s", e)
        return {}


__all__ = ["display_session_report", "format_time"]


def handle_keyboard_interrupt(session_logger, console: Optional[Console] = None) -> dict:
    """Handle KeyboardInterrupt timing and render the session report.

    Stops active timing, starts idle timing and prints the session summary.
    Returns the computed metrics dict. Best-effort; will not raise.
    """
    try:
        from cai.util import start_idle_timer, stop_active_timer

        try:
            stop_active_timer()
        except Exception:
            pass
        try:
            start_idle_timer()
        except Exception:
            pass

        return display_session_report(session_logger, console=console)
    except Exception:
        logging.getLogger(__name__).debug("Error in handle_keyboard_interrupt", exc_info=True)
        return {}


__all__.append("handle_keyboard_interrupt")


def finalize_session(session_logger, start_time: float, idle_time: float) -> dict:
    """
    Finalize the current session: compute totals, render the session summary,
    and perform end-of-session bookkeeping (log session end, end global
    usage tracking, create last-log symlink, and prevent duplicate cost
    display).

    Returns the computed metrics dictionary. Best-effort; will not raise.
    """
    logger = logging.getLogger(__name__)
    try:
        import os
        import sys
        import time

        from cai import is_pentestperf_available
        from cai.sdk.agents.global_usage_tracker import GLOBAL_USAGE_TRACKER
        from cai.util import COST_TRACKER, get_active_time_seconds, get_idle_time_seconds
        from cai.util.orchestration import create_last_log_symlink

        console = Console()

        # Use the precise measurements from timer helpers where available
        try:
            active_time_seconds = get_active_time_seconds()
        except Exception:
            active_time_seconds = 0.0
        try:
            idle_time_seconds = get_idle_time_seconds()
        except Exception:
            idle_time_seconds = idle_time or 0.0

        # Compute total wall-clock time from provided start_time
        total_seconds = max(0.0, time.time() - (start_time or time.time()))

        # Format values for display
        active_time_formatted = format_time(active_time_seconds)
        idle_time_formatted = format_time(idle_time_seconds)
        session_time_formatted = format_time(total_seconds)

        metrics = {
            "session_time": session_time_formatted,
            "active_time": active_time_formatted,
            "idle_time": idle_time_formatted,
            "llm_time": format_time(active_time_seconds),
            "llm_percentage": round((active_time_seconds / total_seconds) * 100, 1)
            if total_seconds > 0
            else 0.0,
            "session_cost": f"${COST_TRACKER.session_total_cost:.6f}",
        }

        logging_path = (
            session_logger.filename
            if (session_logger and hasattr(session_logger, "filename"))
            else None
        )

        # Render using Rich to match previous CLI presentation
        try:
            from rich.box import ROUNDED
            from rich.console import Group
            from rich.panel import Panel
            from rich.text import Text

            content = []
            content.append(f"Session Time: {metrics['session_time']}")
            content.append(f"Active Time: {metrics['active_time']} ({metrics['llm_percentage']}%)")
            content.append(f"Idle Time: {metrics['idle_time']}")
            content.append(f"Total Session Cost: {metrics['session_cost']}")
            if logging_path:
                content.append(f"Log available at: {logging_path}")

            text_content = []
            for line in content:
                if "Total Session Cost" in line:
                    cost_text = Text()
                    parts = line.split(":", 1)
                    cost_text.append(parts[0] + ":", style="bold")
                    cost_text.append(parts[1], style="bold green")
                    text_content.append(cost_text)
                else:
                    text_content.append(Text(line))

            time_panel = Panel(
                Group(*text_content),
                border_style="blue",
                box=ROUNDED,
                padding=(0, 1),
                title="[bold]Session Summary[/bold]",
                title_align="left",
            )
            console.print(time_panel, end="")
        except Exception:
            for line in [
                f"Session Time: {metrics['session_time']}",
                f"Active Time: {metrics['active_time']} ({metrics['llm_percentage']}%)",
                f"Idle Time: {metrics['idle_time']}",
                f"Total Session Cost: {metrics['session_cost']}",
            ]:
                print(line)

        # Log session end if available
        try:
            if session_logger and hasattr(session_logger, "log_session_end"):
                session_logger.log_session_end()
        except Exception:
            logger.debug("Failed to call session_logger.log_session_end", exc_info=True)

        # End the global usage tracker session
        try:
            GLOBAL_USAGE_TRACKER.end_session(final_cost=COST_TRACKER.session_total_cost)
        except Exception:
            logger.debug("Failed to end GLOBAL_USAGE_TRACKER session", exc_info=True)

        # Create symlink to the last log file
        try:
            if session_logger and hasattr(session_logger, "filename"):
                create_last_log_symlink(session_logger.filename)
        except Exception:
            logger.debug("Failed to create last log symlink", exc_info=True)

        # Prevent duplicate cost display from the COST_TRACKER exit handler
        try:
            os.environ["CAI_COST_DISPLAYED"] = "true"
        except Exception:
            pass

        # Attempt to stop any running CTF instance if present on the CLI module
        try:
            cli_mod = sys.modules.get("cai.cli")
            if cli_mod and hasattr(cli_mod, "ctf_global"):
                ctf = cli_mod.ctf_global
                if ctf and is_pentestperf_available() and os.getenv("CTF_NAME", None):
                    try:
                        ctf.stop_ctf()
                    except Exception:
                        pass
        except Exception:
            pass

        return metrics
    except Exception:
        logger.debug("Error generating finalize_session report", exc_info=True)
        return {}


__all__.append("finalize_session")
