"""Red Team Base Agent"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from openai import AsyncOpenAI
# from cai.tools.command_and_control.sshpass import (  # pylint: disable=import-error # noqa: E501
#     run_ssh_command_with_credentials
# )

from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.agents.guardrails import get_security_guardrails

load_dotenv()
model_name = os.getenv("CAI_MODEL", "alias1")

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))
# Prompts
redteam_agent_system_prompt = load_prompt_template("prompts/system_red_team_agent.md")
tools = list(ALL_TOOLS)

# Get security guardrails
input_guardrails, output_guardrails = get_security_guardrails()

redteam_agent = Agent(
    name="Red Team Agent",
    description="""Agent that mimics a red teamer in a security assessment.
                   Expert in cybersecurity, recon, and exploitation.""",
    instructions=create_system_prompt_renderer(redteam_agent_system_prompt),
    tools=tools,
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=AsyncOpenAI(api_key=api_key),
    ),
)

# Transfer function
def transfer_to_redteam_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to red team agent.
    Accepts any keyword arguments but ignores them."""
    return redteam_agent