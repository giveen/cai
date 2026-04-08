"""
CTF Agent with one tool
"""
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

from openai import AsyncOpenAI

from cai.agents.guardrails import get_security_guardrails
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer

# Get model from environment or use default
model_name = os.getenv('CAI_MODEL', "alias1")

# NOTE: This is needed when using LiteLLM Proxy Server
#
# # Create OpenAI client for the agent
# openai_client = AsyncOpenAI(
#     base_url = os.getenv('LITELLM_BASE_URL', 'http://localhost:4000'),
#     api_key=os.getenv('LITELLM_API_KEY', 'key')
# )

# # Check if we're using a Qwen model
# is_qwen = "qwen" in model_name.lower()

# For Qwen models, we need to skip system instructions as they're not supported
instructions = """You are a Cybersecurity expert Leader facing a CTF
                challenge.
                INSTRUCTIONS:
                1. Execute the generic_linux_command tool without any
                explanation.
                2. Never assume the flag format - it could be any string
                3. YOU MUST USE THE flag_discriminator to check the flag
                when you have it in DECODED 64-bit format

                ## generic_linux_command usage
                # 1. Regular command execution
                # 2. Shell Session Management: create and manage
                # interactive shell sessions for commands like netcat,
                # SSH, telnet.

                - To start a new session: Use `generic_linux_command` with
                  commands like `ssh`
                - To list active sessions:
                  `generic_linux_command("session", "list")`
                - To get output from a session:
                  `generic_linux_command("session", "output <session_id>")`
                - To send input to a session:
                  `generic_linux_command("<command>", "<args>",
                  session_id="<session_id>")`
                - To terminate a session:
                  `generic_linux_command("session", "kill <session_id>")`

                """

#Loaded in openaichatcompletion client
# Loaded in openaichatcompletion client
api_key = os.getenv('OPENAI_API_KEY', 'sk-placeholder-key-for-local-models')

# Get security guardrails for this high-risk agent
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

one_tool_agent = Agent(
    name="CTF agent",
    description="""Agent focused on conquering security challenges using generic linux commands
                   Expert in cybersecurity and exploitation.""",
    instructions=create_system_prompt_renderer(instructions),
    tools=list(ALL_TOOLS),
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=_model_inst,
)


def transfer_to_one_tool_agent(**kwargs):  # pylint: disable=W0613
    """Transfer to ctf agent.
    Accepts any keyword arguments but ignores them."""
    return one_tool_agent
