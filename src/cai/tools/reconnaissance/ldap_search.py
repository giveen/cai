"""
LDAP enumeration tool wrapping the ldapsearch CLI.

Designed for CTF and penetration testing use: allows quick anonymous and
authenticated LDAP queries with safe, validated inputs.
"""

import shlex
from typing import Optional

from cai.sdk.agents import function_tool
from cai.tools.common import run_command
from cai.tools.validation import contains_shell_metacharacters, is_valid_target

# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------


def _validate_ldap_inputs(
    host: str,
    base_dn: str,
    filter_str: str,
    bind_dn: Optional[str],
    password: Optional[str],
    extra_args: str,
) -> Optional[str]:
    """Return an error string if any input is unsafe, else None."""
    host_s = (host or "").strip()
    if not host_s:
        return "host is required."
    if not is_valid_target(host_s):
        return f"Invalid host '{host}': must be an IPv4/IPv6 address or hostname."

    for label, value in [
        ("base_dn", base_dn),
        ("bind_dn", bind_dn or ""),
        ("filter_str", filter_str),
        ("extra_args", extra_args),
    ]:
        if contains_shell_metacharacters(value):
            return f"Invalid {label} '{value}': shell metacharacters are not allowed."

    if password and any(c in password for c in (";", "`", "$", "(")):
        return "Password contains unsafe characters."

    return None


def _build_ldapsearch_cmd(
    host: str,
    base_dn: str,
    filter_str: str,
    attributes: str,
    bind_dn: Optional[str],
    password: Optional[str],
    scope: str,
    port: int,
    use_tls: bool,
    extra_args: str,
) -> str:
    """Assemble the ldapsearch command string."""
    uri_scheme = "ldaps" if use_tls else "ldap"
    uri = f"{uri_scheme}://{host}:{port}"

    parts = ["ldapsearch", "-x", "-LLL", f"-H {shlex.quote(uri)}"]

    if bind_dn:
        parts.append(f"-D {shlex.quote(bind_dn)}")
        if password:
            parts.append(f"-w {shlex.quote(password)}")
    # anonymous bind — no -D flag needed

    scope_s = scope.lower()
    if scope_s not in ("base", "one", "sub", "children"):
        scope_s = "sub"
    parts.append(f"-s {scope_s}")

    if base_dn:
        parts.append(f"-b {shlex.quote(base_dn)}")

    if extra_args:
        parts.append(extra_args)

    parts.append(shlex.quote(filter_str or "(objectClass=*)"))

    if attributes:
        # attributes is a space-separated list; pass each token quoted
        parts.extend(shlex.quote(a) for a in attributes.split())

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public tool
# ---------------------------------------------------------------------------


@function_tool
def ldap_search(  # noqa: PLR0913
    host: str,
    base_dn: str = "",
    filter_str: str = "(objectClass=*)",
    attributes: str = "",
    bind_dn: str = "",
    password: str = "",
    scope: str = "sub",
    port: int = 389,
    use_tls: bool = False,
    extra_args: str = "",
    timeout: int = 30,
) -> str:
    """
    Run an ldapsearch query against an LDAP/LDAPS server.

    Wraps the `ldapsearch` CLI to enumerate users, groups, policies, and other
    directory objects. Defaults to anonymous simple-auth (-x) with sub-tree scope.

    Args:
        host:        LDAP server IP or hostname (required).
                     Example: "10.10.10.100"
        base_dn:     Search base DN. Leave empty to auto-detect via rootDSE.
                     Example: "DC=example,DC=com"
        filter_str:  RFC 4515 LDAP filter string.
                     Default: "(objectClass=*)" — return all objects.
                     Examples:
                       "(objectClass=user)"
                       "(objectClass=group)"
                       "(&(objectClass=user)(memberOf=CN=Domain Admins,CN=Users,DC=example,DC=com))"
                       "(userAccountControl:1.2.840.113556.1.4.803:=65536)" — password never expires
        attributes:  Space-separated list of attributes to return.
                     Leave empty for all user attributes (*).
                     Examples: "sAMAccountName mail" "cn dn"
                     Special values: "1.1" (no attrs), "+" (operational attrs)
        bind_dn:     Bind DN for authenticated queries. Leave empty for anonymous.
                     Example: "CN=admin,DC=example,DC=com"
        password:    Bind password. Leave empty for anonymous.
        scope:       Search scope. One of: base, one, sub (default), children.
        port:        LDAP TCP port. Default 389; use 636 for LDAPS.
        use_tls:     Use ldaps:// URI. Automatically set to True when port=636.
        extra_args:  Additional raw ldapsearch flags (e.g. "-A" for names-only,
                     "-z 100" for size limit, "-l 10" for time limit).
                     No shell metacharacters allowed.
        timeout:     Max seconds to wait for query (default 30).

    Returns:
        LDIF-formatted query output, or an error message.

    Common recipes:
        # Enumerate root DSE (discover naming contexts)
        ldap_search(host="10.10.10.100", base_dn="", filter_str="(objectClass=*)",
                    attributes="defaultNamingContext namingContexts", scope="base")

        # Anonymous dump of all objects
        ldap_search(host="10.10.10.100", base_dn="DC=example,DC=com")

        # Authenticated dump of all users
        ldap_search(host="10.10.10.100", base_dn="DC=example,DC=com",
                    filter_str="(objectClass=user)",
                    attributes="sAMAccountName cn mail memberOf",
                    bind_dn="CN=user,DC=example,DC=com", password="pass123")

        # Find all groups
        ldap_search(host="10.10.10.100", base_dn="DC=example,DC=com",
                    filter_str="(objectClass=group)", attributes="cn member")

        # LDAPS on port 636
        ldap_search(host="10.10.10.100", base_dn="DC=example,DC=com",
                    port=636, use_tls=True)
    """
    # Coerce optional empties
    bind_dn = bind_dn or ""
    password = password or ""
    extra_args = extra_args or ""

    # Auto-enable TLS when port 636 is requested
    if port == 636:
        use_tls = True

    err = _validate_ldap_inputs(host, base_dn, filter_str, bind_dn, password, extra_args)
    if err:
        return f"Error: {err}"

    cmd = _build_ldapsearch_cmd(
        host=host.strip(),
        base_dn=base_dn,
        filter_str=filter_str or "(objectClass=*)",
        attributes=attributes,
        bind_dn=bind_dn or None,
        password=password or None,
        scope=scope,
        port=port,
        use_tls=use_tls,
        extra_args=extra_args,
    )

    return run_command(cmd, timeout=timeout)
