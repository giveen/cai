"""Use Case Agent"""

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
from cai.tools.reconnaissance.generic_linux_command import null_tool
from cai.util import create_system_prompt_renderer, load_prompt_template

load_dotenv()
model_name = os.getenv("CAI_MODEL", "alias1")

# Load prompt
use_case_agent_system_prompt = load_prompt_template("prompts/system_use_cases.md")

# # Define tools list
# tools = [
#     generic_linux_command,
#     list_dir,
#     cat_file,
#     edit_file,
#     replace_in_file,
#     read_file,
#     append_to_file,
#     create_file,
#     pwd_command,
#     find_file,
#     execute_code,
# ]
tools = [null_tool]
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
            model=model_name,
            openai_client=_openai_client,
        )
    except Exception:
        _model_inst = None

use_case_agent = Agent(
    name="Use Case Agent",
    description="""Agent that creates high-quality cybersecurity case studies 
                   demonstrating how CAI tackles various security scenarios, 
                   CTF challenges, and cybersecurity exercises.""",
    instructions=create_system_prompt_renderer(use_case_agent_system_prompt),
    tools=tools,
    model=_model_inst,
)


# Transfer function
def transfer_to_use_case_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to use case agent.
    Accepts any keyword arguments but ignores them."""
    return use_case_agent
