from unittest.mock import MagicMock

import cai.repl.loop.session as session


def test_initialize_session_minimal(monkeypatch):
    starting_agent = MagicMock()
    starting_agent.model = MagicMock()
    starting_agent.model.set_agent_name = MagicMock()
    console = MagicMock()

    # Patch COST_TRACKER
    class DummyCostTracker:
        def reset_agent_costs(self):
            pass

    monkeypatch.setattr("cai.util.COST_TRACKER", DummyCostTracker(), raising=False)

    # Patch AGENT_MANAGER
    class DummyAM:
        def reset_registry(self):
            pass

        def switch_to_single_agent(self, agent, name):
            pass

    monkeypatch.setattr(
        "cai.sdk.agents.simple_agent_manager.AGENT_MANAGER",
        DummyAM(),
        raising=False,
    )

    # UI helpers and session recorder
    monkeypatch.setattr(
        "cai.repl.commands.FuzzyCommandCompleter", lambda: MagicMock(), raising=False
    )
    monkeypatch.setattr(
        "cai.repl.ui.keybindings.create_key_bindings", lambda current_text: "kb", raising=False
    )
    monkeypatch.setattr(
        "cai.repl.ui.logging.setup_session_logging", lambda: "history.log", raising=False
    )

    class DummySessionLogger:
        session_id = "sid"

    monkeypatch.setattr(
        "cai.sdk.agents.run_to_jsonl.get_session_recorder",
        lambda: DummySessionLogger(),
        raising=False,
    )

    monkeypatch.setattr(
        "cai.sdk.agents.global_usage_tracker.GLOBAL_USAGE_TRACKER",
        MagicMock(start_session=lambda **kwargs: None),
        raising=False,
    )

    # Banner no-ops
    monkeypatch.setattr("cai.repl.ui.banner.display_banner", lambda c: None, raising=False)
    monkeypatch.setattr("cai.repl.ui.banner.display_quick_guide", lambda c: None, raising=False)

    result = session.initialize_session(starting_agent, console, "one_tool_agent")
    assert isinstance(result, tuple) and len(result) == 5
