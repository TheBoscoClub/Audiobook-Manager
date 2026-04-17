"""Coverage-focused tests for ``library.backend.api_modular.i18n_routes``.

Exercises the HTTP endpoints for locale catalog delivery, activation, and
translation queue interaction. The queue helpers are patched rather than
invoking the real localization pipeline — these tests are about the Flask
wiring (routing, status codes, JSON shape, error handling) and not the
underlying queue, which is covered by ``test_localization_queue_coverage``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def i18n_client(flask_app):
    """Flask test client scoped to a single test."""
    with flask_app.test_client() as client:
        yield client


# ── GET /api/i18n/supported ──


class TestSupportedLocales:
    def test_returns_default_and_sorted_supported_list(self, i18n_client):
        resp = i18n_client.get("/api/i18n/supported")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "default" in body
        assert "supported" in body
        assert isinstance(body["supported"], list)
        # Response is sorted — compare to a sorted copy.
        assert body["supported"] == sorted(body["supported"])
        # The default must appear in the supported list.
        assert body["default"] in body["supported"]


# ── GET /api/i18n/<locale> ──


class TestLocaleCatalog:
    def test_unsupported_locale_returns_404(self, i18n_client):
        resp = i18n_client.get("/api/i18n/xx-Fake")
        assert resp.status_code == 404
        body = resp.get_json()
        assert "error" in body
        assert "xx-Fake" in body["error"]

    def test_supported_locale_returns_catalog_with_cache_header(self, i18n_client):
        resp = i18n_client.get("/api/i18n/en")
        assert resp.status_code == 200
        # Catalogs are dicts of nested strings.
        assert isinstance(resp.get_json(), dict)
        # Long cache — catalogs change only on deploy.
        assert "Cache-Control" in resp.headers
        assert "max-age=3600" in resp.headers["Cache-Control"]


# ── POST /api/i18n/reload ──


class TestReloadCatalogs:
    def test_reload_returns_ok(self, i18n_client):
        with patch("backend.api_modular.i18n_routes.reload_catalogs") as mock_reload:
            resp = i18n_client.post("/api/i18n/reload")
            assert resp.status_code == 200
            assert resp.get_json() == {"status": "ok"}
            mock_reload.assert_called_once()


# ── POST /api/i18n/activate ──


class TestActivateLocale:
    def test_activate_with_empty_body_returns_zero(self, i18n_client):
        resp = i18n_client.post("/api/i18n/activate", json={})
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "queued": 0}

    def test_activate_with_english_skips_queue(self, i18n_client):
        resp = i18n_client.post("/api/i18n/activate", json={"locale": "en"})
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "queued": 0}

    def test_activate_missing_locale_field_skips_queue(self, i18n_client):
        resp = i18n_client.post("/api/i18n/activate", json={"foo": "bar"})
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "queued": 0}

    def test_activate_with_locale_calls_queue(self, i18n_client):
        with (
            patch("localization.queue.enqueue_all_books_for_locale") as mock_enq,
            patch(
                "localization.queue.get_queue_status",
                return_value={"pending": 7, "processing": 0},
            ),
        ):
            resp = i18n_client.post(
                "/api/i18n/activate", json={"locale": "zh-Hans"}
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["status"] == "ok"
            assert body["queued"] == 7
            mock_enq.assert_called_once_with("zh-Hans")

    def test_activate_handles_exception(self, i18n_client):
        with patch(
            "localization.queue.enqueue_all_books_for_locale",
            side_effect=RuntimeError("boom"),
        ):
            resp = i18n_client.post(
                "/api/i18n/activate", json={"locale": "es"}
            )
            assert resp.status_code == 500
            body = resp.get_json()
            assert body["status"] == "error"
            assert "Internal server error" in body["error"]

    def test_activate_with_non_json_body(self, i18n_client):
        # request.get_json(silent=True) returns None for non-JSON; the code
        # falls through to the "or {}" branch.
        resp = i18n_client.post(
            "/api/i18n/activate", data="not-json", content_type="text/plain"
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "queued": 0}


# ── GET /api/translation/queue ──


class TestTranslationQueueStatus:
    def test_queue_returns_status_dict(self, i18n_client):
        payload = {"pending": 4, "processing": 1, "completed": 0, "failed": 0}
        with patch(
            "localization.queue.get_queue_status",
            return_value=payload,
        ):
            resp = i18n_client.get("/api/translation/queue")
            assert resp.status_code == 200
            assert resp.get_json() == payload

    def test_queue_returns_500_on_exception(self, i18n_client):
        with patch(
            "localization.queue.get_queue_status",
            side_effect=RuntimeError("db down"),
        ):
            resp = i18n_client.get("/api/translation/queue")
            assert resp.status_code == 500
            body = resp.get_json()
            assert "error" in body


# ── POST /api/translation/bump ──


class TestBumpPriority:
    def test_bump_without_audiobook_id_is_ok(self, i18n_client):
        resp = i18n_client.post("/api/translation/bump", json={})
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_bump_with_english_is_ok(self, i18n_client):
        resp = i18n_client.post(
            "/api/translation/bump",
            json={"audiobook_id": 1, "locale": "en"},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_bump_with_missing_locale_is_ok(self, i18n_client):
        resp = i18n_client.post(
            "/api/translation/bump", json={"audiobook_id": 1}
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok"}

    def test_bump_calls_queue_helpers(self, i18n_client):
        with (
            patch("localization.queue.bump_priority") as mock_bump,
            patch("localization.queue.enqueue") as mock_enqueue,
        ):
            resp = i18n_client.post(
                "/api/translation/bump",
                json={"audiobook_id": 42, "locale": "zh-Hans"},
            )
            assert resp.status_code == 200
            assert resp.get_json() == {"status": "ok"}
            mock_bump.assert_called_once_with(42, "zh-Hans", priority=100)
            mock_enqueue.assert_called_once_with(
                42, "zh-Hans", priority=100, start_worker=True
            )

    def test_bump_handles_exception(self, i18n_client):
        with patch(
            "localization.queue.bump_priority",
            side_effect=RuntimeError("oops"),
        ):
            resp = i18n_client.post(
                "/api/translation/bump",
                json={"audiobook_id": 5, "locale": "ja"},
            )
            assert resp.status_code == 500
            assert resp.get_json()["status"] == "error"


# ── GET /api/translation/status/<id>/<locale> ──


class TestBookTranslationStatus:
    def test_status_not_queued_returns_marker(self, i18n_client):
        with patch(
            "localization.queue.get_book_translation_status",
            return_value=None,
        ):
            resp = i18n_client.get("/api/translation/status/7/zh-Hans")
            assert resp.status_code == 200
            assert resp.get_json() == {"state": "not_queued"}

    def test_status_returns_dict_when_queued(self, i18n_client):
        payload = {"state": "pending", "step": "stt", "priority": 0}
        with patch(
            "localization.queue.get_book_translation_status",
            return_value=payload,
        ):
            resp = i18n_client.get("/api/translation/status/11/es")
            assert resp.status_code == 200
            assert resp.get_json() == payload

    def test_status_returns_500_on_exception(self, i18n_client):
        with patch(
            "localization.queue.get_book_translation_status",
            side_effect=RuntimeError("db"),
        ):
            resp = i18n_client.get("/api/translation/status/99/fr")
            assert resp.status_code == 500
            assert "error" in resp.get_json()
