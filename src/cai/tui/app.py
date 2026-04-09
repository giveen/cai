"""
Matrix-themed Textual TUI for CAI.

Activated with:
    CAI_TUI=true cai
    cai --tui

Layout mirrors the CAI PRO screenshot; colours are classic
Matrix green-on-black throughout.
"""

from __future__ import annotations

import asyncio
import logging
import inspect
import json
import os
import re
import time
import textwrap
from datetime import datetime
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text as RichText
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets._text_area import TextArea

# ---------------------------------------------------------------------------
# ASCII banner – Matrix green applied via Rich Text styles at render time
# ---------------------------------------------------------------------------
_BANNER_LINES = [
    "          CCCCCCCCCCCCC      +++++++++    +++++++++     IIIIIIIIII",
    "        CCC::::::::::::C  ++++++++++     ++++++++++     I::::::::I",
    "       CC:::::::::::::::C++++++++++       ++++++++++    I::::::::I",
    "      C:::::CCCCCCCC::::C+++++++++   ++    +++++++++    II::::::II",
    "     C:::::C       CCCCCC +++++++  +++++    +++++++       I::::I  ",
    "     C:::::C               +++++  +++++++    +++++        I::::I  ",
    "     C:::::C               ++++    +++++++    ++++        I::::I  ",
    "     C:::::C                ++      +++++++    ++         I::::I  ",
    "     C:::::C                 +   +++++++++++   +          I::::I  ",
    "     C:::::C                   +++++++++++++              I::::I  ",
    "     C:::::C                    +++++++++++               I::::I  ",
    "     C:::::C       CCCCCC        +++++++++                I::::I  ",
    "      C:::::CCCCCCCC::::C         +++++++              II::::::II ",
    "       CC:::::::::::::::C           ++++               I::::::::I ",
    "         CCC::::::::::::C             ++               I::::::::I ",
    "            CCCCCCCCCCCCC               +              IIIIIIIIII ",
    "",
    "               Cybersecurity AI (CAI)   ·   Bug bounty-ready AI  ",
]

# ---------------------------------------------------------------------------
# Agent name prettifier
# ---------------------------------------------------------------------------
_ACRONYMS = {
    "sast",
    "dfir",
    "dns",
    "smtp",
    "sdr",
    "ctf",
    "mcp",
    "api",
    "iot",
    "ai",
    "ml",
    "ids",
    "ips",
    "osint",
}

# Suffix → short distinguishing label appended after the base name
_SUFFIX_LABELS: list[tuple[str, str]] = [
    ("_swarm_pattern", " ↺"),  # swarm
    ("_swarm_pa", " ↺"),
    ("_pattern", " ⊕"),  # non-swarm pattern
    ("_agent", ""),  # plain agent – strip cleanly
]


def _pretty_name(raw: str) -> str:
    """Convert internal agent name → display label.

    android_sast_agent            → Android SAST
    redteam_agent                 → Redteam
    redteam_swarm_pattern         → Redteam ↺
    bug_bounter_agent             → Bug Bounter
    dns_smtp_agent                → DNS SMTP
    blue_team_red_team_shared_..  → Blue/Red Shared
    """
    name = raw
    suffix_label = ""
    for suffix, label in _SUFFIX_LABELS:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            suffix_label = label
            break
    # Abbreviate long blue/red team names to fit the sidebar
    name = (
        name.replace("blue_team_red_team_shared_context", "Blue/Red Shared")
        .replace("blue_team_red_team_split_context", "Blue/Red Split")
        .replace("blue_team_red_team", "Blue/Red Team")
    )
    if "_" not in name and " " in name:
        # Already replaced with a friendly string above
        return name + suffix_label
    parts = []
    for part in name.split("_"):
        if not part:
            continue
        parts.append(part.upper() if part.lower() in _ACRONYMS else part.capitalize())
    return " ".join(parts) + suffix_label or raw


# ---------------------------------------------------------------------------
# Embedded CSS – everything Matrix green-on-black
# ---------------------------------------------------------------------------
_CSS = """
/* ═══════════════ MATRIX THEME ═══════════════ */
Screen {
    background: #000000;
    color: #00ff00;
}

/* ─── Header row ─── */
CaiHeader {
    height: 1;
    dock: top;
    background: #001a00;
    layout: horizontal;
}

#header-left {
    width: 1fr;
    height: 1;
    layout: horizontal;
}

#header-left-text {
    width: auto;
    min-width: 0;
    height: 1;
    content-align: left middle;
    padding: 0 1;
    color: #00ff00;
}

#header-menu {
    width: 8;
    height: 1;
    min-width: 8;
    background: #001a00;
    color: #00cc00;
    border: none;
    padding: 0 1;
}

#header-menu:hover {
    background: #002400;
    color: #00ff00;
}

#header-nav {
    width: 1fr;
    height: 1;
    layout: horizontal;
}

.top-nav-btn {
    width: auto;
    min-width: 8;
    height: 1;
    background: #001a00;
    color: #006600;
    border: none;
    padding: 0 1;
}

.top-nav-btn:hover,
.top-nav-btn.-active-top-nav {
    background: #002c00;
    color: #00ff00;
    text-style: bold;
}

#header-right {
    width: auto;
    height: 1;
    content-align: right middle;
    padding: 0 1;
    color: #00cc00;
}

/* ─── Main body ─── */
#body {
    layout: vertical;
    height: 1fr;
}

#sidebar-tabs {
    background: #000000;
    height: 1fr;
    color: #00ff00;
}

#sidebar-tabs Tabs {
    background: #001a00;
    height: 0;
    display: none;
    border-bottom: solid #003300;
}

#sidebar-tabs Tab {
    background: #001a00;
    color: #006600;
    padding: 0 2;
}

#sidebar-tabs Tab.-active {
    background: #003300;
    color: #00ff00;
    text-style: bold;
}

#sidebar-tabs Tab:hover {
    background: #002200;
    color: #00cc00;
}

#sidebar-tabs ContentSwitcher {
    height: 1fr;
    background: #000000;
}

/* Agents pane */
#agents-pane {
    height: 1fr;
    background: #000000;
    layout: vertical;
}

#agents-scroll {
    height: 1fr;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

.agent-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #00cc00;
    border: none;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

.agent-btn:hover {
    background: #001a00;
    color: #00ff00;
}

.agent-btn.-active-agent {
    background: #002800;
    color: #00ff00;
    text-style: bold;
}

#teams-section {
    height: auto;
    max-height: 18;
    background: #000000;
    border-top: solid #003300;
    layout: vertical;
}

#teams-label {
    height: 1;
    background: #001a00;
    color: #006600;
    padding: 0 1;
    text-style: bold;
}

#teams-scroll {
    height: 1fr;
    max-height: 14;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

#team-playbook-preview {
    height: 4;
    background: #001200;
    color: #00cc00;
    border-top: solid #003300;
    padding: 0 1;
}

.team-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #008800;
    border: none;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

.team-btn:hover {
    background: #001a00;
    color: #00ff00;
}

.team-btn.-active-team {
    background: #002800;
    color: #00ff00;
    text-style: bold;
}

#new-team-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #004400;
    border: none;
    border-top: solid #002200;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

#new-team-btn:hover {
    background: #001a00;
    color: #00aa00;
}

/* Queue pane */
#queue-pane {
    height: 1fr;
    background: #000000;
    layout: vertical;
}

#queue-list {
    height: 1fr;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

#queue-list > ListItem {
    background: #000000;
    color: #00cc00;
    padding: 0 1;
    height: 1;
    border-bottom: solid #001a00;
}

#queue-list > ListItem:hover {
    background: #001a00;
    color: #00ff00;
}

#queue-list > ListItem.--highlight {
    background: #002800;
    color: #00ff00;
}

#queue-list Label {
    background: transparent;
    color: #00cc00;
    width: 1fr;
}

#queue-status {
    height: 2;
    padding: 0 1;
    color: #00aa00;
    border-top: solid #003300;
    background: #000000;
}

#queue-actions {
    height: 3;
    layout: horizontal;
    border-top: solid #003300;
    background: #000000;
}

#queue-input-row {
    height: 3;
    layout: horizontal;
    background: #000000;
    border-top: solid #003300;
}

#queue-prefix {
    width: 4;
    height: 3;
    content-align: left middle;
    color: #004400;
    padding: 0 1;
    background: #000000;
}

#queue-input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

#queue-broadcast {
    width: 12;
    height: 3;
    text-align: center;
}

#queue-input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

#queue-input > .input--placeholder {
    color: #004400;
}

/* ─── Right area: output + status + terminal input ─── */
#main-area {
    width: 1fr;
    height: 100%;
    layout: vertical;
}

/* ─── Scrollable output log ─── */
#output-log {
    height: 1fr;
    background: #000000;
    color: #00ff00;
    border: none;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
    padding: 0 1;
}

/* ─── One-line status / thinking bar ─── */
#status-bar {
    height: 1;
    background: #001a00;
    color: #00aa00;
    padding: 0 1;
    border-top: solid #003300;
}

/* ─── Input row ─── */
#input-row {
    height: 3;
    layout: horizontal;
    background: #000000;
    border-top: solid #003300;
}

#input-prefix {
    width: 7;
    height: 3;
    content-align: left middle;
    color: #00ff00;
    padding: 0 1;
    background: #000000;
}

#user-input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

#user-input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

#user-input > .input--placeholder {
    color: #004400;
}

/* ─── Footer ─── */
Footer {
    background: #001a00;
    color: #00aa00;
}

Footer > .footer--key {
    background: #003300;
    color: #00ff00;
}

Footer > .footer--description {
    color: #00aa00;
}

Footer > .footer--spacer {
    background: #001a00;
}

/* ─── Terminals container: Vertical, two rows max ─── */
#terminals {
    height: 1fr;
    layout: vertical;
    background: #000000;
}

.term-row {
    height: 1fr;
    layout: horizontal;
    background: #000000;
}

TerminalPanel {
    width: 1fr;
    height: 100%;
    layout: vertical;
    border: solid #003300;
    background: #000000;
}

TerminalPanel.-active-panel {
    border: solid #00ff00;
}

TerminalPanel.-inactive-panel {
    border: solid #002200;
}

TerminalPanel.-busy-panel {
    border: solid #00aa55;
}

TerminalPanel.-error-panel {
    border: solid #cc3333;
}

.term-header {
    height: 1;
    background: #001a00;
    color: #00cc00;
    padding: 0 1;
    border-bottom: solid #003300;
}

.term-log {
    height: 1fr;
    background: #000000;
    color: #00ff00;
    border: none;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
    padding: 0 1;
}

.term-status {
    height: 1;
    background: #001a00;
    color: #00aa00;
    padding: 0 1;
}

TerminalPanel.-busy-panel .term-status {
    background: #102300;
    color: #66ff99;
}

TerminalPanel.-error-panel .term-status {
    background: #220000;
    color: #ff7777;
}

.term-input-row {
    height: 4;
    layout: horizontal;
    background: #000000;
    border-top: solid #003300;
}

.term-input-prefix {
    width: 7;
    height: 4;
    content-align: left middle;
    color: #00ff00;
    padding: 0 1;
    background: #000000;
}

.term-input-column {
    width: 1fr;
    height: 1fr;
    layout: vertical;
    background: #000000;
}

.term-input {
    background: #000000;
    color: #00ff00;
    border: none;
    border-bottom: solid #003300;
    height: 3;
    width: 1fr;
    padding: 0 1;
}

.term-input:focus {
    border: none;
    border-bottom: solid #00ff00;
    background: #000000;
}

.term-input > .input--placeholder {
    color: #004400;
}

.term-input-meta {
    height: 1;
    color: #006600;
    background: #000000;
    padding: 0 1;
}

/* ─── Agent action modal ─── */
AgentModal {
    align: center middle;
    background: rgba(0, 0, 0, 0.8);
}

#modal-dialog {
    width: 32;
    height: auto;
    background: #001a00;
    border: solid #00ff00;
    padding: 1 2;
    layout: vertical;
}

#modal-agent-label {
    height: 2;
    color: #00ff00;
    text-style: bold;
    content-align: center middle;
    border-bottom: solid #003300;
    margin-bottom: 1;
}

.modal-btn {
    width: 1fr;
    height: 3;
    background: #002800;
    color: #00cc00;
    border: solid #003300;
    margin-bottom: 1;
    text-align: center;
}

.modal-btn:hover {
    background: #003300;
    color: #00ff00;
}

.modal-btn:focus {
    border: solid #00ff00;
    color: #00ff00;
}

.modal-btn--cancel {
    background: #050000;
    color: #446644;
    border: solid #002200;
    margin-bottom: 0;
}

.modal-btn--cancel:hover {
    background: #110000;
    color: #00aa44;
}

#palette-search {
    margin-bottom: 1;
}

#palette-results-scroll {
    height: 12;
    border-top: solid #003300;
    border-bottom: solid #003300;
    background: #000000;
}

#palette-results {
    height: auto;
    layout: vertical;
    background: #000000;
}

.palette-cmd {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #00cc00;
    border: none;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

.palette-cmd:hover,
.palette-cmd.-selected {
    background: #002800;
    color: #00ff00;
    text-style: bold;
}

#palette-help {
    height: 2;
    color: #008800;
    padding: 0 1;
}
"""
# Add small selection styles for the Sessions list
_CSS += """
#sessions-list > ListItem.-selected {
    background: #002800;
    color: #00ff00;
    border-left: solid #00ff00;
}
#sessions-list > ListItem > Button {
    margin-left: 1;
}
# session preview area
#session-preview {
    height: 7;
    padding: 0 1;
    background: #001400;
    color: #00ff00;
    border-top: solid #003300;
}
"""

# Tools sidebar tab styling
_CSS += """
#tools-pane {
    height: 1fr;
    background: #000000;
    layout: vertical;
}

#tools-list-scroll {
    height: 1fr;
    background: #000000;
    scrollbar-color: #003300 #000000;
    scrollbar-size: 1 1;
}

.tool-btn {
    width: 1fr;
    height: 1;
    background: #000000;
    color: #00cc00;
    border: none;
    text-align: left;
    padding: 0 1;
    margin: 0;
}

.tool-btn:hover {
    background: #001a00;
    color: #00ff00;
}

.tool-btn.-active-tool {
    background: #002800;
    color: #00ff00;
    text-style: bold;
}

#tools-actions {
    height: 3;
    layout: horizontal;
    border-top: solid #003300;
    background: #000000;
}

#tools-preview {
    height: 8;
    padding: 0 1;
    background: #001400;
    color: #00ff00;
    border-top: solid #003300;
}

#tools-history-scroll {
    height: 8;
    border-top: solid #003300;
    background: #000000;
}

#tools-inject-mode.-inject-input {
    background: #001a00;
    color: #00cc00;
    border: solid #003300;
}

#tools-inject-mode.-inject-command {
    background: #003000;
    color: #00ff00;
    border: solid #00aa00;
    text-style: bold;
}

#metrics-pane {
    height: 1fr;
    background: #000000;
    layout: vertical;
}

#metrics-summary {
    height: 10;
    padding: 0 1;
    background: #001400;
    color: #00ff00;
    border-top: solid #003300;
}

#metrics-events-scroll {
    height: 1fr;
    background: #000000;
    border-top: solid #003300;
}

#metrics-events {
    height: auto;
    color: #00cc00;
    padding: 0 1;
}

#metrics-actions {
    height: 3;
    layout: horizontal;
    border-top: solid #003300;
    background: #000000;
}
"""

# Simple on-disk config used by the TUI config screens.
CONFIG_FILE = os.path.join(os.getcwd(), "tui_config.json")
TOOL_CALLS_FILE = os.path.join(os.getcwd(), "logs", "tui_tool_calls.jsonl")
TOOL_CALLS_MAX_BYTES = 2 * 1024 * 1024
TOOL_CALLS_MAX_BACKUPS = 2
TELEMETRY_FILE = os.path.join(os.getcwd(), "logs", "tui_telemetry.jsonl")
TELEMETRY_MAX_BYTES = 2 * 1024 * 1024
TELEMETRY_MAX_BACKUPS = 2
CONTEXT_SNAPSHOTS_FILE = os.path.join(os.getcwd(), "logs", "tui_context_usage.jsonl")
CONTEXT_SNAPSHOTS_MAX_BYTES = 2 * 1024 * 1024
CONTEXT_SNAPSHOTS_MAX_BACKUPS = 2


def _load_tui_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_tui_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Full configuration variable definitions for the `/config` overview
# ---------------------------------------------------------------------------
CONFIG_VARIABLES = [
    {"name": "CTF_NAME", "default": "Not set", "description": "Name of the CTF challenge to run"},
    {
        "name": "CTF_CHALLENGE",
        "default": "Not set",
        "description": "Specific challenge name within the CTF to test",
    },
    {
        "name": "CTF_SUBNET",
        "default": "192.168.3.0/24",
        "description": "Network subnet for the CTF container",
    },
    {
        "name": "CTF_IP",
        "default": "192.168.3.100",
        "description": "IP address for the CTF container",
    },
    {
        "name": "CTF_INSIDE",
        "default": "true",
        "description": "Whether to conquer the CTF from within container",
    },
    {"name": "CAI_MODEL", "default": "alias1", "description": "Model to use for agents"},
    {
        "name": "CAI_DEBUG",
        "default": "1",
        "description": "Set debug output level (0: Only tool outputs, 1: Verbose debug output, 2: CLI debug output)",
    },
    {"name": "CAI_BRIEF", "default": "false", "description": "Enable/disable brief output mode"},
    {
        "name": "CAI_MAX_TURNS",
        "default": "inf",
        "description": "Maximum number of turns for agent interactions",
    },
    {
        "name": "CAI_TRACING",
        "default": "true",
        "description": "Enable/disable OpenTelemetry tracing",
    },
    {
        "name": "CAI_AGENT_TYPE",
        "default": "one_tool",
        "description": "Specify the agents to use (boot2root, one_tool...)",
    },
    {"name": "CAI_STATE", "default": "false", "description": "Enable/disable stateful mode"},
    {
        "name": "CAI_MEMORY",
        "default": "false",
        "description": "Enable/disable memory mode (episodic, semantic, all)",
    },
    {
        "name": "CAI_MEMORY_ONLINE",
        "default": "false",
        "description": "Enable/disable online memory mode",
    },
    {
        "name": "CAI_MEMORY_OFFLINE",
        "default": "false",
        "description": "Enable/disable offline memory",
    },
    {
        "name": "CAI_ENV_CONTEXT",
        "default": "true",
        "description": "Add dirs and current env to llm context",
    },
    {
        "name": "CAI_MEMORY_ONLINE_INTERVAL",
        "default": "5",
        "description": "Number of turns between online memory updates",
    },
    {
        "name": "CAI_PRICE_LIMIT",
        "default": "0",
        "description": "Price limit for the conversation in dollars",
    },
    {
        "name": "CAI_REPORT",
        "default": "ctf",
        "description": "Enable/disable reporter mode (ctf, nis2, pentesting)",
    },
    {
        "name": "CAI_SUPPORT_MODEL",
        "default": "o3-mini",
        "description": "Model to use for the support agent",
    },
    {
        "name": "CAI_SUPPORT_INTERVAL",
        "default": "5",
        "description": "Number of turns between support agent executions",
    },
    {
        "name": "CAI_STREAM",
        "default": "true",
        "description": "Boolean to enable real-time, chunked responses",
    },
    {
        "name": "CAI_WORKSPACE",
        "default": "Not set",
        "description": "Name of the current workspace (affects log file naming)",
    },
    {
        "name": "CAI_WORKSPACE_DIR",
        "default": "Not set",
        "description": "Path to the current workspace directory",
    },
    {
        "name": "CAI_GUARDRAILS",
        "default": "true",
        "description": "Enable/disable security guardrails for prompt injection protection",
    },
    {
        "name": "CAI_ANDROID_SAST_MODEL",
        "default": "Not set",
        "description": "Model override for AndroidSAST agent",
    },
    {
        "name": "CAI_APP_LOGIC_MAPPER_MODEL",
        "default": "Not set",
        "description": "Model override for AppLogicMapper agent",
    },
    {
        "name": "CAI_BB_TRIAGE_SWARM_PATTERN_MODEL",
        "default": "Not set",
        "description": "Model override for Bug bounty Triage agent",
    },
    {
        "name": "CAI_BLUE_TEAM_RED_TEAM_SHARED_CONTEXT_MODEL",
        "default": "Not set",
        "description": "Model override for shared blue/red context agent",
    },
    {
        "name": "CAI_BLUE_TEAM_RED_TEAM_SPLIT_CONTEXT_MODEL",
        "default": "Not set",
        "description": "Model override for split blue/red context agent",
    },
    {
        "name": "CAI_BLUETEAM_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Blue Team agent",
    },
    {
        "name": "CAI_BUG_BOUNTER_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Bug Bounter agent",
    },
    {
        "name": "CAI_DFIR_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for DFIR agent",
    },
    {
        "name": "CAI_DNS_SMTP_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for DNS/SMTP agent",
    },
    {
        "name": "CAI_FLAG_DISCRIMINATOR_MODEL",
        "default": "Not set",
        "description": "Model override for Flag discriminator agent",
    },
    {
        "name": "CAI_INJECTION_DETECTOR_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Prompt Injection Detector agent",
    },
    {
        "name": "CAI_MEMORY_ANALYSIS_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Memory Analysis Specialist agent",
    },
    {
        "name": "CAI_NETWORK_SECURITY_ANALYZER_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Network Security Analyzer agent",
    },
    {
        "name": "CAI_OFFSEC_PATTERN_MODEL",
        "default": "Not set",
        "description": "Model override for offsec_pattern agent",
    },
    {
        "name": "CAI_ONE_TOOL_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for one tool CTF agent",
    },
    {
        "name": "CAI_REDTEAM_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Red Team agent",
    },
    {
        "name": "CAI_REDTEAM_SWARM_PATTERN_MODEL",
        "default": "Not set",
        "description": "Model override for Red Team swarm manager",
    },
    {
        "name": "CAI_REPLAY_ATTACK_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Replay Attack agent",
    },
    {
        "name": "CAI_REPORTING_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for reporting agent",
    },
    {
        "name": "CAI_RETESTER_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Retester agent",
    },
    {
        "name": "CAI_REVERSE_ENGINEERING_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Reverse Engineering agent",
    },
    {
        "name": "CAI_SUBGHZ_SDR_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Sub-GHz SDR agent",
    },
    {
        "name": "CAI_THOUGHT_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for ThoughtAgent",
    },
    {
        "name": "CAI_USE_CASE_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Use Case agent",
    },
    {
        "name": "CAI_WEB_PENTESTER_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Web App Pentester agent",
    },
    {
        "name": "CAI_WIFI_SECURITY_AGENT_MODEL",
        "default": "Not set",
        "description": "Model override for Wi-Fi Security Tester agent",
    },
]


# ---------------------------------------------------------------------------
# Preset team compositions (label, list of agent-type strings)
# ---------------------------------------------------------------------------
TEAM_PRESETS = [
    ("2 red + 2 bug", ["redteam_agent", "redteam_agent", "bug_bounter_agent", "bug_bounter_agent"]),
    (
        "1 red + 3 bug",
        ["redteam_agent", "bug_bounter_agent", "bug_bounter_agent", "bug_bounter_agent"],
    ),
    ("2 red + 2 blue", ["redteam_agent", "redteam_agent", "blueteam_agent", "blueteam_agent"]),
    (
        "2 blue + 2 bug",
        ["blueteam_agent", "blueteam_agent", "bug_bounter_agent", "bug_bounter_agent"],
    ),
    (
        "red + blue + retest + bug",
        ["redteam_agent", "blueteam_agent", "retester_agent", "bug_bounter_agent"],
    ),
    ("2 red + 2 retest", ["redteam_agent", "redteam_agent", "retester_agent", "retester_agent"]),
    ("2 blue + 2 retest", ["blueteam_agent", "blueteam_agent", "retester_agent", "retester_agent"]),
    ("4 red", ["redteam_agent", "redteam_agent", "redteam_agent", "redteam_agent"]),
    ("4 blue", ["blueteam_agent", "blueteam_agent", "blueteam_agent", "blueteam_agent"]),
    ("4 bug", ["bug_bounter_agent", "bug_bounter_agent", "bug_bounter_agent", "bug_bounter_agent"]),
    ("4 retest", ["retester_agent", "retester_agent", "retester_agent", "retester_agent"]),
]

TEAM_PLAYBOOK_HINTS = [
    "Best for: Comprehensive vulnerability discovery with red + bug workflows.",
    "Best for: Bug bounty-heavy runs with red-team lead coverage.",
    "Best for: Balanced adversarial testing with offense + defense.",
    "Best for: Defense-led assessments with bug bounty validation.",
    "Best for: End-to-end lifecycle from discovery to validation.",
    "Best for: Aggressive offensive testing with immediate retest.",
    "Best for: Defensive hardening with continuous retesting.",
    "Best for: Maximum offensive throughput and coverage.",
    "Best for: Comprehensive defensive posture and hardening reviews.",
    "Best for: Intensive bug bounty hunting across multiple surfaces.",
    "Best for: Large-scale retesting and fix verification campaigns.",
]


# ---------------------------------------------------------------------------
# Agent-selection modal
# ---------------------------------------------------------------------------
class AgentModal(ModalScreen):
    """Pop-up shown when the user clicks an agent button.

    Dismissed with:
      ('update', agent_name)  – re-assign the current active terminal
      ('new',    agent_name)  – open a new terminal panel for this agent
      None                    – cancelled
    """

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, agent_name: str, active_term_label: str, at_max: bool = False) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._active_term_label = active_term_label
        self._at_max = at_max

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(
                f"Agent: [bold]{_pretty_name(self._agent_name)}[/bold]",
                id="modal-agent-label",
            )
            yield Button(
                f"Update {self._active_term_label}",
                id="modal-update",
                classes="modal-btn",
            )
            yield Button(
                "New Terminal" if not self._at_max else "New Terminal (max 4 reached)",
                id="modal-new",
                classes="modal-btn" + (" modal-btn--cancel" if self._at_max else ""),
                disabled=self._at_max,
            )
            yield Button(
                "Cancel",
                id="modal-cancel",
                classes="modal-btn modal-btn--cancel",
            )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "modal-update":
            self.dismiss(("update", self._agent_name))
        elif btn_id == "modal-new":
            self.dismiss(("new", self._agent_name))
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Small prompt modal used for rename/export inputs
# ---------------------------------------------------------------------------
class PromptModal(ModalScreen):
    """Simple input modal. Returns the entered string, or None if cancelled."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, prompt: str, default: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._prompt, id="modal-agent-label")
            yield Input(value=self._default, id="prompt-input")
            yield Button("OK", id="prompt-ok", classes="modal-btn")
            yield Button("Cancel", id="prompt-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "prompt-ok":
            try:
                val = self.query_one("#prompt-input", Input).value
            except Exception:
                val = self._default
            self.dismiss(val)
        else:
            self.dismiss(None)


class ConfirmModal(ModalScreen):
    """Simple confirmation modal. Returns True if confirmed, else None."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._message, id="modal-agent-label")
            yield Button("Delete", id="confirm-ok", classes="modal-btn modal-btn--cancel")
            yield Button("Cancel", id="confirm-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "confirm-ok":
            self.dismiss(True)
        else:
            self.dismiss(None)


class CommandPaletteModal(ModalScreen):
    """Command palette modal with fuzzy filtering and keyboard selection."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, commands: list[dict], recent: list[str]) -> None:
        super().__init__()
        self._commands = list(commands)
        self._recent = list(recent)
        self._selected_idx = 0
        self._visible_commands: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Command Palette", id="modal-agent-label")
            yield Input(placeholder="Type a command (fuzzy search)", id="palette-search")
            with ScrollableContainer(id="palette-results-scroll"):
                with Vertical(id="palette-results"):
                    pass
            yield Static("↑/↓ navigate · Enter run · Esc close", id="palette-help")

    def _fuzzy_score(self, query: str, text: str) -> int:
        q = query.lower().strip()
        t = text.lower()
        if not q:
            return 1
        if q in t:
            return 100 + len(q)
        score = 0
        pos = 0
        for ch in q:
            found = t.find(ch, pos)
            if found < 0:
                return -1
            score += 3 if found == pos else 1
            pos = found + 1
        return score

    async def _refresh_results(self) -> None:
        query = ""
        try:
            query = self.query_one("#palette-search", Input).value
        except Exception:
            query = ""

        ranked: list[tuple[int, dict]] = []
        for cmd in self._commands:
            searchable = f"{cmd.get('id', '')} {cmd.get('name', '')} {cmd.get('description', '')}"
            score = self._fuzzy_score(query, searchable)
            if score < 0:
                continue
            try:
                recency = self._recent.index(str(cmd.get("id")))
                score += max(0, 20 - recency)
            except Exception:
                pass
            ranked.append((score, cmd))

        ranked.sort(key=lambda x: (-x[0], str(x[1].get("name", ""))))
        self._visible_commands = [cmd for _, cmd in ranked]

        try:
            holder = self.query_one("#palette-results", Vertical)
        except Exception:
            return

        for child in list(holder.children):
            try:
                await child.remove()
            except Exception:
                pass

        if not self._visible_commands:
            await holder.mount(Static("No matching commands", classes="term-status"))
            self._selected_idx = 0
            return

        self._selected_idx = max(0, min(self._selected_idx, len(self._visible_commands) - 1))
        for idx, cmd in enumerate(self._visible_commands):
            name = str(cmd.get("name", cmd.get("id", "")))
            desc = str(cmd.get("description", ""))
            shortcut = str(cmd.get("shortcut", ""))
            label = f"{name:<8}  {desc}"
            if shortcut:
                label += f"  [{shortcut}]"
            btn = Button(label, id=f"palette-cmd-{cmd.get('id', '')}", classes="palette-cmd")
            if idx == self._selected_idx:
                btn.add_class("-selected")
            await holder.mount(btn)

    def _move_selection(self, delta: int) -> None:
        if not self._visible_commands:
            return
        self._selected_idx = (self._selected_idx + delta) % len(self._visible_commands)
        for idx, btn in enumerate(self.query(".palette-cmd")):
            if idx == self._selected_idx:
                btn.add_class("-selected")
                try:
                    btn.scroll_visible(animate=False)
                except Exception:
                    pass
            else:
                btn.remove_class("-selected")

    def _run_selected(self) -> None:
        if not self._visible_commands:
            return
        cmd_id = str(self._visible_commands[self._selected_idx].get("id", ""))
        if cmd_id:
            self.dismiss(("run", cmd_id))

    async def on_mount(self) -> None:
        await self._refresh_results()
        try:
            self.query_one("#palette-search", Input).focus()
        except Exception:
            pass

    async def on_input_changed(self, event: Input.Changed) -> None:
        if (event.input.id or "") != "palette-search":
            return
        self._selected_idx = 0
        await self._refresh_results()

    async def on_key(self, event: events.Key) -> None:
        if event.key == "up":
            event.stop()
            self._move_selection(-1)
            return
        if event.key == "down":
            event.stop()
            self._move_selection(1)
            return
        if event.key == "enter":
            event.stop()
            self._run_selected()
            return

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if not bid.startswith("palette-cmd-"):
            return
        cmd_id = bid[len("palette-cmd-") :]
        self.dismiss(("run", cmd_id))


class ConfigModal(ModalScreen):
    """Modal to confirm opening a Config section. Returns ('open', action_key) or None."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, action_key: str, display_label: str) -> None:
        super().__init__()
        self._action_key = action_key
        self._display = display_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(f"Open config: [bold]{self._display}[/bold]", id="modal-agent-label")
            yield Button("Open", id="config-open", classes="modal-btn")
            yield Button("Cancel", id="config-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "config-open":
            self.dismiss(("open", self._action_key))
        else:
            self.dismiss(None)


class ContextUsageModal(ModalScreen):
    """Context usage menu modal.

    Returns one of:
      ("refresh",)
      ("copy", summary_text)
      ("inject", summary_text)
      ("jump_metrics",)
      None
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, title: str, content: str, summary_text: str) -> None:
        super().__init__()
        self._title = title
        self._content = content
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static(self._title, id="modal-agent-label")
            yield Static(self._content)
            with Horizontal():
                yield Button("Refresh", id="ctx-refresh", classes="modal-btn")
                yield Button("Copy To Input", id="ctx-copy", classes="modal-btn")
                yield Button("Inject Command", id="ctx-inject", classes="modal-btn")
            with Horizontal():
                yield Button("Jump Metrics", id="ctx-jump", classes="modal-btn")
                yield Button("Close", id="ctx-close", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "ctx-refresh":
            self.dismiss(("refresh",))
        elif btn_id == "ctx-copy":
            self.dismiss(("copy", self._summary_text))
        elif btn_id == "ctx-inject":
            self.dismiss(("inject", self._summary_text))
        elif btn_id == "ctx-jump":
            self.dismiss(("jump_metrics",))
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# Full-screen Config screens
# ---------------------------------------------------------------------------
class ProvidersScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Providers Configuration", id="modal-agent-label")
            yield Static("Existing providers:", id="providers-label")
            yield ListView(id="providers-list")
            yield Input(placeholder="provider name", id="providers-name")
            yield Input(placeholder="api key", id="providers-key")
            with Horizontal():
                yield Button("Save", id="providers-save", classes="modal-btn")
                yield Button("Test", id="providers-test", classes="modal-btn")
                yield Button("Close", id="providers-cancel", classes="modal-btn modal-btn--cancel")

    async def on_mount(self) -> None:
        try:
            lv = self.query_one("#providers-list", ListView)
            providers = self._config.get("providers", {})
            for name, key in providers.items():
                await lv.mount(ListItem(Label(f"{name}: {key[:6]}..."), id=f"provider-item-{name}"))
        except Exception:
            pass

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "providers-save":
            try:
                name = self.query_one("#providers-name", Input).value.strip()
                key = self.query_one("#providers-key", Input).value.strip()
            except Exception:
                name = ""
                key = ""
            if not name:
                self.dismiss(None)
                return
            self.dismiss(("save_provider", name, key))
        elif btn == "providers-test":
            try:
                name = self.query_one("#providers-name", Input).value.strip()
            except Exception:
                name = ""
            self.dismiss(("test_provider", name))
        else:
            self.dismiss(None)


class ModelParamsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        mp = self._config.get("model_params", {})
        with Vertical(id="modal-dialog"):
            yield Static("Model Parameters", id="modal-agent-label")
            yield Input(value=str(mp.get("temperature", "0.0")), id="model-temp")
            yield Input(value=str(mp.get("max_tokens", "1024")), id="model-max-tokens")
            yield Input(value=str(mp.get("system_prompt", "")), id="model-system")
            with Horizontal():
                yield Button("Save", id="model-save", classes="modal-btn")
                yield Button("Close", id="model-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "model-save":
            try:
                temp = float(self.query_one("#model-temp", Input).value)
            except Exception:
                temp = 0.0
            try:
                max_t = int(self.query_one("#model-max-tokens", Input).value)
            except Exception:
                max_t = 1024
            try:
                sys = self.query_one("#model-system", Input).value
            except Exception:
                sys = ""
            self.dismiss(
                (
                    "save_model_params",
                    {"temperature": temp, "max_tokens": max_t, "system_prompt": sys},
                )
            )
        else:
            self.dismiss(None)


class MemoryInspectorScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Memory / RAG Inspector", id="modal-agent-label")
            yield Static("Operations:", id="memory-ops")
            with Horizontal():
                yield Button("Rebuild Index", id="memory-rebuild", classes="modal-btn")
                yield Button("Evict All", id="memory-evict", classes="modal-btn modal-btn--cancel")
                yield Button("Close", id="memory-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "memory-rebuild":
            self.dismiss(("rebuild_memory",))
        elif btn == "memory-evict":
            self.dismiss(("evict_memory",))
        else:
            self.dismiss(None)


class ExportImportScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Export / Import Workspace", id="modal-agent-label")
            yield Input(placeholder="path to export/import", id="export-import-path")
            with Horizontal():
                yield Button("Export", id="export-do", classes="modal-btn")
                yield Button("Import", id="import-do", classes="modal-btn")
                yield Button(
                    "Close", id="export-import-cancel", classes="modal-btn modal-btn--cancel"
                )

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "export-do":
            try:
                path = self.query_one("#export-import-path", Input).value.strip()
            except Exception:
                path = ""
            self.dismiss(("export_config", path))
        elif btn == "import-do":
            try:
                path = self.query_one("#export-import-path", Input).value.strip()
            except Exception:
                path = ""
            self.dismiss(("import_config", path))
        else:
            self.dismiss(None)


class EnvScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Environment Variables (CAI_*)", id="modal-agent-label")
            yield ListView(id="env-list")
            yield Input(placeholder="VAR_NAME (no CAI_ prefix)", id="env-name")
            yield Input(placeholder="value", id="env-value")
            with Horizontal():
                yield Button("Set", id="env-set", classes="modal-btn")
                yield Button("Unset", id="env-unset", classes="modal-btn modal-btn--cancel")
                yield Button("Close", id="env-cancel", classes="modal-btn modal-btn--cancel")

    async def on_mount(self) -> None:
        try:
            lv = self.query_one("#env-list", ListView)
            for k in sorted([k for k in os.environ if k.startswith("CAI_")]):
                await lv.mount(ListItem(Label(f"{k}={os.environ.get(k)}")))
        except Exception:
            pass

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn = event.button.id
        if btn == "env-set":
            try:
                name = self.query_one("#env-name", Input).value.strip()
                val = self.query_one("#env-value", Input).value
            except Exception:
                name = ""
                val = ""
            if not name:
                self.dismiss(None)
                return
            self.dismiss(("set_env", f"CAI_{name}", val))
        elif btn == "env-unset":
            try:
                name = self.query_one("#env-name", Input).value.strip()
            except Exception:
                name = ""
            if not name:
                self.dismiss(None)
                return
            self.dismiss(("unset_env", f"CAI_{name}"))
        else:
            self.dismiss(None)


class SessionRecordingScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config or {}

    def compose(self) -> ComposeResult:
        cur = os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() == "true"
        status = "disabled" if cur else "enabled"
        with Vertical(id="modal-dialog"):
            yield Static(
                f"Session recording is currently: [bold]{status}[/bold]", id="modal-agent-label"
            )
            with Horizontal():
                yield Button("Toggle", id="session-toggle", classes="modal-btn")
                yield Button("Close", id="session-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "session-toggle":
            self.dismiss(("toggle_session_recording",))
        else:
            self.dismiss(None)


class ResetDefaultsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Reset all TUI config to defaults?", id="modal-agent-label")
            yield Button("Reset", id="reset-do", classes="modal-btn modal-btn--cancel")
            yield Button("Cancel", id="reset-cancel", classes="modal-btn modal-btn--cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reset-do":
            self.dismiss(("reset_defaults", True))
        else:
            self.dismiss(None)


class ConfigOverviewScreen(ModalScreen):
    """Full interactive configuration overview: list of variables with Edit/Reset."""

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, variables: list[dict]) -> None:
        super().__init__()
        self._variables = variables

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Static("Configuration Overview", id="modal-agent-label")
            with ScrollableContainer(id="config-overview-scroll"):
                yield ListView(id="config-overview-list")
            with Horizontal():
                yield Button(
                    "Close", id="config-overview-close", classes="modal-btn modal-btn--cancel"
                )

    async def on_mount(self) -> None:
        try:
            lv = self.query_one("#config-overview-list", ListView)
            # Populate rows with current values
            for idx, v in enumerate(self._variables):
                # Ensure 'name' is always a string for downstream lookups
                name = str(v.get("name") or "")
                # Prefer explicit env var, then persisted config, then default
                cfg = _load_tui_config()
                val = (
                    os.environ.get(name)
                    or cfg.get("env", {}).get(name)
                    or v.get("default", "Not set")
                )
                display = f"{idx + 1:2d} | {name:<40.40} | {str(val):<15.15} | {v.get('default', ''):<12.12} | {v.get('description', '')[:60]}"
                item = ListItem(Label(display), id=f"config-item-{idx}")
                await lv.mount(item)
                await item.mount(Button("Edit", id=f"cfg-edit-{idx}", classes="agent-btn"))
                await item.mount(Button("Reset", id=f"cfg-reset-{idx}", classes="team-btn"))
        except Exception:
            pass

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "config-overview-close":
            self.dismiss(None)
            return
        if btn_id.startswith("cfg-edit-"):
            try:
                idx = int(btn_id.rsplit("-", 1)[-1])
            except Exception:
                idx = None
            self.dismiss(("edit", idx))
            return
        if btn_id.startswith("cfg-reset-"):
            try:
                idx = int(btn_id.rsplit("-", 1)[-1])
            except Exception:
                idx = None
            self.dismiss(("reset", idx))
            return


# ---------------------------------------------------------------------------
# Terminal panel widget  (one per open terminal)
# ---------------------------------------------------------------------------
class TerminalPanel(Widget):
    """Self-contained chat terminal: header bar + RichLog + status + input."""

    # Sent to the App when this panel is clicked so the app can mark it active
    class Activated(Message):
        def __init__(self, term_id: int) -> None:
            super().__init__()
            self.term_id = term_id

    # Sent when the close button is clicked
    class CloseRequested(Message):
        def __init__(self, term_id: int) -> None:
            super().__init__()
            self.term_id = term_id

    def __init__(
        self,
        term_id: int,
        agent,
        agent_name: str,
        model_name: str,
    ) -> None:
        super().__init__(id=f"terminal-panel-{term_id}")
        self._term_id = term_id
        self._agent = agent
        self._agent_name = agent_name
        self._model_name = model_name
        self._tool_outputs_by_call_id: dict[str, str] = {}
        self._active_tool_calls: dict[str, dict] = {}
        self._busy: bool = False
        self._last_prompt_text: str = ""
        self._run_worker = None
        self._workers: list[Any] = []
        self._prompt_history: list[str] = []
        self._history_index: Optional[int] = None

    def _get_input_widget(self):
        try:
            return self.query_one(f"#term-input-{self._term_id}", TextArea)
        except Exception:
            return None

    def _set_input_text(self, text: str) -> None:
        inp = self._get_input_widget()
        if inp is None:
            return
        try:
            if hasattr(inp, "load_text"):
                inp.load_text(text)
            else:
                cast(Any, inp).value = text
        except Exception:
            pass

    def _get_input_text(self) -> str:
        inp = self._get_input_widget()
        if inp is None:
            return ""
        try:
            if hasattr(inp, "text"):
                return str(inp.text)
            return str(cast(Any, inp).value)
        except Exception:
            return ""

    def _infer_input_language(self, text: str) -> str:
        raw = str(text or "")
        first = raw.strip().splitlines()[0] if raw.strip() else ""
        m = re.match(r"^```([A-Za-z0-9_+-]+)", first)
        if m:
            return m.group(1).lower()
        if re.search(r"\b(def|class|import|from|async|await)\b", raw):
            return "python"
        if re.search(r"\b(function|const|let|var|=>)\b", raw):
            return "javascript"
        return "markdown"

    def _resize_input_for_text(self, text: str) -> None:
        lines = max(1, len(str(text or "").splitlines()))
        visible_lines = min(8, max(1, lines))
        input_height = visible_lines + 1
        row_height = input_height + 1
        try:
            inp = self._get_input_widget()
            if inp is not None:
                inp.styles.height = input_height
                try:
                    inp.scroll_end(animate=False)
                except Exception:
                    pass
            row = self.query_one(".term-input-row", Horizontal)
            row.styles.height = row_height
            prefix = self.query_one(".term-input-prefix", Static)
            prefix.styles.height = row_height
        except Exception:
            pass

    def _update_input_meta(self, text: str) -> None:
        count = len(str(text or ""))
        mode = "multi-line" if "\n" in str(text or "") else "single-line"
        color = "#006600"
        if count >= 3000:
            color = "#ff6666"
        elif count >= 1200:
            color = "#ffcc66"
        try:
            self.query_one(f"#term-input-meta-{self._term_id}", Static).update(
                f"[{color}]{count} chars · {mode} · Enter send · Shift+Enter newline · Ctrl+Enter send multiline · Ctrl+U clear · Up/Down history[/{color}]"
            )
        except Exception:
            pass

    async def _submit_from_input_widget(self) -> None:
        text = self._get_input_text().strip()
        if not text:
            return

        if not self._prompt_history or self._prompt_history[-1] != text:
            self._prompt_history.append(text)
            if len(self._prompt_history) > 200:
                self._prompt_history = self._prompt_history[-200:]
        self._history_index = None

        self._set_input_text("")
        self._resize_input_for_text("")
        self._update_input_meta("")
        # Safely call CAIApp-specific helpers on the textual App instance
        app_obj = getattr(self, "app", None)
        if app_obj and hasattr(app_obj, "_parse_broadcast_suffix"):
            try:
                msg, is_broadcast = cast(Any, app_obj)._parse_broadcast_suffix(text)
            except Exception:
                msg, is_broadcast = text, False
        else:
            msg, is_broadcast = text, False

        if is_broadcast and msg:
            if app_obj and hasattr(app_obj, "_broadcast_prompt"):
                try:
                    await cast(Any, app_obj)._broadcast_prompt(msg, source_tid=self._term_id)
                except Exception:
                    await self.dispatch(text)
            else:
                await self.dispatch(text)
        else:
            await self.dispatch(text)

    def _history_nav(self, direction: int) -> bool:
        if not self._prompt_history:
            return False
        if self._history_index is None:
            self._history_index = len(self._prompt_history)

        self._history_index = max(
            0, min(len(self._prompt_history), self._history_index + direction)
        )
        if self._history_index >= len(self._prompt_history):
            self._set_input_text("")
            self._update_input_meta("")
            return True

        self._set_input_text(self._prompt_history[self._history_index])
        self._resize_input_for_text(self._prompt_history[self._history_index])
        self._update_input_meta(self._prompt_history[self._history_index])
        return True

    def _set_visual_state(self, state: str) -> None:
        """Apply CSS classes and input behavior for terminal visual states."""
        normalized = state if state in {"ready", "busy", "error"} else "ready"
        self.remove_class("-busy-panel")
        self.remove_class("-error-panel")
        if normalized == "busy":
            self.add_class("-busy-panel")
        elif normalized == "error":
            self.add_class("-error-panel")
        try:
            inp = self.query_one(f"#term-input-{self._term_id}", TextArea)
            inp.disabled = normalized == "busy"
        except Exception:
            pass

    def cancel_active_run(self) -> bool:
        """Cancel the in-flight run if present and return whether cancel occurred."""
        cancelled = False
        try:
            if self._run_worker is not None:
                self._run_worker.cancel()
                cancelled = True
        except Exception:
            pass
        if not cancelled:
            try:
                for worker in list(self._workers):
                    worker.cancel()
                    cancelled = True
            except Exception:
                pass
        if cancelled:
            self._busy = False
            self._set_visual_state("ready")
            self._set_status(f"T{self._term_id}> cancelled")
            self._write_system_message("progress", "run cancelled (Ctrl+C / Esc)", style="#ffaa00")
        return cancelled

    def _write_system_message(self, msg_type: str, text: str, style: str = "#00aa00") -> None:
        try:
            log = self.query_one(f"#term-log-{self._term_id}", RichLog)
            log.write(RichText(f"[system/{msg_type}] {text}", style=style))
        except Exception:
            pass

    def _render_structured_json(self, payload: str) -> bool:
        try:
            obj = json.loads(payload)
        except Exception:
            return False

        try:
            log = self.query_one(f"#term-log-{self._term_id}", RichLog)
            if isinstance(obj, dict):
                table = Table(
                    title="Structured Output", show_header=True, header_style="bold #00ff00"
                )
                table.add_column("Key", style="#00cc00")
                table.add_column("Value", style="#00ff00")
                for key, value in list(obj.items())[:30]:
                    table.add_row(str(key), json.dumps(value, ensure_ascii=True)[:220])
                log.write(table)
                return True
            if isinstance(obj, list):
                table = Table(
                    title=f"Structured Output ({len(obj)} items)",
                    show_header=True,
                    header_style="bold #00ff00",
                )
                table.add_column("Index", style="#00cc00")
                table.add_column("Value", style="#00ff00")
                for idx, value in enumerate(obj[:30]):
                    table.add_row(str(idx), json.dumps(value, ensure_ascii=True)[:220])
                log.write(table)
                return True
        except Exception:
            return False
        return False

    def _render_agent_message(self, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        log = self.query_one(f"#term-log-{self._term_id}", RichLog)

        # Prefer structured table rendering when message is pure JSON.
        if self._render_structured_json(text):
            return

        # Detect fenced code blocks and syntax-highlight when present.
        code_match = re.search(r"```(\w+)?\n([\s\S]*?)```", text)
        if code_match:
            lang = (code_match.group(1) or "text").strip()
            code = code_match.group(2)
            prefix = text[: code_match.start()].strip()
            suffix = text[code_match.end() :].strip()
            if prefix:
                log.write(Markdown(prefix))
            log.write(Syntax(code, lang, line_numbers=False, word_wrap=True))
            if suffix:
                log.write(Markdown(suffix))
            return

        # Fallback to markdown render for rich formatting and tables.
        # Wrap long lines to avoid overflowing the terminal panel width and then
        # prefer Markdown rendering; fall back to plain RichText if Markdown fails.
        try:
            size = getattr(self, "size", None) or getattr(self.app, "size", None)
            w = int(getattr(size, "width", 0) or 0)
            wrap_width = max(40, w - 8) if w > 0 else 80
        except Exception:
            wrap_width = 80
        wrapped = self._wrap_text_for_width(text, wrap_width)
        try:
            log.write(Markdown(wrapped))
        except Exception:
            try:
                log.write(RichText(wrapped))
            except Exception:
                log.write(wrapped)

    def _format_tool_output_preview(self, output: str) -> tuple[str, bool]:
        text = str(output or "")
        lines = text.splitlines()
        max_lines = 14
        max_chars = 1200
        collapsed = False

        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines])
            collapsed = True
        if len(text) > max_chars:
            text = text[:max_chars]
            collapsed = True
        # Wrap long lines to avoid overflowing the terminal panel width.
        try:
            size = getattr(self, "size", None) or getattr(self.app, "size", None)
            w = int(getattr(size, "width", 0) or 0)
            wrap_width = max(40, w - 8) if w > 0 else 80
        except Exception:
            wrap_width = 80
        text = self._wrap_text_for_width(text, wrap_width)
        return text, collapsed

    def _wrap_text_for_width(self, text: str, width: int) -> str:
        """Return text with long lines wrapped to `width` while preserving indentation.

        Uses textwrap.fill with break_long_words so extremely long tokens still break
        and won't overflow the panel box.
        """
        if not text:
            return text
        out_lines: list[str] = []
        for line in str(text).splitlines():
            if not line.strip():
                out_lines.append(line)
                continue
            # preserve leading indentation
            leading = len(line) - len(line.lstrip(" "))
            indent = " " * leading
            body = line.lstrip(" ")
            try:
                wrapped = textwrap.fill(
                    body,
                    width=max(20, width - leading),
                    replace_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=True,
                )
            except Exception:
                wrapped = body
            # reapply indentation to each wrapped sub-line
            wrapped = "\n".join(indent + l for l in wrapped.splitlines())
            out_lines.append(wrapped)
        return "\n".join(out_lines)

    def _extract_stream_text_delta(self, raw_data) -> str:
        data = raw_data
        if hasattr(data, "model_dump"):
            try:
                data = data.model_dump()
            except Exception:
                pass

        if isinstance(data, dict):
            event_type = str(data.get("type", ""))
            if "output_text" in event_type and "delta" in event_type:
                return str(data.get("delta", "") or "")
            if "delta" in data and isinstance(data.get("delta"), str):
                return str(data.get("delta") or "")
            return ""

        event_type = str(getattr(data, "type", ""))
        if "output_text" in event_type and "delta" in event_type:
            return str(getattr(data, "delta", "") or "")
        return str(getattr(data, "delta", "") or "")

    # header text helper
    def _header_text(self) -> str:
        display = _pretty_name(self._agent_name)
        return (
            f"[bold #00ff00]T{self._term_id}[/bold #00ff00]"
            f"[#004400] | [/#004400]"
            f"[#00cc00]{display}[/#00cc00]"
            f"[#004400] ▼  [/#004400]"
            f"[#00aa00]{self._model_name}[/#00aa00]"
            f"[#004400] ▼  container ▼  [/#004400]"
            f"[bold #00ff00]●[/bold #00ff00]"
            f"  [dim #666600]×[/dim #666600]"
        )

    def compose(self) -> ComposeResult:
        yield Static(
            self._header_text(),
            id=f"term-header-{self._term_id}",
            classes="term-header",
        )
        yield RichLog(
            id=f"term-log-{self._term_id}",
            classes="term-log",
            highlight=False,
            markup=True,
            wrap=True,
        )
        yield Static("", id=f"term-status-{self._term_id}", classes="term-status")
        with Horizontal(classes="term-input-row"):
            yield Static("CAI>", classes="term-input-prefix")
            with Vertical(classes="term-input-column"):
                yield TextArea(
                    text="",
                    language="markdown",
                    soft_wrap=True,
                    show_line_numbers=False,
                    compact=True,
                    placeholder="Type a prompt… Enter=send | Shift+Enter=new line | Ctrl+Enter=send multiline | Ctrl+U=clear | Up/Down=history",
                    id=f"term-input-{self._term_id}",
                    classes="term-input",
                )
                yield Static(
                    "0 chars · single-line",
                    id=f"term-input-meta-{self._term_id}",
                    classes="term-input-meta",
                )

    async def on_mount(self) -> None:
        from rich.text import Text as RichText

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        self._set_visual_state("ready")
        for line in _BANNER_LINES:
            log.write(RichText(line, style="#00ff00"))
        log.write(RichText("", style=""))
        log.write(
            RichText(
                f"T{self._term_id} ready — {_pretty_name(self._agent_name)}",
                style="#006600",
            )
        )
        self._write_system_message(
            "init",
            f"agent={self._agent_name} model={self._model_name}",
            style="#0088aa",
        )
        log.write(RichText("", style=""))
        self._resize_input_for_text("")
        self._update_input_meta("")

    @on(TextArea.Changed)
    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if (event.text_area.id or "") != f"term-input-{self._term_id}":
            return
        text = str(getattr(event.text_area, "text", "") or "")

        # ── Enter-key detection ───────────────────────────────────────────────
        # In Textual 8+, TextArea._on_key intercepts ALL printable characters
        # (including Enter) with event.stop() + event.prevent_default() so the
        # key event NEVER bubbles to TerminalPanel.on_key.  For Enter it inserts
        # '\n' into the document before stopping propagation.
        #
        # We detect the resulting document state: text ends with exactly one '\n'
        # and has NO embedded '\n' (was single-line before the keypress) → that
        # means a plain Enter was just pressed while in single-line mode → submit.
        if (
            not self._busy
            and text.endswith("\n")
            and "\n" not in text[:-1]       # no embedded newline → was single-line
        ):
            if text[:-1].strip():
                # Non-empty content → submit via the normal path
                # (_submit_from_input_widget strips the trailing \n via .strip())
                await self._submit_from_input_widget()
            else:
                # Empty Enter press → discard the newline, keep input blank
                self._set_input_text("")
                self._resize_input_for_text("")
                self._update_input_meta("")
            return

        # Normal change: resize the input to fit the content and update metadata
        self._resize_input_for_text(text)
        self._update_input_meta(text)
        try:
            lang = self._infer_input_language(text)
            if getattr(event.text_area, "language", None) != lang:
                event.text_area.language = lang
        except Exception:
            pass

    async def on_key(self, event: events.Key) -> None:
        focused = getattr(self.app.screen, "focused", None)
        if focused is None or (focused.id or "") != f"term-input-{self._term_id}":
            return

        key = event.key
        text = self._get_input_text()

        if key == "ctrl+u":
            event.prevent_default()
            event.stop()
            self._set_input_text("")
            self._resize_input_for_text("")
            self._update_input_meta("")
            return

        if key == "up" and "\n" not in text:
            event.prevent_default()
            event.stop()
            self._history_nav(-1)
            return

        if key == "down" and "\n" not in text:
            event.prevent_default()
            event.stop()
            self._history_nav(1)
            return

        if key == "ctrl+enter":
            event.prevent_default()
            event.stop()
            await self._submit_from_input_widget()
            return

        if key == "enter":
            # Safety net: in Textual 8+ this branch is unreachable because
            # TextArea._on_key consumes Enter with event.stop() before it bubbles.
            # Kept for forward-compatibility if Textual changes this behaviour.
            effective_text = text.rstrip("\n")
            if "\n" not in effective_text and effective_text.strip():
                if text != effective_text:
                    self._set_input_text(effective_text)
                event.prevent_default()
                event.stop()
                await self._submit_from_input_widget()
            return

    def on_click(self) -> None:
        self.post_message(self.Activated(self._term_id))

    def update_agent(self, agent, agent_name: str) -> None:
        """Hot-swap the agent for this terminal and refresh the header."""
        self._agent = agent
        self._agent_name = agent_name
        try:
            self.query_one(f"#term-header-{self._term_id}", Static).update(self._header_text())
        except Exception:
            pass

    # ──────────────────────────────────────────────────────── dispatch / worker

    async def dispatch(self, text: str) -> None:
        from rich.text import Text as RichText

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        log.write(RichText(f"> {text}", style="bold #00ff00"))

        cmd = text.lower().split()[0] if text.startswith("/") else ""
        if cmd in ("/exit", "/quit"):
            self.app.exit()
            return
        if cmd == "/help":
            log.write(
                RichText(
                    "  /exit /quit     Exit the TUI\n"
                    "  /clear          Clear this terminal\n"
                    "  /retry          Retry last prompt after an error\n"
                    "  /cancel         Cancel current run (same as Ctrl+C / Esc)\n"
                    "  /help           Show this message\n"
                    "  tip             Append ' all' to broadcast a prompt to T1-T4\n"
                    "\n"
                    "  ^q  Exit    ^l  Clear    ^c  Cancel    ^s  Sidebar\n"
                    "  Esc Cancel",
                    style="#00cc00",
                )
            )
            return
        if cmd in ("/clear", "/cls"):
            log.clear()
            self._write_system_message("context", "terminal output reset", style="#ffaa00")
            return

        if cmd == "/expand":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                log.write(RichText("  usage: /expand <tool_call_id>", style="#ffcc00"))
                return
            call_id = parts[1].strip()
            full = self._tool_outputs_by_call_id.get(call_id)
            if full is None:
                log.write(RichText(f"  no stored output for call_id={call_id}", style="#ff6600"))
                return
            log.write(RichText(f"  [expanded output] call_id={call_id}", style="#00cc00"))
            self._render_agent_message(full)
            return

        if cmd in ("/cancel", "/stop"):
            if self.cancel_active_run():
                log.write(RichText("  cancelled active run", style="#ffaa00"))
            else:
                log.write(RichText("  no active run", style="#ffcc00"))
            return

        if cmd == "/retry":
            if self._busy:
                log.write(RichText("  terminal is busy; retry unavailable", style="#ffcc00"))
                return
            if not self._last_prompt_text:
                log.write(RichText("  no previous prompt to retry", style="#ffcc00"))
                return
            log.write(RichText(f"  retrying: {self._last_prompt_text[:120]}", style="#00cc00"))
            self._set_visual_state("ready")
            self._set_status(f"T{self._term_id}> retrying previous prompt")
            self._busy = True
            self._set_visual_state("busy")
            self._run_worker = self._run_agent(self._last_prompt_text)
            return

        if self._busy:
            log.write(
                RichText(
                    "  [busy] Working... cancel with Ctrl+C, Esc, or /cancel",
                    style="#ffcc00",
                )
            )
            return

        if not cmd:
            try:
                try:
                    app_obj = getattr(self, "app", None)
                    can_dispatch = True
                    if app_obj and hasattr(app_obj, "_can_dispatch_prompt"):
                        can_dispatch = cast(Any, app_obj)._can_dispatch_prompt()
                    if not can_dispatch:
                        log.write(
                            RichText(
                                "  [paused] Price limit exceeded. Increase CAI_PRICE_LIMIT or start a new session.",
                                style="#ff4444",
                            )
                        )
                        return
                except Exception:
                    pass
            except Exception:
                pass

        if self._agent is None:
            log.write(
                RichText(
                    "  No agent loaded. Select one from the sidebar.",
                    style="#ff4444",
                )
            )
            return

        # Open full config overview with /config
        if cmd == "/config":
            try:
                # Display a full config table in the active terminal's right-hand area
                app_obj = getattr(self, "app", None)
                try:
                    getattr(cast(Any, app_obj), "_display_config_table", lambda: None)()
                except Exception:
                    pass
                # Also schedule the interactive full-config worker (overview/edit loop)
                try:
                    getattr(cast(Any, app_obj), "_open_config_screen", lambda *a, **k: None)(
                        "full-config"
                    )
                except Exception:
                    pass
            except Exception:
                pass
            return

        ts = datetime.now().strftime("%H:%M:%S")
        self._last_prompt_text = text
        self._busy = True
        self._set_visual_state("busy")
        self._set_status(f"T{self._term_id}> [{ts}] ⟳ Working… (Ctrl+C to cancel)")
        # Mandatory handoff trace — visible on stderr even when Textual occupies
        # the terminal.  Confirms input crossed the UI→Runner boundary.
        try:
            import sys as _sys
            _sys.stderr.write(
                f"[CAI-TUI] handoff: T{self._term_id} agent={self._agent_name!r} "
                f"text_len={len(text)} preview={text[:80]!r}\n"
            )
            _sys.stderr.flush()
            logger.info(
                "[TUI-handoff] term=%d agent=%r text_len=%d",
                self._term_id, self._agent_name, len(text),
            )
        except Exception:
            pass
        self._run_worker = self._run_agent(text)

    @work(exclusive=True)
    async def _run_agent(self, text: str) -> None:
        from rich.text import Text as RichText

        from cai.sdk.agents import Runner
        from cai.sdk.agents.items import ToolCallOutputItem
        from cai.sdk.agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent

        log = self.query_one(f"#term-log-{self._term_id}", RichLog)
        stream_iter = None
        result = None
        run_status = "completed"
        run_error: str | None = None
        streamed_chars = 0

        # Temporary debug tracing for stream event diagnosis
        try:
            import sys as _sys
            _sys.stderr.write(
                f"[cai-tui-debug] _run_agent start: term={self._term_id} "
                f"agent={self._agent_name} model={self._model_name} "
                f"text_len={len(str(text or ''))}\n"
            )
            _sys.stderr.flush()
            logger.debug(
                "_run_agent start: term=%s agent=%s model=%s text_len=%d",
                self._term_id,
                self._agent_name,
                self._model_name,
                len(str(text or "")),
            )
        except Exception:
            pass
        try:
            getattr(cast(Any, self.app), "_telemetry_run_started", lambda *a, **k: None)(
                self._term_id, self._agent_name, text
            )
        except Exception:
            pass
        try:
            try:
                import sys as _sys
                _sys.stderr.write(f"[cai-tui-debug] calling Runner.run_streamed term={self._term_id}\n")
                _sys.stderr.flush()
                logger.debug("calling Runner.run_streamed for term=%s", self._term_id)
            except Exception:
                pass

            # Suppress CAI_STREAM and CAI_STREAM_DEBUG while the TUI worker
            # runs so the underlying model driver doesn't try to render its
            # own Rich streaming panel (which would bleed into the RichLog).
            # The TUI renders the final message via message_output_created /
            # _render_agent_message — it does not need nor want the raw delta
            # debug lines that stream_debug produces.
            _prev_cai_stream = os.environ.get("CAI_STREAM")
            _prev_cai_stream_debug = os.environ.get("CAI_STREAM_DEBUG")
            try:
                os.environ["CAI_STREAM"] = "false"
                os.environ["CAI_STREAM_DEBUG"] = "0"
            except Exception:
                pass

            result = Runner.run_streamed(self._agent, text)
            stream_iter = result.stream_events()

            try:
                import sys as _sys
                _sys.stderr.write(f"[cai-tui-debug] stream iterator obtained term={self._term_id}\n")
                _sys.stderr.flush()
                logger.debug("stream iterator obtained for term=%s", self._term_id)
            except Exception:
                pass

            # stream_debug is always False in the TUI — the TUI renders the
            # final agent reply via _render_agent_message (message_output_created).
            stream_debug = False
            _stream_line_buf: str = ""

            async for event in stream_iter:
                try:
                    logger.debug("received stream event type=%s", type(event).__name__)
                except Exception:
                    pass

                if isinstance(event, RawResponsesStreamEvent):
                    delta = self._extract_stream_text_delta(event.data)
                    try:
                        import sys as _sys
                        _sys.stderr.write(f"[cai-tui-debug] RAW_EVENT type={getattr(event.data,'type',type(event.data).__name__)} delta_len={len(delta or '')}\n")
                        _sys.stderr.flush()
                        logger.debug(
                            "raw delta len=%d preview=%r",
                            len(delta or ""),
                            (delta or "")[:200],
                        )
                    except Exception:
                        pass
                    if delta:
                        streamed_chars += len(delta)
                        self._set_status(f"T{self._term_id}> ⟳ Streaming… {streamed_chars} chars")
                        if stream_debug:
                            # Accumulate into line buffer; flush only on newlines
                            # so each log.write() renders a complete line of text
                            # rather than a single token.
                            _stream_line_buf += str(delta)
                            while "\n" in _stream_line_buf:
                                line, _stream_line_buf = _stream_line_buf.split("\n", 1)
                                if line:  # skip blank splits
                                    try:
                                        log.write(RichText(
                                            f"[stream:{self._term_id}] {line}",
                                            style="#3399ff",
                                        ))
                                    except Exception:
                                        pass
                    continue

                if not isinstance(event, RunItemStreamEvent):
                    continue
                ev_name = event.name
                item = event.item
                try:
                    logger.debug(
                        "run item event: name=%s item_type=%s",
                        ev_name,
                        getattr(item, "type", "<unknown>"),
                    )
                except Exception:
                    pass

                try:
                    getattr(cast(Any, self.app), "_emit_telemetry", lambda *a, **k: None)(
                        self._term_id,
                        self._agent_name,
                        "stream_event",
                        {"name": ev_name, "item_type": getattr(item, "type", "unknown")},
                    )
                except Exception:
                    pass

                if ev_name == "message_output_created":
                    try:
                        getattr(
                            cast(Any, self.app), "_telemetry_first_token", lambda *a, **k: None
                        )(self._term_id, self._agent_name)
                    except Exception:
                        pass
                    try:
                        from cai.sdk.agents.items import ItemHelpers

                        content = ItemHelpers.text_message_output(cast(Any, item))
                    except Exception:
                        content = str(getattr(item, "content", ""))
                    if content:
                        self._render_agent_message(content)

                elif ev_name == "reasoning_item_created":
                    self._write_system_message(
                        "progress", "reasoning step created", style="#00cc88"
                    )

                elif ev_name == "tool_called":
                    raw = getattr(item, "raw_item", item)
                    fn_name = getattr(
                        raw,
                        "name",
                        getattr(getattr(raw, "function", None), "name", "tool"),
                    )
                    fn_args = str(getattr(raw, "arguments", "…"))
                    call_id = str(getattr(raw, "call_id", "unknown"))
                    if len(fn_args) > 80:
                        fn_args = fn_args[:80] + "…"
                    self._active_tool_calls[call_id] = {
                        "name": str(fn_name),
                        "args": fn_args,
                    }
                    log.write(RichText(f"  ▶ {fn_name}({fn_args}) [running]", style="#006600"))
                    try:
                        getattr(
                            cast(Any, self.app), "_telemetry_tool_called", lambda *a, **k: None
                        )(
                            self._term_id,
                            self._agent_name,
                            str(fn_name),
                            call_id,
                            str(getattr(raw, "arguments", "")),
                        )
                    except Exception:
                        pass

                elif ev_name == "tool_output":
                    if isinstance(item, ToolCallOutputItem):
                        call_id = "unknown"
                        try:
                            raw_item = getattr(item, "raw_item", {}) or {}
                            if isinstance(raw_item, dict):
                                call_id = str(raw_item.get("call_id", "unknown"))
                            else:
                                call_id = str(getattr(raw_item, "call_id", "unknown"))
                        except Exception:
                            call_id = "unknown"

                        full_output = str(item.output)
                        self._tool_outputs_by_call_id[call_id] = full_output
                        preview, collapsed = self._format_tool_output_preview(full_output)
                        name = self._active_tool_calls.get(call_id, {}).get("name", "tool")
                        log.write(
                            RichText(f"  ✓ {name} [success] call_id={call_id}", style="#00cc00")
                        )
                        self._render_agent_message(preview)
                        if collapsed:
                            remaining = max(
                                0, len(full_output.splitlines()) - len(preview.splitlines())
                            )
                            log.write(
                                RichText(
                                    f"    … collapsed {remaining} lines, use /expand {call_id} for full output",
                                    style="#004400",
                                )
                            )
                        try:
                            out_preview = preview.splitlines()[0] if preview else full_output
                            getattr(
                                cast(Any, self.app), "_telemetry_tool_output", lambda *a, **k: None
                            )(
                                self._term_id,
                                self._agent_name,
                                call_id,
                                out_preview,
                            )
                        except Exception:
                            pass
                        if call_id in self._active_tool_calls:
                            self._active_tool_calls.pop(call_id, None)

        except asyncio.CancelledError:
            run_status = "cancelled"
            log.write(RichText("  [cancelled]", style="#ff6600"))
            self._write_system_message("progress", "run cancelled", style="#ffaa00")
        except Exception as exc:
            # ContextCompactedError is a resumption signal, not a fatal error.
            # The context window was auto-compacted; reload the agent and
            # re-submit with a continuation prompt, exactly as the CLI does.
            try:
                from cai.sdk.agents.exceptions import ContextCompactedError as _CCE
                _is_compact = isinstance(exc, _CCE)
            except Exception:
                _is_compact = "ContextCompactedError" in type(exc).__name__

            if _is_compact:
                try:
                    run_status = "completed"
                    self._write_system_message(
                        "progress",
                        "✓ Context window compacted — resuming task",
                        style="#00cc88",
                    )
                    # Reload agent via AGENT_MANAGER in case it was refreshed.
                    try:
                        from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER as _AM
                        _reloaded = _AM.get_active_agent()
                        if _reloaded is not None:
                            self._agent = _reloaded
                    except Exception:
                        pass
                    _continuation = (
                        f"{self._last_prompt_text}\n\n"
                        "IMPORTANT: Your context window was just compacted. "
                        "Your session memory is already loaded above. "
                        "Review the 'Exhausted Approaches' section in your memory and "
                        "DO NOT repeat any technique, command, URL, port scan, or login "
                        "attempt already listed there. "
                        "Pick up exactly where you left off using only NEW approaches."
                    )
                    # Schedule a fresh run with the continuation prompt.
                    self.call_after_refresh(
                        lambda: self._run_worker.__class__  # keep linter happy
                    )
                    self._run_worker = self._run_agent(_continuation)
                    return  # skip the error-state path below
                except Exception as _retry_exc:
                    # If the retry scheduling itself fails, fall through to
                    # the generic error handler so the user is informed.
                    import sys as _sys
                    _sys.stderr.write(f"[cai-tui-debug] compact retry failed: {_retry_exc!r}\n")
                    _sys.stderr.flush()

            run_status = "error"
            run_error = str(exc)
            try:
                import sys as _sys
                _sys.stderr.write(f"[cai-tui-debug] EXCEPTION in _run_agent: {exc!r}\n")
                _sys.stderr.flush()
            except Exception:
                pass
            # Mark any running tool calls as errored in the log.
            for call_id, meta in list(self._active_tool_calls.items()):
                log.write(
                    RichText(
                        f"  ✗ {meta.get('name', 'tool')} [error] call_id={call_id}",
                        style="#ff4444",
                    )
                )
            self._active_tool_calls.clear()
            # Render a single clean error panel — avoids printing the same
            # exception message twice (once raw, once via _write_system_message).
            try:
                from rich.panel import Panel as _Panel
                log.write(
                    _Panel(
                        f"[bold]{type(exc).__name__}[/bold]: {exc}",
                        title="[bold red]Error[/bold red]",
                        border_style="red",
                        padding=(0, 1),
                    )
                )
            except Exception:
                log.write(RichText(f"  [error] {exc}", style="#ff4444"))
            log.write(RichText("  hint: use /retry to run the last prompt again", style="#ff6666"))
        finally:
            self._busy = False
            self._run_worker = None
            # Restore CAI_STREAM / CAI_STREAM_DEBUG to their original values.
            try:
                if _prev_cai_stream is None:
                    os.environ.pop("CAI_STREAM", None)
                else:
                    os.environ["CAI_STREAM"] = _prev_cai_stream
                if _prev_cai_stream_debug is None:
                    os.environ.pop("CAI_STREAM_DEBUG", None)
                else:
                    os.environ["CAI_STREAM_DEBUG"] = _prev_cai_stream_debug
            except Exception:
                pass
            # Flush any partial line left in the streaming buffer (no-op when stream_debug=False)
            try:
                if stream_debug and _stream_line_buf.strip():
                    log.write(RichText(
                        f"[stream:{self._term_id}] {_stream_line_buf}",
                        style="#3399ff",
                    ))
            except Exception:
                pass
            if stream_iter is not None:
                try:
                    await cast(Any, stream_iter).aclose()
                except Exception:
                    pass
            try:
                status_text = getattr(
                    cast(Any, self.app), "_telemetry_run_finished", lambda *a, **k: ""
                )(
                    self._term_id,
                    self._agent_name,
                    result,
                    run_status,
                    run_error,
                )
            except Exception:
                status_text = ""
            self._set_status(status_text)
            if run_status == "error":
                self._set_visual_state("error")
            else:
                self._set_visual_state("ready")

    def _set_status(self, text: str) -> None:
        try:
            out = str(text or "")
            try:
                if getattr(self.app, "_responsive_mode", "medium") == "small" and len(out) > 52:
                    out = out[:51] + "…"
            except Exception:
                pass
            self.query_one(f"#term-status-{self._term_id}", Static).update(out)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Custom header widget
# ---------------------------------------------------------------------------
class CaiHeader(Widget):
    """Single-line status bar: tab labels on the left, agent/model on right."""

    DEFAULT_CSS = """
    CaiHeader {
        height: 1;
        dock: top;
        background: #001a00;
        layout: horizontal;
    }
    """

    def __init__(
        self,
        agent_name: str = "one_tool_agent",
        model: str = "alias1",
        ctx: str = "container",
    ) -> None:
        super().__init__()
        self._agent_name = agent_name
        self._model = model
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-left"):
            yield Static(
                "[bold #00ff00]T1[/bold #00ff00][#004400] | [/#004400][#00ff00]Terminal[/#00ff00]",
                id="header-left-text",
            )
            with Horizontal(id="header-nav"):
                yield Button("Terminal", id="top-nav-terminal", classes="top-nav-btn")
                yield Button("Agents", id="top-nav-agents", classes="top-nav-btn")
                yield Button("Queue", id="top-nav-queue", classes="top-nav-btn")
                yield Button("Sessions", id="top-nav-sessions", classes="top-nav-btn")
                yield Button("Config", id="top-nav-config", classes="top-nav-btn")
                yield Button("Tools", id="top-nav-tools", classes="top-nav-btn")
                yield Button("Stats", id="top-nav-metrics", classes="top-nav-btn")
                yield Button("Menu", id="header-menu")
        yield Static(
            f"[#00cc00]{self._agent_name}[/#00cc00][#004400] ▼ [/#004400]"
            f"[#00cc00]{self._model}[/#00cc00][#004400] ▼ [/#004400]"
            f"[#006600]{self._ctx}[/#006600][#004400] ▼  [/#004400]"
            "[bold #00ff00]●[/bold #00ff00]",
            id="header-right",
        )


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class CAIApp(App):
    """Matrix-themed TUI with multi-terminal sidebar."""

    TITLE = "CAI"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Exit", show=True),
        Binding("ctrl+l", "clear_active", "Clear", show=True),
        Binding("ctrl+c", "cancel_active", "Cancel", show=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar", show=True),
        Binding("escape", "cancel_active", "Cancel", show=True),
        Binding("ctrl+p", "command_palette", "Palette", show=True),
    ]

    CSS = _CSS

    def __init__(
        self,
        agent=None,
        initial_prompt: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._agent = agent
        self._agent_name: str = (
            getattr(agent, "name", "one_tool_agent") if agent else "one_tool_agent"
        )
        self._model_name: str = os.getenv("CAI_MODEL", "alias1")
        self._initial_prompt = initial_prompt
        self._sidebar_visible = True
        self._available_agents: dict = {}
        self._active_team: Optional[int] = None
        self._queue_items: list[dict] = []
        self._queue_selected_idx: Optional[int] = None
        self._queue_running: bool = False
        self._queue_broadcast_mode: bool = False
        self._tool_registry: dict[str, dict] = {}
        self._tool_call_history: list[dict] = []
        self._selected_tool_id: Optional[str] = None
        self._selected_tool_call_idx: Optional[int] = None
        self._tool_button_id_to_tool_id: dict[str, str] = {}
        self._tool_tool_id_to_button_id: dict[str, str] = {}
        self._tool_calls_file: str = TOOL_CALLS_FILE
        self._tool_calls_max_bytes: int = TOOL_CALLS_MAX_BYTES
        self._tool_calls_max_backups: int = TOOL_CALLS_MAX_BACKUPS
        self._inject_mode: str = "input"
        self._telemetry_file: str = TELEMETRY_FILE
        self._telemetry_max_bytes: int = TELEMETRY_MAX_BYTES
        self._telemetry_max_backups: int = TELEMETRY_MAX_BACKUPS
        self._context_snapshots_file: str = CONTEXT_SNAPSHOTS_FILE
        self._context_snapshots_max_bytes: int = CONTEXT_SNAPSHOTS_MAX_BYTES
        self._context_snapshots_max_backups: int = CONTEXT_SNAPSHOTS_MAX_BACKUPS
        self._telemetry_pending_runs: dict[int, dict] = {}
        self._telemetry_pending_tool_calls: dict[str, dict] = {}
        self._telemetry_stats_by_term: dict[int, dict] = {}
        self._context_snapshot_by_term: dict[int, dict] = (
            self._load_context_snapshots_latest_by_term()
        )
        self._stats_started_ts: float = time.time()
        self._price_limit_warned: bool = False
        self._price_limit_paused: bool = False
        self._responsive_mode: str = "medium"
        self._responsive_label_cache: dict[str, str] = {}
        self._command_palette_recent: list[str] = []
        # terminal tracking
        self._next_term_id = 1
        self._active_term_id = 1

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _ensure_term_stats(self, term_id: int) -> dict:
        stats = self._telemetry_stats_by_term.get(term_id)
        if stats is None:
            stats = {
                "runs": 0,
                "errors": 0,
                "cancelled": 0,
                "tool_calls": 0,
                "retrieval_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0.0,
                "last_cost": 0.0,
                "model": self._model_name,
                "input_price_per_token": 0.0,
                "output_price_per_token": 0.0,
                "sum_first_token_ms": 0,
                "sum_total_latency_ms": 0,
                "last_first_token_ms": None,
                "last_total_latency_ms": None,
                "last_status": "idle",
            }
            self._telemetry_stats_by_term[term_id] = stats
        return stats

    def _is_retrieval_tool(self, tool_name: str) -> bool:
        name = (tool_name or "").lower()
        retrieval_markers = (
            "search",
            "retriev",
            "rag",
            "file_search",
            "web_search",
            "google",
            "shodan",
            "mcp",
        )
        return any(marker in name for marker in retrieval_markers)

    def _rotate_telemetry_if_needed(self) -> None:
        try:
            if not os.path.exists(self._telemetry_file):
                return
            if os.path.getsize(self._telemetry_file) <= self._telemetry_max_bytes:
                return

            max_backups = max(1, int(self._telemetry_max_backups))
            oldest = f"{self._telemetry_file}.{max_backups}"
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except Exception:
                    pass

            for i in range(max_backups - 1, 0, -1):
                src = f"{self._telemetry_file}.{i}"
                dst = f"{self._telemetry_file}.{i + 1}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass

            try:
                os.replace(self._telemetry_file, f"{self._telemetry_file}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _persist_telemetry_record(self, record: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._telemetry_file), exist_ok=True)
            self._rotate_telemetry_if_needed()
            with open(self._telemetry_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            pass

    def _emit_telemetry(self, term_id: int, agent_name: str, event_type: str, data: dict) -> None:
        rec = {
            "event": event_type,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ts_ms": self._now_ms(),
            "terminal_id": term_id,
            "agent_name": agent_name,
            "data": data,
        }
        self._persist_telemetry_record(rec)

    def _load_recent_telemetry_events(self, limit: int = 20) -> list[dict]:
        records: list[dict] = []
        try:
            paths: list[str] = []
            for i in range(self._telemetry_max_backups, 0, -1):
                p = f"{self._telemetry_file}.{i}"
                if os.path.exists(p):
                    paths.append(p)
            if os.path.exists(self._telemetry_file):
                paths.append(self._telemetry_file)

            for path in paths:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                            if isinstance(obj, dict):
                                records.append(obj)
                        except Exception:
                            continue
        except Exception:
            return []
        if limit <= 0:
            return records
        return records[-limit:]

    def _render_metrics_summary_text(self) -> str:
        if not self._telemetry_stats_by_term:
            return "No stats yet. Run a prompt to collect cost and usage metrics."

        def _fmt_cost(value: float) -> str:
            return f"${float(value):.2f}"

        def _fmt_elapsed(seconds: float) -> str:
            total = max(0, int(seconds))
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            if h > 0:
                return f"{h}h {m}m {s}s"
            if m > 0:
                return f"{m}m {s}s"
            return f"{s}s"

        total_runs = 0
        total_tokens = 0
        total_tools = 0
        total_retr = 0
        total_errors = 0
        total_cost = 0.0
        active_terms = 0

        lines: list[str] = ["[bold]Stats[/bold]"]
        lines.append(f"Total Cost: {_fmt_cost(self._session_cost_total())}")
        lines.append("═══════════════════════")

        pricing_lines: list[str] = []

        for term_id in sorted(self._telemetry_stats_by_term.keys()):
            s = self._telemetry_stats_by_term[term_id]
            total_runs += int(s.get("runs", 0) or 0)
            total_tokens += int(s.get("total_tokens", 0) or 0)
            total_tools += int(s.get("tool_calls", 0) or 0)
            total_retr += int(s.get("retrieval_calls", 0) or 0)
            total_errors += int(s.get("errors", 0) or 0)
            term_cost = float(s.get("cost_total", 0.0) or 0.0)
            total_cost += term_cost
            if s.get("last_status") == "running":
                active_terms += 1

            lines.append(f"Terminal {term_id}: {_fmt_cost(term_cost)}")

            model_name = str(s.get("model", self._model_name) or self._model_name)
            in_price = float(s.get("input_price_per_token", 0.0) or 0.0)
            out_price = float(s.get("output_price_per_token", 0.0) or 0.0)
            pricing_lines.append(
                f"T{term_id}: model={model_name} in={in_price:.8f} out={out_price:.8f}"
            )

        if active_terms <= 0:
            try:
                active_terms = len(list(self.query(TerminalPanel)))
            except Exception:
                active_terms = 0

        avg_cost_per_turn = total_cost / max(1, total_runs)
        elapsed = _fmt_elapsed(time.time() - self._stats_started_ts)

        lines.append("")
        lines.append("[bold]Usage Metrics[/bold]")
        lines.append(f"Interactions: {total_runs}")
        lines.append(f"Total tokens: {total_tokens}")
        lines.append(f"Average cost per turn: {_fmt_cost(avg_cost_per_turn)}")
        lines.append(f"Time elapsed: {elapsed}")
        lines.append(f"Active terminals: {active_terms}")
        lines.append("")
        lines.append("[bold]Model pricing details[/bold]")
        lines.extend(pricing_lines)
        lines.append("")

        price_limit, enabled = self._get_price_limit()
        if enabled:
            pct = (total_cost / max(price_limit, 1e-9)) * 100.0
            state = "PAUSED" if self._price_limit_paused else ("WARNING" if pct >= 80.0 else "OK")
            lines.append(
                f"Cost limit: CAI_PRICE_LIMIT={price_limit:.2f} · used={_fmt_cost(total_cost)} ({pct:.1f}%) · {state}"
            )
        else:
            lines.append("Cost limit: disabled (set CAI_PRICE_LIMIT > 0 to enable)")

        lines.append(f"Diagnostics: tools={total_tools} retr={total_retr} errors={total_errors}")
        return "\n".join(lines)

    def _get_price_limit(self) -> tuple[float, bool]:
        raw = os.getenv("CAI_PRICE_LIMIT", "").strip()
        if not raw:
            return (0.0, False)
        try:
            value = float(raw)
            if value <= 0:
                return (0.0, False)
            return (value, True)
        except Exception:
            return (0.0, False)

    def _session_cost_total(self) -> float:
        total = 0.0
        for s in self._telemetry_stats_by_term.values():
            try:
                total += float(s.get("cost_total", 0.0) or 0.0)
            except Exception:
                continue
        return total

    def _refresh_price_limit_state(self, emit_logs: bool = True) -> None:
        limit, enabled = self._get_price_limit()
        if not enabled:
            self._price_limit_warned = False
            self._price_limit_paused = False
            return

        total_cost = self._session_cost_total()
        ratio = total_cost / max(limit, 1e-9)

        if ratio >= 1.0:
            if emit_logs and not self._price_limit_paused:
                self._log_to_active_terminal(
                    f"[cost] limit exceeded ({total_cost:.4f}/{limit:.4f}). Auto-pausing new prompts.",
                    style="#ff4444",
                )
            self._price_limit_paused = True
            return

        self._price_limit_paused = False
        if ratio >= 0.8 and not self._price_limit_warned:
            self._price_limit_warned = True
            if emit_logs:
                self._log_to_active_terminal(
                    f"[cost] warning: approaching limit ({total_cost:.4f}/{limit:.4f}).",
                    style="#ffcc00",
                )
        elif ratio < 0.8:
            self._price_limit_warned = False

    def _can_dispatch_prompt(self) -> bool:
        self._refresh_price_limit_state(emit_logs=True)
        return not self._price_limit_paused

    def _render_metrics_events_text(self, limit: int = 20) -> str:
        events = self._load_recent_telemetry_events(limit=limit)
        if not events:
            return "No telemetry events yet."

        lines: list[str] = []
        for rec in events[-limit:]:
            ts = rec.get("timestamp", "?")
            terminal = rec.get("terminal_id", "?")
            event_name = rec.get("event", "?")
            data = rec.get("data", {}) or {}
            if isinstance(data, dict):
                if "tool_name" in data:
                    detail = f" tool={data.get('tool_name')}"
                elif "latency_ms" in data:
                    detail = f" latency={data.get('latency_ms')}ms"
                elif "status" in data:
                    detail = f" status={data.get('status')}"
                else:
                    detail = ""
            else:
                detail = ""
            lines.append(f"{ts} · T{terminal} · {event_name}{detail}")
        return "\n".join(lines)

    def _update_metrics_view(self) -> None:
        try:
            summary = self.query_one("#metrics-summary", Static)
            summary_text = self._render_metrics_summary_text()
            try:
                summary.update(RichText.from_markup(summary_text))
            except Exception:
                summary.update(summary_text)
        except Exception:
            pass

        try:
            events = self.query_one("#metrics-events", Static)
            events_text = self._render_metrics_events_text(limit=20)
            events.update(events_text)
        except Exception:
            pass

    @work(exclusive=False)
    async def _refresh_metrics_view_worker(self) -> None:
        self._update_metrics_view()

    def _run_key(self, term_id: int) -> int:
        return term_id

    def _tool_key(self, term_id: int, call_id: str) -> str:
        return f"{term_id}:{call_id}"

    def _estimate_tokens_from_text(self, text: str) -> int:
        raw = str(text or "")
        # Fast and deterministic token estimate for UI-only attribution.
        return max(0, (len(raw) + 3) // 4)

    def _format_k_tokens(self, count: int) -> str:
        value = int(count or 0)
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)

    def _usage_field(self, obj, key: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _usage_details_to_dict(self, details) -> dict:
        if details is None:
            return {}
        if isinstance(details, dict):
            return details
        if hasattr(details, "model_dump"):
            try:
                dumped = details.model_dump()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
        if hasattr(details, "__dict__"):
            try:
                as_dict = dict(details.__dict__)
                if isinstance(as_dict, dict):
                    return as_dict
            except Exception:
                pass
        return {}

    def _extract_usage_detail_totals(self, usage) -> dict:
        """Extract normalized usage totals from provider responses when available."""
        input_tokens = int(
            self._usage_field(usage, "input_tokens", 0)
            or self._usage_field(usage, "prompt_tokens", 0)
            or 0
        )
        output_tokens = int(
            self._usage_field(usage, "output_tokens", 0)
            or self._usage_field(usage, "completion_tokens", 0)
            or 0
        )
        total_tokens = int(self._usage_field(usage, "total_tokens", 0) or 0)
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens

        input_details = self._usage_details_to_dict(
            self._usage_field(usage, "input_tokens_details", None)
            or self._usage_field(usage, "prompt_tokens_details", None)
        )
        output_details = self._usage_details_to_dict(
            self._usage_field(usage, "output_tokens_details", None)
            or self._usage_field(usage, "completion_tokens_details", None)
        )

        cached_tokens = int(input_details.get("cached_tokens", 0) or 0)
        reasoning_tokens = int(
            output_details.get("reasoning_tokens", 0)
            or self._usage_field(usage, "reasoning_tokens", 0)
            or 0
        )

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
        }

    def _context_usage_level(self, pct: float) -> tuple[str, str]:
        if pct < 50.0:
            return ("GREEN", "green")
        if pct < 80.0:
            return ("YELLOW", "yellow")
        return ("RED", "red")

    def _resolve_model_context_max_tokens(self, model_name: str) -> int:
        name = str(model_name or "").lower().strip()
        try:
            from cai.util import get_model_input_tokens

            max_tokens = int(get_model_input_tokens(name) or 0)
            if max_tokens > 0:
                return max_tokens
        except Exception:
            pass

        # Fallback map for safety when util lookup is unavailable.
        fallback_map = {
            "alias1": 128000,
            "gpt-4": 128000,
            "gpt-4o": 128000,
            "gpt-5": 200000,
            "claude": 200000,
            "sonnet": 200000,
        }
        for key, value in fallback_map.items():
            if key in name:
                return value
        return 128000

    def _context_categories_blank(self) -> dict:
        return {
            "system_prompt_tokens": 0,
            "tool_definitions_tokens": 0,
            "memory_rag_tokens": 0,
            "user_prompt_tokens": 0,
            "assistant_response_tokens": 0,
            "tool_calls_tokens": 0,
            "tool_results_tokens": 0,
        }

    def _context_snapshot_summary_text(self, snapshot: dict) -> str:
        used = int(snapshot.get("used_tokens", 0) or 0)
        max_tokens = int(snapshot.get("max_tokens", 0) or 0)
        pct = float(snapshot.get("pct_used", 0.0) or 0.0)
        free = int(snapshot.get("free_tokens", 0) or 0)
        last_input = int(snapshot.get("last_input_tokens", 0) or 0)
        return (
            f"Context usage T{snapshot.get('terminal_id', '?')}: "
            f"used {used}/{max_tokens} ({pct:.1f}%), free {free}, last_input {last_input}"
        )

    def _render_context_usage_menu_text(self, snapshot: dict) -> str:
        if not snapshot:
            return "No context data yet. Run one prompt to initialize."

        used = int(snapshot.get("used_tokens", 0) or 0)
        max_tokens = int(snapshot.get("max_tokens", 0) or 0)
        free = int(snapshot.get("free_tokens", 0) or 0)
        pct = float(snapshot.get("pct_used", 0.0) or 0.0)
        last_input = int(snapshot.get("last_input_tokens", 0) or 0)
        categories = snapshot.get("categories", {}) or {}

        bar_width = 28
        fill = 0
        if max_tokens > 0:
            fill = min(bar_width, max(0, int(round((used / max_tokens) * bar_width))))
        bar = ("#" * fill) + ("-" * (bar_width - fill))

        lines: list[str] = [
            f"[bold]Context Usage · T{snapshot.get('terminal_id', '?')}[/bold]",
            f"Model: {snapshot.get('model', '?')} · Agent: {snapshot.get('agent_name', '?')}",
            f"Used: {self._format_k_tokens(used)} / {self._format_k_tokens(max_tokens)} ({pct:.1f}%)",
            f"Free: {self._format_k_tokens(free)} · Last input: {self._format_k_tokens(last_input)}",
            f"[{bar}]",
            "",
            "[bold]Context Level[/bold]",
            f"Level: [{self._context_usage_level(pct)[1]}]{self._context_usage_level(pct)[0]}[/{self._context_usage_level(pct)[1]}]",
            "Legend: [green]GREEN[/green] < 50% · [yellow]YELLOW[/yellow] 50-79% · [red]RED[/red] >= 80%",
            "",
            "[bold]Category Breakdown[/bold]",
        ]

        ordered = [
            ("System prompt", "system_prompt_tokens"),
            ("Tool definitions", "tool_definitions_tokens"),
            ("Memory / RAG", "memory_rag_tokens"),
            ("User prompts", "user_prompt_tokens"),
            ("Assistant responses", "assistant_response_tokens"),
            ("Tool calls", "tool_calls_tokens"),
            ("Tool results", "tool_results_tokens"),
        ]
        denom = max(1, used)
        for label, key in ordered:
            value = int(categories.get(key, 0) or 0)
            cpct = (value / denom) * 100.0
            lines.append(f"- {label}: {self._format_k_tokens(value)} ({cpct:.1f}%)")

        lines.append("")
        lines.append(f"Updated: {snapshot.get('timestamp', '?')}")
        return "\n".join(lines)

    def _build_context_snapshot(
        self,
        term_id: int,
        agent_name: str,
        model_name: str,
        run_data: dict,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        usage_details: dict | None = None,
    ) -> dict:
        categories = self._context_categories_blank()
        details = usage_details or {}

        run_categories = (run_data or {}).get("categories", {}) or {}
        for key in categories.keys():
            categories[key] = int(run_categories.get(key, 0) or 0)

        # Prefer usage-reported values when available.
        if input_tokens > 0:
            categories["user_prompt_tokens"] = input_tokens
        if output_tokens > 0:
            categories["assistant_response_tokens"] = output_tokens

        # Provider-side details (when available) refine attribution.
        # cached_tokens are usually prompt prefix/system/tool material reused across calls.
        # reasoning_tokens are part of assistant generation budget.
        cached_tokens = int(details.get("cached_tokens", 0) or 0)
        reasoning_tokens = int(details.get("reasoning_tokens", 0) or 0)
        if cached_tokens > 0:
            categories["system_prompt_tokens"] += cached_tokens
            categories["user_prompt_tokens"] = max(
                0, int(categories["user_prompt_tokens"]) - cached_tokens
            )
        if reasoning_tokens > 0:
            categories["assistant_response_tokens"] += reasoning_tokens

        used_tokens = int(total_tokens or 0)
        estimated_sum = sum(int(v or 0) for v in categories.values())
        if used_tokens <= 0:
            used_tokens = estimated_sum

        # Keep breakdown bounded to used tokens for sane percentages.
        fixed = (
            int(categories["system_prompt_tokens"])
            + int(categories["tool_definitions_tokens"])
            + int(categories["memory_rag_tokens"])
            + int(categories["user_prompt_tokens"])
            + int(categories["assistant_response_tokens"])
        )
        remaining = max(0, used_tokens - fixed)
        categories["tool_calls_tokens"] = min(int(categories["tool_calls_tokens"]), remaining)
        remaining -= int(categories["tool_calls_tokens"])
        categories["tool_results_tokens"] = min(int(categories["tool_results_tokens"]), remaining)

        max_tokens = max(1, self._resolve_model_context_max_tokens(model_name))
        free_tokens = max(0, max_tokens - used_tokens)
        pct_used = min(100.0, (used_tokens / max_tokens) * 100.0)

        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "terminal_id": term_id,
            "agent_name": agent_name,
            "model": model_name,
            "used_tokens": used_tokens,
            "max_tokens": max_tokens,
            "pct_used": round(pct_used, 2),
            "free_tokens": free_tokens,
            "last_input_tokens": int(categories["user_prompt_tokens"]),
            "provider_usage_details": {
                "cached_tokens": cached_tokens,
                "reasoning_tokens": reasoning_tokens,
            },
            "categories": categories,
        }

    def _rotate_context_snapshots_if_needed(self) -> None:
        try:
            if not os.path.exists(self._context_snapshots_file):
                return
            if os.path.getsize(self._context_snapshots_file) <= self._context_snapshots_max_bytes:
                return

            max_backups = max(1, int(self._context_snapshots_max_backups))
            oldest = f"{self._context_snapshots_file}.{max_backups}"
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except Exception:
                    pass

            for i in range(max_backups - 1, 0, -1):
                src = f"{self._context_snapshots_file}.{i}"
                dst = f"{self._context_snapshots_file}.{i + 1}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass

            try:
                os.replace(self._context_snapshots_file, f"{self._context_snapshots_file}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _persist_context_snapshot(self, snapshot: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._context_snapshots_file), exist_ok=True)
            self._rotate_context_snapshots_if_needed()
            with open(self._context_snapshots_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=True) + "\n")
        except Exception:
            pass

    def _load_context_snapshots_latest_by_term(self) -> dict[int, dict]:
        latest: dict[int, dict] = {}
        try:
            paths: list[str] = []
            for i in range(self._context_snapshots_max_backups, 0, -1):
                p = f"{self._context_snapshots_file}.{i}"
                if os.path.exists(p):
                    paths.append(p)
            if os.path.exists(self._context_snapshots_file):
                paths.append(self._context_snapshots_file)

            for path in paths:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                            if not isinstance(obj, dict):
                                continue
                            term_id = int(obj.get("terminal_id", 0) or 0)
                            if term_id <= 0:
                                continue
                            latest[term_id] = obj
                        except Exception:
                            continue
        except Exception:
            return {}
        return latest

    def _get_context_snapshot(self, term_id: int | None = None) -> dict | None:
        tid = int(term_id or self._active_term_id)
        snap = self._context_snapshot_by_term.get(tid)
        if snap is not None:
            return snap
        # Fallback to latest from any terminal if active has no data yet.
        if self._context_snapshot_by_term:
            return self._context_snapshot_by_term[sorted(self._context_snapshot_by_term.keys())[-1]]
        return None

    @work(exclusive=False)
    async def _open_context_usage_menu_worker(self) -> None:
        while True:
            snapshot = self._get_context_snapshot(self._active_term_id)
            if snapshot is None:
                title = "Context Usage"
                content = "No context data yet. Run one prompt to initialize."
                summary_text = ""
            else:
                title = f"Context Usage · T{snapshot.get('terminal_id', '?')}"
                content = self._render_context_usage_menu_text(snapshot)
                summary_text = self._context_snapshot_summary_text(snapshot)

            result = await self.push_screen_wait(ContextUsageModal(title, content, summary_text))
            if not result:
                return

            action = result[0] if isinstance(result, (tuple, list)) and result else None
            if action == "refresh":
                self._update_metrics_view()
                continue
            if action == "copy":
                payload = result[1] if len(result) > 1 else ""
                try:
                    inp = self.query_one(f"#term-input-{self._active_term_id}", TextArea)
                    if hasattr(inp, "load_text"):
                        inp.load_text(str(payload))
                    else:
                        cast(Any, inp).value = str(payload)
                    inp.focus()
                    self._log_to_active_terminal(
                        "[context] copied summary to input", style="#00ff00"
                    )
                except Exception:
                    pass
                continue
            if action == "inject":
                payload = result[1] if len(result) > 1 else ""
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    await panel.dispatch(str(payload))
                except Exception as exc:
                    self._log_to_active_terminal(f"[context] inject failed: {exc}", style="#ff4444")
                continue
            if action == "jump_metrics":
                self._switch_top_tab("tab-metrics")
                continue
            return

    def _telemetry_run_started(self, term_id: int, agent_name: str, prompt: str) -> None:
        stats = self._ensure_term_stats(term_id)
        stats["runs"] += 1
        stats["last_status"] = "running"
        self._telemetry_pending_runs[self._run_key(term_id)] = {
            "start_ms": self._now_ms(),
            "first_token_ms": None,
            "agent_name": agent_name,
            "prompt_chars": len(prompt or ""),
            "categories": {
                "system_prompt_tokens": 0,
                "tool_definitions_tokens": 0,
                "memory_rag_tokens": 0,
                "user_prompt_tokens": self._estimate_tokens_from_text(prompt),
                "assistant_response_tokens": 0,
                "tool_calls_tokens": 0,
                "tool_results_tokens": 0,
            },
        }
        self._emit_telemetry(
            term_id, agent_name, "run_started", {"prompt_chars": len(prompt or "")}
        )

    def _telemetry_first_token(self, term_id: int, agent_name: str) -> None:
        run = self._telemetry_pending_runs.get(self._run_key(term_id))
        if not run:
            return
        if run.get("first_token_ms") is not None:
            return
        now_ms = self._now_ms()
        first_ms = max(0, now_ms - int(run.get("start_ms", now_ms)))
        run["first_token_ms"] = first_ms
        stats = self._ensure_term_stats(term_id)
        stats["sum_first_token_ms"] += first_ms
        stats["last_first_token_ms"] = first_ms
        self._emit_telemetry(term_id, agent_name, "first_token", {"latency_ms": first_ms})

    def _telemetry_tool_called(
        self,
        term_id: int,
        agent_name: str,
        tool_name: str,
        call_id: str,
        args_preview: str,
    ) -> None:
        stats = self._ensure_term_stats(term_id)
        stats["tool_calls"] += 1
        self._telemetry_pending_tool_calls[self._tool_key(term_id, call_id)] = {
            "start_ms": self._now_ms(),
            "tool_name": tool_name,
            "is_retrieval": self._is_retrieval_tool(tool_name),
        }
        payload = {
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "args_preview": args_preview[:200],
        }
        self._emit_telemetry(term_id, agent_name, "tool_called", payload)
        run = self._telemetry_pending_runs.get(self._run_key(term_id))
        if run and isinstance(run.get("categories"), dict):
            run["categories"]["tool_calls_tokens"] += self._estimate_tokens_from_text(args_preview)
        if self._is_retrieval_tool(tool_name):
            stats["retrieval_calls"] += 1
            if run and isinstance(run.get("categories"), dict):
                # Best-effort attribution: retrieval calls often pull memory/RAG context.
                run["categories"]["memory_rag_tokens"] += self._estimate_tokens_from_text(
                    args_preview
                )
            self._emit_telemetry(
                term_id,
                agent_name,
                "retrieval_called",
                {"tool_name": tool_name, "tool_call_id": call_id},
            )

    def _telemetry_tool_output(
        self,
        term_id: int,
        agent_name: str,
        call_id: str,
        output_preview: str,
    ) -> None:
        key = self._tool_key(term_id, call_id)
        pending = self._telemetry_pending_tool_calls.get(key, {})
        start_ms = int(pending.get("start_ms", self._now_ms()))
        duration_ms = max(0, self._now_ms() - start_ms)
        tool_name = str(pending.get("tool_name", "tool"))
        self._emit_telemetry(
            term_id,
            agent_name,
            "tool_output",
            {
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "duration_ms": duration_ms,
                "output_preview": output_preview[:200],
            },
        )
        run = self._telemetry_pending_runs.get(self._run_key(term_id))
        if run and isinstance(run.get("categories"), dict):
            run["categories"]["tool_results_tokens"] += self._estimate_tokens_from_text(
                output_preview
            )
        if pending.get("is_retrieval"):
            if run and isinstance(run.get("categories"), dict):
                run["categories"]["memory_rag_tokens"] += self._estimate_tokens_from_text(
                    output_preview
                )
            self._emit_telemetry(
                term_id,
                agent_name,
                "retrieval_output",
                {
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "duration_ms": duration_ms,
                },
            )
        if key in self._telemetry_pending_tool_calls:
            self._telemetry_pending_tool_calls.pop(key, None)

    def _telemetry_run_finished(
        self,
        term_id: int,
        agent_name: str,
        result,
        status: str,
        error_text: str | None = None,
    ) -> str:
        run = self._telemetry_pending_runs.pop(self._run_key(term_id), None)
        stats = self._ensure_term_stats(term_id)
        now_ms = self._now_ms()
        total_latency_ms = 0
        if run:
            total_latency_ms = max(0, now_ms - int(run.get("start_ms", now_ms)))
        stats["sum_total_latency_ms"] += total_latency_ms
        stats["last_total_latency_ms"] = total_latency_ms
        stats["last_status"] = status

        if status == "error":
            stats["errors"] += 1
        elif status == "cancelled":
            stats["cancelled"] += 1

        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        usage_detail_totals = {"cached_tokens": 0, "reasoning_tokens": 0}
        try:
            if result is not None:
                for resp in list(getattr(result, "raw_responses", []) or []):
                    usage = getattr(resp, "usage", None)
                    if usage is None:
                        continue
                    usage_totals = self._extract_usage_detail_totals(usage)
                    input_tokens += int(usage_totals.get("input_tokens", 0) or 0)
                    output_tokens += int(usage_totals.get("output_tokens", 0) or 0)
                    total_tokens += int(usage_totals.get("total_tokens", 0) or 0)
                    usage_detail_totals["cached_tokens"] += int(
                        usage_totals.get("cached_tokens", 0) or 0
                    )
                    usage_detail_totals["reasoning_tokens"] += int(
                        usage_totals.get("reasoning_tokens", 0) or 0
                    )
        except Exception:
            pass

        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["total_tokens"] += total_tokens

        payload = {
            "status": status,
            "latency_ms_total": total_latency_ms,
            "latency_ms_first_token": run.get("first_token_ms") if run else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "usage_details": usage_detail_totals,
            "categories": (run or {}).get("categories", {}),
        }
        if error_text:
            payload["error"] = str(error_text)[:300]
        self._emit_telemetry(term_id, agent_name, "run_finished", payload)

        interaction_cost = 0.0
        input_price = 0.0
        output_price = 0.0
        model_name = self._model_name
        try:
            from cai.util import COST_TRACKER

            interaction_cost = float(
                COST_TRACKER.calculate_cost(
                    model_name, input_tokens, output_tokens, label="TUI_STATS"
                )
            )
            input_price, output_price = COST_TRACKER.get_model_pricing(model_name)
        except Exception:
            interaction_cost = 0.0
            input_price, output_price = (0.0, 0.0)

        stats["cost_total"] = float(stats.get("cost_total", 0.0) or 0.0) + interaction_cost
        stats["last_cost"] = interaction_cost
        stats["model"] = model_name
        stats["input_price_per_token"] = float(input_price or 0.0)
        stats["output_price_per_token"] = float(output_price or 0.0)

        try:
            snapshot = self._build_context_snapshot(
                term_id=term_id,
                agent_name=agent_name,
                model_name=self._model_name,
                run_data=(run or {}),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                usage_details=usage_detail_totals,
            )
            self._context_snapshot_by_term[term_id] = snapshot
            self._persist_context_snapshot(snapshot)
            self._emit_telemetry(term_id, agent_name, "context_snapshot", snapshot)
        except Exception:
            pass

        try:
            self._refresh_price_limit_state(emit_logs=True)
        except Exception:
            pass

        # Refresh Metrics tab widgets if present.
        try:
            self._update_metrics_view()
        except Exception:
            pass

        avg_total = int(stats["sum_total_latency_ms"] / max(1, stats["runs"]))
        avg_first = int(stats["sum_first_token_ms"] / max(1, stats["runs"]))
        return (
            f"lat(avg {avg_total}ms / first {avg_first}ms) · "
            f"tok {stats['total_tokens']} · tools {stats['tool_calls']} · "
            f"retr {stats['retrieval_calls']} · {status}"
        )

    def _load_inject_mode_pref(self) -> str:
        """Load persisted inject mode from TUI config."""
        try:
            cfg = _load_tui_config()
            mode = str(cfg.get("tools", {}).get("inject_mode", "input")).strip().lower()
            if mode in ("input", "command"):
                return mode
        except Exception:
            pass
        return "input"

    def _persist_inject_mode_pref(self) -> None:
        """Persist current inject mode to TUI config."""
        try:
            cfg = _load_tui_config()
            cfg.setdefault("tools", {})["inject_mode"] = self._inject_mode
            _save_tui_config(cfg)
        except Exception:
            pass

    # ------------------------------------------------------------------ layout

    def _responsive_mode_for_size(self, width: int, height: int) -> str:
        if width < 120 or height < 40:
            return "small"
        if width < 160 or height < 50:
            return "medium"
        return "large"

    def _truncate_label(self, text: str, max_len: int) -> str:
        raw = str(text or "")
        if len(raw) <= max_len:
            return raw
        if max_len <= 3:
            return raw[:max_len]
        return raw[: max_len - 1] + "…"

    def _responsive_capacity(self, mode: str) -> int:
        if mode == "small":
            return 1
        if mode == "medium":
            return 3
        return 4

    def _visible_panel_ids_for_mode(self, mode: str) -> set[int]:
        panels = sorted(list(self.query(TerminalPanel)), key=lambda p: p._term_id)
        if not panels:
            return set()
        capacity = self._responsive_capacity(mode)
        if capacity >= len(panels):
            return {p._term_id for p in panels}
        if mode == "small":
            return {self._active_term_id}

        selected = [p._term_id for p in panels[:capacity]]
        if self._active_term_id not in selected:
            selected[-1] = self._active_term_id
        return set(selected)

    def _apply_terminal_visibility(self, mode: str) -> None:
        visible_ids = self._visible_panel_ids_for_mode(mode)
        for panel in self.query(TerminalPanel):
            panel.display = panel._term_id in visible_ids

        try:
            top = self.query_one("#term-row-top", Horizontal)
            top_visible = any(getattr(child, "display", True) for child in top.query(TerminalPanel))
            top.display = top_visible
        except Exception:
            pass

        try:
            bottom = self.query_one("#term-row-bottom", Horizontal)
            bottom_visible = any(
                getattr(child, "display", True) for child in bottom.query(TerminalPanel)
            )
            bottom.display = bottom_visible
        except Exception:
            pass

    def _apply_responsive_labels(self, mode: str) -> None:
        if mode == "small":
            max_len = 12
        elif mode == "medium":
            max_len = 24
        else:
            max_len = 40

        for btn in self.query(Button):
            bid = btn.id or ""
            if not bid:
                continue
            if bid not in self._responsive_label_cache:
                self._responsive_label_cache[bid] = str(btn.label)
            base = self._responsive_label_cache.get(bid, str(btn.label))

            if mode == "small" and bid.startswith("team-"):
                compact = self._truncate_label(base.replace("#", "T"), 8)
                btn.label = compact
            elif mode == "small" and bid.startswith("agent-"):
                btn.label = self._truncate_label(base, max_len)
            elif mode == "small" and bid in {
                "sessions-refresh",
                "sessions-load",
                "sessions-resume",
                "sessions-export",
                "sessions-rename",
                "sessions-delete",
                "queue-run",
                "queue-delete",
                "queue-clear",
                "queue-broadcast-mode",
                "tools-run",
                "tools-inspect",
                "tools-replay",
                "tools-inject",
                "tools-inject-mode",
            }:
                btn.label = self._truncate_label(base, 10)
            else:
                btn.label = self._truncate_label(base, max_len)

            try:
                if mode == "large":
                    if bid.startswith("team-"):
                        idx = int(bid.split("-")[-1])
                        label, composition = TEAM_PRESETS[idx]
                        btn.tooltip = (
                            self._team_tooltip_text(idx, label, composition) + "\nViewport: large"
                        )
                    elif bid.startswith("agent-"):
                        btn.tooltip = f"Agent: {base}"
                elif mode == "small" and not btn.tooltip:
                    btn.tooltip = base
            except Exception:
                pass

    def _apply_responsive_chrome(self, mode: str, width: int, height: int) -> None:
        try:
            sidebar = self.query_one("#sidebar", Vertical)
            if mode == "small":
                sidebar.styles.width = 12
            elif mode == "medium":
                sidebar.styles.width = 32
            else:
                sidebar.styles.width = 36
        except Exception:
            pass

        try:
            playbook = self.query_one("#team-playbook-preview", Static)
            playbook.display = mode != "small"
        except Exception:
            pass

        try:
            tools_history = self.query_one("#tools-history-scroll", ScrollableContainer)
            tools_history.display = mode != "small"
        except Exception:
            pass

        try:
            metrics_events = self.query_one("#metrics-events-scroll", ScrollableContainer)
            metrics_events.display = mode != "small"
        except Exception:
            pass

        try:
            header_left = self.query_one("#header-left-text", Static)
            if mode == "small":
                header_left.update("[#00ff00]T[/#00ff00]")
            elif mode == "medium":
                header_left.update("[#00ff00]Terminal[/#00ff00]")
            else:
                header_left.update("[#00ff00]Terminal[/#00ff00]")
        except Exception:
            pass

        if width < 80 or height < 24:
            self._log_to_active_terminal(
                f"[layout] terminal below minimum {width}x{height} (recommended >= 80x24)",
                style="#ff6600",
            )

    def _apply_responsive_layout(self, width: int, height: int) -> None:
        mode = self._responsive_mode_for_size(width, height)
        self._responsive_mode = mode
        self.remove_class("-small-screen")
        self.remove_class("-medium-screen")
        self.remove_class("-large-screen")
        self.add_class(f"-{mode}-screen")
        self._apply_terminal_visibility(mode)
        self._apply_responsive_chrome(mode, width, height)
        self._apply_responsive_labels(mode)

    def _set_top_nav_active(self, tab_id: str) -> None:
        target = str(tab_id or "")
        for btn in self.query(".top-nav-btn"):
            btn.remove_class("-active-top-nav")
        map_to_btn = {
            "tab-terminal": "top-nav-terminal",
            "tab-agents": "top-nav-agents",
            "tab-queue": "top-nav-queue",
            "tab-sessions": "top-nav-sessions",
            "tab-config": "top-nav-config",
            "tab-tools": "top-nav-tools",
            "tab-metrics": "top-nav-metrics",
        }
        btn_id = map_to_btn.get(target)
        if not btn_id:
            return
        try:
            self.query_one(f"#{btn_id}", Button).add_class("-active-top-nav")
        except Exception:
            pass

    def _switch_top_tab(self, tab_id: str) -> None:
        try:
            tabs = self.query_one("#sidebar-tabs", TabbedContent)
            tabs.active = tab_id
            self._set_top_nav_active(tab_id)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        yield CaiHeader(
            agent_name=self._agent_name,
            model=self._model_name,
        )
        with Vertical(id="body"):
            with TabbedContent(id="sidebar-tabs"):
                with TabPane("Terminal", id="tab-terminal"):
                    with Vertical(id="terminals"):
                        with Horizontal(id="term-row-top", classes="term-row"):
                            pass  # first panel added in on_mount
                with TabPane("Agents", id="tab-agents"):
                    with Vertical(id="agents-pane"):
                        with ScrollableContainer(id="agents-scroll"):
                            pass  # populated in on_mount
                        with Vertical(id="teams-section"):
                            yield Static("Teams", id="teams-label")
                            with ScrollableContainer(id="teams-scroll"):
                                for i, (label, _) in enumerate(TEAM_PRESETS):
                                    yield Button(
                                        f"#{i + 1}: {label}",
                                        id=f"team-{i}",
                                        classes="team-btn",
                                    )
                            yield Static(
                                "Select a team to see strategy hints.", id="team-playbook-preview"
                            )
                            yield Button("+ Create Team", id="new-team-btn")
                with TabPane("Queue", id="tab-queue"):
                    with Vertical(id="queue-pane"):
                        yield ListView(id="queue-list")
                        yield Static("Queue: 0 pending · broadcast OFF", id="queue-status")
                        with Horizontal(id="queue-actions"):
                            yield Button("Run Queue", id="queue-run", classes="agent-btn")
                            yield Button("Delete Selected", id="queue-delete", classes="team-btn")
                            yield Button(
                                "Clear All", id="queue-clear", classes="modal-btn modal-btn--cancel"
                            )
                            yield Button(
                                "Broadcast: OFF", id="queue-broadcast-mode", classes="team-btn"
                            )
                        with Horizontal(id="queue-input-row"):
                            yield Static("+", id="queue-prefix")
                            yield Input(
                                placeholder="Add task / command… (append ' all' to broadcast)",
                                id="queue-input",
                            )
                            yield Button("Add", id="queue-add", classes="team-btn")
                with TabPane("Sessions", id="tab-sessions"):
                    with Vertical(id="sessions-pane"):
                        with ScrollableContainer(id="sessions-scroll"):
                            pass  # populated in on_mount
                        yield Static("", id="session-preview")
                        with Horizontal(id="sessions-controls"):
                            yield Button("Refresh", id="sessions-refresh", classes="team-btn")
                            yield Button("Load Selected", id="sessions-load", classes="agent-btn")
                            yield Button(
                                "Resume Selected", id="sessions-resume", classes="agent-btn"
                            )
                            yield Button(
                                "Export Selected", id="sessions-export", classes="agent-btn"
                            )
                            yield Button(
                                "Rename Selected", id="sessions-rename", classes="agent-btn"
                            )
                            yield Button(
                                "Delete Selected",
                                id="sessions-delete",
                                classes="modal-btn modal-btn--cancel",
                            )
                with TabPane("Config", id="tab-config"):
                    with Vertical(id="config-pane"):
                        yield Button("Providers", id="config-providers", classes="menu-btn")
                        yield Button("Model Params", id="config-model-params", classes="menu-btn")
                        yield Button("Memory / RAG", id="config-memory", classes="menu-btn")
                        yield Button(
                            "Export / Import", id="config-export-import", classes="menu-btn"
                        )
                        yield Button("Environment", id="config-env", classes="menu-btn")
                        yield Button(
                            "Toggle Session Recording",
                            id="config-session-recording",
                            classes="menu-btn",
                        )
                        yield Button(
                            "Reset Defaults", id="config-reset-defaults", classes="menu-btn"
                        )
                with TabPane("Tools", id="tab-tools"):
                    with Vertical(id="tools-pane"):
                        with ScrollableContainer(id="tools-list-scroll"):
                            pass
                        with Horizontal(id="tools-actions"):
                            yield Button("Run", id="tools-run", classes="agent-btn")
                            yield Button("Inspect", id="tools-inspect", classes="team-btn")
                            yield Button("Replay", id="tools-replay", classes="agent-btn")
                            yield Button("Inject", id="tools-inject", classes="team-btn")
                            yield Button("Mode: input", id="tools-inject-mode", classes="team-btn")
                        yield Static("", id="tools-preview")
                        with ScrollableContainer(id="tools-history-scroll"):
                            pass
                with TabPane("Stats", id="tab-metrics"):
                    with Vertical(id="metrics-pane"):
                        yield Static("", id="metrics-summary")
                        with ScrollableContainer(id="metrics-events-scroll"):
                            yield Static("", id="metrics-events")
                        with Horizontal(id="metrics-actions"):
                            yield Button("Refresh", id="metrics-refresh", classes="team-btn")
                            yield Button("Context", id="metrics-context", classes="agent-btn")
        yield Footer()

    # ------------------------------------------------------------------ lifecycle

    async def on_mount(self) -> None:
        # Load available agents
        try:
            from cai.agents import get_available_agents

            self._available_agents = get_available_agents()
        except Exception:
            self._available_agents = {}

        scroll = self.query_one("#agents-scroll", ScrollableContainer)
        for name in sorted(self._available_agents.keys()):
            label = _pretty_name(name)
            await scroll.mount(Button(label, id=f"agent-{name}", classes="agent-btn"))

        self._inject_mode = self._load_inject_mode_pref()
        self._tool_call_history = self._load_tool_call_history()
        self._tool_registry = self._build_tool_registry()
        await self._populate_tools_list()
        self._update_metrics_view()
        self._refresh_price_limit_state(emit_logs=False)
        self._sync_team_buttons_metadata()
        self._update_team_playbook_preview(None)
        self._sync_queue_broadcast_button()
        self._update_queue_view()

        self._highlight_active_agent(self._agent_name)

        # Populate sessions list in the Sessions tab
        try:
            await self._populate_sessions_list()
        except Exception:
            pass

        # Spawn the first terminal panel (into top row)
        first = TerminalPanel(
            term_id=1,
            agent=self._agent,
            agent_name=self._agent_name,
            model_name=self._model_name,
        )
        await self.query_one("#term-row-top", Horizontal).mount(first)
        self._set_active_terminal(1)
        self._switch_top_tab("tab-terminal")
        try:
            size = self.size
            self._apply_responsive_layout(int(size.width), int(size.height))
        except Exception:
            pass

        # Focus the first input
        try:
            self.query_one("#term-input-1", TextArea).focus()
        except Exception:
            pass

        if self._initial_prompt:
            await first.dispatch(self._initial_prompt)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(int(event.size.width), int(event.size.height))

    def _build_tool_registry(self) -> dict[str, dict]:
        """Build TUI tool registry from built-ins plus active terminal agent tools."""
        from cai.sdk.agents.run_context import RunContextWrapper
        from cai.sdk.agents.tool import FunctionTool

        def _tool_echo(params: dict) -> dict:
            text = str(params.get("text", ""))
            return {"output": text, "length": len(text)}

        def _tool_now(_: dict) -> dict:
            return {"now": datetime.now().isoformat(timespec="seconds")}

        def _tool_list_agents(_: dict) -> dict:
            keys = sorted(list(self._available_agents.keys()))
            return {"count": len(keys), "agents": keys}

        def _tool_last_tool_call(_: dict) -> dict:
            if not self._tool_call_history:
                return {"has_calls": False}
            last = self._tool_call_history[-1]
            return {
                "has_calls": True,
                "last_call_id": last.get("call_id"),
                "tool_id": last.get("tool_id"),
                "timestamp": last.get("timestamp"),
            }

        registry = {
            "echo": {
                "name": "Echo",
                "description": "Return provided text as tool output.",
                "schema": {"text": "string"},
                "runner": _tool_echo,
            },
            "now": {
                "name": "Current Time",
                "description": "Return local timestamp.",
                "schema": {},
                "runner": _tool_now,
            },
            "list_agents": {
                "name": "List Agents",
                "description": "Show currently available CAI agents.",
                "schema": {},
                "runner": _tool_list_agents,
            },
            "last_tool_call": {
                "name": "Last Tool Call",
                "description": "Inspect metadata of the most recent tool call.",
                "schema": {},
                "runner": _tool_last_tool_call,
            },
        }

        # Pull real function tools from the currently active terminal's agent.
        active_agent = None
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            active_agent = panel._agent
        except Exception:
            active_agent = self._agent

        for tool in list(getattr(active_agent, "tools", []) or []):
            try:
                tool_name = getattr(tool, "name", None)
                if not tool_name:
                    continue

                if isinstance(tool, FunctionTool):

                    async def _invoke_function_tool(params: dict, fn_tool=tool):
                        ctx = RunContextWrapper(context=None)
                        tool_input = json.dumps(params or {}, ensure_ascii=True)
                        output = await fn_tool.on_invoke_tool(ctx, tool_input)
                        return {"output": str(output)}

                    registry[f"agent::{tool_name}"] = {
                        "name": f"{tool_name} (agent)",
                        "description": getattr(tool, "description", "") or "Agent function tool",
                        "schema": getattr(tool, "params_json_schema", {}) or {},
                        "runner": _invoke_function_tool,
                        "async_runner": True,
                        "source": "agent",
                    }
                else:
                    # Hosted tools are inspect-only in this panel for now.
                    registry[f"agent::{tool_name}"] = {
                        "name": f"{tool_name} (inspect)",
                        "description": "Hosted tool (inspect only in TUI panel)",
                        "schema": {},
                        "runner": None,
                        "source": "agent",
                    }
            except Exception:
                continue

        return registry

    async def _populate_tools_list(self) -> None:
        """Render tool buttons in the Tools tab."""
        try:
            scroll = self.query_one("#tools-list-scroll", ScrollableContainer)
        except Exception:
            return

        self._tool_button_id_to_tool_id.clear()
        self._tool_tool_id_to_button_id.clear()

        for child in list(scroll.children):
            try:
                await child.remove()
            except Exception:
                pass

        for idx, tool_id in enumerate(sorted(self._tool_registry.keys()), start=1):
            meta = self._tool_registry.get(tool_id, {})
            name = meta.get("name", tool_id)
            btn_id = f"tool-select-{idx}"
            self._tool_button_id_to_tool_id[btn_id] = tool_id
            self._tool_tool_id_to_button_id[tool_id] = btn_id
            await scroll.mount(Button(name, id=btn_id, classes="tool-btn"))

        if self._tool_registry:
            self._selected_tool_id = sorted(self._tool_registry.keys())[0]
            self._highlight_active_tool(self._selected_tool_id)
            self._sync_inject_mode_button()
            self._update_tools_preview()
            await self._refresh_tools_history_ui()

    def _highlight_active_tool(self, tool_id: str) -> None:
        for btn in self.query(".tool-btn"):
            btn.remove_class("-active-tool")
        try:
            btn_id = self._tool_tool_id_to_button_id.get(tool_id)
            if not btn_id:
                return
            self.query_one(f"#{btn_id}", Button).add_class("-active-tool")
        except Exception:
            pass

    def _append_tool_call(
        self, tool_id: str, inputs: dict, output: dict, replayed: bool = False
    ) -> dict:
        call_id = f"tool-{len(self._tool_call_history) + 1}"
        record = {
            "call_id": call_id,
            "tool_id": tool_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "inputs": inputs,
            "output": output,
            "replayed": replayed,
        }
        self._tool_call_history.append(record)
        self._persist_tool_call_record(record)
        self._selected_tool_call_idx = len(self._tool_call_history) - 1
        return record

    def _load_tool_call_history(self) -> list[dict]:
        records: list[dict] = []
        try:
            paths: list[str] = []
            # oldest backup first, then newest/current file
            for i in range(self._tool_calls_max_backups, 0, -1):
                backup_path = f"{self._tool_calls_file}.{i}"
                if os.path.exists(backup_path):
                    paths.append(backup_path)
            if os.path.exists(self._tool_calls_file):
                paths.append(self._tool_calls_file)

            for path in paths:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        raw = line.strip()
                        if not raw:
                            continue
                        try:
                            item = json.loads(raw)
                            if isinstance(item, dict):
                                records.append(item)
                        except Exception:
                            continue
        except Exception:
            return []
        return records

    def _rotate_tool_calls_if_needed(self) -> None:
        """Rotate `tui_tool_calls.jsonl` when the file grows beyond max bytes."""
        try:
            if not os.path.exists(self._tool_calls_file):
                return
            if os.path.getsize(self._tool_calls_file) <= self._tool_calls_max_bytes:
                return

            max_backups = max(1, int(self._tool_calls_max_backups))

            # Drop the oldest backup if needed.
            oldest = f"{self._tool_calls_file}.{max_backups}"
            if os.path.exists(oldest):
                try:
                    os.remove(oldest)
                except Exception:
                    pass

            # Shift existing backups up: .1 -> .2, etc.
            for i in range(max_backups - 1, 0, -1):
                src = f"{self._tool_calls_file}.{i}"
                dst = f"{self._tool_calls_file}.{i + 1}"
                if os.path.exists(src):
                    try:
                        os.replace(src, dst)
                    except Exception:
                        pass

            # Rotate current file to .1
            try:
                os.replace(self._tool_calls_file, f"{self._tool_calls_file}.1")
            except Exception:
                pass
        except Exception:
            pass

    def _persist_tool_call_record(self, record: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._tool_calls_file), exist_ok=True)
            self._rotate_tool_calls_if_needed()
            with open(self._tool_calls_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception:
            pass

    async def _refresh_tools_history_ui(self) -> None:
        """Render tool call history entries as buttons."""
        try:
            scroll = self.query_one("#tools-history-scroll", ScrollableContainer)
        except Exception:
            return

        for child in list(scroll.children):
            try:
                await child.remove()
            except Exception:
                pass

        # Show newest first and keep recent window to avoid overgrowing the UI.
        history_window = list(enumerate(self._tool_call_history[-20:]))
        history_window.reverse()
        base_idx = max(0, len(self._tool_call_history) - 20)
        for local_idx, record in history_window:
            actual_idx = base_idx + local_idx
            call_id = record.get("call_id", f"call-{actual_idx + 1}")
            tool_id = record.get("tool_id", "?")
            stamp = record.get("timestamp", "")
            tag = " (replay)" if record.get("replayed") else ""
            label = f"{call_id} · {tool_id} · {stamp}{tag}"
            await scroll.mount(Button(label, id=f"tool-call-{actual_idx}", classes="team-btn"))

    def _update_tools_preview(self) -> None:
        try:
            preview = self.query_one("#tools-preview", Static)
        except Exception:
            return

        tool_id = self._selected_tool_id
        if not tool_id or tool_id not in self._tool_registry:
            preview.update("Select a tool to inspect or run.")
            return

        meta = self._tool_registry.get(tool_id, {})
        lines = [
            f"[bold]{meta.get('name', tool_id)}[/bold] ({tool_id})",
            meta.get("description", ""),
            "",
            f"Schema: {json.dumps(meta.get('schema', {}), ensure_ascii=True)}",
            f"Inject mode: {self._inject_mode}",
        ]

        if self._selected_tool_call_idx is not None:
            try:
                rec = self._tool_call_history[self._selected_tool_call_idx]
                lines.extend(
                    [
                        "",
                        f"Last selected call: {rec.get('call_id')} @ {rec.get('timestamp')}",
                        f"Inputs: {json.dumps(rec.get('inputs', {}), ensure_ascii=True)}",
                        f"Output: {json.dumps(rec.get('output', {}), ensure_ascii=True)[:220]}",
                    ]
                )
            except Exception:
                pass

        try:
            preview.update(RichText.from_markup("\n".join(lines)))
        except Exception:
            preview.update("\n".join(lines))

    def _log_to_active_terminal(self, line: str, style: str = "#00aa00") -> None:
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
            log.write(RichText(line, style=style))
        except Exception:
            pass

    def _sync_inject_mode_button(self) -> None:
        try:
            btn = self.query_one("#tools-inject-mode", Button)
            btn.label = f"Mode: {self._inject_mode}"
            btn.remove_class("-inject-input")
            btn.remove_class("-inject-command")
            if self._inject_mode == "command":
                btn.add_class("-inject-command")
            else:
                btn.add_class("-inject-input")
        except Exception:
            pass

    def _toggle_inject_mode(self) -> None:
        self._inject_mode = "command" if self._inject_mode == "input" else "input"
        self._persist_inject_mode_pref()
        self._sync_inject_mode_button()
        self._update_tools_preview()
        self._log_to_active_terminal(f"[inject] mode set to {self._inject_mode}")

    @work(exclusive=False)
    async def _run_selected_tool_worker(self) -> None:
        tool_id = self._selected_tool_id
        if not tool_id or tool_id not in self._tool_registry:
            return

        raw_args = await self.push_screen_wait(PromptModal("Tool JSON args:", "{}"))
        if raw_args is None:
            return

        try:
            params = json.loads(raw_args) if str(raw_args).strip() else {}
            if not isinstance(params, dict):
                params = {}
        except Exception:
            self._log_to_active_terminal(
                "[tool] Invalid JSON args; expected an object.", style="#ff4444"
            )
            return

        meta = self._tool_registry.get(tool_id, {})
        runner = meta.get("runner")
        if not callable(runner):
            self._log_to_active_terminal("[tool] Runner unavailable.", style="#ff4444")
            return

        try:
            output_or_awaitable = runner(params)
            if inspect.isawaitable(output_or_awaitable):
                output = await output_or_awaitable
            else:
                output = output_or_awaitable
            if not isinstance(output, dict):
                output = {"output": str(output)}
            record = self._append_tool_call(tool_id, params, output)
            self._log_to_active_terminal(
                f"[tool] {record['call_id']} {tool_id} -> {json.dumps(output, ensure_ascii=True)[:220]}"
            )
            await self._refresh_tools_history_ui()
            self._update_tools_preview()
        except Exception as exc:
            self._log_to_active_terminal(f"[tool] Execution failed: {exc}", style="#ff4444")

    @work(exclusive=False)
    async def _replay_selected_tool_call_worker(self) -> None:
        idx = self._selected_tool_call_idx
        if idx is None:
            return
        try:
            record = self._tool_call_history[idx]
        except Exception:
            return

        tool_id = record.get("tool_id")
        if not tool_id or tool_id not in self._tool_registry:
            self._log_to_active_terminal("[tool] Replay failed: tool not found.", style="#ff4444")
            return

        runner = self._tool_registry[tool_id].get("runner")
        if not callable(runner):
            return

        default_params = record.get("inputs", {})
        try:
            default_json = json.dumps(default_params, ensure_ascii=True)
        except Exception:
            default_json = "{}"

        edited = await self.push_screen_wait(
            PromptModal("Replay JSON args (edit or keep):", default_json)
        )
        if edited is None:
            return

        try:
            params = json.loads(edited) if str(edited).strip() else {}
            if not isinstance(params, dict):
                params = {}
        except Exception:
            self._log_to_active_terminal(
                "[tool] Replay args must be valid JSON object.", style="#ff4444"
            )
            return

        try:
            meta = self._tool_registry.get(tool_id, {})
            output_or_awaitable = runner(params)
            if inspect.isawaitable(output_or_awaitable):
                output = await output_or_awaitable
            else:
                output = output_or_awaitable
            if not isinstance(output, dict):
                output = {"output": str(output)}
            replay_record = self._append_tool_call(tool_id, params, output, replayed=True)
            self._log_to_active_terminal(
                f"[tool] Replayed {record.get('call_id')} as {replay_record['call_id']}"
            )
            await self._refresh_tools_history_ui()
            self._update_tools_preview()
        except Exception as exc:
            self._log_to_active_terminal(f"[tool] Replay failed: {exc}", style="#ff4444")

    @work(exclusive=False)
    async def _inject_selected_tool_output(self) -> None:
        idx = self._selected_tool_call_idx
        if idx is None:
            return
        try:
            record = self._tool_call_history[idx]
            output_obj = record.get("output", {})
            payload = json.dumps(output_obj, ensure_ascii=True)

            if self._inject_mode == "command":
                self._log_to_active_terminal(f"[inject/command] {payload}", style="#00ff00")
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    await panel.dispatch(payload)
                except Exception as exc:
                    self._log_to_active_terminal(
                        f"[inject] command dispatch failed: {exc}", style="#ff4444"
                    )
            else:
                self._log_to_active_terminal(f"[inject/input] {payload}", style="#00ff00")
                inp = self.query_one(f"#term-input-{self._active_term_id}", TextArea)
                if hasattr(inp, "load_text"):
                    inp.load_text(payload)
                else:
                    cast(Any, inp).value = payload
                inp.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ terminal panel messages

    def on_terminal_panel_activated(self, event: TerminalPanel.Activated) -> None:
        self._set_active_terminal(event.term_id)

    def on_terminal_panel_close_requested(self, event: TerminalPanel.CloseRequested) -> None:
        panels = list(self.query(TerminalPanel))
        if len(panels) <= 1:
            self.exit()
            return
        try:
            panel = self.query_one(f"#terminal-panel-{event.term_id}", TerminalPanel)
            panel.remove()
        except Exception:
            pass
        # Remove empty bottom row if all its panels were closed
        try:
            bottom = self.query_one("#term-row-bottom", Horizontal)
            if not list(bottom.query(TerminalPanel)):
                bottom.remove()
        except Exception:
            pass
        remaining = list(self.query(TerminalPanel))
        if remaining:
            self._set_active_terminal(remaining[-1]._term_id)

    # ------------------------------------------------------------------ terminal management

    def _set_active_terminal(self, term_id: int) -> None:
        self._active_term_id = term_id
        for panel in self.query(TerminalPanel):
            if panel._term_id == term_id:
                panel.add_class("-active-panel")
                panel.remove_class("-inactive-panel")
            else:
                panel.remove_class("-active-panel")
                panel.add_class("-inactive-panel")
        try:
            size = self.size
            self._apply_terminal_visibility(
                self._responsive_mode_for_size(int(size.width), int(size.height))
            )
        except Exception:
            pass
        # Update header to reflect active terminal
        try:
            panel = self.query_one(f"#terminal-panel-{term_id}", TerminalPanel)
            self.query_one(CaiHeader).query_one("#header-right", Static).update(
                f"[bold #00ff00]T{term_id}[/bold #00ff00]"
                f"[#004400] | [/#004400]"
                f"[#00cc00]{panel._agent_name}[/#00cc00]"
                f"[#004400] ▼ [/#004400]"
                f"[#00aa00]{self._model_name}[/#00aa00]"
                f"[#004400] ▼ [/#004400]"
                f"[bold #00ff00]●[/bold #00ff00]"
            )
        except Exception:
            pass
        # Rebuild tool registry from the active terminal's agent and refresh UI.
        try:
            self._tool_registry = self._build_tool_registry()
            self._populate_tools_list_worker()
        except Exception:
            pass

    @work(exclusive=True)
    async def _populate_tools_list_worker(self) -> None:
        await self._populate_tools_list()

    async def _add_terminal(self, agent, agent_name: str) -> None:
        panels = list(self.query(TerminalPanel))
        if len(panels) >= 4:
            return  # hard cap — modal already disables the button
        self._next_term_id += 1
        tid = self._next_term_id
        panel = TerminalPanel(
            term_id=tid,
            agent=agent,
            agent_name=agent_name,
            model_name=self._model_name,
        )
        # Panels 1 & 2 go in the top row; 3 & 4 go in the bottom row
        if tid <= 2:
            row = self.query_one("#term-row-top", Horizontal)
        else:
            # Create bottom row on first use
            try:
                row = self.query_one("#term-row-bottom", Horizontal)
            except Exception:
                row = Horizontal(id="term-row-bottom", classes="term-row")
                await self.query_one("#terminals", Vertical).mount(row)
        await row.mount(panel)
        self._set_active_terminal(tid)
        try:
            size = self.size
            self._apply_responsive_layout(int(size.width), int(size.height))
        except Exception:
            pass
        try:
            self.query_one(f"#term-input-{tid}", TextArea).focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ button events

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""

        if btn_id.startswith("top-nav-"):
            tab_map = {
                "top-nav-terminal": "tab-terminal",
                "top-nav-agents": "tab-agents",
                "top-nav-queue": "tab-queue",
                "top-nav-sessions": "tab-sessions",
                "top-nav-config": "tab-config",
                "top-nav-tools": "tab-tools",
                "top-nav-metrics": "tab-metrics",
            }
            target = tab_map.get(btn_id)
            if target:
                self._switch_top_tab(target)
            return

        if btn_id == "header-menu":
            self.action_command_palette()
            return

        if btn_id.startswith("agent-"):
            agent_name = btn_id[len("agent-") :]
            if agent_name in self._available_agents:
                self._open_agent_modal(agent_name)
            return

        if btn_id.startswith("tool-select-"):
            tool_id = self._tool_button_id_to_tool_id.get(btn_id)
            if tool_id in self._tool_registry:
                self._selected_tool_id = tool_id
                self._highlight_active_tool(tool_id)
                self._update_tools_preview()
            return

        if btn_id.startswith("tool-call-"):
            try:
                idx = int(btn_id[len("tool-call-") :])
                if idx < 0 or idx >= len(self._tool_call_history):
                    return
                self._selected_tool_call_idx = idx
                self._selected_tool_id = self._tool_call_history[idx].get("tool_id")
                if self._selected_tool_id:
                    self._highlight_active_tool(self._selected_tool_id)
                self._update_tools_preview()
            except Exception:
                pass
            return

        if btn_id == "tools-run":
            self._run_selected_tool_worker()
            return

        if btn_id == "tools-inspect":
            self._update_tools_preview()
            self._log_to_active_terminal("[tool] Updated tool inspection view.")
            return

        if btn_id == "tools-replay":
            self._replay_selected_tool_call_worker()
            return

        if btn_id == "tools-inject":
            self._inject_selected_tool_output()
            return

        if btn_id == "tools-inject-mode":
            self._toggle_inject_mode()
            return

        if btn_id == "metrics-refresh":
            self._refresh_metrics_view_worker()
            return

        if btn_id == "metrics-context":
            self._open_context_usage_menu_worker()
            return

        if btn_id.startswith("team-"):
            self._activate_team(int(btn_id[len("team-") :]))
            return

        if btn_id == "queue-add":
            self._add_from_queue_input()
            return

        if btn_id == "queue-run":
            self._run_queue_worker()
            return

        if btn_id == "queue-delete":
            self._delete_selected_queue_item()
            return

        if btn_id == "queue-clear":
            self._clear_queue()
            return

        if btn_id == "queue-broadcast-mode":
            self._toggle_queue_broadcast_mode()
            return

        if btn_id == "new-team-btn":
            self._prompt_new_team()
            return
        # Sessions controls / per-item buttons
        if btn_id == "sessions-refresh":
            self._populate_sessions_list_worker()
            return

        selected = getattr(self, "_session_selected_idx", None)

        if btn_id == "sessions-load":
            # Load selected session, fallback to newest
            try:
                if selected is not None:
                    self._session_open_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_open_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-resume":
            try:
                if selected is not None:
                    self._session_resume_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_resume_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-export":
            try:
                if selected is not None:
                    self._session_export_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_export_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-rename":
            try:
                if selected is not None:
                    self._session_rename_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_rename_worker(int(first))
            except Exception:
                pass
            return

        if btn_id == "sessions-delete":
            try:
                if selected is not None:
                    self._session_delete_worker(int(selected))
                elif hasattr(self, "_session_files") and self._session_files:
                    first = sorted(self._session_files.keys())[0]
                    self._session_delete_worker(int(first))
            except Exception:
                pass
            return

        if btn_id.startswith("session-toggle-"):
            try:
                idx = int(btn_id.rsplit("-", 1)[-1])
            except Exception:
                return
            try:
                self._toggle_session_actions(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-select-"):
            try:
                idx = int(btn_id.rsplit("-", 1)[-1])
                self._set_selected_session(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-open-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_open_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-export-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_export_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-resume-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_resume_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-rename-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_rename_worker(idx)
            except Exception:
                pass
            return

        if btn_id.startswith("session-delete-"):
            try:
                idx = int(btn_id.split("-")[-1])
                self._session_delete_worker(idx)
            except Exception:
                pass
            return

        # Config menu buttons (only handle known sidebar config buttons)
        if btn_id in (
            "config-providers",
            "config-model-params",
            "config-memory",
            "config-export-import",
            "config-env",
            "config-session-recording",
            "config-reset-defaults",
        ):
            try:
                action_key = btn_id[len("config-") :]
                # Open the full config screen directly (avoid an extra confirm modal)
                try:
                    self._open_config_screen(action_key)
                except Exception:
                    # Fallback to the confirm modal if scheduling the worker fails
                    try:
                        self._open_config_modal(action_key)
                    except Exception:
                        pass
            except Exception:
                pass
            return

    @work(exclusive=False)
    async def _open_agent_modal(self, agent_name: str) -> None:
        """Open the agent modal from a worker so push_screen_wait is valid."""
        active_label = f"T{self._active_term_id}"
        at_max = len(list(self.query(TerminalPanel))) >= 4
        result = await self.push_screen_wait(AgentModal(agent_name, active_label, at_max=at_max))
        await self._handle_agent_modal(result)

    @work(exclusive=False)
    async def _open_config_modal(self, action_key: str) -> None:
        """Open a small modal to confirm opening the chosen Config section."""
        mapping = {
            "providers": "Providers",
            "model-params": "Model Params",
            "memory": "Memory / RAG",
            "export-import": "Export / Import",
            "env": "Environment",
            "session-recording": "Session Recording",
            "reset-defaults": "Reset Defaults",
        }
        display = mapping.get(action_key, action_key)
        result = await self.push_screen_wait(ConfigModal(action_key, display))
        # Do not await the handler because it will schedule a worker; call it synchronously.
        self._handle_config_action(result)

    def _handle_config_action(self, result) -> None:
        """Open the corresponding full-screen config editor for the chosen item."""
        if not result:
            return
        action = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else None
        if not action:
            return

        # Schedule the full-screen config worker (do not await the Worker object).
        try:
            self._open_config_screen(action)
        except Exception:
            pass

    @work(exclusive=False)
    async def _open_config_screen(self, action_key: str) -> None:
        """Push the full-screen config modal for `action_key` and handle its result."""
        from rich.text import Text as RichText

        # Log for easier debugging when screens are opened
        try:
            self.log.info(f"_open_config_screen: {action_key}")
        except Exception:
            pass

        cfg = _load_tui_config()

        screen_map = {
            "providers": ProvidersScreen(cfg),
            "model-params": ModelParamsScreen(cfg),
            "memory": MemoryInspectorScreen(cfg),
            "export-import": ExportImportScreen(cfg),
            "env": EnvScreen(cfg),
            "session-recording": SessionRecordingScreen(cfg),
            "reset-defaults": ResetDefaultsScreen(),
            "full-config": None,
        }

        # Special-case: full-config overview requires a loop to allow edits
        if action_key == "full-config":
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
            except Exception:
                panel = None
                log = None
            # Interactive loop: show overview, handle edit/reset, and re-show until closed
            while True:
                screen = ConfigOverviewScreen(CONFIG_VARIABLES)
                result = await self.push_screen_wait(screen)
                if not result:
                    break
                try:
                    action = result[0]
                    if action == "edit":
                        idx = result[1]
                        if idx is None or idx < 0 or idx >= len(CONFIG_VARIABLES):
                            continue
                        var = CONFIG_VARIABLES[idx]
                        name = str(var.get("name") or "")
                        cur_cfg = _load_tui_config()
                        current = (
                            os.environ.get(name)
                            or cur_cfg.get("env", {}).get(name)
                            or var.get("default", "")
                        )
                        newval = await self.push_screen_wait(
                            PromptModal(f"Set value for {name} (empty to unset):", str(current))
                        )
                        if newval is None:
                            continue
                        # Apply change
                        try:
                            cfg2 = _load_tui_config()
                            if newval == "":
                                os.environ.pop(name, None)
                                if "env" in cfg2 and name in cfg2["env"]:
                                    cfg2["env"].pop(name, None)
                            else:
                                os.environ[name] = newval
                                cfg2.setdefault("env", {})[name] = newval
                            _save_tui_config(cfg2)
                            if log:
                                log.write(
                                    RichText.from_markup(f"[green]Set {name} = {newval}[/green]")
                                )
                        except Exception as e:
                            if log:
                                log.write(
                                    RichText.from_markup(f"[red]Failed to set {name}: {e}[/red]")
                                )
                        continue

                    elif action == "reset":
                        idx = result[1]
                        if idx is None or idx < 0 or idx >= len(CONFIG_VARIABLES):
                            continue
                        var = CONFIG_VARIABLES[idx]
                        name = str(var.get("name") or "")
                        default = var.get("default")
                        try:
                            cfg2 = _load_tui_config()
                            if default in (None, "Not set"):
                                os.environ.pop(name, None)
                                if "env" in cfg2 and name in cfg2["env"]:
                                    cfg2["env"].pop(name, None)
                            else:
                                os.environ[name] = str(default)
                                cfg2.setdefault("env", {})[name] = str(default)
                            _save_tui_config(cfg2)
                            if log:
                                log.write(
                                    RichText.from_markup(
                                        f"[green]Reset {name} to {default}[/green]"
                                    )
                                )
                        except Exception as e:
                            if log:
                                log.write(
                                    RichText.from_markup(f"[red]Failed to reset {name}: {e}[/red]")
                                )
                        continue
                    else:
                        # Unknown action – break
                        break
                except Exception:
                    break
            return

        screen = screen_map.get(action_key)
        if screen is None:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                    RichText.from_markup(f"[red]Unknown config section: {action_key}[/red]")
                )
            except Exception:
                pass
            return

        result = await self.push_screen_wait(screen)
        if not result:
            return

        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
        except Exception:
            log = None

        try:
            key = result[0] if isinstance(result, (list, tuple)) else result

            if key == "save_provider":
                _, name, secret = result
                cfg = _load_tui_config()
                cfg.setdefault("providers", {})[name] = secret
                _save_tui_config(cfg)
                if log:
                    log.write(RichText.from_markup(f"[green]Saved provider {name}[/green]"))

            elif key == "test_provider":
                _, name = result
                if log:
                    log.write(
                        RichText.from_markup(
                            f"[dim]Provider test requested for {name} (not implemented)[/dim]"
                        )
                    )

            elif key == "save_model_params":
                _, params = result
                cfg = _load_tui_config()
                cfg["model_params"] = params
                _save_tui_config(cfg)
                if log:
                    log.write(RichText.from_markup("[green]Saved model parameters[/green]"))

            elif key == "rebuild_memory":
                if log:
                    log.write(
                        RichText.from_markup(
                            "[dim]Rebuild memory requested (not implemented)[/dim]"
                        )
                    )

            elif key == "evict_memory":
                if log:
                    log.write(
                        RichText.from_markup("[dim]Evict memory requested (not implemented)[/dim]")
                    )

            elif key == "export_config":
                _, path = result
                dest = path or os.path.join(os.getcwd(), "tui_config_export.json")
                try:
                    cfg = _load_tui_config()
                    if os.path.isdir(dest):
                        dest = os.path.join(dest, "tui_config_export.json")
                    with open(dest, "w") as f:
                        json.dump(cfg, f, indent=2)
                    if log:
                        log.write(RichText.from_markup(f"[green]Exported config to {dest}[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Export failed: {e}[/red]"))

            elif key == "import_config":
                _, path = result
                try:
                    if path and os.path.exists(path):
                        with open(path) as f:
                            imported = json.load(f)
                        cfg = _load_tui_config()
                        cfg.update(imported)
                        _save_tui_config(cfg)
                        if log:
                            log.write(
                                RichText.from_markup(f"[green]Imported config from {path}[/green]")
                            )
                    else:
                        if log:
                            log.write(
                                RichText.from_markup(f"[red]Import path not found: {path}[/red]")
                            )
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Import failed: {e}[/red]"))

            elif key == "set_env":
                _, var, val = result
                try:
                    os.environ[var] = val
                    cfg = _load_tui_config()
                    cfg.setdefault("env", {})[var] = val
                    _save_tui_config(cfg)
                    if log:
                        log.write(RichText.from_markup(f"[green]Set {var}[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Set env failed: {e}[/red]"))

            elif key == "unset_env":
                _, var = result
                try:
                    os.environ.pop(var, None)
                    cfg = _load_tui_config()
                    if "env" in cfg and var in cfg["env"]:
                        cfg["env"].pop(var, None)
                    _save_tui_config(cfg)
                    if log:
                        log.write(RichText.from_markup(f"[green]Unset {var}[/green]"))
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Unset env failed: {e}[/red]"))

            elif key == "toggle_session_recording":
                cur = os.environ.get("CAI_DISABLE_SESSION_RECORDING", "").lower() == "true"
                if cur:
                    os.environ.pop("CAI_DISABLE_SESSION_RECORDING", None)
                    state = "enabled"
                else:
                    os.environ["CAI_DISABLE_SESSION_RECORDING"] = "true"
                    state = "disabled"
                if log:
                    log.write(RichText.from_markup(f"[green]Session recording {state}[/green]"))

            elif key == "reset_defaults":
                try:
                    if os.path.exists(CONFIG_FILE):
                        os.remove(CONFIG_FILE)
                    if log:
                        log.write(
                            RichText.from_markup(
                                "[green]Reset TUI config to defaults (config file removed)[/green]"
                            )
                        )
                except Exception as e:
                    if log:
                        log.write(RichText.from_markup(f"[red]Reset failed: {e}[/red]"))

        except Exception:
            if log:
                log.write(RichText.from_markup("[red]Error handling config action[/red]"))

    # ------------------------------------------------------------------ modal callback

    async def _handle_agent_modal(self, result) -> None:
        if result is None:
            return  # cancelled
        action, agent_name = result
        new_agent = self._available_agents.get(agent_name)
        if new_agent is None:
            return

        if action == "update":
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.update_agent(new_agent, agent_name)
                self._set_active_terminal(self._active_term_id)
            except Exception:
                pass
            self._highlight_active_agent(agent_name)
        elif action == "new":
            await self._add_terminal(new_agent, agent_name)
            self._highlight_active_agent(agent_name)

    # ------------------------------------------------------------------ input events

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        input_id = event.input.id or ""
        text = event.value.strip()
        event.input.clear()

        if input_id == "queue-input":
            if text:
                self._add_queue_item(text)
            return

    # ------------------------------------------------------------------ queue

    def _parse_broadcast_suffix(self, text: str) -> tuple[str, bool]:
        raw = str(text or "").strip()
        if not raw:
            return "", False
        lower = raw.lower()
        if lower.endswith(" all"):
            return raw[:-4].rstrip(), True
        return raw, False

    async def _broadcast_prompt(self, text: str, source_tid: int | None = None) -> None:
        message = str(text or "").strip()
        if not message:
            return
        panels = sorted(list(self.query(TerminalPanel)), key=lambda p: p._term_id)
        for panel in panels[:4]:
            try:
                await panel.dispatch(message)
            except Exception:
                continue
        try:
            self._log_to_active_terminal(
                f"[broadcast] sent to {min(4, len(panels))} terminals: {message[:120]}",
                style="#00ff00",
            )
        except Exception:
            pass
        if source_tid is not None:
            try:
                self._set_active_terminal(source_tid)
            except Exception:
                pass

    def _queue_item_label(self, idx: int, item: dict) -> str:
        status = str(item.get("status", "pending"))
        marker = "○"
        if status == "running":
            marker = "▶"
        elif status == "completed":
            marker = "✓"
        elif status == "error":
            marker = "✗"

        broadcast = bool(item.get("broadcast", False))
        mode_tag = " [ALL]" if broadcast else ""
        text = str(item.get("text", ""))
        return f"{marker} [{idx + 1}]{mode_tag} {text}"

    def _sync_queue_broadcast_button(self) -> None:
        try:
            btn = self.query_one("#queue-broadcast-mode", Button)
            if self._queue_broadcast_mode:
                btn.label = "Broadcast: ON"
                btn.add_class("-active-team")
            else:
                btn.label = "Broadcast: OFF"
                btn.remove_class("-active-team")
        except Exception:
            return

    def _update_queue_status(self) -> None:
        pending = sum(1 for i in self._queue_items if i.get("status") == "pending")
        running = sum(1 for i in self._queue_items if i.get("status") == "running")
        completed = sum(1 for i in self._queue_items if i.get("status") == "completed")
        errors = sum(1 for i in self._queue_items if i.get("status") == "error")
        mode = "ON" if self._queue_broadcast_mode else "OFF"
        run_state = "running" if self._queue_running else "idle"
        if self._responsive_mode == "small":
            text = f"Q p:{pending} r:{running} d:{completed} e:{errors} b:{mode} {run_state}"
        else:
            text = (
                f"Queue: {pending} pending · {running} running · {completed} done · {errors} errors · "
                f"broadcast {mode} · {run_state}"
            )
        try:
            self.query_one("#queue-status", Static).update(text)
        except Exception:
            pass

    def _update_queue_view(self) -> None:
        try:
            lv = self.query_one("#queue-list", ListView)
        except Exception:
            return

        for child in list(lv.children):
            try:
                child.remove()
            except Exception:
                pass

        for idx, item in enumerate(self._queue_items):
            label = self._queue_item_label(idx, item)
            lv.mount(ListItem(Label(label), id=f"queue-item-{idx}"))

        self._update_queue_status()

    def _toggle_queue_broadcast_mode(self) -> None:
        self._queue_broadcast_mode = not self._queue_broadcast_mode
        self._sync_queue_broadcast_button()
        self._update_queue_status()
        self._log_to_active_terminal(
            f"[queue] broadcast mode {'ON' if self._queue_broadcast_mode else 'OFF'}"
        )

    def _add_from_queue_input(self) -> None:
        try:
            inp = self.query_one("#queue-input", Input)
            raw = inp.value.strip()
            if not raw:
                return
            inp.clear()
        except Exception:
            return

        self._add_queue_item(raw)

    @on(ListView.Highlighted, "#queue-list")
    def _on_queue_highlighted(self, event: ListView.Highlighted) -> None:
        try:
            self._queue_selected_idx = int(getattr(event.list_view, "index", -1))
        except Exception:
            self._queue_selected_idx = None

    def _selected_queue_index(self) -> Optional[int]:
        idx = self._queue_selected_idx
        try:
            lv = self.query_one("#queue-list", ListView)
            current_idx = int(getattr(lv, "index", -1))
            if current_idx >= 0:
                idx = current_idx
        except Exception:
            pass

        if idx is None:
            return None
        if idx < 0 or idx >= len(self._queue_items):
            return None
        return idx

    def _delete_selected_queue_item(self) -> None:
        idx = self._selected_queue_index()
        if idx is None:
            self._log_to_active_terminal("[queue] no selected prompt to delete", style="#ff6600")
            return
        try:
            removed = self._queue_items.pop(idx)
            self._queue_selected_idx = None
            self._update_queue_view()
            self._log_to_active_terminal(f"[queue] deleted: {removed.get('text', '')[:120]}")
        except Exception:
            pass

    def _clear_queue(self) -> None:
        self._queue_items = []
        self._queue_selected_idx = None
        self._update_queue_view()
        self._log_to_active_terminal("[queue] cleared all queued prompts")

    @work(exclusive=True)
    async def _run_queue_worker(self) -> None:
        if self._queue_running:
            return

        pending = [i for i in self._queue_items if i.get("status") == "pending"]
        if not pending:
            self._log_to_active_terminal("[queue] no pending prompts to run", style="#ff6600")
            return

        self._queue_running = True
        self._update_queue_status()
        total = len(pending)
        current = 0

        try:
            for item in self._queue_items:
                if item.get("status") != "pending":
                    continue
                current += 1
                item["status"] = "running"
                self._update_queue_view()

                text = str(item.get("text", "")).strip()
                try:
                    broadcast = bool(item.get("broadcast", False) or self._queue_broadcast_mode)
                    if broadcast:
                        await self._broadcast_prompt(text)
                    else:
                        panel = self.query_one(
                            f"#terminal-panel-{self._active_term_id}", TerminalPanel
                        )
                        await panel.dispatch(text)
                    item["status"] = "completed"
                    self._log_to_active_terminal(
                        f"[queue] ({current}/{total}) completed: {text[:120]}"
                    )
                except Exception as exc:
                    item["status"] = "error"
                    item["error"] = str(exc)
                    self._log_to_active_terminal(
                        f"[queue] ({current}/{total}) failed: {text[:120]} · {exc}", style="#ff4444"
                    )

                self._update_queue_view()
                await asyncio.sleep(0)
        finally:
            self._queue_running = False
            self._update_queue_status()

    def _add_queue_item(self, text: str) -> None:
        msg, explicit_broadcast = self._parse_broadcast_suffix(text)
        if not msg:
            return
        self._queue_items.append(
            {
                "text": msg,
                "status": "pending",
                "broadcast": bool(explicit_broadcast),
            }
        )
        self._update_queue_view()

    # ------------------------------------------------------------------ teams

    def _team_tooltip_text(self, idx: int, label: str, agent_types: list[str]) -> str:
        parts: list[str] = []
        for agent_name in (
            "redteam_agent",
            "blueteam_agent",
            "bug_bounter_agent",
            "retester_agent",
        ):
            count = agent_types.count(agent_name)
            if count > 0:
                parts.append(f"{count} {agent_name}")
        composition = f"#{idx + 1}: " + " + ".join(parts)
        lines = [composition]
        for i, name in enumerate(agent_types[:4], start=1):
            lines.append(f"T{i}: {name}")
        hint = self._team_playbook_hint(idx)
        if hint:
            lines.append(f"Best for: {hint}")
        return "\n".join(lines)

    def _team_playbook_hint(self, idx: int) -> str:
        if 0 <= idx < len(TEAM_PLAYBOOK_HINTS):
            return TEAM_PLAYBOOK_HINTS[idx]
        return ""

    def _update_team_playbook_preview(self, idx: int | None) -> None:
        try:
            preview = self.query_one("#team-playbook-preview", Static)
        except Exception:
            return

        if idx is None or idx < 0 or idx >= len(TEAM_PRESETS):
            preview.update("Select a team to see strategy hints.")
            return

        label, composition = TEAM_PRESETS[idx]
        hint = self._team_playbook_hint(idx)
        text = f"[bold]Team #{idx + 1}: {label}[/bold]\nT1-T4: {', '.join(composition[:4])}\n{hint}"
        try:
            preview.update(RichText.from_markup(text))
        except Exception:
            preview.update(text)

    def _sync_team_buttons_metadata(self) -> None:
        for i, (label, agent_types) in enumerate(TEAM_PRESETS):
            try:
                btn = self.query_one(f"#team-{i}", Button)
                btn.label = f"#{i + 1}: {label}"
                btn.tooltip = self._team_tooltip_text(i, label, agent_types)
            except Exception:
                pass

    @work(exclusive=True)
    async def _activate_team_worker(self, idx: int) -> None:
        if idx < 0 or idx >= len(TEAM_PRESETS):
            return

        label, agent_types = TEAM_PRESETS[idx]
        previous_active = self._active_term_id

        # Ensure we have 4 terminals available.
        panels = sorted(list(self.query(TerminalPanel)), key=lambda p: p._term_id)
        while len(panels) < 4:
            next_slot = len(panels)
            agent_name = agent_types[min(next_slot, len(agent_types) - 1)]
            agent_obj = self._available_agents.get(agent_name, self._agent)
            await self._add_terminal(agent_obj, agent_name)
            panels = sorted(list(self.query(TerminalPanel)), key=lambda p: p._term_id)

        # Apply the team mapping to T1..T4 preserving terminal logs/history.
        for slot in range(4):
            panel = panels[slot]
            target_agent_name = agent_types[slot]
            target_agent_obj = self._available_agents.get(target_agent_name)
            if target_agent_obj is None:
                continue
            panel.update_agent(target_agent_obj, target_agent_name)

        if self._active_team is not None:
            try:
                self.query_one(f"#team-{self._active_team}", Button).remove_class("-active-team")
            except Exception:
                pass
        self._active_team = idx
        try:
            self.query_one(f"#team-{idx}", Button).add_class("-active-team")
        except Exception:
            pass
        self._update_team_playbook_preview(idx)

        # Restore active terminal and refresh dependent header/tool views.
        try:
            self._set_active_terminal(previous_active)
        except Exception:
            pass

        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            panel.query_one(f"#term-log-{self._active_term_id}", RichLog).write(
                RichText.from_markup(
                    f"  [dim]Applied Team [bold]#{idx + 1}: {label}[/bold] to T1-T4 (history preserved)\n"
                    f"  Playbook: {self._team_playbook_hint(idx)}[/dim]"
                )
            )
        except Exception:
            pass

    def _activate_team(self, idx: int) -> None:
        self._activate_team_worker(idx)

    def _prompt_new_team(self) -> None:
        try:
            inp = self.query_one("#queue-input", Input)
            inp.placeholder = "team: agent1 agent2 agent3…"
            inp.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------ sessions

    async def _populate_sessions_list(self) -> None:
        """Discover JSONL session files in `logs/` and populate the Sessions list view."""
        try:
            scroll = self.query_one("#sessions-scroll", ScrollableContainer)
        except Exception:
            return

        # Collect current selection path so we can preserve it if possible
        prev_selected_path = None
        try:
            prev_idx = getattr(self, "_session_selected_idx", None)
            if prev_idx is not None and hasattr(self, "_session_files"):
                prev_selected_path = self._session_files.get(prev_idx)
        except Exception:
            prev_selected_path = None

        # Discover files and their mtimes safely
        logs_dir = "logs"
        try:
            raw_names = [f for f in os.listdir(logs_dir) if f.endswith(".jsonl")]
        except Exception:
            raw_names = []

        files = []
        for fname in raw_names:
            path = os.path.join(logs_dir, fname)
            try:
                mtime_ts = os.path.getmtime(path)
            except Exception:
                mtime_ts = 0
            files.append((fname, mtime_ts))

        files_sorted = sorted(files, key=lambda t: t[1], reverse=True)

        # Remove the old ListView entirely and create a fresh one so the Textual
        # widget registry is fully cleared before we mount new items with the same IDs.
        # (child.remove() is non-blocking and leaves stale IDs in the registry.)
        try:
            old_lv = self.query_one("#sessions-list", ListView)
            await old_lv.remove()
        except Exception:
            pass
        lv = ListView(id="sessions-list")
        await scroll.mount(lv)

        self._session_files = {}
        # mapping of idx -> actions container (to toggle visibility)
        self._session_action_containers = {}
        for idx, (fname, mtime_ts) in enumerate(files_sorted):
            path = os.path.join(logs_dir, fname)
            try:
                mtime = (
                    datetime.fromtimestamp(mtime_ts).strftime("%Y-%m-%d %H:%M") if mtime_ts else "?"
                )
            except Exception:
                mtime = "?"
            header = f"{fname}  ({mtime})"
            item = ListItem(id=f"session-item-{idx}")
            await lv.mount(item)
            # Header button: toggles the action area for this session
            await item.mount(Button(header, id=f"session-toggle-{idx}", classes="agent-btn"))
            # Actions container starts hidden and is shown when header is clicked
            actions = Vertical(id=f"session-actions-{idx}")
            actions.display = False
            await item.mount(actions)
            await actions.mount(Button("Select", id=f"session-select-{idx}", classes="team-btn"))
            await actions.mount(Button("Open", id=f"session-open-{idx}", classes="agent-btn"))
            await actions.mount(Button("Resume", id=f"session-resume-{idx}", classes="agent-btn"))
            await actions.mount(Button("Export", id=f"session-export-{idx}", classes="agent-btn"))
            await actions.mount(Button("Rename", id=f"session-rename-{idx}", classes="team-btn"))
            await actions.mount(
                Button("Delete", id=f"session-delete-{idx}", classes="modal-btn modal-btn--cancel")
            )

            self._session_files[idx] = path
            self._session_action_containers[idx] = actions

        # Preserve previous selection if possible, otherwise pick the first
        if self._session_files:
            new_sel = None
            if prev_selected_path:
                for k, p in self._session_files.items():
                    if p == prev_selected_path:
                        new_sel = k
                        break
            if new_sel is None:
                new_sel = min(self._session_files.keys())
            try:
                self._session_selected_idx = new_sel
                self._set_selected_session(new_sel)
            except Exception:
                self._session_selected_idx = None
        else:
            self._session_selected_idx = None

    @work(exclusive=True)
    async def _populate_sessions_list_worker(self) -> None:
        await self._populate_sessions_list()

    @work(exclusive=False)
    async def _session_open_worker(self, idx: int) -> None:
        """Load messages from a session JSONL into the current active agent's history."""
        from rich.text import Text as RichText

        try:
            path = self._session_files.get(idx)
            if not path:
                return
            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl

            messages = load_history_from_jsonl(path)

            # Merge into active agent similar to /load behavior
            from cai.repl.commands.parallel import ParallelCommand
            from cai.sdk.agents.models.openai_chatcompletions import (
                ACTIVE_MODEL_INSTANCES,
                PERSISTENT_MESSAGE_HISTORIES,
            )
            from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

            parallel_cmd = ParallelCommand()
            current_agent = AGENT_MANAGER.get_active_agent()
            current_agent_name = AGENT_MANAGER._active_agent_name
            if not current_agent_name:
                return

            current_history = AGENT_MANAGER.get_message_history(current_agent_name) or []
            original_signatures = set()
            for msg in current_history:
                try:
                    sig = parallel_cmd._get_message_signature(msg)
                    if sig:
                        original_signatures.add(sig)
                except Exception:
                    continue

            unique_messages = []
            for msg in messages:
                try:
                    sig = parallel_cmd._get_message_signature(msg)
                except Exception:
                    sig = None
                if sig and sig not in original_signatures:
                    unique_messages.append(msg)
                    original_signatures.add(sig)

            if not unique_messages:
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                        RichText.from_markup(
                            f"[dim]No new messages to add from {os.path.basename(path)}[/dim]"
                        )
                    )
                except Exception:
                    pass
                return

            final_history = current_history + unique_messages

            # Find active model instance
            model_instance = None
            for (name, inst_id), model_ref in ACTIVE_MODEL_INSTANCES.items():
                try:
                    if name == current_agent_name:
                        model_instance = model_ref() if model_ref else None
                        break
                except Exception:
                    continue

            if model_instance:
                model_instance.message_history.clear()
                os.environ["CAI_CONTEXT_USAGE"] = "0.0"
                for msg in final_history:
                    try:
                        model_instance.add_to_message_history(msg)
                    except Exception:
                        pass
            else:
                PERSISTENT_MESSAGE_HISTORIES[current_agent_name] = final_history

            AGENT_MANAGER._message_history[current_agent_name] = final_history

            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                    RichText.from_markup(
                        f"[green]Loaded {len(unique_messages)} messages into {current_agent_name} from {os.path.basename(path)}[/green]"
                    )
                )
            except Exception:
                pass

        except Exception as e:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                    RichText.from_markup(f"[red]Error loading session: {e}[/red]")
                )
            except Exception:
                pass

    @work(exclusive=False)
    async def _session_delete_worker(self, idx: int) -> None:
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            # Confirm deletion with modal
            result = await self.push_screen_wait(ConfirmModal(f"Delete {os.path.basename(path)}?"))
            if not result:
                return
            try:
                os.remove(path)
            except Exception:
                pass
            # Ensure any expanded action areas are collapsed and selection cleared
            try:
                if hasattr(self, "_session_action_containers"):
                    for k, cont in list(self._session_action_containers.items()):
                        try:
                            cont.display = False
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                self._session_selected_idx = None
                self._update_session_preview(None)
            except Exception:
                pass

            # Schedule a refresh worker rather than awaiting the populate directly
            try:
                self._populate_sessions_list_worker()
            except Exception:
                # Fallback: try to call populate directly
                try:
                    await self._populate_sessions_list()
                except Exception:
                    pass
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_rename_worker(self, idx: int) -> None:
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            default = os.path.basename(path)
            result = await self.push_screen_wait(PromptModal("Rename file to:", default))
            if not result:
                return
            dest = os.path.join("logs", result)
            if not dest.endswith(".jsonl"):
                dest += ".jsonl"
            try:
                os.rename(path, dest)
            except Exception:
                pass
            await self._populate_sessions_list()
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_resume_worker(self, idx: int) -> None:
        try:
            # Try to infer which agent this session belongs to, open a terminal for it,
            # then load the session into that new terminal (so resume maps to the right agent).
            path = self._session_files.get(idx)
            if not path:
                return

            from cai.sdk.agents.run_to_jsonl import load_history_from_jsonl

            messages = load_history_from_jsonl(path)

            # Infer agent key from messages
            best_key = self._infer_agent_from_session_messages(messages)

            # If we have a good match among available agents, open a terminal for it
            opened_terminal = False
            if best_key and best_key in self._available_agents:
                try:
                    agent_obj = self._available_agents.get(best_key)
                    await self._add_terminal(agent_obj, best_key)
                    opened_terminal = True
                except Exception:
                    opened_terminal = False

            # If no mapping found, fall back to opening a terminal for the active panel's agent
            if not opened_terminal:
                try:
                    panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                    agent = panel._agent
                    agent_name = panel._agent_name
                    await self._add_terminal(agent, agent_name)
                except Exception:
                    pass

            # Schedule the session load into the (newly) active terminal
            # Use the existing worker that merges messages into the active agent
            self._session_open_worker(idx)
        except Exception:
            pass

    def _set_selected_session(self, idx: Optional[int]) -> None:
        """Set the currently selected session index and update visuals."""
        try:
            self._session_selected_idx = idx
        except Exception:
            self._session_selected_idx = None

        # Update visual selection for list items
        try:
            for k in list(self._session_files.keys()):
                try:
                    li = self.query_one(f"#session-item-{k}", ListItem)
                    if k == idx:
                        li.add_class("-selected")
                    else:
                        li.remove_class("-selected")
                except Exception:
                    pass
        except Exception:
            pass

        # Update preview for the newly selected session
        try:
            self._update_session_preview(self._session_selected_idx)
        except Exception:
            pass

    def _toggle_session_actions(self, idx: int) -> None:
        """Toggle the visibility of the action buttons for a session list item.

        Collapses all other session action areas and toggles the chosen one.
        Selecting a session also updates the preview and visual selection.
        """
        try:
            if not hasattr(self, "_session_action_containers"):
                return
            for k, container in self._session_action_containers.items():
                try:
                    if k == idx:
                        # toggle this container
                        current = bool(getattr(container, "display", False))
                        container.display = not current
                    else:
                        container.display = False
                except Exception:
                    pass
            # Update selection/preview as if the session was selected
            try:
                self._set_selected_session(idx)
            except Exception:
                pass
        except Exception:
            pass

    @work(exclusive=False)
    async def _session_export_worker(self, idx: int) -> None:
        """Export a session file to an arbitrary location chosen by the user."""
        try:
            path = self._session_files.get(idx)
            if not path:
                return
            default = os.path.basename(path)
            result = await self.push_screen_wait(
                PromptModal("Export to (path or directory):", default)
            )
            if not result:
                return
            dest = os.path.expanduser(result)
            import shutil

            if os.path.isdir(dest):
                dest_path = os.path.join(dest, os.path.basename(path))
            else:
                parent = os.path.dirname(dest)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                dest_path = dest
            shutil.copy2(path, dest_path)
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                    RichText.from_markup(
                        f"[green]Exported {os.path.basename(path)} to {dest_path}[/green]"
                    )
                )
            except Exception:
                pass
        except Exception as e:
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                panel.query_one(f"#term-log-{panel._term_id}", RichLog).write(
                    RichText.from_markup(f"[red]Export failed: {e}[/red]")
                )
            except Exception:
                pass

    def _update_session_preview(self, idx: Optional[int]) -> None:
        """Update the `#session-preview` Static with token/cost summary and a snippet."""
        try:
            preview = self.query_one("#session-preview", Static)
        except Exception:
            return

        if idx is None:
            try:
                preview.update("")
            except Exception:
                pass
            return

        path = self._session_files.get(idx)
        if not path or not os.path.exists(path):
            try:
                preview.update("[dim]No preview available")
            except Exception:
                pass
            return

        # Import helpers if available; avoid leaving names unbound for static analysis
        get_token_stats_fn = None
        try:
            from cai.sdk.agents.run_to_jsonl import (
                get_token_stats as _get_token_stats,
                load_history_from_jsonl,
            )

            get_token_stats_fn = _get_token_stats
            messages = load_history_from_jsonl(path)
        except Exception:
            messages = []

        # Try to get token/cost stats
        try:
            if get_token_stats_fn is not None:
                model_name, prompt_t, completion_t, total_cost, active, idle = get_token_stats_fn(
                    path
                )
                stats = f"Model: {model_name or 'unknown'} · prompt:{prompt_t} completion:{completion_t} cost:{total_cost}"
            else:
                raise Exception("token stats unavailable")
        except Exception:
            stats = "Model: ? · prompt:? completion:? cost:?"

        # Build snippet: last 6 non-system messages
        snippet_lines = []
        try:
            filtered = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
            for m in filtered[-6:]:
                role = m.get("role", "")
                content = m.get("content") or ""
                if not content:
                    # handle nested message structures
                    content = str(m)
                # single-line snippet
                line = content.strip().splitlines()[0][:160]
                snippet_lines.append(f"{role}: {line}")
        except Exception:
            snippet_lines = []

        snippet = "\n".join(snippet_lines) or "(no preview)"

        body = f"[bold]{os.path.basename(path)}[/bold]\n{stats}\n\n{snippet}"
        try:
            preview.update(RichText.from_markup(body))
        except Exception:
            try:
                preview.update(body)
            except Exception:
                pass

    def _infer_agent_from_session_messages(self, messages: list) -> Optional[str]:
        """Try to infer the best-matching available agent key from session messages.

        Heuristic:
        - Collect candidate agent identifiers from message fields: `agent_name`, `name`, `sender`.
        - Normalize and score available agents by matching candidate identifiers against
          agent keys and display names (`agent.name` or `_pretty_name(key)`).
        - Return the agent key with the highest score, or None if no confident match.
        """
        try:
            from collections import Counter
        except Exception:
            Counter = None

        if not messages:
            return None

        # Collect candidate identifiers from messages
        candidates = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            for k in ("agent_name", "name", "sender"):
                v = msg.get(k)
                if v:
                    # Remove instance suffixes like ' #1'
                    try:
                        v2 = v.split(" #")[0]
                    except Exception:
                        v2 = v
                    candidates.append(str(v2))

        if not candidates:
            return None

        counts = Counter(candidates) if Counter is not None else {}

        # Score each available agent
        best_key = None
        best_score = 0

        for key, agent in self._available_agents.items():
            # Build match variants
            display = getattr(agent, "name", None) or _pretty_name(key)
            variants = {
                key,
                key.replace("_agent", "").replace("_pattern", "").replace("_swarm", ""),
            }
            variants.add(display)
            variants.add(display.replace(" ", ""))

            score = 0
            for cand, cnt in counts.items() if counts else []:
                cand_n = "".join([c for c in cand.lower() if c.isalnum()])
                for v in variants:
                    try:
                        v_n = "".join([c for c in str(v).lower() if c.isalnum()])
                    except Exception:
                        v_n = ""
                    if not v_n or not cand_n:
                        continue
                    if cand_n == v_n:
                        score += 10 * cnt
                    elif cand_n in v_n or v_n in cand_n:
                        score += 5 * cnt
                    else:
                        # partial word overlap
                        if any(part in v_n for part in cand_n.split() if len(part) > 3):
                            score += 2 * cnt

            if score > best_score:
                best_score = score
                best_key = key

        # Require some minimal confidence to accept match
        if best_score >= 5:
            return best_key
        return None

    # ------------------------------------------------------------------ agent highlight

    def _highlight_active_agent(self, name: str) -> None:
        for btn in self.query(".agent-btn"):
            btn.remove_class("-active-agent")
        try:
            self.query_one(f"#agent-{name}", Button).add_class("-active-agent")
        except Exception:
            pass

    def _render_config_table(self, width: int = 120) -> str:
        """Render the configuration variables as a table string using Rich."""
        import io

        from rich.console import Console
        from rich.table import Table

        cfg = _load_tui_config()

        table = Table(show_header=True, header_style="bold #00ff00")
        table.add_column("#", width=3)
        table.add_column("Variable", width=40)
        table.add_column("Value", width=20)
        table.add_column("Default", width=12)
        table.add_column("Description", width=60)

        for idx, v in enumerate(CONFIG_VARIABLES):
            name = str(v.get("name") or "")
            default = v.get("default", "")
            desc = v.get("description", "")
            value = os.environ.get(name) or cfg.get("env", {}).get(name) or default
            # Ensure strings
            value_s = str(value) if value is not None else ""
            table.add_row(str(idx + 1), name, value_s, str(default), desc)

        buf = io.StringIO()
        console = Console(file=buf, width=width, color_system=None)
        console.print(table)
        return buf.getvalue()

    def _display_config_table(self) -> None:
        """Write the rendered config table into the active terminal's RichLog."""
        from rich.text import Text as RichText

        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            log = panel.query_one(f"#term-log-{panel._term_id}", RichLog)
        except Exception:
            return

        try:
            table_width = 100
            try:
                table_width = max(60, min(160, int(self.size.width) - 6))
            except Exception:
                pass
            table_str = self._render_config_table(width=table_width)
            # Write the table as a multiline RichText so it preserves formatting
            log.write(RichText(table_str))
        except Exception:
            try:
                log.write(RichText("Failed to render config table"))
            except Exception:
                pass

    def _command_palette_commands(self) -> list[dict]:
        return [
            {
                "id": "clear",
                "name": "clear",
                "description": "Clear terminal output",
                "shortcut": "Ctrl+L",
            },
            {
                "id": "save",
                "name": "save",
                "description": "Save current session",
                "shortcut": "",
            },
            {
                "id": "load",
                "name": "load",
                "description": "Load previous session",
                "shortcut": "",
            },
            {
                "id": "export",
                "name": "export",
                "description": "Export conversation",
                "shortcut": "",
            },
            {
                "id": "reset",
                "name": "reset",
                "description": "Reset agent context",
                "shortcut": "",
            },
            {
                "id": "help",
                "name": "help",
                "description": "Show help information",
                "shortcut": "",
            },
        ]

    def _record_palette_recent(self, cmd_id: str) -> None:
        key = str(cmd_id or "").strip()
        if not key:
            return
        self._command_palette_recent = [k for k in self._command_palette_recent if k != key]
        self._command_palette_recent.insert(0, key)
        self._command_palette_recent = self._command_palette_recent[:20]

    def _selected_or_latest_session_idx(self) -> Optional[int]:
        selected = getattr(self, "_session_selected_idx", None)
        if (
            selected is not None
            and hasattr(self, "_session_files")
            and selected in self._session_files
        ):
            return selected
        try:
            if hasattr(self, "_session_files") and self._session_files:
                return sorted(self._session_files.keys())[0]
        except Exception:
            pass
        return None

    @work(exclusive=False)
    async def _execute_palette_command_worker(self, cmd_id: str) -> None:
        cmd = str(cmd_id or "").strip().lower()
        if not cmd:
            return

        self._record_palette_recent(cmd)

        if cmd == "clear":
            self.action_clear_active()
            self._log_to_active_terminal("[palette] cleared terminal output")
            return

        if cmd == "save":
            try:
                from cai.sdk.agents.run_to_jsonl import get_session_recorder

                recorder = get_session_recorder()
                filename = str(getattr(recorder, "filename", ""))
                self._populate_sessions_list_worker()
                if filename:
                    self._log_to_active_terminal(f"[palette] session recorder active: {filename}")
                else:
                    self._log_to_active_terminal("[palette] session persisted", style="#00aa00")
            except Exception as exc:
                self._log_to_active_terminal(f"[palette] save failed: {exc}", style="#ff4444")
            return

        if cmd == "load":
            idx = self._selected_or_latest_session_idx()
            if idx is None:
                self._log_to_active_terminal("[palette] no session found to load", style="#ff6600")
                return
            self._session_open_worker(idx)
            return

        if cmd == "export":
            idx = self._selected_or_latest_session_idx()
            if idx is None:
                self._log_to_active_terminal(
                    "[palette] no session found to export", style="#ff6600"
                )
                return
            self._session_export_worker(idx)
            return

        if cmd == "reset":
            try:
                from cai.sdk.agents.models.openai_chatcompletions import (
                    ACTIVE_MODEL_INSTANCES,
                    PERSISTENT_MESSAGE_HISTORIES,
                )
                from cai.sdk.agents.simple_agent_manager import AGENT_MANAGER

                current_agent_name = AGENT_MANAGER._active_agent_name
                if current_agent_name:
                    AGENT_MANAGER._message_history[current_agent_name] = []
                    PERSISTENT_MESSAGE_HISTORIES[current_agent_name] = []
                    for (name, _inst_id), model_ref in list(ACTIVE_MODEL_INSTANCES.items()):
                        if name != current_agent_name:
                            continue
                        try:
                            model = model_ref() if model_ref else None
                            if model is not None and hasattr(model, "message_history"):
                                model.message_history.clear()
                        except Exception:
                            continue
                    os.environ["CAI_CONTEXT_USAGE"] = "0.0"
                self._log_to_active_terminal("[palette] agent context reset")
            except Exception as exc:
                self._log_to_active_terminal(f"[palette] reset failed: {exc}", style="#ff4444")
            return

        if cmd == "help":
            try:
                panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
                await panel.dispatch("/help")
            except Exception as exc:
                self._log_to_active_terminal(f"[palette] help failed: {exc}", style="#ff4444")
            return

        self._log_to_active_terminal(f"[palette] unknown command: {cmd}", style="#ff6600")

    @work(exclusive=False)
    async def _open_command_palette_worker(self) -> None:
        result = await self.push_screen_wait(
            CommandPaletteModal(
                commands=self._command_palette_commands(),
                recent=self._command_palette_recent,
            )
        )
        if not result:
            return
        if isinstance(result, (tuple, list)) and len(result) >= 2 and result[0] == "run":
            self._execute_palette_command_worker(str(result[1]))

    # ------------------------------------------------------------------ actions

    def action_clear_active(self) -> None:
        try:
            self.query_one(f"#term-log-{self._active_term_id}", RichLog).clear()
        except Exception:
            pass

    def action_cancel_active(self) -> None:
        try:
            panel = self.query_one(f"#terminal-panel-{self._active_term_id}", TerminalPanel)
            if not panel.cancel_active_run():
                panel._set_status("")
        except Exception:
            pass

    def action_toggle_sidebar(self) -> None:
        try:
            tabs = self.query_one("#sidebar-tabs", TabbedContent)
            current = str(getattr(tabs, "active", "tab-terminal") or "tab-terminal")
            self._switch_top_tab("tab-agents" if current == "tab-terminal" else "tab-terminal")
        except Exception:
            pass

    def action_command_palette(self) -> None:
        self._open_command_palette_worker()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_tui(agent=None, initial_prompt: Optional[str] = None) -> None:
    """Launch the Matrix TUI (blocks until the user exits with ^q or /exit)."""
    # Guarantee .env is loaded and LOCAL_* vars are propagated to OPENAI_* before
    # the first agent request fires — even when the TUI is invoked directly without
    # going through cli.py (which normally calls initialize_env() at import time).
    try:
        from cai.bootstrap import initialize_env
        initialize_env()
    except Exception:
        pass
    CAIApp(agent=agent, initial_prompt=initial_prompt).run()
