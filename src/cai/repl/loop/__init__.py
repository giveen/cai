"""cai.repl.loop — modular components extracted from the CLI main loop.

Modules
-------
state          CliSessionState dataclass (all mutable loop-local variables)
session        initialize_session() — one-time startup: agents, logging, banner
agent_sync     sync/switch helpers for model and agent-type changes
input_handler  get_next_input() — idle-timer-aware user input acquisition
"""

from cai.repl.loop.state import CliSessionState

__all__ = ["CliSessionState"]
