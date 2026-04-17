"""
Tests for the translated audio API blueprint.

Covers the HTTP endpoints that expose TTS-generated translated chapter
audio, plus the internal job-status registry used by the admin
generate / user request / status-poll endpoints.

The actual TTS + FFmpeg paths are heavyweight and network-bound (Vast.ai
/ RunPod), so those async code paths are intentionally not exercised
here; instead we verify the synchronous request-validation layer and
status-tracking infrastructure that wraps them.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.api_modular import translated_audio as ta
from backend.api_modular.translated_audio import (
    _get_status,
    _job_status,
    _set_status,
    _user_requests,
)


# ── Pure helpers (job status registry) ──


class TestJobStatusRegistry:
    """_set_status / _get_status — thread-safe progress tracking."""

    def setup_method(self):
        _job_status.clear()

    def test_get_missing_returns_none(self):
        assert _get_status(999, "zh-Hans") is None

    def test_set_and_get_roundtrip(self):
        _set_status(1, "zh-Hans", state="running", phase="synth", message="hi")
        st = _get_status(1, "zh-Hans")
        assert st is not None
        assert st["state"] == "running"
        assert st["phase"] == "synth"
        assert st["message"] == "hi"
        assert "updated_at" in st

    def test_subsequent_set_merges(self):
        _set_status(2, "ja", state="starting")
        _set_status(2, "ja", phase="gpu_spinup")
        st = _get_status(2, "ja")
        assert st["state"] == "starting"
        assert st["phase"] == "gpu_spinup"

    def test_status_is_isolated_per_locale(self):
        _set_status(3, "ja", state="running")
        _set_status(3, "zh-Hans", state="completed")
        assert _get_status(3, "ja")["state"] == "running"
        assert _get_status(3, "zh-Hans")["state"] == "completed"

    def test_returns_copy_not_reference(self):
        _set_status(4, "zh-Hans", state="running")
        snapshot = _get_status(4, "zh-Hans")
        snapshot["state"] = "MUTATED"
        assert _get_status(4, "zh-Hans")["state"] == "running"


# ── HTTP endpoints ──


@pytest.fixture
def audio_db(flask_app, session_temp_dir):
    """Clean the chapter_translations_audio table and seed an audiobook.

    Re-binds ``ta._db_path`` to the session DB because other test modules
    (e.g. ``test_enriched_api.py``) spin up their own Flask app with a
    different DB and overwrite the module-level global.
    """
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    ta._db_path = db_path

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM chapter_translations_audio")
    conn.execute("DELETE FROM chapter_subtitles")
    conn.execute("DELETE FROM audiobooks WHERE id IN (1, 2, 5)")
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type) "
        "VALUES (1, 'Audio Test 1', 'Author', '/tmp/test1.opus', 'opus', 5.0, 'Product')"
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type) "
        "VALUES (5, 'Audio Test 5', 'Author', '/tmp/test5.opus', 'opus', 5.0, 'Product')"
    )
    conn.commit()
    conn.close()

    # Clear the in-memory job registry too
    _job_status.clear()
    _user_requests.clear()

    yield db_path

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM chapter_translations_audio")
    conn.execute("DELETE FROM chapter_subtitles")
    conn.execute("DELETE FROM audiobooks WHERE id IN (1, 2, 5)")
    conn.commit()
    conn.close()


class TestGetBookTranslatedAudio:
    """GET /api/audiobooks/<id>/translated-audio"""

    def test_empty_returns_empty_list(self, app_client, audio_db):
        resp = app_client.get("/api/audiobooks/1/translated-audio")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_entries_for_book(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/c0.opus', 'edge-tts')"
        )
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 1, 'zh-Hans', '/tmp/c1.opus', 'edge-tts')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translated-audio")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 2
        assert body[0]["chapter_index"] == 0
        assert body[1]["chapter_index"] == 1

    def test_filter_by_locale(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/zh.opus', 'edge-tts')"
        )
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'ja', '/tmp/ja.opus', 'edge-tts')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translated-audio?locale=zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 1
        assert body[0]["locale"] == "zh-Hans"


class TestStreamTranslatedChapter:
    """GET /api/audiobooks/<id>/translated-audio/<idx>/<locale>"""

    def test_not_in_db_404(self, app_client, audio_db):
        resp = app_client.get("/api/audiobooks/1/translated-audio/0/zh-Hans")
        assert resp.status_code == 404

    def test_file_missing_from_disk_404(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', '/nonexistent/missing.opus', 'edge-tts')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translated-audio/0/zh-Hans")
        assert resp.status_code == 404

    def test_streams_existing_file(self, app_client, audio_db, tmp_path):
        # Write a small dummy opus file and point the DB at it
        audio_file = tmp_path / "chapter.opus"
        audio_file.write_bytes(b"\x00\x01\x02\x03fake-opus-bytes")

        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', ?, 'edge-tts')",
            (str(audio_file),),
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translated-audio/0/zh-Hans")
        assert resp.status_code == 200
        assert resp.mimetype == "audio/opus"
        assert b"fake-opus-bytes" in resp.data

    def test_mp3_mime_type(self, app_client, audio_db, tmp_path):
        mp3_file = tmp_path / "chapter.mp3"
        mp3_file.write_bytes(b"\xff\xfbfake-mp3")

        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', ?, 'edge-tts')",
            (str(mp3_file),),
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translated-audio/0/zh-Hans")
        assert resp.status_code == 200
        assert resp.mimetype == "audio/mpeg"


class TestGenerateTranslatedAudio:
    """POST /api/translated-audio/generate (admin)"""

    def test_missing_body_400(self, app_client, audio_db):
        resp = app_client.post("/api/translated-audio/generate")
        assert resp.status_code in (400, 415)  # Flask may return 415 if no content-type

    def test_missing_audiobook_id_400(self, app_client, audio_db):
        resp = app_client.post(
            "/api/translated-audio/generate",
            json={"locale": "zh-Hans"},
        )
        assert resp.status_code == 400

    def test_book_not_found_404(self, app_client, audio_db):
        resp = app_client.post(
            "/api/translated-audio/generate",
            json={"audiobook_id": 99999, "locale": "zh-Hans"},
        )
        assert resp.status_code == 404

    def test_no_subtitles_400(self, app_client, audio_db):
        """Without translated subtitles, generation should fail early."""
        resp = app_client.post(
            "/api/translated-audio/generate",
            json={"audiobook_id": 1, "locale": "zh-Hans"},
        )
        assert resp.status_code == 400
        assert "subtitles" in resp.get_json()["error"].lower()

    def test_already_exists_returns_exists(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/sub.vtt', 'whisper')"
        )
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/cached.opus', 'edge-tts')"
        )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/translated-audio/generate",
            json={"audiobook_id": 1, "locale": "zh-Hans"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "exists"


class TestGetTtsJobStatus:
    """GET /api/translated-audio/status/<id>/<locale>"""

    def test_idle_when_no_status(self, app_client, audio_db):
        resp = app_client.get("/api/translated-audio/status/1/zh-Hans")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "idle"

    def test_returns_stored_state(self, app_client, audio_db):
        _set_status(1, "zh-Hans", state="running", phase="synthesizing", message="wait")
        resp = app_client.get("/api/translated-audio/status/1/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "running"
        assert body["phase"] == "synthesizing"
        assert body["audiobook_id"] == 1
        assert body["locale"] == "zh-Hans"


class TestUserRequestTranslatedAudio:
    """POST /api/user/translated-audio/request"""

    def test_missing_audiobook_id_400(self, app_client, audio_db):
        resp = app_client.post(
            "/api/user/translated-audio/request",
            json={"locale": "zh-Hans"},
        )
        assert resp.status_code == 400

    def test_no_subtitles_400(self, app_client, audio_db):
        resp = app_client.post(
            "/api/user/translated-audio/request",
            json={"audiobook_id": 1, "locale": "zh-Hans"},
        )
        assert resp.status_code == 400
        assert "subtitles" in resp.get_json()["error"].lower()

    def test_book_not_found_404(self, app_client, audio_db):
        resp = app_client.post(
            "/api/user/translated-audio/request",
            json={"audiobook_id": 99999, "locale": "zh-Hans"},
        )
        assert resp.status_code == 404

    def test_existing_audio_returns_exists(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (5, 0, 'zh-Hans', '/tmp/sub.vtt', 'whisper')"
        )
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (5, 0, 'zh-Hans', '/tmp/cached.opus', 'edge-tts')"
        )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/user/translated-audio/request",
            json={"audiobook_id": 5, "locale": "zh-Hans"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "exists"

    def test_existing_running_job_returns_already_running(self, app_client, audio_db):
        """If a job is already running, user request should short-circuit."""
        _set_status(1, "zh-Hans", state="running", phase="synth", message="busy")
        resp = app_client.post(
            "/api/user/translated-audio/request",
            json={"audiobook_id": 1, "locale": "zh-Hans"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "already_running"
