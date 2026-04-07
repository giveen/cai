"""DFIR Base Agent
Digital Forensics and Incident Response (DFIR) Agent module for conducting security investigations
and analyzing digital evidence. This agent specializes in:

- System and network forensics: Analyzing system artifacts, network traffic, and logs
- Malware analysis: Static and dynamic analysis of suspicious code and binaries
- Memory forensics: Examining RAM dumps for evidence of compromise
- Disk forensics: Recovering and analyzing data from storage devices
- Timeline reconstruction: Building chronological sequences of security events
- Evidence preservation: Maintaining chain of custody and forensic integrity
- Incident response: Coordinating investigation and remediation activities
- Threat hunting: Proactively searching for indicators of compromise
"""
import os
from openai import AsyncOpenAI
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel  # pylint: disable=import-error
from cai.util import load_prompt_template, create_system_prompt_renderer
from dotenv import load_dotenv
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501

# Prompts
dfir_agent_system_prompt = load_prompt_template("prompts/system_dfir_agent.md")
tools = list(ALL_TOOLS)


dfir_agent = Agent(
    name="DFIR Agent",
    instructions=create_system_prompt_renderer(dfir_agent_system_prompt),
    description="""Agent that specializes in Digital Forensics and Incident Response.
                   Expert in investigation and analysis of digital evidence.""",
    model=OpenAIChatCompletionsModel(
        model=os.getenv('CAI_MODEL', "alias1"),
        openai_client=AsyncOpenAI(),
    ),
    tools=tools,

)