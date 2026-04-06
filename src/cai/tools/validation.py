"""
Shared input validation helpers for reconnaissance tools.

Provide common regexes and small helper functions so individual
tools don't duplicate validation logic and risk drifting.
"""
from typing import Optional
import re

# Broad shell metacharacter detector used to catch obvious injection
# attempts when constructing shell commands.
SHELL_METACHAR_RE = re.compile(r'[;&|`$<>()\{\}\[\]\n\r\\]')

# Command-injection sequences (includes redirection and control operators)
CMD_INJECT_RE = re.compile(r'(;|&&|\|\||\||`|\$\(|\n|\r|>|<|\\)')

# URL/target safety: no whitespace or a small set of shell-special characters
URL_SAFE_RE = re.compile(r'^[^\s;|&`$<>()\n\r]+$')

# URL scheme matcher (not strictly required by all callers)
URL_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://')

# Valid nmap target patterns: IPv4, CIDR, range, wildcard, IPv6, or hostname
TARGET_RE = re.compile(
    r'^(?:'
    r'(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?'          # IPv4 / CIDR
    r'|(?:\d{1,3}\.){3}\d{1,3}-\d{1,3}'               # IPv4 range
    r'|(?:\d{1,3}\.){3}\*'                             # IPv4 wildcard
    r'|[0-9a-fA-F:]+(?:/\d{1,3})?'                    # IPv6 / CIDR
    r'|[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]*[a-zA-Z0-9])?'  # Hostname/FQDN
    r')$'
)

# Host/IP/hostname validation (IPv4, IPv6, or hostname)
HOST_RE = re.compile(
    r'^(?:'
    r'(?:\d{1,3}\.){3}\d{1,3}'                       # IPv4
    r'|[0-9a-fA-F:]+(?:%[0-9a-zA-Z]+)?'                 # IPv6 (basic)
    r'|[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]*[a-zA-Z0-9])?'   # Hostname/FQDN
    r')$'
)

# Netcat-specific: disallowed flags like -e -c -l
DISALLOWED_ARG_FLAGS = re.compile(r'(^|\s)-(?:e|c|l)($|\s)')

# Filename allowed for execute_code filename validations
FILENAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')


def contains_shell_metacharacters(s: Optional[str]) -> bool:
    return bool(s and SHELL_METACHAR_RE.search(s))


def contains_cmd_injection(s: Optional[str]) -> bool:
    return bool(s and CMD_INJECT_RE.search(s))


def is_url_safe(s: Optional[str]) -> bool:
    return bool(s and URL_SAFE_RE.match(s.strip()))


def is_valid_target(s: Optional[str]) -> bool:
    return bool(s and TARGET_RE.match(s.strip()))


def is_valid_host(s: Optional[str]) -> bool:
    return bool(s and HOST_RE.match(s.strip()))


def has_disallowed_nc_flags(s: Optional[str]) -> bool:
    return bool(s and DISALLOWED_ARG_FLAGS.search(s))


def is_valid_filename(s: Optional[str]) -> bool:
    return bool(s and FILENAME_RE.match(s))


def validate_args_no_injection(args: Optional[str], name: str = 'args', max_length: Optional[int] = None) -> Optional[str]:
    """Return an error string if `args` contains injection sequences or is too long."""
    if not args:
        return None
    if contains_cmd_injection(args):
        return (
            f"Invalid {name} '{args}': command injection sequences (; && || | ` $( newline) are not allowed."
        )
    if max_length and len(args) > max_length:
        return f"Invalid {name}: too long"
    return None
