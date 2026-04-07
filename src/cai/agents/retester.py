"""Retester Agent for vulnerability verification and triage"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501


load_dotenv()

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

# Load the triage agent system prompt
retester_system_prompt = load_prompt_template("prompts/system_triage_agent.md")

tools = list(ALL_TOOLS)

retester_agent = Agent(
    name="Retester Agent",
    instructions=create_system_prompt_renderer(retester_system_prompt),
    description="""Agent that specializes in vulnerability verification and 
                   triage. Expert in determining exploitability and 
                   eliminating false positives.""",
    tools=tools,
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(api_key=api_key),
    )
)




