"""Safe `smbclient` wrapper helpers for data exfiltration tooling.

This module provides small helpers to invoke the `smbclient` CLI while
avoiding shell interpolation of credentials. Prefer passing arguments as
an argument list to `subprocess.run` (i.e. never use `shell=True`) so
passwords containing characters like `!@#$%^&*()` are safe.

It also offers a convenience to write a temporary Samba authentication
file (used with `-A`) when callers prefer not to embed credentials in
the `-U user%password` argument.

Note: callers should ensure CAI guardrails are applied at a higher
level; this module calls into `cai.tools.validation` for light validation
and to sanitize returned output.
"""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile

from cai.tools import validation  # pylint: disable=import-error


def _write_auth_file(username: str, password: str, domain: str | None = None) -> str:
    """Write a temporary Samba authentication file and return its path.

    The file is created with restrictive permissions (0o600). The caller
    is responsible for removing the file after use.
    """
    tf = tempfile.NamedTemporaryFile(prefix="smb_auth_", delete=False, mode="w", encoding="utf-8")
    try:
        tf.write(f"username = {username}\n")
        tf.write(f"password = {password}\n")
        if domain:
            tf.write(f"domain = {domain}\n")
        tf.flush()
        tf_name = tf.name
    finally:
        tf.close()

    # Restrict file permissions
    try:
        os.chmod(tf_name, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        # Best-effort; failure to chmod is non-fatal here.
        pass
    return tf_name


def _validate_host_and_share(host: str, share: str) -> str | None:
    if not host:
        return "Invalid host: required"
    if not validation.is_valid_host(host):
        return f"Invalid host '{host}'"
    if not share:
        return "Invalid share: required"
    # simple sanity: no whitespace/newlines in share name
    if any(c.isspace() for c in share):
        return f"Invalid share '{share}': contains whitespace"
    if validation.contains_shell_metacharacters(share):
        return f"Invalid share '{share}': contains shell metacharacters"
    return None


async def run_smbclient(
    host: str,
    share: str,
    username: str | None = None,
    password: str | None = None,
    domain: str | None = None,
    port: int = 445,
    commands: str | list[str] | None = None,
    use_auth_file: bool = False,
    extra_args: list[str] | None = None,
    timeout: int = 60,
) -> str:
    """Run `smbclient` against a host/share and return combined stdout/stderr.

    - `commands` can be a single string (passed to `-c`) or a list of
      sambaclient commands (joined with `; `).
    - `use_auth_file` will write a temporary credentials file and pass
      it with `-A` instead of using `-U user%password`.

    This function constructs an argv list and calls `subprocess.run`
    with `shell=False`, which prevents shell interpretation of special
    characters in the password. It still validates obvious host/share
    formatting and runs the project's `validation.validate_command_guardrails`
    against the final command string for an extra safety check.
    """
    err = _validate_host_and_share(host, share)
    if err:
        return err

    if password is not None and ("\n" in password or "\r" in password or "\x00" in password):
        return "Invalid password: contains invalid newline/null characters"

    # Build the base argv
    service = f"//{host}/{share}"
    argv: list[str] = ["smbclient", service, "-p", str(port)]

    authfile_path: str | None = None
    try:
        if username:
            if use_auth_file:
                authfile_path = _write_auth_file(username, password or "", domain=domain)
                argv.extend(["-A", authfile_path])
            else:
                # Use -U user%password form; pass as a single argv entry so
                # subprocess won't perform shell interpolation.
                up = f"{username}%{password or ''}"
                argv.extend(["-U", up])
        else:
            # No username: explicitly request no-pass
            argv.append("-N")

        if extra_args:
            argv.extend(extra_args)

        if commands:
            if isinstance(commands, list):
                cmd_str = "; ".join(commands)
            else:
                cmd_str = commands
            argv.extend(["-c", cmd_str])
        else:
            cmd_str = ""

        # Guardrails: join a human-readable command string for validation
        cmd_for_validate = " ".join(argv)
        g_err = validation.validate_command_guardrails(cmd_for_validate)
        if g_err:
            return g_err

        try:
            # Use asyncio subprocess to avoid blocking the event loop
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return "Error: smbclient not found on PATH"
        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            return "Error: smbclient timed out"

        out = (stdout_data.decode("utf-8", errors="replace") if stdout_data else "") + (
            stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        )
        return validation.sanitize_tool_output(cmd_for_validate, out)
    finally:
        if authfile_path:
            try:
                os.remove(authfile_path)
            except Exception:
                pass


async def list_shares(
    host: str,
    username: str | None = None,
    password: str | None = None,
    domain: str | None = None,
    port: int = 445,
    use_auth_file: bool = False,
    timeout: int = 30,
) -> str:
    """List available shares on `host` (wrapper for `smbclient -L`)."""
    if not host:
        return "Invalid host: required"
    if not validation.is_valid_host(host):
        return f"Invalid host '{host}'"

    argv: list[str] = ["smbclient", "-L", host, "-p", str(port)]
    authfile_path: str | None = None
    try:
        if username:
            if use_auth_file:
                authfile_path = _write_auth_file(username, password or "", domain=domain)
                argv.extend(["-A", authfile_path])
            else:
                argv.extend(["-U", f"{username}%{password or ''}"])
        else:
            argv.append("-N")

        cmd_for_validate = " ".join(argv)
        g_err = validation.validate_command_guardrails(cmd_for_validate)
        if g_err:
            return g_err

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return "Error: smbclient not found on PATH"

        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            return "Error: smbclient timed out"

        out = (stdout_data.decode("utf-8", errors="replace") if stdout_data else "") + (
            stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        )
        return validation.sanitize_tool_output(cmd_for_validate, out)
    finally:
        if authfile_path:
            try:
                os.remove(authfile_path)
            except Exception:
                pass


async def download_file(
    host: str,
    share: str,
    remote_path: str,
    local_path: str,
    username: str | None = None,
    password: str | None = None,
    domain: str | None = None,
    port: int = 445,
    use_auth_file: bool = False,
    timeout: int = 120,
) -> str:
    """Download a single file from `//host/share` to `local_path` via smbclient.

    This uses `get` inside the smbclient `-c` command. Both paths are
    passed as string arguments inside the `-c` string; callers should
    avoid using user-controlled strings here unless previously validated.
    """
    # Basic validation on inputs
    hs_err = _validate_host_and_share(host, share)
    if hs_err:
        return hs_err

    if not remote_path or not local_path:
        return "Invalid remote_path/local_path: required"

    # Quote the paths inside the smbclient `-c` string to preserve spaces
    cmd = f'get "{remote_path}" "{local_path}"'
    return await run_smbclient(
        host,
        share,
        username=username,
        password=password,
        domain=domain,
        port=port,
        commands=cmd,
        use_auth_file=use_auth_file,
        timeout=timeout,
    )


__all__ = ["run_smbclient", "list_shares", "download_file"]
