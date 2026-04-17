#!/bin/bash
# Data migration 001: Phase 0 podcast detection backfill (v8.0.3)
#
# Reclassifies known podcast publishers/authors from content_type='Product'
# to 'Podcast' using the heuristics added in commits ccb863e + c10b335.
#
# Required after: fresh installs, DB restores from pre-Phase-0 backups,
# or upgrades from any version < 8.0.3 to >= 8.0.3.
#
# See docs/CONTENT-CLASSIFICATION-DRIFT.md for the full incident writeup.
#
# Idempotent: the underlying Python function checks content_type='Product'
# before reclassifying — rows already fixed are skipped.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   VENV_PYTHON   — path to venv python interpreter
#   APP_DIR       — path to installed application (/opt/audiobooks)
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"
#   INTERACTIVE   — "true" if user can be prompted, "false" for auto-run

# shellcheck disable=SC2154  # variables set by caller

MIN_VERSION="8.0.3"

_dm001_run_backfill() {
    local dry_flag=""
    [[ "$DRY_RUN" == "true" ]] && dry_flag="--dry-run"

    local backfill_script="${APP_DIR}/library/scripts/backfill_enrichment.py"
    if [[ ! -f "$backfill_script" ]]; then
        echo "  Warning: backfill_enrichment.py not found at $backfill_script"
        return 1
    fi

    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo "  Warning: venv python not found at $VENV_PYTHON"
        return 1
    fi

    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks "$VENV_PYTHON" "$backfill_script" --db "$DB_PATH" --asin-only $dry_flag 2>&1
    else
        "$VENV_PYTHON" "$backfill_script" --db "$DB_PATH" --asin-only $dry_flag 2>&1
    fi
}

# Check preconditions
if [[ ! -f "$DB_PATH" ]]; then
    return 0
fi

# Check if there are any Product rows — if zero, skip without prompting.
_dm001_candidate_count=0
if [[ -n "$USE_SUDO" ]]; then
    _dm001_candidate_count=$(sudo -u audiobooks sqlite3 "$DB_PATH" \
        "SELECT COUNT(*) FROM audiobooks WHERE content_type = 'Product';" 2>/dev/null || echo "0")
else
    _dm001_candidate_count=$(sqlite3 "$DB_PATH" \
        "SELECT COUNT(*) FROM audiobooks WHERE content_type = 'Product';" 2>/dev/null || echo "0")
fi

if [[ "$_dm001_candidate_count" == "0" ]]; then
    return 0
fi

echo "  Podcast detection: scanning $_dm001_candidate_count Product rows for podcast publishers"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would run podcast detection backfill"
    return 0
fi

_dm001_run_backfill
_dm001_rc=$?
if [[ $_dm001_rc -eq 0 ]]; then
    echo "  Podcast detection backfill complete"
else
    echo "  Warning: podcast detection backfill exited with code $_dm001_rc"
fi
