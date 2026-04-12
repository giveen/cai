"""Tool for executing code via LLM tool calls.

This tool ensures Python code runs with the currently active Python
interpreter (``sys.executable``) so executions occur inside the active
virtualenv. It also implements a simple error-backoff: repeated identical
errors (e.g., HTTP 500) increment a counter per-session and on the third
consecutive occurrence we append a System Advice warning to the agent and
record the failure into the intelligence journal to avoid immediate
retries.
"""

import base64
import os
import shlex
import sys
import re
import uuid
from datetime import datetime
from typing import Any

from cai.sdk.agents import function_tool
from cai.sdk.agents.run_context import RunContextWrapper
from cai.tools.common import run_command  # pylint: disable=import-error
from cai.tools.validation import is_valid_filename  # pylint: disable=import-error

# Local in-process backoff tracker: session_id -> {last_error_sig, count}
_BACKOFF_STATE: dict[str, dict[str, Any]] = {}

# Persistence helpers (journal) — used to record failed paths
try:
    from cai.orchestration import persistence
except Exception:
    persistence = None


@function_tool
def execute_code(
    ctx: RunContextWrapper[Any],
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
    if not is_valid_filename(filename):
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
    create_cmd = (
        f"mkdir -p {shlex.quote(tmp_dir)} && echo {quoted_encoded} | base64 -d > {quoted_target}"
    )
    res = run_command(create_cmd, stream=False, tool_name="_internal_file_creation")
    if isinstance(res, str) and "error" in res.lower():
        return f"Failed to create code file: {res}"

    # Build execution commands depending on language
    # We always execute from inside tmp_dir so compiled artifacts do not escape
    if language in ["python", "py"]:
        # Use the currently active Python interpreter (sys.executable)
        py_exec = shlex.quote(sys.executable or "python3")
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && {py_exec} {shlex.quote(full_filename)}"
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
        run_command(
            f"cp {quoted_target} {shlex.quote(os.path.join(tmp_dir, 'main.go'))}",
            stream=False,
            tool_name="_internal_setup",
        )
        run_command(
            f"cd {shlex.quote(tmp_dir)} && go mod init temp || true",
            stream=False,
            tool_name="_internal_setup",
        )
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && go run main.go"
    elif language in ["javascript", "js"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && node {shlex.quote(full_filename)}"
    elif language in ["typescript", "ts"]:
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ts-node {shlex.quote(full_filename)}"
    elif language in ["rust", "rs"]:
        run_command(
            f"cd {shlex.quote(tmp_dir)} && rustc {shlex.quote(full_filename)} -o {shlex.quote(filename)}",
            stream=False,
            tool_name="_internal_setup",
        )
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ./{shlex.quote(filename)}"
    elif language in ["csharp", "cs"]:
        run_command(
            f"cd {shlex.quote(tmp_dir)} && dotnet build {shlex.quote(full_filename)}",
            stream=False,
            tool_name="_internal_setup",
        )
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && dotnet run {shlex.quote(full_filename)}"
    elif language in ["java"]:
        run_command(
            f"cd {shlex.quote(tmp_dir)} && javac {shlex.quote(full_filename)}",
            stream=False,
            tool_name="_internal_setup",
        )
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && java {shlex.quote(filename)}"
    elif language in ["kotlin", "kt"]:
        run_command(
            f"cd {shlex.quote(tmp_dir)} && kotlinc {shlex.quote(full_filename)} -include-runtime -d {shlex.quote(filename)}.jar",
            stream=False,
            tool_name="_internal_setup",
        )
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && java -jar {shlex.quote(filename)}.jar"
    elif language in ["c"]:
        run_command(
            f"cd {shlex.quote(tmp_dir)} && gcc {shlex.quote(full_filename)} -o {shlex.quote(filename)}",
            stream=False,
            tool_name="_internal_setup",
        )
        exec_cmd = f"cd {shlex.quote(tmp_dir)} && ./{shlex.quote(filename)}"
    elif language in ["cpp"]:
        run_command(
            f"cd {shlex.quote(tmp_dir)} && g++ {shlex.quote(full_filename)} -o {shlex.quote(filename)}",
            stream=False,
            tool_name="_internal_setup",
        )
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

    # Session id used for journaling/backoff scoping
    session_id = os.getenv("CAI_SESSION_ID") or os.getenv("SESSION_ID") or "default"

    # Prevent immediate retries: if the code itself targets a URL that is
    # already recorded as a failed path in the session's journal, skip execution.
    try:
        if persistence is not None:
            url_in_code = re.search(r"https?://([^/\s:]+)(?::\d+)?(/[^\s\'\"]*)", code or "")
            if url_in_code:
                tgt_host = url_in_code.group(1)
                tgt_path = url_in_code.group(2) or "/"
                journal = persistence._read_journal()
                for e in journal.get("entries", [])[::-1]:
                    if e.get("session_id") != session_id:
                        continue
                    fact = e.get("fact", {}) or {}
                    if isinstance(fact, dict) and fact.get("host") == tgt_host and fact.get("failed_path") == tgt_path:
                        return (
                            f"Skipping execution: Previously recorded failure for {tgt_host}{tgt_path} in this session."
                        )
    except Exception:
        # Journaling lookups are best-effort; on error fall through and execute
        pass

    try:
        output = run_command(
            exec_cmd, timeout=timeout, stream=True, tool_name="execute_code", args=tool_args
        )
        # After execution, inspect output for repeated errors (simple heuristic)
        try:
            out_text = output if isinstance(output, str) else str(output)
            session_id = os.getenv("CAI_SESSION_ID") or os.getenv("SESSION_ID") or "default"

            # Detect an HTTP 500 / Internal Server Error pattern
            is_500 = bool(re.search(r"\b500\b", out_text)) or ("internal server error" in out_text.lower())
            if is_500:
                # Create a concise error signature (first 120 chars around the first 500 mention)
                m = re.search(r"(.{0,60}500.{0,60})", out_text)
                sig = m.group(0) if m else "HTTP 500"

                prev = _BACKOFF_STATE.get(session_id)
                if prev and prev.get("last_error_sig") == sig:
                    prev["count"] = prev.get("count", 1) + 1
                else:
                    _BACKOFF_STATE[session_id] = {"last_error_sig": sig, "count": 1}

                count = _BACKOFF_STATE[session_id]["count"]

                # On three consecutive identical failures, append System Advice and journal the failure
                if count >= 3:
                    # Try to extract host and path from any URL in the output
                    url_match = re.search(r"https?://([^/\s:]+)(?::\d+)?(/[^\s\n\r]*)?", out_text)
                    host = url_match.group(1) if url_match else "<unknown>"
                    path = url_match.group(2) if url_match and url_match.group(2) else "/"
                    system_advice = (
                        f"System Advice: TARGET ERROR: Host {host} is repeatedly returning 500 Internal Server Error. "
                        "Immediate strategy shift or target reset required."
                    )
                    # Append advice to returned output so the agent receives the warning
                    output = (out_text + "\n\n" + system_advice)

                    # Record a compact failure fact in the intelligence journal
                    try:
                        if persistence is not None:
                            journal = persistence._read_journal()
                            entry_id = uuid.uuid4().hex
                            entry = {
                                "id": entry_id,
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                                "category": "failure",
                                "source": "execute_code",
                                "session_id": session_id,
                                "fact": {
                                    "failed_path": path,
                                    "host": host,
                                    "error": "HTTP 500",
                                },
                            }
                            journal.setdefault("entries", []).append(entry)
                            journal.setdefault("meta", {})["updated_at"] = datetime.utcnow().isoformat() + "Z"
                            persistence._write_journal_atomic(journal)
                            try:
                                persistence._render_readme(journal)
                            except Exception:
                                pass
                    except Exception:
                        # Never raise from journaling — journaling is best-effort
                        pass

        except Exception:
            # Parsing/backoff should not affect execution return path
            pass
    finally:
        # Clean up temporary directory unless persistence requested
        if not persist:
            try:
                run_command(
                    f"rm -rf {shlex.quote(tmp_dir)}", stream=False, tool_name="_internal_cleanup"
                )
            except Exception:
                pass

    return output
