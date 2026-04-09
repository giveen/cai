"""
cewl tool — custom wordlist generator from web content.

Wraps the system `cewl` binary, which spiders a URL and extracts unique
words for use in password attacks.  All inputs are validated to prevent
shell-injection from agent-supplied arguments.
"""

import shlex

from cai.sdk.agents import function_tool
from cai.tools import validation
from cai.tools.common import run_command
from cai.tools.validation import is_url_safe, validate_args_no_injection


@function_tool
def cewl(
    url: str,
    depth: int = 2,
    min_word_length: int = 3,
    max_word_length: int = 0,
    lowercase: bool = False,
    with_numbers: bool = False,
    count: bool = False,
    include_meta: bool = False,
    include_email: bool = False,
    user_agent: str = "",
    auth_type: str = "",
    auth_user: str = "",
    auth_pass: str = "",
    cookie: str = "",
    header: str = "",
    proxy_host: str = "",
    proxy_port: int = 8080,
    args: str = "",
    timeout: int = 120,
) -> str:
    """
    Run CeWL against a URL to generate a custom wordlist by spidering the site.

    CeWL spiders a target web application and extracts unique words that can
    be used as a custom wordlist for password attacks (e.g. with Hydra, Medusa,
    or John the Ripper).

    Args:
        url:            Target URL to spider.
                        Example: "http://target.com"
        depth:          Spider depth (default 2).  Use 1 for current page only.
        min_word_length: Minimum word length to include (default 3).
        max_word_length: Maximum word length (0 = no limit).
        lowercase:      Convert all words to lowercase.
        with_numbers:   Accept words that contain numbers as well as letters.
        count:          Show the occurrence count next to each word.
        include_meta:   Also extract words from meta data (PDF, Word, etc.).
        include_email:  Also extract email addresses found on the site.
        user_agent:     Custom User-Agent string to send with requests.
        auth_type:      HTTP authentication type: "basic" or "digest".
        auth_user:      HTTP authentication username.
        auth_pass:      HTTP authentication password.
        cookie:         Cookie header value to include with every request.
                        Example: "PHPSESSID=abc123; logged_in=1"
        header:         Extra HTTP header in "Name:Value" format.
                        Example: "X-Forwarded-For:127.0.0.1"
        proxy_host:     Proxy hostname (e.g. "127.0.0.1" for Burp).
        proxy_port:     Proxy port (default 8080).
        args:           Any additional raw CeWL flags not covered above.
                        Example: "--convert-umlauts --groups 2"
        timeout:        Maximum seconds to wait for cewl to finish (default 120).

    Returns:
        str: Wordlist output — one word per line — plus any stderr messages.

    Examples:
        cewl(url="http://target.com")
        cewl(url="http://target.com", depth=3, min_word_length=5, lowercase=True)
        cewl(url="http://target.com/login", with_numbers=True, count=True)
        cewl(url="http://target.com", auth_type="basic", auth_user="admin", auth_pass="admin")
        cewl(url="http://target.com", cookie="session=abc", depth=1)
        cewl(url="http://target.com", proxy_host="127.0.0.1", proxy_port=8080)
        cewl(url="http://target.com", include_email=True, include_meta=True)
    """
    # ── Input validation ────────────────────────────────────────────────────
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

    for name, val in (
        ("user_agent", user_agent),
        ("auth_user", auth_user),
        ("auth_pass", auth_pass),
        ("cookie", cookie),
        ("header", header),
        ("proxy_host", proxy_host),
    ):
        if val and validation.contains_shell_metacharacters(val):
            return f"Invalid {name} '{val}': contains shell-special characters."

    if auth_type and auth_type.strip().lower() not in ("basic", "digest"):
        return "auth_type must be 'basic' or 'digest'."

    # ── Build command ───────────────────────────────────────────────────────
    parts = ["cewl"]

    parts += ["-d", str(max(0, depth))]
    parts += ["-m", str(max(1, min_word_length))]

    if max_word_length and max_word_length > 0:
        parts += ["-x", str(max_word_length)]
    if lowercase:
        parts.append("--lowercase")
    if with_numbers:
        parts.append("--with-numbers")
    if count:
        parts.append("-c")
    if include_meta:
        parts.append("-a")
    if include_email:
        parts.append("-e")

    if user_agent:
        parts += ["-u", shlex.quote(user_agent)]

    if auth_type:
        parts += ["--auth_type", shlex.quote(auth_type.strip())]
    if auth_user:
        parts += ["--auth_user", shlex.quote(auth_user)]
    if auth_pass:
        parts += ["--auth_pass", shlex.quote(auth_pass)]

    if cookie:
        parts += ["--header", shlex.quote(f"Cookie:{cookie}")]
    if header:
        parts += ["-H", shlex.quote(header)]

    if proxy_host:
        parts += ["--proxy_host", shlex.quote(proxy_host)]
        parts += ["--proxy_port", str(proxy_port)]

    if args:
        parts.append(args)

    parts.append(shlex.quote(url.strip()))

    command = " ".join(parts)

    guard_err = validation.validate_command_guardrails(command)
    if guard_err:
        return guard_err

    result = run_command(command, timeout=timeout)
    if isinstance(result, str):
        result = validation.sanitize_tool_output(command, result)
    return result
