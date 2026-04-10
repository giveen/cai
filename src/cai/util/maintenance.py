"""Knowledge-base maintenance scheduler.

Provides :func:`sync_knowledge_base` — a standalone function that executes
``scripts/vault_sync.sh`` to incrementally update the local Cyber-Vault
ChromaDB index — and :func:`start_scheduler` that configures an APScheduler
``BackgroundScheduler`` to run the sync job every Monday at 03:00 local time.

Usage
-----
Call :func:`start_scheduler` once at application startup (e.g. from the TUI
or CLI entry-point).  The scheduler runs purely in a background daemon thread
and does not block the main event loop.

Manual sync
-----------
    from cai.util.maintenance import sync_knowledge_base
    new_chunks = sync_knowledge_base()

Scheduler
---------
    from cai.util.maintenance import start_scheduler, stop_scheduler
    start_scheduler()   # call once; no-op if already running
    ...
    stop_scheduler()    # graceful shutdown at exit
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from cai.util import write_progress

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SYNC_SCRIPT = _REPO_ROOT / "scripts" / "vault_sync.sh"

# APScheduler BackgroundScheduler singleton — lazy-created by start_scheduler()
_scheduler: object | None = None

# ── Public API ────────────────────────────────────────────────────────────────


def sync_knowledge_base(force: bool = False) -> int:
    """Run ``scripts/vault_sync.sh`` and return the number of new chunks added.

    The function is synchronous and blocking.  It is safe to call from a
    background thread (which APScheduler does when the cron fires).

    Parameters
    ----------
    force:
        When ``True`` the environment variable ``CAI_VAULT_FORCE=1`` is set
        before invoking the script, triggering a full re-index.

    Returns
    -------
    int
        The number of new/updated chunks indexed during this run.
        Returns ``-1`` if the script could not be executed.
    """
    write_progress("Knowledge base sync starting…", "cyan")

    if not _SYNC_SCRIPT.exists():
        msg = (
            f"[MAINTENANCE] vault_sync.sh not found at {_SYNC_SCRIPT}. "
            "Cannot sync knowledge base."
        )
        logger.error(msg)
        write_progress(msg, "red")
        return -1

    env = os.environ.copy()
    if force:
        env["CAI_VAULT_FORCE"] = "1"

    try:
        result = subprocess.run(
            ["bash", str(_SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(_REPO_ROOT),
            timeout=1800,  # 30 min hard limit for clone + index
        )
    except subprocess.TimeoutExpired:
        msg = "[MAINTENANCE] vault_sync.sh timed out after 30 minutes"
        logger.error(msg)
        write_progress(msg, "red")
        return -1
    except Exception as exc:
        msg = f"[MAINTENANCE] Failed to launch vault_sync.sh: {exc}"
        logger.exception(msg)
        write_progress(msg, "red")
        return -1

    if result.returncode != 0:
        msg = (
            f"[MAINTENANCE] vault_sync.sh exited with code {result.returncode}. "
            f"stderr: {result.stderr.strip()[:200]}"
        )
        logger.error(msg)
        write_progress(msg, "red")
        return -1

    new_chunks = _parse_new_chunks(result.stdout)
    _emit_tui_notification(new_chunks, result.stdout)
    return new_chunks


def start_scheduler() -> None:
    """Start the APScheduler background scheduler (Monday 03:00 cron job).

    Safe to call multiple times — subsequent calls are no-ops if the scheduler
    is already running.
    """
    global _scheduler
    if _scheduler is not None:
        return  # already running

    try:
        from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore
    except ModuleNotFoundError:
        logger.warning(
            "[MAINTENANCE] APScheduler not installed. "
            "Run `pip install apscheduler` to enable scheduled syncs."
        )
        return

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=sync_knowledge_base,
        trigger="cron",
        day_of_week="mon",
        hour=3,
        minute=0,
        id="vault_weekly_sync",
        name="Weekly Cyber-Vault knowledge-base sync",
        replace_existing=True,
        misfire_grace_time=3600,  # tolerate up to 1-hour delay (e.g. system sleep)
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "[MAINTENANCE] Scheduler started. Cyber-Vault will sync every Monday at 03:00."
    )
    write_progress(
        "Scheduler started — Cyber-Vault syncs every Monday at 03:00.", "dim"
    )


def stop_scheduler() -> None:
    """Gracefully shut down the APScheduler instance, if running."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)  # type: ignore[union-attr]
    except Exception:
        pass
    _scheduler = None


# ── Internal helpers ──────────────────────────────────────────────────────────


def _parse_new_chunks(stdout: str) -> int:
    """Extract the new-chunk count from the ingest script's stdout.

    The script prints a line like:
        Indexed 412 chunks ... New/updated chunks this run: 37.
    We capture the trailing integer.
    """
    match = re.search(r"New/updated chunks this run:\s*(\d+)", stdout)
    if match:
        return int(match.group(1))
    # Fallback: look for a bare "✓ Cyber-Vault ready. Collection size: N chunks."
    match = re.search(r"Collection size:\s*(\d+)", stdout)
    if match:
        return int(match.group(1))
    return 0


def _emit_tui_notification(new_chunks: int, raw_stdout: str) -> None:
    """Write the post-sync TUI notification with the new-chunk count."""
    # Determine which source the new chunks came from (best-effort)
    source = "HackTricks"
    if "PayloadsAllTheThings" in raw_stdout and "hacktricks" not in raw_stdout.lower():
        source = "PayloadsAllTheThings"
    elif new_chunks == 0:
        write_progress(
            "Knowledge base sync complete: no new payloads since last run.", "dim"
        )
        return

    write_progress(
        f"Knowledge base updated: {new_chunks} new payload"
        f"{'s' if new_chunks != 1 else ''} added from {source}.",
        "green",
    )
