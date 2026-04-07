"""
Basic utilities for executing tools
inside or outside of virtual containers.
"""

import subprocess  # nosec B404
import threading
import os
import pty
import signal
import time
import uuid
import sys
import shlex
import select
from wasabi.util import color  # pylint: disable=import-error
from typing import Any, Optional, Tuple, Dict
from cai.util import format_time, start_active_timer, stop_active_timer, start_idle_timer, stop_idle_timer, cli_print_tool_output


# Instead of direct import
try:
    from cai.cli import START_TIME
except ImportError:
    START_TIME = None
from cai.tools.agent_info import _get_agent_token_info  # migrated
from cai.tools.workspace import _get_workspace_dir, _get_container_workspace_path  # migrated
from cai.tools.sessions import (
    create_shell_session,
    list_shell_sessions,
    _resolve_session_id,
    send_to_session,
    get_session_output,
    terminate_session,
    get_session,
    ACTIVE_SESSIONS,
    SESSION_OUTPUT_COUNTER,
)


def _start_tool_streaming_helper(tool_name: str, tool_args: dict, call_id: Optional[str] = None) -> Tuple[str, dict]:
    """Start a streaming session and return (call_id, token_info)."""
    from cai.util import start_tool_streaming
    token_info = _get_agent_token_info()
    new_call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)
    return new_call_id, token_info


def _update_tool_streaming_helper(tool_name: str, tool_args: dict, content: str, call_id: str, token_info: dict) -> None:
    """Update an existing streaming session."""
    from cai.util import update_tool_streaming
    update_tool_streaming(tool_name, tool_args, content, call_id, token_info)


def _finish_tool_streaming_helper(tool_name: str, tool_args: dict, content: str, call_id: str, execution_info: dict, token_info: Optional[dict] = None) -> None:
    """Finish a streaming session, ensuring token_info is available."""
    from cai.util import finish_tool_streaming
    if token_info is None:
        token_info = _get_agent_token_info()
    finish_tool_streaming(tool_name, tool_args, content, call_id, execution_info, token_info)


def _get_workspace_dir() -> str:
    """Determines the target workspace directory based on env vars for host."""
    base_dir_env = os.getenv("CAI_WORKSPACE_DIR")
    workspace_name = os.getenv("CAI_WORKSPACE")
    # Ensure target_dir is always defined for static analyzers
    target_dir = os.getcwd()

    # Determine the base directory
    if base_dir_env:
        base_dir = os.path.abspath(base_dir_env)
    else: # Default base directory is 'workspaces' 
        if workspace_name:
            base_dir = os.path.join(os.getcwd(), "workspaces")
        else: # If no workspace name is set, the workspace IS the CWD.
             return os.getcwd()

    # If a workspace name is provided, append it to the base directory
    if workspace_name:
        if not all(c.isalnum() or c in ['_', '-'] for c in workspace_name):
            print(color(f"Invalid CAI_WORKSPACE name '{workspace_name}'. "
                        f"Using directory '{base_dir}' instead.", fg="yellow"))
            target_dir = base_dir
        else:
             target_dir = os.path.join(base_dir, workspace_name)
    else:
         target_dir = base_dir

    # Ensure the final target directory exists on the host
    abs_target_dir = os.path.abspath(target_dir)
    try:
        os.makedirs(abs_target_dir, exist_ok=True)
        return abs_target_dir
    except OSError as e:
        print(color(f"Error creating/accessing host workspace directory '{abs_target_dir}': {e}", fg="red"))
        print(color(f"Falling back to current directory: {os.getcwd()}", fg="yellow"))
        return os.getcwd()

def _get_container_workspace_path() -> str:
    """Determines the target workspace path inside the container."""
    workspace_name = os.getenv("CAI_WORKSPACE") 
    if workspace_name:
        if not all(c.isalnum() or c in ['_', '-'] for c in workspace_name):
            print(color(f"Invalid CAI_WORKSPACE name '{workspace_name}' for container. "
                        f"Using '/workspace'.", fg="yellow"))
            return "/"
        # Standard path inside CAI containers
        return f"/workspace/workspaces/{workspace_name}"
    else:
        return "/"

# Session management has been moved to `cai.tools.sessions`.
# The authoritative implementations for `ShellSession` and the session
# registry live in `src/cai/tools/sessions.py` and are imported at the
# top of this module (see the `from cai.tools.sessions import ...`
# statement). Removing duplicate definitions here avoids type conflicts
# and keeps a single source of truth for session lifecycle logic.


def _run_ctf(ctf, command, stdout=False, timeout=100, workspace_dir=None, stream=False):
    """Runs command in CTF env, changing to workspace_dir first."""
    target_dir = workspace_dir or _get_workspace_dir()
    full_command = f"{command}"
    original_cmd_for_msg = command # For logging
    context_msg = f"(ctf:{target_dir})"
    try:
        output = ctf.get_shell(full_command, timeout=timeout)
        # In streaming mode, don't print to stdout to avoid duplication
        # The streaming system will handle the display
        if stdout and not stream:
            print(f"\033[32m{context_msg} $ {original_cmd_for_msg}\n{output}\033[0m") # noqa E501
        return output
    except Exception as e:  # pylint: disable=broad-except
        error_msg = f"Error executing CTF command '{original_cmd_for_msg}' in '{target_dir}': {e}" # noqa E501
        print(color(error_msg, fg="red"))
        return error_msg

def _run_ssh(command, stdout=False, timeout=100, workspace_dir=None, stream=False):
    """Runs command via SSH. Assumes SSH agent or passwordless setup unless sshpass is used externally.""" # noqa E501
    ssh_user = os.environ.get('SSH_USER')
    ssh_host = os.environ.get('SSH_HOST')
    ssh_pass = os.environ.get('SSH_PASS') 
    remote_command = command
    original_cmd_for_msg = command
    context_msg = f"({ssh_user}@{ssh_host})"

    # Construct base SSH command list
    if ssh_pass:
        ssh_cmd_list = ["sshpass", "-p", ssh_pass, "ssh", f"{ssh_user}@{ssh_host}"] # noqa E501
    else:
        ssh_cmd_list = ["ssh", f"{ssh_user}@{ssh_host}"]
    ssh_cmd_list.append(remote_command)

    try:
        # Use subprocess.run with list of args for better security than shell=True
        result = subprocess.run(
            ssh_cmd_list,
            capture_output=True,
            text=True,
            check=False, # Don't raise exception on non-zero exit code
            timeout=timeout
        )
        output = result.stdout if result.stdout else result.stderr
        # In streaming mode, don't print to stdout to avoid duplication
        # The streaming system will handle the display
        if stdout and not stream:
            print(f"\033[32m{context_msg} $ {original_cmd_for_msg}\n{output}\033[0m") # noqa E501
        # Return combined output, potentially including errors
        return output.strip()
    except subprocess.TimeoutExpired as e:
        error_output = e.stdout if e.stdout else str(e)
        timeout_msg = f"Timeout executing SSH command: {error_output}"
        if stdout and not stream:
            print(f"\033[33m{context_msg} $ {original_cmd_for_msg}\nTIMEOUT\n{error_output}\033[0m") # noqa E501
        return timeout_msg
    except FileNotFoundError:
         # Handle case where ssh or sshpass isn't installed
         error_msg = f"'sshpass' or 'ssh' command not found. Ensure they are installed and in PATH." # noqa E501
         print(color(error_msg, fg="red"))
         return error_msg
    except Exception as e:  # pylint: disable=broad-except
        error_msg = f"Error executing SSH command '{original_cmd_for_msg}' on {ssh_host}: {e}" # noqa E501
        print(color(error_msg, fg="red"))
        return error_msg


async def _run_local_async(command, stdout=False, timeout=100, stream=False, call_id=None, tool_name=None, workspace_dir=None, custom_args=None):
    """Async version of _run_local that uses asyncio subprocess for non-blocking execution."""
    import asyncio
    
    # Make sure we're in active time mode for tool execution
    stop_idle_timer()
    start_active_timer()
    
    process_start_time = time.time()  # Initialize with current time

    # Pre-compute target_dir so exception handlers can reference it safely
    target_dir = workspace_dir or _get_workspace_dir()
    try:
        original_cmd_for_msg = command # For logging
        context_msg = f"(local:{target_dir})"
        
        # If streaming is enabled and we have a call_id
        if stream:
            # Import the streaming utilities from util
            from cai.util import start_tool_streaming, update_tool_streaming, finish_tool_streaming
            
            # Parse command into parts for display
            parts = command.strip().split(' ', 1)
            cmd_var = parts[0] if parts else ""
            args_param_val = parts[1] if len(parts) > 1 else ""
            
            # For generic Linux commands, standardize the tool_name format
            if not tool_name:
                tool_name = f"{cmd_var}_command" if cmd_var else "command"
            
            # Create args dictionary with non-empty values only
            tool_args: dict[str, Any] = {}
            if cmd_var:
                tool_args["command"] = cmd_var
            if args_param_val and args_param_val.strip():
                tool_args["args"] = args_param_val
            
            # Add more context for the command
            tool_args["workspace"] = os.path.basename(target_dir)
            tool_args["full_command"] = command
            
            # If custom args were provided, merge them with the default args
            if custom_args is not None:
                if isinstance(custom_args, dict):
                    # Merge the dictionaries, with custom args taking precedence
                    for key, value in custom_args.items():
                        tool_args[key] = value
            
            # For generic commands, ensure we have a unique call_id
            if not call_id:
                call_id = f"cmd_{cmd_var}_{str(uuid.uuid4())[:8]}"
            
            # Get token info for agent display
            token_info = _get_agent_token_info()
            
            # Initialize/use the call_id for this streaming session
            call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)
            
            # Start the async process
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=target_dir
            )

            # Ensure pipes exist for static analyzers
            assert process.stdout is not None
            assert process.stderr is not None
            
            # Begin collecting output
            output_buffer = []
            buffer_size = 0
            update_interval = 10  # lines - default for most tools
            
            # Use a smaller interval for generic_linux_command for better responsiveness
            if tool_name == "generic_linux_command":
                update_interval = 3  # Update more frequently for terminal commands
                
                # Don't add refresh_rate to tool_args as it affects command deduplication
                # The refresh behavior is already handled by the streaming update logic
            
            # Stream stdout with idle detection
            last_output = time.time()
            while True:
                if process.returncode is not None:
                    break
                try:
                    # process.stdout is asserted non-None above
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
                    if line:
                        output_buffer.append(line.decode('utf-8', errors='replace'))
                        buffer_size += 1
                        last_output = time.time()
                        if buffer_size >= update_interval:
                            update_tool_streaming(tool_name, tool_args, ''.join(output_buffer), call_id, token_info)
                            buffer_size = 0
                    else:
                        break
                except asyncio.TimeoutError:
                    if time.time() - last_output > 10:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            process.kill()
                            await process.wait()
                        output_buffer.append("\n[Terminated: idle 10s, likely waiting for input]")
                        break
            
            # Wait for process to complete
            if process.returncode is None:
                try:
                    return_code = await asyncio.wait_for(process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise subprocess.TimeoutExpired(command, timeout)
            else:
                return_code = process.returncode
            
            process_execution_time = time.time() - process_start_time
            
            # Get any stderr output
            stderr_data = await process.stderr.read()
            if stderr_data:
                stderr_str = stderr_data.decode('utf-8', errors='replace')
                output_buffer.append("\nERROR OUTPUT:\n" + stderr_str)
            
            # Final output update
            final_output = ''.join(output_buffer)
            if return_code != 0:
                final_output += f"\nCommand exited with code {return_code}"
                
            # Calculate execution info with environment details
            execution_info = {
                "status": "completed" if return_code == 0 else "error",
                "return_code": return_code,
                "environment": "Local",
                "host": os.path.basename(target_dir),
                "tool_time": process_execution_time
            }
            
            # Complete the streaming session with final output
            finish_tool_streaming(tool_name, tool_args, final_output, call_id, execution_info, token_info)
            
            return final_output
        else:
            # Non-streaming with idle detection
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=target_dir
            )
            
            stdout_chunks, stderr_chunks = [], []
            last_output = time.time()
            start = time.time()
            
            while True:
                if time.time() - start > timeout:
                    process.kill()
                    await process.wait()
                    raise subprocess.TimeoutExpired(command, timeout)
                if process.returncode is not None:
                    break
                try:
                    # Static analysis: ensure streams are present
                    out_stream = process.stdout
                    err_stream = process.stderr
                    assert out_stream is not None
                    assert err_stream is not None
                    out_task = asyncio.create_task(out_stream.read(4096))
                    err_task = asyncio.create_task(err_stream.read(4096))
                    done, pending = await asyncio.wait([out_task, err_task], timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    for task in done:
                        data = await task
                        if data:
                            (stdout_chunks if task == out_task else stderr_chunks).append(data)
                            last_output = time.time()
                except asyncio.TimeoutError:
                    pass
                if time.time() - last_output > 10:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.1)
                        break
                    except asyncio.TimeoutError:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            process.kill()
                            await process.wait()
                        stderr_chunks.append(b"\n[Terminated: idle 10s]")
                        break
            
            stdout_data, stderr_data = b''.join(stdout_chunks), b''.join(stderr_chunks)
            
            # Decode output
            output = stdout_data.decode('utf-8', errors='replace') if stdout_data else ""
            if not output and stderr_data:
                output = stderr_data.decode('utf-8', errors='replace')
            
            # Parse command for display
            parts = command.strip().split(' ', 1)
            
            # In non-streaming mode (typically parallel execution), display completed panel
            # Get token info for agent display
            token_info = _get_agent_token_info()
            
            # Check if we're in parallel mode by checking agent ID
            is_parallel = False
            if token_info and token_info.get("agent_id"):
                agent_id = token_info.get("agent_id")
                if agent_id and agent_id.startswith('P') and agent_id[1:].isdigit():
                    # Check CAI_PARALLEL to confirm
                    if int(os.getenv("CAI_PARALLEL", "1")) > 1:
                        is_parallel = True
            
            # NEVER display panels in non-streaming mode
            # The SDK will handle ALL display when CAI_STREAM=false
            streaming_enabled = os.getenv("CAI_STREAM", "false").lower() == "true"
            
            # Only display panels if we're in streaming mode or parallel mode
            # In streaming mode, the Live panels are handled by the streaming system
            if streaming_enabled and is_parallel:
                # Display the completed tool output
                from cai.util import cli_print_tool_output
                
                # Calculate execution time
                execution_time = time.time() - process_start_time
                
                # Generate a unique call_id if not provided
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"{cmd_name}_{str(uuid.uuid4())[:8]}"
                
                execution_info = {
                    "status": "completed" if process.returncode == 0 else "error",
                    "return_code": process.returncode,
                    "environment": "Local",
                    "host": os.path.basename(target_dir),
                    "tool_time": execution_time
                }
                
                # Display the tool output panel
                cli_print_tool_output(
                    tool_name=tool_name or "generic_linux_command",
                    args={
                        "command": parts[0] if parts else command,
                        "args": parts[1] if len(parts) > 1 else "",
                        "full_command": command,
                        "workspace": os.path.basename(target_dir)
                    },
                    output=output.strip(),
                    call_id=call_id,
                    execution_info=execution_info,
                    token_info=token_info,
                    streaming=False  # This is non-streaming display
                )
            
            return output.strip()
            
    except subprocess.TimeoutExpired as e:
        error_output = e.stdout if hasattr(e, 'stdout') and e.stdout else str(e)
        error_msg = f"Command timed out after {timeout} seconds\n{error_output}"
        
        # If we're streaming, show the timeout in the tool output panel
        if stream and call_id:
            from cai.util import finish_tool_streaming
            # Parse the command the same way we did for streaming
            parts = command.strip().split(' ', 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            
            # Ensure tool_args has complete information
            tool_args: dict[str, Any] = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir)
            }
            execution_info = {
                "status": "timeout", 
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir)
            }
            
            # Get token info for agent display  
            token_info = _get_agent_token_info()
            finish_tool_streaming(tool_name or f"{cmd_var}_command", tool_args, error_msg, call_id, execution_info, token_info)
            
        if stdout:
            print("\033[32m" + error_msg + "\033[0m")
            
        return error_msg
    except Exception as e:  # pylint: disable=broad-except
        error_msg = f"Error executing local command: {e}"
        
        # If we're streaming, show the error in the tool output panel
        if stream and call_id:
            from cai.util import finish_tool_streaming
            # Parse the command the same way we did for streaming
            parts = command.strip().split(' ', 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            
            # Ensure tool_args has complete information
            tool_args: dict[str, Any] = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir)
            }
            execution_info = {
                "status": "error", 
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir)
            }
            
            # Get token info for agent display  
            token_info = _get_agent_token_info()
            finish_tool_streaming(tool_name or f"{cmd_var}_command", tool_args, error_msg, call_id, execution_info, token_info)
            
        print(color(error_msg, fg="red"))
        return error_msg
    finally:
        # Always switch back to idle mode when function completes
        stop_active_timer()
        start_idle_timer()


async def _run_docker_async(command, container_id, stdout=False, timeout=100, stream=False, call_id=None, tool_name=None, args=None):
    """Async version of Docker command execution using asyncio subprocess."""
    import asyncio
    
    # Make sure we're in active time mode for tool execution
    stop_idle_timer()
    start_active_timer()
    
    # Pre-compute container workspace so exception handlers can reference it
    container_workspace = _get_container_workspace_path()
    try:
        
        # Parse command for display
        parts = command.strip().split(' ', 1)
        cmd_name = parts[0] if parts else ""
        cmd_args = parts[1] if len(parts) > 1 else ""
        
        if not tool_name:
            tool_name = f"{cmd_name}_command" if cmd_name else "command"
        
        # Build docker exec command
        docker_cmd_list = [
            "docker", "exec",
            "-w", container_workspace,
            container_id,
            "sh", "-c", command
        ]
        
        if stream:
            from cai.util import start_tool_streaming, update_tool_streaming, finish_tool_streaming
            
            # If args were provided (e.g., from execute_code), use them as base
            # Otherwise create tool args for display
            if args and isinstance(args, dict):
                tool_args: dict[str, Any] = args.copy()
                # Add container-specific info
                tool_args["container"] = container_id[:12]
                tool_args["environment"] = "Container"
                tool_args["workspace"] = container_workspace
                tool_args["full_command"] = command
            else:
                tool_args: dict[str, Any] = {
                    "command": cmd_name,
                    "args": cmd_args if cmd_args.strip() else "",
                    "full_command": command,
                    "container": container_id[:12],
                    "environment": "Container",
                    "workspace": container_workspace
                }
            
            if not call_id:
                call_id = f"cmd_{cmd_name}_{str(uuid.uuid4())[:8]}"
            
            token_info = _get_agent_token_info()
            call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)
            
            # Create async subprocess
            process = await asyncio.create_subprocess_exec(
                *docker_cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Static analysis: ensure streams are present
            assert process.stdout is not None
            assert process.stderr is not None
            
            # Stream output
            output_buffer = []
            buffer_size = 0
            update_interval = 3 if tool_name == "generic_linux_command" else 10
            
            start_time = time.time()
            
            # Read stdout with idle detection
            last_output = time.time()
            while True:
                if process.returncode is not None:
                    break
                try:
                    # process.stdout asserted non-None above
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
                    if line:
                        output_buffer.append(line.decode('utf-8', errors='replace'))
                        buffer_size += 1
                        last_output = time.time()
                        if buffer_size >= update_interval:
                            update_tool_streaming(tool_name, tool_args, ''.join(output_buffer), call_id, token_info)
                            buffer_size = 0
                    else:
                        break
                except asyncio.TimeoutError:
                    if time.time() - last_output > 10:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            process.kill()
                            await process.wait()
                        output_buffer.append("\n[Terminated: idle 10s]")
                        break
            
            # Wait for process completion
            if process.returncode is None:
                try:
                    return_code = await asyncio.wait_for(process.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    raise subprocess.TimeoutExpired(command, timeout)
            else:
                return_code = process.returncode
            
            execution_time = time.time() - start_time
            
            # Get stderr if any
            stderr_data = await process.stderr.read()
            if stderr_data:
                stderr_str = stderr_data.decode('utf-8', errors='replace')
                output_buffer.append("\nERROR OUTPUT:\n" + stderr_str)
            
            final_output = ''.join(output_buffer)
            if return_code != 0:
                final_output += f"\nCommand exited with code {return_code}"
            
            execution_info = {
                "status": "completed" if return_code == 0 else "error",
                "return_code": return_code,
                "environment": "Container",
                "host": container_id[:12],
                "tool_time": execution_time
            }
            
            finish_tool_streaming(tool_name, tool_args, final_output, call_id, execution_info, token_info)
            return final_output
            
        else:
            # Non-streaming async execution
            start_time = time.time()
            process = await asyncio.create_subprocess_exec(
                *docker_cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise subprocess.TimeoutExpired(command, timeout)
            
            output = stdout_data.decode('utf-8', errors='replace') if stdout_data else ""
            if not output and stderr_data:
                output = stderr_data.decode('utf-8', errors='replace')
            
            if stdout:
                context_msg = f"(docker:{container_id[:12]}:{container_workspace})"
                print(f"\033[32m{context_msg} $ {command}\n{output}\033[0m")
            
            # Get token info for display
            token_info = _get_agent_token_info()
            
            # Check if we're in parallel mode
            is_parallel = False
            if token_info and token_info.get("agent_id"):
                agent_id = token_info.get("agent_id")
                if agent_id and agent_id.startswith('P') and agent_id[1:].isdigit():
                    if int(os.getenv("CAI_PARALLEL", "1")) > 1:
                        is_parallel = True
            
            # NEVER display panels in non-streaming mode
            # The SDK will handle ALL display when CAI_STREAM=false
            streaming_enabled = os.getenv("CAI_STREAM", "false").lower() == "true"
            
            # Only display if we're in streaming mode AND parallel mode
            if streaming_enabled and is_parallel:
                from cai.util import cli_print_tool_output
                
                # Calculate execution time
                execution_time = time.time() - start_time
                
                # Parse command for display
                parts = command.strip().split(' ', 1)
                
                # Generate a unique call_id if not provided
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"container_{cmd_name}_{str(uuid.uuid4())[:8]}"
                
                execution_info = {
                    "status": "completed" if process.returncode == 0 else "error",
                    "return_code": process.returncode,
                    "environment": "Container",
                    "host": container_id[:12],
                    "tool_time": execution_time
                }
                
                # Display the tool output panel
                display_args = args if args is not None else {
                    "command": parts[0] if parts else command,
                    "args": parts[1] if len(parts) > 1 else "",
                    "full_command": command,
                    "container": container_id[:12],
                    "workspace": container_workspace
                }
                
                cli_print_tool_output(
                    tool_name=tool_name or "generic_linux_command",
                    args=display_args,
                    output=output.strip(),
                    call_id=call_id,
                    execution_info=execution_info,
                    token_info=token_info,
                    streaming=False
                )
            
            return output.strip()
            
    except Exception as e:
        error_msg = f"Error executing command in container: {str(e)}"
        print(color(error_msg, fg="red"))
        return error_msg
    finally:
        stop_active_timer()
        start_idle_timer()


def _run_local(command, stdout=False, timeout=100, stream=False, call_id=None, tool_name=None, workspace_dir=None, custom_args=None):
    """Runs command locally in the specified workspace_dir."""
    # Make sure we're in active time mode for tool execution
    stop_idle_timer()
    start_active_timer()
    
    process_start_time = time.time()  # Initialize with current time

    # Pre-compute target_dir so exception handlers can reference it safely
    target_dir = workspace_dir or _get_workspace_dir()

    try:
        original_cmd_for_msg = command # For logging
        context_msg = f"(local:{target_dir})"
        
        # If streaming is enabled and we have a call_id
        if stream:
            # Import the streaming utilities from util
            from cai.util import start_tool_streaming, update_tool_streaming, finish_tool_streaming
            
            # Parse command into parts for display
            parts = command.strip().split(' ', 1)
            cmd_var = parts[0] if parts else ""
            args_param_val = parts[1] if len(parts) > 1 else "" # Renamed to avoid conflict with tool_args dict key
            
            # For generic Linux commands, standardize the tool_name format
            if not tool_name:
                tool_name = f"{cmd_var}_command" if cmd_var else "command"
            
            # Create args dictionary with non-empty values only
            tool_args: dict[str, Any] = {}
            if cmd_var:
                tool_args["command"] = cmd_var
            if args_param_val and args_param_val.strip():
                tool_args["args"] = args_param_val
            
            # Add more context for the command
            tool_args["workspace"] = os.path.basename(target_dir)
            tool_args["full_command"] = command
            
            # If custom args were provided, merge them with the default args
            if custom_args is not None:
                if isinstance(custom_args, dict):
                    # Merge the dictionaries, with custom args taking precedence
                    for key, value in custom_args.items():
                        tool_args[key] = value
            
            # For generic commands, ensure we have a unique call_id
            if not call_id:
                call_id = f"cmd_{cmd_var}_{str(uuid.uuid4())[:8]}"
            
            # Get token info for agent display
            token_info = _get_agent_token_info()
            
            # Initialize/use the call_id for this streaming session
            call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)
            
            # Start the process
            process = subprocess.Popen(
                command,
                shell=True,  # nosec B602
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=target_dir
            )
            
            # Begin collecting output
            output_buffer = []
            buffer_size = 0
            update_interval = 10  # lines - default for most tools
            
            # Use a smaller interval for generic_linux_command for better responsiveness
            if tool_name == "generic_linux_command":
                update_interval = 3  # Update more frequently for terminal commands
                
                # Don't add refresh_rate to tool_args as it affects command deduplication
                # The refresh behavior is already handled by the streaming update logic
            
            # Stream stdout in real-time
            # Static analysis: ensure stdout is not None
            assert process.stdout is not None
            assert process.stderr is not None
            for line in iter(process.stdout.readline, ''):
                if not line:
                    break
                
                # Add to output collection
                output_buffer.append(line)
                buffer_size += 1
                
                # Only update periodically to reduce UI refreshes
                if buffer_size >= update_interval:
                    current_output = ''.join(output_buffer)
                    update_tool_streaming(tool_name, tool_args, current_output, call_id, token_info)
                    buffer_size = 0
            
            # Finish process
            if process.stdout:
                process.stdout.close()
            return_code = process.wait(timeout=timeout)
            process_execution_time = time.time() - process_start_time
            
            # Get any stderr output
            stderr_data = process.stderr.read() if process.stderr else None
            if stderr_data:
                output_buffer.append("\nERROR OUTPUT:\n" + stderr_data)
            
            # Final output update
            final_output = ''.join(output_buffer)
            if return_code != 0:
                final_output += f"\nCommand exited with code {return_code}"
                
            # Calculate execution info with environment details
            execution_info = {
                "status": "completed" if return_code == 0 else "error",
                "return_code": return_code,
                "environment": "Local",
                "host": os.path.basename(target_dir),
                "tool_time": process_execution_time
            }
            
            # Complete the streaming session with final output
            finish_tool_streaming(tool_name, tool_args, final_output, call_id, execution_info, token_info)
            
            return final_output
        else:
            # Standard non-streaming execution
            result = subprocess.run(
                command,
                shell=True,  # nosec B602
                capture_output=True,
                text=True,
                check=False, 
                timeout=timeout,
                cwd=target_dir 
            )
            output = result.stdout if result.stdout else result.stderr
            
            # Parse command for display
            parts = command.strip().split(' ', 1)
            
            # In non-streaming mode (typically parallel execution), we should display 
            # the tool output as a completed panel immediately
            # Get token info for agent display
            token_info = _get_agent_token_info()
            
            # Check if we're in parallel mode by checking agent ID
            is_parallel = False
            if token_info and token_info.get("agent_id"):
                agent_id = token_info.get("agent_id")
                if agent_id and agent_id.startswith('P') and agent_id[1:].isdigit():
                    # Check CAI_PARALLEL to confirm
                    if int(os.getenv("CAI_PARALLEL", "1")) > 1:
                        is_parallel = True
            
            # NEVER display panels in non-streaming mode
            # The SDK will handle ALL display when CAI_STREAM=false
            streaming_enabled = os.getenv("CAI_STREAM", "false").lower() == "true"
            
            # Only display if we're in streaming mode AND parallel mode
            if streaming_enabled and is_parallel:
                # Display the completed tool output
                from cai.util import cli_print_tool_output
                
                # Calculate execution time
                execution_time = time.time() - process_start_time
                
                # Generate a unique call_id if not provided
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"{cmd_name}_{str(uuid.uuid4())[:8]}"
                
                execution_info = {
                    "status": "completed" if result.returncode == 0 else "error",
                    "return_code": result.returncode,
                    "environment": "Local",
                    "host": os.path.basename(target_dir),
                    "tool_time": execution_time
                }
                
                # Display the tool output panel
                # Use provided custom_args if available, otherwise create default args
                display_args = custom_args if custom_args is not None else {
                    "command": parts[0] if parts else command,
                    "args": parts[1] if len(parts) > 1 else "",
                    "full_command": command,
                    "workspace": os.path.basename(target_dir)
                }
                
                cli_print_tool_output(
                    tool_name=tool_name or "generic_linux_command",
                    args=display_args,
                    output=output.strip(),
                    call_id=call_id,
                    execution_info=execution_info,
                    token_info=token_info,
                    streaming=False  # This is non-streaming display
                )
            
            return output.strip()
    except subprocess.TimeoutExpired as e:
        error_output = e.stdout if hasattr(e, 'stdout') and e.stdout else str(e)
        error_msg = f"Command timed out after {timeout} seconds\n{error_output}"
        
        # If we're streaming, show the timeout in the tool output panel
        if stream and call_id:
            from cai.util import finish_tool_streaming
            # Parse the command the same way we did for streaming
            parts = command.strip().split(' ', 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            
            # Ensure tool_args has complete information
            tool_args: dict[str, Any] = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir)
            }
            execution_info = {
                "status": "timeout", 
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir)
            }
            
            # Get token info for agent display  
            token_info = _get_agent_token_info()
            finish_tool_streaming(tool_name or f"{cmd_var}_command", tool_args, error_msg, call_id, execution_info, token_info)
            
        if stdout:
            print("\033[32m" + error_msg + "\033[0m")
            return error_msg

            
        return error_msg
    except Exception as e:  # pylint: disable=broad-except
        error_msg = f"Error executing local command: {e}"
        
        # If we're streaming, show the error in the tool output panel
        if stream and call_id:
            from cai.util import finish_tool_streaming
            # Parse the command the same way we did for streaming
            parts = command.strip().split(' ', 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            
            # Ensure tool_args has complete information
            tool_args: dict[str, Any] = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir)
            }
            execution_info = {
                "status": "error", 
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir)
            }
            
            # Get token info for agent display  
            token_info = _get_agent_token_info()
            finish_tool_streaming(tool_name or f"{cmd_var}_command", tool_args, error_msg, call_id, execution_info, token_info)
            
        print(color(error_msg, fg="red"))
        return error_msg
    finally:
        # Always switch back to idle mode when function completes
        stop_active_timer()
        start_idle_timer()


async def run_command_async(command, ctf=None, stdout=False,  # pylint: disable=too-many-arguments # noqa: E501
                      async_mode=False, session_id=None,
                      timeout=100, stream=False, call_id=None, tool_name=None, args=None):
    """
    Async version of run_command that properly supports parallel execution.
    
    Run command in the appropriate environment (Docker, CTF, SSH, Local)
    and workspace.

    Args:
        command: The command to execute
        ctf: CTF environment object (if running in CTF)
        stdout: Whether to print output to stdout
        async_mode: Whether to run the command asynchronously
        session_id: ID of an existing session to send the command to
        timeout: Command timeout in seconds
        stream: Whether to stream output in real-time
        call_id: Unique ID for the command execution (for streaming)
        tool_name: Name of the tool being executed (for display in streaming output).
                  If None, the tool name will be derived from the command.
        args: Additional arguments for the tool (for display and context).

    Returns:
        str: Command output, status message, or session ID.
    """
    # For now, we'll use a hybrid approach - delegate most of the logic to sync version
    # but use async subprocess for local execution
    
    if ctf and not hasattr(ctf, "get_shell"):
        ctf = None
    
    # Parse command into standard parts to ensure consistent naming
    parts = command.strip().split(' ', 1)
    cmd_name = parts[0] if parts else ""
    cmd_args = parts[1] if len(parts) > 1 else ""
    
    # Generate a call_id if we're streaming and one wasn't provided
    if not call_id and stream:
        call_id = f"cmd_{cmd_name}_{str(uuid.uuid4())[:8]}"
        
    # If no tool_name is provided, derive it from the command in a consistent way
    if not tool_name:
        tool_name = f"{cmd_name}_command" if cmd_name else "command"
    
    # Determine execution environment
    from cai.cli import ctf_global
    ctf = ctf_global
    # Pre-initialize target_dir so exception handlers and later blocks can reference it safely
    target_dir = _get_workspace_dir()
    
    # Check for session execution
    if session_id:
        # Sessions need synchronous handling, delegate to sync version
        import asyncio
        import functools
        
        loop = asyncio.get_event_loop()
        func = functools.partial(
            run_command,
            command, ctf, stdout, async_mode, session_id,
            timeout, stream, call_id, tool_name, args
        )
        return await loop.run_in_executor(None, func)
    
    # Check execution environment priority
    active_container = os.getenv("CAI_ACTIVE_CONTAINER", "")
    is_ssh_env = all(os.getenv(var) for var in ['SSH_USER', 'SSH_HOST'])
    
    # For container execution, use async subprocess (delegated to runners)
    if active_container and not is_ssh_env:
        from cai.tools.runners.docker import run_docker_async
        return await run_docker_async(
            command,
            container_id=active_container,
            stdout=stdout,
            timeout=timeout,
            stream=stream,
            call_id=call_id,
            tool_name=tool_name,
            args=args,
        )
    
    # For CTF execution, still need to use sync version in executor
    # because ctf.get_shell() is synchronous
    if ctf and os.getenv('CTF_INSIDE', "True").lower() == "true":
        import asyncio
        import functools
        
        loop = asyncio.get_event_loop()
        func = functools.partial(
            _run_ctf,
            ctf, command, stdout, timeout, _get_workspace_dir(), stream
        )
        return await loop.run_in_executor(None, func)
    
    # For SSH, delegate to sync version for now
    if is_ssh_env:
        import asyncio
        import functools
        
        loop = asyncio.get_event_loop()
        func = functools.partial(
            _run_ssh,
            command, stdout, timeout, _get_workspace_dir(), stream
        )
        return await loop.run_in_executor(None, func)
    
    # For local execution, use the async version (delegated to runners)
    from cai.tools.runners.local import run_local_async
    return await run_local_async(
        command,
        stdout=stdout,
        timeout=timeout,
        stream=stream,
        call_id=call_id,
        tool_name=tool_name,
        workspace_dir=_get_workspace_dir(),
        custom_args=args,
    )


def run_command(command, ctf=None, stdout=False,  # pylint: disable=too-many-arguments # noqa: E501
                async_mode=False, session_id=None,
                timeout=100, stream=False, call_id=None, tool_name=None, args=None):
    """
    Run command in the appropriate environment (Docker, CTF, SSH, Local)
    and workspace.

    Args:
        command: The command to execute
        ctf: CTF environment object (if running in CTF)
        stdout: Whether to print output to stdout
        async_mode: Whether to run the command asynchronously
        session_id: ID of an existing session to send the command to
        timeout: Command timeout in seconds
        stream: Whether to stream output in real-time
        call_id: Unique ID for the command execution (for streaming)
        tool_name: Name of the tool being executed (for display in streaming output).
                  If None, the tool name will be derived from the command.
        args: Additional arguments for the tool (for display and context).

    Returns:
        str: Command output, status message, or session ID.
    """
    if ctf and not hasattr(ctf, "get_shell"):
        ctf = None
    # Use the active timer during tool execution
    stop_idle_timer()
    start_active_timer()
 
    from cai.cli import ctf_global
    ctf = ctf_global
    
    # Parse command into standard parts to ensure consistent naming
    parts = command.strip().split(' ', 1)
    cmd_name = parts[0] if parts else ""
    cmd_args = parts[1] if len(parts) > 1 else ""
    
    # Generate a call_id if we're streaming and one wasn't provided
    # Use a more specific format that includes the command name for easier tracking
    if not call_id and stream:
        call_id = f"cmd_{cmd_name}_{str(uuid.uuid4())[:8]}"
        
    # If no tool_name is provided, derive it from the command in a consistent way
    if not tool_name:
        tool_name = f"{cmd_name}_command" if cmd_name else "command"
    
    try:
        # If session_id is provided, send command to that session
        if session_id:
            resolved_session_id = _resolve_session_id(session_id)
            if not resolved_session_id or resolved_session_id not in ACTIVE_SESSIONS:
                # Switch back to idle mode before returning error
                stop_active_timer()
                start_idle_timer()
                return f"Session {session_id} not found"
            session = ACTIVE_SESSIONS[resolved_session_id]
            result = session.send_input(command) # Send the raw command string
            
            # Wait for the command to execute and capture output
            # This provides automatic output display for async sessions
            wait_time = 3.0  # Wait 3 seconds for command to execute
            
            # Mark the current position in the output buffer before sending input
            session.get_new_output(mark_position=True)  # Reset position marker
            
            # Smart waiting: check for new output every 0.5 seconds, up to max wait time
            max_wait = wait_time
            check_interval = 0.5
            elapsed = 0.0
            new_output_detected = False
            
            while elapsed < max_wait:
                time.sleep(check_interval)
                elapsed += check_interval
                
                # Check if new output is available
                current_new_output = session.get_new_output(mark_position=False)
                
                # If we detect new output, wait a bit more for it to complete, then break
                if current_new_output.strip():
                    if not new_output_detected:
                        new_output_detected = True
                        # Give it a bit more time to complete the output
                        time.sleep(0.5)
                    else:
                        # We already detected new output and waited, now break
                        break
            
            # Always show the session output after sending input using the counter mechanism
            # Generate unique counter for this session input command
            counter_key = f"session_input_{resolved_session_id}"
            if counter_key not in SESSION_OUTPUT_COUNTER:
                SESSION_OUTPUT_COUNTER[counter_key] = 0
            SESSION_OUTPUT_COUNTER[counter_key] += 1
            
            # Create args for display
            label = getattr(session, 'friendly_id', None) or resolved_session_id
            session_args = {
                "command": command,
                "args": "",
                "session_id": label,
                "call_counter": SESSION_OUTPUT_COUNTER[counter_key],  # This ensures uniqueness
                "input_to_session": True,  # Flag to identify this as session input
            }
            
            # Only add auto_output if not already present (prevents duplication)
            if args and isinstance(args, dict):
                # If args were passed and contain auto_output, use that value
                if "auto_output" in args:
                    session_args["auto_output"] = args["auto_output"]
                else:
                    # Otherwise, force it to True for session commands
                    session_args["auto_output"] = True
            else:
                # No args provided, force auto_output
                session_args["auto_output"] = True
            
            # Determine environment info for display
            env_type = "Local"
            if session.container_id:
                env_type = f"Container({session.container_id[:12]})"
            elif session.ctf:
                env_type = "CTF"
            
            # Get only the NEW output to display (not the entire buffer)
            output = session.get_new_output(mark_position=True)
            
            # Create execution info
            execution_info = {
                "status": "completed",
                "environment": env_type,
                "host": session.workspace_dir,
                "session_id": label,
                "wait_time": elapsed,
                "new_output_detected": new_output_detected
            }
            
            # Display the session input and its result using cli_print_tool_output
            from cai.util import cli_print_tool_output
            cli_print_tool_output(
                tool_name="generic_linux_command",
                args=session_args,
                output=output,
                execution_info=execution_info,
                token_info=_get_agent_token_info(),
                streaming=False
            )
            
            # For async sessions, we don't switch back to idle mode here
            # since the session continues to run in the background
            if not async_mode:
                # Switch back to idle mode after synchronous command completes
                stop_active_timer()
                start_idle_timer()
                
            # Return the actual output from the session
            # The output has already been displayed via cli_print_tool_output
            if output and output.strip():
                return output
            else:
                return f"Command sent to session {label}. No output captured."

        # 2. Determine Execution Environment (Container > CTF > SSH > Local)
        active_container = os.getenv("CAI_ACTIVE_CONTAINER", "")
        is_ssh_env = all(os.getenv(var) for var in ['SSH_USER', 'SSH_HOST'])

        # --- Docker Container Execution ---
        if active_container and not is_ssh_env:
            container_id = active_container
            container_workspace = _get_container_workspace_path()
            context_msg = f"(docker:{container_id[:12]}:{container_workspace})"

            # Handle Async Session Creation in Container
            if async_mode and not session_id:
                # Create a session specifically for the container environment
                new_session_id = create_shell_session(command, container_id=container_id) # noqa E501
                if "Failed" in new_session_id: # Check if session creation failed
                    # Switch back to idle mode before returning error
                    stop_active_timer()
                    start_idle_timer()
                    return new_session_id

                # Display the command that creates the async session
                from cai.util import cli_print_tool_output

                # Create args for display
                label = getattr(ACTIVE_SESSIONS.get(new_session_id), 'friendly_id', None) or new_session_id
                session_creation_args = {
                    "command": command,
                    "args": "",
                    "session_id": label,
                    "async_mode": True
                }

                # Create execution info
                execution_info = {
                    "status": "session_created",
                    "environment": f"Container({container_id[:12]})",
                    "host": container_workspace,
                    "session_id": label
                }

                # Get initial output if any
                session = ACTIVE_SESSIONS.get(new_session_id)
                initial_output = ""
                if session:
                    time.sleep(0.2)  # Wait a moment for initial output
                    initial_output = session.get_new_output(mark_position=True)

                # Format the output message
                output_msg = f"Started async session {label} in container {container_id[:12]}. Use this ID to interact."
                if initial_output:
                    output_msg += f"\n\n{initial_output}"

                # Get agent token info
                token_info = _get_agent_token_info()
                # Display the session creation command and initial output
                cli_print_tool_output(
                    tool_name="generic_linux_command",
                    args=session_creation_args,
                    output=output_msg,
                    execution_info=execution_info,
                    token_info=token_info,
                    streaming=False
                )

                # For async sessions, switch back to idle mode after session creation
                stop_active_timer()
                start_idle_timer()
                return f"Started async session {label} in container {container_id[:12]}. Use this ID to interact." # noqa E501

            # Delegate actual container execution to runners/docker.py
            from cai.tools.runners.docker import run_docker
            return run_docker(command, container_id, stdout, timeout, stream, call_id, tool_name, args)

        # --- CTF Execution ---
        
        if ctf and os.getenv('CTF_INSIDE', "True").lower() == "true":
            # If streaming is enabled and we have a call_id, show streaming UI for CTF too
            if stream:
                # Import the streaming utilities from util
                from cai.util import start_tool_streaming, update_tool_streaming, finish_tool_streaming
                
                # If args were provided (e.g., from execute_code), use them
                # Otherwise create args dictionary with standardized format
                if args is not None:
                    tool_args: dict[str, Any] = args.copy() if isinstance(args, dict) else {"args": str(args)}
                    # Add CTF-specific info
                    tool_args["environment"] = "CTF"
                    tool_args["workspace"] = os.path.basename(_get_workspace_dir())
                    tool_args["full_command"] = command
                else:
                    tool_args: dict[str, Any] = {
                        "command": cmd_name,
                        "args": cmd_args if cmd_args.strip() else "",
                        "full_command": command,
                        "environment": "CTF",
                        "workspace": os.path.basename(_get_workspace_dir())
                    }
                
                # Add refresh rate info for generic_linux_command
                if tool_name == "generic_linux_command":
                    tool_args["refresh_rate"] = 2
                
                # Get token info for agent display
                token_info = _get_agent_token_info()
                
                # Initialize the streaming session with a consistent call_id format
                call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)
                
                target_dir = _get_workspace_dir()
                #full_command = f"cd '{target_dir}' && {command}"
                full_command = command
                # Update with "executing" status
                update_tool_streaming(
                    tool_name, 
                    tool_args, 
                    f"Executing in CTF environment: {full_command}\n\nWaiting for response...", 
                    call_id,
                    token_info
                )
                
                try:
                    # Execute the command and get the output
                    start_time = time.time()
                    output = ctf.get_shell(full_command, timeout=timeout)
                    execution_time = time.time() - start_time
                    
                    # Calculate execution info
                    execution_info = {
                        "status": "completed",
                        "environment": "CTF",
                        "tool_time": execution_time
                    }
                    
                    # Complete the streaming with final output
                    finish_tool_streaming(tool_name, tool_args, output, call_id, execution_info, token_info)
                    
                    # Switch back to idle mode after CTF command completes
                    stop_active_timer()
                    start_idle_timer()
                    return output
                    
                except Exception as e:
                    # Handle errors in CTF execution
                    error_msg = f"Error executing CTF command: {str(e)}"
                    execution_info = {
                        "status": "error",
                        "environment": "CTF",
                        "error": str(e)
                    }
                    
                    # Complete the streaming with error output
                    finish_tool_streaming(tool_name, tool_args, error_msg, call_id, execution_info, token_info)
                    
                    # Switch back to idle mode after error
                    stop_active_timer()
                    start_idle_timer()
                    return error_msg
            else:
                # Standard non-streaming CTF execution
                result = _run_ctf(ctf, command, stdout, timeout, _get_workspace_dir(), stream)
            
                # Switch back to idle mode after CTF command completes
                stop_active_timer()
                start_idle_timer()
                return result

        # --- SSH Execution ---
        if is_ssh_env:
            # If streaming is enabled, show streaming UI for SSH too
            if stream:
                # Import the streaming utilities from util
                from cai.util import start_tool_streaming, update_tool_streaming, finish_tool_streaming
                
                # Add SSH connection info for display
                ssh_user = os.environ.get('SSH_USER', 'user')
                ssh_host = os.environ.get('SSH_HOST', 'host')
                ssh_connection = f"{ssh_user}@{ssh_host}"
                
                # If args were provided (e.g., from execute_code), use them
                # Otherwise create args dictionary with standardized format
                if args is not None:
                    tool_args: dict[str, Any] = args.copy() if isinstance(args, dict) else {"args": str(args)}
                    # Add SSH-specific info
                    tool_args["ssh_host"] = ssh_connection
                    tool_args["environment"] = "SSH"
                    tool_args["full_command"] = command
                else:
                    tool_args: dict[str, Any] = {
                        "command": cmd_name,
                        "args": cmd_args if cmd_args.strip() else "",
                        "full_command": command,
                        "ssh_host": ssh_connection,
                        "environment": "SSH"
                    }
                
                # Add refresh rate info for generic_linux_command
                if tool_name == "generic_linux_command":
                    tool_args["refresh_rate"] = 2
                
                # Get token info for agent display
                token_info = _get_agent_token_info()
                
                # Initialize streaming session with a consistent call_id format
                call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)
                
                # Update with "executing" status  
                update_tool_streaming(
                    tool_name, 
                    tool_args, 
                    f"Executing on {ssh_connection}: {command}\n\nWaiting for response...", 
                    call_id,
                    token_info
                )
                
                try:
                    # Construct SSH command for execution
                    ssh_pass = os.environ.get('SSH_PASS')
                    if ssh_pass:
                        ssh_cmd_list = ["sshpass", "-p", ssh_pass, "ssh", ssh_connection]
                    else:
                        ssh_cmd_list = ["ssh", ssh_connection]
                    ssh_cmd_list.append(command)
                    
                    # Execute the command and get the output
                    start_time = time.time()
                    result = subprocess.run(
                        ssh_cmd_list,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=timeout
                    )
                    execution_time = time.time() - start_time
                    
                    # Get command output
                    output = result.stdout if result.stdout else result.stderr
                    
                    # Add SSH connection info to the output for clarity
                    result_with_info = f"Command executed on {ssh_connection}:\n\n{output}"
                    
                    # Determine status based on return code
                    status = "completed" if result.returncode == 0 else "error"
                    
                    # Calculate execution info
                    execution_info = {
                        "status": status,
                        "environment": "SSH",
                        "host": ssh_connection,
                        "return_code": result.returncode,
                        "tool_time": execution_time
                    }
                    
                    # Get agent token info
                    token_info = _get_agent_token_info()
                    
                    # Complete the streaming with final output
                    finish_tool_streaming(tool_name, tool_args, result_with_info, call_id, execution_info, token_info)
                    
                    # Switch back to idle mode after SSH command completes
                    stop_active_timer()
                    start_idle_timer()
                    return output.strip()
                    
                except subprocess.TimeoutExpired as e:
                    # Handle timeout errors
                    error_output = e.stdout if e.stdout else str(e)
                    error_msg = f"Command timed out after {timeout} seconds\n{error_output}"
                    
                    execution_info = {
                        "status": "timeout",
                        "environment": "SSH",
                        "host": ssh_connection,
                        "error": str(e)
                    }
                    
                    # Get agent token info
                    token_info = _get_agent_token_info()
                    
                    # Complete the streaming with timeout error
                    finish_tool_streaming(tool_name, tool_args, error_msg, call_id, execution_info, token_info)
                    
                    # Switch back to idle mode after timeout
                    stop_active_timer()
                    start_idle_timer()
                    return error_msg
                    
                except Exception as e:
                    # Handle other errors
                    error_msg = f"Error executing SSH command: {str(e)}"
                    
                    execution_info = {
                        "status": "error",
                        "environment": "SSH",
                        "host": ssh_connection,
                        "error": str(e)
                    }
                    
                    # Get agent token info
                    token_info = _get_agent_token_info()
                    
                    # Complete the streaming with error
                    finish_tool_streaming(tool_name, tool_args, error_msg, call_id, execution_info, token_info)
                    
                    # Switch back to idle mode after error
                    stop_active_timer()
                    start_idle_timer()
                    return error_msg
            else:
                # Standard non-streaming SSH execution
                result = _run_ssh(command, stdout, timeout, _get_workspace_dir(), stream)
            
                # Switch back to idle mode after SSH command completes
                stop_active_timer()
                start_idle_timer()
                return result

        # --- Local Execution (Default Fallback) ---
        # Let _run_local handle determining the host workspace
        # Handle Async Session Creation Locally
        # Only create new session if no session_id is provided
        if async_mode and not session_id:
            # create_shell_session uses _get_workspace_dir() when container_id is None
            new_session_id = create_shell_session(command)
            if isinstance(new_session_id, str) and "Failed" in new_session_id:  # Check failure
                # Switch back to idle mode before returning error
                stop_active_timer()
                start_idle_timer()
                return new_session_id
            
            # Display the command that creates the async session
            from cai.util import cli_print_tool_output
            
            # Retrieve the actual workspace dir the session is using
            session = ACTIVE_SESSIONS.get(new_session_id)
            actual_workspace = session.workspace_dir if session else "unknown"
            
            # Create args for display
            label = getattr(session, 'friendly_id', None) or new_session_id
            session_creation_args = {
                "command": command,
                "args": "",
                "session_id": label,
                "async_mode": True
            }
            
            # Create execution info
            execution_info = {
                "status": "session_created",
                "environment": "Local",
                "host": os.path.basename(actual_workspace),
                "session_id": label
            }
            
            # Get initial output if any
            initial_output = ""
            if session:
                time.sleep(0.2)  # Allow session buffer to populate
                initial_output = session.get_new_output(mark_position=True)
            
            # Format the output message
            output_msg = f"Started async session {label} locally. Use this ID to interact."
            if initial_output:
                output_msg += f"\n\n{initial_output}"
            
            # Display the session creation command and initial output
            cli_print_tool_output(
                tool_name="generic_linux_command",
                args=session_creation_args,
                output=output_msg,
                execution_info=execution_info,
                token_info=_get_agent_token_info(),
                streaming=False
            )
            
            # For async, switch back to idle mode after session creation
            stop_active_timer()
            start_idle_timer()
            return f"Started async session {label} locally. Use this ID to interact."

        # Handle Synchronous Execution Locally
        # Pass stream parameter as provided (not always True)
        # In parallel mode, stream will be False since Runner.run() is non-streaming
        result = _run_local(
            command, 
            stdout, 
            timeout, 
            stream=stream,  # Use the stream parameter passed to run_command
            call_id=call_id,
            tool_name=tool_name,
            workspace_dir=_get_workspace_dir(),
            custom_args=args
        )
        
        stop_active_timer()
        start_idle_timer()
        return result
    except Exception as e:
        stop_active_timer()
        start_idle_timer()
        raise
