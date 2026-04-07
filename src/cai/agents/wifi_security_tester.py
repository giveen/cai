"""Wi-Fi Security Testing Agent"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from openai import AsyncOpenAI
from cai.util import load_prompt_template  # Add this import
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501

load_dotenv()
# Prompts
wifi_security_agent_system_prompt = load_prompt_template("prompts/wifi_security_agent.md")

tools = list(ALL_TOOLS)

# Create the agent
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
