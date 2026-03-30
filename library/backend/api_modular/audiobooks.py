"""
Audiobook listing, filtering, streaming, and individual book routes.

Note: All queries filter by content_type to exclude non-audiobook content
(podcasts, newspapers, etc.) from the main library.
"""

import logging
import subprocess
import sys
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    send_file,
    send_from_directory,
)

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import COVER_DIR, AUDIOBOOKS_WEBM_CACHE

from .collections import get_collections_lookup
from .core import FlaskResponse, get_db
from .editions import has_edition_marker, normalize_base_title
from .auth import auth_if_enabled, guest_allowed, download_permission_required

logger = logging.getLogger(__name__)

audiobooks_bp = Blueprint("audiobooks", __name__)

# Filter condition for main library (excludes non-audiobook content)
# Include: Product, Performance, Speech (all valid audiobook content)
# Exclude: Lecture, Podcast, Newspaper / Magazine, Show, Radio/TV Program, Episode
# content_type IS NULL handles legacy entries before the field was added
# This constant is safe for SQL - hardcoded, not user input
AUDIOBOOK_FILTER = "(content_type = 'Product' OR content_type IS NULL)"


def init_audiobooks_routes(db_path, project_root, database_path):
    """Initialize audiobooks routes (no-op, kept for API compatibility).

    Database path is now resolved at request time via current_app.config.
    """
    pass


def _get_audiobooks_db():
    """Get database connection from current Flask app config."""
    db_path = current_app.config.get("DATABASE_PATH")
    if db_path is None:
        raise RuntimeError("DATABASE_PATH not configured in Flask app.")
    return get_db(db_path)


@audiobooks_bp.route("/api/stats", methods=["GET"])
@guest_allowed
def get_stats() -> Response:
    """Get library statistics (audiobooks only)"""
    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    # Total audiobooks (audiobooks only)
    cursor.execute(f"SELECT COUNT(*) as total FROM audiobooks WHERE {AUDIOBOOK_FILTER}")  # nosec B608
    total_books = cursor.fetchone()["total"]

    # Total hours (audiobooks only)
    cursor.execute(
        "SELECT SUM(duration_hours) as total_hours FROM audiobooks"  # nosec B608
        f" WHERE {AUDIOBOOK_FILTER}"
    )
    total_hours = cursor.fetchone()["total_hours"] or 0

    # Total storage used (sum of file sizes in MB, convert to GB)
    cursor.execute(
        "SELECT SUM(file_size_mb) as total_size FROM audiobooks"  # nosec B608
        f" WHERE {AUDIOBOOK_FILTER}"
    )
    total_size_mb = cursor.fetchone()["total_size"] or 0
    total_size_gb = total_size_mb / 1024

    # Unique counts (excluding placeholder values like "Audiobook" and "Unknown")
    query = (
        f"SELECT COUNT(DISTINCT author) as count FROM audiobooks"  # nosec B608
        f" WHERE {AUDIOBOOK_FILTER}"
        " AND author IS NOT NULL"
        " AND LOWER(TRIM(author)) != 'audiobook'"
        " AND LOWER(TRIM(author)) != 'unknown author'"
    )
    cursor.execute(query)
    unique_authors = cursor.fetchone()["count"]

    query = (
        f"SELECT COUNT(DISTINCT narrator) as count FROM audiobooks"  # nosec B608
        f" WHERE {AUDIOBOOK_FILTER}"
        " AND narrator IS NOT NULL"
        " AND LOWER(TRIM(narrator)) != 'unknown narrator'"
        " AND LOWER(TRIM(narrator)) != ''"
    )
    cursor.execute(query)
    unique_narrators = cursor.fetchone()["count"]

    cursor.execute(
        "SELECT COUNT(DISTINCT publisher) as count FROM audiobooks"  # nosec B608
        f" WHERE {AUDIOBOOK_FILTER} AND publisher IS NOT NULL"
    )
    unique_publishers = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM genres")
    unique_genres = cursor.fetchone()["count"]

    conn.close()

    # Get database file size
    database_size_mb: float = 0.0
    try:
        import os

        db_path_str = str(current_app.config.get("DATABASE_PATH", ""))
        if os.path.exists(db_path_str):
            database_size_mb = os.path.getsize(db_path_str) / (1024 * 1024)
    except OSError:
        pass  # Non-critical: database size is for informational display only

    return jsonify(
        {
            "total_audiobooks": total_books,
            "total_hours": round(total_hours),
            "total_days": round(total_hours / 24),
            "total_size_gb": round(total_size_gb, 2),
            "database_size_mb": round(database_size_mb, 2),
            "unique_authors": unique_authors,
            "unique_narrators": unique_narrators,
            "unique_publishers": unique_publishers,
            "unique_genres": unique_genres,
        }
    )


# --- Sort configuration constants (module-level, shared by helpers) ---

# Map user-friendly sort names to SQL column names.
# Text columns get COLLATE NOCASE applied in the ORDER BY clause.
_SORT_MAPPINGS = {
    "title": "title",
    "author": "author",
    "author_last": "author_last_name",
    "author_first": "author_first_name",
    "narrator": "narrator",
    "narrator_last": "narrator_last_name",
    "narrator_first": "narrator_first_name",
    "duration_hours": "duration_hours",
    "created_at": "created_at",
    "acquired_date": "acquired_date",
    "published_year": "published_year",
    "published_date": "published_date",
    "file_size_mb": "file_size_mb",
    "series": None,  # Special handling in _build_sort_clause
    "asin": "asin",
    "edition": "edition",
}

# Text columns that need case-insensitive sorting
_TEXT_SORTS = {"title", "author", "narrator", "asin", "edition"}

# Nullable columns: push NULLs to end regardless of sort direction
# SQLite doesn't support NULLS LAST natively; use CASE expression
_NULLABLE_SORTS = {
    "author_last_name",
    "author_first_name",
    "narrator_last_name",
    "narrator_first_name",
    "acquired_date",
    "published_date",
}

# Name-based sorts: use COLLATE NOCASE for case-insensitive ordering
# (handles "Le Carre" vs "le Carre", "Del Toro" vs "del Toro")
# and add secondary sort by title for deterministic order within same name
_NAME_SORTS = {
    "author_last_name",
    "author_first_name",
    "narrator_last_name",
    "narrator_first_name",
}


def _parse_query_params(req) -> dict:
    """Extract and validate query parameters from the request.

    Returns a dict with all parsed/validated parameters ready for use
    by the query builder and sort clause builder.
    """
    page = max(1, int(req.args.get("page", 1)))
    per_page = min(200, max(1, int(req.args.get("per_page", 50))))
    sort_order = req.args.get("order", "asc").lower()
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"

    return {
        "page": page,
        "per_page": per_page,
        "search": req.args.get("search", "").strip(),
        "author": req.args.get("author", "").strip(),
        "narrator": req.args.get("narrator", "").strip(),
        "publisher": req.args.get("publisher", "").strip(),
        "genre": req.args.get("genre", "").strip(),
        "format_filter": req.args.get("format", "").strip(),
        "collection": req.args.get("collection", "").strip(),
        "sort_field": req.args.get("sort", "title"),
        "sort_order": sort_order,
    }


def _build_sort_clause(sort_field: str, sort_order: str) -> tuple[str, str]:
    """Generate ORDER BY clause components from sort field and order.

    Returns (sort_sql, sort_order) where sort_order may be empty if
    the direction is already embedded in sort_sql.
    """
    # Get SQL sort expression from allowlist
    sort_sql = _SORT_MAPPINGS.get(sort_field, "title")

    # Series: sort by series name (user's asc/desc), then sequence always ascending
    if sort_field == "series":
        return f"series COLLATE NOCASE {sort_order}, series_sequence ASC", ""

    # After series handling, sort_sql is always str (default is "title")
    assert sort_sql is not None

    if sort_sql in _NULLABLE_SORTS:
        if sort_sql in _NAME_SORTS:
            # NULLS LAST + case-insensitive + secondary sort by title
            return (
                f"CASE WHEN {sort_sql} IS NULL THEN 1 ELSE 0 END, "
                f"{sort_sql} COLLATE NOCASE {sort_order}, "
                f"title COLLATE NOCASE ASC"
            ), ""
        return f"CASE WHEN {sort_sql} IS NULL THEN 1 ELSE 0 END, {sort_sql}", sort_order
    elif sort_sql in _TEXT_SORTS:
        # Case-insensitive sorting for text columns
        return f"{sort_sql} COLLATE NOCASE", sort_order

    return sort_sql, sort_order


def _resolve_collection(collection: str | None) -> dict | None:
    """Look up collection data from the collections registry."""
    if not collection:
        return None
    db_path = str(current_app.config.get("DATABASE_PATH", ""))
    collections = get_collections_lookup(db_path)
    return collections.get(collection)


# Table-driven filter specs: (param_key, sql_clause, param_transform)
# param_transform: None means use value as-is, callable transforms the value
_FILTER_SPECS: list[tuple[str, str, object]] = [
    (
        "search",
        "id IN (SELECT rowid FROM audiobooks_fts WHERE audiobooks_fts MATCH ?)",
        None,
    ),
    (
        "author",
        """id IN (
                SELECT ba.book_id FROM book_authors ba
                JOIN authors a ON a.id = ba.author_id
                WHERE a.name = ?
            )""",
        None,
    ),
    (
        "narrator",
        """id IN (
                SELECT bn.book_id FROM book_narrators bn
                JOIN narrators n ON n.id = bn.narrator_id
                WHERE n.name = ?
            )""",
        None,
    ),
    ("publisher", "publisher LIKE ?", lambda v: f"%{v}%"),
    ("format_filter", "format = ?", lambda v: v.lower()),
    (
        "genre",
        """id IN (
                SELECT audiobook_id FROM audiobook_genres ag
                JOIN genres g ON ag.genre_id = g.id
                WHERE g.name LIKE ?
            )""",
        lambda v: f"%{v}%",
    ),
]


def _apply_param_filters(params: dict) -> tuple[list[str], list]:
    """Apply table-driven parameter filters to build WHERE clauses."""
    where_clauses: list[str] = []
    sql_params: list = []
    for key, clause, transform in _FILTER_SPECS:
        value = params.get(key)
        if value:
            where_clauses.append(clause)
            sql_params.append(transform(value) if callable(transform) else value)
    return where_clauses, sql_params


def _build_filter_clauses(params: dict) -> tuple[list[str], list]:
    """Generate WHERE conditions and query params from parsed filter values.

    Handles collection lookup, content-type bypass, search, author/narrator/
    publisher/genre/format filters, collection query injection, and
    sort-specific filters (e.g., series-only).
    """
    collection_data = _resolve_collection(params.get("collection"))

    bypasses = collection_data and collection_data.get("bypasses_filter", False)
    where_clauses: list[str] = [] if bypasses else [AUDIOBOOK_FILTER]

    param_clauses, sql_params = _apply_param_filters(params)
    where_clauses.extend(param_clauses)

    if collection_data:
        where_clauses.append(f"({collection_data['query']})")

    if params.get("sort_field") == "series":
        where_clauses.append("series IS NOT NULL AND series != ''")

    return where_clauses, sql_params


def _batch_load_metadata(cursor, book_ids: list[int]) -> dict:
    """Load genres, eras, topics, supplements, authors, and narrators in batch.

    Returns a dict of maps keyed by metadata type, each mapping book_id
    to its associated data.
    """
    placeholders = ",".join("?" * len(book_ids))

    # Batch: genres for all books in one query
    cursor.execute(
        f"""
        SELECT ag.audiobook_id, g.name FROM genres g
        JOIN audiobook_genres ag ON g.id = ag.genre_id
        WHERE ag.audiobook_id IN ({placeholders})
        """,  # nosec B608
        book_ids,
    )
    genres_map: dict[int, list[str]] = {}
    for r in cursor.fetchall():
        genres_map.setdefault(r["audiobook_id"], []).append(r["name"])

    # Batch: eras for all books in one query
    cursor.execute(
        f"""
        SELECT ae.audiobook_id, e.name FROM eras e
        JOIN audiobook_eras ae ON e.id = ae.era_id
        WHERE ae.audiobook_id IN ({placeholders})
        """,  # nosec B608
        book_ids,
    )
    eras_map: dict[int, list[str]] = {}
    for r in cursor.fetchall():
        eras_map.setdefault(r["audiobook_id"], []).append(r["name"])

    # Batch: topics for all books in one query
    cursor.execute(
        f"""
        SELECT at.audiobook_id, t.name FROM topics t
        JOIN audiobook_topics at ON t.id = at.topic_id
        WHERE at.audiobook_id IN ({placeholders})
        """,  # nosec B608
        book_ids,
    )
    topics_map: dict[int, list[str]] = {}
    for r in cursor.fetchall():
        topics_map.setdefault(r["audiobook_id"], []).append(r["name"])

    # Batch: supplement counts in one query
    cursor.execute(
        f"""
        SELECT audiobook_id, COUNT(*) as count FROM supplements
        WHERE audiobook_id IN ({placeholders})
        GROUP BY audiobook_id
        """,  # nosec B608
        book_ids,
    )
    supplements_map = {r["audiobook_id"]: r["count"] for r in cursor.fetchall()}

    # Batch: authors for all books in one query (normalized many-to-many)
    authors_map: dict[int, list[dict]] = {}
    try:
        query = (
            "SELECT ba.book_id, a.id, a.name, a.sort_name, ba.position"  # nosec B608
            " FROM book_authors ba"
            " JOIN authors a ON ba.author_id = a.id"
            f" WHERE ba.book_id IN ({placeholders})"
            " ORDER BY ba.position"
        )
        cursor.execute(
            query,
            book_ids,
        )
        for r in cursor.fetchall():
            authors_map.setdefault(r["book_id"], []).append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "sort_name": r["sort_name"],
                    "position": r["position"],
                }
            )
    except Exception:
        # Tables may not exist yet (pre-migration)
        pass

    # Batch: narrators for all books in one query (normalized many-to-many)
    narrators_map: dict[int, list[dict]] = {}
    try:
        query = (
            "SELECT bn.book_id, n.id, n.name, n.sort_name, bn.position"  # nosec B608
            " FROM book_narrators bn"
            " JOIN narrators n ON bn.narrator_id = n.id"
            f" WHERE bn.book_id IN ({placeholders})"
            " ORDER BY bn.position"
        )
        cursor.execute(
            query,
            book_ids,
        )
        for r in cursor.fetchall():
            narrators_map.setdefault(r["book_id"], []).append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "sort_name": r["sort_name"],
                    "position": r["position"],
                }
            )
    except Exception:
        # Tables may not exist yet (pre-migration)
        pass

    return {
        "genres": genres_map,
        "eras": eras_map,
        "topics": topics_map,
        "supplements": supplements_map,
        "authors": authors_map,
        "narrators": narrators_map,
    }


def _fetch_titles_by_author(cursor, audiobooks: list[dict]) -> dict[str, list[str]]:
    """Query all titles grouped by author for edition detection."""
    authors = list({book["author"] for book in audiobooks if book["author"]})
    if not authors:
        return {}
    author_placeholders = ",".join("?" * len(authors))
    cursor.execute(
        f"""
        SELECT author, title FROM audiobooks
        WHERE author IN ({author_placeholders})
        """,  # nosec B608
        authors,
    )
    result: dict[str, list[str]] = {}
    for r in cursor.fetchall():
        result.setdefault(r["author"], []).append(r["title"])
    return result


def _count_edition(book_title: str, related_titles: list[str]) -> int:
    """Count editions for a single book based on normalized title matching."""
    base_title = normalize_base_title(book_title)
    matching = [t for t in related_titles if normalize_base_title(t) == base_title]
    if len(matching) > 1 and any(has_edition_marker(t) for t in matching):
        return len(matching)
    return 1


def _detect_editions(cursor, audiobooks: list[dict]) -> None:
    """Detect edition counts for a list of audiobooks.

    Modifies books in-place by adding the 'edition_count' key.
    """
    titles_by_author = _fetch_titles_by_author(cursor, audiobooks)
    for book in audiobooks:
        related = titles_by_author.get(book["author"], [])
        book["edition_count"] = _count_edition(book["title"], related)


@audiobooks_bp.route("/api/audiobooks", methods=["GET"])
@guest_allowed
def get_audiobooks() -> Response:
    """
    Get paginated audiobooks with optional filtering
    Query params:
    - page: Page number (default: 1)
    - per_page: Items per page (default: 50, max: 200)
    - search: Search query (full-text search)
    - author: Filter by author
    - narrator: Filter by narrator
    - publisher: Filter by publisher
    - genre: Filter by genre
    - format: Filter by format (opus, m4b, etc.)
    - collection: Filter by predefined collection (e.g., 'great-courses')
    - sort: Sort field (title, author, duration_hours, created_at)
    - order: Sort order (asc, desc)
    """
    qp = _parse_query_params(request)
    sort_sql, sort_order = _build_sort_clause(qp["sort_field"], qp["sort_order"])
    where_clauses, params = _build_filter_clauses(qp)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    # Count total matching audiobooks
    count_query = f"SELECT COUNT(*) as total FROM audiobooks {where_sql}"  # nosec B608
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()["total"]

    # Get paginated audiobooks
    page = qp["page"]
    per_page = qp["per_page"]
    offset = (page - 1) * per_page

    # CodeQL: sort_sql from allowlist (_SORT_MAPPINGS), sort_order validated
    # where_sql/sort_sql built from validated allowlists, not user input
    query = (
        "SELECT id, title, author, narrator, publisher, series,"  # nosec B608
        " series_sequence, edition, asin, acquired_date, published_year,"
        " author_last_name, author_first_name,"
        " narrator_last_name, narrator_first_name,"
        " duration_hours, duration_formatted, file_size_mb,"
        " file_path, cover_path, format, quality, description"
        " FROM audiobooks"
        f" {where_sql}"
        f" ORDER BY {sort_sql} {sort_order}"
        " LIMIT ? OFFSET ?"
    )

    cursor.execute(query, params + [per_page, offset])
    rows = cursor.fetchall()

    # Convert to list of dicts
    audiobooks = []
    book_ids = []
    for row in rows:
        book = dict(row)
        audiobooks.append(book)
        book_ids.append(book["id"])

    if book_ids:
        # Load all related metadata in batch queries
        metadata = _batch_load_metadata(cursor, book_ids)

        # Assign batch results to each book
        for book in audiobooks:
            bid = book["id"]
            book["genres"] = metadata["genres"].get(bid, [])
            book["eras"] = metadata["eras"].get(bid, [])
            book["topics"] = metadata["topics"].get(bid, [])
            book["supplement_count"] = metadata["supplements"].get(bid, 0)
            book["authors"] = metadata["authors"].get(bid, [])
            book["narrators"] = metadata["narrators"].get(bid, [])

        # Detect edition counts
        _detect_editions(cursor, audiobooks)

    conn.close()

    # Edition sort: filter to only books with multiple editions
    sort_field = qp["sort_field"]
    if sort_field == "edition":
        audiobooks = [b for b in audiobooks if b.get("edition_count", 1) > 1]
        total_count = len(audiobooks)

    # Calculate pagination metadata
    total_pages = max(1, (total_count + per_page - 1) // per_page)

    return jsonify(
        {
            "audiobooks": audiobooks,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }
    )


@audiobooks_bp.route("/api/filters", methods=["GET"])
@guest_allowed
def get_filters() -> Response:
    """Get all available filter options (audiobooks only)"""
    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    # Get unique authors from normalized table (individual names, not composites)
    # Return objects with name + sort_name so frontend can display "Last, First"
    _filter_ab = AUDIOBOOK_FILTER.replace("content_type", "ab.content_type")
    query = (
        "SELECT DISTINCT a.name, a.sort_name FROM authors a"  # nosec B608
        " JOIN book_authors ba ON ba.author_id = a.id"
        " JOIN audiobooks ab ON ab.id = ba.book_id"
        f" WHERE {_filter_ab}"
        " ORDER BY a.sort_name COLLATE NOCASE"
    )
    cursor.execute(query)
    authors = [
        {"name": row["name"], "sort_name": row["sort_name"]}
        for row in cursor.fetchall()
    ]

    # Get unique narrators from normalized table
    query = (
        "SELECT DISTINCT n.name FROM narrators n"  # nosec B608
        " JOIN book_narrators bn ON bn.narrator_id = n.id"
        " JOIN audiobooks ab ON ab.id = bn.book_id"
        f" WHERE {_filter_ab}"
        " ORDER BY n.sort_name COLLATE NOCASE"
    )
    cursor.execute(query)
    narrators = [row["name"] for row in cursor.fetchall()]

    # Get unique publishers (audiobooks only)
    cursor.execute(f"""
        SELECT DISTINCT publisher FROM audiobooks
        WHERE {AUDIOBOOK_FILTER} AND publisher IS NOT NULL
        ORDER BY publisher COLLATE NOCASE
    """)  # nosec B608
    publishers = [row["publisher"] for row in cursor.fetchall()]

    # Get genres
    cursor.execute("SELECT name FROM genres ORDER BY name COLLATE NOCASE")
    genres = [row["name"] for row in cursor.fetchall()]

    # Get eras
    cursor.execute("SELECT name FROM eras ORDER BY name COLLATE NOCASE")
    eras = [row["name"] for row in cursor.fetchall()]

    # Get topics
    cursor.execute("SELECT name FROM topics ORDER BY name COLLATE NOCASE")
    topics = [row["name"] for row in cursor.fetchall()]

    # Get formats (audiobooks only)
    cursor.execute(f"""
        SELECT DISTINCT format FROM audiobooks
        WHERE {AUDIOBOOK_FILTER} AND format IS NOT NULL
        ORDER BY format COLLATE NOCASE
    """)  # nosec B608
    formats = [row["format"] for row in cursor.fetchall()]

    conn.close()

    return jsonify(
        {
            "authors": authors,
            "narrators": narrators,
            "publishers": publishers,
            "genres": genres,
            "eras": eras,
            "topics": topics,
            "formats": formats,
        }
    )


@audiobooks_bp.route("/api/narrator-counts", methods=["GET"])
@guest_allowed
def get_narrator_counts() -> Response:
    """Get narrator book counts for autocomplete (audiobooks only)"""
    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    _filter_ab = AUDIOBOOK_FILTER.replace("content_type", "ab.content_type")
    query = (
        "SELECT n.name as narrator, COUNT(DISTINCT bn.book_id) as count"  # nosec B608
        " FROM narrators n"
        " JOIN book_narrators bn ON bn.narrator_id = n.id"
        " JOIN audiobooks ab ON ab.id = bn.book_id"
        f" WHERE {_filter_ab}"
        " GROUP BY n.id, n.name"
        " ORDER BY n.sort_name COLLATE NOCASE"
    )
    cursor.execute(query)

    counts = {row["narrator"]: row["count"] for row in cursor.fetchall()}
    conn.close()

    return jsonify(counts)


@audiobooks_bp.route("/api/audiobooks/<int:audiobook_id>", methods=["GET"])
@guest_allowed
def get_audiobook(audiobook_id: int) -> FlaskResponse:
    """Get single audiobook details"""
    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM audiobooks WHERE id = ?
    """,
        (audiobook_id,),
    )

    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Audiobook not found"}), 404

    book = dict(row)

    # Get related data
    cursor.execute(
        """
        SELECT g.name FROM genres g
        JOIN audiobook_genres ag ON g.id = ag.genre_id
        WHERE ag.audiobook_id = ?
    """,
        (audiobook_id,),
    )
    book["genres"] = [r["name"] for r in cursor.fetchall()]

    cursor.execute(
        """
        SELECT e.name FROM eras e
        JOIN audiobook_eras ae ON e.id = ae.era_id
        WHERE ae.audiobook_id = ?
    """,
        (audiobook_id,),
    )
    book["eras"] = [r["name"] for r in cursor.fetchall()]

    cursor.execute(
        """
        SELECT t.name FROM topics t
        JOIN audiobook_topics at ON t.id = at.topic_id
        WHERE at.audiobook_id = ?
    """,
        (audiobook_id,),
    )
    book["topics"] = [r["name"] for r in cursor.fetchall()]

    conn.close()

    return jsonify(book)


@audiobooks_bp.route("/covers/<path:filename>")
@guest_allowed
def serve_cover(filename: str) -> Response:
    """Serve cover images from configured COVER_DIR"""
    return send_from_directory(COVER_DIR, filename)


def _remux_to_webm(source: Path, webm_path: Path, audiobook_id: int) -> str | None:
    """Remux an Opus/Ogg file to WebM container. Returns error message or None."""
    AUDIOBOOKS_WEBM_CACHE.mkdir(parents=True, exist_ok=True)
    tmp_path = webm_path.with_suffix(".webm.tmp")
    try:
        result = subprocess.run(  # nosec B603
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source),
                "-c:a",
                "copy",
                "-f",
                "webm",
                str(tmp_path),
            ],
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            stderr = result.stderr or b""
            if isinstance(stderr, bytes):
                safe_err = (
                    stderr[:500].replace(b"\n", b" ").decode("utf-8", errors="replace")
                )
            else:
                safe_err = str(stderr)[:500].replace("\n", " ")
            # Sanitize control chars to prevent log injection (CodeQL py/log-injection)
            safe_err = "".join(
                c if c.isprintable() or c == " " else "?" for c in safe_err
            )
            logger.error("WebM remux failed for %d: %s", audiobook_id, safe_err)
            tmp_path.unlink(missing_ok=True)
            return "Format conversion failed"
        tmp_path.rename(webm_path)
        return None
    except (subprocess.TimeoutExpired, OSError) as e:
        safe_msg = str(e).replace("\n", " ")
        # Sanitize control chars to prevent log injection (CodeQL py/log-injection)
        safe_msg = "".join(c if c.isprintable() or c == " " else "?" for c in safe_msg)
        logger.error("WebM remux error for %d: %s", audiobook_id, safe_msg)
        tmp_path.unlink(missing_ok=True)
        return "Format conversion failed"


def _serve_webm(file_path: Path, audiobook_id: int) -> FlaskResponse:
    """Serve an Opus file remuxed to WebM container for Safari/iOS."""
    webm_path = AUDIOBOOKS_WEBM_CACHE / f"{audiobook_id}.webm"

    cache_stale = (
        not webm_path.exists() or webm_path.stat().st_mtime < file_path.stat().st_mtime
    )
    if cache_stale:
        error = _remux_to_webm(file_path, webm_path, audiobook_id)
        if error:
            return jsonify({"error": error}), 500

    return send_file(
        webm_path, mimetype="audio/webm", as_attachment=False, conditional=True
    )


@audiobooks_bp.route("/api/stream/<int:audiobook_id>")
@auth_if_enabled
def stream_audiobook(audiobook_id: int) -> FlaskResponse:
    """Stream audiobook file.

    Supports ?format=webm for Safari/iOS compatibility. Opus files are
    natively in Ogg containers (audio/ogg) which Safari cannot play.
    When format=webm is requested, the file is remuxed to WebM container
    (same Opus codec, no re-encoding) and cached for subsequent requests.
    """
    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT file_path, format FROM audiobooks WHERE id = ?", (audiobook_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Audiobook not found"}), 404

    file_path = Path(row["file_path"])
    if not file_path.exists():
        return jsonify({"error": "File not found on disk"}), 404

    file_format = row["format"] or file_path.suffix.lower().lstrip(".")
    requested_format = request.args.get("format", "")

    # Safari/iOS: remux Opus from Ogg to WebM container (codec copy, no quality loss)
    if requested_format == "webm" and file_format == "opus":
        return _serve_webm(file_path, audiobook_id)

    # Default: serve the original file
    mime_types = {
        "opus": "audio/ogg",
        "m4b": "audio/mp4",
        "m4a": "audio/mp4",
        "mp3": "audio/mpeg",
    }
    mimetype = mime_types.get(file_format, "application/octet-stream")

    return send_file(
        file_path,
        mimetype=mimetype,
        as_attachment=False,
        conditional=True,
    )


@audiobooks_bp.route("/api/download/<int:audiobook_id>")
@download_permission_required
def download_audiobook(audiobook_id: int) -> FlaskResponse:
    """Download audiobook file for offline listening.

    Requires download permission. The file is returned as an attachment
    with a filename based on the audiobook title.
    """
    conn = _get_audiobooks_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT title, author, file_path, format FROM audiobooks WHERE id = ?",
        (audiobook_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Audiobook not found"}), 404

    file_path = Path(row["file_path"])
    if not file_path.exists():
        return jsonify({"error": "File not found on disk"}), 404

    # Build a clean filename from title and author
    title = row["title"] or "audiobook"
    author = row["author"]
    file_format = row["format"] or file_path.suffix.lower().lstrip(".")

    # Sanitize filename: remove/replace problematic characters
    def sanitize(s: str) -> str:
        # Replace characters that are problematic in filenames
        for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
            s = s.replace(char, "-")
        return s.strip()

    if author:
        download_name = f"{sanitize(title)} - {sanitize(author)}.{file_format}"
    else:
        download_name = f"{sanitize(title)}.{file_format}"

    # Map file formats to MIME types
    mime_types = {
        "opus": "audio/ogg",
        "m4b": "audio/mp4",
        "m4a": "audio/mp4",
        "mp3": "audio/mpeg",
    }
    mimetype = mime_types.get(file_format, "application/octet-stream")

    return send_file(
        file_path,
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name,
    )


@audiobooks_bp.route("/health")
def health() -> Response:
    """Health check endpoint with version info"""
    version = "unknown"
    project_dir = current_app.config.get("PROJECT_DIR")
    if project_dir is not None:
        version_file = Path(project_dir) / "VERSION"
        if version_file.exists():
            version = version_file.read_text().strip()
    db_path = current_app.config.get("DATABASE_PATH")
    db_exists = str(Path(db_path).exists()) if db_path is not None else "false"
    return jsonify({"status": "ok", "database": db_exists, "version": version})
