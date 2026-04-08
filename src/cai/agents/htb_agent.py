"""Hack The Box (HTB) Agent"""
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from cai.agents.guardrails import get_security_guardrails
from cai.sdk.agents import Agent, ModelSettings, OpenAIChatCompletionsModel
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer, load_prompt_template

load_dotenv()
model_name = os.getenv("CAI_MODEL", "alias1")
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

htb_agent_system_prompt = load_prompt_template("prompts/system_htb_agent.md")

tools = list(ALL_TOOLS)


input_guardrails, output_guardrails = get_security_guardrails()

htb_agent = Agent(
    name="HTB Agent",
    description="""Agent specialized for Hack The Box machines and challenges.
                   Expert in enumeration, exploitation, and privilege escalation
                   on both Linux and Windows HTB targets.""",
    instructions=create_system_prompt_renderer(htb_agent_system_prompt),
    model_settings=ModelSettings(temperature=0, tool_choice="required"),
    tools=tools,
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=AsyncOpenAI(api_key=api_key),
    ),
)


def transfer_to_htb_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to HTB agent."""
    return htb_agent
