"""
 Here are crypto tools
"""
from cai.tools.common import run_command
from cai.sdk.agents import function_tool



# # URLDecodeTool
# # HexDumpTool
# # Base64DecodeTool
# # ROT13DecodeTool
# # BinaryAnalysisTool

@function_tool
def strings_command(file_path: str, ctf=None) -> str:
    """
    Extract printable strings from a binary file.

#     Args:
#         args: Additional arguments to pass to the strings command
#         file_path: Path to the binary file to extract strings from

#     Returns:
        str: The output of running the strings command
    """
    cmd = ["strings"]
    if file_path:
        cmd.append(file_path)
    return run_command(cmd, ctf=ctf)

@function_tool
def decode64(input_data: str, ctf=None) -> str:  # pylint: disable=unused-argument
    """
    Decode a base64-encoded string.

    Args:
        input_data: The base64-encoded string to decode (not a file path).

    Returns:
        str: The decoded string, or an error message.
    """
    import base64 as _base64
    try:
        decoded = _base64.b64decode(input_data.strip()).decode('utf-8', errors='replace')
        return decoded
    except Exception as e:  # pylint: disable=broad-except
        return f"Error decoding base64: {str(e)}"

@function_tool
def decode_hex_bytes(input_data: str) -> str:
    """
    Decode a string of hex bytes into ASCII text.

    Input Format:
    "0xFF 0x00 0x63..."
    Args:
        input_data: String containing hex bytes

    Returns:
        str: The decoded ASCII text
    """
    try:
        # Split the input string and convert hex strings to bytes
        hex_bytes = [int(x, 16)
                     for x in input_data.split() if x.startswith('0x')]
        # Convert bytes to ASCII string
        decoded = bytes(hex_bytes).decode('ascii')
        return decoded
    except (ValueError, UnicodeDecodeError) as e:
        return f"Error decoding hex bytes: {str(e)}"
