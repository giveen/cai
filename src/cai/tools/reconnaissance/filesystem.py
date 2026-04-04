"""
Here are the CLI tools for executing commands.
"""
import shlex
from typing import List
from cai.tools.common import run_command  # pylint: disable=E0401
from cai.sdk.agents import function_tool

# Dangerous flags that enable RCE, file writes, or file deletion
DANGEROUS_FIND_FLAGS = {
    "-exec", "-execdir", "-ok", "-okdir",
    "-delete",
    "-fprintf", "-fprint", "-fls", "-fprint0",
    "-print0",
}

@function_tool
def list_dir(path: str, args: str = "", ctf=None) -> str:
    """
    List the contents of a directory.
    by def .
    Args:
        path: The directory path to list contents from
        args: Additional arguments to pass to the ls command

    Returns:
        str: The output of running the ls command
    """
    try:
        args_tokens: List[str] = shlex.split(args) if args else []
    except Exception:
        args_tokens = [args]

    cmd: List[str] = ["ls"]
    if path:
        cmd.append(path)
    cmd.extend(args_tokens)

    return run_command(cmd, ctf=ctf)

@function_tool
def cat_file(file_path: str, args: str = "", ctf=None) -> str:
    """
    Display the contents of a file.

    Args:
        args: Additional arguments to pass to the cat command
        file_path: Path to the file to display contents of

    Returns:
        str: The output of running the cat command
    """
    try:
        args_tokens: List[str] = shlex.split(args) if args else []
    except Exception:
        args_tokens = [args]

    cmd: List[str] = ["cat"] + args_tokens
    if file_path:
        cmd.append(file_path)

    return run_command(cmd, ctf=ctf)


# FileSearchTool
# ListDirTool
# TextSearchTool
# FileAnalysisTool
# StringExtractionTool
# ReadFileTool
# FilePermissionsTool
# FileCompressionTool

@function_tool
def pwd_command(ctf=None) -> str:
    """
    Retrieve the current working directory.

    Returns:
        str: The absolute path of the current working directory
    """
    cmd = ["pwd"]
    return run_command(cmd, ctf=ctf)

@function_tool
def find_file(file_path: str, args: str = "", ctf=None) -> str:
    """
    Find a file in the filesystem.
    """
    # Block dangerous flags that enable RCE, file writes, or deletion
    try:
        args_tokens: List[str] = shlex.split(args) if args else []
    except Exception:
        args_tokens = [args]

    for flag in DANGEROUS_FIND_FLAGS:
        if flag in args_tokens:
            return f"Error: DANGEROUS flag '{flag}' is not allowed"

    cmd: List[str] = ["find"]
    if file_path:
        cmd.append(file_path)
    cmd.extend(args_tokens)
    return run_command(cmd, ctf=ctf)
