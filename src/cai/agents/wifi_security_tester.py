"""Wi-Fi Security Testing Agent"""
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

# Load .env if available
load_dotenv()
# Prompts
wifi_security_agent_system_prompt = load_prompt_template("prompts/wifi_security_agent.md")

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

wifi_security_agent = Agent(
    name="Wi-Fi Security Tester",
    instructions=wifi_security_agent_system_prompt,
    description="""Agent for Wi-Fi network security testing and penetration.
                   Specializes in wireless attacks, password recovery, and communication disruption.""",
    tools=tools,
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    )
)
