"""Tests for the Audible enrichment provider.

All API calls are mocked — no network access during tests.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.enrichment.provider_audible import (
    AudibleProvider,
    _extract_categories,
    _extract_editorial_reviews,
    _extract_rating,
    _get_best_image_url,
    _parse_sequence,
)


class TestAudibleProviderCanEnrich:
    def test_can_enrich_with_asin(self):
        provider = AudibleProvider()
        assert provider.can_enrich({"asin": "B08G9PRS1K"}) is True

    def test_cannot_enrich_without_asin(self):
        provider = AudibleProvider()
        assert provider.can_enrich({"asin": ""}) is False
        assert provider.can_enrich({}) is False


class TestAudibleProviderEnrich:
    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_returns_empty_when_no_asin(self, mock_fetch):
        provider = AudibleProvider()
        result = provider.enrich({"asin": ""})
        assert result == {}
        mock_fetch.assert_not_called()

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_returns_empty_when_api_returns_none(self, mock_fetch):
        mock_fetch.return_value = None
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result == {}

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_series(self, mock_fetch):
        mock_fetch.return_value = {"series": [{"title": "Jack Reacher", "sequence": "3"}]}
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result["series"] == "Jack Reacher"
        assert result["series_sequence"] == 3.0

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_skips_series_when_already_set(self, mock_fetch):
        mock_fetch.return_value = {"series": [{"title": "Jack Reacher", "sequence": "3"}]}
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K", "series": "Existing"})
        assert "series" not in result

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_ratings(self, mock_fetch):
        mock_fetch.return_value = {
            "rating": {
                "overall_distribution": {"display_average_rating": 4.5, "num_ratings": 1200},
                "performance_distribution": {"display_average_rating": 4.7},
                "story_distribution": {"display_average_rating": 4.3},
                "num_reviews": 350,
            }
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result["rating_overall"] == 4.5
        assert result["rating_performance"] == 4.7
        assert result["rating_story"] == 4.3
        assert result["num_ratings"] == 350
        assert result["num_reviews"] == 1200

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_categories(self, mock_fetch):
        mock_fetch.return_value = {
            "category_ladders": [
                {
                    "ladder": [
                        {"name": "Literature & Fiction", "id": "1234"},
                        {"name": "Thriller", "id": "5678"},
                    ]
                }
            ]
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        cats = result["categories"]
        assert len(cats) == 2
        assert cats[0]["root_category"] == "Literature & Fiction"
        assert cats[1]["category_path"] == "Literature & Fiction > Thriller"

    @patch("scripts.enrichment.provider_audible._fetch_audible_product")
    def test_extracts_image_url(self, mock_fetch):
        mock_fetch.return_value = {
            "product_images": {
                "500": "https://example.com/500.jpg",
                "252": "https://example.com/252.jpg",
            }
        }
        provider = AudibleProvider()
        result = provider.enrich({"asin": "B08G9PRS1K"})
        assert result["audible_image_url"] == "https://example.com/500.jpg"


class TestHelpers:
    def test_parse_sequence_integer(self):
        assert _parse_sequence("5") == 5.0

    def test_parse_sequence_float(self):
        assert _parse_sequence("3.5") == 3.5

    def test_parse_sequence_embedded(self):
        assert _parse_sequence("Book 7") == 7.0

    def test_parse_sequence_empty(self):
        assert _parse_sequence("") is None

    def test_extract_categories_empty(self):
        assert _extract_categories({}) == []

    def test_extract_editorial_reviews(self):
        product = {"editorial_reviews": ["Great book!", {"review": "Amazing", "source": "NYT"}]}
        reviews = _extract_editorial_reviews(product)
        assert len(reviews) == 2
        assert reviews[0]["review_text"] == "Great book!"
        assert reviews[1]["source"] == "NYT"

    def test_extract_rating_empty(self):
        result = _extract_rating({})
        assert result["rating_overall"] is None

    def test_get_best_image_prefers_largest(self):
        images = {"252": "small.jpg", "1024": "large.jpg", "500": "medium.jpg"}
        assert _get_best_image_url({"product_images": images}) == "large.jpg"

    def test_get_best_image_none(self):
        assert _get_best_image_url({}) is None

    def test_get_best_image_falls_back_to_first_value(self):
        """When none of the preferred sizes exist, return an arbitrary
        image instead of None — any image is better than none."""
        images = {"300": "random.jpg"}
        assert _get_best_image_url({"product_images": images}) == "random.jpg"

    def test_parse_sequence_returns_none_for_empty(self):
        assert _parse_sequence("") is None

    def test_parse_sequence_handles_plain_integer_string(self):
        assert _parse_sequence("7") == 7.0

    def test_parse_sequence_extracts_number_from_prose(self):
        """Some series sequences look like '5.5' or '5 (part 1)'."""
        assert _parse_sequence("5 of 12") == 5.0

    def test_parse_sequence_returns_none_for_purely_nonnumeric(self):
        assert _parse_sequence("epilogue") is None


class TestRateLimit:
    """The _rate_limit helper sleeps when called more than once within
    the delay window. We patch time.monotonic + time.sleep to avoid
    real wallclock waits."""

    def test_rate_limit_sleeps_when_called_within_delay(self, monkeypatch):
        from scripts.enrichment import provider_audible as mod

        slept: list[float] = []

        # Arrange: _last_call_time set to 100.0. First monotonic read returns
        # 100.1 (elapsed=0.1s < _RATE_LIMIT_DELAY=0.3s → sleep 0.2s).
        monkeypatch.setattr(mod, "_last_call_time", 100.0)
        fake_times = iter([100.1, 100.3])  # second call updates state after sleep
        monkeypatch.setattr(mod.time, "monotonic", lambda: next(fake_times))
        monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))

        mod._rate_limit()

        assert len(slept) == 1
        assert slept[0] == pytest_approx(0.2)

    def test_rate_limit_skips_sleep_when_delay_already_elapsed(self, monkeypatch):
        from scripts.enrichment import provider_audible as mod

        slept: list[float] = []
        monkeypatch.setattr(mod, "_last_call_time", 0.0)
        # Large gap: elapsed > delay, sleep NOT called.
        monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)
        monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))

        mod._rate_limit()

        assert slept == []


class TestFetchAudibleProduct:
    """Exercise the error-handling branches in _fetch_audible_product.
    All urllib calls are mocked so no network traffic occurs."""

    def test_fetch_returns_product_on_success(self, monkeypatch):
        import json

        from scripts.enrichment import provider_audible as mod

        monkeypatch.setattr(mod, "_rate_limit", lambda: None)

        class _FakeResp:
            def __init__(self, body: bytes):
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        payload = json.dumps({"product": {"asin": "B123", "title": "T"}}).encode()

        def _fake_urlopen(req, timeout):
            return _FakeResp(payload)

        monkeypatch.setattr(mod.urllib.request, "urlopen", _fake_urlopen)
        result = mod._fetch_audible_product("B123")
        assert result == {"asin": "B123", "title": "T"}

    def test_fetch_returns_none_on_404(self, monkeypatch):
        import urllib.error

        from scripts.enrichment import provider_audible as mod

        monkeypatch.setattr(mod, "_rate_limit", lambda: None)

        def _raise_404(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        monkeypatch.setattr(mod.urllib.request, "urlopen", _raise_404)
        assert mod._fetch_audible_product("B000MISSING") is None

    def test_fetch_retries_once_on_429_then_returns_product(self, monkeypatch):
        """A 429 rate-limit response should trigger a single retry after
        a short sleep, and the retry's successful response is returned."""
        import json
        import urllib.error

        from scripts.enrichment import provider_audible as mod

        monkeypatch.setattr(mod, "_rate_limit", lambda: None)
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)

        class _FakeResp:
            def read(self):
                return json.dumps({"product": {"asin": "B429"}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        calls = {"count": 0}

        def _maybe_429(req, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return _FakeResp()

        monkeypatch.setattr(mod.urllib.request, "urlopen", _maybe_429)
        result = mod._fetch_audible_product("B429")
        assert result == {"asin": "B429"}
        assert calls["count"] == 2

    def test_fetch_returns_none_when_429_retry_also_fails(self, monkeypatch):
        import urllib.error

        from scripts.enrichment import provider_audible as mod

        monkeypatch.setattr(mod, "_rate_limit", lambda: None)
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)

        calls = {"count": 0}

        def _both_fail(req, timeout):
            calls["count"] += 1
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

        monkeypatch.setattr(mod.urllib.request, "urlopen", _both_fail)
        assert mod._fetch_audible_product("B429x") is None
        assert calls["count"] == 2

    def test_fetch_returns_none_on_url_error(self, monkeypatch):
        import urllib.error

        from scripts.enrichment import provider_audible as mod

        monkeypatch.setattr(mod, "_rate_limit", lambda: None)

        def _url_error(req, timeout):
            raise urllib.error.URLError("network unreachable")

        monkeypatch.setattr(mod.urllib.request, "urlopen", _url_error)
        assert mod._fetch_audible_product("Bnet") is None

    def test_fetch_returns_none_on_500(self, monkeypatch):
        """Non-404/429 HTTP errors fall through to the generic None path."""
        import urllib.error

        from scripts.enrichment import provider_audible as mod

        monkeypatch.setattr(mod, "_rate_limit", lambda: None)

        def _five_hundred(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, None)

        monkeypatch.setattr(mod.urllib.request, "urlopen", _five_hundred)
        assert mod._fetch_audible_product("B500") is None


def pytest_approx(value, tol=1e-6):
    """Lightweight approx helper to avoid pulling pytest.approx into
    helper modules — tolerates float rounding when comparing sleeps."""

    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol

    return _Approx()
