from unittest.mock import MagicMock

from cai.repl.loop.state import CliSessionState


def test_cli_session_state_defaults():
    agent = MagicMock()
    state = CliSessionState(agent=agent)

    assert state.turn_count == 0
    assert state._post_compact_input is None
    assert state.parallel_count == 1
    assert state.agent is agent
