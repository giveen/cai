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

from dotenv import load_dotenv

from cai.agents.dfir import dfir_agent
from cai.sdk.agents import (  # pylint: disable=import-error
    Agent,
    OpenAIChatCompletionsModel,
    handoff,
)
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer, load_prompt_template

load_dotenv()


# Prompts
network_security_analyzer_prompt = load_prompt_template("prompts/system_network_analyzer.md")
tools = list(ALL_TOOLS)


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
