"""
Centralised response renderers for the CLI/TUI.

Provides helpers to render final agent analysis responses consistently.
"""
from __future__ import annotations

from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich import box


def display_agent_analysis(content: str, agent_name: Optional[str] = None) -> None:
    """Render agent analysis/content as a Markdown-backed Panel.

    - Renders *content* as `rich.markdown.Markdown` so markdown is respected.
    - Places the Markdown inside a `rich.panel.Panel` with a rounded box and
      a bold cyan border. The panel subtitle is set to `agent_name`.

    This function prints directly to the Console.
    """
    if content is None:
        return

    text = str(content)
    if not text.strip():
        return

    try:
        md = Markdown(text)
        c = Console()
        subtitle = str(agent_name) if agent_name is not None else ""
        c.print(
            Panel(md, box=box.ROUNDED, border_style="bold cyan", subtitle=subtitle)
        )
    except Exception:
        try:
            # Best-effort fallback
            Console().print(text)
        except Exception:
            pass
