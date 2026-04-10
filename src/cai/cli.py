"""
This module provides a CLI interface for testing and
interacting with CAI agents.

Environment Variables
---------------------
    Required:
        N/A

    Optional:
        CTF_NAME: Name of the CTF challenge to
            run (e.g. "picoctf_static_flag")
        CTF_CHALLENGE: Specific challenge name
            within the CTF to test
        CTF_SUBNET: Network subnet for the CTF
            container (default: "192.168.3.0/24")
        CTF_IP: IP address for the CTF
            container (default: "192.168.3.100")
        CTF_INSIDE: Whether to conquer the CTF from
            within container (default: "true")

        CAI_MODEL: Model to use for agents
            (default: "alias1")
        CAI_DEBUG: Set debug output level (default: "1")
            - 0: Only tool outputs
            - 1: Verbose debug output
            - 2: CLI debug output
        CAI_BRIEF: Enable/disable brief output mode (default: "false")
        CAI_MAX_TURNS: Maximum number of turns for
            agent interactions (default: "inf")
        CAI_TRACING: Enable/disable OpenTelemetry tracing
            (default: "true"). When enabled, traces execution
            flow and agent interactions for debugging and analysis.
        CAI_AGENT_TYPE: Specify the agents to use it could take
            the value of (default: "one_tool_agent"). Use "/agent"
            command in CLI to list all available agents.
        CAI_STATE: Enable/disable stateful mode (default: "false").
            When enabled, the agent will use a state agent to keep
            track of the state of the network and the flags found.
        CAI_MEMORY: Enable/disable memory mode (default: "false")
            - episodic: use episodic memory
            - semantic: use semantic memory
            - all: use both episodic and semantic memorys
        CAI_MEMORY_ONLINE: Enable/disable online memory mode
            (default: "false")
        CAI_MEMORY_OFFLINE: Enable/disable offline memory
            (default: "false")
        CAI_ENV_CONTEXT: Add environment context, dirs and
            current env available (default: "true")
        CAI_MEMORY_ONLINE_INTERVAL: Number of turns between
            online memory updates (default: "5")
        CAI_PRICE_LIMIT: Price limit for the conversation in dollars
            (default: "1")
        CAI_SUPPORT_MODEL: Model to use for the support agent
            (default: "o3-mini")
        CAI_SUPPORT_INTERVAL: Number of turns between support agent
            executions (default: "5")
        CAI_STREAM: Enable/disable streaming output in rich panel
            (default: "false")
        CAI_TELEMETRY: Enable/disable telemetry (default: "false")
        CAI_PARALLEL: Number of parallel agent instances to run
            (default: "1"). When set to values greater than 1,
            executes multiple instances of the same agent in
            parallel and displays all results.
        CAI_GUARDRAILS: Enable/disable security guardrails for agents
            (default: "true"). When enabled, applies security guardrails
            to prevent potentially dangerous outputs and inputs. Set to
            "false" to disable all guardrail functionality.

    Extensions (only applicable if the right extension is installed):

        "report"
            CAI_REPORT: Enable/disable reporter mode. Possible values:
                - ctf (default): do a report from a ctf resolution
                - nis2: do a report for nis2
                - pentesting: do a report from a pentesting

Usage Examples:

    # Run against a CTF
    CTF_NAME="kiddoctf" CTF_CHALLENGE="02 linux ii" \
        CAI_AGENT_TYPE="one_tool_agent" CAI_MODEL="alias1" \
        CAI_TRACING="false" cai

    # Run a harder CTF
    CTF_NAME="hackableii" CAI_AGENT_TYPE="redteam_agent" \
        CTF_INSIDE="False" CAI_MODEL="alias1" \
        CAI_TRACING="false" cai

    # Run without a target in human-in-the-loop mode, generating a report
    CAI_TRACING=False CAI_REPORT=pentesting CAI_MODEL="alias1" \
        cai

    # Run with online episodic memory
    #   registers memory every 5 turns:
    #   limits the cost to 5 dollars
    CTF_NAME="hackableII" CAI_MEMORY="episodic" \
        CAI_MODEL="alias1" CAI_MEMORY_ONLINE="True" \
        CTF_INSIDE="False" CTF_HINTS="False"  \
        CAI_PRICE_LIMIT="5" cai

    # Run with custom long_term_memory interval
    # Executes memory long_term_memory every 3 turns:
    CTF_NAME="hackableII" CAI_MEMORY="episodic" \
        CAI_MODEL="alias1" CAI_MEMORY_ONLINE_INTERVAL="3" \
        CAI_MEMORY_ONLINE="False" CTF_INSIDE="False" \
        CTF_HINTS="False" cai
        
    # Run with parallel agents (3 instances)
    CTF_NAME="hackableII" CAI_AGENT_TYPE="redteam_agent" \
        CAI_MODEL="alias1" CAI_PARALLEL="3" cai
"""

from cai.bootstrap import initialize_env
import asyncio
import logging
import os
import shlex
import sys
import time

# Initialize environment early (load .env, configure warnings and logging filters)
initialize_env()

# OpenAI imports
from rich.console import Console  # noqa: E402

from cai import is_pentestperf_available  # noqa: E402

# CAI agents imports
from cai.agents import get_agent_by_name  # noqa: E402

# CAI REPL imports
from cai.repl.commands import handle_command as commands_handle_command  # noqa: E402

# Import parallel config list used to gate the parallel execution path
from cai.repl.commands.parallel import PARALLEL_CONFIGS  # noqa: E402

# Global storage for shared message histories (keyed by a unique identifier)
UNIFIED_MESSAGE_HISTORIES = {}

# CAI SDK imports
from cai.sdk.agents import set_tracing_disabled  # noqa: E402

# CAI utility imports
from cai.util import (  # noqa: E402
    fix_litellm_transcription_annotations,
    setup_ctf,
    start_idle_timer,
    stop_active_timer,
)

ctf_global = None
messages_ctf = ""
ctf_init = 1
previous_ctf_name = os.getenv("CTF_NAME", None)
if is_pentestperf_available() and os.getenv("CTF_NAME", None):
    ctf, messages_ctf = setup_ctf()
    ctf_global = ctf
    ctf_init = 0

# NOTE: This is needed when using LiteLLM Proxy Server
#
# external_client = AsyncOpenAI(
#     base_url = os.getenv('LITELLM_BASE_URL', 'http://localhost:4000'),
#     api_key=os.getenv('LITELLM_API_KEY', 'key'))
#
# set_default_openai_client(external_client)

# Global variables for timing tracking
global START_TIME
START_TIME = time.time()


# model-syncing is handled by the AgentManager.sync_models method
# ``update_agent_models_recursively`` lives in cai.repl.loop.agent_sync and is
# imported here so existing call-sites in this module keep working unchanged.
from cai.repl.loop.agent_sync import update_agent_models_recursively  # noqa: E402


def _run_cai_cli_impl(
    starting_agent,
    context_variables=None,
    max_turns=float("inf"),
    force_until_flag=False,
    initial_prompt=None,
):
    """
    Run a simple interactive CLI loop for CAI.

    Args:
        starting_agent: The initial agent to use for the conversation
        context_variables: Optional dictionary of context variables to initialize the session
        max_turns: Maximum number of interaction turns before terminating (default: infinity)
        force_until_flag: Whether to force execution until a flag is found
        initial_prompt: Optional initial prompt to execute immediately before entering interactive mode

    Returns:
        None
    """
    # Active/idle timing is tracked via cai.util's start/stop timer helpers

    agent = starting_agent
    turn_count = 0
    idle_time = 0
    # Holds a user message to replay on the next iteration without prompting
    # the user — set by auto-compact so the agent continues its current task.
    _post_compact_input: str | None = None
    # When a user interrupts execution (KeyboardInterrupt), set this flag so
    # the subsequent loop iteration will not auto-compact immediately
    # (avoids losing data when a user pauses/resumes the session).
    _skip_auto_compact_after_interrupt = False
    console = Console()
    last_model = os.getenv("CAI_MODEL", "alias1")
    last_agent_type = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")
    parallel_count = int(os.getenv("CAI_PARALLEL", "1"))
    use_initial_prompt = initial_prompt is not None

    # One-time session startup: reset trackers, build UI kit, pre-load data, display banner.
    from cai.repl.loop.session import initialize_session

    command_completer, current_text, kb, history_file, session_logger = initialize_session(
        starting_agent, console, last_agent_type
    )

    prev_max_turns = max_turns
    turn_limit_reached = False
    # Ensure this exists for KeyboardInterrupt handling before try-body sets it
    idle_start_time = 0

    while True:
        # Check if the ctf name has changed and instanciate the ctf
        global previous_ctf_name
        global ctf_global
        global messages_ctf
        global ctf_init
        if previous_ctf_name != os.getenv("CTF_NAME", None):
            if is_pentestperf_available():
                if ctf_global:
                    ctf_global.stop_ctf()
                ctf, messages_ctf = setup_ctf()
                ctf_global = ctf
                previous_ctf_name = os.getenv("CTF_NAME", None)
                ctf_init = 0
        # Check if CAI_MAX_TURNS has been updated via /config
        current_max_turns = os.getenv("CAI_MAX_TURNS", "inf")
        if current_max_turns != str(prev_max_turns):
            max_turns = float(current_max_turns)
            prev_max_turns = max_turns

            if turn_limit_reached and turn_count < max_turns:
                turn_limit_reached = False
                console.print(
                    "[green]Turn limit increased. You can now continue using CAI.[/green]"
                )

        # Check if max turns is reached
        if turn_count >= max_turns and max_turns != float("inf"):
            if not turn_limit_reached:
                turn_limit_reached = True
                console.print(
                    f"[bold red]Error: Maximum turn limit ({int(max_turns)}) reached.[/bold red]"
                )
                console.print(
                    "[yellow]You must increase the limit using the /config command: /config CAI_MAX_TURNS=<new_value>[/yellow]"
                )
                console.print(
                    "[yellow]Only CLI commands (starting with '/') will be processed until the limit is increased.[/yellow]"
                )

        try:
            # Start measuring user idle time
            start_idle_timer()

            # Capture idle start timestamp
            idle_start_time = time.time()

            # Sync model and switch agent type if environment variables changed.
            from cai.repl.loop.agent_sync import switch_agent_if_needed, sync_model

            current_model, last_model = sync_model(agent, last_model, last_agent_type)
            parallel_count = int(os.getenv("CAI_PARALLEL", "1"))
            agent, last_model, last_agent_type, _agent_switch_continue = switch_agent_if_needed(
                agent, last_model, last_agent_type, current_model, console
            )
            if _agent_switch_continue:
                continue

            # Acquire user input, update idle timing and CTF state.
            from cai.repl.loop.input_handler import get_next_input as _get_next_input

            (
                user_input,
                use_initial_prompt,
                _post_compact_input,
                ctf_init,
                idle_time,
            ) = _get_next_input(
                force_until_flag,
                ctf_init,
                use_initial_prompt,
                initial_prompt,
                _post_compact_input,
                command_completer,
                kb,
                history_file,
                current_text,
                messages_ctf,
                idle_time,
                idle_start_time,
            )

            # In parallel mode, all configured agents will run automatically
            # No agent selection menu - just run all agents

        except KeyboardInterrupt:
            # Print newline to ensure clean prompt display after interrupt
            print()

            # Mark that the user interrupted execution so we skip the next
            # automatic compaction cycle to avoid losing data.
            _skip_auto_compact_after_interrupt = True

            # Compute the most recent idle accumulation (best-effort)
            try:
                additional_idle = time.time() - idle_start_time
            except Exception:
                additional_idle = 0

            try:
                from cai.repl.ui.metrics import finalize_session

                try:
                    finalize_session(session_logger, START_TIME, idle_time + (additional_idle or 0))
                except Exception:
                    # If finalize_session fails, fall back to the simpler handler
                    try:
                        from cai.repl.ui.metrics import handle_keyboard_interrupt

                        handle_keyboard_interrupt(session_logger, console=console)
                    except Exception:
                        pass
            except Exception:
                # Last resort: attempt to use the simple keyboard interrupt handler
                try:
                    from cai.repl.ui.metrics import handle_keyboard_interrupt

                    handle_keyboard_interrupt(session_logger, console=console)
                except Exception:
                    pass
            break

        try:
            # Check if turn limit is reached and allow only CLI commands
            if (
                turn_limit_reached
                and not user_input.startswith("/")
                and not user_input.startswith("$")
            ):
                console.print(
                    "[bold red]Error: Turn limit reached. Only CLI commands are allowed.[/bold red]"
                )
                console.print(
                    "[yellow]Please use /config to increase CAI_MAX_TURNS limit.[/yellow]"
                )
                # Skip processing this input but continue the main loop
                stop_active_timer()
                start_idle_timer()
                continue

            # Check if we have parallel configurations to run
            if (
                PARALLEL_CONFIGS
                and not user_input.startswith("/")
                and not user_input.startswith("$")
            ):
                from cai.repl.loop.parallel_exec import run_parallel_configs

                run_parallel_configs(user_input, agent, console)
                turn_count += 1
                stop_active_timer()
                start_idle_timer()
                continue

            # Handle special commands
            if user_input.startswith("/") or user_input.startswith("$"):
                # Remove newlines from pasted input
                cleaned_input = user_input.strip().replace("\n", "").replace("\r", "")

                try:
                    # Parse with shell-like quoting support
                    parts = shlex.split(cleaned_input)
                except ValueError:
                    # Fallback to simple split on error
                    parts = cleaned_input.split()

                if not parts:
                    continue

                command = parts[0]
                args = parts[1:] if len(parts) > 1 else None

                # Process the command with the handler
                if commands_handle_command(command, args):
                    continue  # Command was handled, continue to next iteration

                # If command wasn't recognized, show error (skip for /shell or /s)
                if command not in ("/shell", "/s"):
                    console.print(f"[red]Command failed or unknown: {command}[/red]")
                continue

            from cai.repl.loop.response_handler import build_conversation_input

            conversation_input = build_conversation_input(agent, user_input, messages_ctf)

            # Debug trace: confirm the runner is being invoked.
            # Visible in the terminal even when the TUI is active because it
            # goes to stderr, which bypasses the Rich/prompt_toolkit buffer.
            if os.getenv("CAI_DEBUG", "1") == "2":
                import sys as _sys

                _sys.stderr.write(
                    f"[CAI DEBUG] runner invoked: agent={getattr(agent, 'name', '?')!r} "
                    f"input_len={len(str(conversation_input))}\n"
                )
                _sys.stderr.flush()

            # Process the conversation with the agent - with parallel execution if enabled
            if parallel_count > 1:
                from cai.repl.loop.parallel_exec import run_simple_parallel

                run_simple_parallel(
                    conversation_input, agent, console, last_agent_type, parallel_count
                )
            else:
                from cai.repl.loop.response_handler import run_single_response

                (
                    agent,
                    _post_compact_input,
                    _skip_auto_compact_after_interrupt,
                    _should_continue,
                ) = run_single_response(
                    agent,
                    conversation_input,
                    user_input,
                    _post_compact_input,
                    _skip_auto_compact_after_interrupt,
                    messages_ctf,
                    console,
                    session_logger,
                    parallel_count,
                    idle_time,
                    START_TIME,
                )
                if _should_continue:
                    continue

            turn_count += 1

            # Stop measuring active time and start measuring idle time again
            stop_active_timer()
            start_idle_timer()

        except KeyboardInterrupt:
            # Print newline to ensure clean prompt display after interrupt
            print()

            # Clean up any active streaming panels
            try:
                from cai.util import cleanup_all_streaming_resources

                cleanup_all_streaming_resources()
            except Exception:
                pass

            # Handle pending tool calls to prevent errors on next iteration
            try:
                from cai.util.orchestration import handle_orphaned_tool_calls

                handle_orphaned_tool_calls(agent)
            except Exception:
                pass

            # Add a small delay to allow the system to settle after interruption
            time.sleep(0.1)

            # Clear any asyncio event loop state to ensure clean restart
            try:
                # Get the current event loop if it exists
                loop = asyncio.get_event_loop()
                if loop and loop.is_running():
                    # Can't close a running loop, but we can clear pending tasks
                    try:
                        pending = asyncio.all_tasks(loop)
                    except Exception:
                        TaskType = getattr(asyncio, "Task", None)
                        if TaskType is not None and getattr(TaskType, "all_tasks", None):
                            try:
                                pending = TaskType.all_tasks(loop)
                            except Exception:
                                pending = set()
                        else:
                            pending = set()

                    for task in list(pending):
                        try:
                            task.cancel()
                        except Exception:
                            pass
            except Exception:
                pass

            # Reset the event loop policy to ensure fresh loops
            try:
                asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
            except Exception:
                pass
        except Exception as e:
            import sys
            import traceback

            # Always surface runner errors to stderr so they are visible even
            # when the TUI is active and the Rich console may be paused.
            _tb = traceback.format_exc()
            sys.stderr.write(f"[CAI ERROR] main loop exception: {e}\n{_tb}\n")
            sys.stderr.flush()

            # Also show inline when debug mode is active
            if os.getenv("CAI_DEBUG", "1") == "2":
                exc_type, exc_value, exc_traceback = sys.exc_info()
                tb_info = traceback.extract_tb(exc_traceback)
                filename, line, func, text = tb_info[-1]
                console.print(f"[bold red]Error: {str(e)}[/bold red]")
                console.print(f"[bold red]Traceback: {tb_info}[/bold red]")
            else:
                # In normal mode, also log so it ends up in the .jsonl
                logger = logging.getLogger(__name__)
                logger.error(f"Error in main loop: {str(e)}", exc_info=True)

            # Make sure we switch back to idle mode even if there's an error
            stop_active_timer()
            start_idle_timer()


def create_last_log_symlink(log_filename):
    """
    Create a symbolic link 'logs/last' pointing to the current log file.

    Args:
        log_filename: Path to the current log file
    """
    try:
        from pathlib import Path

        if not log_filename:
            return

        log_path = Path(log_filename)
        if not log_path.exists():
            return

        # Create the symlink path
        symlink_path = Path("logs/last")

        # Remove existing symlink if it exists
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()

        # Create new symlink pointing to just the filename (relative path within logs dir)
        symlink_path.symlink_to(log_path.name)

    except Exception:
        # Silently ignore errors to avoid disrupting the main flow
        pass


def main():
    # Apply litellm patch to fix the __annotations__ error
    patch_applied = fix_litellm_transcription_annotations()
    if not patch_applied:
        # Avoid using wasabi color kwarg here to keep static checkers happy
        print("Something went wrong patching LiteLLM fix_litellm_transcription_annotations")

    # Disable tracing for interactive CLI sessions. Do this at runtime
    # rather than at import time so importing this module (e.g. in tests)
    # doesn't globally disable tracing for the whole process.
    try:
        set_tracing_disabled(True)
    except Exception:
        # If tracing API isn't available for some reason, ignore.
        pass

    # Check for command-line arguments to use as initial prompt.
    # --tui flag triggers the Textual TUI and is consumed here.
    initial_prompt = None
    use_tui = "--tui" in sys.argv or os.getenv("CAI_TUI", "false").lower() not in ("", "0", "false")
    remaining_args = [a for a in sys.argv[1:] if a != "--tui"]
    if remaining_args:
        initial_prompt = remaining_args[0]

    # Detect TUI activation via env or CLI flag (--tui)
    tui_flag = (
        os.getenv("CAI_TUI", "false").lower() not in ("", "0", "false") or "--tui" in sys.argv
    )

    # Get agent type from environment variables or use default
    agent_type = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")

    # If TUI requested, try to initialize and run it (Textual if available,
    # otherwise a Rich fallback). Create the agent only if needed so the
    # TUI can reuse the agent instance for display or interaction.
    agent = None
    if tui_flag:
        try:
            agent = get_agent_by_name(agent_type, agent_id="P1")
            # Disable the CLI rich-streaming panel when running in TUI mode so
            # update_agent_streaming_content doesn't write raw deltas to stdout
            # (which would corrupt Textual's screen rendering).
            if hasattr(agent, "model") and hasattr(agent.model, "disable_rich_streaming"):
                agent.model.disable_rich_streaming = True
            try:
                from cai.tui import run_tui

                ran = run_tui(agent, initial_prompt=initial_prompt)
                # If run_tui returns False explicitly, it failed to start
                if ran is False:
                    agent = None
                else:
                    # run_tui either ran the UI (blocking) or returned truthy/None
                    return
            except Exception as e:
                print(f"Failed to start TUI ({e}), falling back to CLI.")
                agent = None
        except Exception as e:
            print(f"Failed to initialize agent for TUI ({e}), falling back to CLI.")

    # Get the agent instance by name with default ID P1 when not using TUI
    if agent is None:
        agent = get_agent_by_name(agent_type, agent_id="P1")

    # Use the switch_to_single_agent method for proper initialization
    from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

    # IMPORTANT: Always use the agent's proper name, not the agent key
    agent_name = agent.name if hasattr(agent, "name") else agent_type
    AGENT_MANAGER.switch_to_single_agent(agent, agent_name)

    # Configure model flags to work well with CLI
    if hasattr(agent, "model"):
        # Disable rich streaming in the model to avoid conflicts
        if hasattr(agent.model, "disable_rich_streaming"):
            agent.model.disable_rich_streaming = True
        # Allow final output to ensure all agent messages are shown
        if hasattr(agent.model, "suppress_final_output"):
            agent.model.suppress_final_output = False  # Changed to False to show all agent messages

    # Ensure the agent and all its handoff agents use the current model
    current_model = os.getenv("CAI_MODEL", "alias1")
    update_agent_models_recursively(agent, current_model)

    # Launch Textual TUI when requested, otherwise fall through to the REPL
    if use_tui:
        try:
            from cai.tui import run_tui

            run_tui(agent=agent, initial_prompt=initial_prompt)
            return
        except Exception as tui_err:
            console = Console()
            console.print(
                f"[yellow]TUI failed to start ({tui_err}); falling back to REPL.[/yellow]"
            )

    # Run the CLI with the selected agent and optional initial prompt
    # Delegate to the orchestration entrypoint. We define a thin wrapper
    # below so that callers (including the code above) use the refactored
    # implementation in `cai.util.orchestration` while preserving the
    # original public API `run_cai_cli`.
    run_cai_cli(agent, initial_prompt=initial_prompt)


def run_cai_cli(
    starting_agent,
    context_variables=None,
    max_turns=float("inf"),
    force_until_flag=False,
    initial_prompt=None,
):
    """Main entry point for the interactive CLI loop.

    Delegates to ``_run_cai_cli_impl`` which contains the full working
    implementation of the REPL loop.  Extracted helpers live under
    ``cai.repl.loop`` as the refactor progresses.
    """
    return _run_cai_cli_impl(
        starting_agent,
        context_variables=context_variables,
        max_turns=max_turns,
        force_until_flag=force_until_flag,
        initial_prompt=initial_prompt,
    )


if __name__ == "__main__":
    main()
