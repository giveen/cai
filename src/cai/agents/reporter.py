"""Reporter Agent - Creates professional security assessment reports"""
import os
from dotenv import load_dotenv
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from openai import AsyncOpenAI
from cai.util import load_prompt_template  # Add this import

from cai.tools.reconnaissance.generic_linux_command import (  # pylint: disable=import-error # noqa: E501
    generic_linux_command
)

from cai.tools.reconnaissance.exec_code import (  # pylint: disable=import-error # noqa: E501
    execute_code
)

load_dotenv()
import os
try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

reporting_agent_system_prompt = load_prompt_template("prompts/system_reporting_agent.md")

# Define functions list
functions = [
    generic_linux_command,
    execute_code,
]


# Create an instance of the reporting agent
reporting_agent = Agent(
    name="reporting agent",
    instructions=reporting_agent_system_prompt,
    description="""Agent that generates reports in html.""",
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
            model=os.getenv('CAI_MODEL', 'alias1'),
            openai_client=_openai_client,
        )
    except Exception:
        _model_inst = None

    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    )
    model=_model_inst,
