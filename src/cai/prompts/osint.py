"""OSINT Agent"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from cai.tools.reconnaissance.generic_linux_command import generic_linux_command
from cai.tools.web.search_web import make_web_search_with_explanation
from cai.tools.reconnaissance.spiderfoot import spiderfoot
from cai.tools.reconnaissance.exec_code import execute_code
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.agents.guardrails import get_security_guardrails

load_dotenv()
model_name = os.getenv("CAI_MODEL", "alias1")

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

# Prompts
osint_agent_system_prompt = load_prompt_template("prompts/osint.md")

# Define tools list based on available API keys
tools = [generic_linux_command, spiderfoot, execute_code]

# Add make_web_search_with_explanation if PERPLEXITY_API_KEY is set
if os.getenv("PERPLEXITY_API_KEY"):
    tools.append(make_web_search_with_explanation)

# Get security guardrails
input_guardrails, output_guardrails = get_security_guardrails()

osint_agent = Agent(
    name="OSINT Agent",
    description="Agent that specializes in Open Source Intelligence gathering. Expert in web research, reconnaissance, and information discovery.",
    instructions=create_system_prompt_renderer(osint_agent_system_prompt),
    tools=tools,
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=AsyncOpenAI(api_key=api_key),
    ),
)

def transfer_to_osint_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to OSINT agent. Accepts any keyword arguments but ignores them."""
    return osint_agent
