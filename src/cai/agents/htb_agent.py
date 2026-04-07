"""Hack The Box (HTB) Agent"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel, ModelSettings
from openai import AsyncOpenAI

from cai.tools.reconnaissance.generic_linux_command import (  # pylint: disable=import-error # noqa: E501
    generic_linux_command
)
from cai.tools.reconnaissance.exec_code import (  # pylint: disable=import-error # noqa: E501
    execute_code
)
from cai.tools.web.search_web import (  # pylint: disable=import-error # noqa: E501
    make_web_search_with_explanation,
)
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.agents.guardrails import get_security_guardrails

load_dotenv()
model_name = os.getenv("CAI_MODEL", "alias1")
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

htb_agent_system_prompt = load_prompt_template("prompts/system_htb_agent.md")

tools = [
    generic_linux_command,
    execute_code,
]

if os.getenv("PERPLEXITY_API_KEY"):
    tools.append(make_web_search_with_explanation)

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
