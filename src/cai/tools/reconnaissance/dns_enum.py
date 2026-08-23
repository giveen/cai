"""DNS enumeration tool for red team reconnaissance.

Queries multiple DNS record types for a domain and optionally attempts
zone transfer and reverse-lookup enumeration.  Falls back to subprocess
``dig`` when dnspython is unavailable.
"""

from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass, field
from typing import Literal

from cai.sdk.agents import function_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RecordType = Literal["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "SRV", "PTR"]

_ALL_TYPES: tuple[str, ...] = ("A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA", "SRV")


@dataclass
class DnsRecord:
    rtype: str
    value: str


@dataclass
class DnsResult:
    domain: str
    records: list[DnsRecord] = field(default_factory=list)
    zone_transfer: str = ""
    errors: list[str] = field(default_factory=list)


def _query_dnspython(domain: str, rtype: str) -> list[str]:
    """Return record values using dnspython."""
    import dns.resolver  # type: ignore[import]
    import dns.exception  # type: ignore[import]

    try:
        answers = dns.resolver.resolve(domain, rtype, lifetime=5)
        return [r.to_text() for r in answers]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.Timeout:
        return []
    except Exception:
        return []


def _query_dig(domain: str, rtype: str) -> list[str]:
    """Fallback: parse ``dig +short`` output."""
    try:
        result = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", rtype, domain],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        return lines
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


def _try_zone_transfer(domain: str, nameservers: list[str]) -> str:
    """Attempt AXFR zone transfer against discovered name servers."""
    import dns.query  # type: ignore[import]
    import dns.zone  # type: ignore[import]
    import dns.exception  # type: ignore[import]

    for ns in nameservers[:3]:  # limit to first 3 NS
        ns_host = ns.rstrip(".")
        try:
            ns_ip = socket.gethostbyname(ns_host)
            z = dns.zone.from_xfr(dns.query.xfr(ns_ip, domain, timeout=5))
            lines = [f"Zone transfer succeeded from {ns_host}:"]
            for name, node in sorted(z.nodes.items()):
                lines.append(f"  {name} {node.to_text(z.origin)}")
            return "\n".join(lines)
        except (dns.exception.FormError, EOFError, ConnectionRefusedError, OSError):
            continue
        except Exception:
            continue
    return ""


def _reverse_lookup(ip: str) -> str:
    """PTR lookup for a single IP."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, OSError):
        return ""


# ---------------------------------------------------------------------------
# Core implementation
# ---------------------------------------------------------------------------

def _run_dns_enum(
    domain: str,
    record_types: str = "all",
    zone_transfer: bool = True,
    reverse_lookup: bool = True,
) -> str:
    """Enumerate DNS records for *domain* and return a formatted report.

    Args:
        domain: Domain name to enumerate (e.g. ``example.com``).
        record_types: Comma-separated record types, or ``"all"`` (default) for
            A, AAAA, MX, NS, TXT, CNAME, SOA, SRV.
        zone_transfer: Attempt AXFR zone transfer against discovered NS records.
        reverse_lookup: Perform PTR lookups on discovered A/AAAA records.
    """
    domain = domain.strip().rstrip(".")

    # Determine which record types to query
    if record_types.lower() == "all":
        types = list(_ALL_TYPES)
    else:
        types = [t.strip().upper() for t in record_types.split(",") if t.strip()]

    # Choose query backend
    try:
        import dns.resolver  # noqa: F401  — just to confirm available
        query_fn = _query_dnspython
    except ImportError:
        query_fn = _query_dig

    result = DnsResult(domain=domain)

    for rtype in types:
        values = query_fn(domain, rtype)
        for v in values:
            result.records.append(DnsRecord(rtype=rtype, value=v))

    # Zone transfer
    if zone_transfer:
        ns_records = [r.value.rstrip(".") for r in result.records if r.rtype == "NS"]
        if ns_records:
            try:
                zt = _try_zone_transfer(domain, ns_records)
                result.zone_transfer = zt
            except Exception:
                pass

    # Reverse lookups on A records
    ptr_lines: list[str] = []
    if reverse_lookup:
        a_ips = [r.value for r in result.records if r.rtype in ("A", "AAAA")]
        for ip in a_ips[:10]:  # limit to first 10
            ptr = _reverse_lookup(ip)
            if ptr:
                ptr_lines.append(f"  {ip} → {ptr}")

    # Build report
    lines = [f"[dns_enum] DNS enumeration of {domain}\n"]

    if not result.records:
        lines.append("No records found.")
        return "\n".join(lines)

    # Group by type
    by_type: dict[str, list[str]] = {}
    for rec in result.records:
        by_type.setdefault(rec.rtype, []).append(rec.value)

    for rtype in _ALL_TYPES:
        if rtype not in by_type:
            continue
        lines.append(f"--- {rtype} ---")
        for v in by_type[rtype]:
            lines.append(f"  {v}")

    # Extra types not in _ALL_TYPES order
    for rtype, values in by_type.items():
        if rtype not in _ALL_TYPES:
            lines.append(f"--- {rtype} ---")
            for v in values:
                lines.append(f"  {v}")

    if ptr_lines:
        lines.append("\n--- PTR (reverse lookup) ---")
        lines.extend(ptr_lines)

    if result.zone_transfer:
        lines.append("\n" + result.zone_transfer)
    else:
        ns_records = [r.value for r in result.records if r.rtype == "NS"]
        if ns_records and zone_transfer:
            lines.append("\n--- Zone transfer: refused by all name servers ---")

    return "\n".join(lines)


@function_tool
def dns_enum(
    domain: str,
    record_types: str = "all",
    zone_transfer: bool = True,
    reverse_lookup: bool = True,
) -> str:
    """Enumerate DNS records for a domain.

    Queries common DNS record types and optionally attempts zone transfer
    (AXFR) and PTR (reverse) lookups on discovered IPs.

    Args:
        domain: Domain to enumerate (e.g. ``target.com``).
        record_types: Comma-separated record types to query, or ``"all"``
            (default) for A, AAAA, MX, NS, TXT, CNAME, SOA, SRV.
        zone_transfer: Attempt AXFR zone transfer against discovered
            name servers (default True).  Most servers refuse it.
        reverse_lookup: Perform PTR lookups on discovered A/AAAA addresses
            (default True).

    Returns:
        Formatted report of all discovered records, zone transfer result
        (if successful), and PTR data.
    """
    return _run_dns_enum(domain, record_types, zone_transfer, reverse_lookup)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register(
    "dns_enum",
    dns_enum,
    categories=["recon", "network"],
)
