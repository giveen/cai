"""Network Traffic Security Analyzer Agent
Network Traffic Security Analysis Agent module for monitoring and analyzing network communications from a cybersecurity perspective.
This agent specializes in:

- Security-focused packet analysis: Identifying malicious patterns in network packets
- Protocol security analysis: Detecting protocol abuse and malicious exploitation
- Threat monitoring: Real-time detection of suspicious network traffic patterns
- Attack surface identification: Mapping potential network entry points for attackers
- Network anomaly detection: Identifying unusual patterns indicating potential security incidents
- Lateral movement detection: Spotting signs of attackers moving through the network
- Security event correlation: Connecting related security events across the network
- Malicious traffic identification: Detecting command and control traffic and data exfiltration
- Continuous traffic monitoring: Real-time analysis of ongoing network traffic captures

Objectives:
- Incident root cause analysis: Identifying the original cause of security incidents
- Threat actor analysis: Analyzing network patterns to identify and profile potential threat actors
- Vulnerability impact understanding: Assessing how vulnerabilities affect network security
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

from cai.sdk.agents import Agent, OpenAIChatCompletionsModel, handoff  # pylint: disable=import-error
from cai.util import load_prompt_template, create_system_prompt_renderer
from cai.tools.command_and_control.sshpass import (  # pylint: disable=import-error # noqa: E501
    run_ssh_command_with_credentials
)

from cai.tools.reconnaissance.generic_linux_command import (  # pylint: disable=import-error # noqa: E501
    generic_linux_command
)
from cai.tools.web.search_web import (  # pylint: disable=import-error # noqa: E501
    make_web_search_with_explanation
)

from cai.tools.reconnaissance.exec_code import (  # pylint: disable=import-error # noqa: E501
    execute_code
)


from cai.agents.dfir import dfir_agent

load_dotenv()



###
# Import remote traffic capture tools

from cai.tools.network.capture_traffic import (  # noqa: E402
    capture_remote_traffic,
    remote_capture_session_tool,
)


# Prompts
network_security_analyzer_prompt = load_prompt_template("prompts/system_network_analyzer.md")
# Define tools list based on available API keys
tools = [
    generic_linux_command,
    run_ssh_command_with_credentials,
    execute_code,
    capture_remote_traffic,
    remote_capture_session_tool,
]

if os.getenv('PERPLEXITY_API_KEY'):
    tools.append(make_web_search_with_explanation)

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

network_security_analyzer_agent = Agent(
    name="Network Security Analyzer",
    instructions=create_system_prompt_renderer(network_security_analyzer_prompt),
    description="""Agent that specializes in network security analysis.
                   Expert in monitoring, capturing, and analyzing network communications for security threats.""",
        model=_model_inst,
    tools=tools,
    handoffs=[ # Handoff to DFIR agent for further analysis
        handoff(
            agent=dfir_agent,
            tool_name_override="handoff_to_dfir_agent",
            tool_description_override="Call the DFIR agent for deeper forensic analysis of security incidents"
        )
    ]
)
