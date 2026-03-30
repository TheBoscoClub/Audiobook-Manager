"""
Tests for library/scripts/enrich_single.py — single-book enrichment module.

All external API calls (Audible, Google Books, Open Library) are mocked.
Database operations use a real in-memory or tmp_path SQLite DB initialized
from the project schema.
"""

import json
import sqlite3
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure library is on the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrich_single import (
    _extract_categories,
    _extract_editorial_reviews,
    _extract_rating,
    _fetch_audible_product,
    _get_best_image_url,
    _parse_sequence,
    _query_google_books,
    _query_openlibrary_search,
    enrich_book,
    main,
)

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "schema.sql"


# ── Helpers ──────────────────────────────────────────────────


def _init_db(db_path: Path) -> None:
    """Initialize a test database from schema.sql."""
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.close()


def _insert_book(db_path: Path, **overrides) -> int | None:
    """Insert a test audiobook and return its ID."""
    defaults = {
        "title": "Test Book",
        "author": "Test Author",
        "file_path": "/test/book.opus",
        "format": "opus",
        "asin": None,
        "isbn": None,
        "series": None,
        "language": None,
        "description": None,
        "published_year": None,
    }
    defaults.update(overrides)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(["?"] * len(defaults))
    cur.execute(
        f"INSERT INTO audiobooks ({cols}) VALUES ({placeholders})",  # nosec B608
        list(defaults.values()),
    )
    book_id = cur.lastrowid
    conn.commit()
    conn.close()
    return book_id


def _insert_author(db_path: Path, name: str, asin: str | None = None) -> int | None:
    """Insert an author row and return its ID."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO authors (name, sort_name, asin) VALUES (?, ?, ?)",
        (name, name, asin),
    )
    author_id = cur.lastrowid
    conn.commit()
    conn.close()
    return author_id


def _get_book(db_path: Path, book_id: int) -> dict:
    """Read back a full audiobook row as a dict."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM audiobooks WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def _make_mock_urlopen(response_data: dict, status: int = 200):
    """Create a mock context manager that returns JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ── Sample API responses ─────────────────────────────────────

SAMPLE_AUDIBLE_PRODUCT = {
    "product": {
        "asin": "B00TEST123",
        "title": "Test Book",
        "subtitle": "A Test Subtitle",
        "language": "English",
        "format_type": "Unabridged",
        "runtime_length_min": 600,
        "release_date": "2024-01-15",
        "publisher_summary": "A great book about testing.",
        "sample_url": "https://example.com/sample.mp3",
        "sku": "SKU123",
        "is_adult_product": False,
        "merchandising_summary": "Best seller in testing.",
        "content_type": "Product",
        "series": [{"title": "Test Series", "sequence": "3"}],
        "rating": {
            "overall_distribution": {
                "display_average_rating": 4.5,
                "num_ratings": 1000,
            },
            "performance_distribution": {"display_average_rating": 4.3},
            "story_distribution": {"display_average_rating": 4.7},
            "num_reviews": 500,
        },
        "product_images": {
            "500": "https://example.com/img500.jpg",
            "1024": "https://example.com/img1024.jpg",
        },
        "category_ladders": [
            {
                "ladder": [
                    {"name": "Fiction", "id": "cat1"},
                    {"name": "Sci-Fi", "id": "cat2"},
                ]
            }
        ],
        "editorial_reviews": [
            {"review": "Excellent book!", "source": "Publisher"},
            "A string-only review",
        ],
        "authors": [
            {"name": "Test Author", "asin": "AUTH001"},
            {"name": "No ASIN Author"},  # no asin key
        ],
    }
}

SAMPLE_GOOGLE_BOOKS_RESPONSE = {
    "items": [
        {
            "volumeInfo": {
                "language": "en",
                "description": "A book about testing patterns.",
                "publishedDate": "2023-06-15",
                "industryIdentifiers": [
                    {"type": "ISBN_13", "identifier": "9781234567890"},
                    {"type": "ISBN_10", "identifier": "1234567890"},
                ],
            }
        }
    ]
}

SAMPLE_OPENLIBRARY_RESPONSE = {
    "docs": [
        {
            "description": {"value": "An open library description."},
            "isbn": ["9780987654321"],
        }
    ]
}


# ═══════════════════════════════════════════════════════════
# Unit tests — helper functions
# ═══════════════════════════════════════════════════════════


class TestParseSequence:
    """Tests for _parse_sequence()."""

    def test_none_input(self):
        assert _parse_sequence(None) is None

    def test_empty_string(self):
        assert _parse_sequence("") is None

    def test_integer_string(self):
        assert _parse_sequence("3") == 3.0

    def test_float_string(self):
        assert _parse_sequence("2.5") == 2.5

    def test_string_with_prefix(self):
        assert _parse_sequence("Book 7") == 7.0

    def test_string_with_suffix(self):
        assert _parse_sequence("3rd") == 3.0

    def test_no_number(self):
        assert _parse_sequence("no-number-here") is None

    def test_complex_sequence(self):
        assert _parse_sequence("Volume 12.5 of series") == 12.5


class TestExtractCategories:
    """Tests for _extract_categories()."""

    def test_empty_product(self):
        assert _extract_categories({}) == []

    def test_empty_ladders(self):
        assert _extract_categories({"category_ladders": []}) == []

    def test_ladder_with_empty_items(self):
        result = _extract_categories({"category_ladders": [{"ladder": []}]})
        assert result == []

    def test_single_ladder(self):
        product = {
            "category_ladders": [
                {
                    "ladder": [
                        {"name": "Fiction", "id": "cat1"},
                        {"name": "Fantasy", "id": "cat2"},
                    ]
                }
            ]
        }
        result = _extract_categories(product)
        assert len(result) == 2
        assert result[0]["category_path"] == "Fiction"
        assert result[0]["category_name"] == "Fiction"
        assert result[0]["root_category"] == "Fiction"
        assert result[0]["depth"] == 1
        assert result[0]["audible_category_id"] == "cat1"
        assert result[1]["category_path"] == "Fiction > Fantasy"
        assert result[1]["depth"] == 2

    def test_multiple_ladders(self):
        product = {
            "category_ladders": [
                {"ladder": [{"name": "Fiction", "id": "1"}]},
                {"ladder": [{"name": "Nonfiction", "id": "2"}]},
            ]
        }
        result = _extract_categories(product)
        assert len(result) == 2
        assert result[0]["root_category"] == "Fiction"
        assert result[1]["root_category"] == "Nonfiction"

    def test_items_without_name(self):
        product = {
            "category_ladders": [
                {
                    "ladder": [
                        {"name": "", "id": "cat1"},
                        {"name": "Fantasy", "id": "cat2"},
                    ]
                }
            ]
        }
        result = _extract_categories(product)
        # Only the item with a name should be included
        assert len(result) == 1
        assert result[0]["category_name"] == "Fantasy"


class TestExtractEditorialReviews:
    """Tests for _extract_editorial_reviews()."""

    def test_empty_product(self):
        assert _extract_editorial_reviews({}) == []

    def test_dict_review(self):
        product = {"editorial_reviews": [{"review": "Great book!", "source": "NYT"}]}
        result = _extract_editorial_reviews(product)
        assert len(result) == 1
        assert result[0]["review_text"] == "Great book!"
        assert result[0]["source"] == "NYT"

    def test_string_review(self):
        product = {"editorial_reviews": ["A plain string review"]}
        result = _extract_editorial_reviews(product)
        assert len(result) == 1
        assert result[0]["review_text"] == "A plain string review"
        assert result[0]["source"] == ""

    def test_empty_review_text(self):
        product = {"editorial_reviews": [{"review": "", "source": "NYT"}]}
        result = _extract_editorial_reviews(product)
        assert len(result) == 0

    def test_mixed_reviews(self):
        product = {
            "editorial_reviews": [
                {"review": "Dict review", "source": "Pub"},
                "String review",
            ]
        }
        result = _extract_editorial_reviews(product)
        assert len(result) == 2


class TestExtractRating:
    """Tests for _extract_rating()."""

    def test_empty_product(self):
        result = _extract_rating({})
        assert result["rating_overall"] is None
        assert result["rating_performance"] is None
        assert result["rating_story"] is None
        assert result["num_ratings"] is None
        assert result["num_reviews"] is None

    def test_full_rating(self):
        product = {
            "rating": {
                "overall_distribution": {
                    "display_average_rating": 4.5,
                    "num_ratings": 1200,
                },
                "performance_distribution": {"display_average_rating": 4.3},
                "story_distribution": {"display_average_rating": 4.7},
                "num_reviews": 500,
            }
        }
        result = _extract_rating(product)
        assert result["rating_overall"] == 4.5
        assert result["rating_performance"] == 4.3
        assert result["rating_story"] == 4.7
        assert result["num_ratings"] == 500
        assert result["num_reviews"] == 1200

    def test_partial_rating(self):
        product = {
            "rating": {
                "overall_distribution": {"display_average_rating": 3.9},
            }
        }
        result = _extract_rating(product)
        assert result["rating_overall"] == 3.9
        assert result["rating_performance"] is None


class TestGetBestImageUrl:
    """Tests for _get_best_image_url()."""

    def test_empty_product(self):
        assert _get_best_image_url({}) is None

    def test_empty_images(self):
        assert _get_best_image_url({"product_images": {}}) is None

    def test_prefers_2400(self):
        product = {
            "product_images": {
                "500": "url500",
                "1024": "url1024",
                "2400": "url2400",
            }
        }
        assert _get_best_image_url(product) == "url2400"

    def test_prefers_1024_when_no_2400(self):
        product = {"product_images": {"500": "url500", "1024": "url1024"}}
        assert _get_best_image_url(product) == "url1024"

    def test_prefers_500_when_no_larger(self):
        product = {"product_images": {"500": "url500", "252": "url252"}}
        assert _get_best_image_url(product) == "url500"

    def test_falls_back_to_first_available(self):
        product = {"product_images": {"100": "url100"}}
        assert _get_best_image_url(product) == "url100"


# ═══════════════════════════════════════════════════════════
# Unit tests — API fetch functions
# ═══════════════════════════════════════════════════════════


class TestFetchAudibleProduct:
    """Tests for _fetch_audible_product() with mocked urllib."""

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen(SAMPLE_AUDIBLE_PRODUCT)
        result = _fetch_audible_product("B00TEST123")
        assert result is not None
        assert result["title"] == "Test Book"

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_404_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=404, msg="Not Found", hdrs=None, fp=None
        )
        assert _fetch_audible_product("BADASIN") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_429_retries_then_succeeds(self, mock_urlopen):
        mock_resp = _make_mock_urlopen(SAMPLE_AUDIBLE_PRODUCT)
        mock_urlopen.side_effect = [
            urllib.error.HTTPError(
                url="", code=429, msg="Too Many Requests", hdrs=None, fp=None
            ),
            mock_resp,
        ]
        with patch("scripts.enrich_single.time.sleep"):
            result = _fetch_audible_product("B00TEST123")
        assert result is not None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_429_retries_then_fails(self, mock_urlopen):
        mock_urlopen.side_effect = [
            urllib.error.HTTPError(
                url="", code=429, msg="Too Many Requests", hdrs=None, fp=None
            ),
            Exception("Still failing"),
        ]
        with patch("scripts.enrich_single.time.sleep"):
            result = _fetch_audible_product("B00TEST123")
        assert result is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_500_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="Server Error", hdrs=None, fp=None
        )
        assert _fetch_audible_product("B00TEST123") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_url_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Network unreachable")
        assert _fetch_audible_product("B00TEST123") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_timeout_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError()
        assert _fetch_audible_product("B00TEST123") is None


class TestQueryGoogleBooks:
    """Tests for _query_google_books() with mocked urllib."""

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_isbn_query(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen(SAMPLE_GOOGLE_BOOKS_RESPONSE)
        result = _query_google_books(isbn="9781234567890")
        assert result is not None
        assert result["language"] == "en"

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_title_author_query(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen(SAMPLE_GOOGLE_BOOKS_RESPONSE)
        result = _query_google_books(title="Test Book", author="Test Author")
        assert result is not None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_title_only_query(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen(SAMPLE_GOOGLE_BOOKS_RESPONSE)
        result = _query_google_books(title="Test Book")
        assert result is not None

    def test_no_params_returns_none(self):
        assert _query_google_books() is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_empty_results(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen({"items": []})
        assert _query_google_books(isbn="0000000000") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_no_items_key(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen({})
        assert _query_google_books(isbn="0000000000") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_http_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="Error", hdrs=None, fp=None
        )
        assert _query_google_books(isbn="9781234567890") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_timeout_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError()
        assert _query_google_books(isbn="9781234567890") is None


class TestQueryOpenLibrary:
    """Tests for _query_openlibrary_search() with mocked urllib."""

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen(SAMPLE_OPENLIBRARY_RESPONSE)
        result = _query_openlibrary_search("Test Book", "Test Author")
        assert result is not None
        assert "isbn" in result

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_title_only(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen(SAMPLE_OPENLIBRARY_RESPONSE)
        result = _query_openlibrary_search("Test Book")
        assert result is not None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_empty_docs(self, mock_urlopen):
        mock_urlopen.return_value = _make_mock_urlopen({"docs": []})
        assert _query_openlibrary_search("Nonexistent Book") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_http_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="", code=500, msg="Error", hdrs=None, fp=None
        )
        assert _query_openlibrary_search("Test") is None

    @patch("scripts.enrich_single.urllib.request.urlopen")
    def test_timeout_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError()
        assert _query_openlibrary_search("Test") is None


# ═══════════════════════════════════════════════════════════
# Integration tests — enrich_book()
# ═══════════════════════════════════════════════════════════


class TestEnrichBookNoDb:
    """Tests for enrich_book() with no database path."""

    def test_no_db_path_no_config(self):
        with patch("scripts.enrich_single.DATABASE_PATH", None):
            result = enrich_book(book_id=1, db_path=None)
        assert result["errors"] == ["No database path"]
        assert result["fields_updated"] == 0

    def test_no_db_path_with_config(self, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        _insert_book(db, asin=None)
        with patch("scripts.enrich_single.DATABASE_PATH", db):
            result = enrich_book(book_id=1, db_path=None, quiet=True)
        assert "No database path" not in result["errors"]


class TestEnrichBookNotFound:
    """Tests for enrich_book() when book ID doesn't exist."""

    def test_book_not_found(self, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        result = enrich_book(book_id=999, db_path=db, quiet=True)
        assert "Book ID 999 not found" in result["errors"]


class TestEnrichBookAudible:
    """Tests for Audible enrichment path in enrich_book()."""

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_full_audible_enrichment(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")
        _insert_author(db, "Test Author")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        result = enrich_book(book_id=book_id, db_path=db, quiet=True)

        assert result["audible_enriched"] is True
        assert result["fields_updated"] > 0
        assert result["errors"] == []

        book = _get_book(db, book_id)
        assert book["subtitle"] == "A Test Subtitle"
        assert book["language"] == "English"
        assert book["series"] == "Test Series"
        assert book["series_sequence"] == 3.0
        assert book["runtime_length_min"] == 600
        assert book["audible_enriched_at"] is not None

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_no_data(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00NODATA")

        mock_fetch.return_value = None
        result = enrich_book(book_id=book_id, db_path=db, quiet=True)

        assert result["audible_enriched"] is False

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_preserves_existing_series(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123", series="Existing Series")

        product = dict(SAMPLE_AUDIBLE_PRODUCT["product"])
        mock_fetch.return_value = product
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["series"] == "Existing Series"

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_writes_categories(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        conn = sqlite3.connect(db)
        cats = conn.execute(
            "SELECT * FROM audible_categories WHERE audiobook_id = ?",
            (book_id,),
        ).fetchall()
        conn.close()
        assert len(cats) == 2  # Fiction + Fiction > Sci-Fi

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_writes_editorial_reviews(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        conn = sqlite3.connect(db)
        reviews = conn.execute(
            "SELECT * FROM editorial_reviews WHERE audiobook_id = ?",
            (book_id,),
        ).fetchall()
        conn.close()
        assert len(reviews) == 2

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_updates_author_asin(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")
        _insert_author(db, "Test Author")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        author = conn.execute(
            "SELECT asin FROM authors WHERE name = ?", ("Test Author",)
        ).fetchone()
        conn.close()
        assert author["asin"] == "AUTH001"

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_does_not_overwrite_existing_author_asin(
        self, mock_fetch, tmp_path
    ):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")
        _insert_author(db, "Test Author", asin="EXISTING_ASIN")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        author = conn.execute(
            "SELECT asin FROM authors WHERE name = ?", ("Test Author",)
        ).fetchone()
        conn.close()
        assert author["asin"] == "EXISTING_ASIN"

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_release_date_fallback(self, mock_fetch, tmp_path):
        """When release_date is None, falls back to publication_datetime."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")

        product = dict(SAMPLE_AUDIBLE_PRODUCT["product"])
        product["release_date"] = None
        product["publication_datetime"] = "2023-05-20T00:00:00Z"
        mock_fetch.return_value = product
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["release_date"] == "2023-05-20"

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_content_type_only_when_present(self, mock_fetch, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")

        product = dict(SAMPLE_AUDIBLE_PRODUCT["product"])
        product.pop("content_type", None)
        mock_fetch.return_value = product
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        # content_type should keep its default from schema, not be set by enrichment
        assert book["content_type"] == "Product"

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_replaces_categories_on_reenrich(self, mock_fetch, tmp_path):
        """Enriching again should delete old categories before inserting new ones."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        # Clear isbn_enriched_at so re-enrichment can happen fully
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE audiobooks SET isbn_enriched_at = NULL WHERE id = ?",
            (book_id,),
        )
        conn.commit()
        conn.close()

        # Enrich again
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        conn = sqlite3.connect(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM audible_categories WHERE audiobook_id = ?",
            (book_id,),
        ).fetchone()[0]
        conn.close()
        # Should still be 2, not 4 (duplicated)
        assert count == 2


class TestEnrichBookISBN:
    """Tests for ISBN enrichment path in enrich_book()."""

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_google_books_enrichment(self, mock_gb, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        mock_gb.return_value = SAMPLE_GOOGLE_BOOKS_RESPONSE["items"][0]["volumeInfo"]
        result = enrich_book(book_id=book_id, db_path=db, quiet=True)

        assert result["isbn_enriched"] is True
        assert result["fields_updated"] > 0

        book = _get_book(db, book_id)
        assert book["language"] == "English"
        assert book["description"] == "A book about testing patterns."
        assert book["published_year"] == 2023

    @patch("scripts.enrich_single._query_openlibrary_search")
    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_openlibrary_fallback(self, mock_gb, mock_ol, tmp_path):
        """When no ISBN and Google Books returns nothing, falls back to OpenLibrary."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn=None)

        mock_gb.return_value = None
        mock_ol.return_value = SAMPLE_OPENLIBRARY_RESPONSE["docs"][0]
        result = enrich_book(book_id=book_id, db_path=db, quiet=True)

        assert result["isbn_enriched"] is True
        book = _get_book(db, book_id)
        assert book["description"] == "An open library description."
        assert book["isbn"] == "9780987654321"

    @patch("scripts.enrich_single._query_openlibrary_search")
    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_openlibrary_string_description(self, mock_gb, mock_ol, tmp_path):
        """OpenLibrary sometimes returns description as a plain string."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn=None)

        ol_data = {"description": "A plain string description", "isbn": []}
        mock_gb.return_value = None
        mock_ol.return_value = ol_data
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["description"] == "A plain string description"

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_skips_when_already_enriched(self, mock_gb, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        # First enrichment
        mock_gb.return_value = SAMPLE_GOOGLE_BOOKS_RESPONSE["items"][0]["volumeInfo"]
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        # Second enrichment — isbn_enriched_at is already set
        mock_gb.reset_mock()
        enrich_book(book_id=book_id, db_path=db, quiet=True)
        mock_gb.assert_not_called()

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_nothing_missing_marks_attempted(self, mock_gb, tmp_path):
        """When all fields already present, just marks isbn_enriched_at."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(
            db,
            isbn="9781234567890",
            language="English",
            description="Already have one",
            published_year=2020,
        )

        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["isbn_enriched_at"] is not None
        # Google Books should not have been called
        mock_gb.assert_not_called()

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_language_code_mapping(self, mock_gb, tmp_path):
        """Two-letter language codes are mapped to full names."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        gb_data = {"language": "fr", "description": "Un livre"}
        mock_gb.return_value = gb_data
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["language"] == "French"

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_unknown_language_code_kept(self, mock_gb, tmp_path):
        """Unknown 2-letter codes are kept as-is."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        gb_data = {"language": "xx"}
        mock_gb.return_value = gb_data
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["language"] == "xx"

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_longer_language_not_mapped(self, mock_gb, tmp_path):
        """Language codes longer than 2 chars are not mapped."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        gb_data = {"language": "eng"}
        mock_gb.return_value = gb_data
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["language"] == "eng"

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_does_not_overwrite_existing_fields(self, mock_gb, tmp_path):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890", language="German")

        gb_data = {"language": "en", "description": "New desc"}
        mock_gb.return_value = gb_data
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        # Language was already set, shouldn't be overwritten
        assert book["language"] == "German"
        # Description was null, should be set
        assert book["description"] == "New desc"

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_extracts_isbn_from_identifiers(self, mock_gb, tmp_path):
        """When no ISBN in DB, extract from Google Books identifiers."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn=None)

        gb_data = {
            "industryIdentifiers": [
                {"type": "ISBN_13", "identifier": "9781234567890"},
            ]
        }
        mock_gb.return_value = gb_data
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["isbn"] == "9781234567890"

    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_published_date_bad_year_parse(self, mock_gb, tmp_path):
        """publishedDate with non-numeric year prefix is handled gracefully.

        Previously this was a documented bug where the SQL placeholder was
        appended before int() conversion, causing ProgrammingError on
        ValueError. The refactored code handles the ValueError correctly
        by skipping published_year when int() fails, while still setting
        published_date.
        """
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        gb_data = {"publishedDate": "XXXX-01-01"}
        mock_gb.return_value = gb_data
        # Should not raise — bad year is skipped gracefully
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        # published_date is set (bad year prefix treated as literal date string)
        assert book["published_year"] is None  # int("XXXX") failed, skipped

    @patch("scripts.enrich_single._query_openlibrary_search")
    @patch("scripts.enrich_single._query_google_books")
    def test_isbn_ol_no_isbn_available(self, mock_gb, mock_ol, tmp_path):
        """OpenLibrary result with empty isbn list."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn=None)

        mock_gb.return_value = None
        mock_ol.return_value = {"description": "Some desc", "isbn": []}
        enrich_book(book_id=book_id, db_path=db, quiet=True)

        book = _get_book(db, book_id)
        assert book["description"] == "Some desc"
        assert book["isbn"] is None


class TestEnrichBookCombined:
    """Tests for combined Audible + ISBN enrichment."""

    @patch("scripts.enrich_single._query_google_books")
    @patch("scripts.enrich_single._fetch_audible_product")
    def test_audible_then_isbn_fillgap(self, mock_audible, mock_gb, tmp_path):
        """Audible fills some fields, ISBN fills the rest."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123", isbn="9781234567890")

        # Audible product with no language
        product = dict(SAMPLE_AUDIBLE_PRODUCT["product"])
        product.pop("language", None)
        mock_audible.return_value = product

        # Google Books provides language
        mock_gb.return_value = {"language": "en", "description": "GB desc"}

        result = enrich_book(book_id=book_id, db_path=db, quiet=True)

        assert result["audible_enriched"] is True
        assert result["isbn_enriched"] is True

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_no_asin_skips_audible(self, mock_fetch, tmp_path):
        """Books without ASIN skip Audible enrichment entirely."""
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin=None)

        enrich_book(book_id=book_id, db_path=db, quiet=True)
        mock_fetch.assert_not_called()


class TestEnrichBookOutput:
    """Tests for print output (quiet=False)."""

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_output_with_audible(self, mock_fetch, tmp_path, capsys):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00TEST123")

        mock_fetch.return_value = SAMPLE_AUDIBLE_PRODUCT["product"]
        enrich_book(book_id=book_id, db_path=db, quiet=False)

        captured = capsys.readouterr()
        assert "Enriching:" in captured.out
        assert "Audible:" in captured.out
        assert "Total:" in captured.out

    @patch("scripts.enrich_single._fetch_audible_product")
    def test_output_audible_no_data(self, mock_fetch, tmp_path, capsys):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, asin="B00NODATA")

        mock_fetch.return_value = None
        enrich_book(book_id=book_id, db_path=db, quiet=False)

        captured = capsys.readouterr()
        assert "no data for ASIN" in captured.out

    @patch("scripts.enrich_single._query_google_books")
    def test_output_isbn_source(self, mock_gb, tmp_path, capsys):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn="9781234567890")

        mock_gb.return_value = {"language": "en"}
        enrich_book(book_id=book_id, db_path=db, quiet=False)

        captured = capsys.readouterr()
        assert "ISBN (Google Books):" in captured.out

    @patch("scripts.enrich_single._query_openlibrary_search")
    @patch("scripts.enrich_single._query_google_books")
    def test_output_isbn_openlibrary_source(self, mock_gb, mock_ol, tmp_path, capsys):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn=None)

        mock_gb.return_value = None
        mock_ol.return_value = {"description": "desc", "isbn": []}
        enrich_book(book_id=book_id, db_path=db, quiet=False)

        captured = capsys.readouterr()
        assert "ISBN (OpenLibrary):" in captured.out

    @patch("scripts.enrich_single._query_openlibrary_search")
    @patch("scripts.enrich_single._query_google_books")
    def test_output_isbn_no_source(self, mock_gb, mock_ol, tmp_path, capsys):
        db = tmp_path / "test.db"
        _init_db(db)
        book_id = _insert_book(db, isbn=None)

        mock_gb.return_value = None
        mock_ol.return_value = None
        enrich_book(book_id=book_id, db_path=db, quiet=False)

        captured = capsys.readouterr()
        assert "ISBN (none):" in captured.out


# ═══════════════════════════════════════════════════════════
# Tests — main() CLI entry point
# ═══════════════════════════════════════════════════════════


class TestMain:
    """Tests for the main() CLI entry point."""

    @patch("scripts.enrich_single.enrich_book")
    def test_main_success(self, mock_enrich, tmp_path, capsys):
        mock_enrich.return_value = {
            "audible_enriched": True,
            "isbn_enriched": False,
            "fields_updated": 5,
            "errors": [],
        }
        db = tmp_path / "test.db"
        with patch("sys.argv", ["enrich_single.py", "--db", str(db), "--id", "42"]):
            main()
        captured = capsys.readouterr()
        assert "5 fields updated" in captured.out
        assert "Audible API" in captured.out

    @patch("scripts.enrich_single.enrich_book")
    def test_main_isbn_source(self, mock_enrich, tmp_path, capsys):
        mock_enrich.return_value = {
            "audible_enriched": False,
            "isbn_enriched": True,
            "fields_updated": 3,
            "errors": [],
        }
        db = tmp_path / "test.db"
        with patch("sys.argv", ["enrich_single.py", "--db", str(db), "--id", "42"]):
            main()
        captured = capsys.readouterr()
        assert "ISBN" in captured.out

    @patch("scripts.enrich_single.enrich_book")
    def test_main_with_errors(self, mock_enrich, tmp_path):
        mock_enrich.return_value = {
            "audible_enriched": False,
            "isbn_enriched": False,
            "fields_updated": 0,
            "errors": ["Book not found"],
        }
        db = tmp_path / "test.db"
        with patch("sys.argv", ["enrich_single.py", "--db", str(db), "--id", "999"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch("scripts.enrich_single.enrich_book")
    def test_main_no_db_flag(self, mock_enrich, capsys):
        mock_enrich.return_value = {
            "audible_enriched": False,
            "isbn_enriched": False,
            "fields_updated": 0,
            "errors": [],
        }
        with patch("sys.argv", ["enrich_single.py", "--id", "42"]):
            main()
        # db_path should be None when --db not given
        mock_enrich.assert_called_once_with(book_id=42, db_path=None, quiet=False)

    @patch("scripts.enrich_single.enrich_book")
    def test_main_quiet_flag(self, mock_enrich, tmp_path, capsys):
        mock_enrich.return_value = {
            "audible_enriched": False,
            "isbn_enriched": False,
            "fields_updated": 0,
            "errors": [],
        }
        db = tmp_path / "test.db"
        with patch(
            "sys.argv",
            ["enrich_single.py", "--db", str(db), "--id", "42", "--quiet"],
        ):
            main()
        mock_enrich.assert_called_once_with(
            book_id=42, db_path=Path(str(db)), quiet=True
        )
