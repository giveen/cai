"""Hash identification and analysis tool.

Identifies likely hash type from format/length, computes common hashes
of given plaintext for comparison, and optionally delegates cracking
to hashcat or john if they are available on the system.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import os
import shutil
from typing import NamedTuple

from cai.sdk.agents import function_tool

# ---------------------------------------------------------------------------
# Hash type signatures
# ---------------------------------------------------------------------------

class _Sig(NamedTuple):
    name: str
    regex: str
    hashcat_mode: int | None = None
    note: str = ""


_SIGNATURES: list[_Sig] = [
    # Fixed-length hex hashes
    _Sig("MD5",     r"^[0-9a-fA-F]{32}$",  0,   "Very common, fast to crack"),
    _Sig("SHA-1",   r"^[0-9a-fA-F]{40}$",  100, "Common in old systems"),
    _Sig("SHA-224", r"^[0-9a-fA-F]{56}$",  None),
    _Sig("SHA-256", r"^[0-9a-fA-F]{64}$",  1400),
    _Sig("SHA-384", r"^[0-9a-fA-F]{96}$",  10800),
    _Sig("SHA-512", r"^[0-9a-fA-F]{128}$", 1700),
    # NTLM (Windows LM/NT hashes — same length as MD5)
    _Sig("NTLM",    r"^[0-9a-fA-F]{32}$",  1000, "Indistinguishable from MD5 by format alone"),
    # bcrypt
    _Sig("bcrypt",  r"^\$2[aby]?\$\d{2}\$[./A-Za-z0-9]{53}$", 3200),
    # Modular Crypt Format
    _Sig("MD5-crypt",    r"^\$1\$[./A-Za-z0-9]{1,8}\$[./A-Za-z0-9]{22}$", 500),
    _Sig("SHA-256-crypt", r"^\$5\$[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{43}$", 7400),
    _Sig("SHA-512-crypt", r"^\$6\$[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}$", 1800),
    # Django
    _Sig("Django-SHA256", r"^pbkdf2_sha256\$\d+\$.{0,32}\$[A-Za-z0-9+/]{43}=$", 10000),
    # JWT (not a hash, but frequently confused)
    _Sig("JWT",          r"^eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$", None,
         "JSON Web Token — not a hash; decode with jwt tool or base64"),
    # MySQL
    _Sig("MySQL-OLD", r"^[0-9a-fA-F]{16}$", 200),
    _Sig("MySQL4.1", r"^\*[0-9a-fA-F]{40}$", 300),
    # LM hash
    _Sig("LM",       r"^[0-9a-fA-F]{32}$",  3000, "Indistinguishable from MD5 by format alone"),
    # WPA/PBKDF2 (hex)
    _Sig("WPA-PMKID", r"^[0-9a-fA-F]{64}$", 22000, "Possible WPA PMKID — same length as SHA-256"),
    # HMAC-MD5 (same length as MD5)
    _Sig("HMAC-MD5", r"^[0-9a-fA-F]{32}$", 50, "Possible HMAC-MD5"),
]


def _identify_hash(h: str) -> list[str]:
    """Return list of possible hash type names for *h*."""
    h = h.strip()
    seen: list[str] = []
    for sig in _SIGNATURES:
        if re.match(sig.regex, h) and sig.name not in seen:
            seen.append(sig.name)
    return seen


def _compute_hashes(plaintext: str) -> dict[str, str]:
    """Return a dict of common hash names → hex digest for *plaintext*."""
    data = plaintext.encode("utf-8")
    return {
        "MD5":     hashlib.md5(data).hexdigest(),     # nosec B324
        "SHA-1":   hashlib.sha1(data).hexdigest(),    # nosec B324
        "SHA-256": hashlib.sha256(data).hexdigest(),
        "SHA-512": hashlib.sha512(data).hexdigest(),
    }


def _try_crack(hash_value: str, wordlist: str, hashcat_mode: int | None) -> str:
    """Try to crack *hash_value* using hashcat or john.

    Returns a status string describing the outcome.
    """
    if not os.path.isfile(wordlist):
        return f"Wordlist not found: {wordlist}"

    # Try hashcat first
    if shutil.which("hashcat") and hashcat_mode is not None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hash", delete=False) as f:
            f.write(hash_value + "\n")
            hash_file = f.name
        try:
            result = subprocess.run(
                ["hashcat", "-m", str(hashcat_mode), hash_file, wordlist,
                 "--quiet", "--potfile-disable", "--status"],
                capture_output=True, text=True, timeout=60,
            )
            out = (result.stdout + result.stderr).strip()
            # hashcat prints "hash:plain" on success
            for line in out.splitlines():
                if line.startswith(hash_value + ":"):
                    plain = line[len(hash_value) + 1:]
                    return f"[hashcat] Cracked: {plain!r}"
            if "Cracked" in out or "Status.......: Cracked" in out:
                return "[hashcat] Cracked (check potfile)"
            if out:
                return f"[hashcat] Not cracked. Status:\n{out[:500]}"
            return "[hashcat] Not cracked."
        except subprocess.TimeoutExpired:
            return "[hashcat] Timed out after 60s"
        except FileNotFoundError:
            pass
        finally:
            try:
                os.unlink(hash_file)
            except OSError:
                pass

    # Try john
    if shutil.which("john"):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".hash", delete=False) as f:
            f.write(hash_value + "\n")
            hash_file = f.name
        pot_file = hash_file + ".pot"
        try:
            result = subprocess.run(
                ["john", hash_file, f"--wordlist={wordlist}", f"--pot={pot_file}"],
                capture_output=True, text=True, timeout=60,
            )
            show = subprocess.run(
                ["john", "--show", hash_file, f"--pot={pot_file}"],
                capture_output=True, text=True, timeout=10,
            )
            show_out = show.stdout.strip()
            if show_out and "0 password hashes cracked" not in show_out:
                return f"[john] Cracked:\n{show_out}"
            return "[john] Not cracked."
        except subprocess.TimeoutExpired:
            return "[john] Timed out after 60s"
        except FileNotFoundError:
            pass
        finally:
            for p in (hash_file, pot_file):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    return "Neither hashcat nor john found on PATH. Install one to crack hashes."


def _run_hash_analyze(
    hash_or_text: str,
    mode: str = "identify",
    wordlist: str = "/usr/share/wordlists/rockyou.txt",
) -> str:
    """Core hash analysis logic (callable directly in tests)."""
    value = hash_or_text.strip()
    if not value:
        return "[hash_analyzer] Error: empty input"

    lines: list[str] = [f"[hash_analyzer] Input: {value[:80]}{'…' if len(value) > 80 else ''}\n"]

    if mode == "compute":
        hashes = _compute_hashes(value)
        lines.append("Common hashes of the provided plaintext:")
        for name, digest in hashes.items():
            lines.append(f"  {name:<10} {digest}")
        return "\n".join(lines)

    # Identify mode (default)
    candidates = _identify_hash(value)
    if candidates:
        lines.append(f"Possible hash type(s): {', '.join(candidates)}")
        for sig in _SIGNATURES:
            if sig.name in candidates and sig.note:
                lines.append(f"  {sig.name}: {sig.note}")
            if sig.name in candidates and sig.hashcat_mode is not None:
                lines.append(f"  {sig.name} hashcat mode: {sig.hashcat_mode}")
    else:
        lines.append("Hash type not recognized by pattern matching.")
        lines.append(f"  Length: {len(value)} characters")
        printable = all(0x20 <= ord(c) < 0x7f for c in value)
        lines.append(f"  All printable ASCII: {printable}")

    if mode == "crack" and candidates:
        lines.append("")
        lines.append(f"Attempting to crack with wordlist: {wordlist}")
        best_mode = next(
            (s.hashcat_mode for s in _SIGNATURES if s.name == candidates[0] and s.hashcat_mode),
            None,
        )
        crack_result = _try_crack(value, wordlist, best_mode)
        lines.append(crack_result)

    return "\n".join(lines)


@function_tool
def hash_analyzer(
    hash_or_text: str,
    mode: str = "identify",
    wordlist: str = "/usr/share/wordlists/rockyou.txt",
) -> str:
    """Identify, compute, or crack hashes.

    Modes:
      - ``identify`` (default): detect likely hash type from the format.
      - ``compute``: compute MD5/SHA-1/SHA-256/SHA-512 of the given plaintext.
      - ``crack``: identify the hash then attempt to crack it with hashcat
        or john (whichever is found on PATH) using the specified wordlist.

    Args:
        hash_or_text: The hash string to analyze (identify/crack),
            or the plaintext to hash (compute mode).
        mode: One of ``identify``, ``compute``, ``crack``. Default ``identify``.
        wordlist: Path to a password wordlist for crack mode.
            Defaults to ``/usr/share/wordlists/rockyou.txt``.

    Returns:
        Formatted analysis report.
    """
    return _run_hash_analyze(hash_or_text, mode=mode, wordlist=wordlist)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "hash_analyzer",
    hash_analyzer,
    categories=["misc", "exploitation"],
)
