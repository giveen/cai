"""
Here are the curl tools.
"""
import shlex
from typing import List
from cai.tools.common import run_command  # pylint: disable=import-error
from cai.sdk.agents import function_tool

@function_tool
def curl(args: str = "", target: str = "", ctf=None) -> str:
    """
    A simple curl tool to make HTTP requests to a specified target.

    Args:
        args: Additional arguments to pass to the curl command
        target: The target URL to request

    Returns:
        str: The output of running the curl command
    """
    # Parse args into tokens and pass as an argument list to the central runner
    try:
        args_tokens: List[str] = shlex.split(args) if args else []
    except Exception:
        args_tokens = [args]

    cmd: List[str] = ["curl"] + args_tokens
    if target:
        cmd.append(target)

    return run_command(cmd, ctf=ctf)
