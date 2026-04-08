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

# Initialize environment early (load .env, configure warnings and logging filters)
initialize_env()
import os
import time

# OpenAI imports
from rich.console import Console  # noqa: E402

from cai import is_pentestperf_available  # noqa: E402

# CAI agents imports
from cai.agents import get_agent_by_name

# CAI REPL imports
from cai.repl.commands import (  # noqa: E402
    FuzzyCommandCompleter,
    handle_command as commands_handle_command,
)

# Add import for parallel configs at the top of the file
from cai.repl.commands.parallel import (  # noqa: E402
    PARALLEL_AGENT_INSTANCES,
    PARALLEL_CONFIGS,
    ParallelConfig,
)

# Global storage for shared message histories (keyed by a unique identifier)
UNIFIED_MESSAGE_HISTORIES = {}
from cai.repl.ui.banner import display_banner, display_quick_guide  # noqa: E402
from cai.repl.ui.keybindings import create_key_bindings  # noqa: E402
from cai.repl.ui.logging import setup_session_logging  # noqa: E402
from cai.repl.ui.prompt import get_user_input  # noqa: E402
from cai.repl.ui.toolbar import get_toolbar_with_refresh  # noqa: E402

# CAI SDK imports
from cai.sdk.agents import Runner, set_tracing_disabled  # noqa: E402
from cai.sdk.agents.exceptions import (  # noqa: E402
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
)
from cai.sdk.agents.global_usage_tracker import GLOBAL_USAGE_TRACKER  # noqa: E402
from cai.sdk.agents.items import ToolCallOutputItem  # noqa: E402
from cai.sdk.agents.models.openai_chatcompletions import (  # noqa: E402
    ContextCompactedError,
)
from cai.sdk.agents.parallel_isolation import PARALLEL_ISOLATION  # noqa: E402

# Import handled where needed to avoid circular imports
from cai.sdk.agents.run_to_jsonl import get_session_recorder  # noqa: E402
from cai.sdk.agents.stream_events import RunItemStreamEvent  # noqa: E402

# CAI utility imports
from cai.util import (  # noqa: E402
    color,
    fix_litellm_transcription_annotations,
    setup_ctf,
    start_active_timer,
    start_idle_timer,
    stop_active_timer,
    stop_idle_timer,
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


def run_cai_cli(
    starting_agent, context_variables=None, max_turns=float("inf"), force_until_flag=False, initial_prompt=None
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
    # Last raw user input captured so auto-compact replay and ContextCompactedError
    # handlers can reference it reliably even in parallel mode.
    _last_user_input: str = ""
    # When a user interrupts execution (KeyboardInterrupt), set this flag so
    # the subsequent loop iteration will not auto-compact immediately
    # (avoids losing data when a user pauses/resumes the session).
    _skip_auto_compact_after_interrupt = False
    console = Console()
    last_model = os.getenv("CAI_MODEL", "alias1")
    last_agent_type = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")
    parallel_count = int(os.getenv("CAI_PARALLEL", "1"))
    use_initial_prompt = initial_prompt is not None

    # Reset cost tracking at the start
    from cai.util import COST_TRACKER
    COST_TRACKER.reset_agent_costs()

    # Reset simple agent manager for clean start
    from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER
    AGENT_MANAGER.reset_registry()

    # Register the starting agent with AGENT_MANAGER
    starting_agent_name = getattr(starting_agent, "name", last_agent_type)
    AGENT_MANAGER.switch_to_single_agent(starting_agent, starting_agent_name)

    # Initialize command completer and key bindings
    command_completer = FuzzyCommandCompleter()
    current_text = [""]
    kb = create_key_bindings(current_text)

    # Setup session logging
    history_file = setup_session_logging()

    # Initialize session logger and display the filename
    session_logger = get_session_recorder()

    # Start global usage tracking session
    GLOBAL_USAGE_TRACKER.start_session(
        session_id=session_logger.session_id,
        agent_name=None  # Will be updated when agent is selected
    )

    # Initialize global WakeupIndex and load persisted wake-up summaries
    try:
        from cai.rag.summaries import load_summaries_for_session
        from cai.rag.wakeup_store import get_global_wakeup_index

        wakeup_idx = get_global_wakeup_index()
        # Load persisted summaries only (do not attempt regeneration here)
        try:
            count = load_summaries_for_session(
                session_id=session_logger.session_id,
                palace_texts=None,
                wakeup_index=wakeup_idx,
                regenerate_if_missing=False,
            )
        except Exception:
            count = 0

        if os.getenv("CAI_DEBUG", "1") == "2":
            print(f"Loaded {count} wakeup summaries into WakeupIndex for session {session_logger.session_id}")
    except Exception:
        # Best-effort: don't fail session startup if wakeup loading fails
        pass

    # Initialize TripleStore and run a best-effort contradiction check
    try:
        from cai.rag.triplestore_store import get_global_triplestore

        ts = get_global_triplestore()
        try:
            window_sec = int(os.getenv("CAI_TRIPLESTORE_CONTRADICTION_WINDOW_SECONDS", str(24 * 3600)))
        except Exception:
            window_sec = 24 * 3600
        try:
            contradictions = ts.detect_contradictions(window_seconds=window_sec)
            n = len(contradictions)
            if os.getenv("CAI_DEBUG", "1") == "2":
                print(f"TripleStore: detected {n} contradictions in last {window_sec} seconds")
            logging.getLogger(__name__).info("TripleStore startup contradictions=%d", n)
        except Exception:
            # Best-effort: do not fail startup for triple-store checks
            pass
    except Exception:
        # Best-effort: do not fail session startup if triplestore init fails
        pass

    # Display banner
    display_banner(console)
    print("\n")
    display_quick_guide(console)

    # Notify user if auto-compact is active so they can confirm the vars loaded.
    _sc_model_startup = os.getenv("CAI_SUPPORT_MODEL")
    _sc_interval_startup = os.getenv("CAI_SUPPORT_INTERVAL")
    if _sc_model_startup and _sc_interval_startup:
        try:
            console.print(
                f"[bold cyan]🗜  Auto-compact enabled: every {int(_sc_interval_startup)} LLM responses "
                f"using {_sc_model_startup}[/bold cyan]"
            )
        except ValueError:
            pass

    # Function to get the short name of the agent for display
    def get_agent_short_name(agent):
        if hasattr(agent, "name"):
            # Return the full agent name instead of just the first word
            return agent.name
        return "Agent"

    # Prevent the model from using its own rich streaming to avoid conflicts
    # but allow final output message to ensure all agent responses are shown
    if hasattr(agent, "model"):
        if hasattr(agent.model, "disable_rich_streaming"):
            agent.model.disable_rich_streaming = False  # Now True as the model handles streaming
        if hasattr(agent.model, "suppress_final_output"):
            agent.model.suppress_final_output = False  # Changed to False to show all agent messages

        # Set the agent name in the model for proper display in streaming panel
        if hasattr(agent.model, "set_agent_name"):
            agent.model.set_agent_name(get_agent_short_name(agent))

    prev_max_turns = max_turns
    turn_limit_reached = False

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
            import time
            idle_start_time = time.time()

            # Check if model has changed and update if needed
            current_model = os.getenv("CAI_MODEL", "alias1")
            # Check for agent-specific model override
            agent_specific_model = os.getenv(f"CAI_{last_agent_type.upper()}_MODEL")
            if agent_specific_model:
                current_model = agent_specific_model

            if current_model != last_model and hasattr(agent, "model"):
                # Delegate model synchronization to the AgentManager
                try:
                    from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                    AGENT_MANAGER.sync_models(current_model)
                except Exception:
                    # Best-effort: if manager sync fails, try a local update
                    try:
                        agent.model.model = current_model
                    except Exception:
                        pass
                last_model = current_model

            # Check if agent type has changed and recreate agent if needed
            current_agent_type = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")
            # Update parallel_count to reflect changes from /parallel command
            parallel_count = int(os.getenv("CAI_PARALLEL", "1"))


            if current_agent_type != last_agent_type:
                # Check if the /agent command already handled the switch
                if os.environ.get("CAI_AGENT_SWITCH_HANDLED") == "1":
                    os.environ["CAI_AGENT_SWITCH_HANDLED"] = "0"  # Reset flag

                    # Just get the existing agent that was already switched
                    from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                    # First try to get the strong reference if available
                    if hasattr(AGENT_MANAGER, '_current_agent_strong_ref'):
                        agent = AGENT_MANAGER._current_agent_strong_ref
                        # Clear the strong reference after using it
                        delattr(AGENT_MANAGER, '_current_agent_strong_ref')
                    else:
                        agent = AGENT_MANAGER.get_active_agent()

                    if agent:
                        last_agent_type = current_agent_type
                    else:
                        # If the agent is None (weak reference expired), recreate it
                        agent = get_agent_by_name(current_agent_type, agent_id="P1")
                        last_agent_type = current_agent_type
                        # Re-register with AGENT_MANAGER
                        agent_name = agent.name if hasattr(agent, "name") else current_agent_type
                        AGENT_MANAGER.set_active_agent(agent, agent_name, "P1")
                    continue

                try:
                    from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                    # Create the new agent instance
                    agent = get_agent_by_name(current_agent_type, agent_id="P1")
                    last_agent_type = current_agent_type

                    # Reset cost tracking for the new agent
                    from cai.util import COST_TRACKER

                    COST_TRACKER.reset_agent_costs()

                    # Use the manager to perform the single-agent switch; manager will
                    # handle any pending history transfer or extraction from the
                    # previously active agent.
                    agent_name = agent.name if hasattr(agent, "name") else current_agent_type
                    current_active_name = AGENT_MANAGER._active_agent_name

                    if current_active_name == agent_name:
                        # Already active; reuse existing reference
                        existing = AGENT_MANAGER.get_active_agent()
                        if existing:
                            agent = existing
                    else:
                        AGENT_MANAGER.switch_to_single_agent(agent, agent_name)

                    # Configure model flags and ensure model reflects manager history
                    if hasattr(agent, "model"):
                        if hasattr(agent.model, "disable_rich_streaming"):
                            agent.model.disable_rich_streaming = False
                        if hasattr(agent.model, "suppress_final_output"):
                            agent.model.suppress_final_output = False

                        # Apply agent-specific model override if present
                        agent_specific_model = os.getenv(f"CAI_{current_agent_type.upper()}_MODEL")
                        model_to_apply = agent_specific_model if agent_specific_model else current_model

                        # Delegate model synchronization to the manager
                        try:
                            AGENT_MANAGER.sync_models(model_to_apply, target_agent=agent)
                        except Exception:
                            try:
                                agent.model.model = model_to_apply
                            except Exception:
                                pass
                        last_model = model_to_apply

                        if hasattr(agent.model, "set_agent_name"):
                            agent.model.set_agent_name(get_agent_short_name(agent))

                    # Attempt to cancel stray asyncio tasks (best-effort)
                    try:
                        all_tasks = asyncio.all_tasks() if hasattr(asyncio, 'all_tasks') else asyncio.Task.all_tasks()
                        current_task = asyncio.current_task() if hasattr(asyncio, 'current_task') else asyncio.Task.current_task()
                        for task in all_tasks:
                            if task != current_task and not task.done():
                                task.cancel()
                    except Exception:
                        pass

                except Exception as e:
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Error switching agent: {str(e)}")
                    if os.getenv("CAI_DEBUG", "1") == "2":
                        console.print(f"[red]Error switching agent: {str(e)}[/red]")

            if not force_until_flag and ctf_init != 0:
                # Use initial prompt on first iteration if provided
                if use_initial_prompt:
                    user_input = initial_prompt
                    use_initial_prompt = False  # Only use it once
                elif _post_compact_input is not None:
                    # Auto-compact just ran — replay the last task so the agent
                    # continues working without waiting for human input.
                    user_input = _post_compact_input
                    _post_compact_input = None
                else:
                    # Get user input with command completion and history
                    user_input = get_user_input(
                        command_completer, kb, history_file, get_toolbar_with_refresh, current_text
                    )

            else:
                user_input = messages_ctf
                ctf_init = 1
            idle_time += time.time() - idle_start_time

            # Stop measuring user idle time and start measuring active time
            stop_idle_timer()
            start_active_timer()

            if not user_input.strip():
                user_input = "User input is empty, maybe wants to continue"  # Set a default message to continue the conversation

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
                # Use parallel configurations instead of normal processing

                # Show which agents have custom prompts
                _agents_with_prompts = [(idx, config) for idx, config in enumerate(PARALLEL_CONFIGS, 1) if config.prompt]

                # First ensure ALL parallel configs have agent instances (not just selected ones)
                # This prevents agents from disappearing from history when not selected
                from cai.agents import get_available_agents

                # Setup parallel isolation for these agents
                # (PARALLEL_ISOLATION already imported at module level)

                # Get agent IDs
                agent_ids = [config.id or f"P{idx}" for idx, config in enumerate(PARALLEL_CONFIGS, 1)]

                # Check if we already have isolated histories (e.g., from /load parallel)
                # If not, transfer the current agent's history to all parallel agents
                already_has_histories = False
                if PARALLEL_ISOLATION.is_parallel_mode():
                    # Check if at least one agent has a non-empty isolated history
                    for agent_id in agent_ids:
                        isolated_history = PARALLEL_ISOLATION.get_isolated_history(agent_id)
                        if isolated_history:
                            already_has_histories = True
                            break

                if not already_has_histories:
                    # Get the current agent's history to transfer
                    current_history = []
                    if hasattr(agent, 'model') and hasattr(agent.model, 'message_history'):
                        current_history = agent.model.message_history
                    elif hasattr(agent, 'name'):
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER
                        current_history = AGENT_MANAGER.get_message_history(agent.name)

                    # Check if we should transfer history to all agents or just the first one
                    # Pattern 17 (Red/Blue team with different contexts) should only transfer to P1
                    transfer_to_all = True

                    # Check if this is a pattern that requires different contexts
                    # This is typically pattern 17 or similar patterns with "different contexts" in the description
                    pattern_description = os.getenv("CAI_PATTERN_DESCRIPTION", "")
                    if "different contexts" in pattern_description.lower():
                        transfer_to_all = False

                    if transfer_to_all:
                        # Transfer to parallel mode - creates isolated copies for each agent
                        PARALLEL_ISOLATION.transfer_to_parallel(current_history, len(PARALLEL_CONFIGS), agent_ids)
                    else:
                        # Only transfer to the first agent (P1)
                        PARALLEL_ISOLATION._parallel_mode = True
                        if current_history and agent_ids:
                            # Clear any existing histories first
                            PARALLEL_ISOLATION.clear_all_histories()
                            # Set history only for the first agent
                            PARALLEL_ISOLATION.replace_isolated_history(agent_ids[0], current_history.copy())
                            # Initialize empty histories for other agents
                            for agent_id in agent_ids[1:]:
                                PARALLEL_ISOLATION.replace_isolated_history(agent_id, [])
                else:
                    # Already have isolated histories, just ensure we're in parallel mode
                    PARALLEL_ISOLATION._parallel_mode = True

                for idx, config in enumerate(PARALLEL_CONFIGS, 1):
                    instance_key = (config.agent_name, idx)
                    if instance_key not in PARALLEL_AGENT_INSTANCES:
                        # Create instance for this config
                        base_agent = get_available_agents().get(config.agent_name.lower())
                        if base_agent:
                            agent_display_name = getattr(base_agent, "name", config.agent_name)
                            custom_name = f"{agent_display_name} #{idx}"

                            # Determine model
                            model_to_use = config.model or os.getenv("CAI_MODEL", "alias1")

                            # Create and store the instance
                            # No shared_message_history - each agent gets its own isolated copy
                            instance_agent = get_agent_by_name(
                                config.agent_name, custom_name=custom_name, model_override=model_to_use,
                                agent_id=config.id
                            )
                            PARALLEL_AGENT_INSTANCES[instance_key] = instance_agent

                # Build conversation history context before parallel execution
                # Each agent will get its own isolated history to prevent mixing


                async def run_agent_instance(
                    config: ParallelConfig, input_text: str
                ):
                    """Run a single agent instance with its own configuration."""
                    instance_agent = None
                    agent_id = None
                    try:
                        # Get instance number based on position in PARALLEL_CONFIGS
                        # Use all PARALLEL_CONFIGS to ensure consistent numbering
                        instance_number = PARALLEL_CONFIGS.index(config) + 1
                        agent_id = config.id or f"P{instance_number}"

                        # Get the existing instance from PARALLEL_AGENT_INSTANCES
                        instance_key = (config.agent_name, instance_number)
                        instance_agent = PARALLEL_AGENT_INSTANCES.get(instance_key)


                        if not instance_agent:
                            # Fallback: create instance if not found (shouldn't happen normally)
                            from cai.agents import get_available_agents
                            from cai.agents.patterns import get_pattern

                            # Check if this is a pattern
                            agent_display_name = None
                            actual_agent_name = config.agent_name

                            if config.agent_name.endswith("_pattern"):
                                # This is a pattern, get the entry agent
                                pattern = get_pattern(config.agent_name)
                                if pattern and hasattr(pattern, 'entry_agent'):
                                    agent_display_name = getattr(pattern.entry_agent, "name", config.agent_name)
                                    # For patterns, we create the pattern itself, not individual agents
                                    actual_agent_name = config.agent_name
                            else:
                                base_agent = get_available_agents().get(config.agent_name.lower())
                                agent_display_name = base_agent.name if base_agent else config.agent_name

                            # For display, we don't add instance number to pattern entry agents
                            # since they already have unique names like "Red team manager"
                            if not config.agent_name.endswith("_pattern"):
                                custom_name = f"{agent_display_name} #{instance_number}"
                            else:
                                custom_name = agent_display_name

                            # Determine which model to use
                            model_to_use = config.model or os.getenv("CAI_MODEL", "alias1")

                            # Create agent instance with the determined model
                            # Each agent gets its own isolated history from PARALLEL_ISOLATION
                            instance_agent = get_agent_by_name(
                                actual_agent_name, custom_name=custom_name, model_override=model_to_use,
                                agent_id=config.id
                            )

                            # Store a strong reference to prevent garbage collection
                            PARALLEL_AGENT_INSTANCES[instance_key] = instance_agent

                        # Register the agent with AGENT_MANAGER for parallel mode
                        # This ensures it shows up in /history
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER
                        agent_display_name = getattr(instance_agent, 'name', config.agent_name)
                        AGENT_MANAGER.set_parallel_agent(agent_id, instance_agent, agent_display_name)

                        # Ensure the model is properly set for the agent and all handoff agents
                        model_to_use = config.model or os.getenv("CAI_MODEL", "alias1")
                        if model_to_use:
                            update_agent_models_recursively(instance_agent, model_to_use)

                        # For parallel agents, the history is already loaded in the model instance
                        # Check if there's a custom prompt for this config
                        if config.prompt:
                            # Use the custom prompt instead of regular user input
                            instance_input = config.prompt
                        else:
                            # Just pass the user input as a string
                            instance_input = input_text

                        # Run the agent with its own isolated context
                        result = await Runner.run(instance_agent, instance_input)

                        # Clean up any streaming resources created by this agent's tools
                        try:
                            from cai.util import cli_print_tool_output, finish_tool_streaming

                            # In parallel mode, we need to update the final status of panels
                            if hasattr(cli_print_tool_output, "_streaming_sessions"):
                                agent_display_name = getattr(instance_agent, 'name', config.agent_name)

                                # Find sessions belonging to this agent
                                for session_id, session_info in list(cli_print_tool_output._streaming_sessions.items()):
                                    if (session_info.get("agent_name") == agent_display_name and
                                        not session_info.get("is_complete", False)):
                                        # Properly finish the streaming session
                                        finish_tool_streaming(
                                            tool_name=session_info.get("tool_name", "unknown"),
                                            args=session_info.get("args", {}),
                                            output=session_info.get("current_output", "Tool execution completed"),
                                            call_id=session_id,
                                            execution_info={
                                                "status": "completed",
                                                "is_final": True
                                            },
                                            token_info={
                                                "agent_name": agent_display_name,
                                                "agent_id": getattr(instance_agent.model, "agent_id", None) if hasattr(instance_agent, 'model') else None
                                            }
                                        )

                        except Exception:
                            # Silently ignore cleanup errors
                            pass

                        # Save the agent's history after successful completion
                        if instance_agent and agent_id:
                            if hasattr(instance_agent, 'model') and hasattr(instance_agent.model, 'message_history'):
                                PARALLEL_ISOLATION.replace_isolated_history(agent_id, instance_agent.model.message_history)

                        return (config, result)
                    except asyncio.CancelledError:
                        # Task was cancelled (e.g., by Ctrl+C)
                        # Clean up any streaming resources before propagating cancellation
                        try:
                            from cai.util import cleanup_agent_streaming_resources

                            # Clean up streaming sessions for this specific agent
                            if instance_agent:
                                agent_display_name = getattr(instance_agent, 'name', config.agent_name)
                                cleanup_agent_streaming_resources(agent_display_name)
                        except Exception:
                            pass

                        # Save the agent's history before propagating the cancellation
                        if instance_agent and agent_id:
                            if hasattr(instance_agent, 'model') and hasattr(instance_agent.model, 'message_history'):
                                PARALLEL_ISOLATION.replace_isolated_history(agent_id, instance_agent.model.message_history)
                        raise
                    except Exception as e:
                        # Clean up any streaming resources before handling exception
                        try:
                            from cai.util import cleanup_agent_streaming_resources

                            # Clean up streaming sessions for this specific agent
                            if instance_agent:
                                agent_display_name = getattr(instance_agent, 'name', config.agent_name)
                                cleanup_agent_streaming_resources(agent_display_name)
                        except Exception:
                            pass

                        # Also save history on other exceptions
                        if instance_agent and agent_id:
                            if hasattr(instance_agent, 'model') and hasattr(instance_agent.model, 'message_history'):
                                PARALLEL_ISOLATION.replace_isolated_history(agent_id, instance_agent.model.message_history)

                        # Log error details for debugging
                        logger = logging.getLogger(__name__)
                        error_details = f"Error in {config.agent_name}"
                        if config.model:
                            error_details += f" (model: {config.model})"
                        error_details += f": {str(e)}"
                        logger.error(error_details, exc_info=True)

                        # Only show error in debug mode
                        if os.getenv("CAI_DEBUG", "1") == "2":
                            console.print(f"[bold red]{error_details}[/bold red]")
                        return (config, None)

                # Delegate parallel orchestration to the parallel_isolation helpers
                from cai.sdk.agents.parallel_isolation import (
                    run_parallel_agents as _run_parallel_agents,
                    save_parallel_histories as _save_parallel_histories,
                )
                from cai.sdk.agents.shutdown_coordinator import SHUTDOWN_COORDINATOR

                try:
                    results = asyncio.run(_run_parallel_agents(PARALLEL_CONFIGS, user_input, run_agent_instance))
                except KeyboardInterrupt:
                    # Best-effort: save parallel histories and attempt coordinated shutdown
                    try:
                        _save_parallel_histories(PARALLEL_CONFIGS, PARALLEL_AGENT_INSTANCES)
                    except Exception:
                        pass

                    try:
                        targets = os.getenv("CAI_SHUTDOWN_TARGETS", "")
                        targets_list = [t.strip() for t in targets.split(",") if t.strip()]
                        SHUTDOWN_COORDINATOR.shutdown(sigterm_targets=targets_list if targets_list else None)
                    except Exception:
                        pass

                    # Re-raise to trigger outer handlers
                    raise

                turn_count += 1
                stop_active_timer()
                start_idle_timer()
                continue

            # Handle special commands
            if user_input.startswith("/") or user_input.startswith("$"):
                # Remove newlines from pasted input
                cleaned_input = user_input.strip().replace('\n', '').replace('\r', '')

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
            from rich.text import Text

            log_text = Text(
                f"Log file: {session_logger.filename}",
                style="yellow on black",
            )
            console.print(log_text)

            # Build conversation context from previous turns to give the
            # model short-term memory. We only keep messages that have plain
            # text content and ignore tool call entries to prevent schema
            # mismatches when converting to OpenAI chat format.
            history_context = []
            # Use the agent's model's message history directly instead of AGENT_MANAGER
            # This ensures compaction actually clears the history
            if hasattr(agent, 'model') and hasattr(agent.model, 'message_history'):
                for msg in agent.model.message_history:
                    role = msg.get("role")
                    content = msg.get("content")
                    tool_calls = msg.get("tool_calls")

                    if role == "user":
                        history_context.append({"role": "user", "content": content or ""})
                    elif role == "system":
                        history_context.append({"role": "system", "content": content or ""})
                    elif role == "assistant":
                        if tool_calls:
                            history_context.append(
                                {
                                    "role": "assistant",
                                    "content": content,  # Can be None
                                    "tool_calls": tool_calls,
                                }
                            )
                        elif content is not None:
                            history_context.append({"role": "assistant", "content": content})
                        elif (
                            content is None and not tool_calls
                        ):  # Explicitly handle empty assistant message
                            history_context.append({"role": "assistant", "content": None})
                    elif role == "tool":
                        history_context.append(
                            {
                                "role": "tool",
                                "tool_call_id": msg.get("tool_call_id"),
                                "content": msg.get("content"),  # Tool output
                            }
                        )

            # Fix message list structure BEFORE sending to the model to prevent errors
            try:
                from cai.util import fix_message_list

                history_context = fix_message_list(history_context)
            except Exception:
                pass

            # Append the current user input as the last message in the list.
            conversation_input: list | str
            if history_context:
                history_context.append({"role": "user", "content": user_input})
                conversation_input = history_context
            else:
                conversation_input = messages_ctf + user_input

            # Process the conversation with the agent - with parallel execution if enabled
            if parallel_count > 1:
                # Parallel execution mode (always non-streaming)
                async def _run_simple_parallel_agent(instance_number, conversation_context):
                    """Run a single agent instance with its own complete context"""
                    try:
                        # Create a fresh agent instance with unique name to ensure complete isolation
                        from cai.agents import get_available_agents

                        base_agent = get_available_agents().get(last_agent_type.lower())
                        agent_display_name = base_agent.name if base_agent else last_agent_type
                        custom_name = f"{agent_display_name} #{instance_number + 1}"
                        instance_agent = get_agent_by_name(last_agent_type, custom_name=custom_name, agent_id=f"P{instance_number + 1}")

                        # Configure agent instance to match main agent settings
                        if hasattr(instance_agent, "model") and hasattr(agent, "model"):
                            if hasattr(instance_agent.model, "model") and hasattr(
                                agent.model, "model"
                            ):
                                # Check for instance-specific model override first
                                instance_specific_model = os.getenv(
                                    f"CAI_{last_agent_type.upper()}_{instance_number + 1}_MODEL"
                                )

                                if instance_specific_model:
                                    # Use instance-specific model (e.g., CAI_BUG_BOUNTER_1_MODEL)
                                    model_to_use = instance_specific_model
                                else:
                                    # Check for agent-specific model override
                                    agent_specific_model = os.getenv(
                                        f"CAI_{last_agent_type.upper()}_MODEL"
                                    )
                                    model_to_use = (
                                        agent_specific_model
                                        if agent_specific_model
                                        else agent.model.model
                                    )

                                update_agent_models_recursively(instance_agent, model_to_use)

                        # Use the full conversation context including history
                        instance_input = conversation_context

                        # Run the agent with its own isolated context
                        result = await Runner.run(instance_agent, instance_input)

                        return (instance_number, result)
                    except Exception as e:
                        # Log error for debugging
                        logger = logging.getLogger(__name__)
                        logger.error(f"Error in instance {instance_number}: {str(e)}", exc_info=True)

                        # Only show error in debug mode
                        if os.getenv("CAI_DEBUG", "1") == "2":
                            console.print(
                                f"[bold red]Error in instance {instance_number}: {str(e)}[/bold red]"
                            )
                        return (instance_number, None)

                async def process_parallel_responses():
                    """Process multiple parallel agent executions"""
                    # Create tasks for each instance
                    tasks = [
                        _run_simple_parallel_agent(i, conversation_input) for i in range(parallel_count)
                    ]

                    # Wait for all to complete, no matter if some fail
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Filter out exceptions and failed results
                    valid_results = []
                    for result in results:
                        if isinstance(result, tuple) and len(result) == 2:
                            idx, res = result
                            if res is not None and not isinstance(res, Exception):
                                valid_results.append((idx, res))

                    return valid_results

                # Execute all parallel instances
                results = asyncio.run(process_parallel_responses())

                # Print summary info about the results

                # Display the results
                for idx, result in results:
                    if result and hasattr(result, "final_output") and result.final_output:
                        # Add to main message history for context
                        agent.model.add_to_message_history(
                            {"role": "assistant", "content": f"{result.final_output}"}
                        )
            else:
                # Capture user_input before runner calls so ContextCompactedError
                # handlers can reference it even on the very first iteration.
                _last_user_input = user_input if isinstance(user_input, str) else ""

                # Disable streaming by default, unless specifically enabled
                cai_stream = os.getenv("CAI_STREAM", "false")
                # Handle empty string or None values
                if not cai_stream or cai_stream.strip() == "":
                    cai_stream = "false"
                stream = cai_stream.lower() == "true"

                # Single agent execution (original behavior)
                if stream:

                    async def process_streamed_response(agent, conversation_input):
                        tool_calls_seen = {}  # Track tool calls by their ID
                        tool_results_seen = set()  # Track tool results by call_id
                        result = None
                        stream_iterator = None

                        try:
                            result = Runner.run_streamed(agent, conversation_input)
                            stream_iterator = result.stream_events()

                            # Consume events so the async generator is executed.
                            async for event in stream_iterator:
                                if isinstance(event, RunItemStreamEvent):
                                    if event.name == "tool_called":
                                        # Track tool calls that were issued
                                        if hasattr(event.item, 'raw_item'):
                                            # For ToolCallItem, raw_item is a ResponseFunctionToolCall (or similar)
                                            # which has a direct call_id attribute
                                            call_id = getattr(event.item.raw_item, 'call_id', None)
                                            if call_id:
                                                tool_calls_seen[call_id] = event.item
                                    elif event.name == "tool_output":
                                        # Ensure item is a ToolCallOutputItem before accessing attributes
                                        if isinstance(event.item, ToolCallOutputItem):
                                            call_id = event.item.raw_item["call_id"]
                                            tool_results_seen.add(call_id)
                                            tool_msg = {
                                                "role": "tool",
                                                "tool_call_id": call_id,
                                                "content": event.item.output,
                                            }
                                            agent.model.add_to_message_history(tool_msg)

                            return result
                        except OutputGuardrailTripwireTriggered:
                            # Handle guardrail exception specifically - MUST come before broad Exception handler
                            # Clean up streaming display before showing error
                            try:
                                from cai.util import cleanup_all_streaming_resources
                                cleanup_all_streaming_resources()
                            except Exception:
                                pass

                            # Clean up the async generator
                            if stream_iterator is not None:
                                try:
                                    await stream_iterator.aclose()
                                except Exception:
                                    pass

                            # Clean up the result object if it has cleanup methods
                            if result is not None and hasattr(result, '_cleanup_tasks'):
                                try:
                                    result._cleanup_tasks()
                                except Exception:
                                    pass

                            # Re-raise to be caught by outer handler which shows user-friendly message
                            raise
                        except (KeyboardInterrupt, asyncio.CancelledError) as e:
                            # Handle interruption specifically

                            # Clean up the async generator
                            if stream_iterator is not None:
                                try:
                                    await stream_iterator.aclose()
                                except Exception:
                                    pass

                            # Clean up the result object if it has cleanup methods
                            if result is not None and hasattr(result, '_cleanup_tasks'):
                                try:
                                    result._cleanup_tasks()
                                except Exception:
                                    pass

                            # Add synthetic results for any tool calls that don't have results
                            try:
                                for call_id, tool_item in tool_calls_seen.items():
                                    if call_id not in tool_results_seen:
                                        # This tool was called but no result was received
                                        synthetic_msg = {
                                            "role": "tool",
                                            "tool_call_id": call_id,
                                            "content": "Tool execution interrupted"
                                        }
                                        agent.model.add_to_message_history(synthetic_msg)
                            except Exception:
                                # Silently ignore cleanup errors during interrupt
                                pass

                            raise e
                        except ContextCompactedError:
                            # Propagate so the outer try block can handle the restart.
                            raise
                        except Exception as e:
                            # Clean up on any other exception
                            if stream_iterator is not None:
                                try:
                                    await stream_iterator.aclose()
                                except Exception:
                                    pass

                            if result is not None and hasattr(result, '_cleanup_tasks'):
                                try:
                                    result._cleanup_tasks()
                                except Exception:
                                    pass

                            # Re-raise OutputGuardrailTripwireTriggered to be handled by outer handler
                            if isinstance(e, OutputGuardrailTripwireTriggered):
                                raise

                            # Log error for debugging (non-guardrail exceptions)
                            logger = logging.getLogger(__name__)
                            logger.error(f"Error occurred during streaming: {str(e)}", exc_info=True)

                            # Only show error details in debug mode
                            if os.getenv("CAI_DEBUG", "1") == "2":
                                import traceback
                                tb = traceback.format_exc()
                                print(f"\n[Error occurred during streaming: {str(e)}]\nLocation: {tb}")
                            return None

                    try:
                        asyncio.run(process_streamed_response(agent, conversation_input))
                    except ContextCompactedError:
                        # Auto-compact fired mid-runner; restart with fresh context.
                        _base = _last_user_input or "Continue the current task."
                        _post_compact_input = (
                            f"{_base}\n\n"
                            "IMPORTANT: Your context window was just compacted. "
                            "Your session memory is already loaded above. "
                            "Review the 'Exhausted Approaches' section in your memory and "
                            "DO NOT repeat any technique, command, URL, port scan, or login "
                            "attempt already listed there. "
                            "Pick up exactly where you left off using only NEW approaches."
                        )
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER as _AM
                        _reloaded = _AM.get_active_agent()
                        if _reloaded is not None:
                            agent = _reloaded
                        console.print(
                            "[bold green]✓ Context window reset — resuming task[/bold green]\n"
                        )
                        continue
                    except OutputGuardrailTripwireTriggered as e:
                        # Display a user-friendly warning instead of crashing (streaming mode)
                        guardrail_name = e.guardrail_result.guardrail.get_name()
                        reason = e.guardrail_result.output.output_info.get("reason", "Security policy violation")

                        # Use red color for the warning message
                        print("\n\033[91m🛡️  SECURITY GUARDRAIL TRIGGERED\033[0m")
                        print(f"\033[91mGuardrail: {guardrail_name}\033[0m")
                        print(f"\033[91mReason: {reason}\033[0m")
                        print("\033[93mThe agent's output was blocked for security reasons.\033[0m")
                        print("\033[96mYou can continue the conversation with a different request.\033[0m\n")

                        # Continue the conversation loop instead of crashing
                        continue
                    except KeyboardInterrupt:
                        # This will catch the re-raised KeyboardInterrupt from process_streamed_response
                        # The cleanup will happen in the outer try-except block
                        raise
                    except RuntimeError as e:
                        # Handle event loop issues gracefully
                        if "This event loop is already running" in str(e) or "Cannot close a running event loop" in str(e):
                            # Try to recover by creating a new event loop
                            import sys
                            if sys.platform.startswith('win'):
                                # Windows specific event loop policy
                                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                            else:
                                # Unix/Linux/Mac
                                asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

                            # Create a fresh event loop
                            new_loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(new_loop)
                            try:
                                new_loop.run_until_complete(process_streamed_response(agent, conversation_input))
                            except OutputGuardrailTripwireTriggered as e:
                                # Display a user-friendly warning instead of crashing (new event loop)
                                guardrail_name = e.guardrail_result.guardrail.get_name()
                                reason = e.guardrail_result.output.output_info.get("reason", "Security policy violation")

                                # Use red color for the warning message
                                print("\n\033[91m🛡️  SECURITY GUARDRAIL TRIGGERED\033[0m")
                                print(f"\033[91mGuardrail: {guardrail_name}\033[0m")
                                print(f"\033[91mReason: {reason}\033[0m")
                                print("\033[93mThe agent's output was blocked for security reasons.\033[0m")
                                print("\033[96mYou can continue the conversation with a different request.\033[0m\n")

                                # Close the loop and continue the conversation loop
                                new_loop.close()
                                continue
                            finally:
                                if not new_loop.is_closed():
                                    new_loop.close()
                        else:
                            raise
                else:
                    # Use non-streamed response
                    try:
                        response = asyncio.run(Runner.run(agent, conversation_input))
                    except ContextCompactedError:
                        # Auto-compact fired mid-runner; restart with fresh context.
                        _base = _last_user_input or "Continue the current task."
                        _post_compact_input = (
                            f"{_base}\n\n"
                            "IMPORTANT: Your context window was just compacted. "
                            "Your session memory is already loaded above. "
                            "Review the 'Exhausted Approaches' section in your memory and "
                            "DO NOT repeat any technique, command, URL, port scan, or login "
                            "attempt already listed there. "
                            "Pick up exactly where you left off using only NEW approaches."
                        )
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER as _AM
                        _reloaded = _AM.get_active_agent()
                        if _reloaded is not None:
                            agent = _reloaded
                        console.print(
                            "[bold green]✓ Context window reset — resuming task[/bold green]\n"
                        )
                        continue
                    except InputGuardrailTripwireTriggered as e:
                        # Display a user-friendly warning for input guardrails
                        reason = "Potential security threat detected in input"
                        if hasattr(e, 'guardrail_result') and e.guardrail_result:
                            if hasattr(e.guardrail_result, 'output') and e.guardrail_result.output:
                                reason = e.guardrail_result.output.output_info.get("reason", reason)

                        # Use red color for the warning message
                        print("\n\033[91m🛡️  INPUT SECURITY GUARDRAIL TRIGGERED\033[0m")
                        print(f"\033[91mReason: {reason}\033[0m")
                        print("\033[93mYour input was blocked for security reasons.\033[0m")

                        # Check if this is likely due to conversation history
                        if "base64" in reason.lower() or "pattern" in reason.lower():
                            print("\n\033[96mThis may be due to malicious content in the conversation history.\033[0m")
                            print("\033[96mOptions:\033[0m")
                            print("  1. Type \033[92m/clear\033[0m to clear the conversation history")
                            print("  2. Type \033[92m/config set 26 false\033[0m to temporarily disable guardrails")
                            print("  3. Type \033[92m/exit\033[0m to exit CAI")
                        else:
                            print("\033[96mPlease rephrase your request or try a different approach.\033[0m\n")

                        # Continue the conversation loop instead of crashing
                        continue
                    except OutputGuardrailTripwireTriggered as e:
                        # Display a user-friendly warning instead of crashing
                        guardrail_name = e.guardrail_result.guardrail.get_name()
                        reason = e.guardrail_result.output.output_info.get("reason", "Security policy violation")

                        # Use red color for the warning message
                        print("\n\033[91m🛡️  SECURITY GUARDRAIL TRIGGERED\033[0m")
                        print(f"\033[91mGuardrail: {guardrail_name}\033[0m")
                        print(f"\033[91mReason: {reason}\033[0m")
                        print("\033[93mThe agent's output was blocked for security reasons.\033[0m")
                        print("\033[96mYou can continue the conversation with a different request.\033[0m\n")

                        # Continue the conversation loop instead of crashing
                        continue

                    # En modo no-streaming, procesamos SOLO los tool outputs de response.new_items
                    # Los tool calls (assistant messages) ya se añaden correctamente en openai_chatcompletions.py
                    for item in response.new_items:
                        # Handle ONLY tool call output items (tool results)
                        if isinstance(item, ToolCallOutputItem):
                            tool_call_id = item.raw_item["call_id"]

                            # Verificar si ya existe este tool output en message_history para evitar duplicación
                            tool_msg_exists = any(
                                msg.get("role") == "tool"
                                and msg.get("tool_call_id") == tool_call_id
                                for msg in agent.model.message_history
                            )

                            if not tool_msg_exists:
                                # Añadir solo el tool output al message_history
                                tool_msg = {
                                    "role": "tool",
                                    "tool_call_id": tool_call_id,
                                    "content": item.output,
                                }
                                agent.model.add_to_message_history(tool_msg)

                # Post-turn orchestration: centralize message fixes and auto-compact
                try:
                    from cai.util.orchestration import handle_post_turn

                    agent, _post_compact_input, _skip_auto_compact_after_interrupt = handle_post_turn(
                        agent,
                        console,
                        _last_user_input,
                        _post_compact_input,
                        _skip_auto_compact_after_interrupt,
                        parallel_count,
                        session_logger=session_logger,
                        start_time=START_TIME,
                        idle_time=idle_time,
                    )
                except Exception:
                    # Best-effort: do not allow orchestration errors to break the loop
                    pass

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
            import time
            time.sleep(0.1)

            # Clear any asyncio event loop state to ensure clean restart
            try:
                # Get the current event loop if it exists
                loop = asyncio.get_event_loop()
                if loop and loop.is_running():
                    # Can't close a running loop, but we can clear pending tasks
                    pending = asyncio.all_tasks(loop) if hasattr(asyncio, 'all_tasks') else asyncio.Task.all_tasks(loop)
                    for task in pending:
                        task.cancel()
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

            # Only show detailed errors in debug mode
            if os.getenv("CAI_DEBUG", "1") == "2":
                exc_type, exc_value, exc_traceback = sys.exc_info()
                tb_info = traceback.extract_tb(exc_traceback)
                filename, line, func, text = tb_info[-1]
                console.print(f"[bold red]Error: {str(e)}[/bold red]")
                console.print(f"[bold red]Traceback: {tb_info}[/bold red]")
            else:
                # In normal mode, just log the error
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
        print(
            color(
                "Something went wrong patching LiteLLM fix_litellm_transcription_annotations",
                color="red",
            )
        )

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
    tui_flag = os.getenv("CAI_TUI", "false").lower() not in ("", "0", "false") or "--tui" in sys.argv

    # Get agent type from environment variables or use default
    agent_type = os.getenv("CAI_AGENT_TYPE", "one_tool_agent")

    # If TUI requested, try to initialize and run it (Textual if available,
    # otherwise a Rich fallback). Create the agent only if needed so the
    # TUI can reuse the agent instance for display or interaction.
    agent = None
    if tui_flag:
        try:
            agent = get_agent_by_name(agent_type, agent_id="P1")
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


def run_cai_cli(starting_agent, context_variables=None, max_turns=float("inf"), force_until_flag=False, initial_prompt=None):
    """Thin wrapper delegating to cai.util.orchestration.start_cli_loop.

    This keeps the public API stable while the heavy implementation lives
    in `cai.util.orchestration`.
    """
    try:
        from cai.util.orchestration import start_cli_loop
    except Exception:
        # Best-effort fallback to the local implementation if import fails
        # (e.g., during partial refactor). Import from cai.util for backward
        # compatibility.
        try:
            from cai.util import start_cli_loop  # type: ignore
        except Exception:
            raise

    return start_cli_loop(
        starting_agent,
        context_variables=context_variables,
        max_turns=max_turns,
        force_until_flag=force_until_flag,
        initial_prompt=initial_prompt,
    )


if __name__ == "__main__":
    main()
