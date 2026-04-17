#!/bin/bash
# Data migration 002: translation_queue heartbeat + chapter total (v8.2.2)
#
# Adds two columns to translation_queue:
#   - last_progress_at TIMESTAMP — heartbeat updated every chapter
#   - total_chapters   INTEGER   — denominator for X% of Y progress display
#
# Required after upgrades from any version < 8.2.2 to >= 8.2.2.
# Idempotent: ALTER TABLE is guarded by PRAGMA table_info checks.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.2.2"

_dm002_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm002_has_column() {
    local col="$1"
    _dm002_sqlite "PRAGMA table_info(translation_queue);" 2>/dev/null |
        awk -F'|' -v c="$col" '$2 == c { found=1 } END { exit !found }'
}

if [[ ! -f "$DB_PATH" ]]; then
    return 0
fi

# Skip cleanly if translation_queue doesn't exist (fresh installs that haven't
# initialized localization yet).
if ! _dm002_sqlite "SELECT 1 FROM sqlite_master WHERE type='table' AND name='translation_queue';" \
    2>/dev/null | grep -q 1; then
    return 0
fi

_dm002_need_progress=false
_dm002_need_total=false
_dm002_has_column "last_progress_at" || _dm002_need_progress=true
_dm002_has_column "total_chapters" || _dm002_need_total=true

if ! $_dm002_need_progress && ! $_dm002_need_total; then
    return 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would add translation_queue columns: last_progress_at, total_chapters"
    return 0
fi

if $_dm002_need_progress; then
    _dm002_sqlite "ALTER TABLE translation_queue ADD COLUMN last_progress_at TIMESTAMP;" &&
        _dm002_sqlite "UPDATE translation_queue SET last_progress_at = COALESCE(started_at, created_at);" &&
        _dm002_sqlite "CREATE INDEX IF NOT EXISTS idx_tq_last_progress ON translation_queue(last_progress_at);" &&
        echo "  Added translation_queue.last_progress_at"
fi

if $_dm002_need_total; then
    _dm002_sqlite "ALTER TABLE translation_queue ADD COLUMN total_chapters INTEGER;" &&
        echo "  Added translation_queue.total_chapters"
fi
