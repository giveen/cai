"""Red Team Base Agent"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501

from cai.agents.guardrails import get_security_guardrails

load_dotenv()

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))
# Prompts
bug_bounter_system_prompt = load_prompt_template("prompts/system_bug_bounter.md")
tools = list(ALL_TOOLS)

# Get security guardrails
input_guardrails, output_guardrails = get_security_guardrails()

bug_bounter_agent = Agent(
    name="Bug Bounter",
    instructions=create_system_prompt_renderer(bug_bounter_system_prompt),
    description="""Agent that specializes in bug bounty hunting and vulnerability discovery.
                   Expert in web security, API testing, and responsible disclosure.""",
    tools=tools,
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(api_key=api_key),
    )
   
)