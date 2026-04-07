"""Blue Team Base Agent
SSH_PASS
SSH_HOST
SSH_USER
"""
import os
from openai import AsyncOpenAI
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.util import load_prompt_template, create_system_prompt_renderer
from dotenv import load_dotenv
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501

# Prompts
blueteam_agent_system_prompt = load_prompt_template("prompts/system_blue_team_agent.md")
# Define tools list based on available API keys
tools = list(ALL_TOOLS)

load_dotenv()


blueteam_agent = Agent(
    name="Blue Team Agent",
    instructions=create_system_prompt_renderer(blueteam_agent_system_prompt),
    description="""Agent that specializes in system defense and security monitoring.
                   Expert in cybersecurity protection and incident response.""",
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    ),
    tools=tools,
)
