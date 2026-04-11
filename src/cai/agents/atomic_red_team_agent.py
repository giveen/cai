"""Atomic Red Team Logger Tester Agent (SAFE)

This agent is explicitly SAFE: it does NOT execute any offensive
techniques. Its only purpose is to help generate synthetic, non-actionable
log events inspired by Atomic Red Team technique observables so that
logging, alerting, and telemetry pipelines can be validated.

Behavioral guarantees:
- Never execute commands, network scans, or remote code.
- Never provide step-by-step instructions for harmful actions.
- Produce only synthetic placeholders for IPs, hostnames, filenames,
  and process names.

Usage (human/operator): ask the agent to "generate synthetic logs for
techniques ['T1003','T1059'] count=5" and it will return a JSON array
of safe log events suitable for ingestion tests.

References: https://github.com/redcanaryco/atomic-red-team (metadata
only; no actions performed)
"""

from __future__ import annotations

import os
import random
import string
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

try:
    from dotenv import load_dotenv
except Exception:

    def load_dotenv(*args, **kwargs):  # noop when python-dotenv missing
        return False


try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

from cai.agents.guardrails import get_security_guardrails
from cai.sdk.agents import Agent, OpenAIChatCompletionsModel
from cai.tools.all_tools import ALL_TOOLS  # noqa: E501
from cai.util import create_system_prompt_renderer

load_dotenv()

# Model selection
model_name = os.getenv("CAI_MODEL", "alias1")
api_key = os.getenv("OPENAI_API_KEY", "sk-placeholder-key-for-local-models")

# High-level instructions: strictly generate safe, synthetic logs only.
instructions = """
You are the Atomic Red Team Logger Tester. Your purpose is to generate
synthetic, non-actionable log events and observables inspired by the
Atomic Red Team technique catalog. IMPORTANT: Do NOT provide any
instructions, commands, or step-by-step guidance to perform offensive
actions. Do NOT attempt to run or fetch remote code. Always return
structured JSON (array of objects) where each object contains:

- `timestamp` (ISO8601 UTC)
- `technique_id` (e.g., "T1003")
- `technique_name` (short label)
- `host` (synthetic hostname)
- `user` (synthetic username)
- `process` (process name)
- `event_type` (one of: auth_fail, process_start, file_access, network_connection, registry_modify, dns_query)
- `message` (short, non-actionable description)
- `metadata` (dict with harmless placeholder values)

When asked for multiple events, vary timestamps and choose plausible
event_types for the technique but keep all data synthetic and harmless.
"""

# Guardrails
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


def _rand_host() -> str:
    return f"host-{random.randint(1,9999)}"


def _rand_user() -> str:
    return random.choice(["alice", "bob", "svc-agent", "jdoe"])


def _rand_pid() -> int:
    return random.randint(100, 65535)


def _rand_process_for_technique(technique_id: str) -> str:
    # Map a few common technique observables to safe, generic process names
    mapping = {
        "T1003": ["lsass.exe", "lsass_fake.exe"],
        "T1059": ["powershell.exe", "pwsh.exe"],
        "T1071": ["curl.exe", "python.exe"],
        "T1047": ["wmic.exe"],
        "T1016": ["tcpdump.exe", "netsh.exe"],
    }
    opts = mapping.get(technique_id.upper(), ["unknown.exe"])
    return random.choice(opts)


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def generate_synthetic_logs(technique_ids: List[str], count: int = 3) -> List[Dict[str, Any]]:
    """Create a list of synthetic, SAFE log event dicts for testing.

    Args:
        technique_ids: list of technique IDs (strings) to base events on.
        count: number of events per technique_id.

    Returns:
        List of dictionaries with harmless telemetry fields.
    """
    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []

    for tid in technique_ids:
        tid_clean = tid.strip().upper()
        tech_label = f"Technique {tid_clean}"
        for i in range(count):
            ts = now - timedelta(seconds=random.randint(0, 3600))
            event_type = random.choice([
                "auth_fail",
                "process_start",
                "file_access",
                "network_connection",
                "dns_query",
            ])

            proc = _rand_process_for_technique(tid_clean)
            host = _rand_host()
            user = _rand_user()
            pid = _rand_pid()

            message = f"Synthetic {event_type} event for {tid_clean} on {host} by {user}"

            metadata = {
                "pid": pid,
                "exe": proc,
                "src_ip": f"10.0.{random.randint(0,255)}.{random.randint(1,254)}",
                "dst_ip": f"192.0.2.{random.randint(1,254)}",
                "file_path": f"C:\\Windows\\Temp\\{tid_clean}_{random.randint(1,999)}.tmp",
            }

            evt = {
                "timestamp": _format_iso(ts),
                "technique_id": tid_clean,
                "technique_name": tech_label,
                "host": host,
                "user": user,
                "process": proc,
                "event_type": event_type,
                "message": message,
                "metadata": metadata,
            }
            out.append(evt)

    return out


atomic_red_team_agent = Agent(
    name="Atomic Red Team Logger Tester",
    description=(
        "Safe agent to generate synthetic telemetry/log events inspired by "
        "the Atomic Red Team catalog for logging/alert pipeline validation."
    ),
    instructions=create_system_prompt_renderer(instructions),
    tools=list(ALL_TOOLS),
    input_guardrails=input_guardrails,
    output_guardrails=output_guardrails,
    model=_model_inst,
)


def transfer_to_atomic_red_team_agent(**kwargs: Any):  # pylint: disable=W0613
    """Return the singleton atomic_red_team_agent instance.

    Note: this agent will not execute techniques. Use it only to generate
    synthetic logs for testing (see generate_synthetic_logs).
    """
    return atomic_red_team_agent
