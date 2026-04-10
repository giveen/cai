from cai.repl.loop.input_handler import get_next_input


def test_get_next_input_initial_prompt(monkeypatch):
    # Ensure deterministic time delta
    monkeypatch.setattr("cai.repl.loop.input_handler.time.time", lambda: 1000.0)

    user_input, use_initial_prompt, post, ctf_init, idle_time = get_next_input(
        force_until_flag=False,
        ctf_init=1,
        use_initial_prompt=True,
        initial_prompt="start-me",
        _post_compact_input=None,
        command_completer=None,
        kb=None,
        history_file=None,
        current_text=[""],
        messages_ctf="",
        idle_time=0.0,
        idle_start_time=999.0,
    )

    assert user_input == "start-me"
    assert use_initial_prompt is False


def test_get_next_input_post_compact(monkeypatch):
    monkeypatch.setattr("cai.repl.loop.input_handler.time.time", lambda: 2000.0)

    user_input, use_initial_prompt, post, ctf_init, idle_time = get_next_input(
        force_until_flag=False,
        ctf_init=1,
        use_initial_prompt=False,
        initial_prompt=None,
        _post_compact_input="replay",
        command_completer=None,
        kb=None,
        history_file=None,
        current_text=[""],
        messages_ctf="",
        idle_time=0.0,
        idle_start_time=1999.0,
    )

    assert user_input == "replay"
    assert post is None


def test_get_next_input_ctf_mode(monkeypatch):
    monkeypatch.setattr("cai.repl.loop.input_handler.time.time", lambda: 3000.0)

    user_input, use_initial_prompt, post, ctf_init, idle_time = get_next_input(
        force_until_flag=False,
        ctf_init=0,
        use_initial_prompt=False,
        initial_prompt=None,
        _post_compact_input=None,
        command_completer=None,
        kb=None,
        history_file=None,
        current_text=[""],
        messages_ctf="ctf-run",
        idle_time=0.0,
        idle_start_time=2999.0,
    )

    assert user_input == "ctf-run"
    assert ctf_init == 1
