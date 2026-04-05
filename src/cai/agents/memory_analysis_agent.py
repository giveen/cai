"""Memory Analysis and Manipulation Agent"""
import os
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(*args, **kwargs):
        return False

try:
    from openai import OpenAI as AsyncOpenAI
except Exception:
    AsyncOpenAI = None
    
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.util import load_prompt_template
from cai.tools.command_and_control.sshpass import (  # pylint: disable=import-error # noqa: E501
    run_ssh_command_with_credentials,
)

from cai.tools.reconnaissance.generic_linux_command import (  # pylint: disable=import-error # noqa: E501
    generic_linux_command,
)

from cai.tools.web.search_web import (  # pylint: disable=import-error # noqa: E501
    make_web_search_with_explanation,
)

from cai.tools.reconnaissance.exec_code import (  # pylint: disable=import-error # noqa: E501
    execute_code,
)

load_dotenv()
# Prompts
memory_analysis_agent_system_prompt = load_prompt_template("prompts/memory_analysis_agent.md")

# Define functions list
functions = [
    generic_linux_command,
    run_ssh_command_with_credentials,
    execute_code,
]

# Add make_web_search_with_explanation function if PERPLEXITY_API_KEY environment variable is set
if os.getenv('PERPLEXITY_API_KEY'):
    functions.append(make_web_search_with_explanation)
    
# Create the agent
_openai_client = None
if AsyncOpenAI is not None:
    try:
        _openai_client = AsyncOpenAI()
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

memory_analysis_agent = Agent(
    name="Memory Analysis Specialist",
    instructions=memory_analysis_agent_system_prompt,
    description="""Agent for runtime memory analysis and manipulation.
                   Specializes in process memory examination, monitoring, and modification
                   for security assessment, vulnerability discovery, and runtime behavior analysis.""",
    tools=functions,
    model=_model_inst,
)
