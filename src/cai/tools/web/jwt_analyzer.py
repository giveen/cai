"""JWT (JSON Web Token) analyzer and decoder.

Decodes and analyzes JWTs without requiring the signing key.
Flags common vulnerabilities (algorithm none, weak algorithms, missing
claims, long expiry, etc.).  Stdlib-only: base64 + json.
"""

from __future__ import annotations

import base64
import json
import datetime
import re
from typing import Any

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64_decode_url(segment: str) -> bytes:
    """Base64url-decode a JWT segment, adding padding as needed."""
    segment = segment.replace("-", "+").replace("_", "/")
    padding = 4 - len(segment) % 4
    if padding != 4:
        segment += "=" * padding
    return base64.b64decode(segment)


def _parse_segment(segment: str) -> tuple[dict[str, Any] | None, str]:
    """Return (parsed_dict_or_none, raw_bytes_hex_on_failure)."""
    try:
        raw = _b64_decode_url(segment)
        return json.loads(raw.decode("utf-8")), ""
    except Exception as exc:
        return None, str(exc)


def _check_alg(alg: str) -> list[str]:
    warnings: list[str] = []
    alg_upper = alg.upper()
    if alg_upper == "NONE":
        warnings.append("CRITICAL: algorithm is 'none' — no signature validation!")
    elif alg_upper in ("HS256", "HS384", "HS512"):
        warnings.append(f"INFO: symmetric algorithm ({alg}) — secret must be strong")
    elif alg_upper.startswith("RS") or alg_upper.startswith("PS"):
        pass  # RSA — generally safe
    elif alg_upper.startswith("ES"):
        pass  # ECDSA — generally safe
    else:
        warnings.append(f"WARNING: uncommon algorithm '{alg}' — verify it is supported securely")
    return warnings


def _check_claims(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()

    # exp
    exp = payload.get("exp")
    if exp is None:
        warnings.append("WARNING: no 'exp' claim — token never expires")
    else:
        try:
            exp_dt = datetime.datetime.fromtimestamp(float(exp), tz=datetime.timezone.utc)
            if float(exp) < now:
                warnings.append(f"EXPIRED: token expired at {exp_dt.isoformat()}")
            else:
                ttl_days = (float(exp) - now) / 86400
                if ttl_days > 365:
                    warnings.append(
                        f"WARNING: very long expiry ({ttl_days:.0f} days remaining)"
                    )
        except (ValueError, OverflowError, OSError):
            warnings.append("WARNING: 'exp' value is not a valid timestamp")

    # nbf
    nbf = payload.get("nbf")
    if nbf is not None:
        try:
            if float(nbf) > now:
                nbf_dt = datetime.datetime.fromtimestamp(float(nbf), tz=datetime.timezone.utc)
                warnings.append(f"INFO: token not yet valid (nbf = {nbf_dt.isoformat()})")
        except (ValueError, OverflowError, OSError):
            pass

    # iat
    iat = payload.get("iat")
    if iat is None:
        warnings.append("INFO: no 'iat' claim — issued-at not tracked")

    # sub / iss
    if "sub" not in payload:
        warnings.append("INFO: no 'sub' claim")
    if "iss" not in payload:
        warnings.append("INFO: no 'iss' claim")

    return warnings


def _fmt_dict(d: dict[str, Any], indent: int = 2) -> str:
    lines = []
    pad = " " * indent
    for k, v in d.items():
        # Pretty-print timestamp claims
        if k in ("exp", "iat", "nbf") and isinstance(v, (int, float)):
            try:
                dt = datetime.datetime.fromtimestamp(float(v), tz=datetime.timezone.utc)
                lines.append(f"{pad}{k}: {v}  ({dt.isoformat()})")
                continue
            except Exception:
                pass
        lines.append(f"{pad}{k}: {json.dumps(v, ensure_ascii=False)}")
    return "\n".join(lines)


def _run_jwt_analyze(token: str) -> str:
    """Core JWT analysis logic (callable directly in tests)."""
    token = token.strip()
    if not token:
        return "[jwt_analyzer] Error: empty token"

    parts = token.split(".")
    if len(parts) != 3:
        return (
            f"[jwt_analyzer] Error: expected 3 dot-separated parts, got {len(parts)}.\n"
            "If this is a JWE (encrypted JWT), decryption is not supported."
        )

    header_raw, payload_raw, signature_raw = parts

    header, h_err = _parse_segment(header_raw)
    payload, p_err = _parse_segment(payload_raw)

    lines: list[str] = ["[jwt_analyzer] JWT Analysis\n"]

    # --- Header ---
    lines.append("=== Header ===")
    if header is None:
        lines.append(f"  (decode failed: {h_err})")
    else:
        lines.append(_fmt_dict(header))

    # --- Payload ---
    lines.append("\n=== Payload ===")
    if payload is None:
        lines.append(f"  (decode failed: {p_err})")
    else:
        lines.append(_fmt_dict(payload))

    # --- Signature ---
    lines.append("\n=== Signature ===")
    if signature_raw:
        try:
            sig_bytes = _b64_decode_url(signature_raw)
            lines.append(f"  {len(sig_bytes)} bytes  (hex: {sig_bytes[:16].hex()}{'…' if len(sig_bytes) > 16 else ''})")
        except Exception:
            lines.append(f"  (raw) {signature_raw[:60]}")
    else:
        lines.append("  EMPTY — token is unsigned!")

    # --- Security analysis ---
    lines.append("\n=== Security Analysis ===")
    warnings: list[str] = []

    if header:
        alg = header.get("alg", "")
        if alg:
            warnings.extend(_check_alg(str(alg)))
        else:
            warnings.append("CRITICAL: no 'alg' header — algorithm unknown")

        kid = header.get("kid")
        if kid:
            warnings.append(f"INFO: 'kid' = {kid!r} — ensure key ID is validated server-side")

        jku = header.get("jku") or header.get("x5u")
        if jku:
            warnings.append(
                f"CRITICAL: remote key URL in header ({jku!r}) — if accepted by the server, "
                "this enables key injection attacks"
            )

    if not signature_raw:
        warnings.append("CRITICAL: signature is empty — token may be accepted with alg=none")

    if payload:
        warnings.extend(_check_claims(payload))

    if warnings:
        for w in warnings:
            lines.append(f"  {w}")
    else:
        lines.append("  No obvious issues detected.")

    # --- Attack surface summary ---
    lines.append("\n=== Potential Attack Vectors ===")
    attacks: list[str] = []
    if header and str(header.get("alg", "")).upper() in ("HS256", "HS384", "HS512"):
        attacks.append("Brute-force / weak-secret attack on HMAC key (try jwt-cracker / hashcat mode 16500)")
        attacks.append("Algorithm confusion: change alg to RS256 and sign with public key")
    if header and str(header.get("alg", "")).upper() == "NONE":
        attacks.append("Remove signature and set alg=none to bypass validation")
    if header and ("jku" in header or "x5u" in header):
        attacks.append("Inject a crafted JWK Set URL in the jku/x5u header")

    if attacks:
        for a in attacks:
            lines.append(f"  • {a}")
    else:
        lines.append("  No specific vectors identified from static analysis alone.")

    return "\n".join(lines)


@function_tool
def jwt_analyzer(token: str) -> str:
    """Decode and analyze a JSON Web Token (JWT) for security issues.

    Decodes the header and payload without needing the signing key.
    Flags common vulnerabilities including:
    - Algorithm 'none' (unsigned token accepted)
    - Remote key URL injection (jku/x5u headers)
    - Missing, expired, or overly long expiry claims
    - Symmetric HMAC algorithms (brute-forceable if weak secret)
    - Empty or suspicious signature segments

    Args:
        token: The raw JWT string (three base64url parts separated by dots).

    Returns:
        Decoded header/payload with security warnings and suggested attack vectors.
    """
    return _run_jwt_analyze(token)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "jwt_analyzer",
    jwt_analyzer,
    categories=["recon", "web", "exploitation"],
)
