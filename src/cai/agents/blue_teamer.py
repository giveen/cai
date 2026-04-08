"""Blue Team Base Agent
SSH_PASS
SSH_HOST
SSH_USER
"""
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

from dotenv import load_dotenv

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer, load_prompt_template

# Prompts
blueteam_agent_system_prompt = load_prompt_template("prompts/system_blue_team_agent.md")
# Define tools list based on available API keys
tools = list(ALL_TOOLS)

load_dotenv()


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

blueteam_agent = Agent(
    name="Blue Team Agent",
    instructions=create_system_prompt_renderer(blueteam_agent_system_prompt),
    description="""Agent that specializes in system defense and security monitoring.
                   Expert in cybersecurity protection and incident response.""",
    model=_model_inst,
    tools=tools,
)
