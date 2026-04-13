"""Cerebro Protocol Intelligence Engine (CPIE).

Structured network auditing, topology mapping, and packet intelligence for the
Cerebro-AI suite.

Responsibilities
----------------
* Maintain a stateful network map of discovered hosts, ports, services, and
  traffic volume.
* Distinguish internal vs external zones using COSE mission scope data.
* Audit established connections and related PIDs without disrupting traffic.
* Perform DPI using optional backends (scapy / pyshark) with a built-in parser
  fallback so analysis still works on bare Ubuntu environments.
* Detect beaconing, exfiltration, and ARP poisoning indicators.
* Export topology and sample hashes to /workspace/loot/network/topology.json via
  PathGuard-backed writer.
* Provide MODE_CRITIQUE evasion guidance when probing is reset or blocked.

Back-compat
-----------
``process()`` is preserved for callers that still expect a simple status dict.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import socket
import struct
import threading
import time
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from cai.tools.reconnaissance.filesystem import PathGuard as FilesystemPathGuard

try:
    import psutil  # type: ignore
    _PSUTIL_AVAILABLE = True
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]
    _PSUTIL_AVAILABLE = False

try:
    import scapy.all as scapy  # type: ignore
    _SCAPY_AVAILABLE = True
except Exception:  # pragma: no cover
    scapy = None  # type: ignore[assignment]
    _SCAPY_AVAILABLE = False

try:
    import pyshark  # type: ignore
    _PYSHARK_AVAILABLE = True
except Exception:  # pragma: no cover
    pyshark = None  # type: ignore[assignment]
    _PYSHARK_AVAILABLE = False

try:
    import cupy as cp  # type: ignore
    _CUPY_AVAILABLE = True
except Exception:  # pragma: no cover
    cp = None  # type: ignore[assignment]
    _CUPY_AVAILABLE = False

try:
    import numpy as np  # type: ignore
    _NUMPY_AVAILABLE = True
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _NUMPY_AVAILABLE = False

try:
    from cai.agents.usecase import MissionProfile
except Exception:  # pragma: no cover
    MissionProfile = Any  # type: ignore[misc,assignment]

try:
    from cai.tools.misc.reasoning import MODE_CRITIQUE, REASONING_TOOL
    _REASONING_AVAILABLE = True
except Exception:  # pragma: no cover
    MODE_CRITIQUE = "MODE_CRITIQUE"
    REASONING_TOOL = None  # type: ignore[assignment]
    _REASONING_AVAILABLE = False


_CPIE_LOGGER = logging.getLogger("cai.cpie")

_DEFAULT_WORKSPACE = Path(os.getenv("CIR_WORKSPACE", "/workspace")).resolve()
_TOPOLOGY_PATH = "loot/network/topology.json"
_FLOW_LOG_CAPACITY = int(os.getenv("CPIE_FLOW_LOG_CAPACITY", "200000"))
_BEACON_WINDOW = int(os.getenv("CPIE_BEACON_WINDOW", "12"))
_EXFIL_BYTES_THRESHOLD = int(os.getenv("CPIE_EXFIL_BYTES_THRESHOLD", str(50 * 1024 * 1024)))
_CRITICALITY_HIGH = 0.75


@dataclass
class ServiceFingerprint:
    port: int
    protocol: str
    service: str
    banner: str = ""
    pid: Optional[int] = None


@dataclass
class HostRecord:
    host: str
    zone: str
    first_seen: str
    last_seen: str
    open_ports: List[int] = field(default_factory=list)
    services: List[ServiceFingerprint] = field(default_factory=list)
    traffic_bytes: int = 0
    sample_hashes: List[str] = field(default_factory=list)
    asset_criticality: float = 0.0
    tags: List[str] = field(default_factory=list)


@dataclass
class ConnectionRecord:
    local_address: str
    remote_address: str
    status: str
    pid: Optional[int]
    process_name: str = "unknown"
    zone: str = "unknown"


@dataclass
class TrafficObservation:
    timestamp: float
    src_ip: str
    dst_ip: str
    protocol: str
    src_port: Optional[int]
    dst_port: Optional[int]
    payload_size: int
    flags: str = ""
    sample_sha256: str = ""
    banner: str = ""
    mac_src: str = ""
    mac_dst: str = ""


@dataclass
class NetworkAlert:
    alert_type: str
    severity: str
    message: str
    host: Optional[str] = None
    critique: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


@dataclass
class PacketParseResult:
    observations: List[TrafficObservation]
    raw_sha256: str
    anomalies: List[NetworkAlert]


class _NetworkPathGuardViolation(PermissionError):
    """Raised when CPIE tries to write outside the workspace."""


class _TopologyWriter:
    """PathGuard-backed topology exporter scoped to loot/network."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self._guard = FilesystemPathGuard(self.workspace_root, self._audit)
        self._lock = threading.Lock()

    def write_json(self, relative_path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        try:
            resolved = self._guard.validate_path(relative_path, action="cpie_write", mode="write")
        except Exception as exc:
            raise _NetworkPathGuardViolation(str(exc)) from exc
        resolved.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(dict(payload), ensure_ascii=True, indent=2, default=str)
        with self._lock:
            resolved.write_text(body, encoding="utf-8")
        return {"ok": True, "path": str(resolved), "bytes_written": len(body.encode("utf-8"))}

    @staticmethod
    def _audit(*_args: Any, **_kwargs: Any) -> None:
        pass


class CerebroNetworkEngine:
    """Stateful network topology and protocol intelligence engine."""

    def __init__(
        self,
        *,
        workspace_root: Optional[str] = None,
        mission_profile: Optional[MissionProfile] = None,
        retrospective_capacity: int = _FLOW_LOG_CAPACITY,
    ) -> None:
        self.workspace_root = Path(workspace_root or str(_DEFAULT_WORKSPACE)).resolve()
        self.mission_profile = mission_profile
        self._writer = _TopologyWriter(self.workspace_root)
        self._hosts: Dict[str, HostRecord] = {}
        self._flow_log: Deque[TrafficObservation] = deque(maxlen=max(1000, retrospective_capacity))
        self._alerts: Deque[NetworkAlert] = deque(maxlen=256)
        self._arp_claims: Dict[str, Set[str]] = defaultdict(set)
        self._beacon_history: Dict[Tuple[str, str, str, Optional[int]], Deque[float]] = defaultdict(lambda: deque(maxlen=_BEACON_WINDOW))
        self._lock = threading.Lock()

    async def discover_host(
        self,
        host: str,
        *,
        open_ports: Optional[Sequence[int]] = None,
        services: Optional[Sequence[ServiceFingerprint]] = None,
        traffic_bytes: int = 0,
    ) -> HostRecord:
        return await asyncio.to_thread(
            self._discover_host_sync,
            host,
            list(open_ports or []),
            list(services or []),
            int(traffic_bytes),
        )

    async def audit_established_connections(self) -> List[ConnectionRecord]:
        return await asyncio.to_thread(self._audit_established_connections_sync)

    async def ingest_raw_sample(
        self,
        raw_bytes: bytes,
        *,
        source_hint: str = "inline",
    ) -> PacketParseResult:
        return await asyncio.to_thread(self._ingest_raw_sample_sync, raw_bytes, source_hint)

    async def analyze_pcap(self, pcap_path: str) -> PacketParseResult:
        return await asyncio.to_thread(self._analyze_pcap_sync, pcap_path)

    async def export_topology(self) -> Dict[str, Any]:
        payload = await asyncio.to_thread(self._build_topology_payload)
        return await asyncio.to_thread(self._writer.write_json, _TOPOLOGY_PATH, payload)

    def get_network_map(self) -> Dict[str, HostRecord]:
        with self._lock:
            return {host: self._clone_host(record) for host, record in self._hosts.items()}

    def latest_alerts(self) -> List[NetworkAlert]:
        with self._lock:
            return list(self._alerts)

    def retrospective_search(self, indicator: str) -> List[TrafficObservation]:
        with self._lock:
            needle = indicator.lower()
            return [
                obs for obs in self._flow_log
                if needle in obs.src_ip.lower()
                or needle in obs.dst_ip.lower()
                or needle in obs.banner.lower()
                or needle in obs.sample_sha256.lower()
            ]

    def asset_criticality(self, host: str) -> float:
        with self._lock:
            record = self._hosts.get(host)
            return record.asset_criticality if record else 0.0

    def _discover_host_sync(
        self,
        host: str,
        open_ports: List[int],
        services: List[ServiceFingerprint],
        traffic_bytes: int,
    ) -> HostRecord:
        now = datetime.now(tz=UTC).isoformat()
        zone = self._classify_zone(host)
        with self._lock:
            record = self._hosts.get(host)
            if record is None:
                record = HostRecord(host=host, zone=zone, first_seen=now, last_seen=now)
                self._hosts[host] = record
            record.last_seen = now
            record.zone = zone
            record.traffic_bytes += max(0, traffic_bytes)
            merged_ports = set(record.open_ports)
            merged_ports.update(open_ports)
            record.open_ports = sorted(merged_ports)
            if services:
                existing = {(svc.port, svc.protocol, svc.service) for svc in record.services}
                for svc in services:
                    key = (svc.port, svc.protocol, svc.service)
                    if key not in existing:
                        record.services.append(svc)
                        existing.add(key)
            record.asset_criticality = self._score_asset_criticality(record)
            record.tags = self._derive_tags(record)
            return self._clone_host(record)

    def _audit_established_connections_sync(self) -> List[ConnectionRecord]:
        if not _PSUTIL_AVAILABLE or psutil is None:
            return []
        records: List[ConnectionRecord] = []
        try:
            connections = psutil.net_connections(kind="inet")
        except Exception as exc:
            _CPIE_LOGGER.debug("CPIE connection audit failed: %s", exc)
            return []
        for conn in connections:
            if conn.status != "ESTABLISHED":
                continue
            pid = conn.pid
            pname = "unknown"
            if pid:
                try:
                    pname = psutil.Process(pid).name()
                except Exception:
                    pass
            local_addr = self._format_addr(conn.laddr)
            remote_addr = self._format_addr(conn.raddr)
            zone = self._classify_zone(conn.raddr.ip) if conn.raddr else "internal"
            record = ConnectionRecord(
                local_address=local_addr,
                remote_address=remote_addr,
                status=conn.status,
                pid=pid,
                process_name=pname,
                zone=zone,
            )
            records.append(record)
            if conn.raddr:
                self._discover_host_sync(
                    conn.raddr.ip,
                    [conn.raddr.port],
                    [ServiceFingerprint(port=conn.raddr.port, protocol="tcp", service=pname, pid=pid)],
                    0,
                )
        return records

    def _ingest_raw_sample_sync(self, raw_bytes: bytes, source_hint: str) -> PacketParseResult:
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        observations = self._parse_frames(raw_bytes, sha256)
        anomalies = self._evaluate_observations(observations)
        with self._lock:
            for obs in observations:
                self._flow_log.append(obs)
                self._update_topology_from_observation(obs)
            for alert in anomalies:
                self._push_alert(alert)
        return PacketParseResult(observations=observations, raw_sha256=sha256, anomalies=anomalies)

    def _analyze_pcap_sync(self, pcap_path: str) -> PacketParseResult:
        path = Path(pcap_path)
        raw = path.read_bytes()
        if _SCAPY_AVAILABLE and scapy is not None:
            try:
                observations = self._parse_with_scapy(path)
                sha256 = hashlib.sha256(raw).hexdigest()
                anomalies = self._evaluate_observations(observations)
                with self._lock:
                    for obs in observations:
                        self._flow_log.append(obs)
                        self._update_topology_from_observation(obs)
                    for alert in anomalies:
                        self._push_alert(alert)
                return PacketParseResult(observations=observations, raw_sha256=sha256, anomalies=anomalies)
            except Exception as exc:
                _CPIE_LOGGER.debug("CPIE scapy parser failed: %s", exc)
        if _PYSHARK_AVAILABLE and pyshark is not None:
            try:
                observations = self._parse_with_pyshark(path)
                sha256 = hashlib.sha256(raw).hexdigest()
                anomalies = self._evaluate_observations(observations)
                with self._lock:
                    for obs in observations:
                        self._flow_log.append(obs)
                        self._update_topology_from_observation(obs)
                    for alert in anomalies:
                        self._push_alert(alert)
                return PacketParseResult(observations=observations, raw_sha256=sha256, anomalies=anomalies)
            except Exception as exc:
                _CPIE_LOGGER.debug("CPIE pyshark parser failed: %s", exc)
        return self._ingest_raw_sample_sync(raw, str(path))

    def _build_topology_payload(self) -> Dict[str, Any]:
        with self._lock:
            hosts = {
                host: {
                    "zone": record.zone,
                    "first_seen": record.first_seen,
                    "last_seen": record.last_seen,
                    "open_ports": record.open_ports,
                    "services": [
                        {
                            "port": svc.port,
                            "protocol": svc.protocol,
                            "service": svc.service,
                            "banner": svc.banner,
                            "pid": svc.pid,
                        }
                        for svc in record.services
                    ],
                    "traffic_bytes": record.traffic_bytes,
                    "sample_hashes": record.sample_hashes[-50:],
                    "asset_criticality": round(record.asset_criticality, 4),
                    "tags": record.tags,
                }
                for host, record in self._hosts.items()
            }
            alerts = [
                {
                    "alert_type": alert.alert_type,
                    "severity": alert.severity,
                    "message": alert.message,
                    "host": alert.host,
                    "critique": alert.critique,
                    "created_at": alert.created_at,
                }
                for alert in self._alerts
            ]
            flow_size = len(self._flow_log)
        return {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "hosts": hosts,
            "alerts": alerts,
            "retrospective_flow_log_size": flow_size,
            "gpu_acceleration": _CUPY_AVAILABLE,
        }

    def _evaluate_observations(self, observations: List[TrafficObservation]) -> List[NetworkAlert]:
        alerts: List[NetworkAlert] = []
        for obs in observations:
            self._track_beacon_pattern(obs)
            if obs.protocol == "ARP" and obs.banner:
                self._arp_claims[obs.dst_ip].add(obs.banner)
                if len(self._arp_claims[obs.dst_ip]) > 1:
                    alerts.append(self._make_alert(
                        "arp_poisoning",
                        "high",
                        f"Multiple MACs claim IP {obs.dst_ip}: {sorted(self._arp_claims[obs.dst_ip])}",
                        host=obs.dst_ip,
                    ))
            if obs.zone if hasattr(obs, 'zone') else False:
                pass
            if self._is_potential_exfil(obs):
                alerts.append(self._make_alert(
                    "data_exfiltration",
                    "high",
                    f"Outbound transfer {obs.payload_size} bytes from {obs.src_ip} to external {obs.dst_ip}",
                    host=obs.src_ip,
                ))
            if obs.flags and ("RST" in obs.flags or "UNREACHABLE" in obs.flags):
                alerts.append(self._make_alert(
                    "scan_blocked",
                    "medium",
                    f"Probe to {obs.dst_ip}:{obs.dst_port or 0} was reset or unreachable.",
                    host=obs.dst_ip,
                    critique=self._evasion_critique(obs),
                ))
        alerts.extend(self._detect_beacon_alerts())
        return alerts

    def _track_beacon_pattern(self, obs: TrafficObservation) -> None:
        key = (obs.src_ip, obs.dst_ip, obs.protocol, obs.dst_port)
        self._beacon_history[key].append(obs.timestamp)

    def _detect_beacon_alerts(self) -> List[NetworkAlert]:
        alerts: List[NetworkAlert] = []
        for key, timestamps in list(self._beacon_history.items()):
            if len(timestamps) < 4:
                continue
            intervals = [b - a for a, b in zip(timestamps, list(timestamps)[1:])]
            if not intervals:
                continue
            score = self._pattern_regularity_score(intervals)
            if score >= 0.9:
                src_ip, dst_ip, protocol, dst_port = key
                alerts.append(self._make_alert(
                    "c2_beaconing",
                    "high",
                    f"Highly regular {protocol} traffic from {src_ip} to {dst_ip}:{dst_port or 0}",
                    host=src_ip,
                ))
        return alerts

    def _pattern_regularity_score(self, intervals: List[float]) -> float:
        if len(intervals) < 2:
            return 0.0
        if _CUPY_AVAILABLE and cp is not None:
            arr = cp.asarray(intervals, dtype=cp.float32)
            mean = float(cp.mean(arr).get())
            std = float(cp.std(arr).get())
        elif _NUMPY_AVAILABLE and np is not None:
            arr = np.asarray(intervals, dtype=np.float32)
            mean = float(arr.mean())
            std = float(arr.std())
        else:
            mean = sum(intervals) / len(intervals)
            variance = sum((x - mean) ** 2 for x in intervals) / len(intervals)
            std = variance ** 0.5
        if mean <= 0.0:
            return 0.0
        coeff_var = std / mean
        return max(0.0, 1.0 - coeff_var)

    def _is_potential_exfil(self, obs: TrafficObservation) -> bool:
        return (
            self._classify_zone(obs.dst_ip) == "external"
            and obs.payload_size >= _EXFIL_BYTES_THRESHOLD
            and self._classify_zone(obs.src_ip) == "internal"
        )

    def _evasion_critique(self, obs: TrafficObservation) -> Optional[Dict[str, Any]]:
        base = {
            "recommendation": "Pivot to slower timing templates, lower parallelism, and vary source ports.",
            "host": obs.dst_ip,
            "port": obs.dst_port,
            "flags": obs.flags,
        }
        if _REASONING_AVAILABLE and REASONING_TOOL is not None:
            try:
                result = REASONING_TOOL.reason(
                    mode=MODE_CRITIQUE,
                    objective="Provide evasion critique for blocked or detected network probing",
                    context=f"Destination {obs.dst_ip}:{obs.dst_port or 0} returned {obs.flags}",
                    prior_output=(
                        "Suggest slower timing templates, different source ports, longer inter-packet jitter, "
                        "or protocol pivots that reduce scan visibility."
                    ),
                    options=[
                        "Slower timing templates",
                        "Alternate source ports",
                        "Reduced parallelism",
                        "Protocol pivot",
                    ],
                    fetch_facts=False,
                )
                base["mode_critique"] = result
            except Exception as exc:
                base["mode_critique_error"] = str(exc)
        return base

    def _update_topology_from_observation(self, obs: TrafficObservation) -> None:
        self._discover_host_sync(
            obs.src_ip,
            [obs.src_port] if obs.src_port else [],
            [],
            obs.payload_size,
        )
        dst_services = []
        if obs.dst_port:
            dst_services = [ServiceFingerprint(
                port=obs.dst_port,
                protocol=obs.protocol.lower(),
                service=self._service_guess(obs.dst_port, obs.banner),
                banner=obs.banner,
            )]
        record = self._discover_host_sync(
            obs.dst_ip,
            [obs.dst_port] if obs.dst_port else [],
            dst_services,
            obs.payload_size,
        )
        host_record = self._hosts.get(obs.dst_ip)
        if host_record is not None and obs.sample_sha256:
            if obs.sample_sha256 not in host_record.sample_hashes:
                host_record.sample_hashes.append(obs.sample_sha256)
                host_record.sample_hashes = host_record.sample_hashes[-200:]
            host_record.asset_criticality = self._score_asset_criticality(host_record)
            host_record.tags = self._derive_tags(host_record)

    def _score_asset_criticality(self, record: HostRecord) -> float:
        score = 0.0
        ports = set(record.open_ports)
        services = " ".join(f"{svc.service} {svc.banner}" for svc in record.services).lower()
        if {88, 389, 445}.issubset(ports) or "domain controller" in services or "kerberos" in services:
            score += 0.65
        if any(port in ports for port in (1433, 1521, 3306, 5432, 27017)) or "database" in services:
            score += 0.45
        if 5985 in ports or 5986 in ports or "winrm" in services:
            score += 0.15
        if record.traffic_bytes > 100 * 1024 * 1024:
            score += 0.20
        if record.zone == "internal":
            score += 0.05
        return min(1.0, score)

    def _derive_tags(self, record: HostRecord) -> List[str]:
        tags: Set[str] = set()
        ports = set(record.open_ports)
        services = " ".join(f"{svc.service} {svc.banner}" for svc in record.services).lower()
        if {88, 389, 445}.issubset(ports) or "domain controller" in services:
            tags.add("domain-controller")
        if any(port in ports for port in (1433, 3306, 5432, 27017)) or "database" in services:
            tags.add("database-server")
        if record.asset_criticality >= _CRITICALITY_HIGH:
            tags.add("high-value")
        if record.zone == "external":
            tags.add("target")
        else:
            tags.add("trusted")
        return sorted(tags)

    def _classify_zone(self, host: str) -> str:
        if not host:
            return "unknown"
        if self.mission_profile is None:
            if host.startswith(("10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "192.168.")):
                return "internal"
            return "external"
        try:
            ip_obj = ipaddress.ip_address(host)
            auth = self.mission_profile.authorized_surface
            for entry in getattr(auth, "ips", []):
                if entry == host:
                    return "external"
            for entry in getattr(auth, "cidr_ranges", []):
                try:
                    if ip_obj in ipaddress.ip_network(entry, strict=False):
                        return "external"
                except ValueError:
                    continue
            return "internal"
        except ValueError:
            domains = getattr(getattr(self.mission_profile, "authorized_surface", None), "domains", []) if self.mission_profile else []
            return "external" if host in domains else "internal"

    def _make_alert(
        self,
        alert_type: str,
        severity: str,
        message: str,
        *,
        host: Optional[str] = None,
        critique: Optional[Dict[str, Any]] = None,
    ) -> NetworkAlert:
        return NetworkAlert(alert_type=alert_type, severity=severity, message=message, host=host, critique=critique)

    def _push_alert(self, alert: NetworkAlert) -> None:
        if self._alerts and self._alerts[-1].alert_type == alert.alert_type and self._alerts[-1].message == alert.message:
            return
        self._alerts.append(alert)

    def _parse_frames(self, raw_bytes: bytes, sha256: str) -> List[TrafficObservation]:
        # Heuristic: try PCAP first, then raw ethernet frame.
        if len(raw_bytes) >= 24 and raw_bytes[:4] in {b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"}:
            return self._parse_pcap(raw_bytes, sha256)
        obs = self._parse_ethernet_frame(raw_bytes, time.time(), sha256)
        return [obs] if obs else []

    def _parse_pcap(self, raw_bytes: bytes, sha256: str) -> List[TrafficObservation]:
        observations: List[TrafficObservation] = []
        if len(raw_bytes) < 24:
            return observations
        magic = raw_bytes[:4]
        little = magic in {b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"}
        endian = "<" if little else ">"
        offset = 24
        while offset + 16 <= len(raw_bytes):
            ts_sec, ts_usec, incl_len, _orig_len = struct.unpack(endian + "IIII", raw_bytes[offset:offset + 16])
            offset += 16
            if offset + incl_len > len(raw_bytes):
                break
            packet = raw_bytes[offset:offset + incl_len]
            offset += incl_len
            timestamp = ts_sec + (ts_usec / 1_000_000.0)
            obs = self._parse_ethernet_frame(packet, timestamp, sha256)
            if obs:
                observations.append(obs)
        return observations

    def _parse_ethernet_frame(self, packet: bytes, timestamp: float, sha256: str) -> Optional[TrafficObservation]:
        if len(packet) < 14:
            return None
        dst_mac = self._mac(packet[0:6])
        src_mac = self._mac(packet[6:12])
        eth_type = struct.unpack("!H", packet[12:14])[0]
        payload = packet[14:]
        if eth_type == 0x0806:
            return self._parse_arp(payload, timestamp, sha256, src_mac, dst_mac)
        if eth_type == 0x0800:
            return self._parse_ipv4(payload, timestamp, sha256, src_mac, dst_mac)
        return None

    def _parse_arp(self, payload: bytes, timestamp: float, sha256: str, src_mac: str, dst_mac: str) -> Optional[TrafficObservation]:
        if len(payload) < 28:
            return None
        sender_mac = self._mac(payload[8:14])
        sender_ip = socket.inet_ntoa(payload[14:18])
        target_ip = socket.inet_ntoa(payload[24:28])
        return TrafficObservation(
            timestamp=timestamp,
            src_ip=sender_ip,
            dst_ip=target_ip,
            protocol="ARP",
            src_port=None,
            dst_port=None,
            payload_size=len(payload),
            sample_sha256=sha256,
            banner=sender_mac,
            mac_src=src_mac,
            mac_dst=dst_mac,
        )

    def _parse_ipv4(self, payload: bytes, timestamp: float, sha256: str, src_mac: str, dst_mac: str) -> Optional[TrafficObservation]:
        if len(payload) < 20:
            return None
        ihl = (payload[0] & 0x0F) * 4
        protocol_num = payload[9]
        src_ip = socket.inet_ntoa(payload[12:16])
        dst_ip = socket.inet_ntoa(payload[16:20])
        ip_payload = payload[ihl:]
        protocol = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(protocol_num, f"IP-{protocol_num}")
        src_port = None
        dst_port = None
        flags = ""
        banner = ""
        if protocol == "TCP" and len(ip_payload) >= 20:
            src_port, dst_port = struct.unpack("!HH", ip_payload[:4])
            tcp_flags = ip_payload[13]
            flags = self._tcp_flags(tcp_flags)
            banner = self._payload_banner(ip_payload[(ip_payload[12] >> 4) * 4:])
        elif protocol == "UDP" and len(ip_payload) >= 8:
            src_port, dst_port = struct.unpack("!HH", ip_payload[:4])
            banner = self._payload_banner(ip_payload[8:])
        elif protocol == "ICMP" and len(ip_payload) >= 2:
            icmp_type = ip_payload[0]
            icmp_code = ip_payload[1]
            flags = self._icmp_flag(icmp_type, icmp_code)
        return TrafficObservation(
            timestamp=timestamp,
            src_ip=src_ip,
            dst_ip=dst_ip,
            protocol=protocol,
            src_port=src_port,
            dst_port=dst_port,
            payload_size=len(ip_payload),
            flags=flags,
            sample_sha256=sha256,
            banner=banner,
            mac_src=src_mac,
            mac_dst=dst_mac,
        )

    def _parse_with_scapy(self, path: Path) -> List[TrafficObservation]:
        packets = scapy.rdpcap(str(path))
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        observations: List[TrafficObservation] = []
        for pkt in packets:
            if not pkt.haslayer("IP"):
                continue
            ip = pkt["IP"]
            protocol = "TCP" if pkt.haslayer("TCP") else "UDP" if pkt.haslayer("UDP") else "ICMP" if pkt.haslayer("ICMP") else str(ip.proto)
            src_port = int(pkt.sport) if hasattr(pkt, "sport") else None
            dst_port = int(pkt.dport) if hasattr(pkt, "dport") else None
            flags = str(pkt["TCP"].flags) if pkt.haslayer("TCP") else ""
            observations.append(TrafficObservation(
                timestamp=float(pkt.time),
                src_ip=str(ip.src),
                dst_ip=str(ip.dst),
                protocol=protocol,
                src_port=src_port,
                dst_port=dst_port,
                payload_size=len(bytes(pkt.payload)),
                flags=flags,
                sample_sha256=sha256,
                banner=self._payload_banner(bytes(pkt.payload)),
            ))
        return observations

    def _parse_with_pyshark(self, path: Path) -> List[TrafficObservation]:
        capture = pyshark.FileCapture(str(path), keep_packets=False)
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        observations: List[TrafficObservation] = []
        for pkt in capture:
            try:
                src_ip = pkt.ip.src
                dst_ip = pkt.ip.dst
            except Exception:
                continue
            protocol = str(pkt.highest_layer)
            src_port = int(getattr(pkt, protocol.lower()).srcport) if hasattr(getattr(pkt, protocol.lower(), None), "srcport") else None
            dst_port = int(getattr(pkt, protocol.lower()).dstport) if hasattr(getattr(pkt, protocol.lower(), None), "dstport") else None
            observations.append(TrafficObservation(
                timestamp=float(pkt.sniff_timestamp),
                src_ip=src_ip,
                dst_ip=dst_ip,
                protocol=protocol,
                src_port=src_port,
                dst_port=dst_port,
                payload_size=int(getattr(pkt, "length", 0)),
                sample_sha256=sha256,
            ))
        capture.close()
        return observations

    @staticmethod
    def _payload_banner(payload: bytes) -> str:
        try:
            text = payload[:120].decode("utf-8", errors="ignore")
        except Exception:
            return ""
        return " ".join(text.split())

    @staticmethod
    def _format_addr(addr: Any) -> str:
        if not addr:
            return ""
        return f"{addr.ip}:{addr.port}"

    @staticmethod
    def _mac(raw: bytes) -> str:
        return ":".join(f"{b:02x}" for b in raw)

    @staticmethod
    def _tcp_flags(value: int) -> str:
        flags = []
        if value & 0x01:
            flags.append("FIN")
        if value & 0x02:
            flags.append("SYN")
        if value & 0x04:
            flags.append("RST")
        if value & 0x08:
            flags.append("PSH")
        if value & 0x10:
            flags.append("ACK")
        if value & 0x20:
            flags.append("URG")
        return "|".join(flags)

    @staticmethod
    def _icmp_flag(icmp_type: int, icmp_code: int) -> str:
        if icmp_type == 3:
            return f"UNREACHABLE:{icmp_code}"
        return f"ICMP:{icmp_type}:{icmp_code}"

    @staticmethod
    def _service_guess(port: int, banner: str) -> str:
        if banner:
            lower = banner.lower()
            if "kerberos" in lower:
                return "kerberos"
            if "ldap" in lower:
                return "ldap"
            if "mysql" in lower:
                return "mysql"
            if "postgres" in lower:
                return "postgres"
        mapping = {
            53: "dns",
            80: "http",
            88: "kerberos",
            135: "rpc",
            139: "netbios",
            389: "ldap",
            443: "https",
            445: "smb",
            3306: "mysql",
            5432: "postgresql",
            1433: "mssql",
            5985: "winrm",
            5986: "winrm-ssl",
        }
        return mapping.get(port, "unknown")

    @staticmethod
    def _clone_host(record: HostRecord) -> HostRecord:
        return HostRecord(
            host=record.host,
            zone=record.zone,
            first_seen=record.first_seen,
            last_seen=record.last_seen,
            open_ports=list(record.open_ports),
            services=[ServiceFingerprint(**svc.__dict__) for svc in record.services],
            traffic_bytes=record.traffic_bytes,
            sample_hashes=list(record.sample_hashes),
            asset_criticality=record.asset_criticality,
            tags=list(record.tags),
        )


cerebro_network_engine = CerebroNetworkEngine()


def process() -> dict:
    """Legacy status hook preserved for compatibility."""
    return {
        "status": True,
        "mode": "cpie",
        "hosts": len(cerebro_network_engine.get_network_map()),
        "alerts": len(cerebro_network_engine.latest_alerts()),
    }


__all__ = [
    "CerebroNetworkEngine",
    "ServiceFingerprint",
    "HostRecord",
    "ConnectionRecord",
    "TrafficObservation",
    "NetworkAlert",
    "PacketParseResult",
    "cerebro_network_engine",
    "process",
]