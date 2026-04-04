# FileDownloadTool in exploitFlow

"""
Wget tool
"""
import shlex
from typing import List
from cai.tools.common import run_command   # pylint: disable=import-error
from cai.sdk.agents import function_tool

@function_tool
def wget(url: str, args: str = '', ctf=None) -> str:
    """
    Wget tool to download files from the web.
    Args:
        url: The URL of the file to download
        args: Additional arguments to pass to the wget command

    Returns:
        str: The output of running the wget command
    """
    try:
        args_tokens: List[str] = shlex.split(args) if args else []
    except Exception:
        args_tokens = [args]

    cmd: List[str] = ["wget"] + args_tokens
    if url:
        cmd.append(url)

    return run_command(cmd, ctf=ctf)
