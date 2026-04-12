"""Workspace orchestration helpers.

Provides a centralized `set_workspace(name)` helper used by the REPL
workspace command. Responsibilities:

- Set the `CAI_WORKSPACE` env var
- Ensure the host workspace directory exists
- Initialise a fresh `state.json` when creating a new workspace
- Detect existing state files (`state.json`, `intelligence.json`,
  `intelligence.yaml`, `state.yaml`) and parse them
- Build a short 'Welcome Back SitRep' summarising Nmap findings,
  discovered credentials and the last successful step and inject it as
  a memory into the agent system prompt (best-effort).

This module avoids crashing when optional deps (PyYAML, agent manager)
are not available and always returns a structured result object so callers
can display confirmations to the user.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _shorten(s: str, limit: int = 240) -> str:
    if s is None:
        return ""
    ss = str(s)
    if len(ss) <= limit:
        return ss
    return ss[: limit - 3] + "..."


def _read_state_file(p: Path) -> dict[str, Any] | None:
    """Try to read JSON/YAML state files. Returns dict or None on failure."""
    if not p.exists():
        return None
    try:
        # Prefer JSON first
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        # Try YAML if installed
        try:
            import yaml  # type: ignore

            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {"entries": []}
        except Exception:
            return None


def _extract_sitrep_from_journal(journal: dict[str, Any]) -> dict[str, list[str] | str]:
    """Extract a compact SitRep from a journal-like dict.

    Heuristics are intentionally conservative to avoid false positives.
    """
    entries = journal.get("entries", []) or []

    hosts: list[str] = []
    creds: list[str] = []
    last_steps: list[str] = []

    for e in entries:
        cat = (e.get("category") or "").lower() if isinstance(e, dict) else ""
        fact = e.get("fact") if isinstance(e, dict) else None

        # Nmap / host findings
        if "nmap" in cat or "host" in cat or (isinstance(fact, str) and "nmap" in fact.lower()):
            hosts.append(_shorten(json.dumps(fact) if not isinstance(fact, str) else fact, 300))
            continue

        # Credentials heuristics
        if "credential" in cat or "creds" in cat or "password" in cat or "hash" in cat:
            creds.append(_shorten(json.dumps(fact) if not isinstance(fact, str) else fact, 300))
            continue

        # Exploit / step heuristics
        if "exploit" in cat or "exploit" in (str(fact).lower() if fact else "") or "step" in cat:
            # Prefer recently successful steps (look for success flag)
            if isinstance(fact, dict) and (fact.get("status") == "success" or fact.get("result") == "success"):
                last_steps.append(_shorten(json.dumps(fact), 400))
            else:
                last_steps.append(_shorten(json.dumps(fact) if not isinstance(fact, str) else fact, 400))

    # If we didn't find explicit exploit steps, consider the last non-empty entry
    if not last_steps and entries:
        for e in reversed(entries):
            fact = e.get("fact") if isinstance(e, dict) else None
            if fact:
                last_steps.append(_shorten(json.dumps(fact) if not isinstance(fact, str) else fact, 400))
                break

    sitrep: dict[str, list[str] | str] = {
        "known_hosts_services": hosts[:10],
        "found_credentials": creds[:20],
        "last_successful_step": last_steps[0] if last_steps else "(none recorded)",
    }
    return sitrep


def set_workspace(workspace_name: str) -> dict[str, Any]:
    """Set workspace env var, ensure dir, parse state and inject SitRep.

    Returns a result dict containing keys:
      - workspace_dir: str
      - created: bool (True if directory was created)
      - state_file: path or None
      - sitrep: text or None
      - injected: bool (True if memory applied)
      - error: optional error message
    """
    result: dict[str, Any] = {
        "workspace_dir": None,
        "created": False,
        "state_file": None,
        "sitrep": None,
        "injected": False,
        "error": None,
    }

    # Set env var so other helpers compute paths consistently
    os.environ["CAI_WORKSPACE"] = workspace_name

    # Compute workspace dir using common helper if available
    try:
        from cai.tools.common import _get_workspace_dir as _get_work_dir

        workspace_dir = _get_work_dir()
    except Exception:
        base = os.getenv("CAI_WORKSPACE_DIR") or os.getcwd()
        workspace_dir = str(Path(base).joinpath(workspace_name).resolve())

    result["workspace_dir"] = workspace_dir

    p = Path(workspace_dir)
    existed = p.exists()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        result["error"] = f"Failed to create workspace dir: {exc}"
        return result

    result["created"] = not existed

    # Look for candidate state files
    candidates = ["state.json", "intelligence.json", "intelligence.yaml", "state.yaml"]
    found = None
    for c in candidates:
        cp = p / c
        if cp.exists():
            found = cp
            break

    # If nothing found and we just created dir, initialize a fresh state.json
    if not found:
        try:
            init = {"meta": {"version": 1, "created_at": datetime.utcnow().isoformat() + "Z"}, "entries": []}
            (p / "state.json").write_text(json.dumps(init, ensure_ascii=False, indent=2), encoding="utf-8")
            result["state_file"] = str((p / "state.json").resolve())
        except Exception as exc:
            result["error"] = f"Failed to initialise state.json: {exc}"
            return result
        # No sitrep to inject for fresh workspace
        return result

    # Parse detected state file
    data = _read_state_file(found)
    result["state_file"] = str(found.resolve())

    if not data:
        # Could not parse file; bail out but still return path
        result["error"] = "Found state file but failed to parse it (unsupported format)"
        return result

    # Build SitRep
    sitrep_obj = _extract_sitrep_from_journal(data)

    # Render SitRep text
    parts = ["Welcome Back — Short SitRep", ""]
    parts.append("Known Hosts & Services:")
    if sitrep_obj["known_hosts_services"]:
        for h in sitrep_obj["known_hosts_services"]:
            parts.append(f"- {h}")
    else:
        parts.append("- (none recorded)")

    parts.append("")
    parts.append("Found Credentials:")
    if sitrep_obj["found_credentials"]:
        for c in sitrep_obj["found_credentials"]:
            parts.append(f"- {c}")
    else:
        parts.append("- (none recorded)")

    parts.append("")
    parts.append("Last Successful Step:")
    parts.append(str(sitrep_obj["last_successful_step"]))

    sitrep_text = "\n".join(parts)
    result["sitrep"] = sitrep_text

    # Best-effort injection into agent system prompt using memory subsystem
    try:
        from cai.repl.commands.memory import (
            COMPACTED_SUMMARIES,
            APPLIED_MEMORY_IDS,
            MEMORY_COMMAND_INSTANCE,
        )
        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

        # Determine target agent name (P1 preferred)
        agent_name = AGENT_MANAGER.get_agent_by_id("P1")
        if not agent_name:
            agent_name = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")

        # Apply as the first memory for that agent (overwrites previous temporary welcome)
        if agent_name not in COMPACTED_SUMMARIES:
            COMPACTED_SUMMARIES[agent_name] = []
            APPLIED_MEMORY_IDS[agent_name] = []

        # Use a deterministic ID so repeated sets don't proliferate memories
        mem_id = f"WB_{workspace_name}"
        COMPACTED_SUMMARIES[agent_name] = [sitrep_text]
        APPLIED_MEMORY_IDS[agent_name] = [mem_id]

        # Reload agent to ensure system prompt includes the memory
        try:
            MEMORY_COMMAND_INSTANCE._reload_agent_with_memory(agent_name)
            result["injected"] = True
        except Exception:
            # Non-fatal: memory will be applied on next reload/interaction
            result["injected"] = False
    except Exception:
        # If memory subsystem or agent manager unavailable, skip injection silently
        result["injected"] = False

    return result
