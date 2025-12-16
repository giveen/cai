"""
Nikto tool wrapper for AI agents.
This module provides a wrapper for the Nikto web scanner that can be invoked programmatically.
"""

from cai.tools.common import run_command  # pylint: disable=E0401
from cai.sdk.agents import function_tool

@function_tool
def nikto(
    target: str,
    port: int = 80,
    args: str = "",
    ctf=None
) -> str:
    """
    A simple nikto tool to scan a specified target.

    Args:
        target: The target host or IP address to scan (e.g., http://10.10.110.123)
        port: Port to use (default 80)
        args: Additional arguments to pass to the nikto command
        ctf: CTF context for command execution

    Returns:
        str: The output of running the nikto command
    """
    command = f'nikto -h {target} -port {port} {args}'
    return run_command(command, ctf=ctf)
