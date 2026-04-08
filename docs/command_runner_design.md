# CommandRunner Design

**Overview**

This document describes a proposed `CommandRunner` abstraction to decouple command execution from higher-level tools (session management, guardrails, templating, and UI). The goal is to make execution pluggable, sandboxable, and testable, and to enable safer defaults (resource limits, containerized sandboxes) without changing higher-level tool semantics.

**Goals**

- Provide a clear interface for running shell commands synchronously and asynchronously.
- Make execution implementations pluggable (local, container, SSH, test stub).
- Centralize resource limits, timeouts, and sandboxing options in runners.
- Preserve backward compatibility by defaulting to the current local behavior.
- Simplify unit testing by allowing injection of a TestRunner.

**Scope & Non-Goals**

- In-scope: interface design, example implementations (LocalRunner, TestRunner), adapter plan for migrating `run_command` and wrappers. 
- Out-of-scope: complete container orchestration implementation (we will provide a reference ContainerRunner implementation using Docker/Podman but not a production orchestration system).

**High-level Architecture**

Call flow (recommended):

- Tool (e.g., `generic_linux_command`, `curl`) → validate guardrails (`validation.validate_command_guardrails`) → `RunnerManager.get_runner()` → `runner.run_sync()` / `runner.run_async()` → result

The `RunnerManager` is a small factory that selects the appropriate runner implementation by configuration (environment, per-call flags, or explicit injection).

**Interface (Python pseudocode)**

```py
from typing import Optional, AsyncIterator, Dict, Any

class CommandExecutionError(RuntimeError):
    pass

class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    metadata: Dict[str, Any]


class CommandRunner:
    """Abstract interface for command execution."""

    def run_sync(self, command: str, *, timeout: int = 60, cwd: Optional[str] = None,
                 env: Optional[Dict[str, str]] = None, stream: bool = False,
                 stdin: Optional[bytes] = None, tool_name: Optional[str] = None,
                 call_id: Optional[str] = None, session_id: Optional[str] = None) -> CommandResult:
        raise NotImplementedError()

    async def run_async(self, command: str, *, timeout: int = 60, cwd: Optional[str] = None,
                        env: Optional[Dict[str, str]] = None, stream: bool = False,
                        stdin: Optional[bytes] = None, tool_name: Optional[str] = None,
                        call_id: Optional[str] = None, session_id: Optional[str] = None) -> CommandResult:
        raise NotImplementedError()

    def start_session(self, command: str, **kwargs) -> str:
        """Start a persistent interactive session and return a session_id."""

    def send_to_session(self, session_id: str, command: str, **kwargs) -> CommandResult:
        """Send input to an existing session and return output."""

    def terminate_session(self, session_id: str) -> bool:
        raise NotImplementedError()
```

Notes:
- `run_sync` should support `stream=True` semantics: for simplicity it can still return an aggregated `CommandResult` while exposing a callback/iterator API for streaming cases.
- `CommandResult.metadata` should include `call_id`, `tool_name`, runtime, and any sandbox/container identifiers.

**Recommended Runner Implementations**

- `LocalCommandRunner` — runs commands on the local host using `subprocess`. Implements rlimits via `resource.setrlimit` in `preexec_fn`, uses a separate process group, and respects `timeout`. Streams via pseudo-tty when required.

- `ContainerCommandRunner` — runs commands inside a container (Docker/Podman). Enforces cgroup resource limits, mounts workspace readonly by default, and isolates network by default. Use for high-risk operations.

- `SSHCommandRunner` — forwards commands via SSH (lib/ssh or system `ssh`), returning aggregated output. Useful for remote-agent scenarios.

- `TestCommandRunner` — deterministic runner used for unit tests; returns pre-canned outputs.

**Security & Sandbox Controls**

- Always run `validation.validate_command_guardrails(command)` before invoking any runner.
- For risky commands use `ContainerCommandRunner` by default (configurable per-run via `runner_hint='container'`).
- Enforce RLIMIT_CPU, RLIMIT_AS, RLIMIT_FSIZE, RLIMIT_NOFILE in `LocalCommandRunner`.
- Drop privileges (run as low-privileged user) when possible inside the runner.
- Where available, use seccomp/bpf or Linux namespaces to further restrict syscalls.

**Streaming & Observability**

- Streaming API: `run_sync(..., stream=True)` can return a `CommandResult` and expose a `StreamIterator` or accept a callback for chunk updates. The runner should emit structured chunks: {"type":"stdout|stderr|meta","data":...}
- Emit structured logs and metrics: `cai.runner.commands.total`, `cai.runner.commands.blocked`, `cai.runner.commands.duration_seconds`, `cai.runner.commands.exit_code`.

**Error Handling**

- Runner implementations should raise `CommandExecutionError` on infrastructure problems (container runtime errors, timeouts) and surface command exit codes via `CommandResult.exit_code`.
- Guardrails return human-readable error strings early (no runner invocation).

**Migration Plan (incremental, low-risk)**

1. Add `src/cai/tools/runner.py` with `CommandRunner` interface + `RunnerManager`.
2. Add `LocalCommandRunner` that wraps the existing `run_command`/`run_command_async` implementation (adapter). Add unit tests for adapter equivalence.
3. Introduce `RunnerManager.get_default_runner()` and make `cai.tools.common.run_command` delegate to `RunnerManager` (no behavior change by default).
4. Add `TestCommandRunner` and update unit tests to inject it where appropriate.
5. Implement `ContainerCommandRunner` and add opt-in flag/ENV (e.g., `CAI_RUNNER=container`) to select it in integration tests.
6. Gradually enable container runner for high-risk tools (e.g., `execute_code`, `netcat`) behind feature flags.

This keeps default behavior identical while enabling controlled rollout.

**Backwards Compatibility**

- `RunnerManager` defaults to `LocalCommandRunner` that behaves like current `run_command` to avoid breaking existing callers.
- Provide a compatibility adapter so existing code using `run_command(command, ...)` continues to work.

**Testing Strategy**

- Unit tests: mock `CommandRunner` implementations and validate callsite behavior (tools call validate guardrails and call runner with expected args).
- Integration tests: for `LocalCommandRunner` run simple commands (`echo`, `ls`) and assert output; for `ContainerCommandRunner` run the same inside a container in CI where Docker is available.
- Security tests: property-based tests and fuzzing for `validation.validate_command_guardrails` and decoded content checks.

**Files to Add (suggested)**

- `src/cai/tools/runner.py` — interface + RunnerManager
- `src/cai/tools/runner/local.py` — LocalCommandRunner implementation
- `src/cai/tools/runner/container.py` — ContainerCommandRunner reference implementation
- `tests/test_runner_local.py` — unit/integration tests for local runner

**Open Questions / Decisions**

- Which container engine(s) to officially support in CI (Docker, Podman)?
- Do we require an allowlist for certain high-risk tool names (e.g., `nc`, `ssh`, `curl`) or is guardrail sufficient?
- How aggressive should network isolation be for container runner by default?

**Timeline & PR Breakdown (suggested)**

- PR#1 (small): Add `runner.py` interface + `local.py` adapter + tests. Swap `cai.tools.common.run_command` to delegate to `RunnerManager`.
- PR#2 (medium): Add `TestCommandRunner`, update tests to use injection, add `RunnerManager` config via ENV.
- PR#3 (medium): Implement `ContainerCommandRunner` and CI job; add feature flag to opt-in for selected tools.
- PR#4 (small): Harden runners (rlimits, seccomp, detailed metrics) and add rollout plan.

---

If you want, I can create the initial `src/cai/tools/runner.py` and `src/cai/tools/runner/local.py` adapter now (PR#1). Do you want me to implement the interface and adapter next, or revise the doc further?
