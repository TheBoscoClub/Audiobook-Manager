#!/bin/bash
# Data migration 007: add retry_count column to streaming_segments (v8.3.4)
#
# Mirrors library/backend/migrations/023_streaming_retry_count.sql for
# environments that won't re-run schema.sql on upgrade. Enables bounded
# retry policy in scripts/stream-translate-worker.py — on exception the
# worker increments retry_count and requeues (state='pending'), flipping
# to state='failed' only after retry_count >= 3.
#
# Required after upgrades from any version < 8.3.4 to >= 8.3.4.
# Idempotent: ALTER TABLE ADD COLUMN raises "duplicate column name" on
# re-run; we detect the column first and skip.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.4"

_dm007_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm007_column_exists() {
    _dm007_sqlite "PRAGMA table_info(streaming_segments);" 2>/dev/null \
        | awk -F'|' '{print $2}' \
        | grep -qx "retry_count"
}

run_migration() {
    if ! _dm007_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='streaming_segments';" 2>/dev/null \
        | grep -q "^streaming_segments$"; then
        echo "  [007] streaming_segments table not present — skipping (will be created with column on first scan)"
        return 0
    fi

    if _dm007_column_exists; then
        echo "  [007] streaming_segments.retry_count already exists — skipping"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [007] DRY RUN: would ALTER TABLE streaming_segments ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
        return 0
    fi

    echo "  [007] Adding retry_count column to streaming_segments..."
    if _dm007_sqlite "ALTER TABLE streaming_segments ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;" 2>&1; then
        echo "  [007] Column added successfully"
    else
        echo "  [007] ERROR: failed to add column"
        return 1
    fi
}
