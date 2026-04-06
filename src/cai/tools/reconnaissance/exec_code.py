"""
Tool for executing code via LLM tool calls.
"""
import os
import re
import uuid
import shlex
import base64

from cai.tools.common import run_command  # pylint: disable=import-error
from cai.sdk.agents import function_tool


@function_tool
def execute_code(
    code: str = "",
    language: str = "python",
    filename: str = "exploit",
    timeout: int = 100,
    persist: bool = False,
) -> str:
    """
    Create a file code store it and execute it

    This tool allows for executing code provided in different
    programming languages. It creates a permanent file with the provided code
    and executes it using the appropriate interpreter. You can exec this
    code as many times as you want using `generic_linux_command` tool.

    Priorize: Python and Perl

    Args:
        code: The code snippet to execute
        language: Programming language to use (default: python)
        filename: Base name for the file without extension (default: exploit)
        timeout: Timeout for the execution (default: 100 seconds)
                Use high timeout for long running code 
                Use low timeout for short running code
    Returns:
        Command output or error message from execution
    """

    # Basic validation
    if not code:
        return "No code provided to execute"

    if len(code) > 200_000:
        return "Error: code too large"

    # Normalize language and supported extensions/executors
    language = (language or "").lower()
    extensions = {
        "python": "py",
        "php": "php",
        "bash": "sh",
        "shell": "sh",
        "ruby": "rb",
        "perl": "pl",
        "golang": "go",
        "go": "go",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "rust": "rs",
        "csharp": "cs",
        "cs": "cs",
        "java": "java",
        "kotlin": "kt",
        "c": "c",
        "cpp": "cpp",
        "c++": "cpp",
    }

    if language not in extensions:
        return f"Unsupported language: {language}"

    # Validate filename (no paths, limited charset)
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", filename):
        return "Invalid filename: only A-Za-z0-9_- allowed, max 64 chars"

    ext = extensions[language]
    full_filename = f"{filename}.{ext}"

    # Create a unique temporary directory in the target environment (so that
    # run_command which may execute inside a container will place files there).
    run_id = str(uuid.uuid4())[:8]
    tmp_dir = f"/tmp/cai_exec_{run_id}_{filename}"

    # Encode content as base64 to safely transport into the shell environment
    encoded = base64.b64encode(code.encode("utf-8", errors="replace")).decode("ascii")
    quoted_encoded = shlex.quote(encoded)
    target_path = os.path.join(tmp_dir, full_filename)
    quoted_target = shlex.quote(target_path)

    # Prepare file creation command that decodes base64 into the target file
    create_cmd = f"mkdir -p {shlex.quote(tmp_dir)} && echo {quoted_encoded} | base64 -d > {quoted_target}"
    res = run_command(create_cmd, stream=False, tool_name="_internal_file_creation")
    if isinstance(res, str) and "error" in res.lower():
        return f"Failed to create code file: {res}"

    # Build execution commands depending on language
    # We always execute from inside tmp_dir so compiled artifacts do not escape
    if language in ["python", "py"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && python3 {shlex.quote(full_filename)}"
    elif language in ["php"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && php {shlex.quote(full_filename)}"
    elif language in ["bash", "sh", "shell"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && bash {shlex.quote(full_filename)}"
    elif language in ["ruby", "rb"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ruby {shlex.quote(full_filename)}"
    elif language in ["perl", "pl"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && perl {shlex.quote(full_filename)}"
    elif language in ["golang", "go"]:
        # Make sure file is main.go for `go run`
        run_command(f"mkdir -p {shlex.quote(tmp_dir)}", stream=False, tool_name="_internal_setup")
        run_command(f"cp {quoted_target} {shlex.quote(os.path.join(tmp_dir, 'main.go'))}", stream=False, tool_name="_internal_setup")
        run_command(f"cd {shlex.quote(tmp_dir)} && go mod init temp || true", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && go run main.go"
    elif language in ["javascript", "js"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && node {shlex.quote(full_filename)}"
    elif language in ["typescript", "ts"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ts-node {shlex.quote(full_filename)}"
    elif language in ["rust", "rs"]:
        run_command(f"cd {shlex.quote(tmp_dir)} && rustc {shlex.quote(full_filename)} -o {shlex.quote(filename)}", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ./{shlex.quote(filename)}"
    elif language in ["csharp", "cs"]:
        run_command(f"cd {shlex.quote(tmp_dir)} && dotnet build {shlex.quote(full_filename)}", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && dotnet run {shlex.quote(full_filename)}"
    elif language in ["java"]:
        run_command(f"cd {shlex.quote(tmp_dir)} && javac {shlex.quote(full_filename)}", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && java {shlex.quote(filename)}"
    elif language in ["kotlin", "kt"]:
        run_command(f"cd {shlex.quote(tmp_dir)} && kotlinc {shlex.quote(full_filename)} -include-runtime -d {shlex.quote(filename)}.jar", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && java -jar {shlex.quote(filename)}.jar"
    elif language in ["c"]:
        run_command(f"cd {shlex.quote(tmp_dir)} && gcc {shlex.quote(full_filename)} -o {shlex.quote(filename)}", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ./{shlex.quote(filename)}"
    elif language in ["cpp"]:
        run_command(f"cd {shlex.quote(tmp_dir)} && g++ {shlex.quote(full_filename)} -o {shlex.quote(filename)}", stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ./{shlex.quote(filename)}"
    else:
        # Should not hit due to earlier check
        return f"Unsupported language: {language}"

    tool_args = {
        "command": "execute",
        "language": language,
        "filename": filename,
        "code": code,
        "timeout": timeout,
    }

    try:
        output = run_command(exec_cmd, timeout=timeout, stream=True, tool_name="execute_code", args=tool_args)
    finally:
        # Clean up temporary directory unless persistence requested
        if not persist:
            try:
                run_command(f"rm -rf {shlex.quote(tmp_dir)}", stream=False, tool_name="_internal_cleanup")
            except Exception:
                pass

    return output
