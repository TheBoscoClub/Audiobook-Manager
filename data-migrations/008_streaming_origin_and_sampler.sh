#!/bin/bash
# Data migration 008: add origin column to streaming_segments, create
# sampler_jobs table, install priority-invariant triggers (v8.3.8)
#
# Mirrors library/backend/migrations/024_streaming_origin_and_sampler.sql
# for environments that won't re-run schema.sql on upgrade. Enables the
# 6-minute pretranslation sampler (scripts/sampler-daemon.py + the
# scan/locale-add triggers). See docs/SAMPLER.md for the full feature
# description.
#
# Required after upgrades from any version < 8.3.8 to >= 8.3.8.
#
# Idempotency: each step detects its prior state (column presence, table
# presence, trigger presence) and skips instead of failing. Safe to re-run.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.8"

_dm008_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm008_column_exists() {
    local table="$1"
    local column="$2"
    _dm008_sqlite "PRAGMA table_info(${table});" 2>/dev/null \
        | awk -F'|' '{print $2}' \
        | grep -qx "$column"
}

_dm008_table_exists() {
    local table="$1"
    _dm008_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='${table}';" 2>/dev/null \
        | grep -q "^${table}$"
}

_dm008_trigger_exists() {
    local trigger="$1"
    _dm008_sqlite "SELECT name FROM sqlite_master WHERE type='trigger' AND name='${trigger}';" 2>/dev/null \
        | grep -q "^${trigger}$"
}

run_migration() {
    # ─── Step 1: streaming_segments.origin column ───
    if ! _dm008_table_exists "streaming_segments"; then
        echo "  [008] streaming_segments table not present — skipping (created with column on first scan)"
    elif _dm008_column_exists "streaming_segments" "origin"; then
        echo "  [008] streaming_segments.origin already present — skipping column add"
    elif [[ "$DRY_RUN" == "true" ]]; then
        echo "  [008] DRY RUN: would ALTER TABLE streaming_segments ADD COLUMN origin"
    else
        echo "  [008] Adding origin column to streaming_segments..."
        if _dm008_sqlite "ALTER TABLE streaming_segments ADD COLUMN origin TEXT NOT NULL DEFAULT 'live' CHECK (origin IN ('live','sampler','backlog'));" 2>&1; then
            echo "  [008] origin column added"
        else
            echo "  [008] ERROR: failed to add origin column"
            return 1
        fi
    fi

    # ─── Step 2: priority-invariant triggers ───
    # Two triggers — one for INSERT, one for UPDATE. CREATE TRIGGER IF NOT
    # EXISTS handles re-runs.
    for trigger_pair in \
        "streaming_segments_sampler_priority_ins|BEFORE INSERT" \
        "streaming_segments_sampler_priority_upd|BEFORE UPDATE"; do
        local name="${trigger_pair%%|*}"
        local when="${trigger_pair##*|}"
        if _dm008_trigger_exists "$name"; then
            echo "  [008] trigger $name already present — skipping"
            continue
        fi
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [008] DRY RUN: would CREATE TRIGGER $name"
            continue
        fi
        echo "  [008] Creating trigger $name ($when)..."
        if _dm008_sqlite "CREATE TRIGGER IF NOT EXISTS $name $when ON streaming_segments WHEN NEW.origin = 'sampler' AND NEW.priority < 2 BEGIN SELECT RAISE(ABORT, 'sampler rows must have priority >= 2 (p0/p1 reserved for live playback)'); END;" 2>&1; then
            echo "  [008] $name created"
        else
            echo "  [008] ERROR: failed to create $name"
            return 1
        fi
    done

    # ─── Step 3: sampler_jobs table ───
    if _dm008_table_exists "sampler_jobs"; then
        echo "  [008] sampler_jobs table already present — skipping"
    elif [[ "$DRY_RUN" == "true" ]]; then
        echo "  [008] DRY RUN: would CREATE TABLE sampler_jobs"
    else
        echo "  [008] Creating sampler_jobs table..."
        if _dm008_sqlite "CREATE TABLE IF NOT EXISTS sampler_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, audiobook_id INTEGER NOT NULL, locale TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', segments_target INTEGER NOT NULL, segments_done INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(audiobook_id, locale), FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE);" 2>&1; then
            _dm008_sqlite "CREATE INDEX IF NOT EXISTS idx_sampler_jobs_status ON sampler_jobs(status);" 2>&1
            _dm008_sqlite "CREATE INDEX IF NOT EXISTS idx_sampler_jobs_locale ON sampler_jobs(locale, status);" 2>&1
            echo "  [008] sampler_jobs table + indexes created"
        else
            echo "  [008] ERROR: failed to create sampler_jobs"
            return 1
        fi
    fi

    return 0
}
