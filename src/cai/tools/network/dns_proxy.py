"""
Virtual Hosts Manager for CAI — nss-wrapper integration.

Uses libnss_wrapper.so (LD_PRELOAD) to make external binaries (nmap, curl,
hydra, …) resolve custom hostnames without touching /etc/hosts.  This is
the most reliable approach for CTF scenarios where the agent discovers new
virtual-host names and must immediately scan them.

System dependency
-----------------
libnss-wrapper must be installed on the host:

    sudo apt install libnss-wrapper -y          # Debian / Ubuntu
    sudo dnf install nss-wrapper -y             # Fedora / RHEL
    sudo pacman -S nss-wrapper                  # Arch

CAI_VHOST_WRAP=true (default) enables automatic wrapping of network tools.
CAI_VHOST_WRAP=false disables wrapping entirely (useful in containers that
already have custom /etc/hosts).

NSS_WRAPPER_HOSTNAME defaults to "cai-node" and can be overridden via the
CAI_VHOST_HOSTNAME environment variable.
"""

from __future__ import annotations

import ctypes.util
import ipaddress
import logging
import os
import re
import shutil

from cai.sdk.agents import function_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Hostname regex: RFC 952 / RFC 1123 plus wildcard sub-parts used in CTFs.
_HOSTNAME_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.?$"
)

# Tools that should receive the nss-wrapper prefix when present in PATH.
_NETWORK_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "nmap",
        "masscan",
        "rustscan",
        "curl",
        "wget",
        "hydra",
        "medusa",
        "nikto",
        "gobuster",
        "ffuf",
        "wfuzz",
        "feroxbuster",
        "whatweb",
        "sqlmap",
        "nuclei",
        "netcat",
        "nc",
        "smbclient",
        "impacket-smbclient",
        "crackmapexec",
        "nxc",
        "ldapsearch",
        "dnsenum",
    }
)


def _find_libnss_wrapper() -> str | None:
    """Return the path to libnss_wrapper.so, or None if not installed."""
    # ctypes.util.find_library looks in ld.so cache, LIBPATH …
    found = ctypes.util.find_library("nss_wrapper")
    if found:
        return found
    # Fallback: common explicit paths on Debian / Ubuntu systems
    for candidate in (
        "/usr/lib/x86_64-linux-gnu/libnss_wrapper.so",
        "/usr/lib/aarch64-linux-gnu/libnss_wrapper.so",
        "/usr/lib/libnss_wrapper.so",
        "/usr/local/lib/libnss_wrapper.so",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def _validate_hostname(hostname: str) -> str | None:
    """Return None if ok, or an error string."""
    h = (hostname or "").strip().rstrip(".")
    if not h:
        return "hostname is required"
    if len(h) > 253:
        return f"hostname too long ({len(h)} > 253 chars)"
    if not _HOSTNAME_RE.match(h + "."):
        return f"invalid hostname '{h}': only alphanumeric, hyphens, and dots allowed"
    return None


def _validate_ip(ip: str) -> str | None:
    """Return None if ok, or an error string."""
    try:
        ipaddress.ip_address((ip or "").strip())
        return None
    except ValueError:
        return f"invalid IP address '{ip}'"


# ---------------------------------------------------------------------------
# VirtualHostsManager
# ---------------------------------------------------------------------------


class VirtualHostsManager:
    """Manage a workspace-local hosts file and generate nss-wrapper prefixes.

    A single process-wide instance is available via :func:`get_vhm`.
    """

    def __init__(self, workspace_dir: str | None = None) -> None:
        self._workspace_dir = workspace_dir  # None means "resolve lazily"

    # --- Paths ---------------------------------------------------------------

    def _get_workspace(self) -> str:
        if self._workspace_dir:
            return self._workspace_dir
        try:
            from cai.tools.common import _get_workspace_dir

            return _get_workspace_dir()
        except Exception:
            return os.getcwd()

    def get_hosts_path(self) -> str:
        """Absolute path to the workspace hosts.txt file."""
        return os.path.join(self._get_workspace(), "hosts.txt")

    # --- Availability --------------------------------------------------------

    def is_available(self) -> bool:
        """Return True when libnss_wrapper.so is present on the system."""
        return _find_libnss_wrapper() is not None

    # --- Host file management ------------------------------------------------

    def _read_entries(self) -> list[tuple[str, str]]:
        """Read (ip, hostname) pairs from hosts.txt."""
        path = self.get_hosts_path()
        if not os.path.exists(path):
            return []
        entries: list[tuple[str, str]] = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        ip = parts[0]
                        for hostname in parts[1:]:
                            entries.append((ip, hostname))
        except OSError as exc:
            logger.warning("VirtualHostsManager: cannot read %s: %s", path, exc)
        return entries

    def _write_entries(self, entries: list[tuple[str, str]]) -> None:
        """Write (ip, hostname) pairs to hosts.txt (compact, one entry per line)."""
        path = self.get_hosts_path()
        # Group by IP to produce compact lines
        ip_map: dict[str, list[str]] = {}
        for ip, hostname in entries:
            ip_map.setdefault(ip, [])
            if hostname not in ip_map[ip]:
                ip_map[ip].append(hostname)
        lines = ["# CAI virtual hosts — managed automatically\n"]
        for ip, hostnames in ip_map.items():
            lines.append(f"{ip}\t{chr(9).join(hostnames)}\n")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
        except OSError as exc:
            logger.error("VirtualHostsManager: cannot write %s: %s", path, exc)
            raise

    def add_host(self, hostname: str, ip: str) -> str:
        """Add or update a hostname → IP mapping.

        Returns a human-readable result string.
        """
        err = _validate_hostname(hostname)
        if err:
            return f"Error: {err}"
        err = _validate_ip(ip)
        if err:
            return f"Error: {err}"

        hostname = hostname.strip().lower().rstrip(".")
        ip = ip.strip()

        entries = self._read_entries()
        # Remove any stale entry for this hostname, then append fresh one.
        entries = [(i, h) for (i, h) in entries if h.lower() != hostname]
        entries.append((ip, hostname))
        self._write_entries(entries)
        logger.info("VirtualHostsManager: added %s → %s", hostname, ip)
        return f"Added: {hostname} → {ip} (hosts file: {self.get_hosts_path()})"

    def remove_host(self, hostname: str) -> str:
        """Remove a hostname mapping.

        Returns a human-readable result string.
        """
        err = _validate_hostname(hostname)
        if err:
            return f"Error: {err}"
        hostname = hostname.strip().lower().rstrip(".")
        entries = self._read_entries()
        before = len(entries)
        entries = [(i, h) for (i, h) in entries if h.lower() != hostname]
        if len(entries) == before:
            return f"Not found: {hostname}"
        self._write_entries(entries)
        return f"Removed: {hostname}"

    def list_hosts(self) -> list[dict[str, str]]:
        """Return all entries as a list of {ip, hostname} dicts."""
        return [{"ip": ip, "hostname": hostname} for ip, hostname in self._read_entries()]

    # --- Command wrapping ----------------------------------------------------

    def wrap_command(self, command_str: str) -> str:
        """Prepend nss-wrapper environment variables to *command_str*.

        Only wraps when:
        1. CAI_VHOST_WRAP != "false"
        2. libnss_wrapper.so is present
        3. There is at least one entry in hosts.txt
        4. The first token of the command is a known network tool
        5. The system is not running inside Docker (CAI_ACTIVE_CONTAINER is unset)

        Returns the original string unmodified when any condition is not met.
        """
        if os.getenv("CAI_VHOST_WRAP", "true").lower() == "false":
            return command_str

        # Don't wrap inside Docker containers — they typically share network
        # namespaces differently and the LD_PRELOAD path may not exist there.
        if os.getenv("CAI_ACTIVE_CONTAINER", ""):
            return command_str

        lib = _find_libnss_wrapper()
        if not lib:
            return command_str

        # Only wrap when the workspace hosts file is non-empty
        if not self.list_hosts():
            return command_str

        # Extract the first token (binary name) to check against known tools
        stripped = command_str.strip()
        first_token = stripped.split()[0] if stripped else ""
        binary_name = os.path.basename(first_token)
        if binary_name not in _NETWORK_TOOL_NAMES:
            return command_str

        hosts_path = self.get_hosts_path()
        if not os.path.exists(hosts_path):
            return command_str

        hostname = os.getenv("CAI_VHOST_HOSTNAME", "cai-node")
        prefix = (
            f"LD_PRELOAD={lib} "
            f"NSS_WRAPPER_HOSTS={hosts_path} "
            f"NSS_WRAPPER_HOSTNAME={hostname} "
        )
        return prefix + command_str


# --- Process-wide singleton --------------------------------------------------

_VHM_INSTANCE: VirtualHostsManager | None = None


def get_vhm() -> VirtualHostsManager:
    """Return the process-wide :class:`VirtualHostsManager` singleton."""
    global _VHM_INSTANCE  # noqa: PLW0603
    if _VHM_INSTANCE is None:
        _VHM_INSTANCE = VirtualHostsManager()
    return _VHM_INSTANCE


# ---------------------------------------------------------------------------
# Agent-callable function tools
# ---------------------------------------------------------------------------


@function_tool
def add_virtual_host(hostname: str, ip: str) -> str:
    """Add or update a virtual-host entry in the workspace hosts file.

    The entry is immediately honoured by subsequent nmap / curl / hydra calls
    through the nss-wrapper LD_PRELOAD integration.

    Args:
        hostname: The virtual hostname to register, e.g. "sub.principal.htb".
        ip:       The IP address to map it to, e.g. "10.10.11.42".

    Returns:
        str: Confirmation message or error description.

    Examples:
        add_virtual_host("principal.htb", "10.10.11.42")
        add_virtual_host("dev.principal.htb", "10.10.11.42")
    """
    return get_vhm().add_host(hostname, ip)


@function_tool
def remove_virtual_host(hostname: str) -> str:
    """Remove a virtual-host entry from the workspace hosts file.

    Args:
        hostname: The hostname to remove, e.g. "sub.principal.htb".

    Returns:
        str: Confirmation or "Not found" message.
    """
    return get_vhm().remove_host(hostname)


@function_tool
def list_virtual_hosts() -> str:
    """List all virtual-host entries currently in the workspace hosts file.

    Returns:
        str: Formatted table of hostname → IP mappings, or a message if empty.
    """
    entries = get_vhm().list_hosts()
    if not entries:
        hosts_path = get_vhm().get_hosts_path()
        return f"No virtual hosts configured (hosts file: {hosts_path})"
    lines = ["Virtual hosts (workspace hosts.txt):"]
    for e in entries:
        lines.append(f"  {e['hostname']:<40} {e['ip']}")
    available = get_vhm().is_available()
    if not available:
        lines.append("")
        lines.append(
            "WARNING: libnss_wrapper.so not found — wrapping is disabled.\n"
            "Install it with: sudo apt install libnss-wrapper -y"
        )
    return "\n".join(lines)


@function_tool
def check_nss_wrapper() -> str:
    """Check whether libnss_wrapper is installed and virtual-host wrapping is active.

    Returns:
        str: Status report including library path and hosts file location.
    """
    lib = _find_libnss_wrapper()
    vhm = get_vhm()
    hosts_path = vhm.get_hosts_path()
    wrap_enabled = os.getenv("CAI_VHOST_WRAP", "true").lower() != "false"
    in_container = bool(os.getenv("CAI_ACTIVE_CONTAINER", ""))

    lines = ["nss-wrapper status:"]
    if lib:
        lines.append(f"  library    : {lib} ✓")
    else:
        lines.append(
            "  library    : NOT FOUND ✗\n"
            "               Install with: sudo apt install libnss-wrapper -y"
        )
    lines.append(f"  hosts file : {hosts_path}")
    lines.append(f"  wrapping   : {'disabled (CAI_VHOST_WRAP=false)' if not wrap_enabled else 'enabled'}")
    if in_container:
        lines.append("  note       : running inside Docker container — wrapping skipped")
    entries = vhm.list_hosts()
    lines.append(f"  entries    : {len(entries)}")
    for e in entries:
        lines.append(f"    {e['hostname']:<40} {e['ip']}")
    return "\n".join(lines)
