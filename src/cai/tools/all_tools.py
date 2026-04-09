"""
Centralized tool registry.

ALL_TOOLS is the single source of truth for every @function_tool available to
agents.  Conditional tools (those requiring external API keys or services) are
added only when the relevant environment variable is present.
"""

import os

# ── Command & control / lateral movement ────────────────────────────────────
from cai.tools.command_and_control.sshpass import run_ssh_command_with_credentials
from cai.tools.misc.cli_utils import execute_cli_command

# ── Execution & scripting ─────────────────────────────────────────────────────
from cai.tools.misc.code_interpreter import execute_python_code
from cai.tools.misc.rag import add_to_memory_episodic, add_to_memory_semantic, query_memory
from cai.tools.misc.rag_monitor import get_rag_status

# ── Reasoning & memory ────────────────────────────────────────────────────────
from cai.tools.misc.reasoning import read_key_findings, think, thought, write_key_findings
from cai.tools.network.capture_traffic import capture_remote_traffic, remote_capture_session_tool
from cai.tools.network.impacket import impacket_executor
from cai.tools.others.scripting import scripting_tool
from cai.tools.reconnaissance.blue_team_safe_command import blue_team_safe_command
from cai.tools.reconnaissance.crypto_tools import decode64, decode_hex_bytes, strings_command
from cai.tools.reconnaissance.curl import curl
from cai.tools.reconnaissance.exec_code import execute_code
from cai.tools.reconnaissance.filesystem import cat_file, find_file, list_dir, pwd_command

# ── Core recon / exploitation ─────────────────────────────────────────────────
from cai.tools.reconnaissance.generic_linux_command import generic_linux_command  # noqa: E501
from cai.tools.reconnaissance.ldap_search import ldap_search
from cai.tools.reconnaissance.netcat import netcat
from cai.tools.reconnaissance.netstat import netstat
from cai.tools.reconnaissance.nmap import nmap
from cai.tools.reconnaissance.smbclient_tool import (  # noqa: E501
    smb_download_file,
    smb_list_shares,
    smb_run_smbclient,
)
from cai.tools.reconnaissance.wget import wget

# ── Web ───────────────────────────────────────────────────────────────────────
from cai.tools.web.cewl import cewl
from cai.tools.web.headers import web_request_framework
from cai.tools.web.js_surface_mapper import js_surface_mapper
from cai.tools.web.search_web import duckduckgo_web_search
from cai.tools.web.session_pin import (
    get_pinned_session_cookie,
    set_session_cookie,
    unpin_session_cookie,
)
from cai.tools.web.cve_search import (
    cve_search_browse,
    cve_search_db_info,
    cve_search_last,
    cve_search_lookup,
    cve_search_product,
)
from cai.tools.web.sqlmap import sqlmap
from cai.tools.exploitation.exploit_search import github_poc_search

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
    remote_capture_session_tool,
    impacket_executor,
    # Web
    web_request_framework,
    js_surface_mapper,
    duckduckgo_web_search,
    sqlmap,
    cewl,
    cve_search_lookup,
    cve_search_product,
    cve_search_last,
    cve_search_browse,
    cve_search_db_info,
    # Exploit intelligence
    github_poc_search,
    set_session_cookie,
    get_pinned_session_cookie,
    unpin_session_cookie,
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
    get_rag_status,
]

# ── Conditional: OSINT / search ───────────────────────────────────────────────
if os.getenv("SHODAN_API_KEY"):
    from cai.tools.reconnaissance.shodan import shodan_host_info, shodan_search  # noqa: E402

    ALL_TOOLS.extend([shodan_search, shodan_host_info])

if os.getenv("GOOGLE_SEARCH_API_KEY") and os.getenv("GOOGLE_SEARCH_CX"):
    from cai.tools.web.google_search import make_google_search  # noqa: E402

    ALL_TOOLS.append(make_google_search)
