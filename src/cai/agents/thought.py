"""
First prototype of a reasoner agent

using reasoner as a tool call

support meta agent may better @cai.sdk.agents.meta.reasoner_support
"""
from cai.tools.misc.reasoning import think
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
try:
    from openai import OpenAI as AsyncOpenAI
except Exception:
    AsyncOpenAI = None
from cai.util import load_prompt_template, create_system_prompt_renderer
import os

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

thought_agent_system_prompt = load_prompt_template("prompts/system_thought_router.md")

# Thought Process Agent for analysis and planning
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

thought_agent = Agent(
    name="ThoughtAgent",
    model=_model_inst,
    description="""Agent focused on analyzing and planning the next steps
                   in a security assessment or CTF challenge.""",
    instructions=create_system_prompt_renderer(thought_agent_system_prompt),
    tools=[think],
)
