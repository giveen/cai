"""Memory Analysis and Manipulation Agent"""
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

from openai import AsyncOpenAI

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import load_prompt_template  # Add this import

load_dotenv()
# Prompts
memory_analysis_agent_system_prompt = load_prompt_template("prompts/memory_analysis_agent.md")

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
            model=os.getenv('CAI_MODEL', "alias1"),
            openai_client=_openai_client,
        )
    except Exception:
        _model_inst = None

memory_analysis_agent = Agent(
    name="Memory Analysis Specialist",
    instructions=memory_analysis_agent_system_prompt,
    description="""Agent for runtime memory analysis and manipulation.
                   Specializes in process memory examination, monitoring, and modification
                   for security assessment, vulnerability discovery, and runtime behavior analysis.""",
    tools=tools,
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    )
)
