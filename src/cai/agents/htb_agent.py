"""Hack The Box Specialist Agent

This agent is modeled after the Red Team agent but tuned specifically
for Hack The Box (HTB) lab environments. It loads a dedicated system
prompt and includes tools useful for CTF-style reconnaissance and
exploitation while enforcing HTB-specific networking constraints and
operational guardrails.
"""
import os
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):  # noop when python-dotenv is not installed
        return False

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

from cai.tools.reconnaissance.generic_linux_command import (
    generic_linux_command,
)
from cai.tools.reconnaissance.exec_code import (
    execute_code,
)
from cai.tools.web.search_web import (
    make_web_search_with_explanation,
)

from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.agents.guardrails import get_security_guardrails

load_dotenv()

# Model selection
model_name = os.getenv("CAI_MODEL", "alias1")

# Determine API key
api_key = os.getenv("ALIAS_API_KEY", os.getenv("OPENAI_API_KEY", "sk-alias-1234567890"))

# Load HTB-specific system prompt
htb_agent_system_prompt = load_prompt_template("prompts/system_htb_agent.md")

# Define tools available to the agent
tools = [
    generic_linux_command,
    execute_code,
]

# Only add web-search tool if an API key is configured for the provider
if os.getenv("PERPLEXITY_API_KEY") or os.getenv("SEARCH_API_KEY"):
    tools.append(make_web_search_with_explanation)

# Apply security guardrails for input/output
input_guardrails, output_guardrails = get_security_guardrails()


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
            model=model_name,
            openai_client=_openai_client,
        )
    except Exception:
        _model_inst = None

htb_agent = Agent(
    name="Hack The Box Specialist",
    description=(
        "Hack The Box Specialist — expert in HTB platform, specializing in "
        "initial access, privilege escalation, and post-exploitation within HTB labs."
    ),
    instructions=create_system_prompt_renderer(htb_agent_system_prompt),
    tools=tools,
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=_model_inst,
)


def transfer_to_htb_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to HTB agent (accepts and ignores keyword args)."""
    return htb_agent
