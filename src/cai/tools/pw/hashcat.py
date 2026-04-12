"""Hashcat executor tool for CAI agents.

Provides ``hashcat_executor`` — a non-interactive, session-scoped wrapper
around the ``hashcat`` binary that:

* **Discovers wordlists automatically** when the caller omits one.  Probes:
  * ``/usr/share/wordlists/rockyou.txt`` (standard Kali / Debian path)
  * ``/usr/share/seclists/Passwords/`` (SecLists repository)
  * ``/usr/share/wordlists/`` (any ``.txt`` file in the fallback folder)
* **Enforces non-interactive guardrails** — always injects
  ``--status --status-timer 10 --quiet`` and uses a per-session
  ``--potfile-path`` so runs never pollute the system potfile.
* **Applies a hard timeout** (default 600 s) to prevent runaway GPU usage.
* **Post-processes results** with ``hashcat --show`` after the run and
  returns a clean cracked-passwords table.
* Returns a structured JSON response consumed by the Intelligence Panel.

Security notes
--------------
* ``hash_file``, ``wordlist``, and ``extra_args`` are validated against
  shell metacharacter injection before use.
* The subprocess is always invoked with ``shell=False`` (argv list).
* Path-traversal sequences in ``hash_file`` and ``wordlist`` are rejected.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from cai.sdk.agents import function_tool
from cai.tools import validation

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECLISTS_PASSWORDS = Path("/usr/share/seclists/Passwords")
_ROCKYOU = Path("/usr/share/wordlists/rockyou.txt")
_WORDLISTS_DIR = Path("/usr/share/wordlists")
_DEFAULT_TIMEOUT = 600  # seconds

# Pot file lives inside the project's logs/ directory so results are
# session-scoped and never overwrite the system ~/.hashcat/hashcat.potfile.
_POT_DIR = Path("logs")
_POT_FILE = _POT_DIR / "hashes.pot"

# Characters that must not appear inside a filesystem path argument
_PATH_INJECT_RE = re.compile(r"[;&|`$<>()\n\r\x00]")

# Supported hashcat modes we recognise (extend as needed — only used for
# validation; hashcat itself is the authoritative list)
_VALID_ATTACK_MODES = {0, 1, 3, 6, 7}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _seclists_available() -> bool:
    """Return True when the SecLists Passwords tree is present and non-empty."""
    return _SECLISTS_PASSWORDS.exists() and any(_SECLISTS_PASSWORDS.iterdir())


def _system_advice() -> str:
    """Return a hint message if SecLists is not installed."""
    if not _seclists_available():
        return (
            "System Advice: SecLists not found at /usr/share/seclists. "
            "Recommend installing with 'sudo apt install seclists' for better "
            "wordlist coverage."
        )
    return ""


def _resolve_wordlist(requested: str) -> tuple[str, str]:
    """Return (resolved_path, advice).

    Tries each known location in priority order and returns the first that
    exists on disk.  If *requested* is provided it is used as-is after a
    quick path-injection check.
    """
    if requested:
        return (requested, "")

    advice = _system_advice()

    # Priority 1: rockyou.txt
    if _ROCKYOU.exists():
        return (str(_ROCKYOU), advice)

    # Priority 2: best available file under SecLists
    if _seclists_available():
        candidates = [
            _SECLISTS_PASSWORDS / "Common-Credentials" / "10k-most-common.txt",
            _SECLISTS_PASSWORDS / "Common-Credentials" / "best1050.txt",
            _SECLISTS_PASSWORDS / "rockyou-75.txt",
        ]
        for c in candidates:
            if c.exists():
                return (str(c), advice)
        # Fall back to the first .txt in SecLists
        for p in sorted(_SECLISTS_PASSWORDS.rglob("*.txt")):
            return (str(p), advice)

    # Priority 3: first .txt in /usr/share/wordlists/
    if _WORDLISTS_DIR.exists():
        for p in sorted(_WORDLISTS_DIR.glob("*.txt")):
            return (str(p), advice)

    return ("", advice + "\nNo wordlist found on this system — please provide one via `wordlist`.")


def _check_path(value: str, param_name: str) -> Optional[str]:
    """Return an error string when *value* looks dangerous as a path argument."""
    if not value:
        return None
    if _PATH_INJECT_RE.search(value):
        return (
            f"[BLOCKED] Parameter '{param_name}' contains disallowed characters. "
            "Remove shell metacharacters ( ; & | ` $ < > newline ) from the path."
        )
    # Reject obvious path-traversal attempts
    if ".." in Path(value).parts:
        return f"[BLOCKED] Parameter '{param_name}' contains a path-traversal sequence ('..')."
    return None


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mGKH]", "", s or "")


# ---------------------------------------------------------------------------
# Post-processing: parse `hashcat --show` into a table
# ---------------------------------------------------------------------------


def _parse_show_output(raw: str) -> list[dict]:
    """Parse the colon-delimited output of ``hashcat --show``."""
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # hashcat --show format:  hash:plain  (or  hash:salt:plain  for salted types)
        parts = line.split(":", maxsplit=2)
        if len(parts) >= 2:
            rows.append(
                {
                    "hash": parts[0],
                    "plaintext": parts[-1],
                    "raw": line,
                }
            )
    return rows


def _render_cracked_table(rows: list[dict]) -> str:
    """Return a compact plain-text table for the Intelligence Panel."""
    if not rows:
        return "No hashes cracked."
    header = f"{'HASH':<40}  PLAINTEXT"
    sep = "-" * 60
    lines = [header, sep]
    for r in rows:
        h = r["hash"][:38] + "…" if len(r["hash"]) > 40 else r["hash"]
        lines.append(f"{h:<40}  {r['plaintext']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------


@function_tool
async def hashcat_executor(
    hash_file: str,
    hash_type: int,
    attack_mode: int = 0,
    wordlist: str = "",
    extra_args: str = "",
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Run Hashcat against a hash file and return cracked credentials.

    Environment discovery: if *wordlist* is omitted the tool automatically
    probes known SecLists and standard wordlist paths.

    Args:
        hash_file: Path to the file containing hashes (one hash per line).
        hash_type: Hashcat ``-m`` mode integer (e.g. 0 for MD5, 1000 for
            NTLM, 1800 for sha512crypt).
        attack_mode: Hashcat ``-a`` mode integer (default 0 = Straight /
            dictionary attack).
        wordlist: Path to the wordlist/dictionary file.  If omitted, the
            tool searches for rockyou.txt and SecLists dictionaries.
        extra_args: Additional verbatim flags passed to hashcat, e.g.
            ``'-O --force'``.  Shell metacharacters are not permitted.
        timeout: Maximum seconds to allow hashcat to run (default 600).

    Returns:
        JSON string with keys:
        ``cracked_table`` — pretty-printed table of cracked hashes,
        ``cracked_rows`` — list of dicts with ``hash`` and ``plaintext``,
        ``raw_output`` — sanitized hashcat stdout/stderr,
        ``advice`` — SecLists availability hint,
        ``error`` — null or error string.
    """
    # ---- input validation -------------------------------------------------
    if not hash_file:
        return json.dumps({"error": "hash_file is required.", "cracked_rows": [], "cracked_table": ""})

    for param, val in (("hash_file", hash_file), ("wordlist", wordlist)):
        if err := _check_path(val, param):
            return json.dumps({"error": err, "cracked_rows": [], "cracked_table": ""})

    if extra_args:
        if err := validation.validate_args_no_injection(extra_args, "extra_args"):
            return json.dumps({"error": err, "cracked_rows": [], "cracked_table": ""})

    if not Path(hash_file).exists():
        return json.dumps({
            "error": f"hash_file not found: {hash_file}",
            "cracked_rows": [],
            "cracked_table": "",
        })

    if attack_mode not in _VALID_ATTACK_MODES:
        return json.dumps({
            "error": f"Unsupported attack_mode {attack_mode}. Supported: {sorted(_VALID_ATTACK_MODES)}",
            "cracked_rows": [],
            "cracked_table": "",
        })

    # ---- binary check -----------------------------------------------------
    hashcat_bin = shutil.which("hashcat")
    if hashcat_bin is None:
        return json.dumps({
            "error": "hashcat binary not found in PATH. Install with 'sudo apt install hashcat'.",
            "cracked_rows": [],
            "cracked_table": "",
            "advice": _system_advice(),
        })

    # ---- wordlist resolution ----------------------------------------------
    resolved_wordlist, advice = _resolve_wordlist(wordlist)

    if attack_mode == 0 and not resolved_wordlist:
        return json.dumps({
            "error": "No wordlist available and attack_mode=0 requires one.",
            "cracked_rows": [],
            "cracked_table": "",
            "advice": advice,
        })

    # ---- pot file setup ---------------------------------------------------
    try:
        _POT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # non-fatal: hashcat may still run, pot path will just fail silently

    pot_path = str(_POT_FILE)

    # ---- build argv -------------------------------------------------------
    cmd: list[str] = [
        hashcat_bin,
        "-m", str(hash_type),
        "-a", str(attack_mode),
        "--status",
        "--status-timer", "10",
        "--quiet",
        "--potfile-path", pot_path,
        hash_file,
    ]
    if resolved_wordlist:
        cmd.append(resolved_wordlist)

    if extra_args:
        try:
            cmd.extend(shlex.split(extra_args))
        except ValueError as exc:
            return json.dumps({
                "error": f"Could not parse extra_args: {exc}",
                "cracked_rows": [],
                "cracked_table": "",
            })

    # ---- execute ----------------------------------------------------------
    raw_output = ""
    timed_out = False

    def _run_hashcat() -> subprocess.CompletedProcess:  # type: ignore[type-arg]
        return subprocess.run(  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )

    try:
        result = await asyncio.to_thread(_run_hashcat)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        raw_output = stdout + ("\n[stderr]\n" + stderr if stderr.strip() else "")
    except subprocess.TimeoutExpired:
        timed_out = True
        raw_output = f"[TIMEOUT] hashcat exceeded {timeout}s limit. Partial results may exist in {pot_path}."
    except FileNotFoundError:
        return json.dumps({
            "error": f"Binary disappeared after resolution: {cmd[0]}",
            "cracked_rows": [],
            "cracked_table": "",
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "error": f"Execution error: {exc}",
            "cracked_rows": [],
            "cracked_table": "",
        })

    # ---- post-process: --show ---------------------------------------------
    cracked_rows: list[dict] = []
    show_error: str = ""

    show_cmd: list[str] = [
        hashcat_bin,
        "-m", str(hash_type),
        "--quiet",
        "--potfile-path", pot_path,
        "--show",
        hash_file,
    ]

    def _run_show() -> subprocess.CompletedProcess:  # type: ignore[type-arg]
        return subprocess.run(  # nosec B603
            show_cmd,
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
        )

    try:
        show_result = await asyncio.to_thread(_run_show)
        show_raw = _strip_ansi(show_result.stdout or "")
        cracked_rows = _parse_show_output(show_raw)
    except subprocess.TimeoutExpired:
        show_error = "hashcat --show timed out; crack results may still be in the pot file."
    except Exception as exc:  # noqa: BLE001
        show_error = f"hashcat --show error: {exc}"

    cracked_table = _render_cracked_table(cracked_rows)

    # ---- sanitize raw output before returning to agent --------------------
    try:
        from cai.agents.guardrails import sanitize_external_content as _san
        raw_safe = _san(_strip_ansi(raw_output))[:8000]
    except Exception:
        raw_safe = _strip_ansi(raw_output)[:8000]

    summary_parts = [f"Cracked: {len(cracked_rows)} hash(es)"]
    if timed_out:
        summary_parts.append(f"(timeout after {timeout}s)")
    if show_error:
        summary_parts.append(f"Show-step warning: {show_error}")

    return json.dumps(
        {
            "summary": " · ".join(summary_parts),
            "cracked_table": cracked_table,
            "cracked_rows": cracked_rows,
            "raw_output": raw_safe,
            "advice": advice,
            "pot_file": pot_path,
            "command": " ".join(cmd),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": None,
        },
        indent=2,
    )
