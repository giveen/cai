# NetworkConnectionstool in exploitFlow
"""
Netstat tool
"""
import re

from cai.tools.common import run_command   # pylint: disable=import-error
from cai.sdk.agents import function_tool

# Block command chaining/substitution and redirection sequences.
# Disallow: ; && || | ` $( newlines > < backslash
_CMD_INJECT_RE = re.compile(r'(;|&&|\|\||\||`|\$\(|\n|\r|>|<|\\)')


def _validate_netstat_input(args: str):
    """Return an error string if inputs are unsafe, else None."""
    if args and _CMD_INJECT_RE.search(args):
        return (
            f"Invalid args '{args}': command injection or shell-special characters are not allowed."
        )
    if args and len(args) > 256:
        return "Invalid args: too long"
    return None


@function_tool
def netstat(args: str = '', timeout: int = 5) -> str:
    """
    netstat tool to list listening ports and associated programs.

    Args:
        args: Additional arguments to pass to the netstat command (e.g. "-tulnp").
              Do not include shell metacharacters or redirections.
        timeout: Maximum seconds to wait for the command (default 5).

    Returns:
        str: The output of running the netstat command, or an error string.

    Examples:
        netstat()  # default: `netstat -tuln`
        netstat(args='-tulnp')  # include program/PID column
    """
    err = _validate_netstat_input(args)
    if err:
        return err

    base = 'netstat -tuln'
    command = f"{base} {args.strip()}" if args else base
    return run_command(command, timeout=timeout)
