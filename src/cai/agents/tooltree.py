"""ToolTree planner: generate candidate branches for aggressive tools,
score them using the support reasoner agent (if available), choose the
best branch, and persist the reasoning to the intelligence journal.

This module is lightweight and falls back to a deterministic heuristic
if the support reasoner is unavailable or its output cannot be parsed.
"""

from __future__ import annotations

import json
import os
import logging
import re
from typing import Any

from cai.agents.meta.reasoner_support import reasoner_agent
from cai.sdk.agents.run import Runner
from cai.orchestration.persistence import sync_to_journal

logger = logging.getLogger(__name__)


def is_aggressive_tool(tool_id: str | None, meta: dict | None, params: dict | None, text: str | None = None) -> bool:
    """Heuristic: detect whether a tool invocation is aggressive (high-risk, high-noise).

    Conservative substring checks are used; expand as needed.
    """
    try:
        parts = []
        if tool_id:
            parts.append(str(tool_id))
        if isinstance(meta, dict):
            parts.append(str(meta.get("name", "")))
            parts.append(str(meta.get("description", "")))
        if params:
            try:
                parts.append(json.dumps(params, ensure_ascii=False))
            except Exception:
                parts.append(str(params))
        if text:
            parts.append(str(text))
        combined = " ".join([p.lower() for p in parts if p])
        if not combined:
            return False
        keywords = (
            "sqlmap",
            "exploit",
            "metasploit",
            "meterpreter",
            "bruteforce",
            "hydra",
            "fuzz",
            "wfuzz",
            "ffuf",
            "masscan",
            "nmap -sS",
        )
        return any(k in combined for k in keywords)
    except Exception:
        return False


def _generate_branches(tool_id: str, params: dict | None, context_text: str | None = None) -> list[dict[str, Any]]:
    """Produce at least three candidate branches (conservative, balanced, aggressive).

    Branches are best-effort parameter variations; callers should tolerate
    unknown/ignored keys in params.
    """
    base = dict(params or {})
    branches = []

    # Conservative: safe defaults, minimize noise
    conservative = dict(base)
    conservative.update({"mode": "conservative", "concurrency": 1, "force": False, "note": "minimize noise"})
    branches.append({"id": "A", "name": "conservative", "description": "Minimise noise and risk", "params": conservative})

    # Balanced: trade-off between success and noise
    balanced = dict(base)
    balanced.update({"mode": "balanced", "concurrency": 2, "force": False, "note": "balanced approach"})
    branches.append({"id": "B", "name": "balanced", "description": "Balanced approach between success and noise", "params": balanced})

    # Aggressive: maximise chance, accept higher noise
    aggressive = dict(base)
    aggressive.update({"mode": "aggressive", "concurrency": 8, "force": True, "note": "maximize success (noisy)"})
    branches.append({"id": "C", "name": "aggressive", "description": "Maximise success probability at the cost of noise", "params": aggressive})

    return branches


async def _score_with_reasoner(branches: list[dict], tool_id: str, params: dict | None, context_text: str | None = None) -> list[dict]:
    """Ask the support reasoner agent to score each branch.

    Returns a list of dicts with keys: id, probability (0-100), noise (0-100), comment.
    Falls back to heuristic scoring when parsing fails.
    """
    try:
        # Build a compact prompt enumerating the branches
        lines = [f"Tool: {tool_id}", "Alternate branches:"]
        for b in branches:
            lines.append(f"{b['id']}: {b.get('description','')}. params={json.dumps(b.get('params',{}), ensure_ascii=False)}")
        lines.append("")
        lines.append(
            "For each branch return a JSON array of objects with fields: id, probability (0-100), noise (0-100), comment."
        )
        lines.append(
            "Score to maximise: probability * (1 - noise/100). Provide only valid JSON array in your final answer. Keep comments brief."
        )
        prompt = "\n".join(lines)

        # Run the reasoner agent once to get scores
        run_result = await Runner.run(starting_agent=reasoner_agent, input=prompt, context=None, max_turns=1)
        out = run_result.final_output if hasattr(run_result, "final_output") else str(run_result)
        text = str(out or "")

        # Try to extract the first JSON array from the output
        m = re.search(r"(\[\s*\{[\s\S]*?\}\s*\])", text)
        candidate = None
        if m:
            candidate = m.group(1)
        else:
            # As a fallback, try to parse any trailing JSON-like content
            try:
                candidate = text[text.index("["):]
            except Exception:
                candidate = None

        if candidate:
            parsed = json.loads(candidate)
            # Normalize outputs
            results = []
            for p in parsed:
                try:
                    pid = p.get("id")
                    prob = int(p.get("probability") or p.get("prob") or 0)
                    noise = int(p.get("noise") or 0)
                    comment = p.get("comment") or p.get("reason") or ""
                    results.append({"id": pid, "probability": max(0, min(100, prob)), "noise": max(0, min(100, noise)), "comment": str(comment)})
                except Exception:
                    continue
            if results:
                return results
    except Exception:
        logger.exception("reasoner scoring failed")

    # Heuristic fallback: derive scores from branch 'mode' and presence of 'force'
    results = []
    for b in branches:
        mode = (b.get("params", {}).get("mode") or b.get("name") or "").lower()
        force = bool(b.get("params", {}).get("force"))
        if mode == "conservative":
            prob = 35
            noise = 10
        elif mode == "balanced":
            prob = 60
            noise = 30
        else:
            prob = 80 if force else 65
            noise = 70 if force else 50
        results.append({"id": b["id"], "probability": prob, "noise": noise, "comment": "heuristic"})
    return results


def _choose_best(branches: list[dict], scores: list[dict]) -> tuple[dict, list[dict]]:
    """Combine branch metadata with scores, compute aggregate score, and pick best."""
    by_id = {s["id"]: s for s in scores}
    combined = []
    for b in branches:
        sid = b["id"]
        s = by_id.get(sid, {"probability": 0, "noise": 100, "comment": ""})
        prob = float(s.get("probability", 0))
        noise = float(s.get("noise", 100))
        agg = prob * (1.0 - (noise / 100.0))
        entry = dict(b)
        entry.update({"probability": prob, "noise": noise, "score": agg, "comment": s.get("comment", "")})
        combined.append(entry)
    # choose highest score
    chosen = max(combined, key=lambda x: x.get("score", 0.0))
    return chosen, combined


async def plan_and_select(tool_id: str, params: dict | None, context_text: str | None = None) -> tuple[dict, dict]:
    """Generate branches, score them (with support agent when possible), persist reasoning, and return chosen params.

    Returns (chosen_params, plan_record).
    """
    branches = _generate_branches(tool_id, params, context_text)
    scores = await _score_with_reasoner(branches, tool_id, params, context_text)
    chosen, combined = _choose_best(branches, scores)

    plan_record = {
        "tool_id": tool_id,
        "original_params": params or {},
        "branches": combined,
        "selected": {"id": chosen.get("id"), "name": chosen.get("name"), "params": chosen.get("params"), "score": chosen.get("score")},
    }

    try:
        # Persist the plan and reasoning to the intelligence journal
        try:
            sync_to_journal(None, plan_record, category="planning", source="ToolTree", confidence_score=0.8, source_tool="ToolTree")
        except Exception:
            # best-effort; do not fail the tool run if journalling fails
            logger.exception("failed to persist plan to journal")
    except Exception:
        pass

    return chosen.get("params", params or {}), plan_record
