"""
Tests for the grouped audiobook endpoint (/api/audiobooks/grouped).

Tests cover:
- Grouped by author returns correct structure
- Grouped by narrator works
- Invalid 'by' parameter returns 400
- Multi-author book appears in both author groups
- total_books is deduplicated count
- Groups sorted by sort_name (case-insensitive)
- Books within groups sorted by title (case-insensitive)
- Orphan books (no junction rows) appear in Unknown group at end
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))


@pytest.fixture
def grouped_db(flask_app, app_client):
    """Seed the test database with authors, narrators, and junction data.

    Creates a multi-author scenario:
    - "The Talisman" by Stephen King AND Peter Straub, narrated by Frank Muller
    - "It" by Stephen King, narrated by Steven Weber
    - "Ghost Story" by Peter Straub, narrated by Frank Muller
    - "Orphan Book" — no junction rows (tests Unknown group)
    """
    db_path = flask_app.config["DATABASE_PATH"]
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    # Save existing data and clear tables for isolated grouped tests
    cursor.execute("SELECT * FROM audiobooks")
    _saved_books = cursor.fetchall()
    _saved_cols = [desc[0] for desc in cursor.description]
    cursor.execute("DELETE FROM book_authors")
    cursor.execute("DELETE FROM book_narrators")
    cursor.execute("DELETE FROM authors")
    cursor.execute("DELETE FROM narrators")
    cursor.execute("DELETE FROM audiobooks")
    conn.commit()

    # Insert audiobooks
    books = [
        (
            "The Talisman",
            "Stephen King, Peter Straub",
            "Frank Muller",
            "/test/talisman.opus",
            "Product",
        ),
        ("It", "Stephen King", "Steven Weber", "/test/it.opus", "Product"),
        (
            "Ghost Story",
            "Peter Straub",
            "Frank Muller",
            "/test/ghost.opus",
            "Product",
        ),
        (
            "Orphan Book",
            "Unknown Author",
            "Unknown Narrator",
            "/test/orphan.opus",
            "Product",
        ),
    ]

    book_ids = []
    for title, author, narrator, path, ctype in books:
        cursor.execute(
            """
            INSERT INTO audiobooks (title, author, narrator, file_path, format,
                                    duration_hours, content_type, file_size_mb)
            VALUES (?, ?, ?, ?, 'opus', 10.0, ?, 100.0)
            """,
            (title, author, narrator, path, ctype),
        )
        book_ids.append(cursor.lastrowid)

    talisman_id, it_id, ghost_id, orphan_id = book_ids

    # Insert authors (sort_name in "Last, First" form)
    cursor.execute(
        "INSERT INTO authors (name, sort_name) VALUES (?, ?)",
        ("Stephen King", "King, Stephen"),
    )
    king_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO authors (name, sort_name) VALUES (?, ?)",
        ("Peter Straub", "Straub, Peter"),
    )
    straub_id = cursor.lastrowid

    # Insert narrators
    cursor.execute(
        "INSERT INTO narrators (name, sort_name) VALUES (?, ?)",
        ("Frank Muller", "Muller, Frank"),
    )
    muller_id = cursor.lastrowid

    cursor.execute(
        "INSERT INTO narrators (name, sort_name) VALUES (?, ?)",
        ("Steven Weber", "Weber, Steven"),
    )
    weber_id = cursor.lastrowid

    # Junction: book_authors
    # The Talisman -> King + Straub (multi-author)
    cursor.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, ?)",
        (talisman_id, king_id, 0),
    )
    cursor.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, ?)",
        (talisman_id, straub_id, 1),
    )
    # It -> King
    cursor.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, ?)",
        (it_id, king_id, 0),
    )
    # Ghost Story -> Straub
    cursor.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, ?)",
        (ghost_id, straub_id, 0),
    )
    # Orphan Book — no junction rows (intentionally)

    # Junction: book_narrators
    # The Talisman -> Muller
    cursor.execute(
        "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (?, ?, ?)",
        (talisman_id, muller_id, 0),
    )
    # It -> Weber
    cursor.execute(
        "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (?, ?, ?)",
        (it_id, weber_id, 0),
    )
    # Ghost Story -> Muller
    cursor.execute(
        "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (?, ?, ?)",
        (ghost_id, muller_id, 0),
    )
    # Orphan Book — no narrator junction rows

    conn.commit()

    yield {
        "book_ids": {
            "talisman": talisman_id,
            "it": it_id,
            "ghost": ghost_id,
            "orphan": orphan_id,
        },
        "author_ids": {"king": king_id, "straub": straub_id},
        "narrator_ids": {"muller": muller_id, "weber": weber_id},
    }

    # Cleanup: remove test data and restore original books
    cursor.execute("DELETE FROM book_authors")
    cursor.execute("DELETE FROM book_narrators")
    cursor.execute("DELETE FROM authors")
    cursor.execute("DELETE FROM narrators")
    cursor.execute("DELETE FROM audiobooks")
    if _saved_books:
        placeholders = ", ".join("?" * len(_saved_cols))
        cols_str = ", ".join(_saved_cols)
        cursor.executemany(
            f"INSERT INTO audiobooks ({cols_str}) VALUES ({placeholders})",
            _saved_books,
        )
    conn.commit()
    conn.close()


class TestGroupedByAuthor:
    """Tests for GET /api/audiobooks/grouped?by=author"""

    def test_grouped_by_author_structure(self, app_client, grouped_db):
        """Response has correct top-level structure."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        assert resp.status_code == 200
        data = resp.get_json()

        assert "groups" in data
        assert "total_groups" in data
        assert "total_books" in data
        assert isinstance(data["groups"], list)

    def test_grouped_by_author_group_keys(self, app_client, grouped_db):
        """Each group has a key with id, name, and sort_name."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        for group in data["groups"]:
            assert "key" in group
            assert "books" in group
            key = group["key"]
            assert "id" in key
            assert "name" in key
            assert "sort_name" in key

    def test_multi_author_appears_in_both_groups(self, app_client, grouped_db):
        """The Talisman (King + Straub) appears under both author groups."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        talisman_id = grouped_db["book_ids"]["talisman"]

        # Find which groups contain The Talisman
        groups_with_talisman = []
        for group in data["groups"]:
            book_ids_in_group = [b["id"] for b in group["books"]]
            if talisman_id in book_ids_in_group:
                groups_with_talisman.append(group["key"]["name"])

        assert "Stephen King" in groups_with_talisman
        assert "Peter Straub" in groups_with_talisman

    def test_total_books_is_deduplicated(self, app_client, grouped_db):
        """total_books counts each book once even if it appears in multiple groups."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        # We inserted 4 books: Talisman, It, Ghost Story, Orphan
        assert data["total_books"] == 4

        # But total book appearances across groups > 4 because Talisman is in 2 groups
        total_appearances = sum(len(g["books"]) for g in data["groups"])
        assert total_appearances == 5  # King(2) + Straub(2) + Unknown(1)

    def test_groups_sorted_by_sort_name(self, app_client, grouped_db):
        """Groups are sorted by sort_name (case-insensitive). King before Straub."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        # Filter out the Unknown group for sort verification
        named_groups = [g for g in data["groups"] if g["key"]["id"] is not None]
        sort_names = [g["key"]["sort_name"] for g in named_groups]

        # "King, Stephen" should come before "Straub, Peter"
        assert sort_names == sorted(sort_names, key=str.lower)

    def test_books_within_group_sorted_by_title(self, app_client, grouped_db):
        """Books within each group are sorted by title (case-insensitive)."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        for group in data["groups"]:
            titles = [b["title"] for b in group["books"]]
            assert titles == sorted(titles, key=str.lower), (
                f"Books in group '{group['key']['name']}' not sorted by title: {titles}"
            )

    def test_orphan_books_in_unknown_author_group(self, app_client, grouped_db):
        """Books with no junction rows appear in 'Unknown Author' group."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        unknown_groups = [
            g for g in data["groups"] if g["key"]["name"] == "Unknown Author"
        ]
        assert len(unknown_groups) == 1

        orphan_id = grouped_db["book_ids"]["orphan"]
        orphan_books = unknown_groups[0]["books"]
        orphan_book_ids = [b["id"] for b in orphan_books]
        assert orphan_id in orphan_book_ids

    def test_unknown_group_is_last(self, app_client, grouped_db):
        """The Unknown Author group appears at the end of the list."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        last_group = data["groups"][-1]
        assert last_group["key"]["name"] == "Unknown Author"

    def test_total_groups_count(self, app_client, grouped_db):
        """total_groups matches the number of groups returned."""
        resp = app_client.get("/api/audiobooks/grouped?by=author")
        data = resp.get_json()

        assert data["total_groups"] == len(data["groups"])
        # 2 named authors + 1 Unknown
        assert data["total_groups"] == 3


class TestGroupedByNarrator:
    """Tests for GET /api/audiobooks/grouped?by=narrator"""

    def test_grouped_by_narrator_works(self, app_client, grouped_db):
        """Narrator grouping returns correct structure."""
        resp = app_client.get("/api/audiobooks/grouped?by=narrator")
        assert resp.status_code == 200
        data = resp.get_json()

        assert "groups" in data
        assert "total_groups" in data
        assert "total_books" in data

    def test_narrator_groups_correct(self, app_client, grouped_db):
        """Frank Muller has 2 books, Steven Weber has 1, Unknown has 1."""
        resp = app_client.get("/api/audiobooks/grouped?by=narrator")
        data = resp.get_json()

        group_book_counts = {g["key"]["name"]: len(g["books"]) for g in data["groups"]}

        assert group_book_counts.get("Frank Muller") == 2
        assert group_book_counts.get("Steven Weber") == 1
        assert group_book_counts.get("Unknown Narrator") == 1

    def test_narrator_total_books_deduplicated(self, app_client, grouped_db):
        """total_books is deduplicated — 4 unique books."""
        resp = app_client.get("/api/audiobooks/grouped?by=narrator")
        data = resp.get_json()
        assert data["total_books"] == 4

    def test_narrator_unknown_group_last(self, app_client, grouped_db):
        """Unknown Narrator group is last."""
        resp = app_client.get("/api/audiobooks/grouped?by=narrator")
        data = resp.get_json()

        last_group = data["groups"][-1]
        assert last_group["key"]["name"] == "Unknown Narrator"


class TestGroupedValidation:
    """Tests for parameter validation."""

    def test_missing_by_returns_400(self, app_client):
        """Missing 'by' parameter returns 400."""
        resp = app_client.get("/api/audiobooks/grouped")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_invalid_by_returns_400(self, app_client):
        """Invalid 'by' value returns 400."""
        resp = app_client.get("/api/audiobooks/grouped?by=genre")
        assert resp.status_code == 400

    def test_empty_by_returns_400(self, app_client):
        """Empty 'by' value returns 400."""
        resp = app_client.get("/api/audiobooks/grouped?by=")
        assert resp.status_code == 400


class TestGroupedContentTypeFilter:
    """Test that non-audiobook content types are excluded."""

    def test_podcast_excluded(self, flask_app, app_client, grouped_db):
        """Books with content_type='Podcast' are excluded from grouped results."""
        db_path = flask_app.config["DATABASE_PATH"]
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()

        # Insert a podcast
        cursor.execute(
            """
            INSERT INTO audiobooks (title, author, narrator, file_path, format,
                                    duration_hours, content_type, file_size_mb)
            VALUES ('Test Podcast', 'Host', 'Host', '/test/podcast.opus',
                    'opus', 1.0, 'Podcast', 10.0)
            """
        )
        podcast_id = cursor.lastrowid

        # Add to an author group
        king_id = grouped_db["author_ids"]["king"]
        cursor.execute(
            "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, 0)",
            (podcast_id, king_id),
        )
        conn.commit()

        try:
            resp = app_client.get("/api/audiobooks/grouped?by=author")
            data = resp.get_json()

            # Podcast should NOT appear in any group
            all_book_ids = set()
            for group in data["groups"]:
                for book in group["books"]:
                    all_book_ids.add(book["id"])

            assert podcast_id not in all_book_ids
        finally:
            cursor.execute("DELETE FROM audiobooks WHERE id = ?", (podcast_id,))
            conn.commit()
            conn.close()
