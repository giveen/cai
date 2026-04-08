"""Retester Agent for vulnerability verification and triage"""
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

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer, load_prompt_template

load_dotenv()

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

# Load the triage agent system prompt
retester_system_prompt = load_prompt_template("prompts/system_triage_agent.md")

tools = list(ALL_TOOLS)

_openai_client = None
if AsyncOpenAI is not None:
    try:
        _openai_client = AsyncOpenAI(api_key=api_key)
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

retester_agent = Agent(
    name="Retester Agent",
    instructions=create_system_prompt_renderer(retester_system_prompt),
    description="""Agent that specializes in vulnerability verification and 
                   triage. Expert in determining exploitability and 
                   eliminating false positives.""",
    tools=tools,
    model=_model_inst,
)




