"""Agent Lifecycle Manager

This module provides a modern, class-based, asyncio-friendly Agent Lifecycle Manager
that loads agent definitions from YAML/JSON, validates them with pydantic, supports
hot-swapping agents while preserving workspace and memory context, dynamic
capability injection at runtime, and a "Director" orchestrator mode for spawning
and managing sub-agents.

The implementation avoids reusing legacy repo classes and is self-contained.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:
    yaml = None

from pydantic import BaseModel, Field, ValidationError, field_validator


__all__ = ["AgentRegistry", "SecurityAgent", "AgentManager", "main"]


class AgentConfig(BaseModel):
    key: str
    name: Optional[str] = None
    persona: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def key_must_be_nonempty(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("agent key must be a non-empty string")
        return v


@dataclass
class RuntimeContext:
    workspace: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)


class SecurityAgent:
    """A lightweight agent instance with explicit state and async lifecycle."""

    def __init__(self, config: AgentConfig, context: Optional[RuntimeContext] = None):
        self.config = config
        self.context = context or RuntimeContext()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.state: Dict[str, Any] = {}

    @property
    def key(self) -> str:
        return self.config.key

    async def initialize(self) -> None:
        missing = [t for t in self.config.required_tools if not os.getenv(t)]
        if missing:
            raise RuntimeError(f"Missing required tool(s) or env keys: {', '.join(missing)}")
        self.state["persona"] = self.config.persona or ""
        self.state["model"] = self.config.model or os.getenv("DEFAULT_MODEL", "<unset>")

    async def run(self) -> None:
        self._running = True
        try:
            while self._running:
                await asyncio.sleep(0.05)
        finally:
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def spawn(self) -> None:
        """Schedule the agent's run loop on the current event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            if not self._task or self._task.done():
                self._task = loop.create_task(self.run())
        else:
            # Fallback for environments without a running loop (rare / testing)
            asyncio.run(self.run())

    def handoff_state(self) -> Dict[str, Any]:
        return dict(self.state)

    def accept_handoff(self, state: Dict[str, Any]) -> None:
        self.state.update(state)


class AgentRegistry:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._configs: Dict[str, AgentConfig] = {}

    def load(self, path: Optional[Path] = None) -> None:
        p = Path(path or self.path or "agents.json")
        if not p.exists():
            return
        text = p.read_text(encoding="utf8")
        data = None
        if p.suffix in (".yml", ".yaml") and yaml:
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)

        if not isinstance(data, list):
            raise RuntimeError("agent registry file must be a list of agent configs")

        for item in data:
            try:
                cfg = AgentConfig(**item)
            except ValidationError as exc:
                raise RuntimeError(f"Invalid agent config: {exc}")
            self._configs[cfg.key] = cfg

    def list(self) -> List[AgentConfig]:
        return list(self._configs.values())

    def get(self, key: str) -> Optional[AgentConfig]:
        return self._configs.get(key)


class AgentManager:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.active: Optional[SecurityAgent] = None
        self.instances: Dict[str, SecurityAgent] = {}

    def list_available(self) -> List[str]:
        return [c.key for c in self.registry.list()]

    async def load(self, key: str, inject_capabilities: Optional[List[str]] = None, context: Optional[RuntimeContext] = None) -> SecurityAgent:
        cfg = self.registry.get(key)
        if not cfg:
            raise KeyError(f"Unknown agent: {key}")
        injected = cfg.model_copy()
        if inject_capabilities:
            injected.capabilities = list({*injected.capabilities, *inject_capabilities})
        agent = SecurityAgent(injected, context=context)
        await agent.initialize()
        self.instances[agent.key] = agent
        if self.active:
            handoff = self.active.handoff_state()
            agent.accept_handoff(handoff)
            await self.active.stop()
        self.active = agent
        return agent

    def status(self) -> Dict[str, Any]:
        return {
            "active": self.active.key if self.active else None,
            "instances": list(self.instances.keys()),
        }

    async def spawn_subagent(self, parent_key: str, child_key: str, capabilities: Optional[List[str]] = None) -> SecurityAgent:
        parent = self.instances.get(parent_key)
        if not parent:
            raise KeyError(f"Parent agent not found: {parent_key}")
        ctx = RuntimeContext(workspace=dict(parent.context.workspace), memory=dict(parent.context.memory))
        child = await self.load(child_key, inject_capabilities=capabilities, context=ctx)
        child.spawn()
        return child


def _print_json(obj: Any) -> None:
    try:
        print(json.dumps(obj, indent=2))
    except Exception:
        print(obj)


async def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agent-manager")
    parser.add_argument("cmd", choices=["list_available", "load", "status", "spawn_subagent"]) 
    parser.add_argument("keys", nargs="*", help="Keys for load/spawn operations")
    parser.add_argument("--caps", help="Comma separated capabilities to inject", default="")
    parser.add_argument("--registry", help="Path to agent registry (json|yaml)", default="agents.json")
    args = parser.parse_args(argv)

    registry = AgentRegistry(Path(args.registry))
    try:
        registry.load()
    except Exception as exc:
        print(f"Failed to load registry: {exc}")

    manager = AgentManager(registry)

    caps = [c.strip() for c in args.caps.split(",") if c.strip()]

    if args.cmd == "list_available":
        _print_json(manager.list_available())
        return 0

    if args.cmd == "status":
        _print_json(manager.status())
        return 0

    if args.cmd == "load":
        if not args.keys:
            print("load requires an agent key")
            return 2
        key = args.keys[0]
        try:
            agent = await manager.load(key, inject_capabilities=caps)
        except Exception as exc:
            print(f"Failed to load agent: {exc}")
            return 3
        _print_json({"loaded": agent.key, "capabilities": agent.config.capabilities})
        return 0

    if args.cmd == "spawn_subagent":
        if not args.keys or len(args.keys) < 2:
            print("spawn_subagent requires parent_key and child_key")
            return 2
        parent_key, child_key = args.keys[0], args.keys[1]
        try:
            child = await manager.spawn_subagent(parent_key, child_key, capabilities=caps)
        except Exception as exc:
            print(f"Failed to spawn subagent: {exc}")
            return 4
        _print_json({"spawned": child.key, "capabilities": child.config.capabilities})
        return 0

    return 0


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

