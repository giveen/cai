"""
Here are the nmap tools.
"""

import re

from cai.tools.common import run_command  # pylint: disable=E0401
from cai.sdk.agents import function_tool

# Shell metacharacters that would enable command injection
_SHELL_METACHAR_RE = re.compile(r'[;&|`$<>()\{\}\[\]\n\r\\]')

# Valid nmap target patterns: IPv4, CIDR, range, wildcard, IPv6, or hostname
_TARGET_RE = re.compile(
    r'^(?:'
    r'(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?'          # IPv4 / CIDR  (e.g. 10.0.0.0/24)
    r'|(?:\d{1,3}\.){3}\d{1,3}-\d{1,3}'               # IPv4 range   (e.g. 192.168.1.1-20)
    r'|(?:\d{1,3}\.){3}\*'                             # IPv4 wildcard (e.g. 192.168.1.*)
    r'|[0-9a-fA-F:]+(?:/\d{1,3})?'                    # IPv6 / CIDR  (e.g. ::1, fe80::1/64)
    r'|[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]*[a-zA-Z0-9])?'  # Hostname     (e.g. example.com)
    r')$'
)


def _validate_nmap_input(args: str, target: str):
    """Return an error string if inputs are unsafe, else None."""
    if _SHELL_METACHAR_RE.search(args):
        return (
            f"Invalid args '{args}': shell metacharacters (;|&`$<>\\) are not allowed."
        )
    if target and not _TARGET_RE.match(target.strip()):
        return (
            f"Invalid target '{target}': must be an IPv4/IPv6 address, "
            "CIDR block, IP range, or hostname."
        )
    return None


@function_tool
def nmap(target: str, args: str = "", timeout: int = 300) -> str:
    """
    Run nmap against a target to discover open ports, services, and versions.

    Args:
        target:  Host, IP address, CIDR block, or IP range to scan.
                 Examples: "192.168.1.1", "10.0.0.0/24", "192.168.1.1-20", "example.com"
        args:    Additional nmap flags. Common examples:
                   "-sn"               — ping sweep (no port scan)
                   "-sV"               — service/version detection
                   "-sC"               — run default scripts
                   "-sV -sC"           — version + default scripts
                   "-A"                — aggressive: OS, version, scripts, traceroute
                   "-p 22,80,443"      — scan specific ports
                   "-p-"               — scan all 65535 ports
                   "--script vuln"     — run vulnerability scripts
                   "-T4"               — faster timing template
                   "-Pn"               — skip host discovery (treat host as up)
        timeout: Maximum seconds to wait for the scan (default 300).

    Returns:
        str: Raw nmap output including discovered hosts, open ports, services, and versions.

    Examples:
        nmap(target="192.168.1.1")
        nmap(target="10.0.0.0/24", args="-sn")
        nmap(target="192.168.1.1", args="-sV -sC -p 22,80,443")
        nmap(target="192.168.1.1", args="-A -T4 --script vuln", timeout=600)
        nmap(target="192.168.1.1-50", args="-p- --open")
    """
    err = _validate_nmap_input(args, target)
    if err:
        return err

    command = f'nmap {args} {target.strip()}'
    return run_command(command, timeout=timeout)
