"""VPN management for CAI — thin wrapper around the openvpn binary.

VPNManager handles the full lifecycle of an OpenVPN tunnel:
  - Validates .ovpn config files before connecting.
  - Launches openvpn as a daemon subprocess; streams its log in the background.
  - Writes a temporary credentials file when the config requires auth-user-pass
    and cleans it up on disconnect.
  - Queries tun0 interface state (via ioctl / ip addr) to report the live IP.
  - Appends the VPN IP to the workspace intelligence journal once connected.
  - Checks for CAP_NET_ADMIN / root before attempting anything privileged.

Typical use:
    mgr = get_manager()
    ok, err = mgr.load_config("/workspace/vpn_configs/lab.ovpn")
    if mgr.needs_auth():
        ok, err = mgr.connect(auth_creds=("user", "pass"))
    else:
        ok, err = mgr.connect()
    status = mgr.get_status()   # VpnStatus.CONNECTED once tun0 is up
    mgr.disconnect()
"""

from __future__ import annotations

import enum
import fcntl
import logging
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# ── FactManager — live key/value facts exposed as env vars ────────────────────

class FactManager:
    """Thread-safe registry for live tactical facts.

    Facts are stored in memory *and* written to ``os.environ`` under the
    ``CAI_FACT_`` prefix so that all spawned tools (nmap, hydra, …) can
    inherit them without any extra plumbing.  For example:

        get_fact_manager().update_fact("local_vpn_ip", "10.10.10.5")
        # → os.environ["CAI_FACT_LOCAL_VPN_IP"] = "10.10.10.5"
    """

    _ENV_PREFIX = "CAI_FACT_"

    def __init__(self) -> None:
        self._facts: dict[str, str] = {}
        self._lock = threading.Lock()

    # accept both snake_case and dot-path keys
    @staticmethod
    def _env_key(key: str) -> str:
        return FactManager._ENV_PREFIX + key.upper().replace(".", "_")

    def update_fact(self, key: str, value: str) -> None:
        """Store *key=value* and export it to the process environment."""
        with self._lock:
            self._facts[key] = value
        env_key = self._env_key(key)
        try:
            os.environ[env_key] = str(value)
        except Exception:
            pass
        logger.debug("[facts] %s = %s", env_key, value)

    def clear_fact(self, key: str) -> None:
        """Remove *key* from the fact store and from the process environment."""
        with self._lock:
            self._facts.pop(key, None)
        try:
            os.environ.pop(self._env_key(key), None)
        except Exception:
            pass

    def get_fact(self, key: str) -> Optional[str]:
        """Return the current value for *key*, or ``None``."""
        with self._lock:
            return self._facts.get(key)

    def all_facts(self) -> dict[str, str]:
        """Return a snapshot copy of the current fact dict."""
        with self._lock:
            return dict(self._facts)


_fact_manager: Optional[FactManager] = None
_fact_manager_lock = threading.Lock()


def get_fact_manager() -> FactManager:
    """Return (or lazily create) the process-wide FactManager singleton."""
    global _fact_manager
    with _fact_manager_lock:
        if _fact_manager is None:
            _fact_manager = FactManager()
        return _fact_manager


class VpnStatus(enum.Enum):
    OFF = "off"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional["VPNManager"] = None
_instance_lock = threading.Lock()


def get_manager() -> "VPNManager":
    """Return the process-wide VPNManager singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = VPNManager()
        return _instance


# ── Main class ────────────────────────────────────────────────────────────────

class VPNManager:
    """Manages a single OpenVPN connection lifecycle."""

    def __init__(self) -> None:
        self._config_path: Optional[Path] = None
        self._proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._log_path: Optional[str] = None
        self._log_lines: list[str] = []
        self._cred_file: Optional[str] = None
        self._status: VpnStatus = VpnStatus.OFF
        self._status_lock = threading.Lock()
        # Cached IP to avoid repeated subprocess calls on the main thread
        self._cached_vpn_ip: Optional[str] = None
        self._cached_vpn_ip_ts: float = 0.0

    # ── Privilege check ───────────────────────────────────────────────────

    def has_privilege(self) -> bool:
        """Return True if running as root or with CAP_NET_ADMIN (bit 12)."""
        if os.geteuid() == 0:
            return True
        try:
            with open("/proc/self/status", encoding="ascii") as f:
                for line in f:
                    if line.startswith("CapEff:"):
                        cap_eff = int(line.split(":")[1].strip(), 16)
                        return bool(cap_eff & (1 << 12))  # CAP_NET_ADMIN
        except Exception:
            pass
        return False

    # ── Config loading ────────────────────────────────────────────────────

    def load_config(self, path: str) -> Tuple[bool, str]:
        """Validate an .ovpn / .conf file and register it as the active config.

        Returns:
            (True, "")  on success
            (False, "<reason>")  on failure
        """
        p = Path(path)
        if not p.exists():
            return False, f"File not found: {path}"
        if not p.is_file():
            return False, f"Not a regular file: {path}"
        if p.suffix.lower() not in (".ovpn", ".conf"):
            return False, f"Expected .ovpn or .conf extension, got: {p.suffix}"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return False, f"Cannot read file: {exc}"
        if "remote " not in text and "client" not in text:
            return False, "File does not appear to be a valid OpenVPN config (no 'remote' or 'client' directive)"
        self._config_path = p
        return True, ""

    def needs_auth(self) -> bool:
        """Return True if the config contains a bare auth-user-pass directive."""
        if not self._config_path:
            return False
        try:
            text = self._config_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        # Bare directive (no filename argument on the same line)
        return bool(re.search(r"^\s*auth-user-pass\s*$", text, re.MULTILINE))

    # ── Connection ────────────────────────────────────────────────────────

    def connect(self, auth_creds: Optional[Tuple[str, str]] = None) -> Tuple[bool, str]:
        """Launch OpenVPN in daemon mode.

        Args:
            auth_creds: Optional ``(username, password)`` for configs that need
                ``auth-user-pass``.  A temporary 0600 credentials file is created
                and cleaned up on disconnect.

        Returns:
            (True, "")  if the process started without OS-level errors.
            (False, "<reason>")  on failure.  Check ``get_status()`` and
            ``get_log_tail()`` for the actual connection outcome.
        """
        if not self._config_path:
            return False, "No config loaded. Call load_config() first."
        if not self.has_privilege():
            return False, "CAP_NET_ADMIN or root privileges are required to start OpenVPN."
        if self._proc and self._proc.poll() is None:
            return False, "OpenVPN is already running. Call disconnect() first."

        cmd = [
            "openvpn",
            "--config", str(self._config_path),
            "--dev", "tun",
            "--proto", "udp",
            "--script-security", "2",
            "--daemon", "cai-vpn",
        ]

        # Write temporary credentials file if caller provided them
        if auth_creds:
            username, password = auth_creds
            try:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".cred", delete=False, prefix="cai_vpn_"
                )
                tmp.write(f"{username}\n{password}\n")
                tmp.close()
                os.chmod(tmp.name, 0o600)
                self._cred_file = tmp.name
                cmd += ["--auth-user-pass", tmp.name]
            except Exception as exc:
                return False, f"Failed to write credentials file: {exc}"

        # Redirect openvpn log output to a temp file we can tail
        try:
            log_fd, log_path = tempfile.mkstemp(suffix=".log", prefix="cai_vpn_")
            os.close(log_fd)
        except Exception as exc:
            return False, f"Failed to create log file: {exc}"
        self._log_path = log_path
        self._log_lines = []
        cmd += ["--log", log_path]

        with self._status_lock:
            self._status = VpnStatus.CONNECTING

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            with self._status_lock:
                self._status = VpnStatus.ERROR
            return False, "openvpn binary not found. Install openvpn (apt install openvpn)."
        except PermissionError as exc:
            with self._status_lock:
                self._status = VpnStatus.ERROR
            return False, f"Permission denied starting openvpn: {exc}"
        except Exception as exc:
            with self._status_lock:
                self._status = VpnStatus.ERROR
            return False, f"Failed to launch openvpn: {exc}"

        threading.Thread(
            target=self._log_watcher,
            args=(log_path,),
            daemon=True,
            name="cai-vpn-log-watcher",
        ).start()

        return True, ""

    def _log_watcher(self, log_path: str) -> None:
        """Background thread: tail the openvpn log and update _status."""
        import time

        deadline = time.time() + 90  # wait up to 90 s for tunnel to come up
        seen = 0
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                new = lines[seen:]
                seen = len(lines)
                for raw in new:
                    line = raw.rstrip()
                    self._log_lines.append(line)
                    if len(self._log_lines) > 200:
                        self._log_lines = self._log_lines[-200:]
                    logger.debug("[vpn] %s", line)

                    if "Initialization Sequence Completed" in line:
                        with self._status_lock:
                            self._status = VpnStatus.CONNECTED
                        # Refresh IP cache immediately so tools see the new address
                        ip = self.get_vpn_ip()
                        # Publish to FactManager so all child processes inherit it
                        if ip:
                            try:
                                get_fact_manager().update_fact("local_vpn_ip", ip)
                            except Exception:
                                pass
                        # Register VPN logs as a pinned VCM page so they aren't evicted
                        try:
                            from cai.memory.paging import register_vpn_log_page
                            register_vpn_log_page("vpn_logs", "\n".join(self._log_lines))
                        except Exception:
                            pass
                        # Update intelligence journal with new VPN IP
                        try:
                            self.update_intelligence()
                        except Exception:
                            pass
                        return

                    _error_signals = (
                        "AUTH_FAILED",
                        "Connection refused",
                        "Address already in use",
                        "TLS Error",
                        "SIGUSR1",
                        "Fatal TLS error",
                    )
                    if any(sig in line for sig in _error_signals):
                        with self._status_lock:
                            self._status = VpnStatus.ERROR
                        return
            except Exception:
                pass

            if self._proc and self._proc.poll() is not None:
                with self._status_lock:
                    if self._status == VpnStatus.CONNECTING:
                        self._status = VpnStatus.ERROR
                return

        # Timed out waiting for connection
        with self._status_lock:
            if self._status == VpnStatus.CONNECTING:
                self._status = VpnStatus.ERROR

    # ── Disconnect ────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        """Stop the running OpenVPN process and clean up all temporary files."""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

        if self._cred_file:
            try:
                os.unlink(self._cred_file)
            except Exception:
                pass
            self._cred_file = None

        if self._log_path:
            try:
                os.unlink(self._log_path)
            except Exception:
                pass
            self._log_path = None

        # Clear the live fact so tools no longer see the stale VPN IP
        try:
            get_fact_manager().clear_fact("local_vpn_ip")
        except Exception:
            pass
        # Invalidate cache
        self._cached_vpn_ip = None
        self._cached_vpn_ip_ts = 0.0

        with self._status_lock:
            self._status = VpnStatus.OFF

    # ── Status / IP ───────────────────────────────────────────────────────

    def get_status(self) -> VpnStatus:
        """Return the current VPN status, cross-checking with the tun0 interface."""
        if self.get_vpn_ip():
            with self._status_lock:
                self._status = VpnStatus.CONNECTED
            return VpnStatus.CONNECTED
        if self._proc and self._proc.poll() is None:
            # Process alive but tun0 not up yet → return last reported status
            with self._status_lock:
                return self._status
        with self._status_lock:
            return self._status

    def get_vpn_ip(self) -> Optional[str]:
        """Return the IPv4 address on tun0, or None if the interface is absent.

        Uses a two-tier approach to keep the main TUI thread non-blocking:
          1. Fast ioctl (microseconds) — always attempted first.
          2. Subprocess ``ip addr`` — at most once every 15 s via cache.
        This prevents the 3-second TUI poll from ever blocking on a 2 s
        subprocess call during an intense scan.
        """
        # Fast path: ioctl SIOCGIFADDR (non-blocking kernel call)
        SIOCGIFADDR = 0x8915
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                ifreq = struct.pack("16s24x", b"tun0")
                res = fcntl.ioctl(s.fileno(), SIOCGIFADDR, ifreq)
                ip = socket.inet_ntoa(res[20:24])
                self._cached_vpn_ip = ip
                self._cached_vpn_ip_ts = time.monotonic()
                return ip
        except OSError:
            pass

        # Return cached value if it is still fresh (avoids subprocess on main thread)
        now = time.monotonic()
        if self._cached_vpn_ip and now - self._cached_vpn_ip_ts < 15.0:
            return self._cached_vpn_ip

        # Slow path: subprocess ``ip addr`` — stale cache or first miss
        try:
            out = subprocess.check_output(
                ["ip", "-4", "addr", "show", "dev", "tun0"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode(errors="replace")
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
            if m:
                ip = m.group(1)
                self._cached_vpn_ip = ip
                self._cached_vpn_ip_ts = time.monotonic()
                return ip
        except Exception:
            pass
        # Interface gone — clear cache
        self._cached_vpn_ip = None
        return None

    def get_log_tail(self, n: int = 5) -> list[str]:
        """Return the last *n* lines of the OpenVPN log."""
        return list(self._log_lines[-n:]) if self._log_lines else []

    # ── Intelligence integration ──────────────────────────────────────────

    def update_intelligence(self) -> None:
        """Append the active VPN IP to the workspace intelligence journal.

        Called automatically once the tunnel has been confirmed up.
        Safe to call multiple times (adds a new timestamped entry each time).
        """
        ip = self.get_vpn_ip()
        if not ip:
            return
        try:
            from cai.orchestration.persistence import (
                _read_journal,
                _write_journal_atomic,
            )
            from cai.tools.common import _get_workspace_dir

            ws = _get_workspace_dir()
            journal = _read_journal(ws)
            entry = {
                "id": uuid.uuid4().hex,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "category": "network",
                "source": "vpn_manager",
                "source_tool": "vpn_manager",
                "confidence_score": 1.0,
                "fact": {
                    "type": "vpn_connected",
                    "interface": "tun0",
                    "local_vpn_ip": ip,
                    # config_path is stored so workspace resume can offer to reconnect
                    "config_path": str(self._config_path) if self._config_path else None,
                    "note": (
                        "VPN tunnel is active.  All tools (nmap, curl, etc.) "
                        "should route through this IP for local network awareness."
                    ),
                },
            }
            journal.setdefault("entries", []).append(entry)
            journal.setdefault("meta", {})["updated_at"] = datetime.utcnow().isoformat() + "Z"
            _write_journal_atomic(journal, ws)
            logger.info("[vpn] Intelligence journal updated with VPN IP %s", ip)
        except Exception as exc:
            logger.debug("[vpn] intelligence update failed: %s", exc)
