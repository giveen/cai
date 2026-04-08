"""Replay Attack and Counteroffensive Agent
Specialized agent module focused on network replay attacks, packet manipulation, 
and counteroffensive techniques for security testing and incident response.
This agent specializes in:
- Network packet capture and analysis
- Traffic replay attacks against various protocols
- Authentication sequence and session token replay
- Traffic manipulation and injection
- Man-in-the-middle attack simulation
- TCP session hijacking
- Protocol exploitation techniques
- Anti-replay defense testing
Objectives:
- Identify and exploit replay vulnerabilities
- Test protocol implementation security
- Simulate advanced persistent threats
- Evaluate defensive controls against replay attacks
"""


import os

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer, load_prompt_template

# Prompts
replay_attack_agent_prompt = load_prompt_template("prompts/system_replay_attack_agent.md")

tools = list(ALL_TOOLS)


# Create the agent instance
replay_attack_agent = Agent(
    name="Replay Attack Agent",
    instructions=create_system_prompt_renderer(replay_attack_agent_prompt),
    description="""Agent that specializes in network replay attacks and counteroffensive techniques.
                   Expert in packet manipulation, traffic replay, and protocol exploitation.""",
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    ),
    tools=tools,
)

