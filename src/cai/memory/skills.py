"""SkillMiner — discover and persist reusable skills from episodic runs.

The SkillMiner monitors recent tool-call history and workspace episodic
memory looking for breakthrough events (flags, root captures, successful
exploits). When it detects a candidate, it crystallizes the preceding
sequence of tool calls into a compact `Skill JSON` and appends it to the
global skills file at `src/cai/skills/global_skills.json`.

This implementation is intentionally conservative and heuristics-based;
it is best-effort and will not raise on failure.
"""

from __future__ import annotations

import collections
import datetime
import json
import logging
import os
import re
import threading
import time
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# Simple breakthrough keywords (case-insensitive)
_BREAKTHROUGH_RE = re.compile(r"\b(root|root.txt|flag|captured|success|exploited)\b", re.I)


class SkillMiner:
    """Background miner that incrementally discovers skills.

    Usage:
        from cai.memory.skills import SKILL_MINER, register_tool_call, start_skill_miner
        SKILL_MINER.start()
        register_tool_call(record)
    """

    def __init__(self, skills_path: Optional[str] = None, interval: int = 10, threshold: int = 50) -> None:
        self._calls: Deque[Dict[str, Any]] = collections.deque(maxlen=1024)
        self._lock = threading.RLock()
        self._interval = int(interval)
        self._threshold = int(threshold)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_mined_at: float = 0.0

        # Default path: repo/src/cai/skills/global_skills.json
        if skills_path:
            self._skills_path = skills_path
        else:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            skills_dir = os.path.join(base, "skills")
            os.makedirs(skills_dir, exist_ok=True)
            self._skills_path = os.path.join(skills_dir, "global_skills.json")

        # Ensure file exists and is a JSON list
        try:
            if not os.path.exists(self._skills_path):
                with open(self._skills_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except Exception:
            logger.exception("SkillMiner: failed to ensure skills file")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="SkillMiner")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            try:
                self._thread.join(timeout=1.0)
            except Exception:
                pass

    def record_tool_call(self, record: Dict[str, Any]) -> None:
        """Accept a tool-call record as produced by the TUI ToolsMixin.

        This function should be cheap and non-blocking.
        """
        try:
            with self._lock:
                self._calls.append(record)
        except Exception:
            logger.exception("SkillMiner: failed to append tool call")

    def mine_now(self) -> Optional[Dict[str, Any]]:
        """Force a mining pass immediately (synchronous). Returns a skill dict on success."""
        try:
            with self._lock:
                return self._attempt_mining()
        except Exception:
            logger.exception("SkillMiner: mine_now failed")
            return None

    # ------------------------------------------------------------------
    # Internal loop and heuristics
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        logger.debug("SkillMiner: background loop started")
        try:
            while not self._stop.wait(self._interval):
                try:
                    with self._lock:
                        # Heuristic triggers: enough calls since last mine OR a breakthrough in state
                        if self._should_mine():
                            sk = self._attempt_mining()
                            if sk:
                                logger.info("SkillMiner: discovered skill %s", sk.get("name"))
                                self._last_mined_at = time.time()
                except Exception:
                    logger.exception("SkillMiner loop error")
        except Exception:
            logger.exception("SkillMiner terminated unexpectedly")

    def _should_mine(self) -> bool:
        # Enough new calls since last mined?
        try:
            if not self._calls:
                return False
            # If we haven't mined recently and number of calls >= threshold
            if len(self._calls) >= self._threshold and (time.time() - self._last_mined_at) > 30.0:
                return True

            # Check state.json for breakthrough signals
            try:
                from cai.session import _read_state

                st = _read_state()
                # Check milestones/current_objective/next_steps
                texts = []
                if isinstance(st.get("milestones"), list):
                    texts.extend([str(x) for x in st.get("milestones") or []])
                if st.get("current_objective"):
                    texts.append(str(st.get("current_objective")))
                if isinstance(st.get("next_steps"), list):
                    texts.extend([str(x) for x in st.get("next_steps") or []])
                combined = " ".join(texts)
                if combined and _BREAKTHROUGH_RE.search(combined):
                    return True
            except Exception:
                pass

            # Also scan recent calls' outputs for breakthrough words
            for rec in list(self._calls)[-100:]:
                out = rec.get("output") or {}
                text = ""
                if isinstance(out, dict):
                    text = json.dumps(out)
                else:
                    text = str(out)
                if _BREAKTHROUGH_RE.search(text):
                    return True

            return False
        except Exception:
            return False

    def _attempt_mining(self) -> Optional[Dict[str, Any]]:
        """Run a single mining pass producing at most one skill.

        Returns the skill dict if created, else None.
        """
        try:
            calls = list(self._calls)
            if not calls:
                return None

            # Find an index of a breakthrough event in calls or in state
            breakthrough_idx = None
            for i in range(len(calls) - 1, -1, -1):
                rec = calls[i]
                out = rec.get("output") or {}
                text = json.dumps(out) if isinstance(out, dict) else str(out)
                if _BREAKTHROUGH_RE.search(text):
                    breakthrough_idx = i
                    break

            # If none in calls, consult state.json
            if breakthrough_idx is None:
                try:
                    from cai.session import _read_state

                    st = _read_state()
                    # Look for milestone strings with breakthrough words
                    for m in reversed(st.get("milestones") or []):
                        if _BREAKTHROUGH_RE.search(str(m)):
                            # find nearest call before now
                            breakthrough_idx = len(calls) - 1
                            break
                except Exception:
                    pass

            if breakthrough_idx is None:
                # Fallback: mine when threshold exceeded using last sequence
                if len(calls) < max(5, self._threshold // 2):
                    return None
                breakthrough_idx = len(calls) - 1

            # Build action sequence: take up to 12 calls leading up to breakthrough
            start_idx = max(0, breakthrough_idx - 12)
            seq = []
            for c in calls[start_idx : breakthrough_idx + 1]:
                seq.append(
                    {
                        "tool_id": c.get("tool_id"),
                        "inputs": c.get("inputs"),
                        "output_summary": (json.dumps(c.get("output"))[:800] if c.get("output") is not None else ""),
                        "timestamp": c.get("timestamp"),
                    }
                )

            # Simple environment/prereq heuristics
            env = self._detect_environment(seq)
            prereqs = self._detect_prerequisites(seq)

            # Friendly name
            stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            short = "-".join([str(x.get("tool_id") or "?") for x in seq[:3]])
            name = f"auto-skill-{short[:80]}-{stamp}"

            skill = {
                "name": name,
                "environment_type": env,
                "prerequisites": prereqs,
                "action_sequence": seq,
                "source": "skillminer",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            }

            # Persist skill (deduplicating similar sequences)
            saved = self._persist_skill(skill)
            if saved:
                return skill
            return None
        except Exception:
            logger.exception("SkillMiner: mining attempt failed")
            return None

    def _detect_environment(self, seq: List[Dict[str, Any]]) -> str:
        # Heuristic: scan outputs/inputs for OS keywords
        try:
            text = " ".join([json.dumps(s.get("inputs") or {}) + " " + str(s.get("output") or "") for s in seq])
            if re.search(r"ubuntu|debian|centos|alpine|fedora|arch|linux", text, re.I):
                return "linux"
            if re.search(r"windows|win32|win64|nt", text, re.I):
                return "windows"
            return "generic"
        except Exception:
            return "unknown"

    def _detect_prerequisites(self, seq: List[Dict[str, Any]]) -> List[str]:
        # Heuristic: look for discovered open ports or credentials in state
        prereqs: List[str] = []
        try:
            # Check session state for targets/credentials
            try:
                from cai.session import _read_state

                st = _read_state()
                hosts = st.get("targets") or []
                creds = st.get("credentials") or {}
                if hosts:
                    prereqs.append(f"hosts:{','.join([str(h) for h in hosts[:5]])}")
                if creds:
                    prereqs.append(f"creds:{','.join(list(creds.keys())[:5])}")
            except Exception:
                pass

            # Look for port mentions in sequence outputs
            combined = " ".join([s.get("output_summary", "") for s in seq])
            ports = set(re.findall(r"\b([0-9]{2,5})/tcp\b", combined))
            if ports:
                prereqs.append("open_ports:" + ",".join(sorted(ports)))
        except Exception:
            pass
        return prereqs

    def _persist_skill(self, skill: Dict[str, Any]) -> bool:
        try:
            with self._lock:
                # Load current skills
                try:
                    with open(self._skills_path, "r", encoding="utf-8") as f:
                        data = json.load(f) or []
                except Exception:
                    data = []

                # Deduplicate by exact action_sequence or name
                for ex in data:
                    if ex.get("action_sequence") == skill.get("action_sequence"):
                        logger.debug("SkillMiner: duplicate skill detected, skipping")
                        return False

                data.append(skill)
                # Best-effort atomic write
                tmp = self._skills_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    os.replace(tmp, self._skills_path)
                except Exception:
                    try:
                        os.remove(self._skills_path)
                    except Exception:
                        pass
                    os.replace(tmp, self._skills_path)
                logger.info("SkillMiner: persisted skill %s", skill.get("name"))
                return True
        except Exception:
            logger.exception("SkillMiner: failed to persist skill")
            return False


# Singleton instance for convenience
SKILL_MINER = SkillMiner()


def register_tool_call(record: Dict[str, Any]) -> None:
    try:
        SKILL_MINER.record_tool_call(record)
    except Exception:
        pass


def start_skill_miner() -> None:
    try:
        SKILL_MINER.start()
    except Exception:
        pass


def stop_skill_miner() -> None:
    try:
        SKILL_MINER.stop()
    except Exception:
        pass


__all__ = [
    "SKILL_MINER",
    "register_tool_call",
    "start_skill_miner",
    "stop_skill_miner",
]
