"""
Here are the curl tools.
"""

from cai.tools.common import run_command  # pylint: disable=import-error
from cai.sdk.agents import function_tool
from cai.tools.validation import validate_args_no_injection, is_url_safe  # pylint: disable=import-error
from cai.tools import validation  # pylint: disable=import-error


def _validate_curl_input(args: str, target: str):
    """Return an error string if inputs are unsafe, else None."""
    err = validate_args_no_injection(args, 'args')
    if err:
        return err
    if target and not is_url_safe(target):
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
    # Global guardrails
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err

    result = run_command(command, timeout=timeout)
    if isinstance(result, str):
        result = validation.sanitize_tool_output(command, result)
    return result
