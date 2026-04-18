"""
Grouped audiobook endpoint — returns books grouped by author or narrator.

Used by the frontend's collapsible author/narrator sort views. Books with
multiple authors/narrators appear under each relevant group. Orphan books
(no junction rows) appear in a synthetic "Unknown" group at the end.
"""

import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from .auth import guest_allowed
from .core import FlaskResponse, get_db

# Reuse the same content-type filter as audiobooks.py
# Include: Product, Performance, Speech (all valid audiobook content)
# Exclude: Lecture, Podcast, Newspaper / Magazine, Show, Radio/TV Program, Episode
# content_type IS NULL handles legacy entries before the field was added
# This constant is safe for SQL - hardcoded, not user input
AUDIOBOOK_FILTER = "(content_type IN ('Product', 'Performance', 'Speech') OR content_type IS NULL)"

VALID_GROUP_BY = {"author", "narrator"}

grouped_bp = Blueprint("grouped", __name__)


def init_grouped_routes(
    db_path: Path,
) -> None:  # pylint: disable=unused-argument  # kept for API compatibility — path now resolved via current_app.config
    """Initialize grouped routes (no-op, kept for API compatibility).

    Database path is now resolved at request time via current_app.config.
    """
    pass


def _get_grouped_db() -> sqlite3.Connection:
    """Get database connection from current Flask app config."""
    db_path = current_app.config.get("DATABASE_PATH")
    if db_path is None:
        raise RuntimeError("DATABASE_PATH not configured in Flask app.")
    return get_db(db_path)


@grouped_bp.route("/api/audiobooks/grouped", methods=["GET"])
@guest_allowed
def get_grouped_audiobooks() -> FlaskResponse:
    """
    Get audiobooks grouped by author or narrator.

    Query params:
        by: Group by field — "author" or "narrator" (required)

    Returns:
        {
            "groups": [
                {
                    "key": {"id": N, "name": "...", "sort_name": "..."},
                    "books": [{"id": N, "title": "...", ...}, ...]
                },
                ...
            ],
            "total_groups": N,
            "total_books": N   # deduplicated
        }
    """
    group_by = request.args.get("by", "").strip().lower()
    if group_by not in VALID_GROUP_BY:
        return (
            jsonify(
                {
                    "error": f"Invalid 'by' parameter: '{group_by}'. "
                    f"Must be one of: {', '.join(sorted(VALID_GROUP_BY))}"
                }
            ),
            400,
        )

    # Locale-aware sort: when zh-* requested, use pinyin_sort from
    # audiobook_translations if available (falls back to English title).
    locale = request.args.get("locale", "").strip()
    use_pinyin = locale.startswith("zh")

    conn = _get_grouped_db()

    try:
        if group_by == "author":
            groups, all_book_ids = _group_by_author(conn, locale, use_pinyin)
        else:
            groups, all_book_ids = _group_by_narrator(conn, locale, use_pinyin)

        return jsonify(
            {"groups": groups, "total_groups": len(groups), "total_books": len(all_book_ids)}
        )
    finally:
        conn.close()


def _get_book_columns() -> str:
    """Return the SELECT columns for books in grouped responses."""
    return (
        "a.id, a.title, a.author, a.narrator, a.publisher, a.series, "
        "a.series_sequence, a.duration_hours, a.duration_formatted, "
        "a.file_size_mb, a.cover_path, a.format, "
        "a.published_date, a.published_year, a.release_date"
    )


def _row_to_book(row: sqlite3.Row) -> dict:
    """Convert a database row to a book dict for the grouped response."""
    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "narrator": row["narrator"],
        "publisher": row["publisher"],
        "series": row["series"],
        "series_sequence": row["series_sequence"],
        "duration_hours": row["duration_hours"],
        "duration_formatted": row["duration_formatted"],
        "file_size_mb": row["file_size_mb"],
        "cover_path": row["cover_path"],
        "format": row["format"],
        "published_date": row["published_date"],
        "published_year": row["published_year"],
        "release_date": row["release_date"],
    }


def _group_by_author(
    conn: sqlite3.Connection, locale: str = "", use_pinyin: bool = False
) -> tuple[list[dict], set[int]]:
    """Group audiobooks by author via junction tables.

    When ``use_pinyin`` is True, LEFT JOINs ``audiobook_translations`` on
    the given ``locale`` and orders books by
    ``COALESCE(at.pinyin_sort, a.title)`` so zh-* users see a pinyin-
    ordered grid. English queries are unchanged.
    """
    cursor = conn.cursor()
    book_cols = _get_book_columns()

    # Fetch all books with their authors, sorted by author sort_name then publication date
    # Publication date sort: prefer published_date (YYYY-MM-DD), fall back to
    # published_year, then release_date, then title. NULLS LAST via COALESCE.
    # AUDIOBOOK_FILTER and book_cols are hardcoded constants, not user input
    if use_pinyin:
        title_sort_expr = "COALESCE(NULLIF(at.pinyin_sort, ''), a.title) COLLATE NOCASE"
        join_clause = (
            " LEFT JOIN audiobook_translations at ON at.audiobook_id = a.id AND at.locale = ?"
        )
        sort_params: list = [locale]
    else:
        title_sort_expr = "a.title COLLATE NOCASE"
        join_clause = ""
        sort_params = []

    query = (
        f"SELECT {book_cols}, auth.id AS group_id, auth.name AS group_name,"  # nosec B608  # noqa: S608
        " auth.sort_name AS group_sort_name"
        " FROM audiobooks a"
        " JOIN book_authors ba ON a.id = ba.book_id"
        " JOIN authors auth ON ba.author_id = auth.id"
        f"{join_clause}"
        f" WHERE {AUDIOBOOK_FILTER}"
        " ORDER BY auth.sort_name COLLATE NOCASE,"
        " COALESCE(a.published_date, a.release_date,"
        " CAST(a.published_year AS TEXT) || '-01-01',"
        " '9999-12-31') ASC,"
        f" {title_sort_expr}"
    )
    cursor.execute(query, sort_params)
    rows = cursor.fetchall()

    # Build groups preserving query order
    groups: list[dict] = []
    group_map: dict[int, dict] = {}
    all_book_ids: set[int] = set()

    for row in rows:
        gid = row["group_id"]
        all_book_ids.add(row["id"])

        if gid not in group_map:
            group = {
                "key": {"id": gid, "name": row["group_name"], "sort_name": row["group_sort_name"]},
                "books": [],
            }
            group_map[gid] = group
            groups.append(group)

        group_map[gid]["books"].append(_row_to_book(row))

    # Find orphan books (no junction rows) — "Unknown Author" group
    # AUDIOBOOK_FILTER is a hardcoded constant
    query = (
        f"SELECT {book_cols}"  # nosec B608  # noqa: S608
        " FROM audiobooks a"
        f"{join_clause}"
        f" WHERE {AUDIOBOOK_FILTER}"
        " AND a.id NOT IN (SELECT book_id FROM book_authors)"
        f" ORDER BY {title_sort_expr}"
    )
    cursor.execute(query, sort_params)
    orphan_rows = cursor.fetchall()

    if orphan_rows:
        orphan_books = []
        for row in orphan_rows:
            all_book_ids.add(row["id"])
            orphan_books.append(_row_to_book(row))

        groups.append(
            {
                "key": {
                    "id": None,
                    "name": "Unknown Author",
                    "sort_name": "\uffff",  # Sorts to end
                },
                "books": orphan_books,
            }
        )

    return groups, all_book_ids


def _group_by_narrator(
    conn: sqlite3.Connection, locale: str = "", use_pinyin: bool = False
) -> tuple[list[dict], set[int]]:
    """Group audiobooks by narrator via junction tables.

    See ``_group_by_author`` for the pinyin sort semantics.
    """
    cursor = conn.cursor()
    book_cols = _get_book_columns()

    if use_pinyin:
        title_sort_expr = "COALESCE(NULLIF(at.pinyin_sort, ''), a.title) COLLATE NOCASE"
        join_clause = (
            " LEFT JOIN audiobook_translations at ON at.audiobook_id = a.id AND at.locale = ?"
        )
        sort_params: list = [locale]
    else:
        title_sort_expr = "a.title COLLATE NOCASE"
        join_clause = ""
        sort_params = []

    # AUDIOBOOK_FILTER and book_cols are hardcoded constants, not user input
    query = (
        f"SELECT {book_cols}, narr.id AS group_id, narr.name AS group_name,"  # nosec B608  # noqa: S608
        " narr.sort_name AS group_sort_name"
        " FROM audiobooks a"
        " JOIN book_narrators bn ON a.id = bn.book_id"
        " JOIN narrators narr ON bn.narrator_id = narr.id"
        f"{join_clause}"
        f" WHERE {AUDIOBOOK_FILTER}"
        " ORDER BY narr.sort_name COLLATE NOCASE,"
        " COALESCE(a.published_date, a.release_date,"
        " CAST(a.published_year AS TEXT) || '-01-01',"
        " '9999-12-31') ASC,"
        f" {title_sort_expr}"
    )
    cursor.execute(query, sort_params)
    rows = cursor.fetchall()

    groups: list[dict] = []
    group_map: dict[int, dict] = {}
    all_book_ids: set[int] = set()

    for row in rows:
        gid = row["group_id"]
        all_book_ids.add(row["id"])

        if gid not in group_map:
            group = {
                "key": {"id": gid, "name": row["group_name"], "sort_name": row["group_sort_name"]},
                "books": [],
            }
            group_map[gid] = group
            groups.append(group)

        group_map[gid]["books"].append(_row_to_book(row))

    # Orphan books — "Unknown Narrator"
    # AUDIOBOOK_FILTER is a hardcoded constant
    query = (
        f"SELECT {book_cols}"  # nosec B608  # noqa: S608
        " FROM audiobooks a"
        f"{join_clause}"
        f" WHERE {AUDIOBOOK_FILTER}"
        " AND a.id NOT IN (SELECT book_id FROM book_narrators)"
        f" ORDER BY {title_sort_expr}"
    )
    cursor.execute(query, sort_params)
    orphan_rows = cursor.fetchall()

    if orphan_rows:
        orphan_books = []
        for row in orphan_rows:
            all_book_ids.add(row["id"])
            orphan_books.append(_row_to_book(row))

        groups.append(
            {
                "key": {"id": None, "name": "Unknown Narrator", "sort_name": "\uffff"},
                "books": orphan_books,
            }
        )

    return groups, all_book_ids
