"""
Edition detection helpers and routes.
"""

import re

from flask import Blueprint, jsonify

from .core import FlaskResponse, get_db
from .auth import auth_if_enabled

editions_bp = Blueprint("editions", __name__)


def has_edition_marker(title: str | None) -> bool:
    """Check if a title contains edition markers indicating it's a specific edition."""
    if not title:
        return False

    title_lower = title.lower()

    edition_markers = [
        "edition",
        "anniversary",
        "revised",
        "updated",
        "unabridged",
        "abridged",
        "complete",
        "expanded",
        "deluxe",
        "special",
        "collectors",
        "annotated",
        "illustrated",
    ]

    return any(marker in title_lower for marker in edition_markers)


def normalize_base_title(title: str | None) -> str:
    """
    Normalize title by removing edition markers and common suffixes.
    This creates a base title for matching different editions.
    """
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


def init_editions_routes(db_path):
    """Initialize routes with database path."""

    @editions_bp.route("/api/audiobooks/<int:book_id>/editions", methods=["GET"])
    @auth_if_enabled
    def get_book_editions(book_id: int) -> FlaskResponse:
        """
        Get all editions of a specific audiobook.
        Only returns books that are truly different editions (with edition markers in title).
        """
        conn = get_db(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT title, author FROM audiobooks WHERE id = ?", (book_id,))
        result = cursor.fetchone()

        if not result:
            conn.close()
            return jsonify({"error": "Book not found"}), 404

        title = result["title"]
        author = result["author"]
        base_title = normalize_base_title(title)

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

        all_books = cursor.fetchall()
        editions = []

        for row in all_books:
            book_title = row["title"]
            book_base = normalize_base_title(book_title)

            if book_base == base_title:
                editions.append(dict(row))

        has_markers = any(has_edition_marker(ed["title"]) for ed in editions)

        if len(editions) <= 1 and not has_markers:
            editions = [dict(row) for row in all_books if row["id"] == book_id]

        final_editions = []
        for edition in editions:
            cursor.execute(
                """
                SELECT g.name FROM genres g
                JOIN audiobook_genres ag ON g.id = ag.genre_id
                WHERE ag.audiobook_id = ?
            """,
                (edition["id"],),
            )
            edition["genres"] = [r["name"] for r in cursor.fetchall()]

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

    return editions_bp
