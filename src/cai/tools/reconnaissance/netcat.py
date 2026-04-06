"""
 Here are the tools for netcat command
"""
import re
import tempfile
import os

from cai.tools.common import run_command   # pylint: disable=import-error
from cai.sdk.agents import function_tool

# Disallow shell metacharacters to prevent command injection via args/data
_SHELL_METACHAR_RE = re.compile(r'[;&|`$<>()\{\}\[\]\n\r\\]')

# Host/IP/hostname validation (IPv4, IPv6, or hostname)
_HOST_RE = re.compile(
    r'^(?:'
    r'(?:\d{1,3}\.){3}\d{1,3}'                       # IPv4
    r'|[0-9a-fA-F:]+(?:%[0-9a-zA-Z]+)?'                 # IPv6 (basic)
    r'|[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]*[a-zA-Z0-9])?'   # Hostname/FQDN
    r')$'
)

# Disallow potentially dangerous nc flags (execute, listen, etc.)
_DISALLOWED_ARG_FLAGS = re.compile(r'(^|\s)-(?:e|c|l)($|\s)')


def _validate_netcat_input(args: str, host: str, port: int, data: str):
    """Return an error string if inputs are unsafe, else None."""
    # Port checks
    try:
        port_int = int(port)
    except Exception:
        return "Error: Port must be an integer"
    if port_int < 1 or port_int > 65535:
        return "Error: Port must be between 1 and 65535"

    # Host validation
    if not host or not _HOST_RE.match(host.strip()):
        return f"Invalid host '{host}': must be an IP address or hostname"

    # Args should not contain shell metacharacters or disallowed flags
    if args:
        if _SHELL_METACHAR_RE.search(args):
            return f"Invalid args '{args}': shell metacharacters are not allowed"
        if _DISALLOWED_ARG_FLAGS.search(args):
            return f"Invalid args '{args}': flags -e, -c, -l are not allowed"

    # Data length guard
    if data and len(data) > 16_384:
        return "Error: data too large (max 16384 characters)"

    return None


@function_tool
def netcat(host: str, port: int, data: str = '', args: str = '', timeout: int = 10) -> str:
    """
    Connect to a host:port using netcat (nc) with input validation.

    Args:
        host: Target host (IP or hostname).
        port: Target TCP port (1-65535).
        data: Optional string data to send to the socket. Limited in size.
        args: Additional nc flags (restricted). Disallowed: -e -c -l and any shell metacharacters.
        timeout: Maximum seconds to wait for the command (default 10).

    Returns:
        str: The raw output from nc or an error string.

    Notes:
        - This tool writes `data` to a temporary file and feeds it to nc via stdin
          to avoid shell-escaping issues. The temporary file is removed after use.
        - The tool disallows flags that enable remote command execution or listening.
    """
    # Validate inputs
    err = _validate_netcat_input(args, host, port, data)
    if err:
        return err

    host_s = host.strip()
    port_i = int(port)

    tmp_path = None
    try:
        if data:
            # Write payload to a temporary file in a safe manner
            with tempfile.NamedTemporaryFile(delete=False, prefix='cai_nc_', mode='wb') as tf:
                tmp_path = tf.name
                tf.write(data.encode('utf-8', errors='replace'))

            # Use input redirection from the temporary file (tmp_path is safe)
            command = f'nc -w 3 {host_s} {port_i} {args} < {tmp_path}'
        else:
            # No data: ensure nc gets EOF immediately
            command = f'nc -w 3 {host_s} {port_i} {args} < /dev/null'

        result = run_command(command, timeout=timeout)
        return result
    except Exception as e:  # pylint: disable=broad-except
        return f"Error executing netcat command: {str(e)}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
