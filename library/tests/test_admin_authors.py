"""
Tests for admin author/narrator correction endpoints.

Tests rename, merge, and reassign operations for both authors and narrators,
including flat column regeneration after each operation.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))

SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"

# SQL to insert test data — reused to reset DB state between tests
_TEST_DATA_SQL = """
INSERT INTO audiobooks (id, title, author, narrator, file_path, format, content_type)
VALUES (1, 'The Talisman', 'Stephen King, Peter Straub', 'Frank Muller',
        '/test/talisman.opus', 'opus', 'Product');
INSERT INTO audiobooks (id, title, author, narrator, file_path, format, content_type)
VALUES (2, 'It', 'Stephen King', 'Steven Weber',
        '/test/it.opus', 'opus', 'Product');
INSERT INTO audiobooks (id, title, author, narrator, file_path, format, content_type)
VALUES (3, 'Ghost Story', 'Peter Straub', 'Frank Muller',
        '/test/ghost.opus', 'opus', 'Product');

INSERT INTO authors (id, name, sort_name) VALUES (1, 'Stephen King', 'King, Stephen');
INSERT INTO authors (id, name, sort_name) VALUES (2, 'Peter Straub', 'Straub, Peter');
INSERT INTO authors (id, name, sort_name) VALUES (3, 'Steven King', 'King, Steven');

INSERT INTO book_authors (book_id, author_id, position) VALUES (1, 1, 0);
INSERT INTO book_authors (book_id, author_id, position) VALUES (1, 2, 1);
INSERT INTO book_authors (book_id, author_id, position) VALUES (2, 1, 0);
INSERT INTO book_authors (book_id, author_id, position) VALUES (3, 2, 0);

INSERT INTO narrators (id, name, sort_name) VALUES (1, 'Frank Muller', 'Muller, Frank');
INSERT INTO narrators (id, name, sort_name) VALUES (2, 'Steven Weber', 'Weber, Steven');
INSERT INTO narrators (id, name, sort_name) VALUES (3, 'Frank Mueller', 'Mueller, Frank');

INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (1, 1, 0);
INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (2, 2, 0);
INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (3, 1, 0);
"""


def _reset_db(db_path: Path) -> None:
    """Reset test database to initial state."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    # Clear all test tables in dependency order
    conn.execute("DELETE FROM book_authors")
    conn.execute("DELETE FROM book_narrators")
    conn.execute("DELETE FROM authors")
    conn.execute("DELETE FROM narrators")
    conn.execute("DELETE FROM audiobooks")
    conn.executescript(_TEST_DATA_SQL)
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def admin_authors_tmpdir():
    """Module-scoped temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="module")
def admin_authors_app(admin_authors_tmpdir):
    """Create a module-scoped Flask app for admin author tests.

    Builds a minimal Flask app directly (not via create_app) to avoid
    blueprint re-registration conflicts when multiple test files each
    need their own app instance with different database paths.
    """
    from flask import Flask

    from backend.api_modular.admin_authors import admin_authors_bp, init_admin_authors_routes
    from backend.api_modular.core import add_cors_headers

    tmpdir = admin_authors_tmpdir
    db_path = tmpdir / "test.db"

    # Initialize database with schema + test data
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_TEST_DATA_SQL)
    conn.commit()
    conn.close()

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["AUTH_ENABLED"] = False
    app.config["test_db_path"] = db_path

    # Create fresh blueprint to avoid re-registration
    from flask import Blueprint
    fresh_bp = Blueprint("admin_authors_test", __name__)

    # Import the route setup function internals and bind to fresh blueprint
    from backend.api_modular.admin_authors import init_admin_authors_routes as _init
    _init(db_path)

    @app.after_request
    def cors(response):
        return add_cors_headers(response)

    app.register_blueprint(admin_authors_bp)

    yield app


@pytest.fixture(autouse=True)
def reset_db(admin_authors_app):
    """Reset database to clean state before each test."""
    _reset_db(admin_authors_app.config["test_db_path"])


@pytest.fixture
def client(admin_authors_app):
    """Test client for the admin authors app."""
    with admin_authors_app.test_client() as c:
        yield c


@pytest.fixture
def db_conn(admin_authors_app):
    """Direct database connection for verification queries."""
    db_path = admin_authors_app.config["test_db_path"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


# ============================================================
# Author rename tests
# ============================================================


class TestRenameAuthor:
    """Test PUT /api/admin/authors/<id>."""

    def test_rename_author_name_and_sort(self, client, db_conn):
        """Rename updates both name and sort_name."""
        resp = client.put(
            "/api/admin/authors/1",
            json={"name": "Stephen Edwin King", "sort_name": "King, Stephen Edwin"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Stephen Edwin King"
        assert data["sort_name"] == "King, Stephen Edwin"

    def test_rename_author_name_only(self, client):
        """Rename with only name provided."""
        resp = client.put(
            "/api/admin/authors/2",
            json={"name": "Peter Francis Straub"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Peter Francis Straub"
        # sort_name should remain unchanged
        assert data["sort_name"] == "Straub, Peter"

    def test_rename_author_regenerates_flat_column(self, client, db_conn):
        """Rename updates the flat author column on affected books."""
        client.put(
            "/api/admin/authors/1",
            json={"name": "S. King", "sort_name": "King, S."},
        )
        # Book 1 has authors [1, 2] -> "S. King, Peter Straub"
        row = db_conn.execute("SELECT author FROM audiobooks WHERE id = 1").fetchone()
        assert row["author"] == "S. King, Peter Straub"

        # Book 2 has author [1] -> "S. King"
        row = db_conn.execute("SELECT author FROM audiobooks WHERE id = 2").fetchone()
        assert row["author"] == "S. King"

    def test_rename_author_not_found(self, client):
        """Returns 404 for nonexistent author."""
        resp = client.put(
            "/api/admin/authors/999",
            json={"name": "Nobody"},
        )
        assert resp.status_code == 404

    def test_rename_author_no_fields(self, client):
        """Returns 400 when neither name nor sort_name provided."""
        resp = client.put("/api/admin/authors/1", json={})
        assert resp.status_code == 400


# ============================================================
# Author merge tests
# ============================================================


class TestMergeAuthors:
    """Test POST /api/admin/authors/merge."""

    def test_merge_authors_basic(self, client, db_conn):
        """Merge typo duplicate (Steven King -> Stephen King)."""
        # First link the typo author to a book so we have something to reassign
        db_conn.execute(
            "INSERT INTO book_authors (book_id, author_id, position) VALUES (3, 3, 1)"
        )
        db_conn.commit()

        resp = client.post(
            "/api/admin/authors/merge",
            json={"source_ids": [3], "target_id": 1},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["author"]["id"] == 1
        assert data["author"]["name"] == "Stephen King"
        assert data["books_reassigned"] == 1

        # Source author should be deleted
        row = db_conn.execute("SELECT id FROM authors WHERE id = 3").fetchone()
        assert row is None

    def test_merge_authors_target_already_linked(self, client, db_conn):
        """Merge where target is already linked to the same book."""
        # Link author 3 (Steven King) to book 2 which already has author 1 (Stephen King)
        db_conn.execute(
            "INSERT INTO book_authors (book_id, author_id, position) VALUES (2, 3, 1)"
        )
        db_conn.commit()

        resp = client.post(
            "/api/admin/authors/merge",
            json={"source_ids": [3], "target_id": 1},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["books_reassigned"] == 1

        # Source should be deleted, target still exists
        row = db_conn.execute("SELECT id FROM authors WHERE id = 3").fetchone()
        assert row is None

        # Book 2 should only have author 1, not a duplicate
        links = db_conn.execute(
            "SELECT author_id FROM book_authors WHERE book_id = 2"
        ).fetchall()
        author_ids = [row["author_id"] for row in links]
        assert author_ids == [1]

    def test_merge_regenerates_flat_column(self, client, db_conn):
        """Merge updates flat author column on affected books."""
        # Link author 3 to book 3 (currently only has author 2)
        db_conn.execute(
            "INSERT INTO book_authors (book_id, author_id, position) VALUES (3, 3, 1)"
        )
        db_conn.commit()

        client.post(
            "/api/admin/authors/merge",
            json={"source_ids": [3], "target_id": 1},
        )

        # Book 3 should now show "Peter Straub, Stephen King" (positions 0, 1)
        row = db_conn.execute("SELECT author FROM audiobooks WHERE id = 3").fetchone()
        assert "Stephen King" in row["author"]
        assert "Peter Straub" in row["author"]

    def test_merge_target_not_found(self, client):
        """Returns 404 for nonexistent target."""
        resp = client.post(
            "/api/admin/authors/merge",
            json={"source_ids": [3], "target_id": 999},
        )
        assert resp.status_code == 404

    def test_merge_source_not_found(self, client):
        """Returns 404 for nonexistent source."""
        resp = client.post(
            "/api/admin/authors/merge",
            json={"source_ids": [999], "target_id": 1},
        )
        assert resp.status_code == 404

    def test_merge_target_in_sources(self, client):
        """Returns 400 if target_id is in source_ids."""
        resp = client.post(
            "/api/admin/authors/merge",
            json={"source_ids": [1, 2], "target_id": 1},
        )
        assert resp.status_code == 400


# ============================================================
# Book author reassignment tests
# ============================================================


class TestReassignBookAuthors:
    """Test PUT /api/admin/books/<id>/authors."""

    def test_reassign_book_authors(self, client, db_conn):
        """Replace book's author list entirely."""
        resp = client.put(
            "/api/admin/books/3/authors",
            json={"author_ids": [1, 2], "positions": [0, 1]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["authors"]) == 2
        assert data["authors"][0]["id"] == 1
        assert data["authors"][1]["id"] == 2

    def test_reassign_regenerates_flat_column(self, client, db_conn):
        """Reassign updates flat author column."""
        client.put(
            "/api/admin/books/3/authors",
            json={"author_ids": [1], "positions": [0]},
        )
        row = db_conn.execute("SELECT author FROM audiobooks WHERE id = 3").fetchone()
        assert row["author"] == "Stephen King"

    def test_reassign_book_not_found(self, client):
        """Returns 404 for nonexistent book."""
        resp = client.put(
            "/api/admin/books/999/authors",
            json={"author_ids": [1], "positions": [0]},
        )
        assert resp.status_code == 404

    def test_reassign_author_not_found(self, client):
        """Returns 404 for nonexistent author in list."""
        resp = client.put(
            "/api/admin/books/1/authors",
            json={"author_ids": [999], "positions": [0]},
        )
        assert resp.status_code == 404

    def test_reassign_mismatched_lengths(self, client):
        """Returns 400 if positions and author_ids have different lengths."""
        resp = client.put(
            "/api/admin/books/1/authors",
            json={"author_ids": [1, 2], "positions": [0]},
        )
        assert resp.status_code == 400


# ============================================================
# Narrator rename tests
# ============================================================


class TestRenameNarrator:
    """Test PUT /api/admin/narrators/<id>."""

    def test_rename_narrator(self, client):
        """Rename updates name and sort_name."""
        resp = client.put(
            "/api/admin/narrators/1",
            json={"name": "Frank Muller Jr.", "sort_name": "Muller Jr., Frank"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "Frank Muller Jr."
        assert data["sort_name"] == "Muller Jr., Frank"

    def test_rename_narrator_regenerates_flat_column(self, client, db_conn):
        """Rename updates flat narrator column on affected books."""
        client.put(
            "/api/admin/narrators/1",
            json={"name": "F. Muller"},
        )
        row = db_conn.execute("SELECT narrator FROM audiobooks WHERE id = 1").fetchone()
        assert row["narrator"] == "F. Muller"

    def test_rename_narrator_not_found(self, client):
        """Returns 404 for nonexistent narrator."""
        resp = client.put(
            "/api/admin/narrators/999",
            json={"name": "Nobody"},
        )
        assert resp.status_code == 404


# ============================================================
# Narrator merge tests
# ============================================================


class TestMergeNarrators:
    """Test POST /api/admin/narrators/merge."""

    def test_merge_narrators(self, client, db_conn):
        """Merge typo duplicate narrator."""
        # Link narrator 3 (Frank Mueller) to book 2
        db_conn.execute(
            "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (2, 3, 1)"
        )
        db_conn.commit()

        resp = client.post(
            "/api/admin/narrators/merge",
            json={"source_ids": [3], "target_id": 1},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["narrator"]["id"] == 1
        assert data["books_reassigned"] == 1

        # Source narrator deleted
        row = db_conn.execute("SELECT id FROM narrators WHERE id = 3").fetchone()
        assert row is None

    def test_merge_narrator_regenerates_flat(self, client, db_conn):
        """Merge updates flat narrator column."""
        db_conn.execute(
            "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (3, 3, 1)"
        )
        db_conn.commit()

        client.post(
            "/api/admin/narrators/merge",
            json={"source_ids": [3], "target_id": 1},
        )

        row = db_conn.execute("SELECT narrator FROM audiobooks WHERE id = 3").fetchone()
        assert "Frank Muller" in row["narrator"]

    def test_merge_narrator_not_found(self, client):
        """Returns 404 for nonexistent target narrator."""
        resp = client.post(
            "/api/admin/narrators/merge",
            json={"source_ids": [3], "target_id": 999},
        )
        assert resp.status_code == 404


# ============================================================
# Book narrator reassignment tests
# ============================================================


class TestReassignBookNarrators:
    """Test PUT /api/admin/books/<id>/narrators."""

    def test_reassign_book_narrators(self, client, db_conn):
        """Replace book's narrator list entirely."""
        resp = client.put(
            "/api/admin/books/1/narrators",
            json={"narrator_ids": [1, 2], "positions": [0, 1]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["narrators"]) == 2
        assert data["narrators"][0]["id"] == 1
        assert data["narrators"][1]["id"] == 2

    def test_reassign_narrators_regenerates_flat(self, client, db_conn):
        """Reassign updates flat narrator column."""
        client.put(
            "/api/admin/books/2/narrators",
            json={"narrator_ids": [1, 2], "positions": [0, 1]},
        )
        row = db_conn.execute("SELECT narrator FROM audiobooks WHERE id = 2").fetchone()
        assert row["narrator"] == "Frank Muller, Steven Weber"

    def test_reassign_narrator_not_found(self, client):
        """Returns 404 for nonexistent narrator in list."""
        resp = client.put(
            "/api/admin/books/1/narrators",
            json={"narrator_ids": [999], "positions": [0]},
        )
        assert resp.status_code == 404

    def test_reassign_book_not_found_narrators(self, client):
        """Returns 404 for nonexistent book."""
        resp = client.put(
            "/api/admin/books/999/narrators",
            json={"narrator_ids": [1], "positions": [0]},
        )
        assert resp.status_code == 404
