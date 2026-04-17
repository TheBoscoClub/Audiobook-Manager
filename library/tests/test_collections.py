"""
Tests for the dynamic collections system (v8).

Tests cover: slugify, query builders, dynamic collection building,
caching, cache invalidation, and the API endpoint.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from backend.api_modular.collections import (  # noqa: E402
    FICTION_GENRES,
    NONFICTION_GENRES,
    SPECIAL_COLLECTIONS,
    _build_dynamic_collections,
    _era_query,
    _genre_query,
    _multi_genre_query,
    _series_query,
    _slugify,
    _topic_query,
    get_collections_lookup,
    invalidate_collections_cache,
)


# ─── Helper: create a minimal library DB with enrichment tables ──────────────


def create_test_library_db(path: str) -> None:
    """Create a minimal library database with enrichment tables for testing."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            narrator TEXT,
            series TEXT,
            content_type TEXT DEFAULT 'Product',
            publisher TEXT,
            format TEXT DEFAULT 'opus'
        );

        CREATE TABLE IF NOT EXISTS genres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audiobook_genres (
            audiobook_id INTEGER NOT NULL,
            genre_id INTEGER NOT NULL,
            PRIMARY KEY (audiobook_id, genre_id)
        );

        CREATE TABLE IF NOT EXISTS eras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audiobook_eras (
            audiobook_id INTEGER NOT NULL,
            era_id INTEGER NOT NULL,
            PRIMARY KEY (audiobook_id, era_id)
        );

        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audiobook_topics (
            audiobook_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            PRIMARY KEY (audiobook_id, topic_id)
        );
    """)

    # Insert sample audiobooks
    books = [
        ("The Great Gatsby", "F. Scott Fitzgerald", None, None, "Product"),
        ("Dune", "Frank Herbert", None, "Dune Chronicles", "Product"),
        ("A Brief History of Time", "Stephen Hawking", None, None, "Product"),
        ("Murder on the Orient Express", "Agatha Christie", None, None, "Product"),
        ("The Daily", "The New York Times", None, None, "Podcast"),
        ("Intro to Physics", "The Great Courses", None, "Great Courses", "Lecture"),
    ]
    for title, author, narrator, series, ctype in books:
        conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, series, content_type) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, author, narrator, series, ctype),
        )

    # Insert genres
    genres = [
        ("Literary Fiction", [1]),  # Gatsby
        ("Science Fiction", [2]),  # Dune
        ("Science", [3]),  # Brief History
        ("Mystery", [4]),  # Murder
    ]
    for genre_name, book_ids in genres:
        conn.execute("INSERT INTO genres (name) VALUES (?)", (genre_name,))
        genre_id = conn.execute("SELECT id FROM genres WHERE name = ?", (genre_name,)).fetchone()[0]
        for bid in book_ids:
            conn.execute(
                "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
                (bid, genre_id),
            )

    # Insert eras
    eras = [
        ("Jazz Age", [1]),  # Gatsby
        ("Space Age", [2]),  # Dune
    ]
    for era_name, book_ids in eras:
        conn.execute("INSERT INTO eras (name) VALUES (?)", (era_name,))
        era_id = conn.execute("SELECT id FROM eras WHERE name = ?", (era_name,)).fetchone()[0]
        for bid in book_ids:
            conn.execute(
                "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)", (bid, era_id)
            )

    # Insert topics
    topics = [("American Dream", [1]), ("Space Exploration", [2, 3])]
    for topic_name, book_ids in topics:
        conn.execute("INSERT INTO topics (name) VALUES (?)", (topic_name,))
        topic_id = conn.execute("SELECT id FROM topics WHERE name = ?", (topic_name,)).fetchone()[0]
        for bid in book_ids:
            conn.execute(
                "INSERT INTO audiobook_topics (audiobook_id, topic_id) VALUES (?, ?)",
                (bid, topic_id),
            )

    conn.commit()
    conn.close()


@pytest.fixture
def test_db_path():
    """Create a temporary library database with enrichment data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    create_test_library_db(path)
    yield path
    os.unlink(path)


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure cache is cleared between tests."""
    invalidate_collections_cache()
    yield
    invalidate_collections_cache()


# ─── Unit tests: _slugify ────────────────────────────────────────────────────


class TestSlugify:
    def test_basic(self):
        assert _slugify("Science Fiction") == "science-fiction"

    def test_special_chars(self):
        assert _slugify("Biographies & Memoirs") == "biographies-memoirs"

    def test_leading_trailing_spaces(self):
        assert _slugify("  Fantasy  ") == "fantasy"

    def test_multiple_special_chars(self):
        assert _slugify("True Crime / Mystery") == "true-crime-mystery"

    def test_single_word(self):
        assert _slugify("Horror") == "horror"


# ─── Unit tests: query builders ──────────────────────────────────────────────


class TestQueryBuilders:
    def test_genre_query_produces_valid_sql(self):
        q = _genre_query("Science Fiction")
        assert "g.name = 'Science Fiction'" in q
        assert "audiobook_genres" in q

    def test_genre_query_escapes_quotes(self):
        q = _genre_query("O'Brien's Tales")
        assert "O''Brien''s Tales" in q

    def test_multi_genre_query(self):
        q = _multi_genre_query(["Mystery", "Horror"])
        assert "g.name = 'Mystery'" in q
        assert "g.name = 'Horror'" in q
        assert "OR" in q
        assert "DISTINCT" in q

    def test_era_query(self):
        q = _era_query("Jazz Age")
        assert "e.name = 'Jazz Age'" in q
        assert "audiobook_eras" in q

    def test_topic_query(self):
        q = _topic_query("Space Exploration")
        assert "t.name = 'Space Exploration'" in q
        assert "audiobook_topics" in q

    def test_series_query(self):
        q = _series_query("Dune Chronicles")
        assert "series = 'Dune Chronicles'" in q


# ─── Unit tests: genre classification ────────────────────────────────────────


class TestGenreClassification:
    def test_fiction_genres_are_frozenset(self):
        assert isinstance(FICTION_GENRES, frozenset)

    def test_nonfiction_genres_are_frozenset(self):
        assert isinstance(NONFICTION_GENRES, frozenset)

    def test_no_overlap(self):
        assert len(FICTION_GENRES & NONFICTION_GENRES) == 0

    def test_known_fiction(self):
        assert "Mystery" in FICTION_GENRES
        assert "Science Fiction" in FICTION_GENRES
        assert "Fantasy" in FICTION_GENRES

    def test_known_nonfiction(self):
        assert "History" in NONFICTION_GENRES
        assert "Science" in NONFICTION_GENRES
        assert "True Crime" in NONFICTION_GENRES


# ─── Integration tests: _build_dynamic_collections ───────────────────────────


class TestBuildDynamicCollections:
    def test_returns_tree_and_flat(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        assert isinstance(tree, list)
        assert isinstance(flat, dict)
        assert len(tree) > 0
        assert len(flat) > 0

    def test_special_collections_present(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        special_ids = {s["id"] for s in SPECIAL_COLLECTIONS}
        tree_ids = {n["id"] for n in tree}
        assert special_ids.issubset(tree_ids)
        for sid in special_ids:
            assert sid in flat
            assert flat[sid]["bypasses_filter"] is True

    def test_fiction_category_present(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        assert "fiction" in flat
        fiction_nodes = [n for n in tree if n["id"] == "fiction"]
        assert len(fiction_nodes) == 1
        assert fiction_nodes[0]["category"] == "fiction"
        # Literary Fiction, Science Fiction, Mystery should be children
        child_names = {c["name"] for c in fiction_nodes[0].get("children", [])}
        assert "Literary Fiction" in child_names
        assert "Science Fiction" in child_names
        assert "Mystery" in child_names

    def test_nonfiction_category_present(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        assert "nonfiction" in flat
        nonfiction_nodes = [n for n in tree if n["id"] == "nonfiction"]
        assert len(nonfiction_nodes) == 1
        child_names = {c["name"] for c in nonfiction_nodes[0].get("children", [])}
        assert "Science" in child_names

    def test_series_present(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        assert "series" in flat
        # Dune Chronicles should be a child
        series_node = [n for n in tree if n["id"] == "series"][0]
        child_names = {c["name"] for c in series_node.get("children", [])}
        assert "Dune Chronicles" in child_names

    def test_eras_present(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        assert "eras" in flat
        era_node = [n for n in tree if n["id"] == "eras"][0]
        child_names = {c["name"] for c in era_node.get("children", [])}
        assert "Jazz Age" in child_names
        assert "Space Age" in child_names

    def test_topics_present(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        assert "topics" in flat
        topic_node = [n for n in tree if n["id"] == "topics"][0]
        child_names = {c["name"] for c in topic_node.get("children", [])}
        assert "American Dream" in child_names
        assert "Space Exploration" in child_names

    def test_flat_lookup_has_all_children(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        # Every child in tree should be in flat
        for node in tree:
            for child in node.get("children", []):
                assert child["id"] in flat, f"Child {child['id']} missing from flat"

    def test_genre_children_have_counts(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        fiction_node = [n for n in tree if n["id"] == "fiction"][0]
        for child in fiction_node["children"]:
            assert "count" in child
            assert child["count"] > 0

    def test_series_children_have_content_type(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()

        series_node = [n for n in tree if n["id"] == "series"][0]
        for child in series_node["children"]:
            assert "content_type" in child


# ─── Integration tests: caching ──────────────────────────────────────────────


class TestCollectionsCache:
    def test_get_collections_lookup_returns_dict(self, test_db_path):
        result = get_collections_lookup(test_db_path)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_cache_returns_same_object(self, test_db_path):
        result1 = get_collections_lookup(test_db_path)
        result2 = get_collections_lookup(test_db_path)
        assert result1 is result2  # Same object = cached

    def test_invalidate_clears_cache(self, test_db_path):
        result1 = get_collections_lookup(test_db_path)
        invalidate_collections_cache()
        result2 = get_collections_lookup(test_db_path)
        assert result1 is not result2  # Different object = rebuilt

    def test_different_db_path_rebuilds(self, test_db_path):
        # Create a second DB
        fd, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        create_test_library_db(path2)
        try:
            result1 = get_collections_lookup(test_db_path)
            result2 = get_collections_lookup(path2)
            assert result1 is not result2
        finally:
            os.unlink(path2)


# ─── Integration tests: SQL queries execute correctly ────────────────────────


class TestQueryExecution:
    def test_genre_query_executes(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        q = _genre_query("Mystery")
        rows = conn.execute(f"SELECT id FROM audiobooks WHERE {q}").fetchall()  # nosec B608
        conn.close()
        assert len(rows) == 1  # Murder on the Orient Express

    def test_multi_genre_query_executes(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        q = _multi_genre_query(["Mystery", "Science Fiction"])
        rows = conn.execute(f"SELECT id FROM audiobooks WHERE {q}").fetchall()  # nosec B608
        conn.close()
        assert len(rows) == 2  # Dune + Murder

    def test_era_query_executes(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        q = _era_query("Jazz Age")
        rows = conn.execute(f"SELECT id FROM audiobooks WHERE {q}").fetchall()  # nosec B608
        conn.close()
        assert len(rows) == 1  # Gatsby

    def test_topic_query_executes(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        q = _topic_query("Space Exploration")
        rows = conn.execute(f"SELECT id FROM audiobooks WHERE {q}").fetchall()  # nosec B608
        conn.close()
        assert len(rows) == 2  # Dune + Brief History

    def test_series_query_executes(self, test_db_path):
        conn = sqlite3.connect(test_db_path)
        q = _series_query("Dune Chronicles")
        rows = conn.execute(f"SELECT id FROM audiobooks WHERE {q}").fetchall()  # nosec B608
        conn.close()
        assert len(rows) == 1  # Dune


# ─── Empty database edge case ────────────────────────────────────────────────


class TestEmptyDatabase:
    def test_empty_enrichment_tables(self):
        """Collections should still work with no enrichment data."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT,
                author TEXT, series TEXT, content_type TEXT);
            CREATE TABLE genres (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE audiobook_genres (audiobook_id INTEGER, genre_id INTEGER);
            CREATE TABLE eras (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE audiobook_eras (audiobook_id INTEGER, era_id INTEGER);
            CREATE TABLE topics (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE audiobook_topics (audiobook_id INTEGER, topic_id INTEGER);
        """)
        cursor = conn.cursor()
        tree, flat = _build_dynamic_collections(cursor)
        conn.close()
        os.unlink(path)

        # Only special collections should be present
        assert len(tree) == len(SPECIAL_COLLECTIONS)
        assert "fiction" not in flat
        assert "nonfiction" not in flat
        assert "series" not in flat
        assert "eras" not in flat
        assert "topics" not in flat
