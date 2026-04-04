"""
Here are the nmap tools.
"""
import shlex
from typing import List
from cai.tools.common import run_command  # pylint: disable=E0401
from cai.sdk.agents import function_tool


@function_tool
def nmap(args: str, target: str, ctf=None) -> str:
    """
    A simple nmap tool to scan a specified target.

    Args:
        args: Additional arguments to pass to the nmap command
        target: The target host or IP address to scan

    Returns:
        str: The output of running the nmap command
    """
    try:
        args_tokens: List[str] = shlex.split(args) if args else []
    except Exception:
        args_tokens = [args]

    cmd: List[str] = ["nmap"] + args_tokens
    if target:
        cmd.append(target)

    return run_command(cmd, ctf=ctf)
