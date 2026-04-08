"""
Here are the CLI tools for executing commands.
"""
import shlex
from typing import List

from cai.sdk.agents import function_tool
from cai.tools import validation  # pylint: disable=import-error
from cai.tools.common import run_command  # pylint: disable=E0401

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
    command = f'ls {path} {args}'
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err
    return run_command(command, ctf=ctf)

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
    command = f'cat {args} {file_path} '
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err
    return run_command(command, ctf=ctf)


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
    command = 'pwd'
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err
    return run_command(command, ctf=ctf)

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

    command = f'find {file_path} {args}'
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err
    return run_command(command, ctf=ctf)
