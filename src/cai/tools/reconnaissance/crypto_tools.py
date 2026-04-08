"""
Crypto-related helper tools (strings, base64, hex decoding).

These tools are implemented with input validation and safe decoding
to avoid shell-injection risks and unbounded resource consumption.
"""
import base64
import re
import shlex

from cai.sdk.agents import function_tool
from cai.tools.common import run_command
from cai.tools.validation import contains_shell_metacharacters  # pylint: disable=import-error


@function_tool
def strings_command(file_path: str, timeout: int = 10) -> str:
    """
    Extract printable strings from a binary file using the system `strings` tool.

    Args:
        file_path: Path to the file to inspect (absolute or relative).
        timeout: Maximum seconds to wait for the command (default 10).

    Returns:
        str: The output of running `strings` on the provided file, or an error string.

    Notes:
        - The `file_path` is validated for shell-special characters and quoted
          with `shlex.quote` before being passed to the shell to avoid injection.
        - We do not attempt to verify the file exists inside a container/CTF;
          presence is checked locally when possible but absence is not considered
          a hard error because the execution environment may differ.
    """
    if not file_path:
        return "Error: file_path is required"

    if contains_shell_metacharacters(file_path):
        return f"Invalid file_path '{file_path}': contains shell-special characters"

    # If file exists locally we allow it; otherwise still allow but quote it
    quoted = shlex.quote(file_path)
    command = f"strings {quoted}"
    return run_command(command, timeout=timeout)


@function_tool
def decode64(input_data: str, max_input: int = 200_000) -> str:
    """
    Decode a base64-encoded string safely (no shell invocation).

    Args:
        input_data: The base64-encoded string to decode.
        max_input: Maximum length of base64 input allowed (default 200k chars).

    Returns:
        str: The decoded text (utf-8) or a binary-safe latin-1 fallback, or an error.
    """
    if not input_data:
        return ""

    # Remove whitespace/newlines which are allowed in some base64 encodings
    s = re.sub(r"\s+", "", input_data)
    if len(s) > max_input:
        return "Error: input too large"

    try:
        decoded = base64.b64decode(s, validate=True)
    except Exception as e:  # binascii.Error or ValueError
        return f"Error decoding base64: {str(e)}"

    try:
        return decoded.decode('utf-8')
    except UnicodeDecodeError:
        # Return a safe binary-to-text mapping so callers can still inspect bytes
        return decoded.decode('latin-1')


@function_tool
def decode_hex_bytes(input_data: str, max_bytes: int = 16_384) -> str:
    """
    Decode a sequence of hex byte tokens into text.

    Accepted formats:
      - "0xFF 0x00 0x63"
      - "FF 00 63"
      - "48656c6c6f" (interpreted as a continuous hex string)

    Args:
        input_data: The input hex string.
        max_bytes: Maximum number of output bytes to produce (default 16k).

    Returns:
        str: The decoded text (utf-8 or latin-1), or an error message.
    """
    if not input_data:
        return ""

    # Split on whitespace or commas
    parts = re.split(r'[\s,]+', input_data.strip())
    bytes_out = []
    try:
        for p in parts:
            if not p:
                continue
            # Remove optional 0x prefix
            token = p[2:] if p.lower().startswith('0x') else p
            # If token looks like a long hex string, consume in pairs
            if len(token) > 2:
                if len(token) % 2 != 0:
                    return f"Error decoding hex bytes: odd-length token '{p}'"
                for i in range(0, len(token), 2):
                    byte = int(token[i:i+2], 16)
                    bytes_out.append(byte)
            else:
                byte = int(token, 16)
                bytes_out.append(byte)

            if len(bytes_out) > max_bytes:
                return f"Error: decoded output too large (>{max_bytes} bytes)"

        decoded = bytes(bytes_out)
        try:
            return decoded.decode('utf-8')
        except UnicodeDecodeError:
            return decoded.decode('latin-1')
    except ValueError as e:
        return f"Error decoding hex bytes: {str(e)}"
