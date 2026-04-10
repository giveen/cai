from unittest.mock import MagicMock

import cai.repl.loop.agent_sync as agent_sync


def test_update_agent_models_recursively_fallback(monkeypatch):
    agent = MagicMock()
    agent.model = MagicMock()
    agent.model.model = None

    # Simulate AGENT_MANAGER.sync_models raising to exercise fallback path
    monkeypatch.setattr(
        "cai.sdk.agents.simple_agent_manager.AGENT_MANAGER",
        MagicMock(sync_models=MagicMock(side_effect=Exception())),
        raising=False,
    )

    agent_sync.update_agent_models_recursively(agent, "m1")
    assert agent.model.model == "m1"


def test_sync_model_applies_env_override(monkeypatch):
    agent = MagicMock()
    agent.model = MagicMock()
    monkeypatch.setenv("CAI_MODEL", "modelX")
    # Ensure AGENT_MANAGER.sync_models is safe to call
    monkeypatch.setattr(
        "cai.sdk.agents.simple_agent_manager.AGENT_MANAGER",
        MagicMock(sync_models=MagicMock()),
        raising=False,
    )

    current_model, last_model = agent_sync.sync_model(agent, "alias1", "one_tool_agent")
    assert current_model == "modelX"
    assert last_model == "modelX"
