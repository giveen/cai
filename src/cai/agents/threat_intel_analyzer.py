"""
Threat Intel Analyzer Agent
"""

import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.agents.guardrails import get_security_guardrails

# Core tools
# For threat intel analysis, we might need web search and potentially others.
# Similar to web pentester agent, conditionally include web search when API key is available.
from cai.tools.web.search_web import make_web_search_with_explanation

load_dotenv()
model_name = os.getenv("CAI_MODEL", "alias1")

# Load prompt (expects placement under src/cai/prompts/)
threat_intel_system_prompt = load_prompt_template("prompts/threat_intel_system_prompt.md")

# Assemble tools with minimal, high-signal set
tools = [
    # Add core tools here if needed
]

# Conditionally add web search helper when available
if os.getenv("PERPLEXITY_API_KEY"):
    tools.append(make_web_search_with_explanation)

# Security guardrails to dampen prompt-injection from untrusted web content
input_guardrails, output_guardrails = get_security_guardrails()

# Instantiate agent
threat_intel_analyzer_agent = Agent(
    name="Threat Intel Analyzer",
    description=(
        "Agent specializing in gathering and analyzing information about threat actors."
    ),
    instructions=create_system_prompt_renderer(threat_intel_system_prompt),
    tools=tools,
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=OpenAIChatCompletionsModel(
        model=model_name,
        openai_client=AsyncOpenAI(),
    ),
)

def transfer_to_threat_intel_analyzer(**kwargs):  # pylint: disable=unused-argument
    """
    Optional handoff helper for swarm patterns.
    Accepts arbitrary kwargs for compatibility; returns the agent instance.
    """
    return threat_intel_analyzer_agent
