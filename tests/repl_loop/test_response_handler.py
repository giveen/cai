from unittest.mock import MagicMock

from cai.repl.loop.response_handler import build_conversation_input, run_single_response


def test_build_conversation_input_with_history():
    agent = MagicMock()
    agent.model = MagicMock()
    agent.model.message_history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]

    out = build_conversation_input(agent, "bye", "")
    assert isinstance(out, list)
    assert out[-1]["role"] == "user"


def test_run_single_response_handles_compaction(monkeypatch):
    agent = MagicMock()
    agent.model = MagicMock()
    agent.model.message_history = []

    # Simulate Runner.run raising ContextCompactedError
    from cai.sdk.agents.models.openai_chatcompletions import ContextCompactedError

    async def fake_run(agent_obj, conv):
        raise ContextCompactedError()

    monkeypatch.setattr("cai.sdk.agents.Runner", MagicMock(run=fake_run), raising=False)

    # Ensure AGENT_MANAGER.get_active_agent returns a replacement
    class DummyAM:
        @staticmethod
        def get_active_agent():
            new_agent = MagicMock()
            new_agent.model = MagicMock()
            return new_agent

    monkeypatch.setattr("cai.sdk.agents.simple_agent_manager.AGENT_MANAGER", DummyAM(), raising=False)

    agent2, post, skip, cont = run_single_response(
        agent, "conv", "u", None, False, "", MagicMock(), MagicMock(), 1, 0, 0
    )

    assert cont is True
    assert post is not None


def test_run_single_response_records_tool_output(monkeypatch):
    agent = MagicMock()
    agent.model = MagicMock()
    agent.model.message_history = []

    def add(msg):
        agent.model.message_history.append(msg)

    agent.model.add_to_message_history = add

    # Create a fake ToolCallOutputItem class and instance
    class FakeTool:
        def __init__(self):
            self.raw_item = {"call_id": "c1"}
            self.output = "toolout"

    async def fake_run(agent_obj, conv):
        class Resp:
            new_items = [FakeTool()]

        return Resp()

    monkeypatch.setattr("cai.sdk.agents.Runner", MagicMock(run=fake_run), raising=False)
    monkeypatch.setattr("cai.sdk.agents.items.ToolCallOutputItem", FakeTool, raising=False)

    agent2, post, skip, cont = run_single_response(
        agent, "conv", "u", None, False, "", MagicMock(), MagicMock(), 1, 0, 0
    )

    assert any(m.get("role") == "tool" for m in agent.model.message_history)
