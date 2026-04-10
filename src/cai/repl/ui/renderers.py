"""
Centralised response renderers for the CLI/TUI.

Provides helpers to render final agent analysis responses consistently.
"""
from __future__ import annotations

import logging

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

logger = logging.getLogger(__name__)


def display_agent_analysis(
    content: str,
    agent_name: str | None = None,
    title: str = "Analysis",
) -> None:
    """Render agent analysis/content as a Markdown-backed Panel.

    - Renders *content* as `rich.markdown.Markdown` so markdown is respected.
    - Places the Markdown inside a `rich.panel.Panel` with a rounded box,
      bold cyan border, the given *title* at the top, and *agent_name* as
      the subtitle at the bottom.

    This function prints directly to the Console.
    """
    if content is None:
        return

    text = str(content)
    if not text.strip():
        return

    try:
        md = Markdown(text)
        subtitle = f"[dim cyan]{agent_name}[/dim cyan]" if agent_name else ""
        logger.debug(
            "display_agent_analysis: rendering panel agent_name=%r title=%r len=%d",
            agent_name,
            title,
            len(text),
        )
        panel = Panel(
            md,
            box=box.ROUNDED,
            border_style="bold cyan",
            title=f"[bold cyan]{title}[/bold cyan]",
            subtitle=subtitle,
        )
        try:
            from cai.util import write_panel

            write_panel(panel)
        except Exception:
            Console().print(panel)
    except Exception as _exc:
        logger.debug(
            "display_agent_analysis: Panel render failed (%s), falling back to plain text", _exc
        )
        try:
            # Best-effort fallback
            Console().print(text)
        except Exception:
            pass


def display_agent_thought(
    content: str,
    agent_name: str | None = None,
    title: str = "Thought",
) -> None:
    """Render intermediate reasoning/thought content as a prominent Panel.

    This mirrors ``display_agent_analysis`` but uses a yellow highlight and is
    intended for intermediate "thought" or reasoning items.
    """
    if content is None:
        return

    text = str(content)
    if not text.strip():
        return

    try:
        md = Markdown(text)
        subtitle = f"[dim yellow]{agent_name}[/dim yellow]" if agent_name else ""
        logger.debug(
            "display_agent_thought: rendering panel agent_name=%r title=%r len=%d",
            agent_name,
            title,
            len(text),
        )
        panel = Panel(
            md,
            box=box.ROUNDED,
            border_style="bold yellow",
            title=f"[bold yellow]{title}[/bold yellow]",
            subtitle=subtitle,
            padding=(0, 1),
        )
        try:
            from cai.util import write_panel

            write_panel(panel)
        except Exception:
            Console().print(panel)
    except Exception as _exc:
        logger.debug(
            "display_agent_thought: Panel render failed (%s), falling back to plain text",
            _exc,
        )
        try:
            Console().print(text)
        except Exception:
            pass
