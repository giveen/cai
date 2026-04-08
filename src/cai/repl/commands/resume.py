"""Resume command for CAI REPL.

Provides a small `/resume last` command to resume the most recently interrupted
stream for the active agent. This is a lightweight helper that delegates to
`Runner.run_streamed` and prints streaming text deltas to the console.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from rich.console import Console

from cai.repl.commands.base import Command, register_command

console = Console()


class ResumeCommand(Command):
    def __init__(self):
        super().__init__(
            name="/resume",
            description="Resume the last interrupted stream for the active agent",
            aliases=["/r"],
        )

    def handle(self, args: Optional[List[str]] = None) -> bool:
        if not args or args[0] == "last":
            return self.handle_resume_last()

        console.print("[yellow]Usage: /resume last[/yellow]")
        return False

    def handle_resume_last(self) -> bool:
        try:
            from cai.sdk.agents.run import Runner
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            agent = AGENT_MANAGER.get_active_agent()
            if agent is None:
                console.print("[red]No active agent to resume[/red]")
                return False

            model = getattr(agent, "model", None)
            if model is None or not getattr(model, "_last_stream_request", None):
                console.print("[yellow]No resumable stream found[/yellow]")
                return False

            req = model._last_stream_request
            partial = getattr(model, "_last_stream_partial", "") or ""

            continuation_text = (
                "Please continue the previous response. Previous partial output:\n\n" + partial
            )

            original_input = req.get("input")
            if isinstance(original_input, str):
                new_input = original_input + "\n\n" + continuation_text
            else:
                new_input = list(original_input) + [{"role": "user", "content": continuation_text}]

            async def _resume_and_consume():
                streamed = Runner.run_streamed(agent, new_input)
                async for ev in streamed.stream_events():
                    # Prefer raw response delta printing when available
                    try:
                        if hasattr(ev, "data"):
                            raw = ev.data
                            # Try to extract a text delta field
                            delta = getattr(raw, "delta", None) or (
                                raw.get("delta") if isinstance(raw, dict) else None
                            )
                            if delta:
                                # Print without newline so streaming looks natural
                                print(delta, end="", flush=True)
                            else:
                                # Fallback: pretty-print the event
                                console.print(str(raw))
                        else:
                            console.print(str(ev))
                    except Exception:
                        console.print(str(ev))

            try:
                asyncio.run(_resume_and_consume())
            except Exception as e:  # pylint: disable=broad-except
                console.print(f"[red]Error during resume: {e}[/red]")
                return False

            return True

        except Exception as e:  # pylint: disable=broad-except
            console.print(f"[red]Resume failed: {e}[/red]")
            return False


# Register the command
register_command(ResumeCommand())
