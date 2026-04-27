#!/bin/bash
# Data migration 009: create translation_monitor_events audit-trail table (v8.3.9)
#
# Mirrors library/backend/migrations/025_translation_monitor_events.sql for
# environments that won't re-run schema.sql on upgrade. Backs the two-tier
# translation monitor (translation-monitor-live, translation-monitor-sampler)
# introduced in v8.3.9.
#
# Required after upgrades from any version < 8.3.9 to >= 8.3.9.
#
# Idempotency: CREATE TABLE / INDEX IF NOT EXISTS — safe to re-run.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.9"

_dm009_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm009_table_exists() {
    local table="$1"
    _dm009_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='${table}';" 2>/dev/null \
        | grep -q "^${table}$"
}

run_migration() {
    if _dm009_table_exists "translation_monitor_events"; then
        echo "  [009] translation_monitor_events table already present — skipping"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [009] DRY RUN: would CREATE TABLE translation_monitor_events + indexes"
        return 0
    fi

    echo "  [009] Creating translation_monitor_events table..."
    if _dm009_sqlite "CREATE TABLE IF NOT EXISTS translation_monitor_events (id INTEGER PRIMARY KEY AUTOINCREMENT, monitor TEXT NOT NULL CHECK (monitor IN ('live','sampler')), event_type TEXT NOT NULL, audiobook_id INTEGER, segment_id INTEGER, sampler_job_id INTEGER, worker_id TEXT, details TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);" 2>&1; then
        _dm009_sqlite "CREATE INDEX IF NOT EXISTS idx_tm_events_created ON translation_monitor_events(created_at);" 2>&1
        _dm009_sqlite "CREATE INDEX IF NOT EXISTS idx_tm_events_type ON translation_monitor_events(event_type);" 2>&1
        _dm009_sqlite "CREATE INDEX IF NOT EXISTS idx_tm_events_monitor_created ON translation_monitor_events(monitor, created_at);" 2>&1
        echo "  [009] translation_monitor_events table + indexes created"
        return 0
    else
        echo "  [009] ERROR: failed to create translation_monitor_events"
        return 1
    fi
}
