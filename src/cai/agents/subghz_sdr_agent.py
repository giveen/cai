"""Sub-GHz Radio Frequency Analysis Agent using HackRF One"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from openai import AsyncOpenAI
from cai.util import load_prompt_template  # Add this import
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501

load_dotenv()
# Prompts
subghz_agent_system_prompt = load_prompt_template("prompts/subghz_agent.md")

tools = list(ALL_TOOLS)

# Create the agent
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
