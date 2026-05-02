#!/bin/bash
# Data migration 011: backfill audiobook_eras from published_year (v8.3.10.1)
#
# Audiobook-Manager-lpq: only 16 of 1,867 books on prod 2026-05-02 had any
# audiobook_eras row, despite published_year being populated for all 1,867.
# The bulk-import path in backend/import_to_db.py inserts era rows from
# book.get("eras", []) — empty for any book where the importer didn't pre-
# compute eras. The scanner-direct path (utils/db_helpers.py) DOES compute
# eras via determine_literary_era(year), but that path didn't fire for the
# bulk-imported majority.
#
# This migration fills the gap: for every audiobook with a non-NULL
# published_year and no existing audiobook_eras row, derive the era from
# the year and insert it. The era buckets match the canonical mapping in
# library/scanner/metadata_utils.py::determine_literary_era exactly.
#
# Idempotent: skips books that already have an era row (no overwrite). Safe
# to re-run.
#
# Required after upgrades from any version < 8.3.10.1 to >= 8.3.10.1.
#
# Variables set by caller:
#   DB_PATH       — path to audiobooks.db
#   USE_SUDO      — "sudo" or "" for privilege elevation
#   DRY_RUN       — "true" or "false"

# shellcheck disable=SC2154

MIN_VERSION="8.3.10.1"

_dm011_sqlite() {
    if [[ -n "$USE_SUDO" ]]; then
        sudo -u audiobooks sqlite3 "$DB_PATH" "$@"
    else
        sqlite3 "$DB_PATH" "$@"
    fi
}

if [[ ! -f "$DB_PATH" ]]; then
    return 0
fi

# Skip cleanly if audiobooks table doesn't exist (pre-init state).
if ! _dm011_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='audiobooks';" 2>/dev/null | grep -q '^audiobooks$'; then
    return 0
fi

# Confirm both eras and audiobook_eras tables exist
if ! _dm011_sqlite "SELECT name FROM sqlite_master WHERE type='table' AND name='audiobook_eras';" 2>/dev/null | grep -q '^audiobook_eras$'; then
    return 0
fi

# Count books missing eras BEFORE the backfill — informational
missing_before=$(_dm011_sqlite "
    SELECT COUNT(*)
    FROM audiobooks a
    WHERE a.published_year IS NOT NULL
      AND a.published_year > 0
      AND NOT EXISTS (
        SELECT 1 FROM audiobook_eras ae WHERE ae.audiobook_id = a.id
      );
" 2>/dev/null)

if [[ -z "$missing_before" || "$missing_before" == "0" ]]; then
    echo "  [011] No books needing era backfill (already populated)."
    return 0
fi

echo "  [011] Backfilling audiobook_eras for $missing_before books..."

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [011] DRY-RUN: would insert era rows for $missing_before books."
    return 0
fi

# Era buckets MUST match library/scanner/metadata_utils.py::determine_literary_era
# - Pre-1800        → "Classical (Pre-1800)"
# - 1800-1899       → "19th Century (1800-1899)"
# - 1900-1949       → "Early 20th Century (1900-1949)"
# - 1950-1999       → "Late 20th Century (1950-1999)"
# - 2000-2009       → "21st Century - Early (2000-2009)"
# - 2010-2019       → "21st Century - Modern (2010-2019)"
# - 2020+           → "21st Century - Contemporary (2020+)"
#
# Single SQL transaction: ensure every era bucket exists in `eras`, then
# insert audiobook_eras rows for books missing them. INSERT OR IGNORE on
# eras names + the NOT EXISTS guard on audiobook_eras gives idempotency.

_dm011_sqlite <<'SQL'
BEGIN;

-- Ensure all canonical era names exist (idempotent — INSERT OR IGNORE on UNIQUE name)
INSERT OR IGNORE INTO eras (name) VALUES
    ('Classical (Pre-1800)'),
    ('19th Century (1800-1899)'),
    ('Early 20th Century (1900-1949)'),
    ('Late 20th Century (1950-1999)'),
    ('21st Century - Early (2000-2009)'),
    ('21st Century - Modern (2010-2019)'),
    ('21st Century - Contemporary (2020+)');

-- Backfill audiobook_eras for books with published_year but no era row
INSERT INTO audiobook_eras (audiobook_id, era_id)
SELECT
    a.id,
    e.id
FROM audiobooks a
JOIN eras e ON e.name = CASE
    WHEN a.published_year < 1800 THEN 'Classical (Pre-1800)'
    WHEN a.published_year < 1900 THEN '19th Century (1800-1899)'
    WHEN a.published_year < 1950 THEN 'Early 20th Century (1900-1949)'
    WHEN a.published_year < 2000 THEN 'Late 20th Century (1950-1999)'
    WHEN a.published_year < 2010 THEN '21st Century - Early (2000-2009)'
    WHEN a.published_year < 2020 THEN '21st Century - Modern (2010-2019)'
    ELSE '21st Century - Contemporary (2020+)'
END
WHERE a.published_year IS NOT NULL
  AND a.published_year > 0
  AND NOT EXISTS (
    SELECT 1 FROM audiobook_eras ae WHERE ae.audiobook_id = a.id
  );

COMMIT;
SQL

# Verify
missing_after=$(_dm011_sqlite "
    SELECT COUNT(*)
    FROM audiobooks a
    WHERE a.published_year IS NOT NULL
      AND a.published_year > 0
      AND NOT EXISTS (
        SELECT 1 FROM audiobook_eras ae WHERE ae.audiobook_id = a.id
      );
" 2>/dev/null)

backfilled=$((missing_before - ${missing_after:-0}))
echo "  [011] Backfilled $backfilled era rows. Books still without eras (no published_year): ${missing_after:-?}"
