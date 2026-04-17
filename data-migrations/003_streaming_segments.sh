#!/bin/bash
# Data migration 003: streaming translation segment tables (v8.3.0)
#
# Creates two tables for the on-demand streaming translation pipeline:
#   - streaming_segments  — per-segment translation state and cache
#   - streaming_sessions  — active streaming playback sessions
#
# Required after upgrades from any version < 8.3.0 to >= 8.3.0.
# Idempotent: CREATE TABLE IF NOT EXISTS.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.0"

_dm003_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm003_table_exists() {
    local tbl="$1"
    _dm003_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='${tbl}';" 2>/dev/null |
        grep -q "^${tbl}$"
}

run_migration() {
    if _dm003_table_exists "streaming_segments" && _dm003_table_exists "streaming_sessions"; then
        echo "  [003] streaming tables already exist — skipping"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [003] DRY RUN: would create streaming_segments and streaming_sessions tables"
        return 0
    fi

    echo "  [003] Creating streaming translation tables..."

    _dm003_sqlite "
CREATE TABLE IF NOT EXISTS streaming_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    chapter_index INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    locale TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 2,
    worker_id TEXT,
    vtt_content TEXT,
    audio_path TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(audiobook_id, chapter_index, segment_index, locale),
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streaming_seg_book ON streaming_segments(audiobook_id, locale);
CREATE INDEX IF NOT EXISTS idx_streaming_seg_state ON streaming_segments(state, priority);
CREATE INDEX IF NOT EXISTS idx_streaming_seg_chapter ON streaming_segments(audiobook_id, chapter_index, locale);

CREATE TABLE IF NOT EXISTS streaming_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audiobook_id INTEGER NOT NULL,
    locale TEXT NOT NULL,
    active_chapter INTEGER NOT NULL DEFAULT 0,
    buffer_threshold INTEGER NOT NULL DEFAULT 6,
    state TEXT NOT NULL DEFAULT 'buffering',
    gpu_warm INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_streaming_sess_book ON streaming_sessions(audiobook_id, locale);
CREATE INDEX IF NOT EXISTS idx_streaming_sess_state ON streaming_sessions(state);
" 2>&1
    local rc=$?

    if [[ $rc -eq 0 ]]; then
        echo "  [003] Streaming translation tables created successfully"
    else
        echo "  [003] ERROR: Failed to create streaming tables"
        return 1
    fi
}
