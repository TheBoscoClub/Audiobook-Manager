"""
Tests for scanner/utils/cover_resolver.py — external cover art resolution.

Covers all three tiers (Audible, Open Library, Google Books), fallback
behaviour, rate limiting, error handling, _save_image, and edge cases.
All HTTP requests are mocked — no real API calls.
"""

import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

# Ensure library/scripts is on path so `from utils.openlibrary_client`
# resolves when cover_resolver is imported.
LIBRARY_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = LIBRARY_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scanner.utils.cover_resolver import (  # noqa: E402
    _rate_limit,
    _save_image,
    _try_audible,
    _try_google_books,
    _try_openlibrary,
    resolve_cover,
)
import scanner.utils.cover_resolver as cr_module  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A minimal JPEG-like blob >1000 bytes so _save_image accepts it.
VALID_IMAGE = b"\xff\xd8\xff" + b"\x00" * 1200

# Too-small blob — _save_image should reject it.
TINY_IMAGE = b"\xff\xd8\xff" + b"\x00" * 50


def _mock_response(status=200, content=b"", content_type="image/jpeg", json_data=None):
    """Build a fake requests.Response."""
    resp = Mock(spec=requests.Response)
    resp.status_code = status
    resp.content = content
    resp.headers = {"content-type": content_type}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# Fixture: always reset the module-level rate-limit timestamp so tests
# don't sleep waiting on a previous test's timestamp.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset rate-limit state before every test."""
    cr_module._last_request_time = 0.0
    yield


# ---------------------------------------------------------------------------
# _save_image
# ---------------------------------------------------------------------------


class TestSaveImage:
    """Tests for _save_image()."""

    def test_saves_valid_image(self, tmp_path):
        result = _save_image(VALID_IMAGE, tmp_path, "https://example.com/img.jpg")
        assert result is not None
        expected_hash = hashlib.md5(VALID_IMAGE, usedforsecurity=False).hexdigest()
        assert result == f"{expected_hash}.jpg"
        assert (tmp_path / result).exists()
        assert (tmp_path / result).read_bytes() == VALID_IMAGE

    def test_rejects_empty_data(self, tmp_path):
        assert _save_image(b"", tmp_path, "https://example.com/img.jpg") is None

    def test_rejects_none_data(self, tmp_path):
        assert _save_image(None, tmp_path, "https://example.com/img.jpg") is None

    def test_rejects_tiny_image(self, tmp_path):
        assert _save_image(TINY_IMAGE, tmp_path, "https://example.com/img.jpg") is None

    def test_exactly_1000_bytes_accepted(self, tmp_path):
        """Boundary: exactly 1000 bytes passes the < 1000 check."""
        data = b"\x00" * 1000
        assert _save_image(data, tmp_path, "https://example.com/img.jpg") is not None

    def test_999_bytes_rejected(self, tmp_path):
        data = b"\x00" * 999
        assert _save_image(data, tmp_path, "https://example.com/img.jpg") is None

    def test_1001_bytes_accepted(self, tmp_path):
        data = b"\x00" * 1001
        result = _save_image(data, tmp_path, "https://example.com/img.jpg")
        assert result is not None

    def test_os_error_returns_none(self, tmp_path):
        # Use a read-only directory to trigger OSError on write
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        ro_dir.chmod(0o444)
        result = _save_image(VALID_IMAGE, ro_dir, "https://example.com/img.jpg")
        # Restore permissions for cleanup
        ro_dir.chmod(0o755)
        assert result is None

    def test_deterministic_filename(self, tmp_path):
        """Same image data always produces the same filename."""
        r1 = _save_image(VALID_IMAGE, tmp_path, "https://example.com/a.jpg")
        r2 = _save_image(VALID_IMAGE, tmp_path, "https://example.com/b.jpg")
        assert r1 == r2

    def test_different_data_different_filename(self, tmp_path):
        data_a = b"\xff" * 1500
        data_b = b"\xfe" * 1500
        r1 = _save_image(data_a, tmp_path, "https://example.com/a.jpg")
        r2 = _save_image(data_b, tmp_path, "https://example.com/b.jpg")
        assert r1 != r2


# ---------------------------------------------------------------------------
# _try_audible
# ---------------------------------------------------------------------------


class TestTryAudible:
    """Tests for _try_audible() — Tier 1."""

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_success_primary_cdn(self, mock_rl, mock_get, tmp_path):
        mock_get.return_value = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is not None
        assert result.endswith(".jpg")
        # Should have been called once (primary CDN succeeded)
        assert mock_get.call_count == 1
        url_called = mock_get.call_args[0][0]
        assert "m.media-amazon.com" in url_called

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_fallback_to_alternate_cdn(self, mock_rl, mock_get, tmp_path):
        """If primary CDN fails, tries alternate CDN."""
        primary_fail = _mock_response(status=404, content=b"", content_type="text/html")
        alternate_ok = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [primary_fail, alternate_ok]
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is not None
        assert mock_get.call_count == 2
        url2 = mock_get.call_args_list[1][0][0]
        assert "images-na.ssl-images-amazon.com" in url2

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_both_cdns_fail(self, mock_rl, mock_get, tmp_path):
        mock_get.return_value = _mock_response(
            status=404, content=b"", content_type="text/html"
        )
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is None
        assert mock_get.call_count == 2

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_network_error_primary(self, mock_rl, mock_get, tmp_path):
        """Network error on primary CDN tries alternate."""
        mock_get.side_effect = [
            requests.ConnectionError("DNS failure"),
            _mock_response(status=200, content=VALID_IMAGE, content_type="image/jpeg"),
        ]
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is not None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_network_error_both(self, mock_rl, mock_get, tmp_path):
        mock_get.side_effect = requests.ConnectionError("DNS failure")
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_non_image_content_type_rejected(self, mock_rl, mock_get, tmp_path):
        """200 OK but content-type is text/html — not a real image."""
        mock_get.return_value = _mock_response(
            status=200, content=VALID_IMAGE, content_type="text/html"
        )
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_image_too_small_rejected(self, mock_rl, mock_get, tmp_path):
        """200 OK with image content-type but data is a tiny placeholder."""
        mock_get.return_value = _mock_response(
            status=200, content=TINY_IMAGE, content_type="image/jpeg"
        )
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_timeout_error(self, mock_rl, mock_get, tmp_path):
        mock_get.side_effect = requests.Timeout("timed out")
        result = _try_audible("B00ASIN123", tmp_path, 15)
        assert result is None


# ---------------------------------------------------------------------------
# _try_openlibrary
# ---------------------------------------------------------------------------


class TestTryOpenLibrary:
    """Tests for _try_openlibrary() — Tier 2."""

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_success(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [{"cover_i": 12345}]
        mock_client.get_cover_url.return_value = (
            "https://covers.openlibrary.org/b/id/12345-L.jpg"
        )
        mock_get.return_value = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        result = _try_openlibrary("The Hobbit", "Tolkien", tmp_path, 15)
        assert result is not None
        assert result.endswith(".jpg")

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_no_search_results(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = []
        result = _try_openlibrary("Nonexistent Book", None, tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_no_cover_id_in_results(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [
            {"title": "Something", "author_name": ["Author"]}
        ]
        result = _try_openlibrary("Something", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_cover_image_too_small(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [{"cover_i": 99999}]
        mock_client.get_cover_url.return_value = (
            "https://covers.openlibrary.org/b/id/99999-L.jpg"
        )
        mock_get.return_value = _mock_response(
            status=200, content=TINY_IMAGE, content_type="image/jpeg"
        )
        result = _try_openlibrary("Title", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_search_raises_exception(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.side_effect = Exception("API error")
        result = _try_openlibrary("Title", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_cover_download_fails(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [{"cover_i": 12345}]
        mock_client.get_cover_url.return_value = (
            "https://covers.openlibrary.org/b/id/12345-L.jpg"
        )
        mock_get.return_value = _mock_response(
            status=500, content=b"", content_type="text/html"
        )
        result = _try_openlibrary("Title", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_skips_results_without_cover_finds_one_with(
        self, mock_ol_cls, mock_rl, mock_get, tmp_path
    ):
        """First result has no cover_i, second does."""
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [
            {"title": "No Cover"},
            {"cover_i": 55555},
        ]
        mock_client.get_cover_url.return_value = (
            "https://covers.openlibrary.org/b/id/55555-L.jpg"
        )
        mock_get.return_value = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        result = _try_openlibrary("Title", None, tmp_path, 15)
        assert result is not None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_title_only_no_author(self, mock_ol_cls, mock_rl, mock_get, tmp_path):
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [{"cover_i": 11111}]
        mock_client.get_cover_url.return_value = (
            "https://covers.openlibrary.org/b/id/11111-L.jpg"
        )
        mock_get.return_value = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        result = _try_openlibrary("Title", None, tmp_path, 15)
        assert result is not None
        # Verify search was called with author=None
        mock_client.search.assert_called_once_with(title="Title", author=None, limit=3)


# ---------------------------------------------------------------------------
# _try_google_books
# ---------------------------------------------------------------------------


class TestTryGoogleBooks:
    """Tests for _try_google_books() — Tier 3."""

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_success_with_thumbnail(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={
                "items": [
                    {
                        "volumeInfo": {
                            "imageLinks": {
                                "thumbnail": "http://books.google.com/img?zoom=1&id=abc"
                            }
                        }
                    }
                ]
            },
        )
        img_resp = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [search_resp, img_resp]
        result = _try_google_books("The Hobbit", "Tolkien", tmp_path, 15)
        assert result is not None
        # Verify URL was upgraded to https and zoom changed
        img_call_url = mock_get.call_args_list[1][0][0]
        assert img_call_url.startswith("https://")
        assert "zoom=2" in img_call_url

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_success_with_small_thumbnail_fallback(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={
                "items": [
                    {
                        "volumeInfo": {
                            "imageLinks": {
                                "smallThumbnail": "https://books.google.com/img?zoom=1&id=xyz"
                            }
                        }
                    }
                ]
            },
        )
        img_resp = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [search_resp, img_resp]
        result = _try_google_books("Title", None, tmp_path, 15)
        assert result is not None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_no_items_in_response(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={"totalItems": 0},
        )
        mock_get.return_value = search_resp
        result = _try_google_books("Nonexistent", None, tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_empty_items_list(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={"items": []},
        )
        mock_get.return_value = search_resp
        result = _try_google_books("Nonexistent", None, tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_no_image_links(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={"items": [{"volumeInfo": {"title": "No Cover"}}]},
        )
        mock_get.return_value = search_resp
        result = _try_google_books("No Cover", None, tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_search_api_error(self, mock_rl, mock_get, tmp_path):
        mock_get.return_value = _mock_response(
            status=503, content=b"", content_type="text/html"
        )
        result = _try_google_books("Title", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_network_error(self, mock_rl, mock_get, tmp_path):
        mock_get.side_effect = requests.ConnectionError("network")
        result = _try_google_books("Title", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_image_download_too_small(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={
                "items": [
                    {
                        "volumeInfo": {
                            "imageLinks": {
                                "thumbnail": "https://books.google.com/img?id=abc"
                            }
                        }
                    }
                ]
            },
        )
        img_resp = _mock_response(
            status=200, content=TINY_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [search_resp, img_resp]
        result = _try_google_books("Title", "Author", tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_query_includes_author_when_provided(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={"totalItems": 0},
        )
        mock_get.return_value = search_resp
        _try_google_books("Dune", "Frank Herbert", tmp_path, 15)
        url_called = mock_get.call_args[0][0]
        assert "inauthor%3AFrank" in url_called or "inauthor:Frank" in url_called

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_query_without_author(self, mock_rl, mock_get, tmp_path):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={"totalItems": 0},
        )
        mock_get.return_value = search_resp
        _try_google_books("Dune", None, tmp_path, 15)
        url_called = mock_get.call_args[0][0]
        assert "inauthor" not in url_called

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_malformed_json_raises_exception(self, mock_rl, mock_get, tmp_path):
        resp = Mock(spec=requests.Response)
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json.side_effect = ValueError("bad json")
        mock_get.return_value = resp
        result = _try_google_books("Title", None, tmp_path, 15)
        assert result is None

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_skips_items_without_imagelinks_finds_later_one(
        self, mock_rl, mock_get, tmp_path
    ):
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={
                "items": [
                    {"volumeInfo": {"title": "No links"}},
                    {
                        "volumeInfo": {
                            "imageLinks": {
                                "thumbnail": "https://books.google.com/img?zoom=1&id=good"
                            }
                        }
                    },
                ]
            },
        )
        img_resp = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [search_resp, img_resp]
        result = _try_google_books("Title", None, tmp_path, 15)
        assert result is not None


# ---------------------------------------------------------------------------
# resolve_cover — full integration of all tiers
# ---------------------------------------------------------------------------


class TestResolveCover:
    """Tests for resolve_cover() — orchestration and fallback."""

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_returns_none_without_output_dir(self, mock_aud, mock_ol, mock_gb):
        result = resolve_cover("Title", output_dir=None)
        assert result is None
        mock_aud.assert_not_called()

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_audible_succeeds_skips_others(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_aud.return_value = "abc123.jpg"
        result = resolve_cover("Title", asin="B00ASIN", output_dir=tmp_path)
        assert result == "abc123.jpg"
        mock_ol.assert_not_called()
        mock_gb.assert_not_called()

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_no_asin_skips_audible(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_ol.return_value = "cover.jpg"
        result = resolve_cover("Title", author="Author", output_dir=tmp_path)
        assert result == "cover.jpg"
        mock_aud.assert_not_called()

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_audible_fails_openlibrary_succeeds(
        self, mock_aud, mock_ol, mock_gb, tmp_path
    ):
        mock_aud.return_value = None
        mock_ol.return_value = "ol_cover.jpg"
        result = resolve_cover("Title", asin="B00ASIN", output_dir=tmp_path)
        assert result == "ol_cover.jpg"
        mock_gb.assert_not_called()

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_audible_and_ol_fail_google_succeeds(
        self, mock_aud, mock_ol, mock_gb, tmp_path
    ):
        mock_aud.return_value = None
        mock_ol.return_value = None
        mock_gb.return_value = "gb_cover.jpg"
        result = resolve_cover("Title", asin="B00ASIN", output_dir=tmp_path)
        assert result == "gb_cover.jpg"

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_all_tiers_fail(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_aud.return_value = None
        mock_ol.return_value = None
        mock_gb.return_value = None
        result = resolve_cover("Title", asin="B00ASIN", output_dir=tmp_path)
        assert result is None

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_creates_output_dir(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_ol.return_value = "cover.jpg"
        new_dir = tmp_path / "sub" / "deep"
        resolve_cover("Title", output_dir=new_dir)
        assert new_dir.exists()

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_title_only(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_ol.return_value = None
        mock_gb.return_value = "gb.jpg"
        result = resolve_cover("Just A Title", output_dir=tmp_path)
        assert result == "gb.jpg"
        # No ASIN → Audible not called
        mock_aud.assert_not_called()

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_title_author_asin(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_aud.return_value = "audible.jpg"
        result = resolve_cover(
            "The Hobbit", author="Tolkien", asin="B00ASIN", output_dir=tmp_path
        )
        assert result == "audible.jpg"

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_custom_timeout_passed(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_aud.return_value = None
        mock_ol.return_value = None
        mock_gb.return_value = None
        resolve_cover("Title", asin="B00X", output_dir=tmp_path, timeout=30)
        mock_aud.assert_called_once_with("B00X", tmp_path, 30)
        mock_ol.assert_called_once_with("Title", None, tmp_path, 30)
        mock_gb.assert_called_once_with("Title", None, tmp_path, 30)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    """Tests for _rate_limit()."""

    @patch("scanner.utils.cover_resolver.time.sleep")
    @patch("scanner.utils.cover_resolver.time.time")
    def test_sleeps_when_too_fast(self, mock_time, mock_sleep):
        # First call to time.time() returns current time in _rate_limit
        # _last_request_time is 0, but let's set it to simulate recent call
        cr_module._last_request_time = 100.0
        # elapsed < _MIN_DELAY → should sleep
        mock_time.return_value = 100.3  # 0.3s elapsed, need 0.6s
        _rate_limit()
        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert abs(sleep_duration - 0.3) < 0.01

    @patch("scanner.utils.cover_resolver.time.sleep")
    @patch("scanner.utils.cover_resolver.time.time")
    def test_no_sleep_when_enough_time_passed(self, mock_time, mock_sleep):
        cr_module._last_request_time = 100.0
        mock_time.return_value = 101.0  # 1.0s elapsed, > 0.6s
        _rate_limit()
        mock_sleep.assert_not_called()

    @patch("scanner.utils.cover_resolver.time.sleep")
    @patch("scanner.utils.cover_resolver.time.time")
    def test_updates_last_request_time(self, mock_time, mock_sleep):
        cr_module._last_request_time = 0.0
        mock_time.return_value = 500.0
        _rate_limit()
        assert cr_module._last_request_time == 500.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and boundary tests."""

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_empty_title(self, mock_aud, mock_ol, mock_gb, tmp_path):
        mock_ol.return_value = None
        mock_gb.return_value = None
        result = resolve_cover("", output_dir=tmp_path)
        assert result is None

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_empty_asin_not_treated_as_truthy(
        self, mock_aud, mock_ol, mock_gb, tmp_path
    ):
        """Empty string ASIN should not trigger Audible lookup."""
        mock_ol.return_value = None
        mock_gb.return_value = None
        resolve_cover("Title", asin="", output_dir=tmp_path)
        mock_aud.assert_not_called()

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_google_books_http_to_https_upgrade(self, mock_rl, mock_get, tmp_path):
        """Google Books URLs with http:// get upgraded to https://."""
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={
                "items": [
                    {
                        "volumeInfo": {
                            "imageLinks": {
                                "thumbnail": "http://books.google.com/img?zoom=1&id=test"
                            }
                        }
                    }
                ]
            },
        )
        img_resp = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [search_resp, img_resp]
        _try_google_books("Title", None, tmp_path, 15)
        img_url = mock_get.call_args_list[1][0][0]
        assert img_url.startswith("https://")
        assert "http://" not in img_url

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    def test_google_books_zoom_upgrade(self, mock_rl, mock_get, tmp_path):
        """Google Books zoom=1 gets replaced with zoom=2."""
        search_resp = _mock_response(
            status=200,
            content=b"",
            content_type="application/json",
            json_data={
                "items": [
                    {
                        "volumeInfo": {
                            "imageLinks": {
                                "thumbnail": "https://books.google.com/img?zoom=1&id=test"
                            }
                        }
                    }
                ]
            },
        )
        img_resp = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        mock_get.side_effect = [search_resp, img_resp]
        _try_google_books("Title", None, tmp_path, 15)
        img_url = mock_get.call_args_list[1][0][0]
        assert "zoom=2" in img_url
        assert "zoom=1" not in img_url

    @patch("scanner.utils.cover_resolver.requests.get")
    @patch("scanner.utils.cover_resolver._rate_limit")
    @patch("scanner.utils.cover_resolver.OpenLibraryClient")
    def test_openlibrary_cover_i_as_integer(
        self, mock_ol_cls, mock_rl, mock_get, tmp_path
    ):
        """cover_i can be an integer — should be handled directly."""
        mock_client = MagicMock()
        mock_ol_cls.return_value = mock_client
        mock_client.search.return_value = [{"cover_i": 42}]
        mock_client.get_cover_url.return_value = (
            "https://covers.openlibrary.org/b/id/42-L.jpg"
        )
        mock_get.return_value = _mock_response(
            status=200, content=VALID_IMAGE, content_type="image/jpeg"
        )
        result = _try_openlibrary("Title", None, tmp_path, 15)
        assert result is not None
        mock_client.get_cover_url.assert_called_once_with(42, size="L")

    @patch("scanner.utils.cover_resolver._try_google_books")
    @patch("scanner.utils.cover_resolver._try_openlibrary")
    @patch("scanner.utils.cover_resolver._try_audible")
    def test_none_author_and_none_asin(self, mock_aud, mock_ol, mock_gb, tmp_path):
        """Defaults: author=None, asin=None."""
        mock_ol.return_value = None
        mock_gb.return_value = None
        result = resolve_cover("Title", output_dir=tmp_path)
        assert result is None
        mock_aud.assert_not_called()
        mock_ol.assert_called_once_with("Title", None, tmp_path, 15)
        mock_gb.assert_called_once_with("Title", None, tmp_path, 15)
