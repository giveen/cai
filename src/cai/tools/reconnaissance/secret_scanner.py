"""Lightweight secret and credential scanner.

Scans files, directories, or raw strings for common high-entropy secrets and
credential patterns (API keys, tokens, connection strings, private keys, etc.)
without requiring external tools like trufflehog or gitleaks.

Designed for red-team use: enumerate exposed credentials after gaining a
foothold, or audit a target repository / config dump for leaks.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

@dataclass
class SecretPattern:
    name: str
    regex: re.Pattern
    severity: str  # "critical", "high", "medium", "low"
    context_chars: int = 60  # chars before/after match to show


_PATTERNS: list[SecretPattern] = [
    SecretPattern(
        "AWS Access Key",
        re.compile(r"AKIA[0-9A-Z]{16}", re.MULTILINE),
        "critical",
    ),
    SecretPattern(
        "AWS Secret Key (context-based)",
        re.compile(
            r"(?i)(?:aws_secret|secret_access_key)[^\n:=]*[=:]\s*['\"]?([A-Za-z0-9/+]{40})['\"]?",
            re.MULTILINE,
        ),
        "critical",
    ),
    SecretPattern(
        "Private Key (PEM)",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        "critical",
    ),
    SecretPattern(
        "GitHub Personal Access Token",
        re.compile(r"ghp_[A-Za-z0-9]{36}"),
        "critical",
    ),
    SecretPattern(
        "GitHub OAuth Token",
        re.compile(r"gho_[A-Za-z0-9]{36}"),
        "critical",
    ),
    SecretPattern(
        "GitHub App Token",
        re.compile(r"(?:ghu|ghs|ghr)_[A-Za-z0-9]{36}"),
        "high",
    ),
    SecretPattern(
        "Slack Bot Token",
        re.compile(r"xoxb-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24}"),
        "critical",
    ),
    SecretPattern(
        "Slack Webhook",
        re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"),
        "high",
    ),
    SecretPattern(
        "Google API Key",
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        "high",
    ),
    SecretPattern(
        "Google OAuth Client Secret",
        re.compile(r"(?i)client_secret[^\n:=]*[=:]\s*['\"]?([A-Za-z0-9\-_]{24})['\"]?"),
        "high",
    ),
    SecretPattern(
        "Stripe Secret Key",
        re.compile(r"sk_live_[A-Za-z0-9]{24,}"),
        "critical",
    ),
    SecretPattern(
        "Stripe Publishable Key",
        re.compile(r"pk_live_[A-Za-z0-9]{24,}"),
        "medium",
    ),
    SecretPattern(
        "Twilio API Key",
        re.compile(r"SK[0-9a-fA-F]{32}"),
        "high",
    ),
    SecretPattern(
        "Twilio Account SID",
        re.compile(r"AC[0-9a-fA-F]{32}"),
        "medium",
    ),
    SecretPattern(
        "SendGrid API Key",
        re.compile(r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}"),
        "high",
    ),
    SecretPattern(
        "JWT Token",
        re.compile(r"eyJ[A-Za-z0-9\-_=]{10,}\.eyJ[A-Za-z0-9\-_=]{10,}\.[A-Za-z0-9\-_.+/=]{10,}"),
        "high",
    ),
    SecretPattern(
        "Bearer Token (header)",
        re.compile(r"(?i)(?:authorization|auth)[^\n:=]*[=:]\s*['\"]?Bearer\s+([A-Za-z0-9\-._~+/=]{20,})['\"]?"),
        "high",
    ),
    SecretPattern(
        "Basic Auth (base64 encoded)",
        re.compile(r"(?i)(?:authorization)[^\n:=]*[=:]\s*['\"]?Basic\s+([A-Za-z0-9+/=]{20,})['\"]?"),
        "high",
    ),
    SecretPattern(
        "Password in URL",
        re.compile(r"(?i)(?:https?|ftp|jdbc)://[^/\s]*:[^/\s@]+@[^/\s]+"),
        "critical",
    ),
    SecretPattern(
        "Database Connection String",
        re.compile(
            r"(?i)(?:mongodb|postgres|postgresql|mysql|mssql|sqlserver|redis)"
            r"://[^\s'\"\n]{8,}",
        ),
        "critical",
    ),
    SecretPattern(
        "Generic Password Assignment",
        re.compile(
            r"(?i)(?:password|passwd|pwd|secret|token|apikey|api_key|auth_key)"
            r"[^\n:=]*[=:]\s*['\"]([^'\"\s]{8,})['\"]",
            re.MULTILINE,
        ),
        "medium",
    ),
    SecretPattern(
        "SSH Private Key Content",
        re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]+?-----END OPENSSH PRIVATE KEY-----"),
        "critical",
    ),
    SecretPattern(
        "Heroku API Key",
        re.compile(r"(?i)heroku[^\n]*[=:]\s*['\"]?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})['\"]?"),
        "high",
    ),
    SecretPattern(
        "npm Auth Token",
        re.compile(r"(?i)(?:npm_token|//registry\.npmjs\.org/:_authToken)[^\n:=]*[=:]\s*['\"]?([A-Za-z0-9\-_]{36})['\"]?"),
        "high",
    ),
    SecretPattern(
        "Docker Registry Auth",
        re.compile(r'"auths"\s*:\s*\{[^}]*"auth"\s*:\s*"([A-Za-z0-9+/=]{20,})"'),
        "high",
    ),
]


# ---------------------------------------------------------------------------
# High-entropy string detection (fallback for unknown key formats)
# ---------------------------------------------------------------------------

_HIGH_ENTROPY_MIN_LEN = 20
_HIGH_ENTROPY_THRESHOLD = 4.5  # Shannon entropy bits-per-char

_B64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _shannon_entropy(text: str) -> float:
    """Return Shannon entropy (bits per character) of *text*."""
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for c in text:
        freq[c] = freq.get(c, 0) + 1
    n = len(text)
    return -sum((count / n) * math.log2(count / n) for count in freq.values())


def _find_high_entropy_strings(content: str) -> list[tuple[int, str, float]]:
    """Return (line_no, token, entropy) for high-entropy tokens.

    Only reports tokens that look like base64 or hex strings so false
    positives from natural-language prose are minimised.
    """
    results = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        for token in re.findall(r"[A-Za-z0-9+/=_\-]{20,}", line):
            chars = set(token)
            is_b64 = chars.issubset(_B64_CHARS) and len(token) >= _HIGH_ENTROPY_MIN_LEN
            is_hex = chars.issubset(_HEX_CHARS) and len(token) >= 32
            if not (is_b64 or is_hex):
                continue
            entropy = _shannon_entropy(token)
            if entropy >= _HIGH_ENTROPY_THRESHOLD:
                results.append((lineno, token, entropy))
    return results


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------

@dataclass
class SecretFinding:
    pattern_name: str
    severity: str
    file_path: str
    line_no: int
    snippet: str
    entropy: float = 0.0

    def as_text(self) -> str:
        sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(self.severity, "⚪")
        lines = [
            f"{sev_icon} [{self.severity.upper()}] {self.pattern_name}",
            f"   File: {self.file_path}:{self.line_no}",
            f"   Match: {self.snippet}",
        ]
        if self.entropy:
            lines.append(f"   Entropy: {self.entropy:.2f} bits/char")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scanner core
# ---------------------------------------------------------------------------

_BINARY_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".png", ".jpg",
    ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".zip", ".tar", ".gz", ".bz2",
    ".xz", ".7z", ".rar", ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".woff",
    ".woff2", ".ttf", ".otf", ".eot", ".pyc", ".pyo",
})


def _is_binary_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in _BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8192)
    except OSError:
        return True


def _scan_content(
    content: str,
    source: str,
    include_entropy: bool,
    max_findings: int,
) -> list[SecretFinding]:
    findings: list[SecretFinding] = []

    lines = content.splitlines()
    for pattern in _PATTERNS:
        for m in pattern.regex.finditer(content):
            if len(findings) >= max_findings:
                break
            # Compute 1-based line number from match start offset
            lineno = content[:m.start()].count("\n") + 1
            raw_snippet = m.group(0)
            # Redact long secret values to avoid leaking them verbatim
            if len(raw_snippet) > 80:
                snippet = raw_snippet[:40] + "…[redacted]…" + raw_snippet[-10:]
            else:
                snippet = raw_snippet
            findings.append(SecretFinding(
                pattern_name=pattern.name,
                severity=pattern.severity,
                file_path=source,
                line_no=lineno,
                snippet=snippet,
            ))

    if include_entropy and len(findings) < max_findings:
        for lineno, token, entropy in _find_high_entropy_strings(content):
            if len(findings) >= max_findings:
                break
            redacted = token[:20] + "…" if len(token) > 20 else token
            findings.append(SecretFinding(
                pattern_name="High-entropy string",
                severity="medium",
                file_path=source,
                line_no=lineno,
                snippet=redacted,
                entropy=entropy,
            ))

    return findings


def _scan_file(
    path: Path,
    include_entropy: bool,
    max_findings: int,
) -> list[SecretFinding]:
    if _is_binary_file(path):
        return []
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return []
    return _scan_content(content, str(path), include_entropy, max_findings)


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

def _run_scan(
    target: str,
    recursive: bool = True,
    include_entropy: bool = False,
    max_findings: int = 50,
    max_file_size_kb: int = 512,
) -> str:
    """Core scanning logic (callable directly from tests without the FunctionTool wrapper)."""
    findings: list[SecretFinding] = []
    max_bytes = max_file_size_kb * 1024

    path_candidate = Path(target)
    try:
        _path_exists = path_candidate.exists()
    except OSError:
        _path_exists = False
    if _path_exists:
        if path_candidate.is_file():
            if path_candidate.stat().st_size <= max_bytes:
                findings = _scan_file(path_candidate, include_entropy, max_findings)
            else:
                size_kb = path_candidate.stat().st_size // 1024
                return (
                    f"[scan_for_secrets] File too large ({size_kb} KB > {max_file_size_kb} KB). "
                    "Increase max_file_size_kb."
                )
        elif path_candidate.is_dir():
            glob_fn = path_candidate.rglob("*") if recursive else path_candidate.glob("*")
            for fpath in sorted(glob_fn):
                if len(findings) >= max_findings:
                    break
                if not fpath.is_file():
                    continue
                try:
                    if fpath.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                findings.extend(
                    _scan_file(fpath, include_entropy, max_findings - len(findings))
                )
        else:
            return f"[scan_for_secrets] {target!r} is neither a file nor a directory."
    else:
        findings = _scan_content(target, "<stdin>", include_entropy, max_findings)

    if not findings:
        msg = f"No secrets detected in {target!r}."
        if not include_entropy:
            msg += " (run with include_entropy=True for broader coverage)"
        return msg

    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (sev_order.get(f.severity, 4), f.file_path, f.line_no))

    lines = [f"[scan_for_secrets] Found {len(findings)} potential secret(s) in {target!r}:\n"]
    for finding in findings:
        lines.append(finding.as_text())
        lines.append("")

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary = ", ".join(
        f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: sev_order.get(x[0], 4))
    )
    lines.append(f"Summary: {summary}")
    if len(findings) >= max_findings:
        lines.append(
            f"(Output capped at {max_findings} findings. "
            "Increase max_findings or narrow the target.)"
        )

    return "\n".join(lines)


@function_tool
def scan_for_secrets(
    target: str,
    recursive: bool = True,
    include_entropy: bool = False,
    max_findings: int = 50,
    max_file_size_kb: int = 512,
) -> str:
    """Scan files, directories, or raw text for exposed secrets and credentials.

    Detects AWS keys, GitHub tokens, Slack tokens, database URLs, private keys,
    JWT tokens, generic password assignments, and more (~25 pattern families).

    Args:
        target: A file path, directory path, or raw text/command output to scan.
                Directories are walked recursively when recursive=True.
        recursive: Walk directories recursively (default True).
        include_entropy: Also flag high-entropy strings (base64/hex tokens with
                Shannon entropy ≥ 4.5 bits/char). More noisy but catches custom
                secret formats. Default False.
        max_findings: Stop after this many findings to prevent flooding.
        max_file_size_kb: Skip files larger than this many kilobytes (default 512).

    Returns:
        A formatted report of all findings grouped by severity, or a "no findings"
        message when nothing is detected.
    """
    return _run_scan(target, recursive, include_entropy, max_findings, max_file_size_kb)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "scan_for_secrets",
    scan_for_secrets,
    categories=["recon", "misc"],
)
