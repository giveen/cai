"""Team preset constants for CAI TUI.
Extracted from app_impl.py for use by layout.py and other modules.
"""

from __future__ import annotations

TEAM_PRESETS = [
    ("2 red + 2 bug", ["redteam_agent", "redteam_agent", "bug_bounter_agent", "bug_bounter_agent"]),
    (
        "1 red + 3 bug",
        ["redteam_agent", "bug_bounter_agent", "bug_bounter_agent", "bug_bounter_agent"],
    ),
    ("2 red + 2 blue", ["redteam_agent", "redteam_agent", "blueteam_agent", "blueteam_agent"]),
    (
        "2 blue + 2 bug",
        ["blueteam_agent", "blueteam_agent", "bug_bounter_agent", "bug_bounter_agent"],
    ),
    (
        "red + blue + retest + bug",
        ["redteam_agent", "blueteam_agent", "retester_agent", "bug_bounter_agent"],
    ),
    ("2 red + 2 retest", ["redteam_agent", "redteam_agent", "retester_agent", "retester_agent"]),
    ("2 blue + 2 retest", ["blueteam_agent", "blueteam_agent", "retester_agent", "retester_agent"]),
    ("4 red", ["redteam_agent", "redteam_agent", "redteam_agent", "redteam_agent"]),
    ("4 blue", ["blueteam_agent", "blueteam_agent", "blueteam_agent", "blueteam_agent"]),
    ("4 bug", ["bug_bounter_agent", "bug_bounter_agent", "bug_bounter_agent", "bug_bounter_agent"]),
    ("4 retest", ["retester_agent", "retester_agent", "retester_agent", "retester_agent"]),
]

TEAM_PLAYBOOK_HINTS = [
    "Best for: Comprehensive vulnerability discovery with red + bug workflows.",
    "Best for: Bug bounty-heavy runs with red-team lead coverage.",
    "Best for: Balanced adversarial testing with offense + defense.",
    "Best for: Defense-led assessments with bug bounty validation.",
    "Best for: End-to-end lifecycle from discovery to validation.",
    "Best for: Aggressive offensive testing with immediate retest.",
    "Best for: Defensive hardening with continuous retesting.",
    "Best for: Maximum offensive throughput and coverage.",
    "Best for: Comprehensive defensive posture and hardening reviews.",
    "Best for: Intensive bug bounty hunting across multiple surfaces.",
    "Best for: Large-scale retesting and fix verification campaigns.",
]
