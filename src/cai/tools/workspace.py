"""Workspace and sandbox management for tool execution.

This module provides an original, class-driven implementation for creating
and managing per-session workspaces used by security assessments.

Design goals:
- Keep all tool artifacts inside a controlled directory tree.
- Provide safe path resolution that blocks directory traversal.
- Offer lifecycle helpers for setup, cleanup, and archival.
- Preserve compatibility with legacy helpers used across the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
import os
import re
import secrets
import shutil
from typing import Iterable, Mapping


_WORKSPACE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_TEMP_PATTERNS = ("*.tmp", "*.temp", "*.log", "*.cache")


def _warn(message: str) -> None:
    """Emit a user-facing warning without adding non-stdlib dependencies."""
    print(f"[workspace] {message}")


def _is_valid_workspace_name(candidate: str | None) -> bool:
    """Return True when a workspace/run name is safe for filesystem use."""
    if not candidate:
        return False
    return bool(_WORKSPACE_NAME_RE.match(candidate))


def _make_run_token() -> str:
    """Generate a short unique token for session directory names."""
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    random_part = secrets.token_hex(3)
    return f"run-{timestamp}-{random_part}"


@dataclass(slots=True)
class ProjectSpace:
    """Manage one isolated workspace for a single assessment session.

    A ProjectSpace owns one root directory and ensures all resolved paths
    remain below that root. This behavior is suitable for sandboxing tool
    outputs and temporary artifacts.
    """

    root_base: Path
    session_id: str
    session_root: Path = field(init=False)
    _initialized: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.root_base = self.root_base.expanduser()
        self.session_root = (self.root_base / self.session_id).resolve()

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> ProjectSpace:
        """Create a ProjectSpace configured from CAI environment variables.

        Environment behavior:
        - CAI_WORKSPACE_DIR controls the base directory.
        - CAI_WORKSPACE controls the run/session directory name.
        - If CAI_WORKSPACE is missing, a unique run id is generated.
        """
        source = env if env is not None else os.environ
        explicit_base = source.get("CAI_WORKSPACE_DIR")
        explicit_name = source.get("CAI_WORKSPACE")

        if explicit_base:
            base_dir = Path(explicit_base).expanduser().resolve()
        else:
            # Store named/auto sessions in ./workspaces by default.
            base_dir = (Path.cwd() / "workspaces").resolve()

        if _is_valid_workspace_name(explicit_name):
            run_name = str(explicit_name)
        elif explicit_name:
            _warn(
                f"Invalid CAI_WORKSPACE '{explicit_name}'. Using auto-generated run directory instead."
            )
            run_name = _make_run_token()
        else:
            run_name = _make_run_token()

        return cls(root_base=base_dir, session_id=run_name)

    def initialize(self) -> Path:
        """Create and return the session root directory.

        Raises RuntimeError when directory creation is blocked by OS-level
        restrictions (permission denied, read-only fs, etc.).
        """
        try:
            self.session_root.mkdir(parents=True, exist_ok=True)
            self._initialized = True
            return self.session_root
        except OSError as exc:
            raise RuntimeError(
                f"Unable to initialize workspace at '{self.session_root}': {exc}"
            ) from exc

    def ensure_initialized(self) -> Path:
        """Initialize lazily and return the session root."""
        if not self._initialized:
            return self.initialize()
        return self.session_root

    def _enforce_sandbox(self, candidate: Path) -> Path:
        """Verify candidate remains inside this session root."""
        root = self.ensure_initialized().resolve()
        resolved = candidate.resolve()

        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Path '{resolved}' escapes workspace sandbox '{root}'"
            ) from exc

        return resolved

    def get_path(self, *segments: str | os.PathLike[str], create_parent: bool = False) -> Path:
        """Resolve a path inside the session workspace.

        Args:
            *segments: Relative path elements inside the workspace.
            create_parent: If True, ensure parent folder exists.
        """
        target = self.ensure_initialized().joinpath(*[Path(s) for s in segments])
        safe_target = self._enforce_sandbox(target)

        if create_parent:
            try:
                safe_target.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(
                    f"Unable to create parent directory for '{safe_target}': {exc}"
                ) from exc

        return safe_target

    def cleanup(self, patterns: Iterable[str] | None = None, remove_empty_dirs: bool = True) -> int:
        """Delete temporary artifacts from this workspace.

        Only files under the workspace root are considered.

        Returns:
            Count of files removed.
        """
        roots = self.ensure_initialized()
        removed = 0
        matchers = tuple(patterns or _DEFAULT_TEMP_PATTERNS)

        for pattern in matchers:
            for hit in roots.rglob(pattern):
                if hit.is_file():
                    safe_hit = self._enforce_sandbox(hit)
                    try:
                        safe_hit.unlink(missing_ok=True)
                        removed += 1
                    except OSError as exc:
                        raise RuntimeError(
                            f"Failed to remove temporary file '{safe_hit}': {exc}"
                        ) from exc

        if remove_empty_dirs:
            self._prune_empty_dirs()

        return removed

    def _prune_empty_dirs(self) -> None:
        """Remove empty subdirectories, preserving the workspace root."""
        root = self.ensure_initialized()
        # Sort deepest-first so child dirs are removed before parents.
        all_dirs = sorted([d for d in root.rglob("*") if d.is_dir()], key=lambda p: len(p.parts), reverse=True)
        for directory in all_dirs:
            safe_dir = self._enforce_sandbox(directory)
            try:
                safe_dir.rmdir()
            except OSError:
                # Directory is not empty or not removable; safe to ignore.
                continue

    def archive(self, destination: str | os.PathLike[str] | None = None) -> Path:
        """Create a ZIP archive of the full session workspace.

        Args:
            destination: Optional output path. If omitted, archive is placed in
                the workspace base directory as '<session_id>.zip'.

        Returns:
            Absolute path to the archive.
        """
        root = self.ensure_initialized()

        if destination is None:
            archive_target = (self.root_base / f"{self.session_id}.zip").resolve()
        else:
            archive_target = Path(destination).expanduser().resolve()
            if archive_target.suffix.lower() != ".zip":
                archive_target = archive_target.with_suffix(".zip")

        try:
            archive_target.parent.mkdir(parents=True, exist_ok=True)
            generated_path = shutil.make_archive(
                base_name=str(archive_target.with_suffix("")),
                format="zip",
                root_dir=str(root),
                base_dir=".",
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to archive workspace '{root}': {exc}") from exc

        return Path(generated_path).resolve()

    def freeze(self, destination: str | os.PathLike[str] | None = None) -> Path:
        """Alias for archive(), keeping API language aligned with reporting workflows."""
        return self.archive(destination=destination)


# Module-level cache keeps one workspace stable during process lifetime.
_ACTIVE_SPACE: ProjectSpace | None = None


def _current_space() -> ProjectSpace:
    """Return the active ProjectSpace, creating it lazily from environment."""
    global _ACTIVE_SPACE
    if _ACTIVE_SPACE is None:
        _ACTIVE_SPACE = ProjectSpace.from_environment()
        _ACTIVE_SPACE.initialize()
    return _ACTIVE_SPACE


def _get_workspace_dir() -> str:
    """Compatibility helper: return host workspace directory as a string.

    Behavior notes:
    - If CAI_WORKSPACE and/or CAI_WORKSPACE_DIR are provided, this returns a
      managed directory under those settings.
    - If neither variable is set, it falls back to the current directory to
      remain compatible with legacy execution flows.
    """
    workspace_name = os.getenv("CAI_WORKSPACE")
    workspace_base = os.getenv("CAI_WORKSPACE_DIR")

    if not workspace_name and not workspace_base:
        return str(Path.cwd())

    try:
        return str(_current_space().session_root)
    except RuntimeError as exc:
        _warn(f"{exc}. Falling back to current directory.")
        return str(Path.cwd())


def _get_container_workspace_path() -> str:
    """Compatibility helper: return the expected in-container workspace path."""
    workspace_name = os.getenv("CAI_WORKSPACE")

    if workspace_name and _is_valid_workspace_name(workspace_name):
        return f"/workspace/workspaces/{workspace_name}"

    if workspace_name:
        _warn(f"Invalid CAI_WORKSPACE '{workspace_name}' for container path. Using '/'.")

    return "/"


def get_project_space() -> ProjectSpace:
    """Public accessor for integration with agents and tool runners."""
    return _current_space()


def resolve_workspace_path(*segments: str | os.PathLike[str], create_parent: bool = False) -> str:
    """Resolve an absolute sandboxed path in the active workspace."""
    return str(_current_space().get_path(*segments, create_parent=create_parent))
