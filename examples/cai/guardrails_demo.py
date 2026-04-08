#!/usr/bin/env python3
"""
Demo script to demonstrate prompt injection guardrail protection
"""

import asyncio

from cai.agents.one_tool import one_tool_agent
from cai.sdk.agents import InputGuardrailTripwireTriggered, Runner


async def demo_guardrails():
    """Run a short demo of the prompt injection guardrails"""
    print("=" * 60)
    print("PROMPT INJECTION GUARDRAIL DEMO")
    print("=" * 60)

    safe_input = "Please check what files are in the current directory"
    try:
        result = await Runner.run(one_tool_agent, safe_input)
        print(f"✅ Safe input accepted: {safe_input[:50]}...")
    except InputGuardrailTripwireTriggered:
        print(f"❌ Safe input wrongly blocked: {safe_input}")

    print("\nDEMO COMPLETE")


if __name__ == "__main__":
    asyncio.run(demo_guardrails())
