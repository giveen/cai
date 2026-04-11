"""Atomic Red Team integration tools.

These tools provide safe, read-only access to a local clone of the
Atomic Red Team repository (metadata only) and provide a generator for
synthetic, non-actionable log events useful for testing logging/alerting
pipelines.

The tools are intentionally conservative: they will not run any code from
the repository. They only scan files for metadata (YAML/MD) and synthesize
harmless observables.
"""

from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from cai.sdk.agents import function_tool


def _find_art_repo(path: str | None = None) -> Path | None:
    # Prefer explicit path
    if path:
        p = Path(path)
        if p.exists():
            return p

    # Environment variable
    env = os.getenv("ART_REPO_PATH")
    if env:
        p = Path(env)
        if p.exists():
            return p

    # Common local locations (project-relative)
    cwd = Path.cwd()
    candidates = [cwd / "atomic-red-team", cwd.parent / "atomic-red-team", Path("/opt/atomic-red-team")]
    for c in candidates:
        if c.exists():
            return c
    return None


def _scan_repo_yaml(repo: Path) -> List[Path]:
    # Collect YAML/YML files under the repo for lightweight scanning
    out: List[Path] = []
    for ext in ("*.yml", "*.yaml"):
        out.extend(list(repo.rglob(ext)))
    return out


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@function_tool(strict_mode=False)
async def list_art_techniques(path: str = "") -> str:
    """List discovered techniques from a local Atomic Red Team clone.

    Args:
        path: optional path to the cloned atomic-red-team repository.

    Returns:
        JSON array of technique dicts: {"id": "T1003", "name": "...", "path": "..."}
    """
    repo = _find_art_repo(path or None)
    if not repo:
        return json.dumps({"ok": False, "error": "Atomic Red Team repo not found. Set ART_REPO_PATH or clone the repo next to this project."})

    files = _scan_repo_yaml(repo)
    techniques: Dict[str, Dict[str, Any]] = {}

    for f in files:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # Heuristic: find first technique id like 'T1003' in the file
        m = re.search(r"(T\d{4})", txt)
        if not m:
            continue
        tid = m.group(1)

        # Name heuristics: search for display_name or title keys
        name_m = re.search(r"(?m)^(?:display_name|title|name)\s*:\s*['\"]?(.*?)['\"]?$", txt)
        name = name_m.group(1).strip() if name_m else f.stem

        if tid not in techniques:
            techniques[tid] = {"id": tid, "name": name, "path": str(f.relative_to(repo))}

    return json.dumps(list(techniques.values()), indent=2)


@function_tool(strict_mode=False)
async def get_art_technique(technique_id: str, path: str = "") -> str:
    """Return a short summary or the source snippet for a technique id.

    Args:
        technique_id: e.g. 'T1003'
        path: optional path to atomic-red-team repo

    Returns:
        JSON object with fields 'id', 'file', and 'snippet' (text)
    """
    if not technique_id:
        return json.dumps({"ok": False, "error": "No technique_id provided"})

    repo = _find_art_repo(path or None)
    if not repo:
        return json.dumps({"ok": False, "error": "Atomic Red Team repo not found"})

    files = _scan_repo_yaml(repo)
    tid = technique_id.strip().upper()

    for f in files:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if tid in txt:
            # Return a small snippet (first 4000 chars)
            snippet = txt[:4000]
            return json.dumps({"id": tid, "file": str(f.relative_to(repo)), "snippet": snippet})

    return json.dumps({"ok": False, "error": f"Technique {tid} not found"})


def _rand_host() -> str:
    return f"host-{random.randint(1,9999)}"


def _rand_user() -> str:
    return random.choice(["alice", "bob", "svc-agent", "jdoe"])


def _rand_pid() -> int:
    return random.randint(100, 65535)


@function_tool(strict_mode=False)
async def generate_art_logs(techniques: str = "", count: int = 3, path: str = "") -> str:
    """Generate synthetic, non-actionable log events for given techniques.

    Args:
        techniques: comma-separated technique IDs or JSON array string
        count: number of events per technique
        path: optional atomic-red-team repo path (unused, provided for parity)

    Returns:
        JSON array of event objects
    """
    # Parse techniques parameter
    tech_list: List[str] = []
    if not techniques:
        return json.dumps({"ok": False, "error": "No techniques provided"})
    s = techniques.strip()
    if s.startswith("["):
        try:
            arr = json.loads(s)
            tech_list = [str(x).strip().upper() for x in arr if x]
        except Exception:
            tech_list = [t.strip().upper() for t in s.strip("[]").split(",") if t.strip()]
    else:
        tech_list = [t.strip().upper() for t in s.split(",") if t.strip()]

    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []

    for tid in tech_list:
        for i in range(max(1, int(count))):
            ts = now - timedelta(seconds=random.randint(0, 3600))
            event_type = random.choice([
                "auth_fail",
                "process_start",
                "file_access",
                "network_connection",
                "dns_query",
            ])

            proc = f"proc_{tid.lower()}.exe"
            host = _rand_host()
            user = _rand_user()
            pid = _rand_pid()

            message = f"Synthetic {event_type} event for {tid} on {host} by {user}"

            metadata = {
                "pid": pid,
                "exe": proc,
                "src_ip": f"10.0.{random.randint(0,255)}.{random.randint(1,254)}",
                "dst_ip": f"198.51.100.{random.randint(1,254)}",
                "file_path": f"C:\\Windows\\Temp\\{tid}_{random.randint(1,999)}.tmp",
            }

            evt = {
                "timestamp": _format_iso(ts),
                "technique_id": tid,
                "technique_name": f"Technique {tid}",
                "host": host,
                "user": user,
                "process": proc,
                "event_type": event_type,
                "message": message,
                "metadata": metadata,
            }
            out.append(evt)

    return json.dumps(out, indent=2)
