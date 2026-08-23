"""SSL/TLS certificate inspector for web reconnaissance.

Inspects TLS certificates and cipher suite configuration without
any third-party dependencies — uses only the stdlib ssl module.
"""

from __future__ import annotations

import datetime
import json
import socket
import ssl
from dataclasses import dataclass, field
from typing import Any

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CertInfo:
    host: str
    port: int
    subject: dict[str, str] = field(default_factory=dict)
    issuer: dict[str, str] = field(default_factory=dict)
    san: list[str] = field(default_factory=list)
    not_before: str = ""
    not_after: str = ""
    days_until_expiry: int | None = None
    expired: bool = False
    self_signed: bool = False
    tls_version: str = ""
    cipher: str = ""
    bits: int = 0
    serial: str = ""


def _parse_rdn(rdn: tuple[tuple[str, str], ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in rdn:
        result[pair[0]] = pair[1]
    return result


def _parse_cert(cert: dict[str, Any], host: str, port: int) -> CertInfo:
    info = CertInfo(host=host, port=port)

    subject_rdn = cert.get("subject", ())
    issuer_rdn = cert.get("issuer", ())
    info.subject = _parse_rdn(sum(subject_rdn, ()))
    info.issuer = _parse_rdn(sum(issuer_rdn, ()))

    info.self_signed = info.subject == info.issuer

    san_raw = cert.get("subjectAltName", ())
    info.san = [v for _, v in san_raw]

    info.not_before = cert.get("notBefore", "")
    info.not_after = cert.get("notAfter", "")

    info.serial = hex(cert.get("serialNumber", 0))

    if info.not_after:
        try:
            expiry = datetime.datetime.strptime(info.not_after, "%b %d %H:%M:%S %Y %Z")
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            delta = expiry - now
            info.days_until_expiry = delta.days
            info.expired = delta.days < 0
        except ValueError:
            pass

    return info


def _run_ssl_inspect(host: str, port: int = 443, timeout: float = 10.0) -> str:
    """Core SSL inspection logic (callable directly in tests)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        raw_sock = socket.create_connection((host, port), timeout=timeout)
    except (socket.gaierror, OSError) as exc:
        return f"[ssl_inspector] Cannot connect to {host}:{port}: {exc}"

    try:
        with ctx.wrap_socket(raw_sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert(binary_form=False) or {}
            tls_version = ssock.version() or ""
            cipher_name, _, bits = ssock.cipher() or ("", "", 0)

            info = _parse_cert(cert, host, port)
            info.tls_version = tls_version
            info.cipher = cipher_name
            info.bits = bits or 0
    except ssl.SSLError as exc:
        return f"[ssl_inspector] SSL handshake failed for {host}:{port}: {exc}"
    finally:
        raw_sock.close()

    # Build report
    lines = [f"[ssl_inspector] TLS inspection of {host}:{port}\n"]

    lines.append(f"TLS version   : {info.tls_version}")
    lines.append(f"Cipher suite  : {info.cipher} ({info.bits}-bit)")
    lines.append("")

    cn = info.subject.get("commonName") or info.subject.get("CN", "n/a")
    lines.append(f"Subject CN    : {cn}")
    lines.append(f"Issuer        : {info.issuer.get('O', info.issuer.get('organizationName', 'n/a'))}")
    lines.append(f"Self-signed   : {'YES (no trusted CA)' if info.self_signed else 'No'}")
    lines.append(f"Serial number : {info.serial}")
    lines.append("")

    lines.append(f"Valid from    : {info.not_before}")
    lines.append(f"Expires       : {info.not_after}")
    if info.expired:
        lines.append("** CERTIFICATE IS EXPIRED **")
    elif info.days_until_expiry is not None:
        if info.days_until_expiry <= 30:
            lines.append(f"** EXPIRING SOON: {info.days_until_expiry} days remaining **")
        else:
            lines.append(f"Days until expiry: {info.days_until_expiry}")
    lines.append("")

    if info.san:
        lines.append(f"Subject Alt Names ({len(info.san)}):")
        for name in info.san[:20]:
            lines.append(f"  {name}")
        if len(info.san) > 20:
            lines.append(f"  ... and {len(info.san) - 20} more")
    else:
        lines.append("Subject Alt Names: none (CN-only cert)")

    # Weak-cipher warnings
    weak_ciphers = {"RC4", "DES", "3DES", "EXPORT", "NULL", "anon"}
    for wc in weak_ciphers:
        if wc in (info.cipher or "").upper():
            lines.append(f"\n** WEAK CIPHER DETECTED: {info.cipher} **")
            break

    if info.tls_version in ("TLSv1", "TLSv1.1", "SSLv2", "SSLv3"):
        lines.append(f"\n** DEPRECATED PROTOCOL: {info.tls_version} **")

    return "\n".join(lines)


@function_tool
def ssl_inspector(
    host: str,
    port: int = 443,
    timeout: float = 10.0,
) -> str:
    """Inspect the TLS certificate and cipher configuration of a host.

    Uses the stdlib ssl module — no external dependencies.  Checks for:
    expired certificates, upcoming expiry (≤30 days), self-signed certs,
    Subject Alternative Names, weak ciphers (RC4, DES, EXPORT, …),
    and deprecated protocol versions (TLS 1.0/1.1).

    Args:
        host: Hostname or IP address to inspect.
        port: TLS port (default 443).
        timeout: Connection timeout in seconds (default 10.0).

    Returns:
        Formatted certificate and cipher report with security warnings.
    """
    return _run_ssl_inspect(host, port, timeout)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "ssl_inspector",
    ssl_inspector,
    categories=["recon", "web"],
)
