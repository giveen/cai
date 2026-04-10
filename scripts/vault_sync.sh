#!/usr/bin/env bash
# vault_sync.sh — incremental Cyber-Vault knowledge-base sync.
#
# Activates the project venv (if present), then runs ingest_vault.py with
# --update so only markdown files modified since the last index run are
# re-embedded.  Designed to be called by the APScheduler cron job in
# src/cai/util/maintenance.py, but can also be run manually:
#
#   bash scripts/vault_sync.sh
#
# Environment variables honoured:
#   CAI_VAULT_FORCE=1   Run a full re-index (passes --force instead of --update)
#   CAI_PYTHON          Override the python binary used to run the script
#
# Exit codes:
#   0   Success (new_chunks line printed to stdout for parsing by caller).
#   1   Ingest script not found.
#   2   Python binary not found.
#   3   Ingest script returned a non-zero exit code.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 1. Locate python ─────────────────────────────────────────────────────────
VENV_PYTHON="${REPO_ROOT}/cai_venv/bin/python"
if [[ -n "${CAI_PYTHON:-}" ]]; then
    PYTHON="${CAI_PYTHON}"
elif [[ -x "${VENV_PYTHON}" ]]; then
    PYTHON="${VENV_PYTHON}"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "[vault_sync] ERROR: no python binary found." >&2
    exit 2
fi

# ── 2. Locate the ingest script ───────────────────────────────────────────────
INGEST="${SCRIPT_DIR}/ingest_vault.py"
if [[ ! -f "${INGEST}" ]]; then
    echo "[vault_sync] ERROR: ingest_vault.py not found at ${INGEST}" >&2
    exit 1
fi

# ── 3. Build the argument list ────────────────────────────────────────────────
if [[ "${CAI_VAULT_FORCE:-0}" == "1" ]]; then
    MODE_FLAG="--force"
    echo "[vault_sync] Running FULL re-index (CAI_VAULT_FORCE=1) …"
else
    MODE_FLAG="--update"
    echo "[vault_sync] Running incremental sync …"
fi

# ── 4. Execute ────────────────────────────────────────────────────────────────
cd "${REPO_ROOT}"
"${PYTHON}" "${INGEST}" ${MODE_FLAG} || {
    echo "[vault_sync] ERROR: ingest_vault.py exited with code $?" >&2
    exit 3
}

echo "[vault_sync] Done."
