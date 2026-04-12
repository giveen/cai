"""Headless RDP command executor using aardwolf.

Connects to a Windows target over RDP and executes commands by automating the
following keyboard/clipboard sequence:

    Win+R  →  "cmd"  →  Enter                  ← opens a Command Prompt
    (<command>) | clip  →  Enter                ← runs command, captures stdout
    ← waits for RDP clipboard channel to deliver the output text

Authentication modes:
  • NTLM password  — default, most common
  • Pass-the-Hash  — provide nt_hash instead of password
  • Plain / null   — no-auth or legacy RDP (unsafe_ssl=True implied)

Virtual-hosts integration
--------------------------
Python sockets bypass glibc NSS, so ``LD_PRELOAD=libnss_wrapper.so`` has no
effect inside a Python process.  Instead this module resolves hostnames
*before* the TCP dial by reading the hosts file referenced by
``NSS_WRAPPER_HOSTS`` (the same env-var used by the CAI virtual-hosts feature)
or the workspace ``hosts.txt`` managed by :class:`VirtualHostsManager`.  The
resolved IP is used for the TCP connection while the original hostname is kept
in the RDP credentials (relevant for Kerberos SPN and TLS cert checks).

System requirements:
  pip install aardwolf            (or: pip install "cai-framework[rdp]")

Usage example:
    async with HeadlessRDPExecutor(
        target   = "principal.htb",   # resolved via hosts.txt if needed
        username = "Administrator",
        password = "Passw0rd!",
        domain   = "HTB",
    ) as rdp:
        print(await rdp.execute_command("whoami /all"))
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from typing import Any, Optional

from cai.agents.guardrails import sanitize_external_content as _sanitize
from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)


def _emit(event: dict) -> None:
    """Forward an RDP session event to the TUI WindowsStream widget.

    Uses a lazy import so this module works correctly when the TUI is not
    running — ``emit_rdp_event`` silently discards events in that case.
    """
    try:
        from cai.tui.components.windows_stream import emit_rdp_event  # lazy

        emit_rdp_event(event)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

# Block characters that could break the ``(<cmd>) | clip`` shell quoting.
# The command runs inside a Windows cmd.exe session on the *remote* machine,
# so the concern is accidental syntax breakage, not local shell injection.
# We allow ``|`` only to flag it explicitly with a helpful message.
_SUSPICIOUS_RE = re.compile(r"[\x00-\x1f\x7f]")  # control characters only


def _check_command(command: str) -> Optional[str]:
    """Return an error string if *command* looks problematic, else None."""
    if not (command or "").strip():
        return "command must not be empty"
    if _SUSPICIOUS_RE.search(command):
        return "command contains disallowed control characters"
    return None


# ---------------------------------------------------------------------------
# Virtual-hosts resolution
# ---------------------------------------------------------------------------


def _resolve_host(hostname: str) -> str:
    """Resolve *hostname* to an IP using virtual-hosts files when available.

    Resolution order:
    1. If *hostname* is already a numeric IP, return it as-is.
    2. Check ``NSS_WRAPPER_HOSTS`` environment variable (same file used by
       the CAI nss-wrapper integration).
    3. Fall back to the workspace ``hosts.txt`` managed by
       :class:`~cai.tools.network.dns_proxy.VirtualHostsManager`.
    4. Return the original hostname unchanged so Python's normal DNS stack
       handles it.

    Notes:
        Python's ``socket`` module does not call glibc NSS, so
        ``LD_PRELOAD=libnss_wrapper.so`` never affects Python DNS lookups.
        Reading the hosts file directly is the only reliable approach for
        custom hostnames inside a Python process.
    """
    hostname = (hostname or "").strip()
    if not hostname:
        return hostname

    # Already a routable IP address — nothing to do.
    try:
        ipaddress.ip_address(hostname)
        return hostname
    except ValueError:
        pass

    hosts_file: Optional[str] = None

    # 1. Honour the NSS_WRAPPER_HOSTS env var first.
    nss_file = os.environ.get("NSS_WRAPPER_HOSTS", "").strip()
    if nss_file and os.path.isfile(nss_file):
        hosts_file = nss_file

    # 2. Workspace hosts.txt from the VirtualHostsManager singleton.
    if not hosts_file:
        try:
            from cai.tools.network.dns_proxy import get_vhm  # lazy import

            workspace_file = get_vhm().get_hosts_path()
            if os.path.isfile(workspace_file):
                hosts_file = workspace_file
        except Exception:
            pass

    if hosts_file:
        target_lower = hostname.lower().rstrip(".")
        try:
            with open(hosts_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    ip_str = parts[0]
                    for h in parts[1:]:
                        if h.lower().rstrip(".") == target_lower:
                            logger.debug(
                                "rdp_headless: resolved %s → %s via %s",
                                hostname,
                                ip_str,
                                hosts_file,
                            )
                            return ip_str
        except OSError as exc:
            logger.warning("rdp_headless: could not read hosts file %s: %s", hosts_file, exc)

    return hostname


# ---------------------------------------------------------------------------
# HeadlessRDPExecutor
# ---------------------------------------------------------------------------


class HeadlessRDPExecutor:
    """Async context-manager that runs a command on a Windows target over RDP.

    Args:
        target:   IP address or hostname of the RDP server.  Hostnames are
                  resolved via the virtual-hosts file before connecting.
        username: Windows username (without domain).
        password: Plaintext password.  Ignored when *nt_hash* is supplied.
        domain:   Windows domain name.  Use ``"."`` for a local account.
        nt_hash:  32-character NT hash for Pass-the-Hash auth.  When set,
                  *password* is ignored and ``rdp+ntlm-nt://`` is used.
        port:     TCP port of the RDP service (default: 3389).
        timeout:  Overall connection + auth timeout in seconds (default: 60).

    Usage::

        async with HeadlessRDPExecutor("10.10.11.42", "Administrator", "P@ss") as rdp:
            output = await rdp.execute_command("net user")
    """

    # Pause between individual key-press and key-release events (seconds).
    # Increase on very slow or heavily loaded targets.
    _KEY_DELAY: float = 0.06

    # Time to wait for the Win+R Run dialog to appear after the keystrokes.
    _DIALOG_WAIT: float = 1.5

    # Time to wait for cmd.exe to open after pressing Enter in the Run dialog.
    _CMD_WAIT: float = 2.5

    # Maximum time (seconds) to wait for the RDP clipboard channel to deliver
    # the command output after clip.exe has (presumably) finished running.
    _CLIPBOARD_TIMEOUT: float = 45.0

    def __init__(
        self,
        target: str,
        username: str = "Administrator",
        password: str = "",
        domain: str = ".",
        nt_hash: Optional[str] = None,
        port: int = 3389,
        timeout: float = 60.0,
    ) -> None:
        self._original_target = target
        self._target_ip = _resolve_host(target)
        self._username = username
        self._password = password if not nt_hash else ""
        self._domain = domain or "."
        self._nt_hash = nt_hash
        self._port = port
        self._timeout = timeout

        self._conn: Optional[Any] = None
        self._iosettings: Optional[Any] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _build_url(self) -> str:
        """Construct the aardwolf connection URL from stored credentials."""
        from urllib.parse import quote  # stdlib — always available

        _safe = "!$&'()*+,;=-._~"
        user = quote(self._username or "Administrator", safe=_safe + "@\\")
        domain = self._domain or "."

        if self._nt_hash:
            # Pass-the-Hash: rdp+ntlm-nt://DOMAIN\user:NTHASH@ip:port
            scheme = "rdp+ntlm-nt"
            secret = quote(self._nt_hash.strip(), safe=_safe)
        elif self._password:
            scheme = "rdp+ntlm-password"
            secret = quote(self._password, safe=_safe)
        else:
            # Null / plain session (no CredSSP)
            scheme = "rdp+plain"
            secret = ""

        if secret:
            userinfo = f"{domain}\\{user}:{secret}"
        else:
            userinfo = f"{domain}\\{user}"

        return f"{scheme}://{userinfo}@{self._target_ip}:{self._port}"

    async def connect(self) -> None:
        """Establish the RDP connection and authenticate."""
        try:
            from aardwolf.commons.factory import RDPConnectionFactory  # type: ignore[import-untyped]
            from aardwolf.commons.iosettings import RDPIOSettings  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "aardwolf is not installed.  "
                "Install with:  pip install aardwolf  "
                "or:  pip install \"cai-framework[rdp]\""
            ) from exc

        iosettings = RDPIOSettings()
        # Disable pyperclip integration — we run headless without a local
        # clipboard session; pyperclip would raise on import or at runtime.
        iosettings.clipboard_use_pyperclip = False
        self._iosettings = iosettings

        url = self._build_url()
        logger.debug("rdp_headless: connecting to %s (resolved from %s)", self._target_ip, self._original_target)

        factory = RDPConnectionFactory.from_url(url, iosettings)
        conn = factory.get_connection(iosettings)

        _, err = await asyncio.wait_for(conn.connect(), timeout=self._timeout)
        if err is not None:
            raise ConnectionError(f"RDP connection to {self._target_ip}:{self._port} failed: {err}")

        self._conn = conn
        logger.debug("rdp_headless: connected successfully")
        _emit({
            "type": "connect",
            "target": self._original_target,
            "ip": self._target_ip,
        })

    async def disconnect(self) -> None:
        """Gracefully terminate the RDP connection."""
        if self._conn is not None:
            _emit({"type": "disconnect", "target": self._original_target})
            try:
                await asyncio.wait_for(self._conn.terminate(), timeout=10.0)
            except Exception:
                pass
            self._conn = None

    async def __aenter__(self) -> "HeadlessRDPExecutor":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()

    @property
    def _rdp(self) -> Any:
        """Active aardwolf RDPConnection — raises if not yet connected."""
        if self._conn is None:
            raise RuntimeError(
                "Not connected.  Call connect() or use as an async context manager."
            )
        return self._conn

    # ------------------------------------------------------------------
    # Low-level keyboard helpers
    # ------------------------------------------------------------------

    async def _key_down(self, vk: str) -> None:
        """Press a virtual key and wait _KEY_DELAY seconds."""
        await self._rdp.send_key_virtualkey(vk, True, False)
        await asyncio.sleep(self._KEY_DELAY)

    async def _key_up(self, vk: str) -> None:
        """Release a virtual key and wait _KEY_DELAY seconds."""
        await self._rdp.send_key_virtualkey(vk, False, False)
        await asyncio.sleep(self._KEY_DELAY)

    async def _press_vk(self, vk: str) -> None:
        """Press then release a virtual key."""
        await self._key_down(vk)
        await self._key_up(vk)

    async def _type_char(self, char: str) -> None:
        """Send a single Unicode character using the RDP Unicode keyboard event."""
        code = ord(char)
        await self._rdp.send_key_char(code, True)
        await asyncio.sleep(self._KEY_DELAY)
        await self._rdp.send_key_char(code, False)
        await asyncio.sleep(self._KEY_DELAY)

    async def _type_string(self, text: str) -> None:
        """Send each character in *text* as a Unicode keyboard event pair."""
        for ch in text:
            await self._type_char(ch)

    # ------------------------------------------------------------------
    # Desktop ready detection
    # ------------------------------------------------------------------

    async def _wait_for_desktop(self, timeout: float = 15.0) -> None:
        """Block until at least one video frame has arrived from the server.

        Drains ``ext_out_queue`` so the background reader task does not stall
        while we are waiting.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._rdp.desktop_buffer_has_data:
                return
            # Drain queued video frames so the internal asyncio reader keeps
            # making progress and the desktop_buffer_has_data flag gets set.
            while not self._rdp.ext_out_queue.empty():
                try:
                    self._rdp.ext_out_queue.get_nowait()
                except Exception:
                    break
            await asyncio.sleep(0.25)
        # Proceed anyway after the timeout — the desktop may still respond.
        logger.debug("rdp_headless: desktop was not confirmed ready within %.0f s", timeout)

    # ------------------------------------------------------------------
    # Clipboard helpers
    # ------------------------------------------------------------------

    async def _get_clipboard_text(self) -> str:
        """Return the current remote-side clipboard text, or '' on error."""
        try:
            text = await self._rdp.get_current_clipboard_text()
            return text or ""
        except Exception:
            return ""

    async def _wait_clipboard_change(self, previous: str, timeout: float) -> str:
        """Poll until the remote clipboard contains new text.

        Drains ``ext_out_queue`` on every iteration so the RDP reader coroutine
        can process incoming CLIPRDR PDUs (clipboard-format-list, format-data)
        that aardwolf delivers through the same channel loop.

        Args:
            previous: The clipboard text recorded *before* running the command.
            timeout:  Maximum seconds to wait.

        Returns:
            The new clipboard text.

        Raises:
            TimeoutError: When the clipboard does not change within *timeout*.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Keep the aardwolf reader running by draining its output queue.
            while not self._rdp.ext_out_queue.empty():
                try:
                    self._rdp.ext_out_queue.get_nowait()
                except Exception:
                    break

            current = await self._get_clipboard_text()
            if current and current != previous:
                return current

            await asyncio.sleep(0.35)

        raise TimeoutError(
            f"RDP clipboard did not update within {timeout:.0f} s after running "
            "the command.  Possible causes: command produced no output, "
            "clip.exe is not available, or the cmd window did not open."
        )

    # ------------------------------------------------------------------
    # Main API
    # ------------------------------------------------------------------

    async def execute_command(self, command: str) -> str:
        """Run *command* on the remote Windows host and return its stdout.

        Execution sequence:
          1. Wait for the desktop to be ready.
          2. Press Win+R to open the Run dialog.
          3. Type ``cmd`` and press Enter.
          4. Wait for the Command Prompt to appear.
          5. Type ``(<command>) | clip`` and press Enter — this pipes the
             command's stdout into ``clip.exe``, which sets the Windows
             clipboard.
          6. Wait for the RDP CLIPRDR channel to deliver the clipboard update.
          7. Return the captured text.

        Notes:
            * Stderr is **not** captured.  Append ``2>&1`` in *command* to
              include both stdout and stderr.
            * ``clip.exe`` is included in all modern Windows versions
              (Vista+).  It is absent on Windows Server Core minimal installs.
            * Commands that produce more than ~64 KB of output may be silently
              truncated by the clipboard.  Use ``findstr`` / ``more`` to page
              long output.

        Args:
            command: Windows shell command to execute on the remote host.
                     Supports cmd.exe built-ins and any PATH-available binary.
                     Example: ``"net user /domain"``

        Returns:
            str: Stdout of *command* with trailing newlines stripped.

        Raises:
            RuntimeError: If called before :meth:`connect`.
            TimeoutError: If the clipboard does not update in time.
        """
        if self._conn is None:
            raise RuntimeError(
                "Not connected.  Use HeadlessRDPExecutor as a context manager "
                "or call connect() before execute_command()."
            )

        err = _check_command(command)
        if err:
            _emit({"type": "error", "text": f"Blocked: {err}"})
            return f"[BLOCKED] {err}"

        _emit({"type": "command", "cmd": command})

        # ── Step 1: wait for the remote desktop to display something ────────
        _emit({"type": "status", "text": "waiting for desktop…"})
        await self._wait_for_desktop(timeout=15.0)
        # Extra settle time for taskbar / explorer shell initialisation.
        await asyncio.sleep(2.5)

        # ── Step 2: snapshot current clipboard content ──────────────────────
        previous_clipboard = await self._get_clipboard_text()

        # ── Step 3: Win+R → open Run dialog ─────────────────────────────────
        _emit({"type": "status", "text": "opening Run dialog (Win+R)…"})
        # Hold Win, tap R, release Win.
        await self._key_down("VK_LWIN")
        await self._type_char("r")
        await self._key_up("VK_LWIN")
        await asyncio.sleep(self._DIALOG_WAIT)

        # ── Step 4: type "cmd" + Enter ───────────────────────────────────────
        _emit({"type": "status", "text": "launching cmd.exe…"})
        await self._type_string("cmd")
        await self._press_vk("VK_RETURN")
        await asyncio.sleep(self._CMD_WAIT)

        # ── Step 5: run command and capture output via clip.exe ──────────────
        # Wrap in parentheses so piped / multi-part commands work in cmd.exe.
        # E.g.  (ipconfig /all) | clip   captures the full output correctly.
        _emit({"type": "status", "text": "sending command, waiting for clipboard…"})
        clip_cmd = f"({command}) | clip"
        await self._type_string(clip_cmd)
        await self._press_vk("VK_RETURN")

        # ── Step 6: wait for clipboard to change ─────────────────────────────
        output = await self._wait_clipboard_change(
            previous_clipboard,
            timeout=self._CLIPBOARD_TIMEOUT,
        )

        result = output.rstrip("\r\n")
        _emit({"type": "output", "text": result})
        return result


# ---------------------------------------------------------------------------
# Agent-callable function tool
# ---------------------------------------------------------------------------


@function_tool
async def rdp_exec(
    target: str,
    command: str,
    username: str = "Administrator",
    password: str = "",
    domain: str = ".",
    nt_hash: str = "",
    port: int = 3389,
    timeout: float = 60.0,
) -> str:
    """Execute a command on a Windows host via headless RDP using aardwolf.

    Automates Win+R → cmd → ``(<command>) | clip`` and returns the stdout via
    the RDP clipboard channel.  No GUI is required on either end.

    Virtual-host resolution: when the workspace ``hosts.txt`` (or
    ``NSS_WRAPPER_HOSTS``) maps *target* to an IP, that IP is used for the TCP
    connection — so hostnames like ``principal.htb`` work out-of-the-box
    without ``/etc/hosts`` changes.

    Authentication:
      • Username + password      — most common (CredSSP/NTLM)
      • Pass-the-Hash            — supply *nt_hash*; *password* is ignored
      • No credentials           — leave both password and nt_hash empty

    Limitations:
      • Requires ``aardwolf`` Python package (``pip install aardwolf``).
      • Clip.exe must be available on the remote (all Windows Vista+ desktop/
        server editions except Server Core minimal).
      • Stderr is not captured; append ``2>&1`` in *command* for combined output.
      • Output is limited to ~64 KB by the Windows clipboard.

    Args:
        target:   IP address or hostname of the Windows RDP target.
                  Hostnames are resolved via the workspace virtual-hosts file.
        command:  Windows shell command to run, e.g. ``"whoami /all"`` or
                  ``"net user /domain 2>&1"``.
        username: Windows username (default: ``"Administrator"``).
        password: Plaintext password.  Use *nt_hash* for PTH auth.
        domain:   Domain name.  Use ``"."`` for a local account (default).
        nt_hash:  NT hash for Pass-the-Hash, e.g. ``"aad3b435b51404eeaad3b435b51404ee"``.
                  When non-empty, *password* is ignored.
        port:     RDP port (default: 3389).
        timeout:  Connection + auth timeout in seconds (default: 60).

    Returns:
        str: Command stdout, or an error/diagnostic message.

    Examples:
        rdp_exec("10.10.11.42", "whoami /all")
        rdp_exec("principal.htb", "net user /domain", domain="HTB", username="jsmith", password="P@ss!")
        rdp_exec("10.10.11.42", "dir C:\\\\Users", nt_hash="aad3b435b51404eeaad3b435b51404ee")
    """
    # Input validation
    err = _check_command(command)
    if err:
        return f"[BLOCKED] {err}"

    if not (target or "").strip():
        return "[ERROR] target is required"

    _nt_hash: Optional[str] = nt_hash.strip() if nt_hash else None

    try:
        async with HeadlessRDPExecutor(
            target=target.strip(),
            username=username or "Administrator",
            password=password,
            domain=domain or ".",
            nt_hash=_nt_hash,
            port=port,
            timeout=timeout,
        ) as rdp:
            output = await rdp.execute_command(command)

        # Sanitise the remote output before returning it to the agent to
        # defend against prompt-injection embedded in command output.
        return _sanitize(output) if output else "(no output captured)"

    except RuntimeError as exc:
        # aardwolf not installed
        _emit({"type": "error", "text": str(exc)})
        return f"[ERROR] {exc}"
    except ConnectionError as exc:
        _emit({"type": "error", "text": f"RDP connection failed: {exc}"})
        return f"[ERROR] RDP connection failed: {exc}"
    except TimeoutError as exc:
        _emit({"type": "error", "text": str(exc)})
        return f"[TIMEOUT] {exc}"
    except Exception as exc:
        logger.exception("rdp_exec: unexpected error")
        _emit({"type": "error", "text": str(exc)})
        return f"[ERROR] {exc}"
