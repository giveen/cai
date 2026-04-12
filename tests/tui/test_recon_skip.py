import asyncio
import json


class DummyApp:
    def __init__(self):
        # minimal tool registry with an async runner
        async def _nmap_runner(params):
            return {"output": "ok"}

        self._tool_registry = {"nmap": {"name": "nmap", "description": "network scanner", "runner": _nmap_runner}}
        self._tool_call_history: list[dict] = []
        self.logged: list[str] = []
        self._active_term_id = 1

    async def _refresh_tools_history_ui(self):
        return None

    def _update_tools_preview(self):
        return None

    def _append_tool_call(self, tool_id, inputs, output, replayed=False):
        record = {
            "call_id": f"tool-{len(self._tool_call_history) + 1}",
            "tool_id": tool_id,
            "timestamp": "",
            "inputs": inputs,
            "output": output,
            "replayed": replayed,
        }
        self._tool_call_history.append(record)
        return record

    def _log_to_active_terminal(self, msg, style=None):
        self.logged.append(msg)


def test_looks_like_recon_call_positive():
    from cai.tui.controller import _looks_like_recon_call

    assert _looks_like_recon_call("nmap", {"name": "nmap"}, {"target": "1.2.3.4"})
    assert _looks_like_recon_call(None, None, None, "run nmap against 10.0.0.1")


def test_run_tool_skips_on_resume_flag():
    from cai.tui.controller import TuiController

    app = DummyApp()
    app._resume_skip_recon = True
    controller = TuiController(app)

    # call underlying coroutine (bypass @work wrapper)
    asyncio.run(TuiController.run_tool.__wrapped__(controller, "nmap", {"target": "1.2.3.4"}))

    # run_tool should not have appended a tool call and should have logged a skip
    assert len(app._tool_call_history) == 0
    assert any("Skipping" in str(m) or "skipping" in str(m).lower() for m in app.logged)


def test_nmap_scan_skips_on_resume_flag():
    from cai.tui.controller import TuiController

    app = DummyApp()
    app._resume_skip_recon = True
    controller = TuiController(app)

    res = asyncio.run(TuiController.nmap_scan.__wrapped__(controller, "1.2.3.4", 1))
    assert isinstance(res, dict)
    assert res.get("status") == "skipped"
    assert res.get("reason") == "resume_skip_recon"


def test_replay_tool_skips_on_resume_flag():
    from cai.tui.controller import TuiController

    app = DummyApp()
    app._resume_skip_recon = True
    controller = TuiController(app)

    # add a prior call record
    rec = {"call_id": "tool-1", "tool_id": "nmap", "inputs": {"target": "1.2.3.4"}, "output": {}}
    app._tool_call_history.append(rec)

    asyncio.run(TuiController.replay_tool_call.__wrapped__(controller, 0, None))

    # replay should not have appended a new call and should have logged a skip
    assert len(app._tool_call_history) == 1
    assert any("Replay" in str(m) and "skipped" in str(m).lower() for m in app.logged)
