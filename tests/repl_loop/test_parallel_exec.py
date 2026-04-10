from unittest.mock import MagicMock

from cai.repl.loop.parallel_exec import run_simple_parallel


def test_run_simple_parallel_appends_message(monkeypatch):
    main_agent = MagicMock()
    main_agent.model = MagicMock()
    main_agent.model.message_history = []

    def add(msg):
        main_agent.model.message_history.append(msg)

    main_agent.model.add_to_message_history = add

    # get_available_agents returns mapping
    monkeypatch.setattr(
        "cai.agents.get_available_agents", lambda: {"myagent": MagicMock(name="A")}, raising=False
    )

    # get_agent_by_name returns per-instance agent
    def get_agent_side(agent_type, custom_name=None, agent_id=None, **kwargs):
        inst = MagicMock()
        inst.model = MagicMock()
        inst.model.message_history = []
        return inst

    monkeypatch.setattr("cai.agents.get_agent_by_name", get_agent_side, raising=False)

    # Runner.run returns an object with final_output
    class Res:
        final_output = "out"

    async def fake_run(instance_agent, conv):
        return Res()

    monkeypatch.setattr("cai.sdk.agents.Runner", MagicMock(run=fake_run), raising=False)

    # Run with 2 instances
    run_simple_parallel("input", main_agent, MagicMock(), "myagent", 2)

    assert any(m.get("content") == "out" for m in main_agent.model.message_history)
