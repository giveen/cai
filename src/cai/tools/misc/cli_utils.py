"""
CLI utilities module for executing shell commands and processing their output.
"""

from cai.sdk.agents import function_tool
from cai.tools import validation  # pylint: disable=import-error
from cai.tools.common import run_command  # pylint: disable=E0401


@function_tool
def execute_cli_command(command: str) -> str:
    """
    Execute a CLI command and return the output.

    Args:
        command (str): The command to execute.
        Should be concise and focused.

        Avoid overly verbose commands
        with unnecessary flags/options.

    Returns:
        str: Command output, formatted for clarity and readability.
            Long outputs will be truncated or filtered
    """
    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err

    result = run_command(command)
    if isinstance(result, str):
        return validation.sanitize_tool_output(command, result)
    return result
