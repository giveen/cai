# Sidebar Features

> **⚡ CAI-Pro Exclusive Feature**  
> The Terminal User Interface (TUI) is available exclusively in **CAI-Pro**. To access this feature and unlock advanced multi-agent workflows, visit [Alias Robotics](https://aliasrobotics.com/cybersecurityai.php) for more information.

---

The CAI TUI sidebar is a powerful vertical panel that provides quick access to essential features and information. It can be toggled on/off to maximize screen space:

- **Toggle button**: Click the sidebar toggle button in the top bar
- **Keyboard shortcut**: Press `Ctrl+S` to show/hide the sidebar

When hidden, the sidebar collapses completely, giving you full width for terminal content. Toggle it back to access agents, queue, sessions, config, and tools.

---

## Overview

The current sidebar is organized into six main tabs:

1. **Agents** - Agent selection and team preset shortcuts
2. **Queue** - Prompt queue management
3. **Sessions** - Session file browse/open/resume/export/rename/delete
4. **Config** - Provider/model/env/config management screens
5. **Tools** - Run, inspect, replay, and inject tool output
6. **Stats** - Live cost, token, latency, and telemetry summaries

---

## Tools Tab

The **Tools** tab is a TUI-native panel for quickly exercising tools without leaving the active terminal context.

### What You Can Do

- **Run**: Execute the selected tool with JSON input.
- **Inspect**: Review tool description and schema.
- **Replay**: Re-run a previous tool call with editable JSON args.
- **Inject**: Inject selected tool output either as terminal input or immediate command dispatch.

### Current Tool Calls Workflow

1. Pick a tool from the list.
2. Press **Run** and provide JSON arguments.
3. Inspect output in the active terminal log.
4. Select a history entry to **Replay** or **Inject**.

When replaying, the previous args are pre-filled so you can adjust and re-run quickly.

When injecting, choose mode:

- `input`: place payload in the active terminal input box
- `command`: dispatch payload immediately as a terminal command/message

You can switch this with the **Mode** button in the Tools action row.

### Call History

The panel tracks recent calls and shows:

- Tool call id
- Tool id
- Timestamp
- Replay marker

This makes it easy to inspect prior calls and continue workflows from any output.

### Persistence

Tool call history is persisted to:

- `logs/tui_tool_calls.jsonl`

When the TUI starts, this history is loaded and shown in the Tools tab.

History rotation keeps file growth bounded:

- active file rotates when it exceeds approximately 2 MB
- backups are kept as `logs/tui_tool_calls.jsonl.1` and `.2`

### Active Agent Tool Mapping

The Tools tab registry includes:

- Built-in helper tools (`echo`, `now`, `list_agents`, `last_tool_call`)
- Function tools from the currently active terminal's agent

Hosted/non-function tools are currently inspect-only in this panel.

### Metrics & Tracing Telemetry

The TUI now captures lightweight telemetry for:

- Streaming lifecycle events
- Tool-call start/output traces with durations
- Retrieval-oriented calls (search/RAG/MCP-style tools)
- Per-run token and latency summaries

Telemetry is persisted in:

- `logs/tui_telemetry.jsonl`

with rotation backups:

- `logs/tui_telemetry.jsonl.1`
- `logs/tui_telemetry.jsonl.2`

### Stats Cost Guardrails

The Stats tab also applies session cost guardrails using `CAI_PRICE_LIMIT`:

- warning when usage approaches 80% of the limit
- automatic pause of new prompt dispatch when the limit is exceeded

Paused prompts can resume after increasing `CAI_PRICE_LIMIT` (or starting a new session).

### Context Usage Menu

The **Metrics** tab now includes a **Context** action that opens a context usage menu for the active terminal.

The menu shows:

- Used / max context tokens and percentage
- Visual usage bar (used vs free)
- Context-level legend (GREEN < 50%, YELLOW 50-79%, RED >= 80%)
- Free tokens and last-input token count
- Category breakdown percentages:
  - System prompt
  - Tool definitions
  - Memory / RAG
  - User prompts
  - Assistant responses
  - Tool calls
  - Tool results

When provider token details are available, the menu also refines attribution using:

- cached input tokens (applied to system/prefix context)
- reasoning output tokens (applied to assistant response budget)

Quick actions from the menu:

- **Refresh** the view
- **Copy To Input** to place a compact context summary in the active terminal input
- **Inject Command** to dispatch the summary immediately
- **Jump Metrics** to focus the metrics tab

Context snapshots are persisted in:

- `logs/tui_context_usage.jsonl`

with rotation backups:

- `logs/tui_context_usage.jsonl.1`
- `logs/tui_context_usage.jsonl.2`

---

## Teams Tab

The **Teams** tab provides instant access to preconfigured multi-agent team setups. Each team automatically configures all four terminals with specific agent combinations optimized for different security workflows.

### Available Teams

**Team 1: 2 Red + 2 Bug**
- Terminal 1: `redteam_agent`
- Terminal 2: `redteam_agent`
- Terminal 3: `bug_bounter_agent`
- Terminal 4: `bug_bounter_agent`
- **Use Case**: Penetration testing and vulnerability discovery with dual red team + bug bounty approach

**Team 2: 1 Red (T1) + 3 Bug**
- Terminal 1: `redteam_agent`
- Terminal 2: `bug_bounter_agent`
- Terminal 3: `bug_bounter_agent`
- Terminal 4: `bug_bounter_agent`
- **Use Case**: Red team coordination with intensive bug bounty hunting

**Team 3: 2 Red + 2 Blue**
- Terminal 1: `redteam_agent`
- Terminal 2: `redteam_agent`
- Terminal 3: `blueteam_agent`
- Terminal 4: `blueteam_agent`
- **Use Case**: Balanced offensive testing and defensive analysis

**Team 4: 2 Blue + 2 Bug**
- Terminal 1: `blueteam_agent`
- Terminal 2: `blueteam_agent`
- Terminal 3: `bug_bounter_agent`
- Terminal 4: `bug_bounter_agent`
- **Use Case**: Defensive analysis with vulnerability research

**Team 5: Red + Blue + Retester + Bug**
- Terminal 1: `redteam_agent`
- Terminal 2: `blueteam_agent`
- Terminal 3: `retester_agent`
- Terminal 4: `bug_bounter_agent`
- **Use Case**: Comprehensive security workflow with offense, defense, validation, and research

**Team 6: 2 Red + 2 Retester**
- Terminal 1: `redteam_agent`
- Terminal 2: `redteam_agent`
- Terminal 3: `retester_agent`
- Terminal 4: `retester_agent`
- **Use Case**: Offensive testing with immediate vulnerability validation

**Team 7: 2 Blue + 2 Retester**
- Terminal 1: `blueteam_agent`
- Terminal 2: `blueteam_agent`
- Terminal 3: `retester_agent`
- Terminal 4: `retester_agent`
- **Use Case**: Defensive validation with retesting confirmation

**Team 8: 4 Red**
- Terminal 1: `redteam_agent`
- Terminal 2: `redteam_agent`
- Terminal 3: `redteam_agent`
- Terminal 4: `redteam_agent`
- **Use Case**: Full offensive operations with maximum red team coverage

**Team 9: 4 Blue**
- Terminal 1: `blueteam_agent`
- Terminal 2: `blueteam_agent`
- Terminal 3: `blueteam_agent`
- Terminal 4: `blueteam_agent`
- **Use Case**: Unified defensive posture analysis and hardening

**Team 10: 4 Bug**
- Terminal 1: `bug_bounter_agent`
- Terminal 2: `bug_bounter_agent`
- Terminal 3: `bug_bounter_agent`
- Terminal 4: `bug_bounter_agent`
- **Use Case**: Intensive bug bounty hunting and vulnerability research

**Team 11: 4 Retester**
- Terminal 1: `retester_agent`
- Terminal 2: `retester_agent`
- Terminal 3: `retester_agent`
- Terminal 4: `retester_agent`
- **Use Case**: Comprehensive vulnerability revalidation and verification

### Team Button Features

Each team button displays:
- **Team number** (e.g., `#1`, `#2`)
- **Compact agent composition** (e.g., `2 red + 2 bug`)
- **Adaptive text**: Button labels automatically adjust based on available width
  - **Full width**: Shows complete agent names without `_agent` suffix
  - **Narrow width**: Abbreviates to short names (e.g., `red`, `blue`, `bug`, `retest`)

### Team Tooltips

Hover over any team button to see detailed information:

```
#2: 2 redteam_agent + 2 bug_bounter_agent
T1: redteam_agent
T2: redteam_agent
T3: bug_bounter_agent
T4: bug_bounter_agent
Best for: Comprehensive vulnerability discovery with red + bug workflows.
```

**Tooltip features**:
- Color-coded title with team composition
- Terminal-by-terminal agent breakdown
- Built-in playbook hint (`Best for`) for quick strategy selection
- Visual consistency with TUI color palette

### Team Playbook Preview

When a team is selected, the Teams panel shows a compact preview card with:

- Team number and label
- T1-T4 agent assignment summary
- Strategy hint for when to use that team

### Using Teams

1. **Click any team button** to instantly configure all four terminals
2. **Automatic synchronization**: Terminal headers update immediately
3. **Preserved context**: Each terminal maintains its conversation history
4. **No disruption**: Switch between teams without losing work

**Example workflow**:

```
1. Start with Team 1 (2 redteam + 2 bug_bounter)
2. Conduct initial vulnerability scan
3. Switch to Team 5 (2 redteam + 2 retester)
4. Validate discovered vulnerabilities
5. Switch to Team 3 (2 redteam + 2 blueteam)
6. Analyze defensive implications
```

---

## Queue Tab

The **Queue** tab manages prompt batching and broadcast execution.

### Queue Management

Queue controls now include:

- **Run Queue**: Execute pending prompts sequentially in queue order
- **Delete Selected**: Remove a single highlighted queue entry
- **Clear All**: Remove the full queue safely
- **Broadcast: ON/OFF**: Toggle queue-wide broadcast mode

### Add Prompts

Add prompts in the queue input row and press Enter (or **Add**).

Prompt format options:

- normal: queued for active terminal execution
- suffix `all`: mark prompt for broadcast to all terminals

### Broadcast Mode

When **Broadcast: ON**, queued prompts execute to terminals T1-T4 during queue runs.

When **Broadcast: OFF**, only prompts explicitly marked with `all` are broadcast.

### Execution Progress

Queue items display status markers:

- `○` pending
- `▶` running
- `✓` completed
- `✗` failed

The queue status line shows pending/running/completed/error totals and current run state.

### Automatic Queuing (legacy behavior)

Commands are automatically added to the queue when you:
- **Send prompts to busy terminals**: New commands wait while previous ones execute
- **Issue rapid commands**: Quick successive prompts queue automatically
- **Work across terminals**: Commands accumulate independently per terminal

### Queue Display

The queue shows:
- **Pending commands**: Commands waiting to execute
- **Command content**: Full text of each queued prompt
- **Target terminal**: Which terminal will execute the command
- **Execution order**: Commands execute in FIFO (First In, First Out) order
- **Real-time updates**: Queue updates automatically as commands are added or completed

Broadcast-tagged entries are labeled with `[ALL]`.

### How It Works

**Automatic execution flow**:
1. You send a prompt to a terminal that's already processing
2. The new prompt is automatically added to that terminal's queue
3. When the current operation completes, the queued prompt executes immediately
4. No manual intervention required

**Visual feedback**:
- **Pending**: Command waiting to execute (displayed in queue)
- **Executing**: Command currently running (queue updates)
- **Completed**: Command finished (removed from queue)

### Monitoring the Queue

Use the Queue tab to:
- **Track pending work**: See what commands are waiting
- **Verify execution order**: Confirm commands will run in the correct sequence
- **Plan workflow**: Know when terminals will be available
- **Avoid conflicts**: Prevent overloading terminals with too many commands

### Best Practices

✅ **Monitor before sending**: Check the queue before adding more commands to busy terminals

✅ **Use multiple terminals**: Distribute work across terminals to avoid queue buildup

✅ **Wait for completion**: For complex operations, wait until current task finishes before queuing more

---

## Stats Tab

The **Stats** tab provides real-time monitoring of your CAI usage, costs, and performance metrics.

### Token Usage

**Display metrics**:
- **Input tokens**: Tokens sent to the model
- **Output tokens**: Tokens received from the model
- **Total tokens**: Combined input and output
- **Token rate**: Tokens per request

**Per-terminal breakdown**:

```
Terminal 1: 15,234 tokens
Terminal 2: 8,956 tokens
Terminal 3: 12,445 tokens
Terminal 4: 6,789 tokens
```

### Cost Tracking

**Real-time cost calculation**:
- **Per-terminal costs**: Individual terminal spending
- **Session total**: Combined cost for current session
- **Model-specific rates**: Accurate pricing per model
- **Currency**: Displayed in USD

**Cost breakdown example**:

```
Terminal 1 (alias1): $0.45
Terminal 2 (gpt-5): $0.32
Terminal 3 (alias1): $0.08
Terminal 4 (claude-sonnet-4.5): $0.51

Session Total: $1.36
```

### Request Statistics

**Tracked metrics**:
- **Total requests**: Number of API calls made
- **Successful requests**: Completed without errors
- **Failed requests**: Errors or timeouts
- **Average response time**: Mean latency per request

### Session Information

**Displayed data**:
- **Session duration**: Total time elapsed
- **Active terminals**: Number of terminals in use
- **Current models**: Models assigned to each terminal
- **Active agents**: Agents assigned to each terminal

### Cost Optimization Tips

The Stats tab helps you optimize costs by:
1. **Monitoring usage patterns**: Identify high-cost terminals
2. **Model selection**: Compare costs between models
3. **Token awareness**: Track verbose responses
4. **Budget management**: Set spending limits

---

## Keys Tab

The **Keys** tab allows you to manage API keys for different LLM providers directly from the TUI.

### Supported Providers

CAI supports API keys for:
- **ALIAS1** (Alias model - Optimized for cybersecurity tasks)
- **OpenAI** (GPT models)
- **Anthropic** (Claude models)
- **Google** (Gemini models)
- **Groq** (Fast inference models)
- **OpenRouter** (Multi-provider routing)
- **Custom providers** (Self-hosted models)

#### About ALIAS1

**ALIAS1** is Alias Robotics' proprietary large language model, specifically fine-tuned and optimized for cybersecurity operations. It is the **default model** in CAI-Pro and offers:

- **Specialized cybersecurity knowledge**: Deep understanding of offensive/defensive security
- **Tool integration**: Native support for security tools and frameworks
- **Cost efficiency**: Competitive pricing for professional security workflows
- **Privacy**: Self-hosted option available for sensitive operations
- **Performance**: Optimized response times for security tasks
- **Default selection**: Pre-configured as the primary model for all terminals

**Learn more**: [https://aliasrobotics.com/alias1](https://aliasrobotics.com/alias1)

ALIAS1 is automatically configured when you launch the CAI --tui. To explicitly set or verify the model:

```bash
/model alias1
```

To use ALIAS1 with your API key, configure it in the Keys tab or via `.env` file:

```bash
ALIAS_API_KEY=your-alias1-key-here
```

---

### Adding API Keys

**Interactive method**:
1. Navigate to the **Keys** tab
2. Select the provider
3. Enter your API key
4. Press `Enter` to save

### Viewing Configured Keys

The Keys tab displays:
- **Provider names**: Which providers are configured
- **Masked keys**: Shows only last 4 characters for security

**Example display**:

```
ALIAS_API_KEY:sk-12hk......2t4
OpenAI_API_KEY: sk-...abc123 
ANTHROPIC_API_KEY: sk-ant-...xyz789 
```

### Key Security

**Security features**:
- **Encrypted storage**: Keys stored securely in `.env`
- **Masked display**: Only last characters visible
- **No logging**: Keys never written to logs
- **Session-scoped**: Keys loaded at startup

### Key Validation

CAI automatically validates keys:
- **On startup**: Checks if keys are properly formatted
- **On first use**: Tests actual API connectivity
- **Real-time feedback**: Immediate error messages for invalid keys

### Managing Keys via Config File

You can also manage keys by editing the `.env` file directly:

```bash
# .env file
ALIAS_API_KEY=sk-212...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
GROQ_API_KEY=gsk-...
```

---

## Sidebar Shortcuts

### Navigation

- **Mouse click**: Click any tab to switch
- **Scroll**: Use mouse wheel to scroll long lists (Queue, Stats)
- **Hover**: Hover over team buttons for detailed tooltips

### Visibility

- **Always visible**: Sidebar remains visible at all times
- **Responsive width**: Adapts to terminal window size
- **Scrollable content**: Long lists scroll independently

---

## Tips and Best Practices

### Teams

✅ **Use teams for consistent workflows**: Save time by using preconfigured teams instead of manually setting up agents

✅ **Switch teams mid-session**: Change strategies without losing context

✅ **Combine with commands**: Use `/agent` command in specific terminals to fine-tune team configurations

### Queue

✅ **Batch operations**: Queue multiple commands for unattended execution

✅ **Parallel efficiency**: Let multiple terminals work simultaneously

✅ **Strategic ordering**: Order commands to maximize parallelism

### Stats

✅ **Monitor costs regularly**: Keep an eye on spending during long sessions

✅ **Compare models**: Use stats to find the best cost/performance ratio

✅ **Track patterns**: Identify which workflows consume most tokens

### Keys

✅ **Configure on first launch**: Set up all keys before starting work

✅ **Use environment variables**: For production, prefer `.env` over interactive input

---

## Troubleshooting

### Teams not loading

**Symptom**: Team buttons don't appear or don't respond

**Solutions**:
- Restart the TUI
- Check that team configuration file exists
- Verify agent names are correct


### Stats showing zero

**Symptom**: Token counts and costs display as zero

**Solutions**:
- Execute at least one command to generate stats
- Verify API keys are configured correctly
- Check that model pricing data is loaded

### Keys not saving

**Symptom**: API keys don't persist after restart

**Solutions**:
- Ensure `.env` file has write permissions
- Check for errors in the status bar when saving
- Manually edit `.env` file if interactive method fails

---

## Related Documentation

- [User Interface Overview](user_interface.md) - Complete TUI layout guide
- [Keyboard Shortcuts](keyboard_shortcuts.md) - All keyboard commands
- [Commands Reference](commands_reference.md) - Complete command list
- [Terminals Management](terminals_management.md) - Multi-terminal workflows
- [Getting Started](getting_started.md) - Initial setup and configuration

---

*Last updated: October 2025 | CAI TUI v0.6+*

**Need help?** Press `F1` or type `/help` for context-sensitive assistance.

