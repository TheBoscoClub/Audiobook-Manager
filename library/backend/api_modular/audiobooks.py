"""
Audiobook listing, filtering, streaming, and individual book routes.

Note: All queries filter by content_type to exclude periodicals (podcasts,
newspapers, etc.) which belong in the Reading Room, not the main library.
"""

import sys
from flask import Blueprint, Response, jsonify, request, send_from_directory, send_file
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import COVER_DIR

from .core import get_db, FlaskResponse
from .editions import has_edition_marker, normalize_base_title
from .collections import COLLECTIONS

audiobooks_bp = Blueprint("audiobooks", __name__)

# Filter condition for main library (excludes periodicals)
# Include: Product, Lecture, Performance, Speech (all valid audiobook content)
# Exclude: Podcast, Newspaper / Magazine, Show, Radio/TV Program (periodicals → Reading Room)
# content_type IS NULL handles legacy entries before the field was added
AUDIOBOOK_FILTER = "(content_type IN ('Product', 'Lecture', 'Performance', 'Speech') OR content_type IS NULL)"


def init_audiobooks_routes(db_path, project_root, database_path):
    """Initialize routes with database path and project directories."""

    @audiobooks_bp.route("/api/stats", methods=["GET"])
    def get_stats() -> Response:
        """Get library statistics (excludes periodicals)"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        # Total audiobooks (excluding periodicals)
        cursor.execute(f"SELECT COUNT(*) as total FROM audiobooks WHERE {AUDIOBOOK_FILTER}")
        total_books = cursor.fetchone()["total"]

        # Total hours (excluding periodicals)
        cursor.execute(f"SELECT SUM(duration_hours) as total_hours FROM audiobooks WHERE {AUDIOBOOK_FILTER}")
        total_hours = cursor.fetchone()["total_hours"] or 0

        # Total storage used (sum of file sizes in MB, convert to GB)
        cursor.execute(f"SELECT SUM(file_size_mb) as total_size FROM audiobooks WHERE {AUDIOBOOK_FILTER}")
        total_size_mb = cursor.fetchone()["total_size"] or 0
        total_size_gb = total_size_mb / 1024

        # Unique counts (excluding placeholder values like "Audiobook" and "Unknown")
        cursor.execute(f"""
            SELECT COUNT(DISTINCT author) as count FROM audiobooks
            WHERE {AUDIOBOOK_FILTER}
              AND author IS NOT NULL
              AND LOWER(TRIM(author)) != 'audiobook'
              AND LOWER(TRIM(author)) != 'unknown author'
        """)
        unique_authors = cursor.fetchone()["count"]

        cursor.execute(f"""
            SELECT COUNT(DISTINCT narrator) as count FROM audiobooks
            WHERE {AUDIOBOOK_FILTER}
              AND narrator IS NOT NULL
              AND LOWER(TRIM(narrator)) != 'unknown narrator'
              AND LOWER(TRIM(narrator)) != ''
        """)
        unique_narrators = cursor.fetchone()["count"]

        cursor.execute(
            f"SELECT COUNT(DISTINCT publisher) as count FROM audiobooks WHERE {AUDIOBOOK_FILTER} AND publisher IS NOT NULL"
        )
        unique_publishers = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM genres")
        unique_genres = cursor.fetchone()["count"]

        conn.close()

        # Get database file size
        database_size_mb: float = 0.0
        try:
            import os

            db_path_str = str(database_path)
            if os.path.exists(db_path_str):
                database_size_mb = os.path.getsize(db_path_str) / (1024 * 1024)
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

    @audiobooks_bp.route("/api/audiobooks", methods=["GET"])
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
            "series": "series, series_sequence",
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

        conn = get_db(db_path)
        cursor = conn.cursor()

        # Build query - always filter to exclude periodicals from main library
        where_clauses = [AUDIOBOOK_FILTER]  # Excludes periodicals (podcasts, news, etc.)
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

    @audiobooks_bp.route("/api/filters", methods=["GET"])
    def get_filters() -> Response:
        """Get all available filter options (excludes periodicals)"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        # Get unique authors (excluding periodicals)
        cursor.execute(f"""
            SELECT DISTINCT author FROM audiobooks
            WHERE {AUDIOBOOK_FILTER} AND author IS NOT NULL
            ORDER BY author
        """)
        authors = [row["author"] for row in cursor.fetchall()]

        # Get unique narrators (excluding periodicals)
        cursor.execute(f"""
            SELECT DISTINCT narrator FROM audiobooks
            WHERE {AUDIOBOOK_FILTER} AND narrator IS NOT NULL
            ORDER BY narrator
        """)
        narrators = [row["narrator"] for row in cursor.fetchall()]

        # Get unique publishers (excluding periodicals)
        cursor.execute(f"""
            SELECT DISTINCT publisher FROM audiobooks
            WHERE {AUDIOBOOK_FILTER} AND publisher IS NOT NULL
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

        # Get formats (excluding periodicals)
        cursor.execute(f"""
            SELECT DISTINCT format FROM audiobooks
            WHERE {AUDIOBOOK_FILTER} AND format IS NOT NULL
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

    @audiobooks_bp.route("/api/narrator-counts", methods=["GET"])
    def get_narrator_counts() -> Response:
        """Get narrator book counts for autocomplete (excludes periodicals)"""
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT narrator, COUNT(*) as count
            FROM audiobooks
            WHERE {AUDIOBOOK_FILTER}
              AND narrator IS NOT NULL
              AND narrator != ''
              AND narrator != 'Unknown Narrator'
            GROUP BY narrator
            ORDER BY narrator
        """)

        counts = {row["narrator"]: row["count"] for row in cursor.fetchall()}
        conn.close()

        return jsonify(counts)

    @audiobooks_bp.route("/api/audiobooks/<int:audiobook_id>", methods=["GET"])
    def get_audiobook(audiobook_id: int) -> FlaskResponse:
        """Get single audiobook details"""
        conn = get_db(db_path)
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
    def serve_cover(filename: str) -> Response:
        """Serve cover images from configured COVER_DIR"""
        return send_from_directory(COVER_DIR, filename)

    @audiobooks_bp.route("/api/stream/<int:audiobook_id>")
    def stream_audiobook(audiobook_id: int) -> FlaskResponse:
        """Stream audiobook file"""
        conn = get_db(db_path)
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

    @audiobooks_bp.route("/health")
    def health() -> Response:
        """Health check endpoint with version info"""
        version = "unknown"
        # VERSION file is in project root (one level above library/)
        version_file = project_root.parent / "VERSION"
        if version_file.exists():
            version = version_file.read_text().strip()
        return jsonify({
            "status": "ok",
            "database": str(db_path.exists()),
            "version": version
        })

    return audiobooks_bp
