"""
BloodyAD tool wrapper for AI agents.
This module provides a wrapper for the BloodyAD Active Directory exploitation tool.
"""

from cai.tools.common import run_command  # pylint: disable=E0401
from cai.sdk.agents import function_tool

@function_tool
def bloodyad(
    command: str,
    subcommand: str = "",
    domain: str = "",
    username: str = "",
    password: str = "",
    kerberos: bool = False,
    host: str = "",
    dc_ip: str = "",
    args: str = "",
    ctf=None
) -> str:
    """
    A simple BloodyAD tool to interact with Active Directory.

    Args:
        command: The bloodyAD subcommand to execute (add, get, remove, set)
        subcommand: The specific subcommand for 'add' category (computer, user, etc.)
        domain: Domain used for NTLM authentication
        username: Username used for NTLM authentication
        password: Password or hash for authentication
        kerberos: Enable Kerberos authentication
        host: Hostname or IP of the DC
        dc_ip: IP of the DC (useful if --host can't resolve)
        args: Additional arguments to pass to the bloodyad command
        ctf: CTF context for command execution

    Returns:
        str: The output of running the bloodyad command
    """
    # Build base command with authentication options
    cmd_parts = ["bloodyAD"]
    
    if domain:
        cmd_parts.append(f"-d {domain}")
    if username:
        cmd_parts.append(f"-u {username}")
    if password:
        cmd_parts.append(f"-p {password}")
    if kerberos:
        cmd_parts.append("-k")
    if host:
        cmd_parts.append(f"--host {host}")
    if dc_ip:
        cmd_parts.append(f"--dc-ip {dc_ip}")
    
    # Add the main command and subcommand
    cmd_parts.append(command)
    if subcommand:
        cmd_parts.append(subcommand)
    
    # Add any additional args
    if args:
        cmd_parts.append(args)
    
    command_str = " ".join(cmd_parts)
    return run_command(command_str, ctf=ctf)
