# FileDownloadTool in exploitFlow

"""
Wget tool
"""
import re

from cai.tools.common import run_command   # pylint: disable=import-error
from cai.sdk.agents import function_tool

# Block command chaining / substitution and shell redirection sequences.
# Dangerous sequences include: ;, &&, ||, |, `...`, $(...), newlines, >, <, and backslash
_CMD_INJECT_RE = re.compile(r'(;|&&|\|\||\||`|\$\(|\n|\r|>|<|\\)')

# URL/target safety: no whitespace or shell-special characters allowed in the
# provided URL/host string. Schemes (http://, https://, ftp://) are allowed
# but not required — the agent should provide a safe target.
_URL_SAFE_RE = re.compile(r'^[^\s;|&`$<>()\n\r]+$')


def _validate_wget_input(args: str, url: str):
    """Return an error string if inputs are unsafe, else None."""
    if args and _CMD_INJECT_RE.search(args):
        return (
            f"Invalid args '{args}': command injection sequences (; && || | ` $( > < \\ newline) are not allowed."
        )
    if url and not _URL_SAFE_RE.match(url.strip()):
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
    return run_command(command, timeout=timeout)
