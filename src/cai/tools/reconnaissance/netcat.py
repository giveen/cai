"""
 Here are the tools for netcat command
"""
import shlex
import subprocess
from cai.tools.common import run_command   # pylint: disable=import-error
from cai.sdk.agents import function_tool

@function_tool
def netcat(host: str, port: int, data: str = '',
           args: str = '', ctf=None) -> str:
    """
    A simple netcat tool to connect to a specified host and port.
    Args:
        args: Additional arguments to pass to the netcat command
        host: The target host to connect to
        port: The target port to connect to
        data: Data to send to the host (optional)

    Returns:
        str: The output of running the netcat command
         or error message if connection fails
    """
    try:
        if not isinstance(port, int):
            return "Error: Port must be an integer"
        if port < 1 or port > 65535:
            return "Error: Port must be between 1 and 65535"

        if ctf:
            # Remote/CTF context: prefer argument lists when possible.
            try:
                args_tokens = shlex.split(args) if args else []
            except Exception:
                args_tokens = [args]

            # If data needs to be piped, fallback to a shell command string.
            if data:
                extra = ' '.join(shlex.quote(a) for a in args_tokens) if args_tokens else ''
                nc_cmd = f'nc -w 3 {shlex.quote(host)} {port} {extra}'.strip()
                shell_cmd = f'printf "%s\\n" {shlex.quote(data)} | {nc_cmd}'
                return run_command(shell_cmd, ctf=ctf)

            # No piping needed: pass as an argument list
            cmd = ['nc', '-w', '3', host, str(port)]
            if args_tokens:
                cmd.extend(args_tokens)
            return run_command(cmd, ctf=ctf)

        # Local: subprocess with an argument list — no shell, no injection risk.
        # data is fed directly as stdin bytes, sidestepping all quoting issues.
        cmd = ['nc', '-w', '3', host, str(port)]
        if args:
            cmd.extend(shlex.split(args))
        stdin_data = (data + '\n').encode() if data else b''
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            timeout=10,
        )
        output = proc.stdout.decode(errors='replace')
        err = proc.stderr.decode(errors='replace')
        return output or err or ''
    except Exception as e:  # pylint: disable=broad-except
        return f"Error executing netcat command: {str(e)}"
