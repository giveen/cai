"""Reverse Engineering and Binary Analysis Agent"""

import os

try:
    from dotenv import load_dotenv
except Exception:

    def load_dotenv(*args, **kwargs):
        return False


try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import load_prompt_template  # Add this import

# Prompts
reverse_engineering_agent_system_prompt = load_prompt_template(
    "prompts/reverse_engineering_agent.md"
)

tools = list(ALL_TOOLS)

# Create the agent
_openai_client = None
if AsyncOpenAI is not None:
    try:
        _openai_client = AsyncOpenAI()
    except Exception:
        _openai_client = None

_model_inst = None
if _openai_client is not None:
    try:
        _model_inst = OpenAIChatCompletionsModel(
            model=os.getenv("CAI_MODEL", "alias1"),
            openai_client=_openai_client,
        )
    except Exception:
        _model_inst = None

reverse_engineering_agent = Agent(
    name="Reverse Engineering Specialist",
    instructions=reverse_engineering_agent_system_prompt,
    description="""Agent for binary analysis and reverse engineering.
                   Specializes in firmware analysis, binary disassembly,
                   decompilation, and vulnerability discovery using tools
                   like Ghidra, Binwalk, and various binary analysis utilities.""",
    tools=tools,
    model=OpenAIChatCompletionsModel(
        model=os.getenv("CAI_MODEL", "alias1"),
        openai_client=AsyncOpenAI(),
    ),
)
