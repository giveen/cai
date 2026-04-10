"""Simple, safe wrapper for the `ffuf` command-line fuzzer.

This module builds an argv list and invokes `ffuf` via `subprocess.run`
without `shell=True`, avoiding shell interpolation of user-supplied
values (e.g. wordlists, headers, cookies, passwords). It also runs
light validation via `cai.tools.validation` and returns sanitized
output.
"""

from __future__ import annotations

import subprocess

from cai.tools import validation  # pylint: disable=import-error


def _validate_url(u: str | None) -> str | None:
    if not u:
        return "Invalid url: required and must include FUZZ"
    if "FUZZ" not in u:
        return "Invalid url: must contain the FUZZ placeholder"
    if not validation.URL_SCHEME_RE.match(u.strip()):
        return "Invalid url: must include a scheme (http:// or https://)"
    if validation.contains_shell_metacharacters(u):
        return "Invalid url: contains shell metacharacters"
    return None


def _validate_wordlists(w: str | list[str]) -> str | None:
    if not w:
        return "Invalid wordlist: required"
    if isinstance(w, str):
        if validation.contains_shell_metacharacters(w):
            return "Invalid wordlist path: contains shell metacharacters"
    else:
        for item in w:
            if validation.contains_shell_metacharacters(item):
                return f"Invalid wordlist path '{item}': contains shell metacharacters"
    return None


def _validate_headers(headers: list[str] | None) -> str | None:
    if not headers:
        return None
    for h in headers:
        if "\n" in h or "\r" in h:
            return "Invalid header: contains newline"
        if ":" not in h:
            return f"Invalid header '{h}': expected 'Name: Value'"
        if validation.contains_shell_metacharacters(h):
            return f"Invalid header '{h}': contains shell metacharacters"
    return None


def run_ffuf(
    url: str,
    wordlist: str | list[str],
    headers: list[str] | None = None,
    method: str | None = None,
    data: str | None = None,
    threads: int | None = None,
    rate: int | float | str | None = None,
    proxy: str | None = None,
    json_output: bool = False,
    output_file: str | None = None,
    timeout: int = 300,
    extra_args: list[str] | None = None,
) -> str:
    """Run `ffuf` with the provided options and return combined stdout/stderr.

    This wrapper performs minimal validation and then calls `ffuf` using
    `subprocess.run` with an argv list (no shell). The returned output is
    passed through `validation.sanitize_tool_output` before being returned.
    """
    # Validate inputs
    err = _validate_url(url)
    if err:
        return err
    err = _validate_wordlists(wordlist)
    if err:
        return err
    err = _validate_headers(headers)
    if err:
        return err
    if data and validation.contains_shell_metacharacters(data):
        return "Invalid data: contains shell metacharacters"

    argv: list[str] = ["ffuf", "-u", url]

    # wordlist(s)
    if isinstance(wordlist, str):
        argv.extend(["-w", wordlist])
    else:
        for w in wordlist:
            argv.extend(["-w", w])

    # headers
    if headers:
        for h in headers:
            argv.extend(["-H", h])

    if method:
        argv.extend(["-X", method])
    if data:
        argv.extend(["-d", data])
    if threads:
        argv.extend(["-t", str(threads)])
    if rate is not None:
        argv.extend(["-rate", str(rate)])
    if proxy:
        argv.extend(["-x", proxy])
    if json_output:
        argv.append("-json")
    if output_file:
        argv.extend(["-o", output_file])
    if extra_args:
        argv.extend(extra_args)

    # Guardrail validation using a human-readable command string
    cmd_str = " ".join(argv)
    g_err = validation.validate_command_guardrails(cmd_str)
    if g_err:
        return g_err

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
    except FileNotFoundError:
        return "Error: ffuf not found on PATH"
    except subprocess.TimeoutExpired:
        return "Error: ffuf timed out"

    out = (proc.stdout or "") + (proc.stderr or "")
    return validation.sanitize_tool_output(cmd_str, out)


__all__ = ["run_ffuf"]
