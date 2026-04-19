#!/bin/bash
# Data migration 004: audiobooks.chapter_count column (v8.3.2)
#
# Adds a nullable chapter_count column to the audiobooks table. This column
# is populated at scan/ingest time by the scanner (free — ffprobe already
# runs), and lazily backfilled on first streaming request for existing rows.
#
# Why: streaming_translate.py's _get_chapter_count() previously fell back to
# translation_queue.total_chapters when no chapter_subtitles rows existed,
# returning 0 for any book that had never been translated. Downstream code
# used `or 1`, collapsing the entire book into a single virtual chapter —
# which meant a 24-hour Sapiens request queued 1836 segments with no seek
# bounds and no chapter grouping.
#
# chapter_count on the audiobooks table is the ontologically correct home:
# chapters are a property of the audio file, not of a translation attempt.
#
# Required after upgrades from any version < 8.3.2 to >= 8.3.2.
# Idempotent: ALTER TABLE is guarded by PRAGMA table_info check. No forced
# backfill — rows populate lazily on first streaming request (~50 ms once
# per book, forever).
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.2"

_dm004_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm004_has_column() {
    local col="$1"
    _dm004_sqlite "PRAGMA table_info(audiobooks);" 2>/dev/null \
        | awk -F'|' -v c="$col" '$2 == c { found=1 } END { exit !found }'
}

if [[ ! -f "$DB_PATH" ]]; then
    return 0
fi

# Skip cleanly if audiobooks table doesn't exist (pre-init state).
if ! _dm004_sqlite "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiobooks';" \
    2>/dev/null | grep -q 1; then
    return 0
fi

if _dm004_has_column "chapter_count"; then
    return 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would add audiobooks.chapter_count column"
    return 0
fi

_dm004_sqlite "ALTER TABLE audiobooks ADD COLUMN chapter_count INTEGER;" \
    && echo "  Added audiobooks.chapter_count"
