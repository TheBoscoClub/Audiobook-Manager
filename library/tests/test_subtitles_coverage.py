"""Coverage-focused tests for ``library.backend.api_modular.subtitles``.

Exercises the GET endpoints, validation paths on POST endpoints, and the
user_request cooldown/already-running logic. The background thread that
actually spawns STT (``_start_generation`` → ``_generate``) is patched out
— its GPU-bound execution is covered by VM integration tests, not unit
tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.api_modular import subtitles as sub


@pytest.fixture
def subtitles_db(flask_app, session_temp_dir):
    """Seed a pair of audiobooks + subtitles for the session DB."""
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    sub._db_path = db_path
    # subtitles.init_translated_audio_routes also sets _library_path via init,
    # but for these tests send_file is exercised only with an absolute path.
    sub._library_path = session_temp_dir

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM chapter_subtitles WHERE audiobook_id IN (20, 21)")
    conn.execute("DELETE FROM audiobooks WHERE id IN (20, 21)")
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, "
        "duration_hours, content_type) "
        "VALUES (20, 'Sub Test 20', 'Author', '/tmp/sub20.opus', 'opus', "
        "5.0, 'Product')"
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, "
        "duration_hours, content_type) "
        "VALUES (21, 'Sub Test 21', 'Author', '/nonexistent/sub21.opus', 'opus', "
        "5.0, 'Product')"
    )
    conn.commit()
    conn.close()

    # Clear in-memory job registry and user cooldowns.
    sub._job_status.clear()
    sub._user_requests.clear()

    yield db_path

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM chapter_subtitles WHERE audiobook_id IN (20, 21)")
    conn.execute("DELETE FROM audiobooks WHERE id IN (20, 21)")
    conn.commit()
    conn.close()


# ── _set_status / _get_status ──


class TestJobStatusRegistry:
    def setup_method(self):
        sub._job_status.clear()

    def test_get_missing_returns_none(self):
        assert sub._get_status(999, "zh-Hans") is None

    def test_set_and_get_roundtrip(self):
        sub._set_status(99, "ja", state="running", phase="transcribing")
        s = sub._get_status(99, "ja")
        assert s["state"] == "running"
        assert s["phase"] == "transcribing"
        assert "updated_at" in s

    def test_subsequent_set_merges(self):
        sub._set_status(99, "zh-Hans", state="starting")
        sub._set_status(99, "zh-Hans", phase="gpu_spinup")
        s = sub._get_status(99, "zh-Hans")
        assert s["state"] == "starting"
        assert s["phase"] == "gpu_spinup"

    def test_returns_copy_not_reference(self):
        sub._set_status(50, "zh-Hans", state="running")
        snap = sub._get_status(50, "zh-Hans")
        snap["state"] = "MUTATED"
        assert sub._get_status(50, "zh-Hans")["state"] == "running"


# ── GET /api/audiobooks/<id>/subtitles ──


class TestGetBookSubtitles:
    def test_empty_returns_empty_list(self, app_client, subtitles_db):
        resp = app_client.get("/api/audiobooks/20/subtitles")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_rows(self, app_client, subtitles_db):
        conn = sqlite3.connect(str(subtitles_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (20, 0, 'en', '/tmp/c0.en.vtt', 'whisper')"
        )
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (20, 0, 'zh-Hans', '/tmp/c0.zh.vtt', 'whisper')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/20/subtitles")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 2

    def test_filter_by_locale(self, app_client, subtitles_db):
        conn = sqlite3.connect(str(subtitles_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (20, 0, 'en', '/tmp/en.vtt')"
        )
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (20, 0, 'zh-Hans', '/tmp/zh.vtt')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/20/subtitles?locale=zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 1
        assert body[0]["locale"] == "zh-Hans"


# ── GET /api/audiobooks/<id>/subtitles/<idx>/<locale> ──


class TestGetChapterSubtitle:
    def test_not_in_db_404(self, app_client, subtitles_db):
        resp = app_client.get("/api/audiobooks/20/subtitles/0/zh-Hans")
        assert resp.status_code == 404

    def test_missing_vtt_on_disk_404(self, app_client, subtitles_db):
        conn = sqlite3.connect(str(subtitles_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (20, 0, 'zh-Hans', '/nonexistent/missing.vtt')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/20/subtitles/0/zh-Hans")
        assert resp.status_code == 404
        assert "missing" in resp.get_json()["error"].lower()

    def test_serves_existing_vtt(self, app_client, subtitles_db, tmp_path: Path):
        vtt = tmp_path / "test.vtt"
        vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhi\n")

        conn = sqlite3.connect(str(subtitles_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (20, 0, 'en', ?)",
            (str(vtt),),
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/20/subtitles/0/en")
        assert resp.status_code == 200
        assert "vtt" in resp.headers.get("Content-Type", "").lower()


# ── POST /api/subtitles/generate (validation only) ──


class TestGenerateSubtitles:
    def test_no_body_400(self, app_client, subtitles_db):
        resp = app_client.post(
            "/api/subtitles/generate",
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_missing_audiobook_id_400(self, app_client, subtitles_db):
        resp = app_client.post("/api/subtitles/generate", json={"locale": "zh-Hans"})
        assert resp.status_code == 400

    def test_book_not_found_404(self, app_client, subtitles_db):
        resp = app_client.post(
            "/api/subtitles/generate",
            json={"audiobook_id": 9999, "locale": "zh-Hans"},
        )
        assert resp.status_code == 404

    def test_audio_file_missing_404(self, app_client, subtitles_db):
        # Book 21 points at /nonexistent/sub21.opus.
        resp = app_client.post(
            "/api/subtitles/generate",
            json={"audiobook_id": 21, "locale": "zh-Hans"},
        )
        assert resp.status_code == 404
        assert "disk" in resp.get_json()["error"].lower()

    def test_happy_path_starts_generation(
        self, app_client, subtitles_db, tmp_path: Path
    ):
        audio = tmp_path / "audio.opus"
        audio.write_bytes(b"fake")
        conn = sqlite3.connect(str(subtitles_db))
        conn.execute(
            "UPDATE audiobooks SET file_path = ? WHERE id = 20",
            (str(audio),),
        )
        conn.commit()
        conn.close()

        with patch.object(sub, "_start_generation") as mock_start:
            resp = app_client.post(
                "/api/subtitles/generate",
                json={"audiobook_id": 20, "locale": "zh-Hans"},
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["status"] == "started"
            mock_start.assert_called_once()


# ── GET /api/subtitles/status/<id>/<locale> ──


class TestSubtitleJobStatus:
    def test_idle_when_no_job(self, app_client, subtitles_db):
        # Ensure registry is clean for this key before asserting.
        sub._job_status.pop((20, "zh-Hans"), None)
        resp = app_client.get("/api/subtitles/status/20/zh-Hans")
        assert resp.status_code == 200
        assert resp.get_json() == {"state": "idle"}

    def test_returns_stored_state(self, app_client, subtitles_db):
        sub._set_status(20, "zh-Hans", state="running", phase="transcribing")
        resp = app_client.get("/api/subtitles/status/20/zh-Hans")
        body = resp.get_json()
        assert body["audiobook_id"] == 20
        assert body["locale"] == "zh-Hans"
        assert body["state"] == "running"
        assert body["phase"] == "transcribing"


# ── POST /api/user/subtitles/request ──


class TestUserRequestSubtitles:
    def test_missing_audiobook_id_400(self, app_client, subtitles_db):
        resp = app_client.post(
            "/api/user/subtitles/request", json={"locale": "zh-Hans"}
        )
        assert resp.status_code == 400

    def test_book_not_found_404(self, app_client, subtitles_db):
        with patch.object(sub, "_start_generation"):
            resp = app_client.post(
                "/api/user/subtitles/request",
                json={"audiobook_id": 9999, "locale": "zh-Hans"},
            )
            assert resp.status_code == 404

    def test_audio_missing_404(self, app_client, subtitles_db):
        with patch.object(sub, "_start_generation"):
            resp = app_client.post(
                "/api/user/subtitles/request",
                json={"audiobook_id": 21, "locale": "zh-Hans"},
            )
            assert resp.status_code == 404

    def test_existing_running_job_returns_already_running(
        self, app_client, subtitles_db
    ):
        sub._set_status(20, "zh-Hans", state="running", phase="transcribing")
        resp = app_client.post(
            "/api/user/subtitles/request",
            json={"audiobook_id": 20, "locale": "zh-Hans"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "already_running"
        # Existing status fields are merged into the response.
        assert body["phase"] == "transcribing"

    def test_happy_path_starts_generation(
        self, app_client, subtitles_db, tmp_path: Path
    ):
        audio = tmp_path / "audio.opus"
        audio.write_bytes(b"fake")
        conn = sqlite3.connect(str(subtitles_db))
        conn.execute(
            "UPDATE audiobooks SET file_path = ? WHERE id = 20",
            (str(audio),),
        )
        conn.commit()
        conn.close()

        with patch.object(sub, "_start_generation") as mock_start:
            resp = app_client.post(
                "/api/user/subtitles/request",
                json={"audiobook_id": 20, "locale": "zh-Hans"},
            )
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["status"] == "started"
            mock_start.assert_called_once()

    def test_non_json_body_still_returns_400_on_missing_id(
        self, app_client, subtitles_db
    ):
        # silent=True → get_json returns None → falls through to "or {}".
        resp = app_client.post(
            "/api/user/subtitles/request",
            data="xxxx",
            content_type="text/plain",
        )
        assert resp.status_code == 400
