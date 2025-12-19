"""
CTF Agent with one tool
"""
import os
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from cai.tools.reconnaissance.generic_linux_command import generic_linux_command  # noqa
from openai import AsyncOpenAI
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.agents.guardrails import get_security_guardrails
import sys
import importlib.util
from pathlib import Path

# Get model from environment or use default
model_name = os.getenv('CAI_MODEL', "alias1")

# Loaded in openaichatcompletion client
api_key = os.getenv('OPENAI_API_KEY', 'sk-placeholder-key-for-local-models')

def discover_tools_from_directory(directory_path):
    """
    Discover and load all Python files in a directory,
    returning a list of tool functions.
    Assumes tools are defined as decorated functions or have specific markers.
    This example assumes they are decorated with @function_tool
    (you might need to adjust based on actual tool definition).
    """
    tools = []
    try:
        # Get all .py files in the directory
        py_files = Path(directory_path).glob("*.py")
        for file_path in py_files:
            if file_path.name == "__init__.py":
                continue  # Skip __init__.py

            spec = importlib.util.spec_from_file_location(f"dynamic_tool_{file_path.stem}", file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"dynamic_tool_{file_path.stem}"] = module
            spec.loader.exec_module(module)

            # Iterate through attributes of the loaded module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # Check if it's a function and potentially a tool (adjust condition as needed)
                # This is a simplified check; you might need more robust logic based on how tools are defined.
                if callable(attr) and hasattr(attr, '__tool_info__') or \
                   (hasattr(attr, 'function_tool_name') and attr.function_tool_name):  # Example marker
                    tools.append(attr)

    except Exception as e:
        print(f"Error discovering tools from {directory_path}: {e}")
    return tools

# Get security guardrails for this high-risk agent
input_guardrails, output_guardrails = get_security_guardrails()

# Load prompt from file instead of hardcoded string
one_tool_agent_system_prompt = load_prompt_template("prompts/one_tool.md")
instructions = create_system_prompt_renderer(one_tool_agent_system_prompt)

# Define the path to your tools directory (adjust if needed)
TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "tools")

# Discover all available tools
discovered_tools = discover_tools_from_directory(TOOLS_DIR)

# Combine with existing tool(s) like generic_linux_command
combined_tools = [generic_linux_command] + discovered_tools

one_tool_agent = Agent(
    name="CTF agent",
    description="""Agent focused on conquering security challenges using generic linux commands
                   Expert in cybersecurity and exploitation.""",
    instructions=instructions,
    tools=combined_tools, # Use the combined list here
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=AsyncOpenAI(api_key=api_key),
    )
)


def transfer_to_one_tool_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to ctf agent.
    Accepts any keyword arguments but ignores them."""
    return one_tool_agent
