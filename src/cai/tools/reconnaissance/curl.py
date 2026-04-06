"""
Here are the curl tools.
"""

import re

from cai.tools.common import run_command  # pylint: disable=import-error
from cai.sdk.agents import function_tool

# Block command chaining / substitution sequences.
# Note: we deliberately allow {} [] () so JSON bodies like -d '{"key":"val"}'
# and glob-style patterns remain usable. The dangerous sequences are:
#   ;     — command separator
#   &&    — conditional chaining
#   ||    — conditional chaining
#   |     — pipe (could chain to shell commands)
#   `…`   — backtick command substitution
#   $(…)  — dollar-paren command substitution
#   \n\r  — newline injection (can smuggle extra shell commands / HTTP headers)
_CMD_INJECT_RE = re.compile(r'(;|&&|\|\||\||`|\$\(|\n|\r)')

# Valid URL target: must start with a recognised scheme (http/https/ftp/ftps),
# or look like a bare host/IP that curl will default to http://.
# Either way it must NOT contain whitespace or shell injection characters.
_URL_SAFE_RE = re.compile(r'^[^\s;|&`$<>()\n\r]+$')
_URL_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://')


def _validate_curl_input(args: str, target: str):
    """Return an error string if inputs are unsafe, else None."""
    if _CMD_INJECT_RE.search(args):
        return (
            f"Invalid args '{args}': command injection sequences "
            "(; && || | ` $( newline) are not allowed."
        )
    if target:
        if not _URL_SAFE_RE.match(target):
            return (
                f"Invalid target '{target}': must be a URL or hostname "
                "without whitespace or shell-special characters."
            )
    return None


@function_tool
def curl(target: str, args: str = "", timeout: int = 30) -> str:
    """
    Make an HTTP/HTTPS/FTP request to a target using curl.

    Args:
        target:  The URL or host to request.
                 Examples: "http://example.com", "https://api.target.com/v1/users",
                           "http://192.168.1.1:8080/admin", "ftp://files.target.com"
        args:    Additional curl flags. Common examples:
                   "-L"                          — follow redirects
                   "-k"                          — ignore TLS certificate errors
                   "-v"                          — verbose (shows headers/handshake)
                   "-I"                          — HEAD request (headers only)
                   "-X POST -d 'body'"           — POST with body
                   "-X POST -d '{\"k\":\"v\"}' -H 'Content-Type: application/json'"
                   "-H 'Authorization: Bearer TOKEN'"  — custom header
                   "-H 'Cookie: session=abc'"    — send cookie
                   "-u user:pass"                — HTTP basic auth
                   "-o /tmp/file.bin"            — save response to file
                   "-s"                          — silent (no progress output)
                   "-D /tmp/headers.txt"         — dump response headers to file
                   "--max-time 10"               — per-request timeout in seconds
                   "-x http://proxy:8080"        — use HTTP proxy (e.g. Burp)
        timeout: Maximum seconds to wait for the command (default 30).

    Returns:
        str: The raw curl output (response body and/or headers).

    Examples:
        curl(target="http://192.168.1.1")
        curl(target="https://target.com/login", args="-k -v")
        curl(target="http://api.target.com/users", args="-H 'Authorization: Bearer abc123'")
        curl(target="http://target.com/login", args="-X POST -d 'user=admin&pass=secret'")
        curl(target="http://target.com/api", args="-X POST -H 'Content-Type: application/json' -d '{\"cmd\":\"id\"}'")
        curl(target="http://target.com/file.zip", args="-L -o /tmp/file.zip", timeout=120)
    """
    err = _validate_curl_input(args, target)
    if err:
        return err

    command = f'curl {args} {target.strip()}'
    return run_command(command, timeout=timeout)
