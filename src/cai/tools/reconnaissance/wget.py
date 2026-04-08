# FileDownloadTool in exploitFlow

"""
Wget tool
"""

from cai.sdk.agents import function_tool
from cai.tools import validation  # pylint: disable=import-error
from cai.tools.common import run_command  # pylint: disable=import-error
from cai.tools.validation import (  # pylint: disable=import-error
    is_url_safe,
    validate_args_no_injection,
)


def _validate_wget_input(args: str, url: str):
    """Return an error string if inputs are unsafe, else None."""
    err = validate_args_no_injection(args, 'args')
    if err:
        return err
    if url and not is_url_safe(url):
        return (
            f"Invalid url '{url}': must be a URL, IP, or hostname without whitespace or shell-special characters."
        )
    return None


@function_tool
def wget(url: str, args: str = '', timeout: int = 60) -> str:
    """
    Download files using wget with safe input checks.

    Args:
        url:   The URL or host to download from. Examples: "http://example.com/file.zip",
               "https://api.target.com/resource", "ftp://files.example.com/data.tar.gz",
               or a bare host/IP like "192.168.1.5".
        args:  Additional wget flags. Common examples:
                 "-O /tmp/file"           — write output to a file
                 "-q"                     — quiet (no progress)
                 "-c"                     — continue partially downloaded files
                 "-P /tmp/dir"            — set download directory
                 "--limit-rate=200k"      — throttle download speed
                 "-r -np"                 — recursive, no-parent (use with caution)
                 "--user user --password pw" — basic auth
        timeout: Maximum seconds to wait for the command (default 60).

    Returns:
        str: Raw wget output (stdout/stderr).

    Examples:
        wget("http://example.com/file.zip")
        wget("http://example.com/file.zip", args="-O /tmp/file.zip")
        wget("https://api.target.com/data", args="--limit-rate=100k -q", timeout=120)
    """
    err = _validate_wget_input(args, url)
    if err:
        return err

    command = f'wget {args} {url.strip()}'
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err

    result = run_command(command, timeout=timeout)
    if isinstance(result, str):
        result = validation.sanitize_tool_output(command, result)
    return result
