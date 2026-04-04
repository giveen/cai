"""
Tool for executing code via LLM tool calls.
"""
import os
import base64
from cai.tools.common import run_command, _get_workspace_dir  # pylint: disable=import-error
from cai.sdk.agents import function_tool


@function_tool
def execute_code(code: str = "", language: str = "python",
                filename: str = "exploit", timeout: int = 100, ctf=None) -> str:
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

    if not code:
        return "No code provided to execute"

    # Map file extensions
    extensions = {
        "python": "py",
        "php": "php",
        "bash": "sh",
        "shell": "sh",  # Add shell as alias for bash
        "ruby": "rb",
        "perl": "pl",
        "golang": "go",
        "go": "go",     # Add go as alias for golang
        "javascript": "js",
        "js": "js",     # Add js as alias for javascript
        "typescript": "ts",
        "ts": "ts",     # Add ts as alias for typescript
        "rust": "rs",
        "csharp": "cs",
        "cs": "cs",     # Add cs as alias for csharp
        "java": "java",
        "kotlin": "kt",
        "c": "c",       # Add C language
        "cpp": "cpp",   # Add C++ language
        "c++": "cpp"    # Add C++ language alias
    }
    # Normalize language to lowercase
    language = language.lower()
    ext = extensions.get(language, "txt")
    full_filename = f"{filename}.{ext}"

    # Determine whether code needs to land in a remote environment
    # (container, SSH, or CTF) so we can route the write correctly.
    _active_container = os.getenv("CAI_ACTIVE_CONTAINER", "")
    _is_ssh = all(os.getenv(v) for v in ["SSH_USER", "SSH_HOST"])
    try:
        from cai.cli import ctf_global as _ctf_global  # pylint: disable=import-outside-toplevel
        _use_remote = bool(
            _active_container or _is_ssh or
            (_ctf_global and hasattr(_ctf_global, "get_shell") and
             os.getenv("CTF_INSIDE", "True").lower() == "true")
        )
    except ImportError:
        _use_remote = bool(_active_container or _is_ssh)

    if _use_remote:
        # Encode the code as base64 so any special characters are transmitted
        # safely through the shell without heredoc quoting issues.
        _encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
        create_cmd = f"echo '{_encoded}' | base64 -d > {full_filename}"
        # Use a shell invocation through an arg list so the central runner
        # receives a list while preserving shell redirection semantics.
        result = run_command(["sh", "-c", create_cmd], ctf=ctf, stream=False, tool_name="_internal_file_creation")
        if result and "error" in result.lower():
            return f"Failed to create code file: {result}"
    else:
        # Local: write the file directly with Python I/O — reliable and
        # immune to shell-escaping or EOF-collision issues.
        _target = os.path.join(_get_workspace_dir(), full_filename)
        try:
            with open(_target, "w", encoding="utf-8") as _f:
                _f.write(code)
        except OSError as _e:
            return f"Failed to create code file: {_e}"
    
    # Prepare execution command based on language
    if language in ["python", "py"]:
        exec_cmd = ["python3", full_filename]
    elif language in ["php"]:
        exec_cmd = ["php", full_filename]
    elif language in ["bash", "sh", "shell"]:
        exec_cmd = ["bash", full_filename]
    elif language in ["ruby", "rb"]:
        exec_cmd = ["ruby", full_filename]
    elif language in ["perl", "pl"]:
        exec_cmd = ["perl", full_filename]
    elif language in ["golang", "go"]:
        temp_dir = f"/tmp/go_exec_{filename}"
        run_command(["mkdir", "-p", temp_dir], ctf=ctf, stream=False, tool_name="_internal_setup")
        run_command(["cp", full_filename, f"{temp_dir}/main.go"], ctf=ctf, stream=False, tool_name="_internal_setup")
        # Keep the cd && go mod/init as a shell compound for remote environments
        run_command(["sh", "-c", f"cd {temp_dir} && go mod init temp"], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = f"cd {temp_dir} && go run main.go"
    elif language in ["javascript", "js"]:
        exec_cmd = ["node", full_filename]
    elif language in ["typescript", "ts"]:
        exec_cmd = ["ts-node", full_filename]
    elif language in ["rust", "rs"]:
        # For Rust, we need to compile first
        run_command(["rustc", full_filename, "-o", filename], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = [f"./{filename}"]
    elif language in ["csharp", "cs"]:
        # For C#, compile with dotnet
        run_command(["dotnet", "build", full_filename], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = ["dotnet", "run", full_filename]
    elif language in ["java"]:
        # For Java, compile first
        run_command(["javac", full_filename], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = ["java", filename]
    elif language in ["kotlin", "kt"]:
        # For Kotlin, compile first
        run_command(["kotlinc", full_filename, "-include-runtime", "-d", f"{filename}.jar"], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = ["java", "-jar", f"{filename}.jar"]
    elif language in ["c"]:
        # For C, compile with gcc
        run_command(["gcc", full_filename, "-o", filename], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = [f"./{filename}"]
    elif language in ["cpp", "c++"]:
        # For C++, compile with g++
        run_command(["g++", full_filename, "-o", filename], ctf=ctf, stream=False, tool_name="_internal_setup")
        exec_cmd = [f"./{filename}"]
    else:
        return f"Unsupported language: {language}"

    # Execute the code with syntax-highlighted output
    # Create a custom tool args dictionary to send language and code info to the tool output function
    tool_args = {
        "command": "execute",
        "language": language,
        "filename": filename,
        "code": code,  # Include the code for syntax highlighting
        "timeout": timeout
    }
    
    # Run the command with streaming to get syntax highlighting
    output = run_command(
        exec_cmd, 
        ctf=ctf, 
        timeout=timeout, 
        stream=True,  # ALWAYS use streaming
        tool_name="execute_code", 
        args=tool_args
    )

    return output
