"""
Generic agent factory module for creating agent instances dynamically.
"""

import importlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Optional

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel


# Cache workspace-level instruction text so we don't re-read files repeatedly
_WORKSPACE_INSTRUCTIONS_CACHE: Optional[str] = None


def _load_workspace_instructions() -> Optional[str]:
    """Load any workspace-level instruction templates ("*.instructions.md").

    Searches the repository root (heuristic) for files named
    "*.instructions.md" and concatenates them. Returns None when nothing
    relevant is found.
    """
    global _WORKSPACE_INSTRUCTIONS_CACHE
    if _WORKSPACE_INSTRUCTIONS_CACHE is not None:
        return _WORKSPACE_INSTRUCTIONS_CACHE

    try:
        repo_root = Path(__file__).resolve()
        # Walk up to find repository root (stop at a few common markers)
        for _ in range(6):
            if (repo_root / "pyproject.toml").exists() or (repo_root / ".git").exists() or (
                repo_root / "README.md"
            ).exists():
                break
            if repo_root.parent == repo_root:
                break
            repo_root = repo_root.parent

        files = sorted(repo_root.rglob("*.instructions.md"))
        contents: list[str] = []
        for f in files:
            try:
                txt = f.read_text(encoding="utf-8")
                contents.append(f"\n\n# Source: {f.relative_to(repo_root)}\n\n{txt}")
            except Exception:
                continue

        if contents:
            combined = "\n\n".join(contents)
            _WORKSPACE_INSTRUCTIONS_CACHE = combined
            return combined
    except Exception:
        pass

    _WORKSPACE_INSTRUCTIONS_CACHE = None
    return None


def create_generic_agent_factory(
    agent_module_path: str, agent_var_name: str
) -> Callable[[str | None, str | None], Agent]:
    """
    Create a generic factory function for any agent.

    Args:
        agent_module_path: Full module path to the agent (e.g., 'cai.agents.one_tool')
        agent_var_name: Name of the agent variable in the module (e.g., 'one_tool_agent')

    Returns:
        A factory function that creates new instances of the agent
    """

    def factory(
        model_override: str | None = None,
        custom_name: str | None = None,
        agent_id: str | None = None,
    ):
        # Import the module
        module = importlib.import_module(agent_module_path)

        # Get the original agent instance
        original_agent = getattr(module, agent_var_name)

        # Get model configuration - check multiple sources
        model_name = model_override  # First priority: explicit override

        if not model_name:
            # Second priority: agent-specific environment variable
            agent_key = agent_var_name.upper()
            model_name = os.getenv(f"CAI_{agent_key}_MODEL")

        if not model_name:
            # Third priority: global CAI_MODEL
            model_name = os.environ.get("CAI_MODEL", "alias1")

        api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder-key-for-local-models")

        # Create a new model instance with the original agent name
        # Custom name is only for display purposes, not for the model
        new_model = None
        if AsyncOpenAI is not None:
            try:
                new_model = OpenAIChatCompletionsModel(
                    model=model_name,
                    openai_client=AsyncOpenAI(api_key=api_key),
                    agent_name=original_agent.name,  # Always use original agent name
                    agent_id=agent_id,
                    agent_type=agent_var_name,  # Pass the agent type for registry
                )
            except Exception:
                new_model = None

        # Mark as parallel agent if running in parallel mode
        parallel_count = int(os.getenv("CAI_PARALLEL", "1"))
        if parallel_count > 1 and agent_id and agent_id.startswith("P"):
            new_model._is_parallel_agent = True

        # Clone the agent with the new model
        cloned_agent = original_agent.clone(model=new_model)

        # Update agent name if custom name was provided
        if custom_name:
            cloned_agent.name = custom_name

        # Append any workspace-level instruction templates to the agent's
        # system prompt. This uses the lightweight helpers in cai.util and is
        # best-effort (fail silently on any errors) so agent creation remains
        # robust even if templates are missing or unreadable.
        try:
            additional = _load_workspace_instructions()
            if additional:
                # Import here to avoid potential import-time cycles
                from cai.util import append_instructions, create_system_prompt_renderer

                if not getattr(cloned_agent, "instructions", None):
                    cloned_agent.instructions = create_system_prompt_renderer(additional)
                else:
                    append_instructions(cloned_agent, additional)
        except Exception:
            # Best-effort: if anything goes wrong we don't want to break agent creation
            pass

        # Check if this agent has any MCP tools configured
        try:
            from cai.repl.commands.mcp import get_mcp_tools_for_agent

            # Get MCP tools for this agent and add them
            mcp_tools = get_mcp_tools_for_agent(agent_var_name)
            if mcp_tools:
                # Ensure the agent has tools list
                if not hasattr(cloned_agent, "tools"):
                    cloned_agent.tools = []

                # Remove any existing tools with the same names to avoid duplicates
                existing_tool_names = {t.name for t in mcp_tools}
                cloned_agent.tools = [
                    t for t in cloned_agent.tools if t.name not in existing_tool_names
                ]

                # Add the MCP tools
                cloned_agent.tools.extend(mcp_tools)

        except ImportError:
            # MCP command not available, skip
            pass

        return cloned_agent

    return factory


def discover_agent_factories() -> dict[str, Callable[[], Agent]]:
    """
    Dynamically discover all agents and create factories for them.

    Returns:
        Dictionary mapping agent names to factory functions
    """
    import pkgutil

    import cai.agents

    agent_factories = {}

    # Scan the agents module for all agent definitions
    for importer, modname, ispkg in pkgutil.iter_modules(
        cai.agents.__path__, cai.agents.__name__ + "."
    ):
        if ispkg:
            continue  # Skip packages like 'patterns' and 'meta'

        try:
            # Import the module
            module = importlib.import_module(modname)

            # Look for Agent instances
            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue

                attr = getattr(module, attr_name)
                if isinstance(attr, Agent):
                    # Create a factory for this agent
                    agent_name = attr_name.lower()
                    agent_factories[agent_name] = create_generic_agent_factory(modname, attr_name)

        except Exception:
            # Skip modules that fail to import
            continue

    # Also scan patterns subdirectory
    patterns_path = os.path.join(os.path.dirname(cai.agents.__file__), "patterns")
    if os.path.exists(patterns_path):
        for importer, modname, ispkg in pkgutil.iter_modules(
            [patterns_path], cai.agents.__name__ + ".patterns."
        ):
            if ispkg:
                continue

            try:
                module = importlib.import_module(modname)

                for attr_name in dir(module):
                    if attr_name.startswith("_"):
                        continue

                    attr = getattr(module, attr_name)
                    if isinstance(attr, Agent):
                        agent_name = attr_name.lower()
                        agent_factories[agent_name] = create_generic_agent_factory(
                            modname, attr_name
                        )

            except Exception:
                continue

    return agent_factories


# Global registry of agent factories
AGENT_FACTORIES = None


def get_agent_factory(agent_name: str) -> Callable[[], Agent]:
    """
    Get a factory function for creating instances of the specified agent.

    Args:
        agent_name: Name of the agent

    Returns:
        Factory function that creates new agent instances

    Raises:
        ValueError: If agent not found
    """
    global AGENT_FACTORIES

    # Lazy initialization
    if AGENT_FACTORIES is None:
        AGENT_FACTORIES = discover_agent_factories()

    agent_name_lower = agent_name.lower()

    if agent_name_lower not in AGENT_FACTORIES:
        raise ValueError(
            f"Agent '{agent_name}' not found. Available agents: {list(AGENT_FACTORIES.keys())}"
        )

    return AGENT_FACTORIES[agent_name_lower]
