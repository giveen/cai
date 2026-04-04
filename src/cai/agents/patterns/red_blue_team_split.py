"""
Parallel security assessment pattern - red/blue team with split context.

This pattern demonstrates the use of the unified Pattern class for
parallel agent execution, where red and blue team agents operate
with separate contexts for independent analysis.
"""

try:
    from cai.repl.commands.parallel import ParallelConfig
except Exception:
    class ParallelConfig:  # lightweight fallback
        def __init__(self, agent_name, model=None, prompt=None, unified_context=True):
            self.agent_name = agent_name
            self.model = model
            self.prompt = prompt
            self.unified_context = unified_context

# Pattern configuration
blue_team_red_team_split_context_pattern = {
    "name": "blue_team_red_team_split_context",
    "type": "parallel",
    "description": (
        "Red and blue team agents with different contexts for "
        "comprehensive security assessment"
    ),
    "configs": [
        ParallelConfig("redteam_agent"),
        ParallelConfig("blueteam_agent"),
    ],
    "unified_context": False,
}