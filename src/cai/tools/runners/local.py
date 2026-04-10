"""Local runner implementation (extracted from common.py).

Provides `run_local` and `run_local_async` which mirror the original
implementations from `cai.tools.common` but live in a dedicated module.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

from wasabi import color


async def run_local_async(
    command,
    stdout=False,
    timeout=100,
    stream=False,
    call_id=None,
    tool_name=None,
    workspace_dir=None,
    custom_args=None,
):
    """Async version of local command execution (uses asyncio subprocess).

    This is a near-direct extraction of `_run_local_async` from `common.py`.
    """
    import asyncio
    import shlex

    from cai.tools.agent_info import _get_agent_token_info
    from cai.tools.workspace import _get_workspace_dir

    # Import timers/utilities lazily to avoid import cycles
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

    process_start_time = time.time()
    try:
        target_dir = workspace_dir or _get_workspace_dir()
        _original_cmd_for_msg = command
        _context_msg = f"(local:{target_dir})"

        if stream:
            # Streamed execution using asyncio create_subprocess_shell
            parts = command.strip().split(" ", 1)
            cmd_var = parts[0] if parts else ""
            args_param_val = parts[1] if len(parts) > 1 else ""

            if not tool_name:
                tool_name = f"{cmd_var}_command" if cmd_var else "command"

            tool_args = {}
            if cmd_var:
                tool_args["command"] = cmd_var
            if args_param_val and args_param_val.strip():
                tool_args["args"] = args_param_val

            tool_args["workspace"] = os.path.basename(target_dir)
            tool_args["full_command"] = command

            if custom_args is not None and isinstance(custom_args, dict):
                for k, v in custom_args.items():
                    tool_args[k] = v

            if not call_id:
                call_id = f"cmd_{cmd_var}_{str(uuid.uuid4())[:8]}"

            token_info = _get_agent_token_info()
            call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)

            # Prefer exec-style subprocess for streaming when possible
            try:
                exec_list = shlex.split(command)
                process = await asyncio.create_subprocess_exec(
                    *exec_list,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=target_dir,
                )
            except Exception:
                # Fall back to shell mode for complex commands that require
                # shell features (pipes, redirection, etc.). Streaming will
                # still behave the same in that case.
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=target_dir,
                )

            output_buffer = []
            buffer_size = 0
            update_interval = 10
            if tool_name == "generic_linux_command":
                update_interval = 3

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
                        output_buffer.append("\n[Terminated: idle 10s, likely waiting for input]")
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

            process_execution_time = time.time() - process_start_time

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
                "environment": "Local",
                "host": os.path.basename(target_dir),
                "tool_time": process_execution_time,
            }

            finish_tool_streaming(
                tool_name, tool_args, final_output, call_id, execution_info, token_info
            )
            return final_output

        else:
            # Non-streaming async execution behaves like sync wrapper
            # Non-streaming async execution: prefer exec-mode for safety
            try:
                exec_list = shlex.split(command)
                process = await asyncio.create_subprocess_exec(
                    *exec_list,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=target_dir,
                )
            except Exception:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=target_dir,
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
                    out_task = asyncio.create_task(process.stdout.read(4096))
                    err_task = asyncio.create_task(process.stderr.read(4096))
                    done, pending = await asyncio.wait(
                        [out_task, err_task], timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                    )
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
                            await process.wait()
                        except Exception:
                            pass

            stdout_data = b"".join(stdout_chunks)
            stderr_data = b"".join(stderr_chunks)

            output = (
                stdout_data.decode("utf-8", errors="replace")
                if stdout_data
                else (stderr_data.decode("utf-8", errors="replace") if stderr_data else "")
            )

            process_execution_time = time.time() - process_start_time

            # If we're in streaming/parallel mode, present a panel
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

                parts = command.strip().split(" ", 1)
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"{cmd_name}_{str(uuid.uuid4())[:8]}"
                execution_info = {
                    "status": "completed",
                    "return_code": 0,
                    "environment": "Local",
                    "host": os.path.basename(target_dir),
                    "tool_time": process_execution_time,
                }
                display_args = (
                    custom_args
                    if custom_args is not None
                    else {
                        "command": parts[0] if parts else command,
                        "args": parts[1] if len(parts) > 1 else "",
                        "full_command": command,
                        "workspace": os.path.basename(target_dir),
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

            stop_active_timer()
            start_idle_timer()
            return output.strip()

    except subprocess.TimeoutExpired as e:
        error_output = e.stdout if hasattr(e, "stdout") and e.stdout else str(e)
        error_msg = f"Command timed out after {timeout} seconds\n{error_output}"
        if stream and call_id:
            from cai.util import finish_tool_streaming

            parts = command.strip().split(" ", 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            tool_args = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir),
            }
            execution_info = {
                "status": "timeout",
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir),
            }
            token_info = _get_agent_token_info()
            finish_tool_streaming(
                tool_name or f"{cmd_var}_command",
                tool_args,
                error_msg,
                call_id,
                execution_info,
                token_info,
            )
        if stdout:
            print("\033[32m" + error_msg + "\033[0m")
            return error_msg
        return error_msg
    except Exception as e:
        error_msg = f"Error executing local command: {e}"
        if stream and call_id:
            from cai.util import finish_tool_streaming

            parts = command.strip().split(" ", 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            tool_args = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir),
            }
            execution_info = {
                "status": "error",
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir),
            }
            token_info = _get_agent_token_info()
            finish_tool_streaming(
                tool_name or f"{cmd_var}_command",
                tool_args,
                error_msg,
                call_id,
                execution_info,
                token_info,
            )
        print(color(error_msg, fg="red"))
        return error_msg


def run_local(
    command,
    stdout=False,
    timeout=100,
    stream=False,
    call_id=None,
    tool_name=None,
    workspace_dir=None,
    custom_args=None,
):
    """Synchronous local execution wrapper (extracted from common.py).

    Intentionally mirrors the original `_run_local` implementation.
    """
    import subprocess

    from cai.tools.agent_info import _get_agent_token_info
    from cai.tools.workspace import _get_workspace_dir
    from cai.util import start_active_timer, stop_idle_timer

    stop_idle_timer()
    start_active_timer()

    process_start_time = time.time()
    try:
        target_dir = workspace_dir or _get_workspace_dir()
        _context_msg = f"(local:{target_dir})"

        if stream:
            from cai.util import finish_tool_streaming, start_tool_streaming, update_tool_streaming

            parts = command.strip().split(" ", 1)
            cmd_var = parts[0] if parts else ""
            args_param_val = parts[1] if len(parts) > 1 else ""
            if not tool_name:
                tool_name = f"{cmd_var}_command" if cmd_var else "command"

            tool_args = {}
            if cmd_var:
                tool_args["command"] = cmd_var
            if args_param_val and args_param_val.strip():
                tool_args["args"] = args_param_val
            tool_args["workspace"] = os.path.basename(target_dir)
            tool_args["full_command"] = command

            if custom_args is not None and isinstance(custom_args, dict):
                for key, value in custom_args.items():
                    tool_args[key] = value

            if not call_id:
                call_id = f"cmd_{cmd_var}_{str(uuid.uuid4())[:8]}"

            token_info = _get_agent_token_info()
            call_id = start_tool_streaming(tool_name, tool_args, call_id, token_info)

            import shlex
            try:
                exec_list = shlex.split(command)
                process = subprocess.Popen(
                    exec_list,
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=target_dir,
                )
            except Exception:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=target_dir,
                )

            output_buffer = []
            buffer_size = 0
            update_interval = 10
            if tool_name == "generic_linux_command":
                update_interval = 3

            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                output_buffer.append(line)
                buffer_size += 1
                if buffer_size >= update_interval:
                    current_output = "".join(output_buffer)
                    update_tool_streaming(tool_name, tool_args, current_output, call_id, token_info)
                    buffer_size = 0

            process.stdout.close()
            return_code = process.wait(timeout=timeout)
            process_execution_time = time.time() - process_start_time

            stderr_data = process.stderr.read()
            if stderr_data:
                output_buffer.append("\nERROR OUTPUT:\n" + stderr_data)

            final_output = "".join(output_buffer)
            if return_code != 0:
                final_output += f"\nCommand exited with code {return_code}"

            execution_info = {
                "status": "completed" if return_code == 0 else "error",
                "return_code": return_code,
                "environment": "Local",
                "host": os.path.basename(target_dir),
                "tool_time": process_execution_time,
            }

            finish_tool_streaming(
                tool_name, tool_args, final_output, call_id, execution_info, token_info
            )
            return final_output

        else:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                cwd=target_dir,
            )
            output = result.stdout if result.stdout else result.stderr

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

                execution_time = time.time() - process_start_time
                parts = command.strip().split(" ", 1)
                if not call_id:
                    cmd_name = parts[0] if parts else "cmd"
                    call_id = f"{cmd_name}_{str(uuid.uuid4())[:8]}"
                execution_info = {
                    "status": "completed" if result.returncode == 0 else "error",
                    "return_code": result.returncode,
                    "environment": "Local",
                    "host": os.path.basename(target_dir),
                    "tool_time": execution_time,
                }
                display_args = (
                    custom_args
                    if custom_args is not None
                    else {
                        "command": parts[0] if parts else command,
                        "args": parts[1] if len(parts) > 1 else "",
                        "full_command": command,
                        "workspace": os.path.basename(target_dir),
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

            return output.strip()
    except subprocess.TimeoutExpired as e:
        error_output = e.stdout if hasattr(e, "stdout") and e.stdout else str(e)
        error_msg = f"Command timed out after {timeout} seconds\n{error_output}"
        if stream and call_id:
            from cai.util import finish_tool_streaming

            parts = command.strip().split(" ", 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            tool_args = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir),
            }
            execution_info = {
                "status": "timeout",
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir),
            }
            token_info = _get_agent_token_info()
            finish_tool_streaming(
                tool_name or f"{cmd_var}_command",
                tool_args,
                error_msg,
                call_id,
                execution_info,
                token_info,
            )
        if stdout:
            print("\033[32m" + error_msg + "\033[0m")
            return error_msg
        return error_msg
    except Exception as e:
        error_msg = f"Error executing local command: {e}"
        if stream and call_id:
            from cai.util import finish_tool_streaming

            parts = command.strip().split(" ", 1)
            cmd_var = parts[0] if parts else ""
            args_var = parts[1] if len(parts) > 1 else ""
            tool_args = {
                "command": cmd_var,
                "args": args_var if args_var.strip() else "",
                "full_command": command,
                "environment": "Local",
                "workspace": os.path.basename(target_dir),
            }
            execution_info = {
                "status": "error",
                "error": str(e),
                "environment": "Local",
                "host": os.path.basename(target_dir),
            }
            token_info = _get_agent_token_info()
            finish_tool_streaming(
                tool_name or f"{cmd_var}_command",
                tool_args,
                error_msg,
                call_id,
                execution_info,
                token_info,
            )
        print(color(error_msg, fg="red"))
        return error_msg
