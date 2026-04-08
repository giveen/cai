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
offsec_pattern = {
    "name": "offsec_pattern",
    "type": "parallel",
    "description": (
        "Bug bounty and red team with different contexts for "
        "offensive security ops"
    ),
    "configs": [
        ParallelConfig("redteam_agent"),
        ParallelConfig("bug_bounter_agent")
    ],
    "unified_context": False,
}