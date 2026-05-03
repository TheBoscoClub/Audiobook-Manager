#!/bin/bash
# Data migration 012: purge translated/ chapter-artifact rows from audiobooks
# (v8.3.10.1)
#
# The scanner historically ingested ``.opus`` files under ``<book>/translated/``
# — per-chapter translation artifacts (e.g. ``Book.ch001.zh-Hans.opus``) — as
# standalone audiobook rows. They surfaced in the library grid as a flood of
# duplicate, single-chapter "books" with mangled titles.
#
# v8.3.10.1 adds a write-side filter in ``library/scanner/add_new_audiobooks.py``
# and ``library/scanner/scan_audiobooks.py`` plus a defense-in-depth WHERE
# filter on ``GET /api/audiobooks`` and ``GET /api/audiobooks/grouped``. This
# migration cleans up the existing rows on operator installs that were
# affected before the fix landed.
#
# Idempotent: the DELETE is keyed on ``file_path LIKE '%/translated/%'``, so
# repeated runs are harmless once the table is clean. FK cascades remove any
# associated junction rows (book_authors, book_narrators, audiobook_genres,
# audiobook_eras, audiobook_topics, etc.) thanks to ON DELETE CASCADE in the
# schema.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"
#
# See Audiobook-Manager-2sw.

# shellcheck disable=SC2154

MIN_VERSION="8.3.10.1"

_dm011_count_translated_rows() {
    local sql="SELECT COUNT(*) FROM audiobooks WHERE file_path LIKE '%/translated/%';"
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$sql" 2>/dev/null || echo "0"
    else
        sqlite3 "$DB_PATH" "$sql" 2>/dev/null || echo "0"
    fi
}

_dm011_purge_translated_rows() {
    # FK cascades only fire when foreign_keys is ON for this connection.
    # Wrap the DELETE in a transaction so a partial failure rolls back cleanly.
    local sql
    sql="$(
        cat <<'SQL'
PRAGMA foreign_keys = ON;
BEGIN;
DELETE FROM audiobooks WHERE file_path LIKE '%/translated/%';
COMMIT;
SQL
    )"

    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$sql"
    else
        sqlite3 "$DB_PATH" "$sql"
    fi
}

# Fresh-install / no-DB-yet — nothing to clean up.
if [[ ! -f "$DB_PATH" ]]; then
    return 0
fi

_dm011_before=$(_dm011_count_translated_rows)
_dm011_before="${_dm011_before//[^0-9]/}"
_dm011_before="${_dm011_before:-0}"

if [[ "$_dm011_before" == "0" ]]; then
    # Already clean — idempotent no-op.
    return 0
fi

echo "  [011] Found $_dm011_before translated/ chapter-artifact rows in audiobooks table"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [011] DRY RUN: would DELETE $_dm011_before rows where file_path LIKE '%/translated/%'"
    return 0
fi

if ! _dm011_purge_translated_rows; then
    echo "  [011] ERROR: purge failed (transaction rolled back)"
    return 1
fi

_dm011_after=$(_dm011_count_translated_rows)
_dm011_after="${_dm011_after//[^0-9]/}"
_dm011_after="${_dm011_after:-0}"
_dm011_removed=$((_dm011_before - _dm011_after))

echo "  [011] Purged $_dm011_removed translated/ chapter-artifact rows ($_dm011_after remaining)"
return 0
