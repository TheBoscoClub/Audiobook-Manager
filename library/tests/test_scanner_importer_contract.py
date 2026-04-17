"""
Tests for the data contract between scanner (enrich_metadata) and importer (import_to_db).

Validates that:
1. enrich_metadata() output fields match what import_to_db.py expects
2. The full chain (enrich -> JSON -> import -> DB) populates junction tables
3. The literary_era/eras regression never recurs (scanner must output both)
4. Edge cases (empty genre, empty year, empty description) produce valid output
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scanner.metadata_utils import (
    build_genres_list,
    categorize_genre,
    determine_literary_era,
    enrich_metadata,
    extract_topics,
)

# Schema path for creating test databases
SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


def _create_test_db(db_path: Path) -> sqlite3.Connection:
    """Create a test database from schema.sql and return the connection."""
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    return conn


def _import_book_to_db(conn: sqlite3.Connection, book: dict) -> int:
    """Import a single book dict into the database, mimicking import_to_db.py logic.

    Returns the audiobook_id of the inserted row.
    """
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO audiobooks (
            title, author, narrator, publisher, series,
            duration_hours, duration_formatted, file_size_mb,
            file_path, cover_path, format, quality, description,
            sha256_hash, published_year, published_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book.get("title"),
            book.get("author"),
            book.get("narrator"),
            book.get("publisher"),
            book.get("series"),
            book.get("duration_hours"),
            book.get("duration_formatted"),
            book.get("file_size_mb"),
            book.get("file_path"),
            book.get("cover_path"),
            book.get("format"),
            book.get("quality"),
            book.get("description", ""),
            book.get("sha256_hash"),
            book.get("published_year"),
            book.get("published_date"),
        ),
    )
    audiobook_id: int = cursor.lastrowid  # type: ignore[assignment]  # always set after INSERT

    # Handle genres — same logic as import_to_db.py line 471
    for genre_name in book.get("genres", []):
        cursor.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (genre_name,))
        cursor.execute("SELECT id FROM genres WHERE name = ?", (genre_name,))
        genre_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
            (audiobook_id, genre_id),
        )

    # Handle eras — same logic as import_to_db.py line 483
    for era_name in book.get("eras", []):
        cursor.execute("INSERT OR IGNORE INTO eras (name) VALUES (?)", (era_name,))
        cursor.execute("SELECT id FROM eras WHERE name = ?", (era_name,))
        era_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)",
            (audiobook_id, era_id),
        )

    # Handle topics — same logic as import_to_db.py line 494
    for topic_name in book.get("topics", []):
        cursor.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (topic_name,))
        cursor.execute("SELECT id FROM topics WHERE name = ?", (topic_name,))
        topic_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO audiobook_topics (audiobook_id, topic_id) VALUES (?, ?)",
            (audiobook_id, topic_id),
        )

    conn.commit()
    return audiobook_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_metadata():
    """Representative raw metadata as produced by the scanner before enrichment."""
    return {
        "title": "Dune",
        "author": "Frank Herbert",
        "narrator": "Scott Brick",
        "publisher": "Macmillan Audio",
        "series": "Dune",
        "genre": "Science Fiction",
        "year": "1965",
        "description": (
            "A stunning blend of adventure and mysticism set on the desert "
            "planet Arrakis, exploring politics, religion, and technology "
            "in a far future society."
        ),
        "duration_hours": 21.1,
        "duration_formatted": "21:06:00",
        "file_size_mb": 450.2,
        "file_path": "/audiobooks/Library/Frank Herbert/Dune/Dune.opus",
        "cover_path": "abc123.jpg",
        "format": "opus",
        "quality": "64kbps",
        "published_year": 1965,
        "published_date": "1965-08-01",
    }


@pytest.fixture
def test_db():
    """Create a temporary test database from schema.sql."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_contract.db"
        conn = _create_test_db(db_path)
        yield conn
        conn.close()


# ===========================================================================
# Test 1: Field contract — enrich_metadata output matches importer expectations
# ===========================================================================


class TestFieldContract:
    """Verify enrich_metadata() output field names and types match import_to_db.py."""

    def test_genres_key_exists_and_is_list(self, sample_metadata):
        """enrich_metadata must produce 'genres' as a list (importer: book.get('genres', []))."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert "genres" in enriched, "Missing 'genres' key in enriched metadata"
        assert isinstance(enriched["genres"], list), (
            f"'genres' must be a list, got {type(enriched['genres']).__name__}"
        )

    def test_eras_key_exists_and_is_list(self, sample_metadata):
        """enrich_metadata must produce 'eras' as a list (importer: book.get('eras', []))."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert "eras" in enriched, "Missing 'eras' key in enriched metadata"
        assert isinstance(enriched["eras"], list), (
            f"'eras' must be a list, got {type(enriched['eras']).__name__}"
        )

    def test_topics_key_exists_and_is_list(self, sample_metadata):
        """enrich_metadata must produce 'topics' as a list (importer: book.get('topics', []))."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert "topics" in enriched, "Missing 'topics' key in enriched metadata"
        assert isinstance(enriched["topics"], list), (
            f"'topics' must be a list, got {type(enriched['topics']).__name__}"
        )

    def test_literary_era_key_exists_and_is_string(self, sample_metadata):
        """enrich_metadata must produce 'literary_era' as a string."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert "literary_era" in enriched, "Missing 'literary_era' key in enriched metadata"
        assert isinstance(enriched["literary_era"], str), (
            f"'literary_era' must be a string, got {type(enriched['literary_era']).__name__}"
        )

    def test_genre_category_fields_present(self, sample_metadata):
        """enrich_metadata must produce genre_category, genre_subcategory, genre_original."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert "genre_category" in enriched
        assert "genre_subcategory" in enriched
        assert "genre_original" in enriched

    def test_all_list_elements_are_strings(self, sample_metadata):
        """All elements in genres, eras, and topics lists must be strings."""
        enriched = enrich_metadata(sample_metadata.copy())
        for field in ("genres", "eras", "topics"):
            for item in enriched[field]:
                assert isinstance(item, str), (
                    f"'{field}' contains non-string element: {item!r} ({type(item).__name__})"
                )

    def test_enriched_output_is_json_serializable(self, sample_metadata):
        """Enriched metadata must be JSON-serializable (scanner writes JSON for importer)."""
        enriched = enrich_metadata(sample_metadata.copy())
        try:
            json.dumps(enriched)
        except (TypeError, ValueError) as exc:
            pytest.fail(f"Enriched metadata is not JSON-serializable: {exc}")

    def test_genres_content_for_science_fiction(self, sample_metadata):
        """Science Fiction genre should produce a non-empty genres list with display name."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert len(enriched["genres"]) > 0
        assert "Science Fiction" in enriched["genres"]

    def test_eras_content_for_1965(self, sample_metadata):
        """Year 1965 should produce an era in the eras list."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert len(enriched["eras"]) == 1
        assert enriched["eras"][0] == "Late 20th Century (1950-1999)"

    def test_topics_content_for_description_with_keywords(self, sample_metadata):
        """Description with politics/religion/technology/society keywords should extract topics."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert len(enriched["topics"]) > 0
        # The description mentions politics, religion, technology, society, adventure
        extracted = set(enriched["topics"])
        assert "technology" in extracted
        assert "politics" in extracted
        assert "religion" in extracted
        assert "society" in extracted
        assert "adventure" in extracted


# ===========================================================================
# Test 2: End-to-end chain — enrich -> JSON -> import -> DB
# ===========================================================================


class TestEndToEndChain:
    """Verify the full pipeline: enrich_metadata -> JSON serialize -> import -> DB."""

    def test_genres_reach_database(self, sample_metadata, test_db):
        """Genres from enriched metadata must populate audiobook_genres junction table."""
        enriched = enrich_metadata(sample_metadata.copy())

        # Round-trip through JSON (as the real pipeline does)
        book = json.loads(json.dumps(enriched))

        audiobook_id = _import_book_to_db(test_db, book)

        cursor = test_db.cursor()
        cursor.execute(
            "SELECT g.name FROM genres g "
            "JOIN audiobook_genres ag ON g.id = ag.genre_id "
            "WHERE ag.audiobook_id = ?",
            (audiobook_id,),
        )
        db_genres = [row[0] for row in cursor.fetchall()]
        assert len(db_genres) > 0, "No genres found in database after import"
        assert set(db_genres) == set(enriched["genres"])

    def test_eras_reach_database(self, sample_metadata, test_db):
        """Eras from enriched metadata must populate audiobook_eras junction table."""
        enriched = enrich_metadata(sample_metadata.copy())
        book = json.loads(json.dumps(enriched))

        audiobook_id = _import_book_to_db(test_db, book)

        cursor = test_db.cursor()
        cursor.execute(
            "SELECT e.name FROM eras e "
            "JOIN audiobook_eras ae ON e.id = ae.era_id "
            "WHERE ae.audiobook_id = ?",
            (audiobook_id,),
        )
        db_eras = [row[0] for row in cursor.fetchall()]
        assert len(db_eras) > 0, "No eras found in database after import"
        assert set(db_eras) == set(enriched["eras"])

    def test_topics_reach_database(self, sample_metadata, test_db):
        """Topics from enriched metadata must populate audiobook_topics junction table."""
        enriched = enrich_metadata(sample_metadata.copy())
        book = json.loads(json.dumps(enriched))

        audiobook_id = _import_book_to_db(test_db, book)

        cursor = test_db.cursor()
        cursor.execute(
            "SELECT t.name FROM topics t "
            "JOIN audiobook_topics at2 ON t.id = at2.topic_id "
            "WHERE at2.audiobook_id = ?",
            (audiobook_id,),
        )
        db_topics = [row[0] for row in cursor.fetchall()]
        assert len(db_topics) > 0, "No topics found in database after import"
        assert set(db_topics) == set(enriched["topics"])

    def test_multiple_books_share_genre_rows(self, test_db):
        """Two books with the same genre should share a single genres row."""
        meta_a = {
            "title": "Book A",
            "author": "Author A",
            "genre": "Mystery",
            "year": "2020",
            "description": "A mystery adventure.",
            "file_path": "/audiobooks/Library/Author A/Book A/a.opus",
        }
        meta_b = {
            "title": "Book B",
            "author": "Author B",
            "genre": "Mystery",
            "year": "2021",
            "description": "Another mystery journey.",
            "file_path": "/audiobooks/Library/Author B/Book B/b.opus",
        }
        enriched_a = enrich_metadata(meta_a)
        enriched_b = enrich_metadata(meta_b)

        book_a = json.loads(json.dumps(enriched_a))
        book_b = json.loads(json.dumps(enriched_b))

        _import_book_to_db(test_db, book_a)
        _import_book_to_db(test_db, book_b)

        cursor = test_db.cursor()
        # Both should produce the same genre display name
        assert enriched_a["genres"] == enriched_b["genres"]

        # The genres table should have only one row for this genre
        cursor.execute("SELECT COUNT(*) FROM genres WHERE name = ?", (enriched_a["genres"][0],))
        assert cursor.fetchone()[0] == 1, "Shared genre should have exactly one row in genres table"

        # Both junction table entries should reference the same genre_id
        cursor.execute("SELECT DISTINCT genre_id FROM audiobook_genres")
        genre_ids = [row[0] for row in cursor.fetchall()]
        assert len(genre_ids) == 1, "Both books should reference the same genre_id"


# ===========================================================================
# Test 3: Field mismatch regression — literary_era (string) AND eras (list)
# ===========================================================================


class TestLiteraryEraRegression:
    """Regression tests for the literary_era/eras field mismatch bug.

    The original bug: scanner output 'literary_era' (string) but importer
    expected 'eras' (list). Fix: scanner now outputs BOTH.
    """

    def test_both_literary_era_and_eras_present(self, sample_metadata):
        """enrich_metadata MUST produce both 'literary_era' (str) and 'eras' (list)."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert "literary_era" in enriched, "Missing 'literary_era' — regression!"
        assert "eras" in enriched, "Missing 'eras' — regression!"
        assert isinstance(enriched["literary_era"], str)
        assert isinstance(enriched["eras"], list)

    def test_eras_list_contains_literary_era_value(self, sample_metadata):
        """The eras list should contain the literary_era string value."""
        enriched = enrich_metadata(sample_metadata.copy())
        era_string = enriched["literary_era"]
        era_list = enriched["eras"]
        if era_string:
            assert era_string in era_list, (
                f"literary_era '{era_string}' not found in eras list {era_list}"
            )

    def test_eras_list_not_string(self, sample_metadata):
        """Catch the original bug: if someone accidentally sets eras to a string."""
        enriched = enrich_metadata(sample_metadata.copy())
        assert not isinstance(enriched["eras"], str), (
            "eras must be a list, not a string — this was the original bug"
        )

    def test_importer_iterates_eras_list(self, sample_metadata, test_db):
        """Verify eras list can be iterated by the importer without error.

        The original bug caused the importer to iterate characters of a string
        instead of list elements.
        """
        enriched = enrich_metadata(sample_metadata.copy())
        book = json.loads(json.dumps(enriched))

        audiobook_id = _import_book_to_db(test_db, book)

        cursor = test_db.cursor()
        cursor.execute(
            "SELECT e.name FROM eras e "
            "JOIN audiobook_eras ae ON e.id = ae.era_id "
            "WHERE ae.audiobook_id = ?",
            (audiobook_id,),
        )
        db_eras = [row[0] for row in cursor.fetchall()]

        # Should have exactly one era entry, not individual characters
        assert len(db_eras) == 1
        # The era should be a full era name, not a single character
        assert len(db_eras[0]) > 5, (
            f"Era value '{db_eras[0]}' looks like a single character — "
            "string iteration bug may have recurred"
        )

    @pytest.mark.parametrize(
        "year,expected_era",
        [
            ("1965", "Late 20th Century (1950-1999)"),
            ("2023", "21st Century - Contemporary (2020+)"),
            ("1850", "19th Century (1800-1899)"),
            ("1750", "Classical (Pre-1800)"),
            ("1925", "Early 20th Century (1900-1949)"),
            ("2005", "21st Century - Early (2000-2009)"),
            ("2015", "21st Century - Modern (2010-2019)"),
        ],
    )
    def test_era_values_consistent_across_forms(self, year, expected_era):
        """literary_era and eras[0] must contain the same value for a given year."""
        metadata = {"genre": "Fiction", "year": year, "description": "A story."}
        enriched = enrich_metadata(metadata)
        assert enriched["literary_era"] == expected_era
        assert enriched["eras"] == [expected_era]


# ===========================================================================
# Test 4: Edge cases — empty inputs produce valid, importable output
# ===========================================================================


class TestEdgeCases:
    """Edge cases: empty or missing fields should still produce valid output."""

    def test_empty_genre(self):
        """Empty genre string should produce empty genres list (uncategorized)."""
        metadata = {"genre": "", "year": "2020", "description": "A story."}
        enriched = enrich_metadata(metadata)
        assert isinstance(enriched["genres"], list)
        assert enriched["genre_category"] == "uncategorized"
        # Uncategorized produces empty genres list (no DB entry for "uncategorized")
        assert enriched["genres"] == []

    def test_empty_year(self):
        """Empty year should produce 'Unknown Era' literary_era and eras list."""
        metadata = {"genre": "Fiction", "year": "", "description": "A story."}
        enriched = enrich_metadata(metadata)
        assert enriched["literary_era"] == "Unknown Era"
        assert enriched["eras"] == ["Unknown Era"]
        assert isinstance(enriched["eras"], list)

    def test_missing_year_key(self):
        """Missing year key entirely should produce Unknown Era."""
        metadata = {"genre": "Fiction", "description": "A story."}
        enriched = enrich_metadata(metadata)
        assert enriched["literary_era"] == "Unknown Era"
        assert enriched["eras"] == ["Unknown Era"]

    def test_empty_description(self):
        """Empty description should produce ['general'] topics list."""
        metadata = {"genre": "Fiction", "year": "2020", "description": ""}
        enriched = enrich_metadata(metadata)
        assert isinstance(enriched["topics"], list)
        assert enriched["topics"] == ["general"]

    def test_missing_description_key(self):
        """Missing description key should produce ['general'] topics list."""
        metadata = {"genre": "Fiction", "year": "2020"}
        enriched = enrich_metadata(metadata)
        assert isinstance(enriched["topics"], list)
        assert enriched["topics"] == ["general"]

    def test_content_type_genre_produces_empty_genres(self):
        """Genre 'Audiobook' (a content type, not genre) should produce empty genres list."""
        metadata = {"genre": "Audiobook", "year": "2020", "description": "A story."}
        enriched = enrich_metadata(metadata)
        assert enriched["genres"] == []
        assert enriched["genre_category"] == "uncategorized"

    def test_all_empty_fields_importable(self, test_db):
        """Metadata with all empty enrichable fields should import without error."""
        metadata = {
            "title": "Minimal Book",
            "author": "Unknown",
            "genre": "",
            "year": "",
            "description": "",
            "file_path": "/audiobooks/Library/Unknown/Minimal/minimal.opus",
        }
        enriched = enrich_metadata(metadata)
        book = json.loads(json.dumps(enriched))

        # Should not raise — empty lists produce no junction rows
        audiobook_id = _import_book_to_db(test_db, book)

        cursor = test_db.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM audiobook_genres WHERE audiobook_id = ?", (audiobook_id,)
        )
        assert cursor.fetchone()[0] == 0, "Empty genre should produce zero genre junction rows"

        # Empty year still produces an "Unknown Era" entry
        cursor.execute(
            "SELECT COUNT(*) FROM audiobook_eras WHERE audiobook_id = ?", (audiobook_id,)
        )
        assert cursor.fetchone()[0] == 1

        # Empty description produces "general" topic
        cursor.execute(
            "SELECT COUNT(*) FROM audiobook_topics WHERE audiobook_id = ?", (audiobook_id,)
        )
        assert cursor.fetchone()[0] == 1

    def test_none_genre_field(self):
        """None as genre should not crash enrich_metadata."""
        metadata = {"genre": None, "year": "2020", "description": "A story."}
        enriched = enrich_metadata(metadata)
        assert isinstance(enriched["genres"], list)


# ===========================================================================
# Test 5: Component function contracts (building blocks of enrich_metadata)
# ===========================================================================


class TestComponentContracts:
    """Test individual functions that enrich_metadata delegates to."""

    def test_categorize_genre_returns_expected_keys(self):
        """categorize_genre must return dict with 'main', 'sub', 'original'."""
        result = categorize_genre("Science Fiction")
        assert "main" in result
        assert "sub" in result
        assert "original" in result

    def test_build_genres_list_returns_list(self):
        """build_genres_list must always return a list."""
        genre_cat = categorize_genre("Fantasy")
        result = build_genres_list(genre_cat)
        assert isinstance(result, list)

    def test_determine_literary_era_returns_string(self):
        """determine_literary_era must always return a string."""
        result = determine_literary_era("2020")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_extract_topics_returns_list_of_strings(self):
        """extract_topics must always return a list of strings."""
        result = extract_topics("A story about war and technology")
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)

    def test_extract_topics_with_no_matches_returns_general(self):
        """extract_topics with no keyword matches should return ['general']."""
        result = extract_topics("A simple tale of nothing noteworthy.")
        assert result == ["general"]

    def test_determine_literary_era_invalid_year(self):
        """determine_literary_era with non-numeric year returns 'Unknown Era'."""
        assert determine_literary_era("not-a-year") == "Unknown Era"
        assert determine_literary_era("") == "Unknown Era"
