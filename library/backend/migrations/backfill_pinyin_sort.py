#!/usr/bin/env python3
"""
Backfill the `pinyin_sort` column on `audiobook_translations`.

Strategy:
1. Ensure the column exists by running migration 021 (idempotent — catches
   "duplicate column name" when re-run).
2. For every row whose `pinyin_sort` is NULL or empty, compute a tone-
   stripped lowercase pinyin key from `title` using
   `api_modular.search_cjk.pinyin_sort_key`.
3. UPDATE each row. Rows whose title is empty or for which pinyin
   generation fails are left NULL — query-time COALESCE falls back to
   the English title_sort.

This script mirrors the style of `backfill_asins.py` and is safe to re-
run; unchanged rows are no-ops.

Usage:
    python -m backend.migrations.backfill_pinyin_sort [--db-path PATH]

If --db-path is omitted, DATABASE_PATH from backend.config is used, so
no hardcoded paths leak in.
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

# Add parent directories to path for config import
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent.parent.parent))  # library/

# Side-load search_cjk.py directly (bypasses api_modular/__init__.py which
# imports i18n — a module only resolved at Flask app init time).
_SEARCH_CJK_PATH = _THIS.parent.parent / "api_modular" / "search_cjk.py"
_spec = importlib.util.spec_from_file_location("_search_cjk_backfill", _SEARCH_CJK_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load search_cjk module spec from {_SEARCH_CJK_PATH}")
_search_cjk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_search_cjk)
pinyin_sort_key = _search_cjk.pinyin_sort_key

try:
    from backend.config import DATABASE_PATH  # type: ignore  # noqa: E402
except ImportError:
    # Running outside the backend package context — caller must pass --db-path
    DATABASE_PATH = ""  # type: ignore

MIGRATION_SQL_PATH = _THIS.parent / "021_audiobook_translations_pinyin_sort.sql"


def apply_column_migration(conn: sqlite3.Connection) -> None:
    """Apply migration 021 idempotently.

    `ALTER TABLE ... ADD COLUMN` is not guarded by IF NOT EXISTS in
    SQLite, so we run the SQL and catch the "duplicate column name"
    OperationalError.
    """
    sql = MIGRATION_SQL_PATH.read_text()
    try:
        conn.executescript(sql)
        conn.commit()
        print(f"[migration 021] Applied {MIGRATION_SQL_PATH.name}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            # Column already exists — still run the CREATE INDEX portion
            conn.executescript(
                "CREATE INDEX IF NOT EXISTS idx_audiobook_translations_pinyin_sort "
                "ON audiobook_translations(locale, pinyin_sort);"
            )
            conn.commit()
            print("[migration 021] pinyin_sort column already exists — skipped ADD COLUMN")
        else:
            raise


def backfill(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Populate pinyin_sort on rows that need it.

    Returns (scanned, updated, skipped_empty).
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, locale, title FROM audiobook_translations "
        "WHERE pinyin_sort IS NULL OR pinyin_sort = ''"
    )
    rows = cursor.fetchall()

    scanned = len(rows)
    updated = 0
    skipped = 0

    for row_id, _locale, title in rows:
        key = pinyin_sort_key(title or "")
        if key is None:
            # Empty title OR pypinyin unavailable — leave NULL so the
            # query-time COALESCE falls back to English title_sort.
            skipped += 1
            continue
        cursor.execute(
            "UPDATE audiobook_translations SET pinyin_sort = ? WHERE id = ?", (key, row_id)
        )
        updated += 1

    conn.commit()
    return scanned, updated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=None,
        help=("Path to audiobooks SQLite database. Defaults to backend.config.DATABASE_PATH."),
    )
    args = parser.parse_args()

    db_path = args.db_path or str(DATABASE_PATH)
    print(f"Backfilling pinyin_sort into database: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        apply_column_migration(conn)
        scanned, updated, skipped = backfill(conn)
        print(f"[backfill] scanned={scanned} updated={updated} skipped_empty={skipped}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
