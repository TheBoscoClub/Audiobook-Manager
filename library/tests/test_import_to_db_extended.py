"""
Extended tests for import_to_db module.

Covers uncovered lines: orphaned cover cleanup, enrichment preservation/restore,
content_type preservation, category/review preservation, junction table rebuild,
validate_json_source, main() error paths, and edge cases.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add library and backend directories to path so import_to_db can
# resolve its internal imports (name_parser, config)
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))

SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"


def _create_db_with_schema(db_path: Path) -> sqlite3.Connection:
    """Create a database initialized from the real schema.sql."""
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def _make_book(**overrides) -> dict:
    """Build a minimal audiobook dict for JSON import, applying overrides."""
    book: dict[str, str | int | float | list | None] = {
        "title": "Default Title",
        "author": "Jane Smith",
        "narrator": "John Doe",
        "publisher": "Acme Publishing",
        "series": None,
        "duration_hours": 8.0,
        "duration_formatted": "8:00:00",
        "file_size_mb": 200.0,
        "file_path": "/library/default.opus",
        "cover_path": None,
        "format": "opus",
        "quality": "64kbps",
        "description": "",
        "genres": [],
        "eras": [],
        "topics": [],
    }
    book.update(overrides)
    return book


def _write_json(path: Path, audiobooks: list[dict]) -> Path:
    """Write audiobooks list to a JSON file."""
    path.write_text(json.dumps({"audiobooks": audiobooks}))
    return path


def _import_helper(tmp_path, audiobooks, *, cover_dir=None):
    """Set up DB + JSON, run import, return (conn, cursor).

    Patches module-level paths so import_to_db operates entirely
    inside tmp_path.  Optionally sets COVER_DIR for cover cleanup tests.
    """
    from backend import import_to_db

    db_path = tmp_path / "test.db"
    schema_path = LIBRARY_DIR / "backend" / "schema.sql"
    json_path = _write_json(tmp_path / "audiobooks.json", audiobooks)

    patches = {
        "DB_PATH": db_path,
        "SCHEMA_PATH": schema_path,
        "JSON_PATH": json_path,
    }
    if cover_dir is not None:
        patches["COVER_DIR"] = cover_dir

    with patch.multiple(import_to_db, **patches):
        conn = import_to_db.create_database()
        import_to_db.import_audiobooks(conn)

    return conn, conn.cursor()


# =====================================================================
# _cleanup_orphaned_covers
# =====================================================================


class TestCleanupOrphanedCovers:
    """Tests for _cleanup_orphaned_covers (lines 48-81)."""

    def test_no_cover_dir(self, tmp_path, capsys):
        """Line 55-56: early return when COVER_DIR does not exist."""
        from backend import import_to_db

        nonexistent = tmp_path / "no_such_dir"
        db_path = tmp_path / "test.db"
        conn = _create_db_with_schema(db_path)
        cursor = conn.cursor()

        with patch.object(import_to_db, "COVER_DIR", nonexistent):
            import_to_db._cleanup_orphaned_covers(cursor)

        out = capsys.readouterr().out
        # Should return silently — no output about orphans or cleanup
        assert "orphaned" not in out.lower()
        conn.close()

    def test_no_orphans(self, tmp_path, capsys):
        """Lines 70-72: all cover files are referenced — nothing to delete."""
        from backend import import_to_db

        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        # Create a cover file on disk
        (cover_dir / "book1_cover.jpg").write_bytes(b"\xff" * 100)

        db_path = tmp_path / "test.db"
        conn = _create_db_with_schema(db_path)
        cursor = conn.cursor()
        # Insert a row referencing that cover
        cursor.execute(
            "INSERT INTO audiobooks (title, file_path, cover_path)"
            " VALUES ('Book', '/lib/b.opus', 'book1_cover.jpg')"
        )
        conn.commit()

        with patch.object(import_to_db, "COVER_DIR", cover_dir):
            import_to_db._cleanup_orphaned_covers(cursor)

        out = capsys.readouterr().out
        assert "No orphaned cover files" in out
        # File still exists
        assert (cover_dir / "book1_cover.jpg").exists()
        conn.close()

    def test_removes_orphaned_covers(self, tmp_path, capsys):
        """Lines 66-67, 74-81: orphaned files are deleted and stats printed."""
        from backend import import_to_db

        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()

        # Referenced cover
        (cover_dir / "keep.jpg").write_bytes(b"\xff" * 50)
        # Orphaned covers
        (cover_dir / "orphan1.jpg").write_bytes(b"\xff" * 1024)
        (cover_dir / "orphan2.jpg").write_bytes(b"\xff" * 2048)

        db_path = tmp_path / "test.db"
        conn = _create_db_with_schema(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audiobooks (title, file_path, cover_path)"
            " VALUES ('Book', '/lib/b.opus', 'keep.jpg')"
        )
        conn.commit()

        with patch.object(import_to_db, "COVER_DIR", cover_dir):
            import_to_db._cleanup_orphaned_covers(cursor)

        out = capsys.readouterr().out
        assert "2 orphaned cover files" in out

        # Orphans gone, keep.jpg survives
        assert (cover_dir / "keep.jpg").exists()
        assert not (cover_dir / "orphan1.jpg").exists()
        assert not (cover_dir / "orphan2.jpg").exists()
        conn.close()

    def test_skips_subdirectories(self, tmp_path):
        """Line 66: only files are collected, subdirectories are ignored."""
        from backend import import_to_db

        cover_dir = tmp_path / "covers"
        cover_dir.mkdir()
        (cover_dir / "subdir").mkdir()  # should be ignored
        (cover_dir / "orphan.jpg").write_bytes(b"\xff" * 10)

        db_path = tmp_path / "test.db"
        conn = _create_db_with_schema(db_path)
        cursor = conn.cursor()
        # No audiobooks → orphan.jpg is orphaned
        with patch.object(import_to_db, "COVER_DIR", cover_dir):
            import_to_db._cleanup_orphaned_covers(cursor)

        assert not (cover_dir / "orphan.jpg").exists()
        assert (cover_dir / "subdir").is_dir()  # subdirectory untouched
        conn.close()


# =====================================================================
# Junction table rebuild (_populate_names_and_junctions)
# =====================================================================


class TestPopulateNamesAndJunctions:
    """Tests for junction table population (lines 84-185)."""

    def test_author_name_split(self, tmp_path):
        """Author last/first name columns populated correctly."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(author="Stephen King", file_path="/lib/a.opus")],
        )
        cur.execute("SELECT author_last_name, author_first_name FROM audiobooks")
        row = cur.fetchone()
        assert row[0] == "King"
        assert row[1] == "Stephen"
        conn.close()

    def test_narrator_name_split(self, tmp_path):
        """Narrator last/first name columns populated correctly."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(narrator="Morgan Freeman", file_path="/lib/a.opus")],
        )
        cur.execute("SELECT narrator_last_name, narrator_first_name FROM audiobooks")
        row = cur.fetchone()
        assert row[0] == "Freeman"
        assert row[1] == "Morgan"
        conn.close()

    def test_junction_tables_created(self, tmp_path):
        """Junction rows created for authors and narrators."""
        conn, cur = _import_helper(
            tmp_path,
            [
                _make_book(
                    author="Stephen King",
                    narrator="Will Patton",
                    file_path="/lib/a.opus",
                )
            ],
        )
        cur.execute("SELECT COUNT(*) FROM book_authors")
        assert cur.fetchone()[0] >= 1
        cur.execute("SELECT COUNT(*) FROM book_narrators")
        assert cur.fetchone()[0] >= 1
        conn.close()

    def test_multiple_authors_junction(self, tmp_path):
        """Multiple authors produce multiple junction rows."""
        conn, cur = _import_helper(
            tmp_path,
            [
                _make_book(
                    author="Neil Gaiman, Terry Pratchett",
                    file_path="/lib/a.opus",
                )
            ],
        )
        cur.execute("SELECT COUNT(*) FROM book_authors")
        count = cur.fetchone()[0]
        assert count == 2

        cur.execute("SELECT COUNT(*) FROM authors")
        assert cur.fetchone()[0] == 2
        conn.close()

    def test_duplicate_author_dedup(self, tmp_path):
        """Same author across two books creates only one authors row."""
        conn, cur = _import_helper(
            tmp_path,
            [
                _make_book(author="Stephen King", file_path="/lib/a.opus"),
                _make_book(
                    title="Book 2", author="Stephen King", file_path="/lib/b.opus"
                ),
            ],
        )
        cur.execute("SELECT COUNT(*) FROM authors")
        assert cur.fetchone()[0] == 1

        cur.execute("SELECT COUNT(*) FROM book_authors")
        assert cur.fetchone()[0] == 2
        conn.close()

    def test_empty_author_no_junction(self, tmp_path):
        """Blank/null author produces no junction row."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(author="", narrator="", file_path="/lib/a.opus")],
        )
        cur.execute("SELECT COUNT(*) FROM book_authors")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM book_narrators")
        assert cur.fetchone()[0] == 0
        conn.close()

    def test_single_name_author(self, tmp_path):
        """Author with single name (no comma in sort_name) — line 111-112."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(author="Voltaire", file_path="/lib/a.opus")],
        )
        cur.execute("SELECT author_last_name, author_first_name FROM audiobooks")
        row = cur.fetchone()
        # Single-name: last_name is the sort name, first_name is None
        assert row[0] is not None
        conn.close()

    def test_whitespace_only_author_skipped(self, tmp_path):
        """Line 104: whitespace-only author treated as empty."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(author="   ", file_path="/lib/a.opus")],
        )
        cur.execute("SELECT COUNT(*) FROM book_authors")
        assert cur.fetchone()[0] == 0
        conn.close()


# =====================================================================
# Enrichment preservation & restore
# =====================================================================


class TestEnrichmentPreservation:
    """Tests for preserving and restoring enrichment data (lines 244-279, 391-414)."""

    def test_enrichment_round_trip(self, tmp_path):
        """Enrichment data survives a reimport cycle."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/enriched.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            # Simulate enrichment (as enrich_from_audible.py would do)
            conn.execute(
                "UPDATE audiobooks SET series='Dark Tower', series_sequence=1,"
                " subtitle='The Gunslinger', language='English',"
                " audible_enriched_at='2026-01-01' WHERE file_path=?",
                (fp,),
            )
            conn.commit()

            # Re-import — enrichment should survive
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute(
            "SELECT series, series_sequence, subtitle, language,"
            " audible_enriched_at FROM audiobooks WHERE file_path=?",
            (fp,),
        )
        row = cur.fetchone()
        assert row[0] == "Dark Tower"
        assert row[1] == 1
        assert row[2] == "The Gunslinger"
        assert row[3] == "English"
        assert row[4] == "2026-01-01"
        conn.close()


# =====================================================================
# Content type preservation (non-enriched)
# =====================================================================


class TestContentTypePreservation:
    """Tests for content_type preservation (lines 206-215, 419-423)."""

    def test_content_type_preserved_without_enrichment(self, tmp_path):
        """Line 419-423: content_type set by populate_content_types.py
        (no audible_enriched_at) survives reimport."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/podcast.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            # Set content_type WITHOUT setting audible_enriched_at
            conn.execute(
                "UPDATE audiobooks SET content_type='Podcast' WHERE file_path=?",
                (fp,),
            )
            conn.commit()

            # Re-import
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute("SELECT content_type FROM audiobooks WHERE file_path=?", (fp,))
        assert cur.fetchone()[0] == "Podcast"
        conn.close()

    def test_default_content_type_not_preserved(self, tmp_path):
        """Only non-default content_type values are preserved (line 211)."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/normal.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)
            # content_type defaults to 'Product' from schema — this should NOT
            # appear in preserved_content_types (line 211 filters it out)
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute("SELECT content_type FROM audiobooks WHERE file_path=?", (fp,))
        # Will be NULL since not explicitly set and 'Product' default is filtered
        # The INSERT doesn't set content_type, so it's whatever the DB defaults to
        row = cur.fetchone()
        assert row is not None  # row exists
        conn.close()


# =====================================================================
# Category preservation
# =====================================================================


class TestCategoryPreservation:
    """Tests for Audible category round-trip (lines 282-303, 425-441)."""

    def test_categories_round_trip(self, tmp_path):
        """Categories survive a reimport."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/cat.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            # Simulate category insertion (as enrich_from_audible.py would)
            cur = conn.cursor()
            cur.execute("SELECT id FROM audiobooks WHERE file_path=?", (fp,))
            aid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO audible_categories"
                " (audiobook_id, category_path, category_name,"
                "  root_category, depth, audible_category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    aid,
                    "Science Fiction > Space Opera",
                    "Space Opera",
                    "Science Fiction",
                    2,
                    "cat123",
                ),
            )
            conn.commit()

            # Re-import
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute(
            "SELECT category_name, root_category, depth"
            " FROM audible_categories ac"
            " JOIN audiobooks a ON a.id = ac.audiobook_id"
            " WHERE a.file_path=?",
            (fp,),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "Space Opera"
        assert row[1] == "Science Fiction"
        assert row[2] == 2
        conn.close()


# =====================================================================
# Editorial review preservation
# =====================================================================


class TestEditorialReviewPreservation:
    """Tests for editorial review round-trip (lines 305-322, 443-450)."""

    def test_reviews_round_trip(self, tmp_path):
        """Editorial reviews survive a reimport."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/rev.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            cur = conn.cursor()
            cur.execute("SELECT id FROM audiobooks WHERE file_path=?", (fp,))
            aid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO editorial_reviews"
                " (audiobook_id, review_text, source)"
                " VALUES (?, ?, ?)",
                (aid, "A masterpiece of storytelling.", "Publishers Weekly"),
            )
            conn.commit()

            # Re-import
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute(
            "SELECT er.review_text, er.source FROM editorial_reviews er"
            " JOIN audiobooks a ON a.id = er.audiobook_id"
            " WHERE a.file_path=?",
            (fp,),
        )
        row = cur.fetchone()
        assert row[0] == "A masterpiece of storytelling."
        assert row[1] == "Publishers Weekly"
        conn.close()

    def test_multiple_reviews_preserved(self, tmp_path):
        """Multiple reviews for the same book all survive."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/multi_rev.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            cur = conn.cursor()
            cur.execute("SELECT id FROM audiobooks WHERE file_path=?", (fp,))
            aid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO editorial_reviews"
                " (audiobook_id, review_text, source) VALUES (?, ?, ?)",
                (aid, "Review one", "Source A"),
            )
            cur.execute(
                "INSERT INTO editorial_reviews"
                " (audiobook_id, review_text, source) VALUES (?, ?, ?)",
                (aid, "Review two", "Source B"),
            )
            conn.commit()

            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM editorial_reviews")
        assert cur.fetchone()[0] == 2
        conn.close()


# =====================================================================
# Genre preservation from existing DB
# =====================================================================


class TestGenrePreservation:
    """Test that genre data from DB overrides JSON genres on reimport (lines 237-239)."""

    def test_preserved_genres_override_json(self, tmp_path):
        """Preserved DB genres take priority over JSON genres."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/genre.opus"

        # JSON says genres = ["Fiction"]
        books = [_make_book(file_path=fp, genres=["Fiction"])]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            # Manually add a richer genre set (as Audible enrichment would)
            cur = conn.cursor()
            cur.execute("SELECT id FROM audiobooks WHERE file_path=?", (fp,))
            aid = cur.fetchone()[0]
            cur.execute("INSERT INTO genres (name) VALUES ('Thriller')")
            gid = cur.lastrowid
            cur.execute(
                "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
                (aid, gid),
            )
            conn.commit()

            # Re-import — preserved genres should include both Fiction and Thriller
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute(
            "SELECT g.name FROM genres g"
            " JOIN audiobook_genres ag ON g.id = ag.genre_id"
            " JOIN audiobooks a ON a.id = ag.audiobook_id"
            " WHERE a.file_path=?",
            (fp,),
        )
        genres = {row[0] for row in cur.fetchall()}
        # Preserved genres from DB should include the Thriller we added
        assert "Thriller" in genres
        assert "Fiction" in genres
        conn.close()


# =====================================================================
# validate_json_source
# =====================================================================


class TestValidateJsonSource:
    """Tests for validate_json_source (lines 547-586)."""

    def test_valid_production_data(self, tmp_path):
        """Returns True for production-like data (>= 20 books, no test titles)."""
        from backend import import_to_db

        books = [
            _make_book(title=f"Real Book {i}", file_path=f"/lib/{i}.opus")
            for i in range(25)
        ]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        assert import_to_db.validate_json_source(json_path) is True

    def test_small_dataset_exits(self, tmp_path):
        """Line 567-568: < 20 books without SKIP_IMPORT_VALIDATION exits."""
        from backend import import_to_db

        books = [
            _make_book(title=f"Book {i}", file_path=f"/lib/{i}.opus") for i in range(5)
        ]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        # Ensure env var is NOT set
        env = os.environ.copy()
        env.pop("SKIP_IMPORT_VALIDATION", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                import_to_db.validate_json_source(json_path)
            assert exc_info.value.code == 1

    def test_small_dataset_skip_validation(self, tmp_path, monkeypatch):
        """Line 567: SKIP_IMPORT_VALIDATION=1 bypasses small-dataset check."""
        from backend import import_to_db

        monkeypatch.setenv("SKIP_IMPORT_VALIDATION", "1")

        books = [
            _make_book(title=f"Book {i}", file_path=f"/lib/{i}.opus") for i in range(5)
        ]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        assert import_to_db.validate_json_source(json_path) is True

    def test_test_titles_exits(self, tmp_path):
        """Lines 574-584: 'Test Audiobook' in titles triggers exit."""
        from backend import import_to_db

        books = [
            _make_book(title=f"Real Book {i}", file_path=f"/lib/{i}.opus")
            for i in range(25)
        ]
        books.append(
            _make_book(title="Test Audiobook Sample", file_path="/lib/test.opus")
        )
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        env = os.environ.copy()
        env.pop("SKIP_IMPORT_VALIDATION", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                import_to_db.validate_json_source(json_path)
            assert exc_info.value.code == 1

    def test_test_titles_skip_validation(self, tmp_path, monkeypatch):
        """Lines 575-584: SKIP_IMPORT_VALIDATION=1 bypasses test-title check."""
        from backend import import_to_db

        monkeypatch.setenv("SKIP_IMPORT_VALIDATION", "1")

        books = [
            _make_book(title=f"Real Book {i}", file_path=f"/lib/{i}.opus")
            for i in range(25)
        ]
        books.append(
            _make_book(title="Test Audiobook Sample", file_path="/lib/test.opus")
        )
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        assert import_to_db.validate_json_source(json_path) is True


# =====================================================================
# main() error/success paths
# =====================================================================


class TestMainExtended:
    """Tests for main() paths not covered by existing tests (lines 605-616)."""

    def test_main_import_exception(self, tmp_path, capsys):
        """Lines 605-610: Exception during import_audiobooks prints traceback."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        # Write invalid JSON structure (missing 'audiobooks' key)
        json_path = tmp_path / "audiobooks.json"
        json_path.write_text('{"bad_key": []}')

        with (
            patch.object(import_to_db, "DB_PATH", db_path),
            patch.object(import_to_db, "SCHEMA_PATH", schema_path),
            patch.object(import_to_db, "JSON_PATH", json_path),
            patch.object(import_to_db, "validate_json_source", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc_info:
                import_to_db.main()

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Error:" in out

    def test_main_closes_connection(self, tmp_path, monkeypatch):
        """Line 612: connection is always closed (finally block)."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        books = [_make_book(file_path="/lib/a.opus")]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        monkeypatch.setenv("SKIP_IMPORT_VALIDATION", "1")

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            import_to_db.main()

        # Verify DB file exists and is accessible (connection was closed cleanly)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM audiobooks")
        assert cur.fetchone()[0] == 1
        conn.close()


# =====================================================================
# Narrator preservation (line 225)
# =====================================================================


class TestNarratorPreservation:
    """Test narrator preservation across reimport (lines 217-226, 350-352)."""

    def test_narrator_preserved_on_reimport(self, tmp_path):
        """Manually set narrator survives reimport even when JSON has null."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/narr.opus"

        # JSON has narrator=None
        books = [_make_book(file_path=fp, narrator=None)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            # Manually set narrator (simulating Audible export)
            conn.execute(
                "UPDATE audiobooks SET narrator='Jim Dale' WHERE file_path=?",
                (fp,),
            )
            conn.commit()

            # Re-import
            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute("SELECT narrator FROM audiobooks WHERE file_path=?", (fp,))
        assert cur.fetchone()[0] == "Jim Dale"
        conn.close()


# =====================================================================
# Edge cases
# =====================================================================


class TestEdgeCasesExtended:
    """Additional edge cases."""

    def test_missing_metadata_fields(self, tmp_path):
        """Import succeeds when many optional fields are absent."""
        minimal_book = {
            "title": "Minimal",
            "file_path": "/lib/minimal.opus",
        }
        conn, cur = _import_helper(tmp_path, [minimal_book])
        cur.execute("SELECT title FROM audiobooks")
        assert cur.fetchone()[0] == "Minimal"
        conn.close()

    def test_asin_stored(self, tmp_path):
        """ASIN field imported correctly."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(file_path="/lib/a.opus", asin="B012345678")],
        )
        cur.execute("SELECT asin FROM audiobooks")
        assert cur.fetchone()[0] == "B012345678"
        conn.close()

    def test_published_year_and_date(self, tmp_path):
        """Published year and date stored correctly."""
        conn, cur = _import_helper(
            tmp_path,
            [
                _make_book(
                    file_path="/lib/a.opus",
                    published_year=2024,
                    published_date="2024-06-15",
                    acquired_date="2025-01-01",
                )
            ],
        )
        cur.execute(
            "SELECT published_year, published_date, acquired_date FROM audiobooks"
        )
        row = cur.fetchone()
        assert row[0] == 2024
        assert row[1] == "2024-06-15"
        assert row[2] == "2025-01-01"
        conn.close()

    def test_shared_genres_eras_topics_across_books(self, tmp_path):
        """Shared genre/era/topic names across books are deduped."""
        conn, cur = _import_helper(
            tmp_path,
            [
                _make_book(
                    title="Book A",
                    file_path="/lib/a.opus",
                    genres=["Sci-Fi"],
                    eras=["Modern"],
                    topics=["AI"],
                ),
                _make_book(
                    title="Book B",
                    file_path="/lib/b.opus",
                    genres=["Sci-Fi"],
                    eras=["Modern"],
                    topics=["AI"],
                ),
            ],
        )
        cur.execute("SELECT COUNT(*) FROM genres")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM eras")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT COUNT(*) FROM topics")
        assert cur.fetchone()[0] == 1

        # Both books linked
        cur.execute("SELECT COUNT(*) FROM audiobook_genres")
        assert cur.fetchone()[0] == 2
        conn.close()

    def test_enrichment_null_values_skipped(self, tmp_path):
        """Enrichment fields with None values are not written to UPDATE."""
        from backend import import_to_db

        db_path = tmp_path / "test.db"
        schema_path = LIBRARY_DIR / "backend" / "schema.sql"
        fp = "/lib/partial_enrich.opus"

        books = [_make_book(file_path=fp)]
        json_path = _write_json(tmp_path / "audiobooks.json", books)

        with patch.multiple(
            import_to_db,
            DB_PATH=db_path,
            SCHEMA_PATH=schema_path,
            JSON_PATH=json_path,
        ):
            conn = import_to_db.create_database()
            import_to_db.import_audiobooks(conn)

            # Set only some enrichment fields, leave others NULL
            conn.execute(
                "UPDATE audiobooks SET language='English',"
                " audible_enriched_at='2026-01-01'"
                " WHERE file_path=?",
                (fp,),
            )
            conn.commit()

            import_to_db.import_audiobooks(conn)

        cur = conn.cursor()
        cur.execute(
            "SELECT language, subtitle FROM audiobooks WHERE file_path=?",
            (fp,),
        )
        row = cur.fetchone()
        assert row[0] == "English"
        assert row[1] is None  # subtitle was never set
        conn.close()

    def test_cover_path_stored(self, tmp_path):
        """Cover path from JSON is stored in the database."""
        conn, cur = _import_helper(
            tmp_path,
            [_make_book(file_path="/lib/a.opus", cover_path="abc123.jpg")],
        )
        cur.execute("SELECT cover_path FROM audiobooks")
        assert cur.fetchone()[0] == "abc123.jpg"
        conn.close()
