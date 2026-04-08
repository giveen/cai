"""
Centralized tool registry.

ALL_TOOLS is the single source of truth for every @function_tool available to
agents.  Conditional tools (those requiring external API keys or services) are
added only when the relevant environment variable is present.
"""

import os

# ── Core recon / exploitation ─────────────────────────────────────────────────
from cai.tools.reconnaissance.generic_linux_command import generic_linux_command  # noqa: E501
from cai.tools.reconnaissance.exec_code import execute_code
from cai.tools.reconnaissance.ldap_search import ldap_search
from cai.tools.reconnaissance.nmap import nmap
from cai.tools.reconnaissance.netcat import netcat
from cai.tools.reconnaissance.netstat import netstat
from cai.tools.reconnaissance.curl import curl
from cai.tools.reconnaissance.wget import wget
from cai.tools.reconnaissance.smbclient_tool import smb_list_shares, smb_run_smbclient, smb_download_file  # noqa: E501
from cai.tools.reconnaissance.filesystem import cat_file, find_file, list_dir, pwd_command
from cai.tools.reconnaissance.crypto_tools import strings_command, decode64, decode_hex_bytes
from cai.tools.reconnaissance.blue_team_safe_command import blue_team_safe_command

# ── Command & control / lateral movement ────────────────────────────────────
from cai.tools.command_and_control.sshpass import run_ssh_command_with_credentials
from cai.tools.network.capture_traffic import capture_remote_traffic, remote_capture_session

# ── Web ───────────────────────────────────────────────────────────────────────
from cai.tools.web.headers import web_request_framework
from cai.tools.web.js_surface_mapper import js_surface_mapper

# ── Execution & scripting ─────────────────────────────────────────────────────
from cai.tools.misc.code_interpreter import execute_python_code
from cai.tools.others.scripting import scripting_tool
from cai.tools.misc.cli_utils import execute_cli_command

# ── Reasoning & memory ────────────────────────────────────────────────────────
from cai.tools.misc.reasoning import thought, think, write_key_findings, read_key_findings
from cai.tools.misc.rag import query_memory, add_to_memory_episodic, add_to_memory_semantic

# ── Always-on tool list ───────────────────────────────────────────────────────
ALL_TOOLS = [
    # Recon
    generic_linux_command,
    execute_code,
    ldap_search,
    nmap,
    netcat,
    netstat,
    curl,
    wget,
    smb_list_shares,
    smb_run_smbclient,
    smb_download_file,
    cat_file,
    find_file,
    list_dir,
    pwd_command,
    strings_command,
    decode64,
    decode_hex_bytes,
    blue_team_safe_command,
    # C2 / movement
    run_ssh_command_with_credentials,
    capture_remote_traffic,
    remote_capture_session,
    # Web
    web_request_framework,
    js_surface_mapper,
    # Execution & scripting
    execute_python_code,
    scripting_tool,
    execute_cli_command,
    # Reasoning & memory
    thought,
    think,
    write_key_findings,
    read_key_findings,
    query_memory,
    add_to_memory_episodic,
    add_to_memory_semantic,
]

# ── Conditional: OSINT / search ───────────────────────────────────────────────
if os.getenv('SHODAN_API_KEY'):
    from cai.tools.reconnaissance.shodan import shodan_search, shodan_host_info  # noqa: E402
    ALL_TOOLS.extend([shodan_search, shodan_host_info])

if os.getenv('GOOGLE_SEARCH_API_KEY') and os.getenv('GOOGLE_SEARCH_CX'):
    from cai.tools.web.search_web import make_google_search  # noqa: E402
    ALL_TOOLS.append(make_google_search)

if os.getenv('PERPLEXITY_API_KEY'):
    from cai.tools.web.search_web import (  # noqa: E402
        make_web_search_with_explanation,
        query_perplexity,
    )
    ALL_TOOLS.extend([make_web_search_with_explanation, query_perplexity])
