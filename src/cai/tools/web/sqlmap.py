"""
sqlmap tool — automated SQL injection detection and exploitation.

Wraps the system `sqlmap` binary.  Inputs are validated to prevent
shell-injection from agent-supplied arguments; sqlmap is always run with
`--batch` so it never prompts interactively.
"""

import re
import shlex

from cai.sdk.agents import function_tool
from cai.tools import validation
from cai.tools.common import run_command
from cai.tools.validation import is_url_safe, validate_args_no_injection

# Disallow flags that would trigger interactive prompts or OOB shells
# even when --batch is set.
_DANGEROUS_FLAGS = re.compile(
    r"(?:^|\s)--(?:os-shell|os-pwn|os-cmd|os-smbrelay|os-bof"
    r"|priv-esc|msf-path|tmp-path|wizard)",
    re.I,
)


def _validate_sqlmap_input(url: str, args: str) -> str | None:
    """Return an error string if inputs are unsafe, otherwise None."""
    if not url or not url.strip():
        return "url is required."
    if not is_url_safe(url.strip()):
        return (
            f"Invalid url '{url}': must be a well-formed URL without "
            "whitespace or shell-special characters."
        )
    if args:
        err = validate_args_no_injection(args, "args")
        if err:
            return err
        if _DANGEROUS_FLAGS.search(args):
            return (
                "Disallowed flag in args: --os-shell, --os-pwn, and similar "
                "OS-access flags are not permitted."
            )
    return None


@function_tool
def sqlmap(
    url: str,
    args: str = "",
    data: str = "",
    cookie: str = "",
    timeout: int = 300,
) -> str:
    """
    Run sqlmap against a URL to detect and exploit SQL injection vulnerabilities.

    sqlmap is always run with --batch (no interactive prompts) and
    --flush-session (fresh state each call).

    Args:
        url:     Target URL with at least one injectable parameter.
                 Examples:
                   "http://target.com/page.php?id=1"
                   "http://target.com/login.php"
        args:    Additional sqlmap flags.  Common examples:
                   "-p id"                        — test only the 'id' parameter
                   "--dbs"                        — enumerate databases
                   "--current-db"                 — print current database name
                   "--current-user"               — print current DB user
                   "--tables -D mydb"             — list tables in database 'mydb'
                   "--dump -D mydb -T users"      — dump the 'users' table
                   "--columns -D mydb -T users"   — list columns in 'users' table
                   "--passwords"                  — try to retrieve password hashes
                   "--level=3 --risk=2"           — increase test depth / risk
                   "--dbms=mysql"                 — specify backend DBMS
                   "--technique=BEU"              — only boolean/error/union tests
                   "--random-agent"               — randomise User-Agent header
                   "--proxy=http://127.0.0.1:8080" — route through Burp/proxy
                   "--ignore-code=401"            — ignore 401 response codes
                   "--threads=4"                  — parallelise tests
                   "--banner"                     — retrieve DBMS banner
                   "--schema"                     — enumerate full schema
                   "--dump-all"                   — dump all databases (slow)
                   "-v 3"                         — verbose output level 3
        data:    POST body string (equivalent to sqlmap --data).
                 Example: "username=admin&password=test"
        cookie:  HTTP Cookie header value (equivalent to sqlmap --cookie).
                 Example: "PHPSESSID=abc123; auth=1"
        timeout: Maximum seconds to wait for sqlmap to finish (default 300).

    Returns:
        str: Raw sqlmap output including injection findings, extracted data,
             and any errors.

    Examples:
        sqlmap(url="http://target.com/vuln.php?id=1")
        sqlmap(url="http://target.com/vuln.php?id=1", args="--dbs")
        sqlmap(url="http://target.com/login", data="user=admin&pass=x", args="--current-user")
        sqlmap(url="http://target.com/vuln.php?id=1", args="--dump -D appdb -T users")
        sqlmap(url="http://target.com/page", cookie="session=abc", args="-p id --level=3")
    """
    err = _validate_sqlmap_input(url, args)
    if err:
        return err

    # Merge pinned session cookie (caller-supplied value takes precedence).
    try:
        from cai.util.orchestration import merge_pinned_cookie

        cookie = merge_pinned_cookie(cookie)
    except Exception:
        pass

    parts = ["sqlmap", "--batch", "--flush-session", f"-u {shlex.quote(url.strip())}"]

    if data:
        # validate data string — no shell injection; single quotes are fine inside
        if validation.contains_shell_metacharacters(data):
            return f"Invalid data '{data}': contains shell-special characters."
        parts.append(f"--data={shlex.quote(data)}")

    if cookie:
        if validation.contains_shell_metacharacters(cookie):
            return f"Invalid cookie '{cookie}': contains shell-special characters."
        parts.append(f"--cookie={shlex.quote(cookie)}")

    if args:
        parts.append(args)

    command = " ".join(parts)

    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err

    result = run_command(command, timeout=timeout)
    if isinstance(result, str):
        result = validation.sanitize_tool_output(command, result)
    return result
