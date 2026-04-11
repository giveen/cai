"""Tool output schema validation and automatic summarization helpers.

This module provides a small, conservative framework to (1) enforce
lightweight pydantic schemas for well-known tools (e.g. nmap, hashcat)
and (2) produce compact SitReps for very verbose CLI outputs so the
LLM receives structured tactical findings instead of raw noise.

Design goals:
- Fail-safe: never raise for production tool runs — on error we return
  a compact summary string so callers still get usable output.
- Incremental: add parsers/schemas for more tools over time.
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


# ------------------ Schemas (pydantic models) ------------------


class NmapPort(BaseModel):
    port: int
    proto: str
    state: str
    service: Optional[str] = None
    version: Optional[str] = None


class NmapHost(BaseModel):
    ip: Optional[str] = None
    name: Optional[str] = None
    ports: List[NmapPort] = Field(default_factory=list)


class NmapResult(BaseModel):
    hosts: List[NmapHost] = Field(default_factory=list)


class SitRep(BaseModel):
    summary: str
    hosts: List[str] = Field(default_factory=list)
    open_ports_count: int = 0
    credentials: List[str] = Field(default_factory=list)
    raw_snippet: Optional[str] = None


# ------------------ Parsers / Summarizers ------------------


def _parse_nmap_output(text: str) -> Dict[str, Any]:
    """Best-effort parse of nmap text output into structured dict.

    This is intentionally permissive; it's okay if it only extracts the
    most important information (host lines and open ports).
    """
    hosts: List[Dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    current_host: Optional[Dict[str, Any]] = None

    host_re = re.compile(r"^Nmap scan report for (?P<name>[^\(\n]+?)(?: \((?P<ip>[0-9a-fA-F:\.]+)\))?$")
    port_header_re = re.compile(r"^PORT\s+STATE\s+SERVICE")

    while i < len(lines):
        line = lines[i]
        m = host_re.match(line)
        if m:
            if current_host:
                hosts.append(current_host)
            current_host = {"name": m.group("name").strip(), "ip": m.group("ip"), "ports": []}
            i += 1
            continue

        if current_host and port_header_re.match(line):
            # Parse subsequent port lines
            i += 1
            while i < len(lines) and lines[i].strip():
                pl = lines[i].strip()
                parts = pl.split()
                # Expect at least port/proto and state
                if len(parts) >= 2:
                    port_proto = parts[0]
                    state = parts[1]
                    svc = parts[2] if len(parts) >= 3 else None
                    ver = " ".join(parts[3:]) if len(parts) >= 4 else None
                    try:
                        port_num = int(port_proto.split("/")[0])
                        proto = port_proto.split("/")[1] if "/" in port_proto else "tcp"
                    except Exception:
                        port_num = 0
                        proto = "tcp"
                    current_host["ports"].append(
                        {
                            "port": port_num,
                            "proto": proto,
                            "state": state,
                            "service": svc,
                            "version": ver,
                        }
                    )
                i += 1
            continue

        i += 1

    if current_host:
        hosts.append(current_host)

    return {"hosts": hosts}


def _parse_hashcat_output(text: str) -> Dict[str, Any]:
    """Best-effort parse of hashcat output to find recovered credentials.

    We look for lines containing a ':' which often indicate hash:plaintext.
    """
    creds: List[str] = []
    for line in text.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            # Heuristic: take short left/right pair
            parts = line.split(":", 1)
            left, right = parts[0].strip(), parts[1].strip()
            if left and right:
                creds.append(f"{left}:{right}")
    return {"cracked": creds}


def _generic_summarizer(text: str, max_snippet: int = 500) -> Dict[str, Any]:
    hosts = sorted(set(re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", text)))
    open_count = len(re.findall(r"\bopen\b", text, flags=re.I))
    creds_lines = [l for l in text.splitlines() if re.search(r"password|credential|passwd|root|admin|user", l, flags=re.I)]
    creds = creds_lines[:10]
    snippet = text[:max_snippet]
    # Build a short human-readable summary so SitRep validation has a
    # concise `summary` field to validate against. Keep it minimal and
    # robust (fall back to a snippet when nothing else is present).
    parts: List[str] = []
    if hosts:
        if len(hosts) <= 3:
            parts.append(f"hosts: {', '.join(hosts)}")
        else:
            parts.append(f"{len(hosts)} hosts (examples: {', '.join(hosts[:3])})")
    if open_count:
        parts.append(f"{open_count} occurrences of 'open' found")
    if creds:
        parts.append(f"{len(creds)} credential-like lines")

    if parts:
        summary = "; ".join(parts)
    else:
        # Minimal fallback: single-line excerpt of the output
        summary = snippet.replace("\n", " ")[:300].strip()

    return {
        "summary": summary,
        "hosts": hosts,
        "open_ports_count": open_count,
        "credentials": creds,
        "raw_snippet": snippet,
    }


# ------------------ Public process function ------------------


def process_tool_output(tool_name: Optional[str], output: Any, max_verbose: int = 4000) -> str:
    """Validate/summarize tool output and return a JSON/text-safe string.

    Runs the potentially expensive parsing/validation in a short-lived
    background thread and enforces a timeout so the CLI doesn't freeze.
    On timeout or error we fall back to a compact snippet to ensure the
    caller always receives a responsive result.
    """

    def _work() -> str:
        try:
            if not isinstance(output, str):
                # If tool already returned structured data, try to validate against
                # known schema by tool name.
                if tool_name and tool_name.lower().find("nmap") >= 0:
                    try:
                        validated = NmapResult.model_validate(output) if hasattr(NmapResult, "model_validate") else NmapResult.parse_obj(output)
                        return json.dumps({"schema": "nmap", "data": validated.model_dump() if hasattr(validated, "model_dump") else validated.dict()}, ensure_ascii=False)
                    except Exception:
                        pass
                # Default: stringify structured output
                return json.dumps(output, ensure_ascii=False)

            text = output
            lname = (tool_name or "").lower() if tool_name else ""

            # Known tool parsers
            if "nmap" in lname:
                try:
                    parsed = _parse_nmap_output(text)
                    nm = NmapResult.model_validate(parsed) if hasattr(NmapResult, "model_validate") else NmapResult.parse_obj(parsed)
                    return json.dumps({"schema": "nmap", "data": nm.model_dump() if hasattr(nm, "model_dump") else nm.dict()}, ensure_ascii=False)
                except ValidationError as e:
                    logger.debug("nmap schema validation failed: %s", e)
                except Exception:
                    logger.exception("nmap parsing error")

            if "hashcat" in lname or "hashcat" in text.lower():
                try:
                    parsed = _parse_hashcat_output(text)
                    return json.dumps({"schema": "hashcat", "data": parsed}, ensure_ascii=False)
                except Exception:
                    logger.exception("hashcat parsing error")

            # If output is huge, provide a SitRep instead of raw text
            if isinstance(text, str) and len(text) > max_verbose:
                try:
                    s = _generic_summarizer(text)
                    sit = SitRep.model_validate(s) if hasattr(SitRep, "model_validate") else SitRep.parse_obj(s)
                    return json.dumps({"schema": "sitrep", "data": sit.model_dump() if hasattr(sit, "model_dump") else sit.dict()}, ensure_ascii=False)
                except Exception:
                    logger.exception("sitrep generation failed")
                    # fall back to a compact snippet
                    return json.dumps({"schema": "snippet", "data": {"snippet": text[:1000]}}, ensure_ascii=False)

            # Small output: return as-is (string)
            return text

        except Exception:
            logger.exception("process_tool_output unexpected error")
            # Last-resort fallback: return raw string
            try:
                return str(output)
            except Exception:
                return ""

    # Run the worker in a background thread with a short timeout
    try:
        timeout_secs = 3
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_work)
            return future.result(timeout=timeout_secs)
    except Exception as e:  # including concurrent.futures.TimeoutError
        logger.warning("process_tool_output timed out or failed (%s). Returning compact snippet.", e)
        try:
            txt = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            return json.dumps({"schema": "snippet", "data": {"snippet": txt[:1000]}}, ensure_ascii=False)
        except Exception:
            try:
                return str(output)[:1000]
            except Exception:
                return ""
