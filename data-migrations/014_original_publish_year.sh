#!/bin/bash
# Data migration 014: audiobooks.original_publish_year column (v8.4.2.0)
#
# Adds a nullable original_publish_year column to the audiobooks table
# (Audiobook-Manager-nfx). The existing published_year is Audible-sourced
# and conflates the book's print-publication year with the audiobook's
# release year (~7.5% mismatches in prod). original_publish_year holds the
# ORIGINAL print-publication year from Open Library (first_publish_year),
# powering the "Original Print Year" sort dimension.
#
# Population strategy (deliberately NOT done here):
# - New imports: populated by the "Original print year" post-insert hook
#   (library/scanner/post_insert.py -> scripts/original_print_year.py)
# - Existing rows (~1880): operator-run backfill —
#     /opt/audiobooks/library/venv/bin/python \
#         /opt/audiobooks/library/scripts/original_print_year.py --backfill --execute
#   The backfill hits the Open Library API with polite rate limiting
#   (~0.6s/request => ~20 minutes for a full library), far too slow and
#   network-dependent to run inside an upgrade. It is resumable (skips rows
#   already populated), so it can be run any time after this migration.
# - Rows OL cannot resolve stay NULL — the sort SQL falls back to
#   published_year via COALESCE, so no fallback value is baked into data.
#
# Required after upgrades from any version < 8.4.2.0 to >= 8.4.2.0.
# Idempotent: ALTER TABLE is guarded by a PRAGMA table_info check.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.4.2.0"

_dm014_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

_dm014_has_column() {
    local col="$1"
    _dm014_sqlite "PRAGMA table_info(audiobooks);" 2>/dev/null \
        | awk -F'|' -v c="$col" '$2 == c { found=1 } END { exit !found }'
}

if [[ ! -f "$DB_PATH" ]]; then
    return 0
fi

# Skip cleanly if audiobooks table doesn't exist (pre-init state).
if ! _dm014_sqlite "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audiobooks';" \
    2>/dev/null | grep -q 1; then
    return 0
fi

if _dm014_has_column "original_publish_year"; then
    return 0
fi

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would add audiobooks.original_publish_year column"
    return 0
fi

_dm014_sqlite "ALTER TABLE audiobooks ADD COLUMN original_publish_year INTEGER;" \
    && echo "  Added audiobooks.original_publish_year (backfill: scripts/original_print_year.py --backfill --execute)"
