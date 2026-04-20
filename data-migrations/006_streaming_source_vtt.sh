#!/bin/bash
# Data migration 006: add source_vtt_content column to streaming_segments (v8.3.2)
#
# Mirrors library/backend/migrations/022_streaming_source_vtt.sql for
# environments that won't re-run schema.sql on upgrade. Persists the
# English (source) VTT alongside the translated VTT so the bilingual
# transcript panel (双语文字记录) can render after a chapter consolidates.
#
# Required after upgrades from any version < 8.3.2 to >= 8.3.2.
# Idempotent: ALTER TABLE ADD COLUMN raises "duplicate column name" on
# re-run; we detect the column first and skip.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.2"

_dm006_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm006_column_exists() {
    _dm006_sqlite "PRAGMA table_info(streaming_segments);" 2>/dev/null \
        | awk -F'|' '{print $2}' \
        | grep -qx "source_vtt_content"
}

run_migration() {
    if ! _dm006_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='streaming_segments';" 2>/dev/null \
        | grep -q "^streaming_segments$"; then
        echo "  [006] streaming_segments table not present — skipping (will be created with column on first scan)"
        return 0
    fi

    if _dm006_column_exists; then
        echo "  [006] streaming_segments.source_vtt_content already exists — skipping"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [006] DRY RUN: would ALTER TABLE streaming_segments ADD COLUMN source_vtt_content TEXT"
        return 0
    fi

    echo "  [006] Adding source_vtt_content column to streaming_segments..."
    if _dm006_sqlite "ALTER TABLE streaming_segments ADD COLUMN source_vtt_content TEXT;" 2>&1; then
        echo "  [006] Column added successfully"
    else
        echo "  [006] ERROR: failed to add column"
        return 1
    fi
}
