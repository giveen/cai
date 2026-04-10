"""Session management for shell/CTF/container interactive sessions.

This module contains `ShellSession` and the session registry plus helper
functions. It is a thread-safe extract of the session-related code from
`common.py` and provides a clean API for creating, listing, sending input
to, reading from, and terminating interactive sessions.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import subprocess  # nosec B404
import threading
import time
import uuid

from wasabi import color  # pylint: disable=import-error

from cai.tools.workspace import _get_container_workspace_path, _get_workspace_dir

# Session registry and helpers (protected by SESSIONS_LOCK)
SESSIONS_LOCK = threading.Lock()
ACTIVE_SESSIONS: dict[str, ShellSession] = {}
FRIENDLY_SESSION_MAP: dict[str, str] = {}
REVERSE_SESSION_MAP: dict[str, str] = {}
SESSION_COUNTER = 0
SESSION_OUTPUT_COUNTER: dict[str, int] = {}


class ShellSession:  # pylint: disable=too-many-instance-attributes
    """Class to manage interactive shell sessions.

    The implementation mirrors the prior behavior in `common.py`.
    """

    def __init__(self, command, session_id=None, ctf=None, workspace_dir=None, container_id=None):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.command = command
        self.ctf = ctf
        self.container_id = container_id
        if self.container_id:
            self.workspace_dir = _get_container_workspace_path()
        elif self.ctf:
            self.workspace_dir = workspace_dir or _get_workspace_dir()
        else:
            self.workspace_dir = workspace_dir or _get_workspace_dir()
        self.friendly_id = None
        self.created_at = time.time()
        self.process = None
        self.master = None
        self.slave = None
        self.output_buffer = []
        self.is_running = False
        self.last_activity = time.time()

    def start(self):
        start_message_cmd = self.command

        # Container execution
        if self.container_id:
            try:
                self.master, self.slave = pty.openpty()
                docker_cmd_list = [
                    "docker",
                    "exec",
                    "-i",
                    "-t",
                    "-w",
                    self.workspace_dir,
                    self.container_id,
                    "sh",
                    "-c",
                    self.command,
                ]
                self.process = subprocess.Popen(
                    docker_cmd_list,
                    stdin=self.slave,
                    stdout=self.slave,
                    stderr=self.slave,
                    preexec_fn=os.setsid,
                    universal_newlines=True,
                )
                self.is_running = True
                self.output_buffer.append(
                    f"[Session {self.session_id}] Started in container {self.container_id[:12]}: "
                    f"{start_message_cmd} in {self.workspace_dir}"
                )
                threading.Thread(target=self._read_output, daemon=True).start()
                return None
            except Exception as e:
                self.output_buffer.append(f"Error starting container session: {str(e)}")
                self.is_running = False
                return str(e)

        # CTF execution (synchronous request/response)
        if self.ctf:
            try:
                self.is_running = True
                self.output_buffer.append(
                    f"[Session {self.session_id}] Started CTF command: {self.command}"
                )
                output = self.ctf.get_shell(self.command)
                if output:
                    self.output_buffer.append(output)
                self.is_running = False
                return None
            except Exception as e:  # pylint: disable=broad-except
                self.output_buffer.append(f"Error executing CTF command: {str(e)}")
                self.is_running = False
                return str(e)

        # Local host execution
        try:
            self.master, self.slave = pty.openpty()
            self.process = subprocess.Popen(
                self.command,
                shell=True,  # nosec B602
                stdin=self.slave,
                stdout=self.slave,
                stderr=self.slave,
                cwd=self.workspace_dir,
                preexec_fn=os.setsid,
                universal_newlines=True,
            )
            self.is_running = True
            self.output_buffer.append(f"[Session {self.session_id}] Started: {self.command}")
            threading.Thread(target=self._read_output, daemon=True).start()
        except Exception as e:  # pylint: disable=broad-except
            self.output_buffer.append(f"Error starting local session: {str(e)}")
            self.is_running = False
            return str(e)

    def _read_output(self):
        try:
            while self.is_running and self.master is not None:
                try:
                    if self.process and self.process.poll() is not None:
                        self.is_running = False
                        break

                    ready, _, _ = select.select([self.master], [], [], 0.5)
                    if not ready:
                        if self.process and self.process.poll() is not None:
                            self.is_running = False
                            break
                        continue

                    output = os.read(self.master, 4096).decode("utf-8", errors="replace")

                    if output is not None and output != "":
                        self.output_buffer.append(output)
                        self.last_activity = time.time()
                    else:
                        if self.process and self.process.poll() is None:
                            pass
                        else:
                            self.is_running = False
                            break
                except UnicodeDecodeError:
                    self.output_buffer.append(
                        f"[Session {self.session_id}] Unicode decode error in output\n"
                    )
                    continue
                except Exception as read_err:
                    self.output_buffer.append(f"Error reading output buffer: {str(read_err)}\n")
                    self.is_running = False
                    break

                if self.is_process_running():
                    time.sleep(0.05)

        except Exception as e:
            self.output_buffer.append(f"Error in read_output loop: {str(e)}")
            self.is_running = False
            return str(e)

    def is_process_running(self):
        if self.container_id or self.ctf:
            return self.is_running
        if not self.process:
            return False
        return self.process.poll() is None

    def send_input(self, input_data):
        if not self.is_running:
            if self.process and self.process.poll() is None:
                self.is_running = True
            else:
                return "Session is not running"

        try:
            if self.ctf:
                output = self.ctf.get_shell(input_data)
                self.output_buffer.append(output)
                return "Input sent to CTF session"

            if self.master is not None:
                input_data_bytes = (input_data.rstrip() + "\n").encode()
                bytes_written = os.write(self.master, input_data_bytes)
                if bytes_written != len(input_data_bytes):
                    self.output_buffer.append(
                        f"[Session {self.session_id}] Warning: Partial input write."
                    )
                self.last_activity = time.time()
                return "Input sent to session"
            else:
                return "Session PTY not available for input"
        except Exception as e:  # pylint: disable=broad-except
            self.output_buffer.append(f"Error sending input: {str(e)}")
            return f"Error sending input: {str(e)}"

    def get_output(self, clear=True):
        output = "\n".join(self.output_buffer)
        if clear:
            self.output_buffer = []
        return output

    def get_new_output(self, mark_position=True):
        if not hasattr(self, "_last_output_position"):
            self._last_output_position = 0
        new_output_lines = self.output_buffer[self._last_output_position :]
        new_output = "\n".join(new_output_lines)
        if mark_position:
            self._last_output_position = len(self.output_buffer)
        return new_output

    def terminate(self):
        session_id_short = self.session_id[:8]
        termination_message = f"Session {session_id_short} terminated"

        if not self.is_running:
            if self.process and self.process.poll() is None:
                pass
            else:
                return f"Session {session_id_short} already terminated or finished."

        try:
            self.is_running = False

            if self.process:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    print(
                        color(
                            f"Session {session_id_short} did not terminate gracefully, sending SIGKILL...",
                            fg="yellow",
                        )
                    )
                    try:
                        if pgid:
                            os.killpg(pgid, signal.SIGKILL)
                        else:
                            self.process.kill()
                    except ProcessLookupError:
                        pass
                    except Exception as kill_err:
                        termination_message = f" (Error during SIGKILL: {kill_err})"
                except Exception as term_err:
                    termination_message = f" (Error during SIGTERM: {term_err})"
                    try:
                        self.process.kill()
                    except Exception:
                        pass

                if self.process.poll() is None:
                    print(
                        color(
                            f"Session {session_id_short} process {self.process.pid} may still be running after termination attempts.",
                            fg="red",
                        )
                    )
                    termination_message += " (Warning: Process may still be running)"

            if self.master:
                try:
                    os.close(self.master)
                except OSError:
                    pass
                self.master = None
            if self.slave:
                try:
                    os.close(self.slave)
                except OSError:
                    pass
                self.slave = None

            return termination_message
        except Exception as e:  # pylint: disable=broad-except
            return f"Error terminating session {session_id_short}: {str(e)}"


def create_shell_session(command, ctf=None, container_id=None, **kwargs):
    """Create a new shell session in the correct workspace/environment."""
    workspace_dir = kwargs.get("workspace_dir") if "workspace_dir" in kwargs else None
    if container_id:
        session = ShellSession(command, ctf=ctf, container_id=container_id)
    else:
        workspace_dir = workspace_dir or _get_workspace_dir()
        session = ShellSession(command, ctf=ctf, workspace_dir=workspace_dir)

    session.start()
    if session.is_running or (ctf and not session.is_running):
        global SESSION_COUNTER
        with SESSIONS_LOCK:
            SESSION_COUNTER += 1
            friendly = f"S{SESSION_COUNTER}"
            session.friendly_id = friendly
            ACTIVE_SESSIONS[session.session_id] = session
            FRIENDLY_SESSION_MAP[friendly] = session.session_id
            REVERSE_SESSION_MAP[session.session_id] = friendly
        return session.session_id
    else:
        error_msg = session.get_output(clear=True)
        print(color(f"Failed to start session: {error_msg}", fg="red"))
        return f"Failed to start session: {error_msg}"


def list_shell_sessions():
    """List all active shell sessions"""
    result = []
    with SESSIONS_LOCK:
        for session_id, session in list(ACTIVE_SESSIONS.items()):
            if not session.is_running:
                del ACTIVE_SESSIONS[session_id]
                continue

            result.append(
                {
                    "friendly_id": getattr(session, "friendly_id", None),
                    "session_id": session_id,
                    "command": session.command,
                    "running": session.is_running,
                    "last_activity": time.strftime(
                        "%H:%M:%S", time.localtime(session.last_activity)
                    ),
                }
            )
    return result


def _resolve_session_id(session_identifier: str | None) -> str | None:
    """Resolve a session identifier (friendly alias, numeric, 'last') to real ID."""
    if not session_identifier:
        return None
    sid = str(session_identifier).strip()
    key = sid
    if sid.lower() == "last":
        with SESSIONS_LOCK:
            if not ACTIVE_SESSIONS:
                return None
            latest = None
            latest_t = -1
            for _sid, sess in ACTIVE_SESSIONS.items():
                if hasattr(sess, "created_at") and sess.created_at > latest_t and sess.is_running:
                    latest = _sid
                    latest_t = sess.created_at
            return latest or next(iter(ACTIVE_SESSIONS.keys()))
    if sid.startswith("#"):
        key = f"S{sid[1:]}"
    elif sid.isdigit():
        key = f"S{sid}"
    elif sid.upper().startswith("S") and sid[1:].isdigit():
        key = sid.upper()

    with SESSIONS_LOCK:
        if sid in ACTIVE_SESSIONS:
            return sid
        if key in FRIENDLY_SESSION_MAP:
            return FRIENDLY_SESSION_MAP[key]
    return None


def get_session(session_id: str):
    with SESSIONS_LOCK:
        return ACTIVE_SESSIONS.get(session_id)


def send_to_session(session_id, input_data):
    resolved = _resolve_session_id(session_id)
    if not resolved:
        return f"Session {session_id} not found"
    with SESSIONS_LOCK:
        if resolved not in ACTIVE_SESSIONS:
            return f"Session {session_id} not found"
        session = ACTIVE_SESSIONS[resolved]
    return session.send_input(input_data)


def get_session_output(session_id, clear=True, stdout=True):
    resolved = _resolve_session_id(session_id)
    if not resolved:
        return f"Session {session_id} not found"
    with SESSIONS_LOCK:
        if resolved not in ACTIVE_SESSIONS:
            return f"Session {session_id} not found"
        session = ACTIVE_SESSIONS[resolved]
    output = session.get_output(clear)
    return output


def terminate_session(session_id):
    resolved = _resolve_session_id(session_id)
    if not resolved:
        return f"Session {session_id} not found or already terminated."
    with SESSIONS_LOCK:
        if resolved not in ACTIVE_SESSIONS:
            return f"Session {session_id} not found or already terminated."
        session = ACTIVE_SESSIONS[resolved]
    result = session.terminate()
    with SESSIONS_LOCK:
        if resolved in ACTIVE_SESSIONS:
            del ACTIVE_SESSIONS[resolved]
            friendly = REVERSE_SESSION_MAP.pop(resolved, None)
            if friendly:
                FRIENDLY_SESSION_MAP.pop(friendly, None)
    return result


__all__ = [
    "ShellSession",
    "create_shell_session",
    "list_shell_sessions",
    "_resolve_session_id",
    "send_to_session",
    "get_session_output",
    "terminate_session",
    "get_session",
    "ACTIVE_SESSIONS",
    "SESSION_OUTPUT_COUNTER",
]
