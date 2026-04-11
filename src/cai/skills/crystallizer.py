"""Skill Crystallizer — distill successful exploit paths into Action Templates.

When a milestone indicating completion (flag/root found, exploited, success)
appears in `state.json`, the Crystallizer inspects recent tool calls (via
`SKILL_MINER`), extracts the sequence leading to the breakthrough, replaces
environment-specific values with placeholders, and writes a reusable JSON
`Action Template` into `src/cai/skills/` (templates/). Templates can be
looked up with `find_matching_templates()` and injected into prompts.

This implementation is conservative and best-effort; failures do not raise.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Reuse the same breakthrough regex as SkillMiner
_BREAKTHROUGH_RE = re.compile(r"\b(root|root.txt|flag|captured|success|exploited|complete)\b", re.I)


def _iso_to_dt(s: str) -> Optional[datetime.datetime]:
    if not s:
        return None
    try:
        # Strip trailing Z if present
        s2 = s.rstrip("Z")
        return datetime.datetime.fromisoformat(s2)
    except Exception:
        try:
            return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None


def _sanitize_value(v: Any) -> Any:
    """Replace environment-specific strings with placeholders."""
    try:
        if v is None:
            return v
        if isinstance(v, dict):
            return {k: _sanitize_value(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_sanitize_value(x) for x in v]
        if isinstance(v, str):
            s = v
            # IP addresses -> {{target_host}}
            s = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "{{target_host}}", s)
            # ports like 8080/tcp or :8080 -> {{port}}
            s = re.sub(r"\b([0-9]{2,5})/tcp\b", "{{port}}/tcp", s)
            s = re.sub(r":([0-9]{2,5})(?![0-9])", ":{{port}}", s)
            # credentials user:pass -> {{username}}:{{password}}
            s = re.sub(r"([A-Za-z0-9_\-\.]{1,64}):([^\s,:;]{1,200})", "{{username}}:{{password}}", s)
            # URLs with host -> replace host portion
            s = re.sub(r"(https?://)(?:[^/\s:]+)", r"\1{{target_host}}", s)
            return s
        return v
    except Exception:
        return v


class Crystallizer:
    def __init__(self, skills_dir: Optional[str] = None, interval: int = 6) -> None:
        self._interval = int(interval)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        skills_base = os.path.join(base, "skills") if skills_dir is None else skills_dir
        os.makedirs(skills_base, exist_ok=True)
        self._skills_base = skills_base
        self._templates_dir = os.path.join(self._skills_base, "templates")
        os.makedirs(self._templates_dir, exist_ok=True)
        # Keep track of milestones we've already processed
        self._seen_milestones: set = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="Crystallizer")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            try:
                self._thread.join(timeout=1.0)
            except Exception:
                pass

    def _run_loop(self) -> None:
        try:
            while not self._stop.wait(self._interval):
                try:
                    self._check_state_for_completion()
                except Exception:
                    logger.exception("Crystallizer loop error")
        except Exception:
            logger.exception("Crystallizer terminated unexpectedly")

    def _check_state_for_completion(self) -> None:
        try:
            # Lazy import to avoid heavy deps at module import time
            from cai.session import _read_state

            st = _read_state()
            milestones = st.get("milestones") or []
            for m in milestones:
                if not isinstance(m, str):
                    continue
                if m in self._seen_milestones:
                    continue
                # Simple match for breakthrough/completion
                # milestone strings may have a timestamp prefix: "2026-..Z - text"
                parts = m.split(" - ", 1)
                text = parts[1] if len(parts) > 1 else parts[0]
                if _BREAKTHROUGH_RE.search(text):
                    # New completion found — process it
                    try:
                        self._process_milestone(m)
                    except Exception:
                        logger.exception("failed to process milestone: %s", m)
                self._seen_milestones.add(m)
        except Exception:
            # Do not propagate errors
            pass

    def _process_milestone(self, milestone: str) -> Optional[str]:
        try:
            # Find candidate calls from SkillMiner (best-effort)
            try:
                from cai.memory.skills import SKILL_MINER

                calls_deque = getattr(SKILL_MINER, "_calls", None)
                calls = list(calls_deque) if calls_deque is not None else []
            except Exception:
                calls = []

            # If no in-memory calls, fallback to TUI log file
            if not calls:
                try:
                    from cai.tui.app_impl import TOOL_CALLS_FILE

                    if os.path.exists(TOOL_CALLS_FILE):
                        with open(TOOL_CALLS_FILE, encoding="utf-8") as f:
                            calls = [json.loads(l) for l in f.read().splitlines() if l.strip()]
                except Exception:
                    calls = []

            if not calls:
                return None

            # Parse milestone timestamp if present
            ts = None
            parts = milestone.split(" - ", 1)
            if len(parts) > 1:
                ts = _iso_to_dt(parts[0])

            # Select calls leading up to the milestone time (or last N)
            selected: List[Dict[str, Any]] = []
            if ts:
                for c in reversed(calls):
                    cts = _iso_to_dt(c.get("timestamp", ""))
                    if cts is None or cts <= ts:
                        selected.insert(0, c)
                    if len(selected) >= 12:
                        break
            else:
                selected = calls[-12:]

            if not selected:
                return None

            # Distill sequence into template steps with placeholders
            steps = []
            for c in selected:
                steps.append(
                    {
                        "tool_id": c.get("tool_id"),
                        "inputs": _sanitize_value(c.get("inputs")),
                        "output_summary": (json.dumps(c.get("output"), ensure_ascii=False)[:800] if c.get("output") is not None else ""),
                    }
                )

            # Guess service from steps
            svc = self._guess_service_from_steps(steps)

            name = f"crystal-{svc}-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
            tpl = {
                "name": name,
                "service": svc,
                "milestone": milestone,
                "steps": steps,
                "placeholders": ["{{target_host}}", "{{port}}", "{{username}}", "{{password}}"],
                "source": "crystallizer",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            }

            # Write template file
            fname = os.path.join(self._templates_dir, f"{name}.json")
            try:
                with open(fname + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(tpl, f, ensure_ascii=False, indent=2)
                os.replace(fname + ".tmp", fname)
            except Exception:
                try:
                    with open(fname, "w", encoding="utf-8") as f:
                        json.dump(tpl, f, ensure_ascii=False, indent=2)
                except Exception:
                    logger.exception("Crystallizer: failed to write template file %s", fname)
                    return None

            logger.info("Crystallizer: wrote template %s", fname)
            return fname
        except Exception:
            logger.exception("Crystallizer: processing milestone failed")
            return None

    def _guess_service_from_steps(self, steps: List[Dict[str, Any]]) -> str:
        try:
            combined = " ".join([s.get("output_summary", "") + " " + json.dumps(s.get("inputs", {})) for s in steps])
            svc_map = [
                (r"\bhttp\b|\bweb\b|\bhttp/\b", "http"),
                (r"\bssh\b", "ssh"),
                (r"mysql|mariadb|3306", "mysql"),
                (r"postgres|5432", "postgres"),
                (r"rdp|3389", "rdp"),
                (r"ftp\b", "ftp"),
                (r"smtp\b", "smtp"),
                (r"vnc\b", "vnc"),
            ]
            for patt, label in svc_map:
                if re.search(patt, combined, re.I):
                    return label
            # fallback: use first tool as proxy
            first_tool = steps[0].get("tool_id") if steps else "generic"
            return re.sub(r"[^a-z0-9_-]", "-", str(first_tool or "generic")).lower()
        except Exception:
            return "generic"

    def find_matching_templates(self, service: str | None = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            for fn in sorted(os.listdir(self._templates_dir)):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(self._templates_dir, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if service:
                    svc = (data.get("service") or "").lower()
                    if service.lower() in svc:
                        out.append(data)
                else:
                    out.append(data)
        except Exception:
            pass
        return out


# Singleton
CRYSTALLIZER = Crystallizer()


def start_crystallizer() -> None:
    try:
        CRYSTALLIZER.start()
    except Exception:
        pass


def stop_crystallizer() -> None:
    try:
        CRYSTALLIZER.stop()
    except Exception:
        pass


def find_matching_templates(service: str | None = None) -> List[Dict[str, Any]]:
    try:
        return CRYSTALLIZER.find_matching_templates(service)
    except Exception:
        return []


__all__ = ["CRYSTALLIZER", "start_crystallizer", "stop_crystallizer", "find_matching_templates"]
