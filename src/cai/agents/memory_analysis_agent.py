"""Memory Analysis and Manipulation Agent"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from openai import AsyncOpenAI
from cai.util import load_prompt_template  # Add this import
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501

load_dotenv()
# Prompts
memory_analysis_agent_system_prompt = load_prompt_template("prompts/memory_analysis_agent.md")

tools = list(ALL_TOOLS)

# Create the agent
memory_analysis_agent = Agent(
    name="Memory Analysis Specialist",
    instructions=memory_analysis_agent_system_prompt,
    description="""Agent for runtime memory analysis and manipulation.
                   Specializes in process memory examination, monitoring, and modification
                   for security assessment, vulnerability discovery, and runtime behavior analysis.""",
    tools=tools,
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    )
)
