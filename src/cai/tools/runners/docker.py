"""Docker/container runner implementation (extracted from common.py).

Provides `run_docker_async` and `run_docker` to execute commands inside
Docker containers and stream or return output, with sensible fallbacks
to local execution when needed.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
import uuid

from wasabi import color
from cai.tools.base import process_tool_output


async def run_docker_async(
    command,
    container_id,
    stdout=False,
    timeout=100,
    stream=False,
    call_id=None,
    tool_name=None,
    args=None,
):
    """Async version of Docker command execution (extracted from common.py)."""
    import asyncio

    from cai.tools.agent_info import _get_agent_token_info
    from cai.tools.workspace import _get_container_workspace_path
    from cai.util import (
        finish_tool_streaming,
        start_active_timer,
        start_idle_timer,
        start_tool_streaming,
        stop_active_timer,
        stop_idle_timer,
        update_tool_streaming,
    )

    stop_idle_timer()
    start_active_timer()

    try:
        container_workspace = _get_container_workspace_path()

        parts = command.strip().split(" ", 1)
        cmd_name = parts[0] if parts else ""
        cmd_args = parts[1] if len(parts) > 1 else ""

        if not tool_name:
            tool_name = f"{cmd_name}_command" if cmd_name else "command"

        docker_cmd_list = [
            "docker",
            "exec",
            "-w",
            container_workspace,
            container_id,
            "sh",
            "-c",
            command,
        ]

        if stream:
            # Prepare streaming args
            if args and isinstance(args, dict):
                tool_args = args.copy()
                tool_args["container"] = container_id[:12]
                tool_args["environment"] = "Container"
                tool_args["workspace"] = container_workspace
                tool_args["full_command"] = command
            else:
                tool_args = {
                    "command": cmd_name,
                    "args": cmd_args if cmd_args.strip() else "",
                    "full_command": command,
                    "container": container_id[:12],
                    "environment": "Container",
                    "workspace": container_workspace,
                }

            if not call_id:
                call_id = f"cmd_{cmd_name}_{str(uuid.uuid4())[:8]}"

            # Check recon-skip before starting streaming in container
            try:
                from cai.tools.common import _should_skip_recon

                if _should_skip_recon(tool_name, tool_args, None):
                    return {"status": "skipped", "reason": "resume_skip_recon", "tool": tool_name}
            except Exception:
                pass

            token_info = _get_agent_token_info()
            call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)

            process = await asyncio.create_subprocess_exec(
                *docker_cmd_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            output_buffer = []
            buffer_size = 0
            update_interval = 3 if tool_name == "generic_linux_command" else 10

            start_time = time.time()
            last_output = time.time()
            while True:
                if process.returncode is not None:
                    break
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
                    if line:
                        output_buffer.append(line.decode("utf-8", errors="replace"))
                        buffer_size += 1
                        last_output = time.time()
                        if buffer_size >= update_interval:
                            update_tool_streaming(
                                tool_name, tool_args, "".join(output_buffer), call_id, token_info
                            )
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

            stderr_data = await process.stderr.read()
            if stderr_data:
                stderr_str = stderr_data.decode("utf-8", errors="replace")
                output_buffer.append("\nERROR OUTPUT:\n" + stderr_str)

            final_output = "".join(output_buffer)
            if return_code != 0:
                final_output += f"\nCommand exited with code {return_code}"

            execution_info = {
                "status": "completed" if return_code == 0 else "error",
                "return_code": return_code,
                "environment": "Container",
                "host": container_id[:12],
                "tool_time": execution_time,
            }

            finish_tool_streaming(
                tool_name, tool_args, final_output, call_id, execution_info, token_info
            )
            try:
                t0 = time.time()
                res = process_tool_output(tool_name, final_output)
                t1 = time.time()
                try:
                    with open("/tmp/cai_tool_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} DOCKER_STREAM processed tool={tool_name} dur={(t1-t0):.3f} size={len(final_output)}\n")
                except Exception:
                    pass
                return res
            except Exception:
                try:
                    with open("/tmp/cai_tool_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} DOCKER_STREAM process_tool_output FAILED tool={tool_name} size={len(final_output)}\n")
                except Exception:
                    pass
                return final_output

        else:
            # Non-streaming async execution
            start_time = time.time()
            # Before executing, honor recon-skip heuristics
            try:
                from cai.tools.common import _should_skip_recon

                display_args = (
                    args
                    if args is not None
                    else {
                        "command": cmd_name,
                        "args": cmd_args,
                        "full_command": command,
                        "container": container_id[:12],
                        "workspace": container_workspace,
                    }
                )
                if _should_skip_recon(tool_name, display_args, None):
                    return {"status": "skipped", "reason": "resume_skip_recon", "tool": tool_name}
            except Exception:
                pass

            process = await asyncio.create_subprocess_exec(
                *docker_cmd_list, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise subprocess.TimeoutExpired(command, timeout)

            output = (
                stdout_data.decode("utf-8", errors="replace")
                if stdout_data
                else (stderr_data.decode("utf-8", errors="replace") if stderr_data else "")
            )

            if stdout:
                context_msg = f"(docker:{container_id[:12]}:{container_workspace})"
                print(f"\033[32m{context_msg} $ {command}\n{output}\033[0m")

            token_info = _get_agent_token_info()
            is_parallel = False
            if token_info and token_info.get("agent_id"):
                agent_id = token_info.get("agent_id")
                if agent_id and agent_id.startswith("P") and agent_id[1:].isdigit():
                    if int(os.getenv("CAI_PARALLEL", "1")) > 1:
                        is_parallel = True

            streaming_enabled = os.getenv("CAI_STREAM", "false").lower() == "true"
            if streaming_enabled and is_parallel:
                from cai.util import cli_print_tool_output

                execution_time = time.time() - start_time
                parts = command.strip().split(" ", 1)
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"container_{cmd_name}_{str(uuid.uuid4())[:8]}"

                execution_info = {
                    "status": "completed" if process.returncode == 0 else "error",
                    "return_code": process.returncode,
                    "environment": "Container",
                    "host": container_id[:12],
                    "tool_time": execution_time,
                }

                display_args = (
                    args
                    if args is not None
                    else {
                        "command": parts[0] if parts else command,
                        "args": parts[1] if len(parts) > 1 else "",
                        "full_command": command,
                        "container": container_id[:12],
                        "workspace": container_workspace,
                    }
                )

                cli_print_tool_output(
                    tool_name=tool_name or "generic_linux_command",
                    args=display_args,
                    output=output.strip(),
                    call_id=call_id,
                    execution_info=execution_info,
                    token_info=token_info,
                    streaming=False,
                )

            try:
                t0 = time.time()
                res = process_tool_output(tool_name, output.strip())
                t1 = time.time()
                try:
                    with open("/tmp/cai_tool_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} DOCKER_ASYNC processed tool={tool_name} dur={(t1-t0):.3f} size={len(output)}\n")
                except Exception:
                    pass
                return res
            except Exception:
                try:
                    with open("/tmp/cai_tool_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} DOCKER_ASYNC process_tool_output FAILED tool={tool_name} size={len(output)}\n")
                except Exception:
                    pass
                return output.strip()

    except Exception as e:
        error_msg = f"Error executing command in container: {str(e)}"
        print(color(error_msg, fg="red"))
        return error_msg
    finally:
        stop_active_timer()
        start_idle_timer()


def run_docker(
    command,
    container_id,
    stdout=False,
    timeout=100,
    stream=False,
    call_id=None,
    tool_name=None,
    args=None,
):
    """Synchronous docker execution (extracted and consolidated).

    This function implements both streaming and non-streaming container
    execution and preserves the fallback-to-local behavior.
    """
    from cai.tools.agent_info import _get_agent_token_info
    from cai.tools.runners.local import run_local as _run_local
    from cai.tools.workspace import _get_container_workspace_path
    from cai.util import (
        finish_tool_streaming,
        start_active_timer,
        start_idle_timer,
        start_tool_streaming,
        stop_active_timer,
        stop_idle_timer,
        update_tool_streaming,
    )

    stop_idle_timer()
    start_active_timer()

    container_workspace = _get_container_workspace_path()
    context_msg = f"(docker:{container_id[:12]}:{container_workspace})"

    # Streaming container execution
    if stream:
        if args is not None:
            tool_args = args.copy() if isinstance(args, dict) else {"args": str(args)}
            tool_args["container"] = container_id[:12]
            tool_args["environment"] = "Container"
            tool_args["workspace"] = container_workspace
            tool_args["full_command"] = command
        else:
            parts = command.strip().split(" ", 1)
            tool_args = {
                "command": parts[0] if parts else command,
                "args": parts[1] if len(parts) > 1 else "",
                "full_command": command,
                "container": container_id[:12],
                "environment": "Container",
                "workspace": container_workspace,
            }

        if tool_name == "generic_linux_command":
            tool_args["refresh_rate"] = 2

        # Check recon-skip before starting streaming in container
        try:
            from cai.tools.common import _should_skip_recon

            if _should_skip_recon(tool_name, tool_args, None):
                return {"status": "skipped", "reason": "resume_skip_recon", "tool": tool_name}
        except Exception:
            pass

        token_info = _get_agent_token_info()
        call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)

        # Ensure workspace exists inside container
        mkdir_cmd = ["docker", "exec", container_id, "mkdir", "-p", container_workspace]
        subprocess.run(mkdir_cmd, capture_output=True, text=True, check=False, timeout=10)

        docker_exec_cmd = (
            "docker exec -w "
            f"{shlex.quote(container_workspace)} "
            f"{shlex.quote(container_id)} sh -c "
            f"{shlex.quote(command)}"
        )

        try:
            start_time = time.time()
            # Before executing, honor recon-skip heuristics for sync container run
            try:
                from cai.tools.common import _should_skip_recon

                if _should_skip_recon(tool_name, tool_args, None):
                    stop_active_timer()
                    start_idle_timer()
                    return {"status": "skipped", "reason": "resume_skip_recon", "tool": tool_name}
            except Exception:
                pass

            process = subprocess.Popen(
                docker_exec_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=os.getcwd(),
            )

            output_buffer = []
            buffer_size = 0
            update_interval = 10

            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                output_buffer.append(line)
                buffer_size += 1
                if buffer_size >= update_interval:
                    current_output = "".join(output_buffer)
                    token_info = _get_agent_token_info()
                    update_tool_streaming(tool_name, tool_args, current_output, call_id, token_info)
                    buffer_size = 0

            process.stdout.close()
            return_code = process.wait(timeout=timeout)
            execution_time = time.time() - start_time

            stderr_data = process.stderr.read()
            if stderr_data:
                output_buffer.append("\nERROR OUTPUT:\n" + stderr_data)

            final_output = "".join(output_buffer)
            if return_code != 0:
                final_output += f"\nCommand exited with code {return_code}"

            execution_info = {
                "status": "completed" if return_code == 0 else "error",
                "return_code": return_code,
                "environment": "Container",
                "host": container_id[:12],
                "tool_time": execution_time,
            }

            token_info = _get_agent_token_info()
            finish_tool_streaming(
                tool_name, tool_args, final_output, call_id, execution_info, token_info
            )
            stop_active_timer()
            start_idle_timer()
            try:
                t0 = time.time()
                res = process_tool_output(tool_name, final_output)
                t1 = time.time()
                try:
                    with open("/tmp/cai_tool_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} DOCKER_STREAM_SYNC processed tool={tool_name} dur={(t1-t0):.3f} size={len(final_output)}\n")
                except Exception:
                    pass
                return res
            except Exception:
                try:
                    with open("/tmp/cai_tool_debug.log", "a") as _f:
                        _f.write(f"{time.time():.3f} DOCKER_STREAM_SYNC process_tool_output FAILED tool={tool_name} size={len(final_output)}\n")
                except Exception:
                    pass
                return final_output

        except subprocess.TimeoutExpired as e:
            error_output = e.stdout if hasattr(e, "stdout") and e.stdout else str(e)
            error_msg = f"Command timed out after {timeout} seconds\n{error_output}"
            if stdout:
                print(f"\033[33m{context_msg} $ {command}\nTIMEOUT\033[0m")
                print(color("Attempting execution on host instead.", fg="yellow"))
            stop_active_timer()
            start_idle_timer()
            # Fallback to local execution
            return _run_local(command, stdout, timeout, False, None, tool_name, os.getcwd(), args)
        except Exception as e:
            error_msg = f"Error executing command in container: {str(e)}"
            print(color(error_msg, fg="red"))
            print(color("Attempting execution on host instead.", fg="yellow"))
            stop_active_timer()
            start_idle_timer()
            return _run_local(command, stdout, timeout, False, None, tool_name, os.getcwd(), args)

    # Handle synchronous execution in container (non-stream)
    try:
        mkdir_cmd = ["docker", "exec", container_id, "mkdir", "-p", container_workspace]
        subprocess.run(mkdir_cmd, capture_output=True, text=True, check=False, timeout=10)

        cmd_list = ["docker", "exec", "-w", container_workspace, container_id, "sh", "-c", command]
        result = subprocess.run(
            cmd_list, capture_output=True, text=True, check=False, timeout=timeout
        )

        output = result.stdout if result.stdout else result.stderr
        output = output.strip()

        if stdout and not stream:
            print(f"\033[32m{context_msg} $ {command}\n{output}\033[0m")

        if result.returncode != 0 and result.stderr and "is not running" in result.stderr:
            print(
                color(
                    f"{context_msg} Container is not running. Attempting execution on host instead.",
                    fg="yellow",
                )
            )
            stop_active_timer()
            start_idle_timer()
            return _run_local(
                command, stdout, timeout, stream, call_id, tool_name, os.getcwd(), args
            )

        if not stream:
            token_info = _get_agent_token_info()
            is_parallel = False
            if token_info and token_info.get("agent_id"):
                agent_id = token_info.get("agent_id")
                if agent_id and agent_id.startswith("P") and agent_id[1:].isdigit():
                    if int(os.getenv("CAI_PARALLEL", "1")) > 1:
                        is_parallel = True

            streaming_enabled = os.getenv("CAI_STREAM", "false").lower() == "true"
            if streaming_enabled and is_parallel:
                from cai.util import cli_print_tool_output

                execution_time = 0
                parts = command.strip().split(" ", 1)
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"container_{cmd_name}_{str(uuid.uuid4())[:8]}"

                execution_info = {
                    "status": "completed" if result.returncode == 0 else "error",
                    "return_code": result.returncode,
                    "environment": "Container",
                    "host": container_id[:12],
                    "tool_time": execution_time,
                }

                display_args = (
                    args
                    if args is not None
                    else {
                        "command": parts[0] if parts else command,
                        "args": parts[1] if len(parts) > 1 else "",
                        "full_command": command,
                        "container": container_id[:12],
                        "workspace": container_workspace,
                    }
                )

                cli_print_tool_output(
                    tool_name=tool_name or "generic_linux_command",
                    args=display_args,
                    output=output,
                    call_id=call_id,
                    execution_info=execution_info,
                    token_info=token_info,
                    streaming=False,
                )

        stop_active_timer()
        start_idle_timer()
        try:
            t0 = time.time()
            res = process_tool_output(tool_name, output)
            t1 = time.time()
            try:
                with open("/tmp/cai_tool_debug.log", "a") as _f:
                    _f.write(f"{time.time():.3f} DOCKER_SYNC processed tool={tool_name} dur={(t1-t0):.3f} size={len(output)}\n")
            except Exception:
                pass
            return res
        except Exception:
            try:
                with open("/tmp/cai_tool_debug.log", "a") as _f:
                    _f.write(f"{time.time():.3f} DOCKER_SYNC process_tool_output FAILED tool={tool_name} size={len(output)}\n")
            except Exception:
                pass
            return output

    except subprocess.TimeoutExpired:
        _timeout_msg = "Timeout executing command in container."
        if stdout:
            print(f"\033[33m{context_msg} $ {command}\nTIMEOUT\033[0m")
            print(color("Attempting execution on host instead.", fg="yellow"))
        stop_active_timer()
        start_idle_timer()
        return _run_local(command, stdout, timeout, stream, call_id, tool_name, os.getcwd(), args)
    except Exception as e:
        error_msg = f"Error executing command in container: {str(e)}"
        print(color(f"{context_msg} {error_msg}", fg="red"))
        print(color("Attempting execution on host instead.", fg="yellow"))
        stop_active_timer()
        start_idle_timer()
        return _run_local(command, stdout, timeout, stream, call_id, tool_name, os.getcwd(), args)
