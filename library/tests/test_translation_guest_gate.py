"""Guests must not queue or trigger translation/STT/TTS work (bd gau).

Operator decision 2026-10-13: every route that enqueues or triggers
translation work needs an authenticated user. Guests keep read access to
cached content. AUTH_ENABLED=false (standalone) behaves like every other
protected route: allowed through.

Each work route is hit as a guest with a spy on the work sink; the spy must
record NO call. The same request as a signed-in user must reach the sink.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from backend.api_modular import streaming_translate as st
from backend.api_modular import translations as tr

AUTH_SHARED = "backend.api_modular.auth_shared"


@pytest.fixture
def streaming_db(flask_app, session_temp_dir):
    db_path = session_temp_dir / "test_audiobooks.db"
    saved_tr_db = tr._db_path
    st._db_path = db_path
    tr._db_path = db_path  # other modules rebind it; pin to the session DB
    st._chapter_count_memo.clear()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO audiobooks "
        "(id, title, file_path, format, duration_hours, chapter_count) "
        "VALUES (1, 'Gate Book', '/nonexistent/gate.opus', 'opus', 1.0, 3)"
    )
    conn.commit()
    conn.close()
    yield db_path
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM streaming_segments")
    conn.execute("DELETE FROM streaming_sessions")
    conn.execute("DELETE FROM audiobooks WHERE id = 1")
    conn.commit()
    conn.close()
    tr._db_path = saved_tr_db
    st._chapter_count_memo.clear()


@pytest.fixture
def auth_on(flask_app):
    old = flask_app.config.get("AUTH_ENABLED", False)
    flask_app.config["AUTH_ENABLED"] = True
    yield
    flask_app.config["AUTH_ENABLED"] = old


@pytest.fixture
def as_guest(auth_on):
    with patch(f"{AUTH_SHARED}.get_current_user", return_value=None):
        yield


@pytest.fixture
def as_user(auth_on):
    with patch(f"{AUTH_SHARED}.get_current_user", return_value=MagicMock(id=7, is_admin=False)):
        yield


def _segment_rows(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM streaming_segments").fetchone()[0]
    finally:
        conn.close()


# (method, url, json, spy target, expected-reached-by-user)
TRIGGER_ROUTES = [
    (
        "/api/i18n/activate",
        {"locale": "zh-Hans"},
        "localization.queue.enqueue_all_books_for_locale",
    ),
    (
        "/api/translation/bump",
        {"audiobook_id": 1, "locale": "zh-Hans"},
        "localization.queue.enqueue",
    ),
    (
        "/api/translate/sampler/activate",
        {"audiobook_id": 1, "locale": "zh-Hans", "chapter_index": 0, "segment_index": 0},
        "backend.api_modular.streaming_translate._ensure_chapter_segments",
    ),
    ("/api/translate/warmup", {}, "backend.api_modular.streaming_translate._get_db"),
    (
        "/api/translate/stop",
        {"audiobook_id": 1, "locale": "zh-Hans"},
        "backend.api_modular.streaming_translate.stop_streaming_impl",
    ),
]


def _rv(url):
    """Sampler activate serialises the sink's return value as an int."""
    return {"return_value": 0} if url.endswith("sampler/activate") else {}


class TestPureTriggerRoutes:
    @pytest.mark.parametrize("url,body,target", TRIGGER_ROUTES)
    def test_guest_gets_401_and_no_work(
        self, app_client, streaming_db, as_guest, url, body, target
    ):
        with patch(target, **_rv(url)) as spy:
            resp = app_client.post(url, json=body)
        assert resp.status_code == 401, resp.get_data(as_text=True)
        spy.assert_not_called()
        assert _segment_rows(streaming_db) == 0

    @pytest.mark.parametrize("url,body,target", TRIGGER_ROUTES)
    def test_user_reaches_work(self, app_client, streaming_db, as_user, url, body, target):
        with patch(target, **_rv(url)) as spy:
            app_client.post(url, json=body)
        spy.assert_called()

    @pytest.mark.parametrize("url,body,target", TRIGGER_ROUTES)
    def test_standalone_unchanged(self, app_client, streaming_db, flask_app, url, body, target):
        assert not flask_app.config.get("AUTH_ENABLED", False)
        with patch(target, **_rv(url)) as spy:
            app_client.post(url, json=body)
        spy.assert_called()


class TestStreamAndSeek:
    def test_stream_guest_uncached_401_no_segments(self, app_client, streaming_db, as_guest):
        with patch.object(st, "_ensure_chapter_segments") as spy:
            resp = app_client.post(
                "/api/translate/stream", json={"audiobook_id": 1, "locale": "zh-Hans"}
            )
        assert resp.status_code == 401
        assert resp.get_json()["state"] == "login_required"
        spy.assert_not_called()
        assert _segment_rows(streaming_db) == 0

    def test_stream_guest_cached_still_200(self, app_client, streaming_db, as_guest):
        with (
            patch.object(st, "_has_cached_subtitles", return_value=True),
            patch.object(st, "_has_cached_audio", return_value=True),
            patch.object(st, "_ensure_chapter_segments") as spy,
        ):
            resp = app_client.post(
                "/api/translate/stream", json={"audiobook_id": 1, "locale": "zh-Hans"}
            )
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "cached"
        spy.assert_not_called()

    def test_stream_user_uncached_queues(self, app_client, streaming_db, as_user):
        resp = app_client.post(
            "/api/translate/stream", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        assert _segment_rows(streaming_db) > 0

    def test_stream_standalone_queues(self, app_client, streaming_db):
        resp = app_client.post(
            "/api/translate/stream", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        assert _segment_rows(streaming_db) > 0

    def test_seek_guest_uncached_401_no_segments(self, app_client, streaming_db, as_guest):
        with patch.object(st, "_ensure_chapter_segments") as spy:
            resp = app_client.post(
                "/api/translate/seek",
                json={
                    "audiobook_id": 1,
                    "locale": "zh-Hans",
                    "chapter_index": 0,
                    "segment_index": 2,
                },
            )
        assert resp.status_code == 401
        spy.assert_not_called()
        assert _segment_rows(streaming_db) == 0

    def test_seek_guest_cached_segment_200(self, app_client, streaming_db, as_guest):
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (1, 0, 2, 'zh-Hans', 'completed', 0)"
        )
        conn.commit()
        conn.close()
        resp = app_client.post(
            "/api/translate/seek",
            json={"audiobook_id": 1, "locale": "zh-Hans", "chapter_index": 0, "segment_index": 2},
        )
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "cached"

    def test_seek_user_and_standalone_queue(self, app_client, streaming_db, as_user):
        body = {"audiobook_id": 1, "locale": "zh-Hans", "chapter_index": 0, "segment_index": 1}
        assert app_client.post("/api/translate/seek", json=body).status_code == 200
        assert _segment_rows(streaming_db) > 0


class TestCacheOrWorkReadRoutes:
    """Guest: cache served (200, tagged), provider never called. User: provider called."""

    def test_by_locale_guest_cache_only(self, app_client, streaming_db, as_guest):
        with patch("backend.api_modular.translations._translate_missing") as spy:
            resp = app_client.get("/api/translations/by-locale/zh-Hans?ids=1,2,3")
        assert resp.status_code == 200
        assert resp.headers["X-Translation-Guest"] == "cache-only"
        spy.assert_not_called()

    def test_by_locale_guest_no_ids_plain_read(self, app_client, streaming_db, as_guest):
        resp = app_client.get("/api/translations/by-locale/zh-Hans")
        assert resp.status_code == 200
        assert "X-Translation-Guest" not in resp.headers

    def test_by_locale_user_translates(self, app_client, streaming_db, as_user):
        with patch("backend.api_modular.translations._translate_missing") as spy:
            resp = app_client.get("/api/translations/by-locale/zh-Hans?ids=1,2,3")
        assert resp.status_code == 200
        spy.assert_called_once()

    def test_by_locale_standalone_translates(self, app_client, streaming_db):
        with patch("backend.api_modular.translations._translate_missing") as spy:
            app_client.get("/api/translations/by-locale/zh-Hans?ids=1,2,3")
        spy.assert_called_once()

    def test_collections_guest_cache_only(self, app_client, streaming_db, as_guest):
        with patch("backend.api_modular.translations._translate_missing_collections") as spy:
            resp = app_client.get("/api/translations/collections/zh-Hans")
        assert resp.status_code == 200
        assert resp.headers.get("X-Translation-Guest") == "cache-only"
        spy.assert_not_called()

    def test_collections_user_translates(self, app_client, streaming_db, as_user):
        with patch("backend.api_modular.translations._translate_missing_collections") as spy:
            app_client.get("/api/translations/collections/zh-Hans")
        spy.assert_called_once()

    def test_strings_guest_cache_only(self, app_client, streaming_db, as_guest):
        with patch("backend.api_modular.translations._translate_and_cache_strings") as spy:
            resp = app_client.post(
                "/api/translations/strings",
                json={"locale": "zh-Hans", "strings": ["gate-test-uncached-string"]},
            )
        assert resp.status_code == 200
        assert resp.headers.get("X-Translation-Guest") == "cache-only"
        spy.assert_not_called()

    def test_strings_user_translates(self, app_client, streaming_db, as_user):
        with patch("backend.api_modular.translations._translate_and_cache_strings") as spy:
            app_client.post(
                "/api/translations/strings",
                json={"locale": "zh-Hans", "strings": ["gate-test-uncached-string"]},
            )
        spy.assert_called_once()

    def test_strings_standalone_translates(self, app_client, streaming_db):
        with patch("backend.api_modular.translations._translate_and_cache_strings") as spy:
            app_client.post(
                "/api/translations/strings",
                json={"locale": "zh-Hans", "strings": ["gate-test-uncached-string"]},
            )
        spy.assert_called_once()

    def test_on_demand_guest_cache_only(self, app_client, streaming_db, as_guest):
        with patch("backend.api_modular.translations._do_on_demand_translation") as spy:
            resp = app_client.post(
                "/api/translations/on-demand", json={"locale": "zh-Hans", "audiobook_ids": [1]}
            )
        assert resp.status_code == 200
        assert resp.headers.get("X-Translation-Guest") == "cache-only"
        spy.assert_not_called()

    def test_on_demand_user_translates(self, app_client, streaming_db, as_user):
        with patch("backend.api_modular.translations._do_on_demand_translation") as spy:
            app_client.post(
                "/api/translations/on-demand", json={"locale": "zh-Hans", "audiobook_ids": [1]}
            )
        spy.assert_called_once()

    def test_on_demand_standalone_translates(self, app_client, streaming_db):
        with patch("backend.api_modular.translations._do_on_demand_translation") as spy:
            app_client.post(
                "/api/translations/on-demand", json={"locale": "zh-Hans", "audiobook_ids": [1]}
            )
        spy.assert_called_once()


class TestGenerationRequestRoutes:
    @pytest.mark.parametrize(
        "url,sink",
        [
            ("/api/user/subtitles/request", "backend.api_modular.subtitles._start_generation"),
            (
                "/api/user/translated-audio/request",
                "backend.api_modular.translated_audio.threading.Thread",
            ),
        ],
    )
    def test_guest_401_no_generation(self, app_client, streaming_db, as_guest, url, sink):
        with patch(sink) as spy:
            resp = app_client.post(url, json={"audiobook_id": 1, "locale": "zh-Hans"})
        assert resp.status_code == 401
        spy.assert_not_called()


class TestI18nReloadAdminOnly:
    """POST /api/i18n/reload is not listening: admin, or localhost in standalone."""

    SINK = "backend.api_modular.i18n_routes.reload_catalogs"

    def test_guest_401_no_reload(self, app_client, as_guest):
        with patch(self.SINK) as spy:
            resp = app_client.post("/api/i18n/reload")
        assert resp.status_code == 401
        spy.assert_not_called()

    def test_non_admin_403_no_reload(self, app_client, as_user):
        with patch(self.SINK) as spy:
            resp = app_client.post("/api/i18n/reload")
        assert resp.status_code == 403
        spy.assert_not_called()

    def test_admin_ok(self, app_client, auth_on):
        admin = MagicMock(id=1, is_admin=True)
        with patch(f"{AUTH_SHARED}.get_current_user", return_value=admin), patch(self.SINK) as spy:
            resp = app_client.post("/api/i18n/reload")
        assert resp.status_code == 200
        spy.assert_called_once()

    def test_standalone_localhost_ok(self, app_client):
        with patch(self.SINK) as spy:
            resp = app_client.post("/api/i18n/reload", environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert resp.status_code == 200
        spy.assert_called_once()

    def test_standalone_remote_404_no_reload(self, app_client):
        with patch(self.SINK) as spy:
            resp = app_client.post("/api/i18n/reload", environ_base={"REMOTE_ADDR": "203.0.113.1"})
        assert resp.status_code == 404
        spy.assert_not_called()
