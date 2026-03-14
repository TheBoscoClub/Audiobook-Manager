"""
Tests for enriched flat /api/audiobooks endpoint with authors/narrators arrays.

Verifies that the flat audiobooks endpoint returns structured author/narrator
arrays alongside the existing flat string fields after the multi-author
normalization migration (011_multi_author_narrator.sql).
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Add library directory to path for imports
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"
MIGRATION_PATH = (
    LIBRARY_DIR / "backend" / "migrations" / "011_multi_author_narrator.sql"
)


@pytest.fixture(scope="module")
def enriched_temp_dir():
    """Module-scoped temp directory for enriched API tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(scope="module")
def enriched_app(enriched_temp_dir):
    """Create a Flask app with test data including normalized authors/narrators."""
    from backend.api_modular import create_app

    db_path = enriched_temp_dir / "enriched_test.db"
    supplements_dir = enriched_temp_dir / "supplements"
    supplements_dir.mkdir(exist_ok=True)

    # Initialize database with full schema
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    # Run the multi-author migration
    with open(MIGRATION_PATH) as f:
        conn.executescript(f.read())

    # Insert test audiobooks
    conn.execute(
        """
        INSERT INTO audiobooks (id, title, author, narrator, file_path, format,
            duration_hours, duration_formatted, file_size_mb, content_type,
            author_last_name, author_first_name,
            narrator_last_name, narrator_first_name)
        VALUES (1, 'The Talisman', 'Stephen King, Peter Straub', 'Frank Muller',
            '/test/talisman.opus', 'opus', 25.5, '25:30:00', 500.0, 'Product',
            'King', 'Stephen', 'Muller', 'Frank')
        """
    )
    conn.execute(
        """
        INSERT INTO audiobooks (id, title, author, narrator, file_path, format,
            duration_hours, duration_formatted, file_size_mb, content_type,
            author_last_name, author_first_name,
            narrator_last_name, narrator_first_name)
        VALUES (2, 'It', 'Stephen King', 'Steven Weber',
            '/test/it.opus', 'opus', 44.5, '44:30:00', 900.0, 'Product',
            'King', 'Stephen', 'Weber', 'Steven')
        """
    )
    conn.execute(
        """
        INSERT INTO audiobooks (id, title, author, narrator, file_path, format,
            duration_hours, duration_formatted, file_size_mb, content_type,
            author_last_name, author_first_name,
            narrator_last_name, narrator_first_name)
        VALUES (3, 'Solo Book', 'Jane Doe', 'John Smith',
            '/test/solo.opus', 'opus', 10.0, '10:00:00', 200.0, 'Product',
            'Doe', 'Jane', 'Smith', 'John')
        """
    )

    # Populate normalized authors
    conn.execute(
        "INSERT INTO authors (id, name, sort_name)"
        " VALUES (1, 'Stephen King', 'King, Stephen')"
    )
    conn.execute(
        "INSERT INTO authors (id, name, sort_name)"
        " VALUES (2, 'Peter Straub', 'Straub, Peter')"
    )
    conn.execute(
        "INSERT INTO authors (id, name, sort_name) VALUES (3, 'Jane Doe', 'Doe, Jane')"
    )

    # Populate normalized narrators
    conn.execute(
        "INSERT INTO narrators (id, name, sort_name)"
        " VALUES (1, 'Frank Muller', 'Muller, Frank')"
    )
    conn.execute(
        "INSERT INTO narrators (id, name, sort_name)"
        " VALUES (2, 'Steven Weber', 'Weber, Steven')"
    )
    conn.execute(
        "INSERT INTO narrators (id, name, sort_name)"
        " VALUES (3, 'John Smith', 'Smith, John')"
    )

    # Populate junction tables (book_authors)
    conn.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (1, 1, 0)"
    )  # Talisman -> King
    conn.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (1, 2, 1)"
    )  # Talisman -> Straub
    conn.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (2, 1, 0)"
    )  # It -> King
    conn.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (3, 3, 0)"
    )  # Solo -> Doe

    # Populate junction tables (book_narrators)
    conn.execute(
        "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (1, 1, 0)"
    )  # Talisman -> Muller
    conn.execute(
        "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (2, 2, 0)"
    )  # It -> Weber
    conn.execute(
        "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (3, 3, 0)"
    )  # Solo -> Smith

    conn.commit()
    conn.close()

    app = create_app(
        database_path=db_path,
        project_dir=enriched_temp_dir,
        supplements_dir=supplements_dir,
        api_port=5098,
    )
    app.config["TESTING"] = True

    return app


@pytest.fixture
def client(enriched_app):
    """Test client for enriched API tests."""
    with enriched_app.test_client() as c:
        yield c


class TestEnrichedAuthorsArray:
    """Test that /api/audiobooks returns authors arrays."""

    def test_authors_array_present(self, client):
        """Books should have an 'authors' array in the response."""
        resp = client.get("/api/audiobooks")
        assert resp.status_code == 200
        data = resp.get_json()
        books = data["audiobooks"]
        assert len(books) >= 3
        for book in books:
            assert "authors" in book, f"Book '{book['title']}' missing 'authors' array"
            assert isinstance(book["authors"], list)

    def test_multi_author_book_has_both_authors(self, client):
        """The Talisman (multi-author) should have two entries in authors array."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        assert len(talisman["authors"]) == 2
        names = [a["name"] for a in talisman["authors"]]
        assert "Stephen King" in names
        assert "Peter Straub" in names

    def test_author_entry_has_required_fields(self, client):
        """Each author entry should have id, name, sort_name, position."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        for author in talisman["authors"]:
            assert "id" in author
            assert "name" in author
            assert "sort_name" in author
            assert "position" in author
            assert isinstance(author["id"], int)
            assert isinstance(author["position"], int)

    def test_author_sort_name_format(self, client):
        """Author sort_name should be in 'Last, First' format."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        king = next(a for a in talisman["authors"] if a["name"] == "Stephen King")
        assert king["sort_name"] == "King, Stephen"
        straub = next(a for a in talisman["authors"] if a["name"] == "Peter Straub")
        assert straub["sort_name"] == "Straub, Peter"

    def test_author_position_ordering(self, client):
        """Authors should be ordered by position."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        positions = [a["position"] for a in talisman["authors"]]
        assert positions == sorted(positions)
        assert talisman["authors"][0]["name"] == "Stephen King"
        assert talisman["authors"][1]["name"] == "Peter Straub"

    def test_single_author_book(self, client):
        """A book with one author should have a single-element authors array."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        it_book = next(b for b in data["audiobooks"] if b["title"] == "It")
        assert len(it_book["authors"]) == 1
        assert it_book["authors"][0]["name"] == "Stephen King"
        assert it_book["authors"][0]["position"] == 0


class TestEnrichedNarratorsArray:
    """Test that /api/audiobooks returns narrators arrays."""

    def test_narrators_array_present(self, client):
        """Books should have a 'narrators' array in the response."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        for book in data["audiobooks"]:
            assert "narrators" in book, (
                f"Book '{book['title']}' missing 'narrators' array"
            )
            assert isinstance(book["narrators"], list)

    def test_narrator_entry_has_required_fields(self, client):
        """Each narrator entry should have id, name, sort_name, position."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        it_book = next(b for b in data["audiobooks"] if b["title"] == "It")
        assert len(it_book["narrators"]) == 1
        narrator = it_book["narrators"][0]
        assert narrator["id"] == 2
        assert narrator["name"] == "Steven Weber"
        assert narrator["sort_name"] == "Weber, Steven"
        assert narrator["position"] == 0

    def test_narrator_sort_name_format(self, client):
        """Narrator sort_name should be in 'Last, First' format."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        assert len(talisman["narrators"]) == 1
        assert talisman["narrators"][0]["sort_name"] == "Muller, Frank"


class TestFlatFieldsUnchanged:
    """Test that flat author/narrator string fields remain intact."""

    def test_flat_author_string_present(self, client):
        """The flat 'author' string field must still be present."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        assert talisman["author"] == "Stephen King, Peter Straub"

    def test_flat_narrator_string_present(self, client):
        """The flat 'narrator' string field must still be present."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        talisman = next(b for b in data["audiobooks"] if b["title"] == "The Talisman")
        assert talisman["narrator"] == "Frank Muller"

    def test_flat_and_array_coexist(self, client):
        """Both flat string and structured array should be in every book."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        for book in data["audiobooks"]:
            # Flat fields
            assert "author" in book
            assert "narrator" in book
            # Array fields
            assert "authors" in book
            assert "narrators" in book


class TestEmptyAuthorsNarrators:
    """Test behavior when normalized tables have no entries for a book."""

    def test_book_without_normalized_authors_gets_empty_array(
        self, enriched_app, client
    ):
        """A book with no entries in book_authors should get an empty authors array."""
        # Insert a book with no normalized author data
        db_path = enriched_app.config["DATABASE_PATH"]
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO audiobooks (id, title, author, narrator, file_path, format,
                duration_hours, duration_formatted, file_size_mb, content_type)
            VALUES (99, 'Orphan Book', 'Unknown Author', 'Unknown Narrator',
                '/test/orphan.opus', 'opus', 5.0, '5:00:00', 100.0, 'Product')
            """
        )
        conn.commit()
        conn.close()

        try:
            resp = client.get("/api/audiobooks?search=Orphan")
            data = resp.get_json()
            orphan = next(
                (b for b in data["audiobooks"] if b["title"] == "Orphan Book"), None
            )
            assert orphan is not None
            assert orphan["authors"] == []
            assert orphan["narrators"] == []
            # Flat fields still present
            assert orphan["author"] == "Unknown Author"
            assert orphan["narrator"] == "Unknown Narrator"
        finally:
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM audiobooks WHERE id = 99")
            conn.commit()
            conn.close()
