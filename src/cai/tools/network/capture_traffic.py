#!/usr/bin/env python3
import paramiko
import tempfile
import os
import shutil
import subprocess
import time
import socket
import sys
import threading
from contextlib import contextmanager
from cai.sdk.agents import function_tool

# Stores (tmp_dir, pipe_thread) for active captures, keyed by fifo_path
_CAPTURE_STATE: dict = {}

@function_tool
def capture_remote_traffic(ip, username, password, interface, capture_filter="", port=22, timeout=10):
    """
    Captures network traffic from a remote VM via tcpdump over SSH.

    A background thread pipes the remote tcpdump byte-stream into a local FIFO.
    The caller (or ``remote_capture_session``) is responsible for reading that
    FIFO (e.g. with tshark) and triggering cleanup when done.

    Args:
        ip (str): IP address of the remote VM
        username (str): SSH username
        password (str): SSH password
        interface (str): Network interface to capture on (e.g., eth0)
        capture_filter (str, optional): tcpdump filter expression
        port (int, optional): SSH port (default: 22)
        timeout (int, optional): SSH connection timeout in seconds (default: 10)

    Returns:
        str: Path to the local FIFO that receives the pcap stream.

    Raises:
        ConnectionError: If the SSH connection fails.
        RuntimeError: If traffic capture cannot be started.
    """
    client = None
    tmp_dir = None
    fifo_path = None
    registered = False
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        print(f"Connecting to {ip}:{port} as {username}...")
        client.connect(ip, port=port, username=username, password=password, timeout=timeout)

        # Verify interface exists
        _, stdout, stderr = client.exec_command(f"ip link show {interface}")
        if stdout.channel.recv_exit_status() != 0:
            error = stderr.read().decode().strip()
            raise RuntimeError(f"Interface {interface} not found: {error}")

        # Check tcpdump availability
        _, stdout, stderr = client.exec_command("which tcpdump")
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError("tcpdump not found on remote system")

        # Build tcpdump command
        tcpdump_cmd = f"tcpdump -U -i {interface} -w - "
        if capture_filter:
            tcpdump_cmd += f"'{capture_filter}'"

        print(f"Starting capture on {ip}:{interface}...")
        stdin, stdout, stderr = client.exec_command(tcpdump_cmd)

        # Non-blocking check that tcpdump started
        time.sleep(1)
        if stdout.channel.exit_status_ready():
            error = stderr.read().decode().strip()
            raise RuntimeError(f"Failed to start tcpdump: {error}")

        # Create FIFO inside a secure temp directory (avoids mktemp race condition)
        tmp_dir = tempfile.mkdtemp(prefix="cai_cap_")
        fifo_path = os.path.join(tmp_dir, "capture.fifo")
        os.mkfifo(fifo_path)

        # Transfer SSH client ownership to the pipe thread so it is closed on
        # completion rather than being leaked.
        _ssh_client = client
        client = None  # prevent the finally block from double-closing

        def pipe_ssh_to_fifo():
            try:
                with open(fifo_path, 'wb') as fifo:
                    while True:
                        data = stdout.read(4096)
                        if not data:
                            break
                        fifo.write(data)
                        fifo.flush()
            except (BrokenPipeError, OSError) as exc:
                print(f"Error in pipe_ssh_to_fifo: {exc}")
            finally:
                try:
                    _ssh_client.close()
                except Exception:
                    pass

        thread = threading.Thread(target=pipe_ssh_to_fifo, daemon=True, name="cai-ssh-capture")
        thread.start()

        _CAPTURE_STATE[fifo_path] = (tmp_dir, thread)
        registered = True

        print(f"Capture running. FIFO available at: {fifo_path}")
        print(f"Use: tshark -r {fifo_path} -c 100 [options]")

        return fifo_path

    except paramiko.AuthenticationException:
        raise ConnectionError("Authentication failed. Check username and password.")
    except paramiko.SSHException as e:
        raise ConnectionError(f"SSH connection error: {str(e)}")
    except socket.timeout:
        raise ConnectionError(f"Connection timed out after {timeout} seconds")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {str(e)}")
    finally:
        # Close SSH client if ownership was not transferred to the pipe thread.
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        # Clean up temp dir when FIFO setup failed before registration.
        if not registered and tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@contextmanager
def remote_capture_session(ip, username, password, interface, capture_filter="", port=22):
    """
    Context manager for remote traffic capture with automatic resource cleanup.

    Usage:
        with remote_capture_session("192.168.1.100", "admin", "password", "eth0") as fifo_path:
            subprocess.run(["tshark", "-r", fifo_path, "-T", "fields", "-e", "ip.src"])
    """
    fifo_path = None
    try:
        fifo_path = capture_remote_traffic(ip, username, password, interface,
                                           capture_filter=capture_filter, port=port)
        yield fifo_path
    finally:
        if fifo_path:
            state = _CAPTURE_STATE.pop(fifo_path, None)
            tmp_dir = state[0] if state else None
            # Remove FIFO
            if os.path.exists(fifo_path):
                try:
                    os.unlink(fifo_path)
                except OSError:
                    pass
            # Remove the temp directory that housed the FIFO
            if tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)


@function_tool
def remote_capture_session_tool(ip, username, password, interface, capture_filter="", port=22):
    """
    Tool wrapper to start a remote capture and return the FIFO path.

    Use the `remote_capture_session` context manager for automatic cleanup
    when calling from local scripts. This tool returns the path and is
    intended to be used as an agent tool (non-contextmanager).
    """
    # Delegate to the existing capture_remote_traffic helper which is itself
    # a tool-friendly function and returns the FIFO path when successful.
    return capture_remote_traffic(ip, username, password, interface, capture_filter=capture_filter, port=port)

if __name__ == "__main__":
    # Example usage
    if len(sys.argv) < 5:
        print("Usage: capture_traffic.py <ip> <username> <password> <interface> [filter]")
        sys.exit(1)
    
    ip = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    interface = sys.argv[4]
    capture_filter = sys.argv[5] if len(sys.argv) > 5 else ""
    
    try:
        with remote_capture_session(ip, username, password, interface, capture_filter) as fifo_path:
            # Keep the script running until interrupted
            print("Press Ctrl+C to stop the capture")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nCapture stopped")
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)