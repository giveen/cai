"""Hardened asynchronous shell proxy for Cerebro-AI."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Dict, Iterable, List, Optional, Sequence

from cai.memory.logic import clean_data
from cai.repl.commands.shell import SecureSubprocess
from cai.repl.ui.logging import get_cerebro_logger
from cai.sdk.agents import function_tool
from cai.tools.misc.cli_utils import CLI_UTILS
from cai.tools.validation import sanitize_tool_output, validate_command_guardrails
from cai.tools.workspace import get_project_space


_MAX_TIMEOUT_SECONDS = 30
_MAX_OUTPUT_CHARS = 50_000
_MAX_LINE_CHARS = 4_000
_WRITE_COMMANDS = {
    "touch",
    "tee",
    "cp",
    "mv",
    "rm",
    "mkdir",
    "rmdir",
    "truncate",
    "dd",
    "install",
    "ln",
    "chmod",
    "chown",
    "chgrp",
    "sed",
    "awk",
    "perl",
}
_RESTRICTED_PATHS = {
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/master.passwd",
    "/windows/system32/config/sam",
    "/windows/system32/config/security",
}
_PASSWD_LINE_RE = re.compile(r"^([^:]+):([^:]*):([^:]*):([^:]*):([^:]*):([^:]*):([^:]*)$", re.MULTILINE)
_ENV_SECRET_KEY_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASS|CREDENTIAL|AWS_|AZURE_|GCP_|GOOGLE_|OPENAI_|ANTHROPIC_)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SemanticError:
    code: str
    message: str
    retryable: bool
    category: str


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command: str
    argv: List[str]
    cwd: str
    started_at: str
    ended_at: str
    exit_code: Optional[int]
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    output_limit_chars: int
    error: Optional[Dict[str, Any]]


class PathGuard:
    """Command path policy for workspace-safe execution."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace = workspace_root.resolve()
        self._tmp_roots = self._build_tmp_roots()

    def validate_command(self, argv: Sequence[str]) -> None:
        if not argv:
            raise PermissionError("No command tokens supplied")

        write_intent = self._has_write_intent(argv)
        for token in argv:
            candidate = token.strip()
            if not candidate or candidate.startswith("-"):
                continue
            if "\x00" in candidate:
                raise PermissionError("Null byte detected in command token")

            path = self._as_path(candidate)
            if path is None:
                continue

            self._assert_not_restricted(path)
            if write_intent:
                self._assert_write_allowed(path)

    def _assert_not_restricted(self, candidate: Path) -> None:
        lowered = str(candidate).lower().replace("\\", "/")
        if lowered in _RESTRICTED_PATHS:
            raise PermissionError(f"Restricted path access denied: {candidate}")

    def _assert_write_allowed(self, candidate: Path) -> None:
        if self._is_within(self._workspace, candidate):
            return
        for tmp_root in self._tmp_roots:
            if self._is_within(tmp_root, candidate):
                return
        raise PermissionError(
            "Write denied by PathGuard: destination must stay inside workspace or approved tmp directories"
        )

    @staticmethod
    def _is_within(root: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _has_write_intent(argv: Sequence[str]) -> bool:
        exe = Path(argv[0]).name.lower()
        if exe in _WRITE_COMMANDS:
            return True
        lowered = " ".join(argv).lower()
        return any(flag in lowered for flag in ("--in-place", "--output", "--append", "of="))

    def _as_path(self, token: str) -> Optional[Path]:
        if token in {".", ".."}:
            return (self._workspace / token).resolve()
        if token.startswith("/") or token.startswith("~") or token.startswith("./") or token.startswith("../"):
            raw = Path(token).expanduser()
            return (self._workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
        if "/" in token or "\\" in token:
            raw = Path(token)
            return (self._workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
        return None

    @staticmethod
    def _build_tmp_roots() -> List[Path]:
        roots = {Path("/tmp").resolve(), Path("/var/tmp").resolve()}
        env_tmp = os.getenv("TMPDIR", "").strip()
        if env_tmp:
            try:
                roots.add(Path(env_tmp).expanduser().resolve())
            except Exception:
                pass
        return sorted(roots)


class CerebroLinuxCommandTool:
    """Async shell execution proxy with boundary and redaction controls."""

    def __init__(self) -> None:
        self._workspace = get_project_space().ensure_initialized().resolve()
        self._secure = SecureSubprocess(workspace_root=self._workspace)
        self._logger = get_cerebro_logger()
        self._audit_log = (self._workspace / ".cai" / "audit" / "linux_command_replay.jsonl").resolve()
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)
        self._guard = PathGuard(self._workspace)

    async def execute(self, *, command: str, timeout_seconds: int = _MAX_TIMEOUT_SECONDS) -> Dict[str, Any]:
        command = (command or "").strip()
        if not command:
            return self._error(
                SemanticError(
                    code="empty_command",
                    message="No command provided.",
                    retryable=False,
                    category="validation",
                )
            )

        guardrail_error = validate_command_guardrails(command)
        if guardrail_error:
            return self._error(
                SemanticError(
                    code="guardrail_blocked",
                    message=guardrail_error,
                    retryable=False,
                    category="policy",
                )
            )

        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            return self._error(
                SemanticError(
                    code="invalid_syntax",
                    message=f"Unable to parse command tokens: {exc}",
                    retryable=False,
                    category="validation",
                )
            )

        if not argv:
            return self._error(
                SemanticError(
                    code="empty_command",
                    message="No executable token found.",
                    retryable=False,
                    category="validation",
                )
            )

        if not self._resolve_executable(argv[0]):
            return self._error(
                SemanticError(
                    code="command_not_found",
                    message=f"Command not found: {argv[0]}",
                    retryable=False,
                    category="dependency",
                )
            )

        try:
            self._guard.validate_command(argv)
        except PermissionError as exc:
            message = str(exc)
            code = "restricted_path" if "Restricted path" in message else "boundary_violation"
            category = "policy" if code == "restricted_path" else "sandbox"
            return self._error(
                SemanticError(
                    code=code,
                    message=message,
                    retryable=False,
                    category=category,
                )
            )

        timeout_seconds = max(1, min(int(timeout_seconds), _MAX_TIMEOUT_SECONDS))
        started_at = datetime.now(tz=UTC)

        clean_env, redactions = self._secure.build_clean_environment()
        runtime_base = self._scrub_environment(clean_env)

        process: asyncio.subprocess.Process
        with CLI_UTILS.managed_env_context(base_env=runtime_base) as runtime_env:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._workspace),
                env=runtime_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_text, stderr_text, timed_out = await self._capture_with_watchdog(
                process=process,
                redactions=redactions,
                timeout_seconds=timeout_seconds,
            )

        ended_at = datetime.now(tz=UTC)
        exit_code = process.returncode

        semantic = self._translate_exit_error(exit_code=exit_code, stderr=stderr_text, timed_out=timed_out)
        payload = CommandResult(
            ok=semantic is None,
            command=command,
            argv=list(argv),
            cwd=str(self._workspace),
            started_at=started_at.isoformat(),
            ended_at=ended_at.isoformat(),
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=sanitize_tool_output(command, stdout_text),
            stderr=sanitize_tool_output(command, stderr_text),
            stdout_truncated=len(stdout_text) >= _MAX_OUTPUT_CHARS,
            stderr_truncated=len(stderr_text) >= _MAX_OUTPUT_CHARS,
            output_limit_chars=_MAX_OUTPUT_CHARS,
            error=asdict(semantic) if semantic else None,
        )

        await self._log_replay(payload)
        return clean_data(asdict(payload))

    async def _capture_with_watchdog(
        self,
        *,
        process: asyncio.subprocess.Process,
        redactions: Dict[str, str],
        timeout_seconds: int,
    ) -> tuple[str, str, bool]:
        if process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise RuntimeError("Process streams unavailable")

        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        async def _read_stream(stream: asyncio.StreamReader, bucket: List[str]) -> None:
            total = 0
            while True:
                chunk = await stream.readline()
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                text = self._secure.redact_text(text, redactions)
                text = self._redact_output(text)
                if len(text) > _MAX_LINE_CHARS:
                    text = text[:_MAX_LINE_CHARS] + "\n...[line truncated by policy]"
                remaining = _MAX_OUTPUT_CHARS - total
                if remaining <= 0:
                    continue
                if len(text) > remaining:
                    bucket.append(text[:remaining] + "\n...[output truncated by policy]")
                    total = _MAX_OUTPUT_CHARS
                else:
                    bucket.append(text)
                    total += len(text)

        out_task = asyncio.create_task(_read_stream(process.stdout, stdout_chunks))
        err_task = asyncio.create_task(_read_stream(process.stderr, stderr_chunks))

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=float(timeout_seconds))
        except asyncio.TimeoutError:
            timed_out = True
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        finally:
            await asyncio.gather(out_task, err_task, return_exceptions=True)

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if timed_out:
            stderr = (stderr + "\nExecution timed out after policy limit.").strip()
        return stdout, stderr, timed_out

    def _scrub_environment(self, base_env: Dict[str, str]) -> Dict[str, str]:
        clean: Dict[str, str] = {}
        for key, value in base_env.items():
            if not value:
                continue
            if key in {"HISTFILE", "HISTSIZE", "HISTCONTROL", "PYTHONPATH"}:
                continue
            if _ENV_SECRET_KEY_RE.search(key):
                continue
            if key in {"HOME", "USER", "LOGNAME"}:
                continue
            clean[key] = value

        clean["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        clean["WORKSPACE_ROOT"] = str(self._workspace)
        return clean

    @staticmethod
    def _resolve_executable(executable: str) -> bool:
        candidate = Path(executable)
        if candidate.is_absolute() and candidate.exists() and candidate.is_file():
            return True
        from shutil import which

        return which(executable) is not None

    def _redact_output(self, text: str) -> str:
        redacted = text.replace(str(self._workspace), "[WORKSPACE_ROOT]")
        redacted = redacted.replace(str(Path.home()), "[HOME]")

        def _mask_passwd(match: re.Match[str]) -> str:
            user = match.group(1)
            uid = match.group(3)
            gid = match.group(4)
            shell = match.group(7)
            return f"{user}:x:{uid}:{gid}:[REDACTED_USERINFO]:[REDACTED_PATH]:{shell}"

        redacted = _PASSWD_LINE_RE.sub(_mask_passwd, redacted)
        redacted = re.sub(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", "[REDACTED_EMAIL]", redacted)
        redacted = re.sub(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)", "[REDACTED_PHONE]", redacted)
        return redacted

    def _translate_exit_error(self, *, exit_code: Optional[int], stderr: str, timed_out: bool) -> Optional[SemanticError]:
        if timed_out:
            return SemanticError(
                code="timeout",
                message="Command exceeded execution timeout and was terminated.",
                retryable=True,
                category="watchdog",
            )
        if exit_code in (None, 0):
            return None
        if exit_code == 127:
            return SemanticError(
                code="command_not_found",
                message="The command executable was not found on PATH.",
                retryable=False,
                category="dependency",
            )
        if exit_code == 13 or "permission denied" in stderr.lower():
            return SemanticError(
                code="permission_denied",
                message="Permission denied while executing command or reading target resource.",
                retryable=False,
                category="authorization",
            )
        return SemanticError(
            code="command_failed",
            message=f"Command exited with status {exit_code}.",
            retryable=False,
            category="execution",
        )

    async def _log_replay(self, result: CommandResult) -> None:
        row = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "command": result.command,
            "argv": result.argv,
            "user_context": {
                "agent_id": self._resolve_agent_id(),
                "cwd": result.cwd,
            },
            "exit_status": result.exit_code,
            "timed_out": result.timed_out,
        }

        line = json.dumps(clean_data(row), ensure_ascii=True) + "\n"
        await asyncio.to_thread(self._append_line, self._audit_log, line)
        if self._logger is not None:
            try:
                self._logger.audit(
                    "linux command replay logged",
                    actor="generic_linux_command",
                    data=clean_data(row),
                    tags=["linux_command", "replay"],
                )
            except Exception:
                pass

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    @staticmethod
    def _resolve_agent_id() -> str:
        for key in ("CAI_AGENT_ID", "AGENT_ID", "CAI_AGENT", "CAI_AGENT_TYPE"):
            value = os.getenv(key, "").strip()
            if value:
                return value
        return "unknown-agent"

    @staticmethod
    def _error(error: SemanticError) -> Dict[str, Any]:
        return clean_data(
            {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "retryable": error.retryable,
                    "category": error.category,
                },
            }
        )


LINUX_COMMAND_TOOL = CerebroLinuxCommandTool()


@function_tool(strict_mode=False)
async def generic_linux_command(command: str = "", interactive: bool = False, session_id: Optional[str] = None) -> str:
    _ = (interactive, session_id)
    result = await LINUX_COMMAND_TOOL.execute(command=command)
    if not result.get("ok"):
        error = result.get("error") or {}
        return str(error.get("message", "Command execution failed"))

    stdout = str(result.get("stdout", "")).strip()
    stderr = str(result.get("stderr", "")).strip()
    if stdout and stderr:
        return f"{stdout}\n{stderr}".strip()
    return (stdout or stderr or "").strip()


@function_tool
def null_tool() -> str:
    return "Null tool"


__all__ = ["SemanticError", "PathGuard", "CerebroLinuxCommandTool", "LINUX_COMMAND_TOOL", "generic_linux_command", "null_tool"]
