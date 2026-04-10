"""NetExec executor and parser

This tool runs `netexec` (if available) or parses a provided NetExec CLI
output string and converts tabular results into a structured "Network Map"
JSON suitable for CAI agents.

Usage patterns:
  - Provide raw NetExec output: `netexec_executor(output=raw_text)`
  - Run installed binary: `netexec_executor(run_args='-t 10.0.0.0/24 --threads 50')`

The parser heuristically detects table headers (pipe-delimited or multi-space
columns), strips ANSI color codes, and aggregates hosts, discovered services,
and captured credentials into a JSON-friendly structure.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

from cai.agents.guardrails import sanitize_external_content as _sanitize
from cai.sdk.agents import function_tool

_INJECTION_RE = re.compile(r"[;&|`$]|\$\(|>\s*\S")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _check_injection(value: str, param_name: str) -> Optional[str]:
    if _INJECTION_RE.search(value or ""):
        return (
            f"[BLOCKED] Parameter '{param_name}' contains disallowed shell "
            "metacharacters. Remove ; & | ` $ $() > from the value."
        )
    return None


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s or "")


def _split_row(line: str) -> List[str]:
    # Prefer pipe separators if present, otherwise split on 2+ spaces
    if "|" in line:
        return [c.strip() for c in line.split("|") if c.strip()]
    return [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]


def _find_header(lines: List[str]) -> Optional[int]:
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        cols = _split_row(line)
        # Heuristic: header must have at least 3 columns and contain one of
        # the canonical tokens we expect (IP, PORT, SERVICE, USER, PASS)
        tokens = {c.lower() for c in cols}
        if len(cols) >= 3 and ("ip" in tokens or "host" in tokens or "address" in tokens) and (
            "port" in tokens or "service" in tokens or "user" in tokens or "pass" in tokens
        ):
            return idx
    return None


def _normalize_header(cols: List[str]) -> List[str]:
    norm = []
    for c in cols:
        k = c.strip().lower()
        k = k.replace("#", "")
        k = k.replace("/", "_")
        k = k.replace(" ", "_")
        norm.append(k)
    return norm


def _parse_netexec_table(raw: str) -> Dict[str, Any]:
    lines = [ln for ln in raw.splitlines()]
    header_idx = _find_header(lines)
    rows: List[Dict[str, str]] = []

    if header_idx is None:
        # Fallback: attempt to parse any multi-column lines as rows
        for line in lines:
            if not line.strip():
                continue
            parts = _split_row(line)
            if len(parts) >= 3:
                rows.append({f"col{i}": p for i, p in enumerate(parts)})
        return {"hosts": [], "rows": rows}

    header_line = lines[header_idx]
    header_cols = _split_row(header_line)
    keys = _normalize_header(header_cols)

    # Parse subsequent lines until a blank or a line of dashes
    for line in lines[header_idx + 1 :]:
        if not line.strip():
            break
        if re.match(r"^[-\s|]+$", line):
            continue
        parts = _split_row(line)
        # Some implementations include footers or separators — skip odd short lines
        if len(parts) < 2:
            continue
        # Map columns to keys (truncate/extrapolate as needed)
        row: Dict[str, str] = {}
        for i, val in enumerate(parts[: len(keys)]):
            row[keys[i]] = val
        rows.append(row)

    # Aggregate rows into host-centric network map
    hosts_map: Dict[str, Dict[str, Any]] = {}
    creds_count = 0
    for r in rows:
        # Canonical IP/host
        ip = r.get("ip") or r.get("address") or r.get("host") or r.get("hostname") or ""
        ip = ip.split()[0] if ip else ""
        service = r.get("service") or r.get("svc") or r.get("port") or ""
        port = None
        proto = None
        # Try to extract port/proto from a 'port' column like '445/tcp'
        port_raw = r.get("port") or r.get("port/proto") or r.get("port_service") or ""
        if port_raw:
            m = re.match(r"(?P<p>\d+)(?:\/(?P<pr>\w+))?", port_raw)
            if m:
                try:
                    port = int(m.group("p"))
                except Exception:
                    port = None
                proto = m.group("pr")

        user = r.get("user") or r.get("username") or r.get("login") or ""
        passwd = r.get("pass") or r.get("password") or r.get("cred") or ""
        status = r.get("status") or r.get("state") or ""
        hostname = r.get("hostname") or r.get("host") or ""

        key = ip or hostname or service or f"row_{len(hosts_map)}"
        if key not in hosts_map:
            hosts_map[key] = {
                "ip": ip,
                "hostname": hostname,
                "services": [],
                "credentials": [],
                "rows": [],
            }

        # Preserve raw row for audit
        hosts_map[key]["rows"].append(r)

        if service or port:
            hosts_map[key]["services"].append(
                {"service": service or "", "port": port, "proto": proto, "raw": port_raw}
            )

        if user or passwd:
            creds_count += 1
            hosts_map[key]["credentials"].append({"username": user, "password": passwd, "service": service, "port": port})

        if status:
            hosts_map[key]["status"] = status

    hosts_list = list(hosts_map.values())

    summary = {
        "hosts": len(hosts_list),
        "credential_entries": creds_count,
        "parsed_at": datetime.utcnow().isoformat() + "Z",
    }

    return {"summary": summary, "hosts": hosts_list, "rows": rows}


@function_tool
async def netexec_executor(
    output: str = "",
    run_args: str = "",
    timeout: int = 120,
) -> str:
    """Run NetExec or parse provided NetExec output into a Network Map JSON.

    Args:
        output: If provided, parse this string as NetExec output instead of
            running the binary.
        run_args: Arguments passed to the `netexec` binary when `output` is
            not provided (tokenised with `shlex.split`). Do NOT include shell
            metacharacters like `;|&`.
        timeout: Maximum seconds to wait for external execution.

    Returns:
        A JSON string with keys: `network_map` (parsed structure), `raw`
        (sanitized raw output), and `error` when applicable.
    """
    # Input sanitation
    for param, val in (("run_args", run_args),):
        if err := _check_injection(val, param):
            return json.dumps({"error": err})

    raw_output = ""
    if output and output.strip():
        raw_output = output
    else:
        binary = shutil.which("netexec")
        if binary is None:
            return json.dumps({
                "error": "netexec binary not found in PATH. Provide `output` or install NetExec."
            })

        cmd = [binary]
        if run_args:
            try:
                cmd.extend(shlex.split(run_args))
            except ValueError as exc:
                return json.dumps({"error": f"Could not parse run_args: {exc}"})

        def _do_run() -> subprocess.CompletedProcess:  # type: ignore[type-arg]
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )

        try:
            result = await asyncio.to_thread(_do_run)
            raw_output = (result.stdout or "") + ("\n[stderr]\n" + (result.stderr or "") if getattr(result, "stderr", None) else "")
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"netexec timed out after {timeout}s"})
        except FileNotFoundError:
            return json.dumps({"error": f"Binary disappeared after resolution: {cmd[0]}"})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Execution error: {exc}"})

    if not raw_output.strip():
        return json.dumps({"error": "No output to parse from NetExec."})

    raw = _strip_ansi(raw_output)

    parsed = _parse_netexec_table(raw)

    # Sanitize values to avoid prompt-injection when sending back to agent
    try:
        def _san(x):
            if isinstance(x, str):
                return _sanitize(x)[:1024]
            if x is None:
                return x
            if isinstance(x, dict):
                return {k: _san(v) for k, v in x.items()}
            if isinstance(x, list):
                return [_san(v) for v in x]
            return x

        parsed_safe = _san(parsed)
        raw_safe = _sanitize(raw)[:10000]
    except Exception:
        parsed_safe = parsed
        raw_safe = raw[:10000]

    out = {"network_map": parsed_safe, "raw": raw_safe, "error": None}
    return json.dumps(out, indent=2)
