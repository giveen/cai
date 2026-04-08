"""Sub-GHz Radio Frequency Analysis Agent using HackRF One"""
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

load_dotenv()
# Prompts
subghz_agent_system_prompt = load_prompt_template("prompts/subghz_agent.md")

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

subghz_sdr_agent = Agent(
    name="Sub-GHz SDR Specialist",
    instructions=subghz_agent_system_prompt,
    description="""Agent for sub-GHz radio frequency analysis using HackRF One.
                   Specializes in signal capture, replay, and protocol analysis for IoT, 
                   automotive, industrial, and wireless security applications.""",
    tools=tools,
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    )
)
