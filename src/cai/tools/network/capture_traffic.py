#!/usr/bin/env python3
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

import paramiko

from cai.sdk.agents import function_tool


def _capture_remote_traffic_impl(
    ip, username, password, interface, capture_filter="", port=22, timeout=10
):
    """Core implementation — returns the FIFO path; caller is responsible for cleanup."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"Connecting to {ip}:{port} as {username}...")
    client.connect(ip, port=port, username=username, password=password, timeout=timeout)

    _, stdout, stderr = client.exec_command(f"ip link show {interface}")
    if stdout.channel.recv_exit_status() != 0:
        error = stderr.read().decode().strip()
        raise RuntimeError(f"Interface {interface} not found: {error}")

    _, stdout, stderr = client.exec_command("which tcpdump")
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError("tcpdump not found on remote system")

    tcpdump_cmd = f"tcpdump -U -i {interface} -w - "
    if capture_filter:
        tcpdump_cmd += f"'{capture_filter}'"

    print(f"Starting capture on {ip}:{interface}...")
    _, stdout, stderr = client.exec_command(tcpdump_cmd)

    time.sleep(1)
    if stdout.channel.exit_status_ready():
        error = stderr.read().decode().strip()
        raise RuntimeError(f"Failed to start tcpdump: {error}")

    fifo_path = tempfile.mktemp()
    os.mkfifo(fifo_path)

    def pipe_ssh_to_fifo():
        try:
            with open(fifo_path, "wb") as fifo:
                while True:
                    data = stdout.read(4096)
                    if not data:
                        break
                    fifo.write(data)
                    fifo.flush()
        except (BrokenPipeError, OSError) as e:
            print(f"Error in pipe_ssh_to_fifo: {str(e)}")
        finally:
            print("Closing FIFO due to error or completion.")

    thread = threading.Thread(target=pipe_ssh_to_fifo, daemon=True)
    thread.start()

    print(f"Capture running. Data available at: {fifo_path}")
    print(f"You can now use: tshark -r {fifo_path} -c 100 [options]")

    subprocess.run(["tshark", "-r", fifo_path, "-c", "100"])

    return fifo_path


@function_tool
def capture_remote_traffic(
    ip, username, password, interface, capture_filter="", port=22, timeout=10
):
    """
    Captures network traffic from a remote VM and returns a path to a FIFO pipe readable by tshark.

    Args:
        ip (str): IP address of the remote VM
        username (str): SSH username for the remote VM
        password (str): SSH password for the remote VM
        interface (str): Network interface to capture on (e.g., eth0)
        capture_filter (str, optional): tcpdump filter expression
        port (int, optional): SSH port (default: 22)
        timeout (int, optional): Connection timeout in seconds (default: 10)

    Returns:
        str: Path to a FIFO pipe that can be read by tshark

    Raises:
        ConnectionError: If connection to the remote VM fails
        RuntimeError: If traffic capture fails to start
    """
    try:
        return _capture_remote_traffic_impl(
            ip, username, password, interface, capture_filter=capture_filter, port=port, timeout=timeout
        )
    except paramiko.AuthenticationException:
        raise ConnectionError("Authentication failed. Check username and password.")
    except paramiko.SSHException as e:
        raise ConnectionError(f"SSH connection error: {str(e)}")
    except socket.timeout:
        raise ConnectionError(f"Connection timed out after {timeout} seconds")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {str(e)}")


@contextmanager
def remote_capture_session(ip, username, password, interface, capture_filter="", port=22):
    """
    Context manager for remote traffic capture that automatically cleans up resources.

    Usage:
        with remote_capture_session("192.168.1.100", "admin", "password", "eth0") as fifo_path:
            subprocess.run(["tshark", "-r", fifo_path, "-T", "fields", "-e", "ip.src"])

    Note: this is a context manager, not a @function_tool — use capture_remote_traffic for
    direct agent tool calls.
    """
    fifo_path = None
    try:
        fifo_path = _capture_remote_traffic_impl(
            ip, username, password, interface, capture_filter=capture_filter, port=port
        )
        yield fifo_path
    finally:
        if fifo_path and os.path.exists(fifo_path):
            try:
                os.unlink(fifo_path)
            except OSError:
                pass


if __name__ == "__main__":
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
            print("Press Ctrl+C to stop the capture")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nCapture stopped")
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)


# --- Auto-register with ToolRegistry ---
from cai.tool_registry import TOOL_REGISTRY  # noqa: E402

TOOL_REGISTRY.register("capture_remote_traffic", capture_remote_traffic, categories=["network", "recon"])
