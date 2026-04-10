#!/usr/bin/env python3
"""Lightweight smoke checks for TUI tools registry logic.

This script validates the non-UI Tools tab mechanics in `CAIApp`:
- registry creation
- basic tool execution
- call history append
- replay metadata helper (`last_tool_call`)
"""

import json
import os
import tempfile

import cai.tui.app as tui_app
from cai.tui.app import CAIApp


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def main() -> int:
    print("Running TUI tools smoke tests...")

    # Isolate config persistence checks by overriding module config file path.
    work_dir = tempfile.mkdtemp(prefix="tui_tools_smoke_")
    original_config_file = tui_app.CONFIG_FILE
    tui_app.CONFIG_FILE = os.path.join(work_dir, "tui_config.json")

    app = CAIApp(agent=None)

    palette = app._command_palette_commands()
    palette_ids = {c.get("id") for c in palette}
    required_palette = {"clear", "save", "load", "export", "reset", "help"}
    if required_palette.issubset(palette_ids):
        ok("command palette command inventory")
    else:
        fail(f"command palette missing commands: {required_palette - palette_ids}")
        return 1

    app._record_palette_recent("help")
    app._record_palette_recent("clear")
    app._record_palette_recent("help")
    if app._command_palette_recent[:2] == ["help", "clear"]:
        ok("command palette recent history")
    else:
        fail(f"command palette recent history unexpected: {app._command_palette_recent}")
        return 1

    panel = tui_app.TerminalPanel(
        term_id=1,
        agent=None,
        agent_name="one_tool_agent",
        model_name="alias1",
    )

    # Terminal panel helper checks for richer rendering paths.
    short_preview, short_collapsed = panel._format_tool_output_preview("ok")
    if short_preview == "ok" and not short_collapsed:
        ok("tool output preview no-collapse")
    else:
        fail(
            f"tool output preview unexpected (short): {short_preview}, collapsed={short_collapsed}"
        )
        return 1

    long_output = "\n".join(["line"] * 30)
    long_preview, long_collapsed = panel._format_tool_output_preview(long_output)
    if long_collapsed and len(long_preview.splitlines()) <= 14:
        ok("tool output preview collapse")
    else:
        fail(f"tool output preview unexpected (long): collapsed={long_collapsed}")
        return 1

    delta = panel._extract_stream_text_delta({"type": "response.output_text.delta", "delta": "abc"})
    if delta == "abc":
        ok("stream delta extraction dict")
    else:
        fail(f"stream delta dict extraction unexpected: {delta}")
        return 1

    class _DeltaObj:
        type = "response.output_text.delta"
        delta = "xyz"

    delta_obj = panel._extract_stream_text_delta(_DeltaObj())
    if delta_obj == "xyz":
        ok("stream delta extraction object")
    else:
        fail(f"stream delta object extraction unexpected: {delta_obj}")
        return 1

    if not panel.cancel_active_run():
        ok("cancel active run idle")
    else:
        fail("cancel active run unexpectedly cancelled when idle")
        return 1

    class _WorkerStub:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    worker = _WorkerStub()
    panel._run_worker = worker
    panel._busy = True
    if panel.cancel_active_run() and worker.cancelled and not panel._busy:
        ok("cancel active run worker")
    else:
        fail("cancel active run worker behavior unexpected")
        return 1

    # Teams parity checks.
    if len(tui_app.TEAM_PRESETS) == 11:
        ok("team preset count is 11")
    else:
        fail(f"team preset count unexpected: {len(tui_app.TEAM_PRESETS)}")
        return 1

    for i, (label, composition) in enumerate(tui_app.TEAM_PRESETS):
        if len(composition) != 4:
            fail(f"team #{i + 1} does not define 4 terminals: {composition}")
            return 1
        tooltip = app._team_tooltip_text(i, label, composition)
        if (
            f"#{i + 1}:" not in tooltip
            or "T1:" not in tooltip
            or "T4:" not in tooltip
            or "Best for:" not in tooltip
        ):
            fail(f"team tooltip format invalid: {tooltip}")
            return 1
    ok("team preset layout and tooltip format")

    # Broadcast suffix parsing checks.
    msg, is_all = app._parse_broadcast_suffix("Scan target all")
    if msg == "Scan target" and is_all:
        ok("broadcast suffix parse")
    else:
        fail(f"broadcast suffix parse unexpected: msg={msg}, is_all={is_all}")
        return 1

    msg2, is_all2 = app._parse_broadcast_suffix("Scan target")
    if msg2 == "Scan target" and not is_all2:
        ok("non-broadcast suffix parse")
    else:
        fail(f"non-broadcast parse unexpected: msg={msg2}, is_all={is_all2}")
        return 1

    # Queue management checks.
    if not app._queue_broadcast_mode:
        ok("queue broadcast mode default off")
    else:
        fail("queue broadcast mode expected off by default")
        return 1

    app._toggle_queue_broadcast_mode()
    if app._queue_broadcast_mode:
        ok("queue broadcast mode toggle")
    else:
        fail("queue broadcast mode toggle failed")
        return 1

    app._add_queue_item("Scan target")
    app._add_queue_item("Check login all")
    if len(app._queue_items) == 2:
        ok("queue add items")
    else:
        fail(f"queue add unexpected size: {len(app._queue_items)}")
        return 1

    if app._queue_items[0].get("status") == "pending" and not app._queue_items[0].get("broadcast"):
        ok("queue normal item status")
    else:
        fail(f"queue normal item unexpected: {app._queue_items[0]}")
        return 1

    if app._queue_items[1].get("broadcast"):
        ok("queue explicit broadcast suffix")
    else:
        fail(f"queue broadcast suffix not detected: {app._queue_items[1]}")
        return 1

    label0 = app._queue_item_label(0, app._queue_items[0])
    label1 = app._queue_item_label(1, app._queue_items[1])
    if "[1]" in label0 and "[2]" in label1 and "[ALL]" in label1:
        ok("queue label formatting")
    else:
        fail(f"queue labels unexpected: {label0} | {label1}")
        return 1

    app._queue_selected_idx = 0
    app._delete_selected_queue_item()
    if len(app._queue_items) == 1:
        ok("queue delete selected")
    else:
        fail(f"queue delete selected unexpected size: {len(app._queue_items)}")
        return 1

    app._clear_queue()
    if len(app._queue_items) == 0:
        ok("queue clear all")
    else:
        fail(f"queue clear all unexpected size: {len(app._queue_items)}")
        return 1
    fd, temp_path = tempfile.mkstemp(prefix="tui_tool_calls_", suffix=".jsonl")
    os.close(fd)
    try:
        os.remove(temp_path)
    except Exception:
        pass
    app._tool_calls_file = temp_path
    app._tool_calls_max_bytes = 80
    app._tool_calls_max_backups = 2
    tfd, telemetry_path = tempfile.mkstemp(prefix="tui_telemetry_", suffix=".jsonl")
    os.close(tfd)
    try:
        os.remove(telemetry_path)
    except Exception:
        pass
    app._telemetry_file = telemetry_path
    app._telemetry_max_bytes = 2000
    app._telemetry_max_backups = 2
    cfd, context_path = tempfile.mkstemp(prefix="tui_context_usage_", suffix=".jsonl")
    os.close(cfd)
    try:
        os.remove(context_path)
    except Exception:
        pass
    app._context_snapshots_file = context_path
    app._context_snapshots_max_bytes = 2000
    app._context_snapshots_max_backups = 2

    # Seed small fake agents list so list_agents tool has deterministic content.
    app._available_agents = {"redteam_agent": object(), "blueteam_agent": object()}

    registry = app._build_tool_registry()
    required = {"echo", "now", "list_agents", "last_tool_call"}
    if required.issubset(set(registry.keys())):
        ok("registry contains expected tool ids")
    else:
        fail(f"registry missing expected ids: {required - set(registry.keys())}")
        return 1

    # Mode toggle logic should flip between input and command.
    initial_mode = app._inject_mode
    app._toggle_inject_mode()
    first_toggle = app._inject_mode
    app._toggle_inject_mode()
    second_toggle = app._inject_mode
    if initial_mode == "input" and first_toggle == "command" and second_toggle == "input":
        ok("inject mode toggle logic")
    else:
        fail(f"inject mode toggle unexpected: {initial_mode} -> {first_toggle} -> {second_toggle}")
        return 1

    # Persist and reload inject mode preference.
    app._inject_mode = "command"
    app._persist_inject_mode_pref()
    cfg_path = tui_app.CONFIG_FILE
    if os.path.exists(cfg_path):
        ok("inject mode preference file created")
    else:
        fail("inject mode preference file missing")
        return 1

    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        persisted = cfg.get("tools", {}).get("inject_mode")
    except Exception as exc:
        fail(f"inject mode preference read failed: {exc}")
        return 1

    if persisted == "command":
        ok("inject mode preference persisted")
    else:
        fail(f"inject mode preference unexpected value: {persisted}")
        return 1

    app2 = CAIApp(agent=None)
    loaded_pref = app2._load_inject_mode_pref()
    if loaded_pref == "command":
        ok("inject mode preference reload")
    else:
        fail(f"inject mode preference reload unexpected: {loaded_pref}")
        return 1

    echo_out = registry["echo"]["runner"]({"text": "hello"})
    if echo_out.get("output") == "hello" and echo_out.get("length") == 5:
        ok("echo tool output")
    else:
        fail(f"echo tool unexpected output: {echo_out}")
        return 1

    list_out = registry["list_agents"]["runner"]({})
    if list_out.get("count") == 2 and "redteam_agent" in list_out.get("agents", []):
        ok("list_agents tool output")
    else:
        fail(f"list_agents unexpected output: {list_out}")
        return 1

    rec = app._append_tool_call("echo", {"text": "hello"}, echo_out)
    if rec.get("call_id") == "tool-1" and len(app._tool_call_history) == 1:
        ok("append call history")
    else:
        fail(f"append history unexpected state: {rec}")
        return 1

    if os.path.exists(temp_path):
        ok("tool call history file created")
    else:
        fail("tool call history file not created")
        return 1

    loaded = app._load_tool_call_history()
    if len(loaded) == 1 and loaded[0].get("tool_id") == "echo":
        ok("tool call history reload")
    else:
        fail(f"tool call history reload unexpected: {loaded}")
        return 1

    # Trigger rotation by appending larger payloads.
    app._append_tool_call("echo", {"text": "x" * 120}, {"output": "x" * 120})
    app._append_tool_call("echo", {"text": "y" * 120}, {"output": "y" * 120})
    rotated = f"{temp_path}.1"
    if os.path.exists(rotated):
        ok("tool call history rotation created backup")
    else:
        fail("tool call history rotation did not create backup")
        return 1

    loaded_after_rotation = app._load_tool_call_history()
    if len(loaded_after_rotation) >= 2:
        ok("tool call history reload includes rotated backups")
    else:
        fail(f"rotated reload unexpectedly short: {loaded_after_rotation}")
        return 1

    # Rebuild registry so last_tool_call runner closes over updated history.
    registry = app._build_tool_registry()
    last = registry["last_tool_call"]["runner"]({})
    if last.get("has_calls") and last.get("tool_id") == "echo":
        ok("last_tool_call reflects latest history")
    else:
        fail(f"last_tool_call unexpected output: {last}")
        return 1

    # Telemetry checks: streaming run lifecycle + retrieval traces + token totals.
    class _FakeUsage:
        def __init__(self, i, o, t):
            self.input_tokens = i
            self.output_tokens = o
            self.total_tokens = t
            self.input_tokens_details = {"cached_tokens": 2}
            self.output_tokens_details = {"reasoning_tokens": 3}

    class _FakeResp:
        def __init__(self, usage):
            self.usage = usage

    class _FakeResult:
        def __init__(self):
            self.raw_responses = [_FakeResp(_FakeUsage(10, 5, 15))]

    app._telemetry_run_started(1, "one_tool_agent", "hello")
    app._telemetry_first_token(1, "one_tool_agent")
    app._telemetry_tool_called(1, "one_tool_agent", "web_search_preview", "c1", '{"q":"x"}')
    app._telemetry_tool_output(1, "one_tool_agent", "c1", "result")
    status = app._telemetry_run_finished(1, "one_tool_agent", _FakeResult(), "completed")
    if "tok" in status and "retr" in status:
        ok("telemetry status summary")
    else:
        fail(f"telemetry status summary unexpected: {status}")
        return 1

    if os.path.exists(telemetry_path):
        ok("telemetry file created")
    else:
        fail("telemetry file missing")
        return 1

    try:
        telemetry_records = []
        for p in (f"{telemetry_path}.2", f"{telemetry_path}.1", telemetry_path):
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                telemetry_records.extend([json.loads(line) for line in f if line.strip()])
    except Exception as exc:
        fail(f"telemetry read failed: {exc}")
        return 1

    events = {r.get("event") for r in telemetry_records}
    required_events = {
        "run_started",
        "first_token",
        "tool_called",
        "tool_output",
        "retrieval_called",
        "retrieval_output",
        "run_finished",
    }
    if required_events.issubset(events):
        ok("telemetry event coverage")
    else:
        fail(f"telemetry missing events: {required_events - events}")
        return 1

    snapshot = app._get_context_snapshot(1)
    if (
        snapshot
        and int(snapshot.get("used_tokens", 0)) > 0
        and int(snapshot.get("max_tokens", 0)) > 0
    ):
        ok("context snapshot created")
    else:
        fail(f"context snapshot unexpected: {snapshot}")
        return 1

    categories = (snapshot or {}).get("categories", {})
    expected_categories = {
        "system_prompt_tokens",
        "tool_definitions_tokens",
        "memory_rag_tokens",
        "user_prompt_tokens",
        "assistant_response_tokens",
        "tool_calls_tokens",
        "tool_results_tokens",
    }
    if expected_categories.issubset(set(categories.keys())):
        ok("context category buckets")
    else:
        fail(f"context categories missing: {expected_categories - set(categories.keys())}")
        return 1

    context_text = app._render_context_usage_menu_text(snapshot or {})
    if (
        "Context Usage" in context_text
        and "Category Breakdown" in context_text
        and "Free:" in context_text
        and "Legend:" in context_text
        and ">= 80%" in context_text
    ):
        ok("context menu render")
    else:
        fail(f"context menu render unexpected: {context_text}")
        return 1

    summary_line = app._context_snapshot_summary_text(snapshot or {})
    if "used" in summary_line and "last_input" in summary_line:
        ok("context summary quick action payload")
    else:
        fail(f"context summary text unexpected: {summary_line}")
        return 1

    if os.path.exists(context_path):
        ok("context snapshot file created")
    else:
        fail("context snapshot file missing")
        return 1

    provider_usage = (snapshot or {}).get("provider_usage_details", {})
    if (
        int(provider_usage.get("cached_tokens", 0)) == 2
        and int(provider_usage.get("reasoning_tokens", 0)) == 3
    ):
        ok("provider usage details attribution")
    else:
        fail(f"provider usage details unexpected: {provider_usage}")
        return 1

    summary_text = app._render_metrics_summary_text()
    if (
        "Stats" in summary_text
        and "Total Cost:" in summary_text
        and "Average cost per turn:" in summary_text
    ):
        ok("stats summary render")
    else:
        fail(f"stats summary render unexpected: {summary_text}")
        return 1

    events_text = app._render_metrics_events_text(limit=20)
    if "run_finished" in events_text:
        ok("metrics events render")
    else:
        fail(f"metrics events render unexpected: {events_text}")
        return 1

    # Trigger telemetry rotation.
    app._telemetry_max_bytes = 200
    for _ in range(20):
        app._emit_telemetry(1, "one_tool_agent", "stream_event", {"name": "tick", "pad": "x" * 80})
    if os.path.exists(f"{telemetry_path}.1"):
        ok("telemetry rotation created backup")
    else:
        fail("telemetry rotation did not create backup")
        return 1

    # Cost limit warning/pause checks.
    prev_limit = os.environ.get("CAI_PRICE_LIMIT")
    try:
        os.environ["CAI_PRICE_LIMIT"] = "0.01"
        app._ensure_term_stats(1)["cost_total"] = 0.02
        app._refresh_price_limit_state(emit_logs=False)
        if app._price_limit_paused:
            ok("cost limit pause enabled")
        else:
            fail("cost limit pause not enabled when expected")
            return 1
    finally:
        if prev_limit is None:
            os.environ.pop("CAI_PRICE_LIMIT", None)
        else:
            os.environ["CAI_PRICE_LIMIT"] = prev_limit
        app._refresh_price_limit_state(emit_logs=False)

    # Trigger context snapshot rotation.
    app._context_snapshots_max_bytes = 180
    for i in range(20):
        fake_snapshot = {
            "timestamp": f"2026-01-01T00:00:{i:02d}",
            "terminal_id": 1,
            "agent_name": "one_tool_agent",
            "model": "alias1",
            "used_tokens": 100 + i,
            "max_tokens": 1000,
            "pct_used": 10.0,
            "free_tokens": 900 - i,
            "last_input_tokens": 20,
            "categories": {
                "system_prompt_tokens": 0,
                "tool_definitions_tokens": 0,
                "memory_rag_tokens": 5,
                "user_prompt_tokens": 20,
                "assistant_response_tokens": 30,
                "tool_calls_tokens": 10,
                "tool_results_tokens": 15,
            },
        }
        app._persist_context_snapshot(fake_snapshot)
    if os.path.exists(f"{context_path}.1"):
        ok("context snapshot rotation created backup")
    else:
        fail("context snapshot rotation did not create backup")
        return 1

    try:
        tui_app.CONFIG_FILE = original_config_file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        for idx in (1, 2):
            p = f"{temp_path}.{idx}"
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(telemetry_path):
            os.remove(telemetry_path)
        for idx in (1, 2):
            p = f"{telemetry_path}.{idx}"
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(context_path):
            os.remove(context_path)
        for idx in (1, 2):
            p = f"{context_path}.{idx}"
            if os.path.exists(p):
                os.remove(p)
        cfg_path = os.path.join(work_dir, "tui_config.json")
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        if os.path.isdir(work_dir):
            os.rmdir(work_dir)
    except Exception:
        pass

    print("TUI tools smoke tests complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
