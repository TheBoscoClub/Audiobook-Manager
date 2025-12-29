#!/usr/bin/env python3
"""
Audiobook Library API - Flask Backend
Provides fast, paginated API for audiobook queries
"""

from flask import Flask, Response, jsonify, request, send_from_directory, send_file
import sqlite3
from pathlib import Path
import os
import sys
from typing import Union

# Type alias for Flask route return types
FlaskResponse = Union[Response, tuple[Response, int], tuple[str, int]]

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH, API_PORT, PROJECT_DIR, SUPPLEMENTS_DIR  # noqa: E402

app = Flask(__name__)


# =============================================================================
# CORS Implementation (replaces flask-cors to eliminate CVE vulnerabilities)
# =============================================================================


@app.after_request
def add_cors_headers(response: Response) -> Response:
    """
    Add CORS headers to all responses.
    This is a simple implementation suitable for localhost/personal use.
    Replaces flask-cors which has multiple CVEs (CVE-2024-6221, etc.)
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
    response.headers["Access-Control-Expose-Headers"] = (
        "Content-Range, Accept-Ranges, Content-Length"
    )
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def handle_options(path: str) -> tuple[str, int]:
    """Handle CORS preflight requests"""
    return "", 204


DB_PATH = DATABASE_PATH
PROJECT_ROOT = PROJECT_DIR / "library"


def get_db() -> sqlite3.Connection:
    """Get database connection"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    return conn


# ============================================
# COLLECTIONS - Predefined groups of audiobooks
# ============================================


# Helper to create genre-based query (uses junction table)
def _escape_like_pattern(pattern: str) -> str:
    """Escape special characters in LIKE patterns to prevent SQL injection."""
    # Escape SQL special characters for LIKE: % _ ' \
    pattern = pattern.replace("'", "''")  # Escape single quotes
    return pattern


def genre_query(genre_pattern: str) -> str:
    """Create a query for books matching a genre pattern."""
    safe_pattern = _escape_like_pattern(genre_pattern)
    return f"""id IN (
        SELECT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE g.name LIKE '{safe_pattern}'
    )"""


def multi_genre_query(genre_patterns: list[str]) -> str:
    """Create a query for books matching any of the genre patterns."""
    conditions = " OR ".join(
        [f"g.name LIKE '{_escape_like_pattern(p)}'" for p in genre_patterns]
    )
    return f"""id IN (
        SELECT DISTINCT ag.audiobook_id FROM audiobook_genres ag
        JOIN genres g ON ag.genre_id = g.id
        WHERE {conditions}
    )"""


COLLECTIONS = {
    # === SPECIAL COLLECTIONS ===
    "great-courses": {
        "name": "The Great Courses",
        "description": "Educational lecture series from The Teaching Company",
        "query": "author LIKE '%The Great Courses%'",
        "icon": "🎓",
        "category": "special",
    },
    # === MAIN GENRES ===
    "fiction": {
        "name": "Fiction",
        "description": "Literary fiction, genre fiction, and novels",
        "query": multi_genre_query(
            ["Literature & Fiction", "Literary Fiction", "Genre Fiction"]
        ),
        "icon": "📖",
        "category": "main",
    },
    "mystery-thriller": {
        "name": "Mystery & Thriller",
        "description": "Mystery, suspense, and thriller novels",
        "query": multi_genre_query(
            [
                "Mystery",
                "Thriller & Suspense",
                "Suspense",
                "Crime Fiction",
                "Crime Thrillers",
            ]
        ),
        "icon": "🔍",
        "category": "main",
    },
    "scifi-fantasy": {
        "name": "Sci-Fi & Fantasy",
        "description": "Science fiction and fantasy",
        "query": multi_genre_query(
            ["Science Fiction & Fantasy", "Science Fiction", "Fantasy"]
        ),
        "icon": "🚀",
        "category": "main",
    },
    "horror": {
        "name": "Horror",
        "description": "Horror and supernatural fiction",
        "query": multi_genre_query(
            ["Horror", "Ghosts", "Paranormal & Urban", "Occult"]
        ),
        "icon": "👻",
        "category": "main",
    },
    "classics": {
        "name": "Classics",
        "description": "Classic literature and timeless stories",
        "query": genre_query("Classics"),
        "icon": "📜",
        "category": "main",
    },
    "comedy": {
        "name": "Comedy & Humor",
        "description": "Funny books and comedy",
        "query": genre_query("Comedy & Humor"),
        "icon": "😂",
        "category": "main",
    },
    # === NONFICTION ===
    "biography-memoir": {
        "name": "Biography & Memoir",
        "description": "Biographies, autobiographies, and memoirs",
        "query": multi_genre_query(
            ["Biographies & Memoirs", "Memoirs", "Biographical Fiction"]
        ),
        "icon": "👤",
        "category": "nonfiction",
    },
    "history": {
        "name": "History",
        "description": "Historical nonfiction and world history",
        "query": multi_genre_query(["History", "Historical", "World"]),
        "icon": "🏛️",
        "category": "nonfiction",
    },
    "science": {
        "name": "Science & Technology",
        "description": "Science, technology, and nature",
        "query": multi_genre_query(
            [
                "Science",
                "Science & Engineering",
                "Biological Sciences",
                "Technothrillers",
            ]
        ),
        "icon": "🔬",
        "category": "nonfiction",
    },
    "health-wellness": {
        "name": "Health & Wellness",
        "description": "Health, psychology, and self-improvement",
        "query": multi_genre_query(
            ["Health & Wellness", "Psychology", "Self-Help", "Personal Development"]
        ),
        "icon": "🧘",
        "category": "nonfiction",
    },
    # === SUBGENRES ===
    "historical-fiction": {
        "name": "Historical Fiction",
        "description": "Fiction set in historical periods",
        "query": genre_query("Historical Fiction"),
        "icon": "⚔️",
        "category": "subgenre",
    },
    "action-adventure": {
        "name": "Action & Adventure",
        "description": "Action-packed adventure stories",
        "query": multi_genre_query(["Action & Adventure", "Adventure"]),
        "icon": "🗺️",
        "category": "subgenre",
    },
    "anthologies": {
        "name": "Short Stories",
        "description": "Anthologies and short story collections",
        "query": genre_query("Anthologies & Short Stories"),
        "icon": "📚",
        "category": "subgenre",
    },
}


# ============================================
# EDITION DETECTION HELPERS
# ============================================


def has_edition_marker(title: str | None) -> bool:
    """Check if a title contains edition markers indicating it's a specific edition"""
    if not title:
        return False

    title_lower = title.lower()

    # Edition keywords
    edition_markers = [
        "edition",  # 2nd edition, revised edition, etc.
        "anniversary",  # 20th anniversary, etc.
        "revised",  # revised version
        "updated",  # updated version
        "unabridged",  # unabridged vs abridged
        "abridged",
        "complete",  # complete edition
        "expanded",  # expanded edition
        "deluxe",  # deluxe edition
        "special",  # special edition
        "collectors",  # collector's edition
        "annotated",  # annotated edition
        "illustrated",  # illustrated edition
    ]

    return any(marker in title_lower for marker in edition_markers)


def normalize_base_title(title: str | None) -> str:
    """
    Normalize title by removing edition markers and common suffixes.
    This creates a base title for matching different editions.
    """
    import re

    if not title:
        return ""

    base = title.lower().strip()

    # Remove edition markers and surrounding text
    base = re.sub(r"\s*\([^)]*edition[^)]*\)", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\s*\([^)]*anniversary[^)]*\)", "", base, flags=re.IGNORECASE)
    base = re.sub(
        r"\s*-\s*\d+(st|nd|rd|th)\s+edition.*$", "", base, flags=re.IGNORECASE
    )
    base = re.sub(
        r"\s*:\s*(unabridged|abridged|complete|expanded).*$",
        "",
        base,
        flags=re.IGNORECASE,
    )

    # Remove "(Unabridged)" or similar at the end
    base = re.sub(r"\s*\((un)?abridged\)", "", base, flags=re.IGNORECASE)

    # Remove year in parentheses at the end like "(2024)"
    base = re.sub(r"\s*\(\d{4}\)\s*$", "", base)

    # Normalize punctuation
    base = base.replace(":", "").replace("-", " ").replace("  ", " ")

    return base.strip()


@app.route("/api/stats", methods=["GET"])
def get_stats() -> Response:
    """Get library statistics"""
    conn = get_db()
    cursor = conn.cursor()

    # Total audiobooks
    cursor.execute("SELECT COUNT(*) as total FROM audiobooks")
    total_books = cursor.fetchone()["total"]

    # Total hours
    cursor.execute("SELECT SUM(duration_hours) as total_hours FROM audiobooks")
    total_hours = cursor.fetchone()["total_hours"] or 0

    # Total storage used (sum of file sizes in MB, convert to GB)
    cursor.execute("SELECT SUM(file_size_mb) as total_size FROM audiobooks")
    total_size_mb = cursor.fetchone()["total_size"] or 0
    total_size_gb = total_size_mb / 1024

    # Unique counts (excluding placeholder values like "Audiobook" and "Unknown")
    cursor.execute("""
        SELECT COUNT(DISTINCT author) as count FROM audiobooks
        WHERE author IS NOT NULL
          AND LOWER(TRIM(author)) != 'audiobook'
          AND LOWER(TRIM(author)) != 'unknown author'
    """)
    unique_authors = cursor.fetchone()["count"]

    cursor.execute("""
        SELECT COUNT(DISTINCT narrator) as count FROM audiobooks
        WHERE narrator IS NOT NULL
          AND LOWER(TRIM(narrator)) != 'unknown narrator'
          AND LOWER(TRIM(narrator)) != ''
    """)
    unique_narrators = cursor.fetchone()["count"]

    cursor.execute(
        "SELECT COUNT(DISTINCT publisher) as count FROM audiobooks WHERE publisher IS NOT NULL"
    )
    unique_publishers = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM genres")
    unique_genres = cursor.fetchone()["count"]

    conn.close()

    # Get database file size
    database_size_mb = 0
    try:
        import os

        db_path = str(DATABASE_PATH)
        if os.path.exists(db_path):
            database_size_mb = os.path.getsize(db_path) / (1024 * 1024)
    except Exception:
        pass

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


@app.route("/api/audiobooks", methods=["GET"])
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
    # Parse parameters
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(200, max(1, int(request.args.get("per_page", 50))))
    search = request.args.get("search", "").strip()
    author = request.args.get("author", "").strip()
    narrator = request.args.get("narrator", "").strip()
    publisher = request.args.get("publisher", "").strip()
    genre = request.args.get("genre", "").strip()
    format_filter = request.args.get("format", "").strip()
    collection = request.args.get("collection", "").strip()
    sort_field = request.args.get("sort", "title")
    sort_order = request.args.get("order", "asc").lower()

    # Map user-friendly sort names to SQL expressions
    sort_mappings = {
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
        "series": "series, series_sequence",  # Sort by series name, then sequence
        "asin": "asin",
        "edition": "edition",
    }

    # Get SQL sort expression
    if sort_field in sort_mappings:
        sort_sql = sort_mappings[sort_field]
    else:
        sort_sql = "title"
        sort_field = "title"

    # Validate sort order
    if sort_order not in ["asc", "desc"]:
        sort_order = "asc"

    conn = get_db()
    cursor = conn.cursor()

    # Build query
    where_clauses = []
    params = []

    if search:
        # Full-text search
        where_clauses.append(
            "id IN (SELECT rowid FROM audiobooks_fts WHERE audiobooks_fts MATCH ?)"
        )
        params.append(search)

    if author:
        where_clauses.append("author LIKE ?")
        params.append(f"%{author}%")

    if narrator:
        where_clauses.append("narrator LIKE ?")
        params.append(f"%{narrator}%")

    if publisher:
        where_clauses.append("publisher LIKE ?")
        params.append(f"%{publisher}%")

    if format_filter:
        where_clauses.append("format = ?")
        params.append(format_filter.lower())

    if genre:
        where_clauses.append("""
            id IN (
                SELECT audiobook_id FROM audiobook_genres ag
                JOIN genres g ON ag.genre_id = g.id
                WHERE g.name LIKE ?
            )
        """)
        params.append(f"%{genre}%")

    # Collection filter (predefined query from COLLECTIONS)
    if collection and collection in COLLECTIONS:
        where_clauses.append(f"({COLLECTIONS[collection]['query']})")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    # Count total matching audiobooks
    count_query = f"SELECT COUNT(*) as total FROM audiobooks {where_sql}"
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()["total"]

    # Get paginated audiobooks
    offset = (page - 1) * per_page

    query = f"""
        SELECT
            id, title, author, narrator, publisher, series,
            series_sequence, edition, asin, acquired_date, published_year,
            author_last_name, author_first_name,
            narrator_last_name, narrator_first_name,
            duration_hours, duration_formatted, file_size_mb,
            file_path, cover_path, format, quality, description
        FROM audiobooks
        {where_sql}
        ORDER BY {sort_sql} {sort_order}
        LIMIT ? OFFSET ?
    """

    cursor.execute(query, params + [per_page, offset])
    rows = cursor.fetchall()

    # Convert to list of dicts
    audiobooks = []
    for row in rows:
        book = dict(row)

        # Get genres, eras, topics
        cursor.execute(
            """
            SELECT g.name FROM genres g
            JOIN audiobook_genres ag ON g.id = ag.genre_id
            WHERE ag.audiobook_id = ?
        """,
            (book["id"],),
        )
        book["genres"] = [r["name"] for r in cursor.fetchall()]

        cursor.execute(
            """
            SELECT e.name FROM eras e
            JOIN audiobook_eras ae ON e.id = ae.era_id
            WHERE ae.audiobook_id = ?
        """,
            (book["id"],),
        )
        book["eras"] = [r["name"] for r in cursor.fetchall()]

        cursor.execute(
            """
            SELECT t.name FROM topics t
            JOIN audiobook_topics at ON t.id = at.topic_id
            WHERE at.audiobook_id = ?
        """,
            (book["id"],),
        )
        book["topics"] = [r["name"] for r in cursor.fetchall()]

        # Get supplement count for this audiobook
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM supplements
            WHERE audiobook_id = ?
        """,
            (book["id"],),
        )
        result = cursor.fetchone()
        book["supplement_count"] = result["count"] if result else 0

        # Get edition count (only count if book has edition markers)
        # First check if this book or any related books have edition markers
        base_title = normalize_base_title(book["title"])

        # Find books with same author
        cursor.execute(
            """
            SELECT title
            FROM audiobooks
            WHERE author = ?
        """,
            (book["author"],),
        )

        related_books = cursor.fetchall()
        matching_editions = []

        for related in related_books:
            related_base = normalize_base_title(related["title"])
            if related_base == base_title:
                matching_editions.append(related["title"])

        # Only set edition_count > 1 if there are multiple matches AND at least one has markers
        has_markers = any(has_edition_marker(title) for title in matching_editions)
        if len(matching_editions) > 1 and has_markers:
            book["edition_count"] = len(matching_editions)
        else:
            book["edition_count"] = 1

        audiobooks.append(book)

    conn.close()

    # Calculate pagination metadata
    total_pages = (total_count + per_page - 1) // per_page

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


@app.route("/api/filters", methods=["GET"])
def get_filters() -> Response:
    """Get all available filter options"""
    conn = get_db()
    cursor = conn.cursor()

    # Get unique authors
    cursor.execute("""
        SELECT DISTINCT author FROM audiobooks
        WHERE author IS NOT NULL
        ORDER BY author
    """)
    authors = [row["author"] for row in cursor.fetchall()]

    # Get unique narrators
    cursor.execute("""
        SELECT DISTINCT narrator FROM audiobooks
        WHERE narrator IS NOT NULL
        ORDER BY narrator
    """)
    narrators = [row["narrator"] for row in cursor.fetchall()]

    # Get unique publishers
    cursor.execute("""
        SELECT DISTINCT publisher FROM audiobooks
        WHERE publisher IS NOT NULL
        ORDER BY publisher
    """)
    publishers = [row["publisher"] for row in cursor.fetchall()]

    # Get genres
    cursor.execute("SELECT name FROM genres ORDER BY name")
    genres = [row["name"] for row in cursor.fetchall()]

    # Get eras
    cursor.execute("SELECT name FROM eras ORDER BY name")
    eras = [row["name"] for row in cursor.fetchall()]

    # Get topics
    cursor.execute("SELECT name FROM topics ORDER BY name")
    topics = [row["name"] for row in cursor.fetchall()]

    # Get formats
    cursor.execute("""
        SELECT DISTINCT format FROM audiobooks
        WHERE format IS NOT NULL
        ORDER BY format
    """)
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


@app.route("/api/narrator-counts", methods=["GET"])
def get_narrator_counts() -> Response:
    """Get narrator book counts for autocomplete"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT narrator, COUNT(*) as count
        FROM audiobooks
        WHERE narrator IS NOT NULL
          AND narrator != ''
          AND narrator != 'Unknown Narrator'
        GROUP BY narrator
        ORDER BY narrator
    """)

    counts = {row["narrator"]: row["count"] for row in cursor.fetchall()}
    conn.close()

    return jsonify(counts)


@app.route("/api/collections", methods=["GET"])
def get_collections() -> Response:
    """Get available collections with counts, grouped by category"""
    conn = get_db()
    cursor = conn.cursor()

    # Define category order and labels
    category_order = ["special", "main", "nonfiction", "subgenre"]
    category_labels = {
        "special": "Special Collections",
        "main": "Main Genres",
        "nonfiction": "Nonfiction",
        "subgenre": "Subgenres",
    }

    result = []
    for collection_id, collection in COLLECTIONS.items():
        # Get count for this collection
        cursor.execute(
            f"SELECT COUNT(*) as count FROM audiobooks WHERE {collection['query']}"
        )
        count = cursor.fetchone()["count"]

        result.append(
            {
                "id": collection_id,
                "name": collection["name"],
                "description": collection["description"],
                "icon": collection["icon"],
                "count": count,
                "category": collection.get("category", "main"),
                "category_label": category_labels.get(
                    collection.get("category", "main"), "Other"
                ),
            }
        )

    # Sort by category order, then by name within category
    def sort_key(item):
        cat_idx = (
            category_order.index(item["category"])
            if item["category"] in category_order
            else 99
        )
        return (cat_idx, item["name"])

    result.sort(key=sort_key)

    conn.close()
    return jsonify(result)


@app.route("/api/audiobooks/<int:audiobook_id>", methods=["GET"])
def get_audiobook(audiobook_id: int) -> FlaskResponse:
    """Get single audiobook details"""
    conn = get_db()
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


@app.route("/covers/<path:filename>")
def serve_cover(filename: str) -> Response:
    """Serve cover images"""
    covers_dir = PROJECT_ROOT / "web" / "covers"
    return send_from_directory(covers_dir, filename)


@app.route("/api/stream/<int:audiobook_id>")
def stream_audiobook(audiobook_id: int) -> FlaskResponse:
    """Stream audiobook file"""
    conn = get_db()
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

    # Map file formats to MIME types
    mime_types = {
        "opus": "audio/ogg",
        "m4b": "audio/mp4",
        "m4a": "audio/mp4",
        "mp3": "audio/mpeg",
    }

    file_format = row["format"] or file_path.suffix.lower().lstrip(".")
    mimetype = mime_types.get(file_format, "application/octet-stream")

    # Use send_file directly for better handling of special characters in paths
    return send_file(
        file_path,
        mimetype=mimetype,
        as_attachment=False,
        conditional=True,  # Enable range requests for seeking
    )


@app.route("/health")
def health() -> Response:
    """Health check endpoint"""
    return jsonify({"status": "ok", "database": str(DB_PATH.exists())})


# ============================================
# DUPLICATE DETECTION ENDPOINTS
# ============================================


@app.route("/api/hash-stats", methods=["GET"])
def get_hash_stats() -> Response:
    """Get hash generation statistics"""
    conn = get_db()
    cursor = conn.cursor()

    # Check if sha256_hash column exists
    cursor.execute("PRAGMA table_info(audiobooks)")
    columns = [row["name"] for row in cursor.fetchall()]

    if "sha256_hash" not in columns:
        conn.close()
        return jsonify(
            {
                "hash_column_exists": False,
                "total_audiobooks": 0,
                "hashed_count": 0,
                "unhashed_count": 0,
                "duplicate_groups": 0,
            }
        )

    cursor.execute("SELECT COUNT(*) as total FROM audiobooks")
    total = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash IS NOT NULL"
    )
    hashed = cursor.fetchone()["count"]

    cursor.execute("""
        SELECT COUNT(*) as count FROM (
            SELECT sha256_hash FROM audiobooks
            WHERE sha256_hash IS NOT NULL
            GROUP BY sha256_hash
            HAVING COUNT(*) > 1
        )
    """)
    duplicate_groups = cursor.fetchone()["count"]

    conn.close()

    return jsonify(
        {
            "hash_column_exists": True,
            "total_audiobooks": total,
            "hashed_count": hashed,
            "unhashed_count": total - hashed,
            "hashed_percentage": round(hashed * 100 / total, 1) if total > 0 else 0,
            "duplicate_groups": duplicate_groups,
        }
    )


@app.route("/api/duplicates", methods=["GET"])
def get_duplicates() -> FlaskResponse:
    """Get all duplicate audiobook groups"""
    conn = get_db()
    cursor = conn.cursor()

    # Check if sha256_hash column exists
    cursor.execute("PRAGMA table_info(audiobooks)")
    columns = [row["name"] for row in cursor.fetchall()]

    if "sha256_hash" not in columns:
        conn.close()
        return jsonify(
            {"error": "Hash column not found. Run hash generation first."}
        ), 400

    # Get all duplicate groups
    cursor.execute("""
        SELECT sha256_hash, COUNT(*) as count
        FROM audiobooks
        WHERE sha256_hash IS NOT NULL
        GROUP BY sha256_hash
        HAVING count > 1
        ORDER BY count DESC
    """)
    groups = cursor.fetchall()

    duplicate_groups = []
    total_wasted_space = 0

    for group in groups:
        hash_val = group["sha256_hash"]
        count = group["count"]

        # Get all files in this group
        cursor.execute(
            """
            SELECT id, title, author, narrator, file_path, file_size_mb,
                   format, duration_formatted, cover_path
            FROM audiobooks
            WHERE sha256_hash = ?
            ORDER BY id ASC
        """,
            (hash_val,),
        )

        files = [dict(row) for row in cursor.fetchall()]

        # First file (by ID) is the "keeper"
        for i, f in enumerate(files):
            f["is_keeper"] = i == 0
            f["is_duplicate"] = i > 0

        file_size = files[0]["file_size_mb"] if files else 0
        wasted = file_size * (count - 1)
        total_wasted_space += wasted

        duplicate_groups.append(
            {
                "hash": hash_val,
                "count": count,
                "file_size_mb": file_size,
                "wasted_mb": round(wasted, 2),
                "files": files,
            }
        )

    conn.close()

    return jsonify(
        {
            "duplicate_groups": duplicate_groups,
            "total_groups": len(duplicate_groups),
            "total_wasted_mb": round(total_wasted_space, 2),
            "total_duplicate_files": sum(g["count"] - 1 for g in duplicate_groups),
        }
    )


@app.route("/api/duplicates/by-title", methods=["GET"])
def get_duplicates_by_title() -> Response:
    """
    Get duplicate audiobooks based on normalized title and REAL author.
    This finds "same book, different version/format" entries.

    IMPROVED LOGIC:
    - Excludes "Audiobook" as a valid author for grouping
    - Groups by title + real author + similar duration (within 10%)
    - Prevents flagging different books with same title as duplicates
    """
    conn = get_db()
    cursor = conn.cursor()

    # Find duplicates by normalized title + real author (excluding "Audiobook")
    # Also require similar duration to avoid grouping different books
    cursor.execute("""
        SELECT
            LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) as norm_title,
            LOWER(TRIM(author)) as norm_author,
            ROUND(duration_hours, 1) as duration_group,
            COUNT(*) as count
        FROM audiobooks
        WHERE title IS NOT NULL
          AND author IS NOT NULL
          AND LOWER(TRIM(author)) != 'audiobook'
          AND LOWER(TRIM(author)) != 'unknown author'
        GROUP BY norm_title, norm_author, duration_group
        HAVING count > 1
        ORDER BY count DESC, norm_title
    """)
    groups = cursor.fetchall()

    duplicate_groups = []
    total_potential_savings = 0

    for group in groups:
        norm_title = group["norm_title"]
        norm_author = group["norm_author"]
        duration_group = group["duration_group"]

        # Get all files in this group (including any with "Audiobook" author that match)
        cursor.execute(
            """
            SELECT id, title, author, narrator, file_path, file_size_mb,
                   format, duration_formatted, duration_hours, cover_path, sha256_hash
            FROM audiobooks
            WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) = ?
              AND (LOWER(TRIM(author)) = ? OR LOWER(TRIM(author)) = 'audiobook')
              AND ROUND(duration_hours, 1) = ?
            ORDER BY
                -- Prefer entries with real author over "Audiobook"
                CASE WHEN LOWER(TRIM(author)) = 'audiobook' THEN 1 ELSE 0 END,
                CASE format
                    WHEN 'opus' THEN 1
                    WHEN 'm4b' THEN 2
                    WHEN 'm4a' THEN 3
                    WHEN 'mp3' THEN 4
                    ELSE 5
                END,
                file_size_mb DESC,
                id ASC
        """,
            (norm_title, norm_author, duration_group),
        )

        files = [dict(row) for row in cursor.fetchall()]

        if len(files) < 2:
            continue

        # First file (with real author, preferred format) is the "keeper"
        for i, f in enumerate(files):
            f["is_keeper"] = i == 0
            f["is_duplicate"] = i > 0

        # Calculate potential savings (sum of all but the largest file)
        sizes = sorted([f["file_size_mb"] for f in files], reverse=True)
        potential_savings = sum(sizes[1:])  # All except the largest
        total_potential_savings += potential_savings

        # Use the real author (first file has real author due to ORDER BY)
        display_author = files[0]["author"]
        if display_author.lower() == "audiobook":
            # Fallback: find real author from the group
            for f in files:
                if f["author"].lower() != "audiobook":
                    display_author = f["author"]
                    break

        duplicate_groups.append(
            {
                "title": files[0]["title"],
                "author": display_author,
                "count": len(files),
                "potential_savings_mb": round(potential_savings, 2),
                "files": files,
            }
        )

    conn.close()

    return jsonify(
        {
            "duplicate_groups": duplicate_groups,
            "total_groups": len(duplicate_groups),
            "total_potential_savings_mb": round(total_potential_savings, 2),
            "total_duplicate_files": sum(g["count"] - 1 for g in duplicate_groups),
        }
    )


@app.route("/api/audiobooks/<int:book_id>/editions", methods=["GET"])
def get_book_editions(book_id: int) -> FlaskResponse:
    """
    Get all editions of a specific audiobook.
    Only returns books that are truly different editions (with edition markers in title).
    """
    conn = get_db()
    cursor = conn.cursor()

    # First, get the book to find its title and author
    cursor.execute(
        """
        SELECT title, author FROM audiobooks WHERE id = ?
    """,
        (book_id,),
    )
    result = cursor.fetchone()

    if not result:
        conn.close()
        return jsonify({"error": "Book not found"}), 404

    title = result["title"]
    author = result["author"]

    # Check if this book or related books have edition markers
    # Get the base title for matching
    base_title = normalize_base_title(title)

    # Find all books with similar base title + same author
    cursor.execute(
        """
        SELECT
            id, title, author, narrator, publisher, series,
            duration_hours, duration_formatted, file_size_mb,
            file_path, cover_path, format, quality, description
        FROM audiobooks
        WHERE author = ?
        ORDER BY title ASC, id ASC
    """,
        (author,),
    )

    # Filter to books with matching base title
    all_books = cursor.fetchall()
    editions = []

    for row in all_books:
        book_title = row["title"]
        book_base = normalize_base_title(book_title)

        # Match if base titles are similar
        if book_base == base_title:
            editions.append(dict(row))

    # Only return if multiple editions exist OR if any title has edition markers
    has_markers = any(has_edition_marker(ed["title"]) for ed in editions)

    if len(editions) <= 1 and not has_markers:
        # Not a true multi-edition book
        editions = [dict(row) for row in all_books if row["id"] == book_id]

    # Get additional metadata for each edition
    final_editions = []
    for edition in editions:
        # Get genres
        cursor.execute(
            """
            SELECT g.name FROM genres g
            JOIN audiobook_genres ag ON g.id = ag.genre_id
            WHERE ag.audiobook_id = ?
        """,
            (edition["id"],),
        )
        edition["genres"] = [r["name"] for r in cursor.fetchall()]

        # Get supplement count
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM supplements
            WHERE audiobook_id = ?
        """,
            (edition["id"],),
        )
        result = cursor.fetchone()
        edition["supplement_count"] = result["count"] if result else 0

        final_editions.append(edition)

    conn.close()

    return jsonify(
        {
            "title": title,
            "author": author,
            "edition_count": len(final_editions),
            "editions": final_editions,
        }
    )


@app.route("/api/duplicates/delete", methods=["POST"])
def delete_duplicates() -> FlaskResponse:
    """
    Delete selected duplicate audiobooks.
    SAFETY: Will NEVER delete the last remaining copy of any audiobook.

    Request body:
    {
        "audiobook_ids": [1, 2, 3],  // IDs to delete
        "mode": "title" or "hash"    // Optional, defaults to "title"
    }

    IMPROVED SAFETY:
    - Groups by title + duration (not author, since author may be "Audiobook")
    - Ensures at least one copy with REAL author is kept
    - Prefers keeping entries with real author over "Audiobook" entries
    """
    data = request.get_json()
    if not data or "audiobook_ids" not in data:
        return jsonify({"error": "Missing audiobook_ids"}), 400

    ids_to_delete = data["audiobook_ids"]
    if not ids_to_delete:
        return jsonify({"error": "No audiobook IDs provided"}), 400

    mode = data.get("mode", "title")  # Default to title mode

    conn = get_db()
    cursor = conn.cursor()

    # Get all audiobooks to be deleted with their grouping keys
    placeholders = ",".join("?" * len(ids_to_delete))
    cursor.execute(
        f"""
        SELECT id, sha256_hash, title, author, file_path, duration_hours, file_size_mb,
               LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) as norm_title,
               LOWER(TRIM(author)) as norm_author,
               ROUND(duration_hours, 1) as duration_group
        FROM audiobooks
        WHERE id IN ({placeholders})
    """,
        ids_to_delete,
    )

    to_delete = [dict(row) for row in cursor.fetchall()]

    blocked_ids = []
    safe_to_delete = []

    if mode == "title":
        # Group by normalized title + duration (duration distinguishes different books with same title)
        title_groups = {}
        for item in to_delete:
            key = (item["norm_title"], item["duration_group"])
            if key not in title_groups:
                title_groups[key] = []
            title_groups[key].append(item)

        # For each title group, verify at least one copy will remain
        for (norm_title, duration_group), items in title_groups.items():
            # Count total copies with this title + similar duration
            cursor.execute(
                """
                SELECT COUNT(*) as count FROM audiobooks
                WHERE LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, ':', ''), '-', ''), '  ', ' '))) = ?
                  AND ROUND(duration_hours, 1) = ?
            """,
                (norm_title, duration_group),
            )
            total_copies = cursor.fetchone()["count"]

            deleting_count = len(items)

            if deleting_count >= total_copies:
                # Would delete all copies - block the best one (keeper)
                # Sort: prefer real author, then preferred format, then by ID
                def sort_key(x):
                    # Prefer real author over "Audiobook"
                    author_priority = 1 if x["norm_author"] == "audiobook" else 0
                    fmt_order = {"opus": 1, "m4b": 2, "m4a": 3, "mp3": 4}
                    ext = Path(x["file_path"]).suffix.lower().lstrip(".")
                    return (author_priority, fmt_order.get(ext, 5), x["id"])

                items_sorted = sorted(items, key=sort_key)
                blocked_ids.append(items_sorted[0]["id"])
                safe_to_delete.extend([i["id"] for i in items_sorted[1:]])
            else:
                safe_to_delete.extend([i["id"] for i in items])
    else:
        # Hash-based mode (original logic)
        hash_groups = {}
        for item in to_delete:
            h = item["sha256_hash"]
            if h not in hash_groups:
                hash_groups[h] = []
            hash_groups[h].append(item)

        for hash_val, items in hash_groups.items():
            if hash_val is None:
                blocked_ids.extend([i["id"] for i in items])
                continue

            cursor.execute(
                """
                SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?
            """,
                (hash_val,),
            )
            total_copies = cursor.fetchone()["count"]

            deleting_count = len(items)

            if deleting_count >= total_copies:
                items_sorted = sorted(items, key=lambda x: x["id"])
                blocked_ids.append(items_sorted[0]["id"])
                safe_to_delete.extend([i["id"] for i in items_sorted[1:]])
            else:
                safe_to_delete.extend([i["id"] for i in items])

    # Now perform the actual deletions
    deleted_files = []
    errors = []

    for audiobook_id in safe_to_delete:
        cursor.execute(
            "SELECT file_path, title FROM audiobooks WHERE id = ?", (audiobook_id,)
        )
        row = cursor.fetchone()

        if not row:
            continue

        file_path = Path(row["file_path"])
        title = row["title"]

        try:
            # Delete the physical file
            if file_path.exists():
                file_path.unlink()

            # Delete from database
            cursor.execute(
                "DELETE FROM audiobook_topics WHERE audiobook_id = ?", (audiobook_id,)
            )
            cursor.execute(
                "DELETE FROM audiobook_eras WHERE audiobook_id = ?", (audiobook_id,)
            )
            cursor.execute(
                "DELETE FROM audiobook_genres WHERE audiobook_id = ?", (audiobook_id,)
            )
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (audiobook_id,))

            deleted_files.append(
                {"id": audiobook_id, "title": title, "path": str(file_path)}
            )

        except Exception as e:
            errors.append({"id": audiobook_id, "title": title, "error": str(e)})

    conn.commit()
    conn.close()

    return jsonify(
        {
            "success": True,
            "deleted_count": len(deleted_files),
            "deleted_files": deleted_files,
            "blocked_count": len(blocked_ids),
            "blocked_ids": blocked_ids,
            "blocked_reason": "These IDs were blocked to prevent deleting the last copy",
            "errors": errors,
        }
    )


@app.route("/api/duplicates/verify", methods=["POST"])
def verify_deletion_safe() -> FlaskResponse:
    """
    Verify that a list of IDs can be safely deleted.
    Returns which IDs are safe and which would delete the last copy.
    """
    data = request.get_json()
    if not data or "audiobook_ids" not in data:
        return jsonify({"error": "Missing audiobook_ids"}), 400

    ids_to_check = data["audiobook_ids"]

    conn = get_db()
    cursor = conn.cursor()

    placeholders = ",".join("?" * len(ids_to_check))
    cursor.execute(
        f"""
        SELECT id, sha256_hash, title
        FROM audiobooks
        WHERE id IN ({placeholders})
    """,
        ids_to_check,
    )

    items = [dict(row) for row in cursor.fetchall()]

    # Group by hash
    hash_groups = {}
    for item in items:
        h = item["sha256_hash"]
        if h not in hash_groups:
            hash_groups[h] = []
        hash_groups[h].append(item)

    safe_ids = []
    unsafe_ids = []

    for hash_val, group_items in hash_groups.items():
        if hash_val is None:
            # No hash - can't verify safety
            unsafe_ids.extend(
                [
                    {
                        "id": i["id"],
                        "title": i["title"],
                        "reason": "No hash - cannot verify duplicates",
                    }
                    for i in group_items
                ]
            )
            continue

        cursor.execute(
            "SELECT COUNT(*) as count FROM audiobooks WHERE sha256_hash = ?",
            (hash_val,),
        )
        total_copies = cursor.fetchone()["count"]

        if len(group_items) >= total_copies:
            # Would delete all - block the first one (keeper)
            sorted_items = sorted(group_items, key=lambda x: x["id"])
            unsafe_ids.append(
                {
                    "id": sorted_items[0]["id"],
                    "title": sorted_items[0]["title"],
                    "reason": "Last remaining copy - protected from deletion",
                }
            )
            safe_ids.extend([i["id"] for i in sorted_items[1:]])
        else:
            safe_ids.extend([i["id"] for i in group_items])

    conn.close()

    return jsonify(
        {
            "safe_ids": safe_ids,
            "unsafe_ids": unsafe_ids,
            "safe_count": len(safe_ids),
            "unsafe_count": len(unsafe_ids),
        }
    )


# ============================================
# SUPPLEMENT ENDPOINTS
# ============================================

# SUPPLEMENTS_DIR imported from config


@app.route("/api/supplements", methods=["GET"])
def get_all_supplements() -> Response:
    """Get all supplements in the library"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, a.title as audiobook_title, a.author as audiobook_author
        FROM supplements s
        LEFT JOIN audiobooks a ON s.audiobook_id = a.id
        ORDER BY s.filename
    """)

    supplements = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify({"supplements": supplements, "total": len(supplements)})


@app.route("/api/supplements/stats", methods=["GET"])
def get_supplement_stats() -> Response:
    """Get supplement statistics"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM supplements")
    total = cursor.fetchone()["total"]

    cursor.execute(
        "SELECT COUNT(*) as linked FROM supplements WHERE audiobook_id IS NOT NULL"
    )
    linked = cursor.fetchone()["linked"]

    cursor.execute("SELECT SUM(file_size_mb) as total_size FROM supplements")
    total_size = cursor.fetchone()["total_size"] or 0

    cursor.execute("SELECT type, COUNT(*) as count FROM supplements GROUP BY type")
    by_type = {row["type"]: row["count"] for row in cursor.fetchall()}

    conn.close()

    return jsonify(
        {
            "total_supplements": total,
            "linked_to_audiobooks": linked,
            "unlinked": total - linked,
            "total_size_mb": round(total_size, 2),
            "by_type": by_type,
        }
    )


@app.route("/api/audiobooks/<int:audiobook_id>/supplements", methods=["GET"])
def get_audiobook_supplements(audiobook_id: int) -> Response:
    """Get supplements for a specific audiobook"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM supplements WHERE audiobook_id = ?
        ORDER BY type, filename
    """,
        (audiobook_id,),
    )

    supplements = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(
        {
            "audiobook_id": audiobook_id,
            "supplements": supplements,
            "count": len(supplements),
        }
    )


@app.route("/api/supplements/<int:supplement_id>/download", methods=["GET"])
def download_supplement(supplement_id: int) -> FlaskResponse:
    """Download/serve a supplement file"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM supplements WHERE id = ?", (supplement_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Supplement not found"}), 404

    file_path = Path(row["file_path"])
    if not file_path.exists():
        return jsonify({"error": "File not found on disk"}), 404

    # Map file types to MIME types
    mime_types = {
        "pdf": "application/pdf",
        "epub": "application/epub+zip",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "mp3": "audio/mpeg",
    }

    ext = file_path.suffix.lower().lstrip(".")
    mimetype = mime_types.get(ext, "application/octet-stream")

    return send_file(
        file_path, mimetype=mimetype, as_attachment=False, download_name=row["filename"]
    )


@app.route("/api/supplements/scan", methods=["POST"])
def scan_supplements() -> FlaskResponse:
    """
    Scan the supplements directory and update the database.
    Links supplements to audiobooks by matching filenames to titles.
    """
    if not SUPPLEMENTS_DIR.exists():
        return jsonify({"error": "Supplements directory not found"}), 404

    conn = get_db()
    cursor = conn.cursor()

    # Get existing supplements to avoid duplicates
    cursor.execute("SELECT file_path FROM supplements")
    existing_paths = {row["file_path"] for row in cursor.fetchall()}

    added = []
    updated = []

    for file_path in SUPPLEMENTS_DIR.iterdir():
        if file_path.is_file():
            path_str = str(file_path)
            filename = file_path.name
            ext = file_path.suffix.lower().lstrip(".")
            file_size = file_path.stat().st_size / (1024 * 1024)  # MB

            # Determine type
            type_map = {
                "pdf": "pdf",
                "epub": "ebook",
                "mobi": "ebook",
                "jpg": "image",
                "jpeg": "image",
                "png": "image",
                "mp3": "audio",
                "wav": "audio",
            }
            supplement_type = type_map.get(ext, "other")

            # Try to match to an audiobook by title
            # Clean filename for matching (remove extension, replace underscores)
            clean_name = file_path.stem.replace("_", " ").replace("-", " ")

            cursor.execute(
                """
                SELECT id, title FROM audiobooks
                WHERE LOWER(title) LIKE ?
                OR LOWER(REPLACE(REPLACE(title, ':', ''), '-', '')) LIKE ?
                LIMIT 1
            """,
                (f"%{clean_name[:30].lower()}%", f"%{clean_name[:30].lower()}%"),
            )

            match = cursor.fetchone()
            audiobook_id = match["id"] if match else None

            if path_str in existing_paths:
                # Update existing record
                cursor.execute(
                    """
                    UPDATE supplements
                    SET audiobook_id = ?, file_size_mb = ?, type = ?
                    WHERE file_path = ?
                """,
                    (audiobook_id, file_size, supplement_type, path_str),
                )
                updated.append(filename)
            else:
                # Insert new record
                cursor.execute(
                    """
                    INSERT INTO supplements (audiobook_id, type, filename, file_path, file_size_mb)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (audiobook_id, supplement_type, filename, path_str, file_size),
                )
                added.append(filename)

    conn.commit()
    conn.close()

    return jsonify(
        {
            "success": True,
            "added": len(added),
            "updated": len(updated),
            "added_files": added[:20],  # Limit response size
            "updated_files": updated[:20],
        }
    )


# ============================================
# UTILITIES - Library Administration
# ============================================


@app.route("/api/audiobooks/<int:id>", methods=["PUT"])
def update_audiobook(id: int) -> FlaskResponse:
    """Update audiobook metadata"""
    data = request.get_json()

    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    conn = get_db()
    cursor = conn.cursor()

    # Build update query dynamically based on provided fields
    allowed_fields = [
        "title",
        "author",
        "narrator",
        "publisher",
        "series",
        "series_sequence",
        "published_year",
        "asin",
        "isbn",
        "description",
    ]
    updates = []
    values = []

    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = ?")
            values.append(data[field])

    if not updates:
        conn.close()
        return jsonify({"success": False, "error": "No valid fields to update"}), 400

    values.append(id)
    query = f"UPDATE audiobooks SET {', '.join(updates)} WHERE id = ?"

    try:
        cursor.execute(query, values)
        conn.commit()
        rows_affected = cursor.rowcount
        conn.close()

        if rows_affected > 0:
            return jsonify({"success": True, "updated": rows_affected})
        else:
            return jsonify({"success": False, "error": "Audiobook not found"}), 404
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/audiobooks/<int:id>", methods=["DELETE"])
def delete_audiobook(id: int) -> FlaskResponse:
    """Delete audiobook from database (does not delete file)"""
    conn = get_db()
    cursor = conn.cursor()

    try:
        # Delete related records first
        cursor.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (id,))
        cursor.execute("DELETE FROM audiobook_topics WHERE audiobook_id = ?", (id,))
        cursor.execute("DELETE FROM audiobook_eras WHERE audiobook_id = ?", (id,))
        cursor.execute("DELETE FROM supplements WHERE audiobook_id = ?", (id,))

        # Delete the audiobook
        cursor.execute("DELETE FROM audiobooks WHERE id = ?", (id,))
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected > 0:
            return jsonify({"success": True, "deleted": rows_affected})
        else:
            return jsonify({"success": False, "error": "Audiobook not found"}), 404
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/audiobooks/bulk-update", methods=["POST"])
def bulk_update_audiobooks() -> FlaskResponse:
    """Update a field for multiple audiobooks"""
    data = request.get_json()

    if not data or "ids" not in data or "field" not in data:
        return jsonify(
            {"success": False, "error": "Missing required fields: ids, field, value"}
        ), 400

    ids = data["ids"]
    field = data["field"]
    value = data.get("value")

    # Whitelist allowed fields for bulk update
    allowed_fields = ["narrator", "series", "publisher", "published_year"]
    if field not in allowed_fields:
        return jsonify(
            {"success": False, "error": f"Field not allowed for bulk update: {field}"}
        ), 400

    if not ids:
        return jsonify({"success": False, "error": "No audiobook IDs provided"}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        placeholders = ",".join("?" * len(ids))
        query = f"UPDATE audiobooks SET {field} = ? WHERE id IN ({placeholders})"
        cursor.execute(query, [value] + ids)
        conn.commit()
        updated_count = cursor.rowcount
        conn.close()

        return jsonify({"success": True, "updated_count": updated_count})
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/audiobooks/bulk-delete", methods=["POST"])
def bulk_delete_audiobooks() -> FlaskResponse:
    """Delete multiple audiobooks"""
    data = request.get_json()

    if not data or "ids" not in data:
        return jsonify({"success": False, "error": "Missing required field: ids"}), 400

    ids = data["ids"]
    delete_files = data.get("delete_files", False)

    if not ids:
        return jsonify({"success": False, "error": "No audiobook IDs provided"}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Get file paths if we need to delete files
        deleted_files = []
        if delete_files:
            placeholders = ",".join("?" * len(ids))
            cursor.execute(
                f"SELECT id, file_path FROM audiobooks WHERE id IN ({placeholders})",
                ids,
            )
            for row in cursor.fetchall():
                file_path = Path(row["file_path"])
                if file_path.exists():
                    try:
                        file_path.unlink()
                        deleted_files.append(str(file_path))
                    except Exception as e:
                        print(f"Warning: Could not delete file {file_path}: {e}")

        # Delete related records
        placeholders = ",".join("?" * len(ids))
        cursor.execute(
            f"DELETE FROM audiobook_genres WHERE audiobook_id IN ({placeholders})", ids
        )
        cursor.execute(
            f"DELETE FROM audiobook_topics WHERE audiobook_id IN ({placeholders})", ids
        )
        cursor.execute(
            f"DELETE FROM audiobook_eras WHERE audiobook_id IN ({placeholders})", ids
        )
        cursor.execute(
            f"DELETE FROM supplements WHERE audiobook_id IN ({placeholders})", ids
        )

        # Delete audiobooks
        cursor.execute(f"DELETE FROM audiobooks WHERE id IN ({placeholders})", ids)
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        return jsonify(
            {
                "success": True,
                "deleted_count": deleted_count,
                "files_deleted": len(deleted_files) if delete_files else 0,
            }
        )
    except Exception as e:
        conn.close()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/audiobooks/missing-narrator", methods=["GET"])
def get_audiobooks_missing_narrator() -> Response:
    """Get audiobooks without narrator information"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, series, file_path
        FROM audiobooks
        WHERE narrator IS NULL OR narrator = '' OR narrator = 'Unknown Narrator'
        ORDER BY title
        LIMIT 200
    """)

    audiobooks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(audiobooks)


@app.route("/api/audiobooks/missing-hash", methods=["GET"])
def get_audiobooks_missing_hash() -> Response:
    """Get audiobooks without SHA-256 hash"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, series, file_path
        FROM audiobooks
        WHERE sha256_hash IS NULL OR sha256_hash = ''
        ORDER BY title
        LIMIT 200
    """)

    audiobooks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(audiobooks)


@app.route("/api/utilities/rescan", methods=["POST"])
def rescan_library() -> FlaskResponse:
    """Trigger a library rescan"""
    import subprocess

    scanner_path = PROJECT_ROOT / "scanner" / "scan_audiobooks.py"

    if not scanner_path.exists():
        return jsonify({"success": False, "error": "Scanner script not found"}), 500

    try:
        result = subprocess.run(
            ["python3", str(scanner_path)],
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout for large libraries
        )

        # Parse output to get file count
        output = result.stdout
        files_found = 0
        for line in output.split("\n"):
            if "Total audiobook files:" in line:
                try:
                    files_found = int(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass

        return jsonify(
            {
                "success": result.returncode == 0,
                "files_found": files_found,
                "output": output[-2000:]
                if len(output) > 2000
                else output,  # Limit output size
                "error": result.stderr if result.returncode != 0 else None,
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify(
            {"success": False, "error": "Scan timed out after 30 minutes"}
        ), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/utilities/reimport", methods=["POST"])
def reimport_database() -> FlaskResponse:
    """Reimport audiobooks to database"""
    import subprocess

    import_path = PROJECT_ROOT / "backend" / "import_to_db.py"

    if not import_path.exists():
        return jsonify({"success": False, "error": "Import script not found"}), 500

    try:
        result = subprocess.run(
            ["python3", str(import_path)],
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        # Parse output to get import count
        output = result.stdout
        imported_count = 0
        for line in output.split("\n"):
            if "Imported" in line and "audiobooks" in line:
                try:
                    # Extract number from lines like "✓ Imported 500 audiobooks"
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == "Imported" and i + 1 < len(parts):
                            imported_count = int(parts[i + 1])
                            break
                except (ValueError, IndexError):
                    pass

        return jsonify(
            {
                "success": result.returncode == 0,
                "imported_count": imported_count,
                "output": output[-2000:] if len(output) > 2000 else output,
                "error": result.stderr if result.returncode != 0 else None,
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify(
            {"success": False, "error": "Import timed out after 5 minutes"}
        ), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/utilities/generate-hashes", methods=["POST"])
def generate_hashes() -> FlaskResponse:
    """Generate SHA-256 hashes for audiobooks"""
    import subprocess

    hash_script = PROJECT_ROOT / "scripts" / "generate_hashes.py"

    if not hash_script.exists():
        return jsonify(
            {"success": False, "error": "Hash generation script not found"}
        ), 500

    try:
        result = subprocess.run(
            ["python3", str(hash_script), "--parallel"],
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout for large libraries
        )

        # Parse output to get hash count
        output = result.stdout
        hashes_generated = 0
        for line in output.split("\n"):
            if "Generated" in line or "hashes" in line.lower():
                try:
                    # Extract numbers from output
                    import re

                    numbers = re.findall(r"\d+", line)
                    if numbers:
                        hashes_generated = int(numbers[0])
                except ValueError:
                    pass

        return jsonify(
            {
                "success": result.returncode == 0,
                "hashes_generated": hashes_generated,
                "output": output[-2000:] if len(output) > 2000 else output,
                "error": result.stderr if result.returncode != 0 else None,
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify(
            {"success": False, "error": "Hash generation timed out after 30 minutes"}
        ), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/utilities/vacuum", methods=["POST"])
def vacuum_database() -> FlaskResponse:
    """Vacuum the SQLite database to reclaim space"""
    conn = get_db()

    try:
        # Get size before vacuum
        size_before = DB_PATH.stat().st_size

        # Run VACUUM
        conn.execute("VACUUM")
        conn.close()

        # Get size after vacuum
        size_after = DB_PATH.stat().st_size
        space_reclaimed = (size_before - size_after) / (1024 * 1024)  # Convert to MB

        return jsonify(
            {
                "success": True,
                "size_before_mb": size_before / (1024 * 1024),
                "size_after_mb": size_after / (1024 * 1024),
                "space_reclaimed_mb": max(0, space_reclaimed),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/utilities/export-db", methods=["GET"])
def export_database() -> FlaskResponse:
    """Download the SQLite database file"""
    if DB_PATH.exists():
        return send_file(
            DB_PATH,
            mimetype="application/x-sqlite3",
            as_attachment=True,
            download_name="audiobooks.db",
        )
    else:
        return jsonify({"error": "Database not found"}), 404


@app.route("/api/utilities/export-json", methods=["GET"])
def export_json() -> Response:
    """Export library as JSON"""
    import json
    from datetime import datetime

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, publisher, series, series_sequence,
               duration_hours, file_size_mb, file_path, published_year, asin, isbn
        FROM audiobooks
        ORDER BY title
    """)

    audiobooks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # Create response with JSON file
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "total_count": len(audiobooks),
        "audiobooks": audiobooks,
    }

    response = app.response_class(
        response=json.dumps(export_data, indent=2),
        status=200,
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = (
        "attachment; filename=audiobooks_export.json"
    )
    return response


@app.route("/api/utilities/export-csv", methods=["GET"])
def export_csv() -> Response:
    """Export library as CSV"""
    import csv
    import io
    from datetime import datetime

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, author, narrator, publisher, series, series_sequence,
               duration_hours, duration_formatted, file_size_mb, published_year, asin, isbn, file_path
        FROM audiobooks
        ORDER BY title
    """)

    audiobooks = cursor.fetchall()
    conn.close()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(
        [
            "ID",
            "Title",
            "Author",
            "Narrator",
            "Publisher",
            "Series",
            "Series #",
            "Duration (hours)",
            "Duration",
            "Size (MB)",
            "Year",
            "ASIN",
            "ISBN",
            "File Path",
        ]
    )

    # Write data
    for book in audiobooks:
        writer.writerow(list(book))

    # Create response
    response = app.response_class(
        response=output.getvalue(), status=200, mimetype="text/csv"
    )
    response.headers["Content-Disposition"] = (
        f"attachment; filename=audiobooks_export_{datetime.now().strftime('%Y%m%d')}.csv"
    )
    return response


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Please run: python3 backend/import_to_db.py")
        exit(1)

    print("Starting Audiobook Library API...")
    print(f"Database: {DB_PATH}")
    print("\nEndpoints:")
    print("  GET /api/stats - Library statistics")
    print("  GET /api/audiobooks - Paginated audiobooks")
    print("  GET /api/audiobooks/<id> - Single audiobook")
    print("  GET /api/filters - Available filter options")
    print("  GET /api/stream/<id> - Stream audiobook file")
    print("  GET /api/supplements - All supplements")
    print("  GET /api/supplements/stats - Supplement statistics")
    print("  GET /api/audiobooks/<id>/supplements - Supplements for audiobook")
    print("  POST /api/supplements/scan - Scan and import supplements")
    print("  GET /covers/<filename> - Cover images")
    print("\nExample queries:")
    print("  /api/audiobooks?page=1&per_page=50")
    print("  /api/audiobooks?search=tolkien")
    print("  /api/audiobooks?author=sanderson&sort=duration_hours&order=desc")
    print()

    # Check if running with waitress (production mode)
    use_waitress = os.environ.get("AUDIOBOOKS_USE_WAITRESS", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    if use_waitress:
        try:
            from waitress import serve

            bind_address = os.environ.get("AUDIOBOOKS_BIND_ADDRESS", "127.0.0.1")
            print("Running in production mode (waitress)")
            print(f"Listening on: http://{bind_address}:{API_PORT}")
            print()
            serve(app, host=bind_address, port=API_PORT, threads=4)
        except ImportError:
            print("Error: waitress not installed. Install with: pip install waitress")
            print("Falling back to Flask development server...")
            print(f"API running on: http://0.0.0.0:{API_PORT}")
            print()
            app.run(debug=True, host="0.0.0.0", port=API_PORT)
    else:
        # Development mode (Flask dev server)
        print("Running in development mode (Flask dev server)")
        print(f"API running on: http://0.0.0.0:{API_PORT}")
        print()
        app.run(debug=True, host="0.0.0.0", port=API_PORT)
