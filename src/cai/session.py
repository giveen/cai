"""SessionManager — Auto-Commit Fact Layer.

This module provides a small, best-effort SessionManager which inspects
tool outputs and automatically updates a lightweight `state.json` in the
current workspace. The state schema is intentionally small and focused on
three keys:

- `targets`: list[str] — discovered IPs/hosts
- `credentials`: dict[str,str] — username -> password mapping
- `milestones`: list[str] — short human-readable progress points

All writes are atomic and best-effort; analysis heuristics are conservative
to avoid noisy commits. This module is safe to call from background
workers and will never raise to callers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from cai.tools.workspace import _get_workspace_dir
except Exception:  # pragma: no cover - fallback
    def _get_workspace_dir() -> str:  # type: ignore
        return os.getcwd()

STATE_NAME = "state.json"


def _state_path(workspace_dir: Optional[str] = None) -> Path:
    base = Path(workspace_dir or _get_workspace_dir())
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base / STATE_NAME


def _read_state(workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    p = _state_path(workspace_dir)
    if not p.exists():
        return {"targets": [], "credentials": {}, "milestones": [], "current_objective": "", "next_steps": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        # Best-effort YAML support
        try:
            import yaml  # type: ignore

            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return {"targets": [], "credentials": {}, "milestones": [], "current_objective": "", "next_steps": []}

    return {
        "targets": list(dict.fromkeys(data.get("targets", []) or [])),
        "credentials": dict(data.get("credentials", {}) or {}),
        "milestones": list(data.get("milestones", []) or []),
        "current_objective": (data.get("current_objective") or "") if isinstance(data, dict) else "",
        "next_steps": list(data.get("next_steps", []) or []),
    }


def _write_state_atomic(state: Dict[str, Any], workspace_dir: Optional[str] = None) -> bool:
    p = _state_path(workspace_dir)
    d = p.parent
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    fd, tmp = tempfile.mkstemp(prefix=p.name, suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, ensure_ascii=False, indent=2))
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, str(p))
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _extract_from_parsed(parsed: Any) -> Dict[str, Any]:
    """Given a parsed JSON-like tool output, extract candidates."""
    found_targets: List[str] = []
    found_credentials: Dict[str, str] = {}
    found_milestones: List[str] = []

    try:
        if isinstance(parsed, dict):
            schema = parsed.get("schema")
            data = parsed.get("data")
            # nmap schema
            if schema == "nmap" and isinstance(data, dict):
                hosts = data.get("hosts", [])
                for h in hosts:
                    ip = None
                    try:
                        ip = h.get("ip")
                    except Exception:
                        pass
                    if not ip:
                        try:
                            ip = h.get("name")
                        except Exception:
                            pass
                    if ip:
                        found_targets.append(str(ip))
                if hosts:
                    found_milestones.append("nmap scan produced hosts")

            # hashcat schema
            if schema == "hashcat" and isinstance(data, dict):
                cracked = data.get("cracked", []) if isinstance(data, dict) else []
                for i, line in enumerate(cracked or []):
                    try:
                        if ":" in line:
                            user, pwd = line.split(":", 1)
                            user = user.strip()
                            pwd = pwd.strip()
                            if user:
                                found_credentials[user] = pwd
                            else:
                                found_credentials[f"cracked_{i}"] = pwd
                        else:
                            found_credentials[f"cracked_{i}"] = str(line)
                    except Exception:
                        continue
                if cracked:
                    found_milestones.append("hashcat recovered credentials")

            # sitrep schema (generic)
            if schema == "sitrep" and isinstance(data, dict):
                hosts = data.get("hosts", []) or []
                for h in hosts:
                    found_targets.append(str(h))
                creds = data.get("credentials", []) or []
                for c in creds:
                    # credential lines may be "user:pass" or freeform
                    if isinstance(c, str) and ":" in c:
                        user, pwd = c.split(":", 1)
                        found_credentials[user.strip()] = pwd.strip()
                if hosts or creds:
                    found_milestones.append("sitrep reported findings")

    except Exception:
        pass

    return {"targets": found_targets, "credentials": found_credentials, "milestones": found_milestones}


def analyze_tool_output(tool_name: Optional[str], output: Any) -> Dict[str, Any]:
    """Best-effort analysis of a tool return value.

    Returns a dict with keys `targets`, `credentials`, `milestones`.
    """
    try:
        # Normalize the primary textual content
        text = ""
        parsed_json = None
        if isinstance(output, dict):
            # many runners return {'output': <str>} or similar
            text = str(output.get("output") or output.get("result") or json.dumps(output))
        elif isinstance(output, str):
            text = output
        else:
            try:
                text = json.dumps(output)
            except Exception:
                text = str(output)

        # Try to parse JSON produced by `process_tool_output`
        try:
            parsed_json = json.loads(text)
        except Exception:
            parsed_json = None

        if parsed_json is not None:
            extracted = _extract_from_parsed(parsed_json)
        else:
            # Fallback heuristics on raw text
            ips = sorted(set(re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", text)))
            creds: Dict[str, str] = {}
            for m in re.findall(r"([A-Za-z0-9_@%+\.-]{1,64}):([^\s:,;]{1,200})", text):
                try:
                    user, pwd = m[0].strip(), m[1].strip()
                    creds[user] = pwd
                except Exception:
                    continue

            milestones: List[str] = []
            tl = (tool_name or "").lower()
            if re.search(r"\b(success|successful|succeeded|vulnerable|exploited)\b", text, flags=re.I):
                milestones.append(f"{tool_name or 'tool'} reported success or vulnerability")
            if re.search(r"key finding|key findings|important finding", text, flags=re.I):
                # capture a short snippet as milestone
                snippet = text.strip().splitlines()[0][:200]
                milestones.append(f"Key Finding: {snippet}")

            extracted = {"targets": ips, "credentials": creds, "milestones": milestones}

        # Conservative post-filtering: truncate long lists
        extracted["targets"] = extracted.get("targets", [])[:200]
        extracted["milestones"] = extracted.get("milestones", [])[:200]
        extracted["credentials"] = extracted.get("credentials", {})
        return extracted
    except Exception:
        return {"targets": [], "credentials": {}, "milestones": []}


def commit_changes(changes: Dict[str, Any], workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """Merge `changes` into state.json and write atomically.

    Returns a small summary dict describing what was added.
    """
    try:
        state = _read_state(workspace_dir)
        before_targets = set(state.get("targets", []) or [])
        before_creds = dict(state.get("credentials", {}) or {})
        before_milestones = list(state.get("milestones", []) or [])

        # Merge targets
        new_targets = list(dict.fromkeys(list(before_targets) + list(changes.get("targets", []) or [])))

        # Merge credentials (latest wins) but respect Anchored facts recorded
        # in the intelligence journal (confidence_score == 1.0). Anchored
        # credentials will not be overwritten unless explicitly cleared.
        creds = dict(before_creds)
        for k, v in (changes.get("credentials") or {}).items():
            try:
                if not (k and v):
                    continue
                try:
                    # Best-effort: consult the intelligence journal for anchored facts
                    from cai.orchestration import persistence as _persistence

                    anchored = _persistence.is_fact_anchored(
                        str(k),
                        category_candidates=["credential", "credentials", "hashcat", "sitrep"],
                        workspace_dir=workspace_dir,
                    )
                except Exception:
                    anchored = False

                if anchored:
                    # Skip overwriting an anchored credential
                    continue

                creds[str(k)] = str(v)
            except Exception:
                continue

        # Merge milestones (append with timestamp)
        milestones = list(before_milestones)
        for m in (changes.get("milestones") or []):
            try:
                ts = datetime.utcnow().isoformat() + "Z"
                milestones.append(f"{ts} - {m}")
            except Exception:
                continue

        # Preserve or update objective/next_steps if provided in changes
        current_objective = None
        if isinstance(changes, dict) and "current_objective" in changes:
            try:
                current_objective = str(changes.get("current_objective") or "")
            except Exception:
                current_objective = ""

        next_steps_val = None
        if isinstance(changes, dict) and "next_steps" in changes:
            try:
                next_steps_val = list(changes.get("next_steps") or [])
            except Exception:
                next_steps_val = []

        new_state = {
            "targets": new_targets,
            "credentials": creds,
            "milestones": milestones,
            "current_objective": current_objective if current_objective is not None else state.get("current_objective", ""),
            "next_steps": next_steps_val if next_steps_val is not None else list(state.get("next_steps", []) or []),
        }
        # Best-effort: include VCM state so the workspace keeps a snapshot
        try:
            from cai.memory.paging import VCM

            try:
                new_state["vcm"] = VCM.export_state()
            except Exception:
                pass
        except Exception:
            pass
        ok = _write_state_atomic(new_state, workspace_dir)
        return {
            "status": "ok" if ok else "error",
            "added_targets": [t for t in new_targets if t not in before_targets],
            "added_credentials": [k for k in creds.keys() if k not in before_creds],
            "added_milestones_count": max(0, len(milestones) - len(before_milestones)),
        }
    except Exception:
        return {"status": "error", "message": "commit failed"}


def commit_objective_state(
    current_objective: Optional[str], next_steps: Optional[List[str]], workspace_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Atomically write `current_objective` and `next_steps` into state.json.

    Preserves existing `targets`, `credentials`, and `milestones`.
    """
    try:
        state = _read_state(workspace_dir)
        state["current_objective"] = str(current_objective or "")
        state["next_steps"] = list(next_steps or [])
        ok = _write_state_atomic(state, workspace_dir)
        return {"status": "ok" if ok else "error", "current_objective": state["current_objective"], "next_steps_count": len(state.get("next_steps", []))}
    except Exception:
        return {"status": "error", "message": "commit_objective failed"}


def auto_commit_from_tool(tool_name: Optional[str], output: Any, workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """Convenience: analyze a tool return and commit any discoveries.

    This function is best-effort and will never raise to callers.
    """
    try:
        changes = analyze_tool_output(tool_name, output)
        # If nothing useful found, return quickly
        if not (changes.get("targets") or changes.get("credentials") or changes.get("milestones")):
            return {"status": "noop"}
        return commit_changes(changes, workspace_dir)
    except Exception:
        return {"status": "error", "message": "auto-commit failed"}


def export_vcm_state(workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """Export the current VCM state into the workspace `state.json` under the
    `vcm` key. Returns a small status dict."""
    try:
        try:
            from cai.memory.paging import VCM

            vstate = VCM.export_state()
        except Exception:
            return {"status": "noop", "message": "VCM unavailable"}

        state = _read_state(workspace_dir)
        state["vcm"] = vstate
        ok = _write_state_atomic(state, workspace_dir)
        return {"status": "ok" if ok else "error"}
    except Exception:
        return {"status": "error", "message": "export_vcm_state failed"}


def import_vcm_state(workspace_dir: Optional[str] = None) -> Dict[str, Any]:
    """Import the `vcm` key from workspace `state.json` into the runtime VCM.
    Best-effort and non-fatal.
    """
    try:
        try:
            from cai.memory.paging import VCM
        except Exception:
            return {"status": "noop", "message": "VCM unavailable"}

        state = _read_state(workspace_dir)
        vstate = state.get("vcm")
        if not vstate:
            return {"status": "noop", "message": "no_vcm_state"}

        VCM.import_state(vstate)
        return {"status": "ok"}
    except Exception:
        return {"status": "error", "message": "import_vcm_state failed"}
