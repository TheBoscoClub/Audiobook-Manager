"""
Behavioral tests for the audiobooks API.

Unlike test_api.py (which checks HTTP status codes against an empty database),
these tests seed diverse data and verify that filtering, sorting, pagination,
and search actually produce correct results.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Resolve paths the same way conftest does
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

SCHEMA_PATH = LIBRARY_DIR / "backend" / "schema.sql"


def _init_test_database(db_path: Path) -> None:
    """Initialize a test database with the schema."""
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_BOOKS = [
    {
        "id": 1,
        "title": "Dune",
        "author": "Frank Herbert",
        "author_last_name": "Herbert",
        "author_first_name": "Frank",
        "narrator": "Scott Brick",
        "narrator_last_name": "Brick",
        "narrator_first_name": "Scott",
        "publisher": "Macmillan Audio",
        "series": "Dune Chronicles",
        "series_sequence": 1,
        "duration_hours": 21.0,
        "file_path": "/test/dune.opus",
        "format": "opus",
        "published_year": 1965,
        "content_type": "Product",
        "acquired_date": "2025-06-01",
        "file_size_mb": 500.0,
    },
    {
        "id": 2,
        "title": "Dune Messiah",
        "author": "Frank Herbert",
        "author_last_name": "Herbert",
        "author_first_name": "Frank",
        "narrator": "Scott Brick",
        "narrator_last_name": "Brick",
        "narrator_first_name": "Scott",
        "publisher": "Macmillan Audio",
        "series": "Dune Chronicles",
        "series_sequence": 2,
        "duration_hours": 8.5,
        "file_path": "/test/dune_messiah.opus",
        "format": "opus",
        "published_year": 1969,
        "content_type": "Product",
        "acquired_date": "2025-06-15",
        "file_size_mb": 200.0,
    },
    {
        "id": 3,
        "title": "Neuromancer",
        "author": "William Gibson",
        "author_last_name": "Gibson",
        "author_first_name": "William",
        "narrator": "Robertson Dean",
        "narrator_last_name": "Dean",
        "narrator_first_name": "Robertson",
        "publisher": "Brilliance Audio",
        "series": None,
        "series_sequence": None,
        "duration_hours": 6.8,
        "file_path": "/test/neuromancer.m4b",
        "format": "m4b",
        "published_year": 1984,
        "content_type": "Product",
        "acquired_date": "2025-07-01",
        "file_size_mb": 180.0,
    },
    {
        "id": 4,
        "title": "Project Hail Mary",
        "author": "Andy Weir",
        "author_last_name": "Weir",
        "author_first_name": "Andy",
        "narrator": "Ray Porter",
        "narrator_last_name": "Porter",
        "narrator_first_name": "Ray",
        "publisher": "Audible Studios",
        "series": None,
        "series_sequence": None,
        "duration_hours": 16.1,
        "file_path": "/test/hail_mary.opus",
        "format": "opus",
        "published_year": 2021,
        "content_type": "Product",
        "acquired_date": "2025-08-10",
        "file_size_mb": 400.0,
    },
    {
        "id": 5,
        "title": "The Hobbit",
        "author": "J.R.R. Tolkien",
        "author_last_name": "Tolkien",
        "author_first_name": "J.R.R.",
        "narrator": "Rob Inglis",
        "narrator_last_name": "Inglis",
        "narrator_first_name": "Rob",
        "publisher": "Recorded Books",
        "series": "Middle-earth",
        "series_sequence": 0,
        "duration_hours": 11.0,
        "file_path": "/test/hobbit.m4b",
        "format": "m4b",
        "published_year": 1937,
        "content_type": "Product",
        "acquired_date": "2025-05-01",
        "file_size_mb": 300.0,
    },
    {
        "id": 6,
        "title": "Foundation",
        "author": "Isaac Asimov",
        "author_last_name": "Asimov",
        "author_first_name": "Isaac",
        "narrator": "Scott Brick",
        "narrator_last_name": "Brick",
        "narrator_first_name": "Scott",
        "publisher": "Random House Audio",
        "series": "Foundation",
        "series_sequence": 1,
        "duration_hours": 8.3,
        "file_path": "/test/foundation.opus",
        "format": "opus",
        "published_year": 1951,
        "content_type": "Product",
        "acquired_date": "2025-09-01",
        "file_size_mb": 210.0,
    },
    {
        "id": 7,
        "title": "Sapiens",
        "author": "Yuval Noah Harari",
        "author_last_name": "Harari",
        "author_first_name": "Yuval Noah",
        "narrator": "Derek Perkins",
        "narrator_last_name": "Perkins",
        "narrator_first_name": "Derek",
        "publisher": "Harper Audio",
        "series": None,
        "series_sequence": None,
        "duration_hours": 15.2,
        "file_path": "/test/sapiens.mp3",
        "format": "mp3",
        "published_year": 2011,
        "content_type": "Product",
        "acquired_date": "2025-10-01",
        "file_size_mb": 450.0,
    },
    {
        "id": 8,
        "title": "Daily News Podcast",
        "author": "News Corp",
        "author_last_name": "Corp",
        "author_first_name": "News",
        "narrator": "Various",
        "narrator_last_name": "Various",
        "narrator_first_name": None,
        "publisher": "News Corp",
        "series": None,
        "series_sequence": None,
        "duration_hours": 0.5,
        "file_path": "/test/podcast.opus",
        "format": "opus",
        "published_year": 2025,
        "content_type": "Podcast",
        "acquired_date": "2025-11-01",
        "file_size_mb": 15.0,
    },
]

SEED_AUTHORS = [
    {"id": 1, "name": "Frank Herbert", "sort_name": "Herbert, Frank"},
    {"id": 2, "name": "William Gibson", "sort_name": "Gibson, William"},
    {"id": 3, "name": "Andy Weir", "sort_name": "Weir, Andy"},
    {"id": 4, "name": "J.R.R. Tolkien", "sort_name": "Tolkien, J.R.R."},
    {"id": 5, "name": "Isaac Asimov", "sort_name": "Asimov, Isaac"},
    {"id": 6, "name": "Yuval Noah Harari", "sort_name": "Harari, Yuval Noah"},
    {"id": 7, "name": "News Corp", "sort_name": "Corp, News"},
]

SEED_NARRATORS = [
    {"id": 1, "name": "Scott Brick", "sort_name": "Brick, Scott"},
    {"id": 2, "name": "Robertson Dean", "sort_name": "Dean, Robertson"},
    {"id": 3, "name": "Ray Porter", "sort_name": "Porter, Ray"},
    {"id": 4, "name": "Rob Inglis", "sort_name": "Inglis, Rob"},
    {"id": 5, "name": "Derek Perkins", "sort_name": "Perkins, Derek"},
    {"id": 6, "name": "Various", "sort_name": "Various"},
]

# book_id -> author_id
SEED_BOOK_AUTHORS = [
    (1, 1),
    (2, 1),
    (3, 2),
    (4, 3),
    (5, 4),
    (6, 5),
    (7, 6),
    (8, 7),
]

# book_id -> narrator_id
SEED_BOOK_NARRATORS = [
    (1, 1),
    (2, 1),
    (3, 2),
    (4, 3),
    (5, 4),
    (6, 1),
    (7, 5),
    (8, 6),
]

SEED_GENRES = [
    {"id": 1, "name": "Science Fiction"},
    {"id": 2, "name": "Fantasy"},
    {"id": 3, "name": "Nonfiction"},
]

# audiobook_id -> genre_id
SEED_AUDIOBOOK_GENRES = [
    (1, 1),
    (2, 1),
    (3, 1),
    (4, 1),
    (5, 2),
    (6, 1),
    (7, 3),
]

SEED_ERAS = [
    {"id": 1, "name": "20th Century"},
    {"id": 2, "name": "21st Century"},
]

SEED_AUDIOBOOK_ERAS = [
    (1, 1),
    (2, 1),
    (3, 1),
    (5, 1),
    (6, 1),
    (4, 2),
    (7, 2),
]


def _seed_database(db_path: Path) -> None:
    """Insert diverse test data into the database."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Audiobooks
    cols = [
        "id",
        "title",
        "author",
        "author_last_name",
        "author_first_name",
        "narrator",
        "narrator_last_name",
        "narrator_first_name",
        "publisher",
        "series",
        "series_sequence",
        "duration_hours",
        "file_path",
        "format",
        "published_year",
        "content_type",
        "acquired_date",
        "file_size_mb",
    ]
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    for book in SEED_BOOKS:
        vals = [book.get(c) for c in cols]
        cur.execute(
            f"INSERT INTO audiobooks ({col_names}) VALUES ({placeholders})", vals  # nosec B608  # test SQL uses hardcoded identifiers
        )

    # Authors & narrators (normalized)
    for a in SEED_AUTHORS:
        cur.execute(
            "INSERT INTO authors (id, name, sort_name) VALUES (?, ?, ?)",
            (a["id"], a["name"], a["sort_name"]),
        )
    for n in SEED_NARRATORS:
        cur.execute(
            "INSERT INTO narrators (id, name, sort_name) VALUES (?, ?, ?)",
            (n["id"], n["name"], n["sort_name"]),
        )

    for book_id, author_id in SEED_BOOK_AUTHORS:
        cur.execute(
            "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, 0)",
            (book_id, author_id),
        )
    for book_id, narrator_id in SEED_BOOK_NARRATORS:
        cur.execute(
            "INSERT INTO book_narrators (book_id, narrator_id, position) VALUES (?, ?, 0)",
            (book_id, narrator_id),
        )

    # Genres
    for g in SEED_GENRES:
        cur.execute("INSERT INTO genres (id, name) VALUES (?, ?)", (g["id"], g["name"]))
    for ab_id, g_id in SEED_AUDIOBOOK_GENRES:
        cur.execute(
            "INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (?, ?)",
            (ab_id, g_id),
        )

    # Eras
    for e in SEED_ERAS:
        cur.execute("INSERT INTO eras (id, name) VALUES (?, ?)", (e["id"], e["name"]))
    for ab_id, e_id in SEED_AUDIOBOOK_ERAS:
        cur.execute(
            "INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (?, ?)",
            (ab_id, e_id),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def populated_app():
    """Flask app backed by a database with diverse seed data."""
    from backend.api_modular import create_app

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "test_behavioral.db"
        supplements_dir = tmpdir / "supplements"
        supplements_dir.mkdir()

        _init_test_database(db_path)
        _seed_database(db_path)

        app = create_app(
            database_path=db_path,
            project_dir=tmpdir,
            supplements_dir=supplements_dir,
            api_port=5098,
        )
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def client(populated_app):
    """Test client for the populated app."""
    with populated_app.test_client() as c:
        yield c


def _get(client, path):
    """Helper: GET path and return parsed JSON."""
    resp = client.get(path)
    assert resp.status_code == 200, f"GET {path} returned {resp.status_code}"
    return json.loads(resp.data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Number of Product/NULL content_type books (excludes Podcast id=8)
AUDIOBOOK_COUNT = 7


# ===========================================================================
# FILTERING TESTS
# ===========================================================================


class TestFilterByAuthor:
    """Verify author filter returns only books by that author."""

    def test_single_author_match(self, client):
        data = _get(client, "/api/audiobooks?author=Frank Herbert")
        books = data["audiobooks"]
        assert len(books) == 2
        titles = {b["title"] for b in books}
        assert titles == {"Dune", "Dune Messiah"}

    def test_author_no_match(self, client):
        data = _get(client, "/api/audiobooks?author=Nonexistent Author")
        assert len(data["audiobooks"]) == 0

    def test_all_returned_books_have_correct_author(self, client):
        data = _get(client, "/api/audiobooks?author=Andy Weir")
        for book in data["audiobooks"]:
            assert book["author"] == "Andy Weir"


class TestFilterByNarrator:
    """Verify narrator filter returns only books narrated by that person."""

    def test_narrator_with_multiple_books(self, client):
        """Scott Brick narrates Dune, Dune Messiah, and Foundation."""
        data = _get(client, "/api/audiobooks?narrator=Scott Brick")
        books = data["audiobooks"]
        assert len(books) == 3
        titles = {b["title"] for b in books}
        assert titles == {"Dune", "Dune Messiah", "Foundation"}

    def test_narrator_single_book(self, client):
        data = _get(client, "/api/audiobooks?narrator=Ray Porter")
        books = data["audiobooks"]
        assert len(books) == 1
        assert books[0]["title"] == "Project Hail Mary"


class TestFilterByFormat:
    """Verify format filter returns only books in that format."""

    def test_opus_filter(self, client):
        data = _get(client, "/api/audiobooks?format=opus")
        books = data["audiobooks"]
        # Opus audiobooks: Dune, Dune Messiah, Hail Mary, Foundation (Podcast excluded by AUDIOBOOK_FILTER)
        assert len(books) == 4
        for book in books:
            assert book["format"] == "opus"

    def test_m4b_filter(self, client):
        data = _get(client, "/api/audiobooks?format=m4b")
        books = data["audiobooks"]
        assert len(books) == 2
        for book in books:
            assert book["format"] == "m4b"

    def test_mp3_filter(self, client):
        data = _get(client, "/api/audiobooks?format=mp3")
        books = data["audiobooks"]
        assert len(books) == 1
        assert books[0]["title"] == "Sapiens"


class TestFilterByGenre:
    """Verify genre filter returns only books in that genre."""

    def test_scifi_genre(self, client):
        data = _get(client, "/api/audiobooks?genre=Science Fiction")
        books = data["audiobooks"]
        # Science Fiction: Dune, Dune Messiah, Neuromancer, Hail Mary, Foundation
        assert len(books) == 5
        titles = {b["title"] for b in books}
        assert "Dune" in titles
        assert "Neuromancer" in titles
        assert "Foundation" in titles

    def test_fantasy_genre(self, client):
        data = _get(client, "/api/audiobooks?genre=Fantasy")
        books = data["audiobooks"]
        assert len(books) == 1
        assert books[0]["title"] == "The Hobbit"

    def test_nonfiction_genre(self, client):
        data = _get(client, "/api/audiobooks?genre=Nonfiction")
        books = data["audiobooks"]
        assert len(books) == 1
        assert books[0]["title"] == "Sapiens"


class TestFilterByPublisher:
    """Verify publisher filter uses LIKE matching."""

    def test_publisher_exact(self, client):
        data = _get(client, "/api/audiobooks?publisher=Macmillan Audio")
        books = data["audiobooks"]
        assert len(books) == 2
        for book in books:
            assert "Macmillan Audio" in book["publisher"]

    def test_publisher_partial(self, client):
        data = _get(client, "/api/audiobooks?publisher=Audio")
        books = data["audiobooks"]
        # "Macmillan Audio", "Brilliance Audio", "Random House Audio", "Harper Audio", "Audible Studios"
        assert len(books) >= 4


class TestContentTypeFiltering:
    """AUDIOBOOK_FILTER should exclude non-Product content types."""

    def test_podcast_excluded_from_default_listing(self, client):
        data = _get(client, "/api/audiobooks?per_page=200")
        books = data["audiobooks"]
        titles = {b["title"] for b in books}
        assert "Daily News Podcast" not in titles
        assert len(books) == AUDIOBOOK_COUNT


# ===========================================================================
# COMBINED FILTER TESTS
# ===========================================================================


class TestCombinedFilters:
    """Verify multiple filters intersect correctly."""

    def test_author_and_format(self, client):
        data = _get(client, "/api/audiobooks?author=Frank Herbert&format=opus")
        books = data["audiobooks"]
        assert len(books) == 2
        for book in books:
            assert book["author"] == "Frank Herbert"
            assert book["format"] == "opus"

    def test_narrator_and_genre(self, client):
        """Scott Brick + Science Fiction -> Dune, Dune Messiah, Foundation."""
        data = _get(
            client,
            "/api/audiobooks?narrator=Scott Brick&genre=Science Fiction",
        )
        books = data["audiobooks"]
        assert len(books) == 3

    def test_format_and_genre_no_overlap(self, client):
        """mp3 + Fantasy -> no books (Sapiens is mp3/Nonfiction, Hobbit is m4b/Fantasy)."""
        data = _get(client, "/api/audiobooks?format=mp3&genre=Fantasy")
        assert len(data["audiobooks"]) == 0

    def test_author_and_narrator_intersect(self, client):
        """Frank Herbert + Scott Brick -> Dune + Dune Messiah."""
        data = _get(
            client,
            "/api/audiobooks?author=Frank Herbert&narrator=Scott Brick",
        )
        books = data["audiobooks"]
        assert len(books) == 2


# ===========================================================================
# SORTING TESTS
# ===========================================================================


class TestSortByTitle:
    """Default sort is by title ascending."""

    def test_title_asc(self, client):
        data = _get(client, "/api/audiobooks?sort=title&order=asc&per_page=200")
        titles = [b["title"] for b in data["audiobooks"]]
        assert titles == sorted(titles, key=str.lower)

    def test_title_desc(self, client):
        data = _get(client, "/api/audiobooks?sort=title&order=desc&per_page=200")
        titles = [b["title"] for b in data["audiobooks"]]
        assert titles == sorted(titles, key=str.lower, reverse=True)


class TestSortByDuration:
    """Sort by duration_hours produces numerically ordered results."""

    def test_duration_asc(self, client):
        data = _get(
            client, "/api/audiobooks?sort=duration_hours&order=asc&per_page=200"
        )
        durations = [b["duration_hours"] for b in data["audiobooks"]]
        assert durations == sorted(durations)

    def test_duration_desc(self, client):
        data = _get(
            client, "/api/audiobooks?sort=duration_hours&order=desc&per_page=200"
        )
        durations = [b["duration_hours"] for b in data["audiobooks"]]
        assert durations == sorted(durations, reverse=True)


class TestSortByPublishedYear:
    """Sort by published_year produces chronologically ordered results."""

    def test_year_asc(self, client):
        data = _get(
            client,
            "/api/audiobooks?sort=published_year&order=asc&per_page=200",
        )
        years = [b["published_year"] for b in data["audiobooks"]]
        assert years == sorted(years)

    def test_year_desc(self, client):
        data = _get(
            client,
            "/api/audiobooks?sort=published_year&order=desc&per_page=200",
        )
        years = [b["published_year"] for b in data["audiobooks"]]
        assert years == sorted(years, reverse=True)


class TestSortBySeries:
    """Sort by series shows only books with a series, ordered by name then sequence."""

    def test_series_sort_excludes_non_series(self, client):
        data = _get(client, "/api/audiobooks?sort=series&order=asc&per_page=200")
        books = data["audiobooks"]
        # Only books with series: Dune(1), Dune Messiah(2), The Hobbit(0), Foundation(1)
        for book in books:
            assert book["series"] is not None

    def test_series_sort_order(self, client):
        data = _get(client, "/api/audiobooks?sort=series&order=asc&per_page=200")
        books = data["audiobooks"]
        # Within the same series, sequence should be ascending
        series_groups = {}
        for b in books:
            series_groups.setdefault(b["series"], []).append(b["series_sequence"])
        for series_name, sequences in series_groups.items():
            assert sequences == sorted(sequences), (
                f"Series '{series_name}' not in sequence order: {sequences}"
            )


class TestSortByAuthorLast:
    """Sort by author_last sorts by last name with NULLs at the end."""

    def test_author_last_asc(self, client):
        data = _get(
            client,
            "/api/audiobooks?sort=author_last&order=asc&per_page=200",
        )
        last_names = [
            b["author_last_name"]
            for b in data["audiobooks"]
            if b["author_last_name"] is not None
        ]
        assert last_names == sorted(last_names, key=str.lower)


class TestSortByFileSize:
    """Sort by file_size_mb produces numerically ordered results."""

    def test_file_size_asc(self, client):
        data = _get(
            client,
            "/api/audiobooks?sort=file_size_mb&order=asc&per_page=200",
        )
        sizes = [b["file_size_mb"] for b in data["audiobooks"]]
        assert sizes == sorted(sizes)


# ===========================================================================
# PAGINATION TESTS
# ===========================================================================


class TestPagination:
    """Verify pagination controls work correctly."""

    def test_per_page_respected(self, client):
        data = _get(client, "/api/audiobooks?per_page=3&page=1")
        assert len(data["audiobooks"]) == 3

    def test_different_pages_return_different_books(self, client):
        page1 = _get(client, "/api/audiobooks?per_page=3&page=1&sort=title&order=asc")
        page2 = _get(client, "/api/audiobooks?per_page=3&page=2&sort=title&order=asc")
        ids_p1 = {b["id"] for b in page1["audiobooks"]}
        ids_p2 = {b["id"] for b in page2["audiobooks"]}
        assert ids_p1.isdisjoint(ids_p2), "Page 1 and page 2 share books"

    def test_all_pages_cover_all_books(self, client):
        """Walking through all pages should yield every audiobook exactly once."""
        all_ids = set()
        page = 1
        while True:
            data = _get(client, f"/api/audiobooks?per_page=3&page={page}&sort=title")
            books = data["audiobooks"]
            if not books:
                break
            for b in books:
                assert b["id"] not in all_ids, f"Book {b['id']} appeared twice"
                all_ids.add(b["id"])
            page += 1
        assert len(all_ids) == AUDIOBOOK_COUNT

    def test_pagination_metadata(self, client):
        data = _get(client, "/api/audiobooks?per_page=3&page=1")
        pag = data["pagination"]
        assert pag["page"] == 1
        assert pag["per_page"] == 3
        assert pag["total_count"] == AUDIOBOOK_COUNT
        assert pag["total_pages"] == 3  # ceil(7/3)
        assert pag["has_next"] is True
        assert pag["has_prev"] is False

    def test_last_page_metadata(self, client):
        data = _get(client, "/api/audiobooks?per_page=3&page=3")
        pag = data["pagination"]
        assert pag["has_next"] is False
        assert pag["has_prev"] is True
        # Last page should have 1 book (7 total, 3 per page -> 3+3+1)
        assert len(data["audiobooks"]) == 1

    def test_per_page_capped_at_200(self, client):
        data = _get(client, "/api/audiobooks?per_page=500")
        assert data["pagination"]["per_page"] == 200


# ===========================================================================
# SEARCH TESTS
# ===========================================================================


class TestSearch:
    """Verify FTS search returns matching results."""

    def test_search_by_title(self, client):
        data = _get(client, "/api/audiobooks?search=dune")
        books = data["audiobooks"]
        assert len(books) >= 1
        titles = {b["title"] for b in books}
        assert "Dune" in titles

    def test_search_by_author(self, client):
        data = _get(client, "/api/audiobooks?search=tolkien")
        books = data["audiobooks"]
        assert len(books) >= 1
        assert any("Tolkien" in b["author"] for b in books)

    def test_search_by_narrator(self, client):
        data = _get(client, "/api/audiobooks?search=porter")
        books = data["audiobooks"]
        assert len(books) >= 1
        assert any("Porter" in (b["narrator"] or "") for b in books)

    def test_search_by_series(self, client):
        data = _get(client, "/api/audiobooks?search=foundation")
        books = data["audiobooks"]
        assert len(books) >= 1
        assert any(b["title"] == "Foundation" for b in books)

    def test_search_no_match(self, client):
        data = _get(client, "/api/audiobooks?search=zzzznonexistentzzzz")
        assert len(data["audiobooks"]) == 0

    def test_search_excludes_podcast(self, client):
        """Even if search matches, Podcast content_type should be excluded."""
        data = _get(client, "/api/audiobooks?search=podcast")
        titles = {b["title"] for b in data["audiobooks"]}
        assert "Daily News Podcast" not in titles


# ===========================================================================
# SEARCH + FILTER COMBINATION
# ===========================================================================


class TestSearchWithFilters:
    """Verify search and filters work together."""

    def test_search_plus_format_filter(self, client):
        """Search 'dune' + format=m4b -> nothing (Dune books are opus)."""
        data = _get(client, "/api/audiobooks?search=dune&format=m4b")
        assert len(data["audiobooks"]) == 0

    def test_search_plus_genre_filter(self, client):
        """Search 'foundation' + genre=Science Fiction -> Foundation."""
        data = _get(
            client,
            "/api/audiobooks?search=foundation&genre=Science Fiction",
        )
        books = data["audiobooks"]
        assert len(books) >= 1
        assert books[0]["title"] == "Foundation"


# ===========================================================================
# RESPONSE BODY CONTENT TESTS
# ===========================================================================


class TestResponseContent:
    """Verify response bodies contain expected structure and data."""

    def test_audiobook_has_genres(self, client):
        """Books should have genres populated from junction table."""
        data = _get(client, "/api/audiobooks?author=Frank Herbert&per_page=1")
        book = data["audiobooks"][0]
        assert "genres" in book
        assert "Science Fiction" in book["genres"]

    def test_audiobook_has_eras(self, client):
        """Books should have eras populated from junction table."""
        data = _get(client, "/api/audiobooks?author=Frank Herbert&per_page=1")
        book = data["audiobooks"][0]
        assert "eras" in book
        assert "20th Century" in book["eras"]

    def test_audiobook_has_normalized_authors(self, client):
        """Books should include normalized authors list."""
        data = _get(client, "/api/audiobooks?author=Frank Herbert&per_page=1")
        book = data["audiobooks"][0]
        assert "authors" in book
        assert len(book["authors"]) == 1
        assert book["authors"][0]["name"] == "Frank Herbert"
        assert book["authors"][0]["sort_name"] == "Herbert, Frank"

    def test_audiobook_has_normalized_narrators(self, client):
        """Books should include normalized narrators list."""
        data = _get(client, "/api/audiobooks?narrator=Ray Porter&per_page=1")
        book = data["audiobooks"][0]
        assert "narrators" in book
        assert len(book["narrators"]) == 1
        assert book["narrators"][0]["name"] == "Ray Porter"

    def test_single_audiobook_detail(self, client):
        """GET /api/audiobooks/<id> returns full detail with genres/eras/topics."""
        resp = client.get("/api/audiobooks/1")
        assert resp.status_code == 200
        book = json.loads(resp.data)
        assert book["title"] == "Dune"
        assert book["author"] == "Frank Herbert"
        assert "genres" in book
        assert "eras" in book
        assert "topics" in book


# ===========================================================================
# STATS ENDPOINT WITH DATA
# ===========================================================================


class TestStatsWithData:
    """Verify /api/stats returns correct aggregates."""

    def test_total_audiobooks_count(self, client):
        data = _get(client, "/api/stats")
        assert data["total_audiobooks"] == AUDIOBOOK_COUNT

    def test_total_hours(self, client):
        data = _get(client, "/api/stats")
        expected_hours = sum(
            b["duration_hours"] for b in SEED_BOOKS if b["content_type"] == "Product"
        )
        assert data["total_hours"] == round(expected_hours)

    def test_unique_authors(self, client):
        data = _get(client, "/api/stats")
        # 6 unique Product authors (News Corp excluded as Podcast)
        assert data["unique_authors"] == 6

    def test_unique_narrators(self, client):
        data = _get(client, "/api/stats")
        # 5 unique Product narrators (Various excluded as Podcast)
        assert data["unique_narrators"] == 5

    def test_unique_genres(self, client):
        data = _get(client, "/api/stats")
        assert data["unique_genres"] == 3


# ===========================================================================
# FILTERS ENDPOINT WITH DATA
# ===========================================================================


class TestFiltersEndpointWithData:
    """Verify /api/filters returns actual filter options from seeded data."""

    def test_authors_populated(self, client):
        data = _get(client, "/api/filters")
        author_names = [a["name"] for a in data["authors"]]
        assert "Frank Herbert" in author_names
        assert "Andy Weir" in author_names

    def test_narrators_populated(self, client):
        data = _get(client, "/api/filters")
        assert "Scott Brick" in data["narrators"]
        assert "Ray Porter" in data["narrators"]

    def test_publishers_populated(self, client):
        data = _get(client, "/api/filters")
        assert "Macmillan Audio" in data["publishers"]

    def test_genres_populated(self, client):
        data = _get(client, "/api/filters")
        assert "Science Fiction" in data["genres"]
        assert "Fantasy" in data["genres"]
        assert "Nonfiction" in data["genres"]

    def test_formats_populated(self, client):
        data = _get(client, "/api/filters")
        assert "opus" in data["formats"]
        assert "m4b" in data["formats"]
        assert "mp3" in data["formats"]

    def test_eras_populated(self, client):
        data = _get(client, "/api/filters")
        assert "20th Century" in data["eras"]
        assert "21st Century" in data["eras"]


# ===========================================================================
# NARRATOR COUNTS ENDPOINT
# ===========================================================================


class TestNarratorCountsWithData:
    """Verify /api/narrator-counts returns correct counts."""

    def test_scott_brick_count(self, client):
        data = _get(client, "/api/narrator-counts")
        assert data["Scott Brick"] == 3  # Dune, Dune Messiah, Foundation

    def test_ray_porter_count(self, client):
        data = _get(client, "/api/narrator-counts")
        assert data["Ray Porter"] == 1
