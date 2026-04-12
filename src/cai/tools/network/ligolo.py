"""Ligolo-ng helper tool for CAI agents.

Provides a small helper to prepare instructions for setting up a Ligolo-ng
pivot, and optionally start/stop a local Ligolo server process (if the
binary is present). The tool is conservative: if the agent does not supply
`run_args`, it will return step-by-step instructions and templated commands
that an operator can run on the target and local host.

Actions supported:
  - `prepare`: return templated commands and instructions (default)
  - `start`: start a local ligolo process using the provided `run_args`
  - `stop`: stop the previously started local ligolo process
  - `status`: show current local ligolo process status
  - `setup`: shorthand for `prepare` then optionally `start` if `start_local`

The tool never shells-out via `shell=True` and blocks on unsafe shell meta
characters in `run_args`.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from cai.sdk.agents import function_tool

_LOG_DIR = Path("logs") / "ligolo"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Keep a reference to the running local process so we can stop it later.
_LOCAL_PROC: Dict[str, Any] = {}

_INJECTION_RE = r"[;&|`$]|\$\(|>\s*\S"


def _resolve_binary() -> Optional[str]:
    for name in ("ligolo-ng", "ligolo"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _check_injection(value: str) -> Optional[str]:
    import re

    if not value:
        return None
    if re.search(_INJECTION_RE, value):
        return "Parameter contains disallowed shell metacharacters; remove ; & | ` $ $() >"
    return None


async def _start_process(binary: str, args: list[str], log_path: Path) -> Dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def _drain_stream(reader: asyncio.StreamReader, dest: Path) -> None:
        try:
            with dest.open("ab") as fh:
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    fh.write(chunk)
                    fh.flush()
        except Exception:
            pass

    task = asyncio.create_task(_drain_stream(proc.stdout, log_path))
    return {
        "proc": proc,
        "task": task,
        "log": str(log_path),
        "started_at": time.time(),
        "args": args,
    }


async def _stop_process(info: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
    proc = info.get("proc")
    if not proc:
        return {"stopped": False, "reason": "no process"}
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except Exception as exc:
        return {"stopped": False, "reason": str(exc)}
    # Cancel the drain task
    try:
        t = info.get("task")
        if t:
            t.cancel()
    except Exception:
        pass
    return {"stopped": True}


@function_tool
async def ligolo_executor(
    action: str = "prepare",
    target: str = "",
    run_args: str = "",
    start_local: bool = False,
    timeout: int = 120,
) -> str:
    """Prepare or manage a Ligolo-ng pivot.

    Args:
        action: One of `prepare`, `start`, `stop`, `status`, or `setup`.
        target: Target host/IP (optional) used to render templated client
            commands in the `prepare`/`setup` flow.
        run_args: Arguments to pass to the local ligolo binary when starting.
        start_local: When `action` is `setup`, if True attempt to start
            a local ligolo server using `run_args`.
        timeout: Timeout in seconds for start/stop operations.

    Returns:
        JSON string with keys: `status`, `instructions`, `server` (if started),
        and `error` when applicable.
    """
    action = (action or "").strip().lower()
    if action not in {"prepare", "start", "stop", "status", "setup"}:
        return json.dumps({"error": "unknown action"})

    # Sanitize inputs
    if run_args:
        if _check_injection(run_args):
            return json.dumps({"error": "run_args contains unsafe shell characters"})

    binary = _resolve_binary()

    # Prepare templated commands/instructions
    instructions = {
        "notes": "Ligolo-ng setup helper. Review commands before running.",
        "docs": "https://docs.ligolo.ng/",
        "local": {},
        "remote": {},
    }

    # Template: leave placeholders; agents/operators will likely supply the proper flags
    local_template = {
        "binary": binary or "<ligolo binary not found in PATH>",
        "example_start": "ligolo-ng --server --listen 0.0.0.0:8080 # adjust flags as needed",
        "log": str(_LOG_DIR),
    }

    remote_template = {
        "example_client": "ligolo-ng --client --connect <server_ip>:8080 --proxy socks5://127.0.0.1:1080",
        "notes": "Run the client on the pivot host (or via a droplet/container) to connect back to the server.",
    }

    instructions["local"] = local_template
    instructions["remote"] = remote_template

    if action in {"prepare", "setup"}:
        out = {"status": "prepared", "instructions": instructions, "error": None}
        # Optionally start local server if requested and we have a binary
        if action == "setup" and start_local:
            if not binary:
                out["error"] = "ligolo binary not found, cannot start local server"
                return json.dumps(out, indent=2)
            if not run_args:
                out["error"] = "run_args required to start local ligolo server"
                return json.dumps(out, indent=2)

            # Start local process
            args = shlex.split(run_args)
            log_path = _LOG_DIR / f"ligolo_{int(time.time())}.log"
            try:
                info = await _start_process(binary, args, log_path)
                _LOCAL_PROC["server"] = info
                out["server"] = {
                    "pid": getattr(info.get("proc"), "pid", None),
                    "log": str(log_path),
                }
                out["status"] = "running"
            except Exception as exc:
                out["error"] = f"failed to start ligolo: {exc}"
        return json.dumps(out, indent=2)

    if action == "start":
        if not binary:
            return json.dumps({"error": "ligolo binary not found in PATH"})
        if not run_args:
            return json.dumps({"error": "run_args required to start local ligolo server"})
        if _LOCAL_PROC.get("server"):
            return json.dumps(
                {
                    "error": "local ligolo server already running",
                    "server": {"pid": getattr(_LOCAL_PROC["server"].get("proc"), "pid", None)},
                }
            )

        args = shlex.split(run_args)
        log_path = _LOG_DIR / f"ligolo_{int(time.time())}.log"
        try:
            info = await _start_process(binary, args, log_path)
            _LOCAL_PROC["server"] = info
            return json.dumps(
                {
                    "status": "running",
                    "pid": getattr(info.get("proc"), "pid", None),
                    "log": str(log_path),
                },
                indent=2,
            )
        except Exception as exc:
            return json.dumps({"error": f"failed to start ligolo: {exc}"})

    if action == "stop":
        info = _LOCAL_PROC.get("server")
        if not info:
            return json.dumps({"status": "not_running"})
        res = await _stop_process(info)
        _LOCAL_PROC.pop("server", None)
        return json.dumps({"stop_result": res}, indent=2)

    if action == "status":
        info = _LOCAL_PROC.get("server")
        if not info:
            return json.dumps({"status": "not_running"})
        proc = info.get("proc")
        pid = getattr(proc, "pid", None)
        started_at = datetime.utcfromtimestamp(info.get("started_at")).isoformat() + "Z"
        return json.dumps(
            {"status": "running", "pid": pid, "started_at": started_at, "log": info.get("log")},
            indent=2,
        )

    return json.dumps({"error": "unhandled action"})
