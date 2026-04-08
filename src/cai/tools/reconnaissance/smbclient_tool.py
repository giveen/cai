"""Function-tool wrappers exposing safe smbclient helpers to agents.

This module wraps the implementations in
`cai.tools.reconnaissance.data_exfiltration.smbclient` and exposes
three function tools backed by the same safe helpers (guardrails,
temporary auth file support, etc.).
"""
from __future__ import annotations

from typing import List, Optional

from cai.sdk.agents import function_tool
from cai.tools.reconnaissance.data_exfiltration import smbclient as _smb_impl


async def _list_shares_impl(
    host: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    domain: Optional[str] = None,
    port: int = 445,
    use_auth_file: bool = False,
    timeout: int = 30,
) -> str:
    """List SMB shares on a host.

    This is a thin wrapper around `cai.tools.reconnaissance.data_exfiltration.smbclient.list_shares`.
    """
    return await _smb_impl.list_shares(
        host=host,
        username=username,
        password=password,
        domain=domain,
        port=port,
        use_auth_file=use_auth_file,
        timeout=timeout,
    )


async def _run_smbclient_impl(
    host: str,
    share: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    domain: Optional[str] = None,
    port: int = 445,
    commands: Optional[str] = None,
    use_auth_file: bool = False,
    extra_args: Optional[List[str]] = None,
    timeout: int = 60,
) -> str:
    """Run `smbclient` commands against a host/share.

    - `commands` should be a single string (or None to open an interactive session).
    - `extra_args` can be used to pass additional safe flags.
    """
    return await _smb_impl.run_smbclient(
        host=host,
        share=share,
        username=username,
        password=password,
        domain=domain,
        port=port,
        commands=commands,
        use_auth_file=use_auth_file,
        extra_args=extra_args,
        timeout=timeout,
    )


async def _download_file_impl(
    host: str,
    share: str,
    remote_path: str,
    local_path: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    domain: Optional[str] = None,
    port: int = 445,
    use_auth_file: bool = False,
    timeout: int = 120,
) -> str:
    """Download a single file from an SMB share to a local path.

    This uses the existing safe `download_file` helper.
    """
    return await _smb_impl.download_file(
        host=host,
        share=share,
        remote_path=remote_path,
        local_path=local_path,
        username=username,
        password=password,
        domain=domain,
        port=port,
        use_auth_file=use_auth_file,
        timeout=timeout,
    )


# Create FunctionTool objects that the agents infrastructure expects.
smb_list_shares = function_tool(_list_shares_impl)
smb_run_smbclient = function_tool(_run_smbclient_impl)
smb_download_file = function_tool(_download_file_impl)

__all__ = ["smb_list_shares", "smb_run_smbclient", "smb_download_file"]
