"""
Searchsploit tool wrapper for AI agents.
This module provides a wrapper for the searchsploit exploit database search tool.
"""

from cai.tools.common import run_command  # pylint: disable=E0401
from cai.sdk.agents import function_tool

@function_tool
def searchsploit(
    terms: str,
    case_sensitive: bool = False,
    exact_match: bool = False,
    strict_search: bool = False,
    title_only: bool = False,
    exclude_terms: str = "",
    cve: str = "",
    json_output: bool = False,
    verbose: bool = False,
    show_path: bool = False,
    path_id: str = "",
    show_urls: bool = False,
    disable_colour: bool = False,
    update: bool = False,
    mirror_exploit: str = "",
    examine_exploit: str = "",
    overflow: bool = False,
    display_id: bool = False,
    nmap_file: str = "",
    ctf=None
) -> str:
    """
    Search the Exploit-DB database for exploits.

    Args:
        terms: Search terms (can be any number of words)
        case_sensitive: Perform a case-sensitive search
        exact_match: Perform an exact match on exploit title
        strict_search: Perform a strict search, disabling fuzzy search for version ranges
        title_only: Search just the exploit title (default is title AND file path)
        exclude_terms: Remove values from results using pipe-separated terms
        cve: Search for Common Vulnerabilities and Exposures value
        json_output: Show result in JSON format
        verbose: Display more information in output
        show_path: Show full path to an exploit
        path_id: EDB-ID when using --path option
        show_urls: Show URLs to Exploit-DB.com rather than local path
        disable_colour: Disable colour highlighting in search results
        update: Check for and install any exploitdb package updates
        mirror_exploit: Mirror (copy) an exploit to the current working directory
        examine_exploit: Examine (open) the exploit using $PAGER
        overflow: Exploit titles are allowed to overflow their columns
        display_id: Display the EDB-ID value rather than local path
        nmap_file: Checks all results in Nmap's XML output with service version
        ctf: CTF context for command execution

    Returns:
        str: The output of running the searchsploit command
    """
    # Build base command
    cmd_parts = ["searchsploit"]
    
    # Add options
    if case_sensitive:
        cmd_parts.append("-c")
    if exact_match:
        cmd_parts.append("-e")
    if strict_search:
        cmd_parts.append("-s")
    if title_only:
        cmd_parts.append("-t")
    if exclude_terms:
        cmd_parts.append(f"--exclude={exclude_terms}")
    if cve:
        cmd_parts.append(f"--cve {cve}")
    if json_output:
        cmd_parts.append("-j")
    if verbose:
        cmd_parts.append("-v")
    if show_path and path_id:
        cmd_parts.append(f"-p {path_id}")
    elif show_path:
        cmd_parts.append("-p")
    if show_urls:
        cmd_parts.append("-w")
    if disable_colour:
        cmd_parts.append("--disable-colour")
    if update:
        cmd_parts.append("-u")
    if mirror_exploit:
        cmd_parts.append(f"-m {mirror_exploit}")
    if examine_exploit:
        cmd_parts.append(f"-x {examine_exploit}")
    if overflow:
        cmd_parts.append("-o")
    if display_id:
        cmd_parts.append("--id")
    if nmap_file:
        cmd_parts.append(f"--nmap {nmap_file}")
    
    # Add search terms
    cmd_parts.append(terms)
    
    command_str = " ".join(cmd_parts)
    return run_command(command_str, ctf=ctf)
