"""
Tests for the streaming translation API blueprint.

Exercises the on-demand streaming translation endpoints that coordinate
chapter-level GPU work and segment-by-segment completion broadcasts.

These are API-level tests that hit the route handlers via the Flask test
client and verify behavior at the HTTP boundary (status codes, JSON
bodies, DB side effects). The GPU worker callback endpoints are exercised
without requiring a real worker.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from backend.api_modular import streaming_translate as st
from backend.api_modular.streaming_translate import (
    _LOG_SCRUB_RE,
    _SAFE_LOCALE_RE,
    _chapter_segment_count,
    _safe_log_value,
    _safe_subtitles_path,
    _sanitize_locale,
)

# ── Module-level helpers ──


class TestSanitizeLocale:
    """_sanitize_locale — path-traversal and log-injection defense."""

    @pytest.mark.parametrize(
        "locale", ["en", "zh", "zh-Hans", "pt-BR", "es", "ja", "de-CH", "en-US"]
    )
    def test_valid_locales_pass(self, locale):
        assert _sanitize_locale(locale) == locale

    @pytest.mark.parametrize(
        "locale",
        [
            "../etc",
            "en/../zh",
            "en\nzh",
            "en\rzh",
            "en\x00zh",
            "",
            "1",
            "123",
            "zh-Hans-extra-suffix-too-long-this",
            "zh_Hans",  # underscore not allowed
            "en;drop",
            None,
            42,
            [],
            {},
        ],
    )
    def test_invalid_locales_raise(self, locale):
        with pytest.raises(ValueError):
            _sanitize_locale(locale)  # type: ignore[arg-type]

    def test_regex_requires_letters(self):
        """_SAFE_LOCALE_RE must reject non-letter primary segments."""
        assert _SAFE_LOCALE_RE.match("en")
        assert not _SAFE_LOCALE_RE.match("e1")
        assert not _SAFE_LOCALE_RE.match("1en")


class TestSafeLogValue:
    """_safe_log_value — CRLF injection / log forging defense."""

    def test_none_returns_empty(self):
        assert _safe_log_value(None) == ""

    def test_plain_string_passthrough(self):
        assert _safe_log_value("hello world") == "hello world"

    def test_strips_cr_lf(self):
        assert _safe_log_value("ab\r\ncd") == "ab__cd"

    def test_strips_null_byte(self):
        assert _safe_log_value("a\x00b") == "a_b"

    def test_strips_other_control_chars(self):
        # tab, bell, vertical tab — all control chars must be replaced
        assert _safe_log_value("a\tb\vc\x07d") == "a_b_c_d"

    def test_truncates_long_value(self):
        long = "A" * 300
        result = _safe_log_value(long)
        assert result.endswith("...(truncated)")
        assert "A" * 200 in result
        assert len(result) == 200 + len("...(truncated)")

    def test_integer_coerced_to_string(self):
        assert _safe_log_value(42) == "42"

    def test_scrub_regex_exact_chars(self):
        """_LOG_SCRUB_RE must match every byte in [0..0x1f, 0x7f]."""
        for ch in range(0x20):
            assert _LOG_SCRUB_RE.search(chr(ch)) is not None
        assert _LOG_SCRUB_RE.search("\x7f") is not None
        assert _LOG_SCRUB_RE.search("a") is None


class TestSafeSubtitlesPath:
    """_safe_subtitles_path — path-injection defense."""

    def test_happy_path(self, tmp_path):
        root = tmp_path
        p = _safe_subtitles_path(root, 1, 0, "zh-Hans")
        assert p.is_relative_to(root.resolve())
        assert p.name == "ch000.zh-Hans.vtt"

    def test_rejects_negative_book_id(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_subtitles_path(tmp_path, -1, 0, "zh-Hans")

    def test_rejects_string_book_id(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_subtitles_path(tmp_path, "1", 0, "zh-Hans")  # type: ignore[arg-type]

    def test_rejects_negative_chapter_index(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_subtitles_path(tmp_path, 1, -1, "zh-Hans")

    def test_rejects_string_chapter_index(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_subtitles_path(tmp_path, 1, "0", "zh-Hans")  # type: ignore[arg-type]

    def test_rejects_bad_locale(self, tmp_path):
        with pytest.raises(ValueError):
            _safe_subtitles_path(tmp_path, 1, 0, "../etc")


class TestChapterSegmentCount:
    """_chapter_segment_count — duration → segment count math."""

    def test_zero_duration(self):
        assert _chapter_segment_count(0) == 0

    def test_negative_duration(self):
        assert _chapter_segment_count(-10) == 0

    def test_exact_30s(self):
        assert _chapter_segment_count(30) == 1

    def test_sub_segment_rounds_up(self):
        assert _chapter_segment_count(1) == 1

    def test_90s_is_three_segments(self):
        assert _chapter_segment_count(90) == 3

    def test_fraction_rounds_up(self):
        assert _chapter_segment_count(45) == 2


# ── HTTP endpoint tests ──


def _init_translation_queue(db_path: Path) -> None:
    """Create the translation_queue table used by the streaming fallback."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            state TEXT DEFAULT 'pending',
            total_chapters INTEGER,
            UNIQUE(audiobook_id, locale)
        )
        """)
    conn.commit()
    conn.close()


@pytest.fixture
def streaming_db(flask_app, session_temp_dir):
    """Provide the session DB path and pre-create the translation_queue table.

    Re-binds ``st._db_path`` to the session DB because other test modules
    (e.g. ``test_enriched_api.py``) spin up their own Flask app with a
    different DB and overwrite the module-level global.
    """
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    st._db_path = db_path
    _init_translation_queue(db_path)
    yield db_path
    # Clean up rows we created so we don't pollute other tests
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM streaming_segments")
    conn.execute("DELETE FROM streaming_sessions")
    conn.execute("DELETE FROM chapter_subtitles")
    conn.execute("DELETE FROM chapter_translations_audio")
    conn.execute("DELETE FROM translation_queue")
    conn.commit()
    conn.close()


class TestRequestStreamingTranslation:
    """POST /api/translate/stream"""

    def test_missing_audiobook_id_400(self, app_client, streaming_db):
        resp = app_client.post("/api/translate/stream", json={})
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "audiobook_id required"

    def test_invalid_locale_400(self, app_client, streaming_db):
        resp = app_client.post(
            "/api/translate/stream", json={"audiobook_id": 1, "locale": "../etc"}
        )
        assert resp.status_code == 400
        assert "invalid" in resp.get_json()["error"]

    def test_non_integer_book_id_400(self, app_client, streaming_db):
        resp = app_client.post(
            "/api/translate/stream", json={"audiobook_id": "not-an-int", "locale": "zh-Hans"}
        )
        assert resp.status_code == 400

    def test_new_session_buffering_state(self, app_client, streaming_db):
        # Insert a translation_queue hint so _get_chapter_count returns >0
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT OR REPLACE INTO translation_queue "
            "(audiobook_id, locale, total_chapters) VALUES (1, 'zh-Hans', 3)"
        )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/translate/stream",
            json={"audiobook_id": 1, "locale": "zh-Hans", "chapter_index": 0},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "buffering"
        assert body["audiobook_id"] == 1
        assert body["chapter_index"] == 0
        assert body["locale"] == "zh-Hans"
        assert "session_id" in body
        assert "segment_bitmap" in body

    def test_existing_session_reused(self, app_client, streaming_db):
        # First call creates a session
        r1 = app_client.post(
            "/api/translate/stream",
            json={"audiobook_id": 2, "locale": "zh-Hans", "chapter_index": 0},
        )
        session_id_1 = r1.get_json()["session_id"]

        # Second call with a different active chapter should reuse session
        r2 = app_client.post(
            "/api/translate/stream",
            json={"audiobook_id": 2, "locale": "zh-Hans", "chapter_index": 1},
        )
        session_id_2 = r2.get_json()["session_id"]
        assert session_id_1 == session_id_2

    def test_all_cached_returns_cached_state(self, app_client, streaming_db):
        # Pre-insert cached subtitles and audio for all chapters of book 3
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO translation_queue (audiobook_id, locale, total_chapters) "
            "VALUES (3, 'zh-Hans', 2)"
        )
        for ch in range(2):
            conn.execute(
                "INSERT INTO chapter_subtitles "
                "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
                "VALUES (3, ?, 'zh-Hans', '/tmp/x.vtt', 'test')",
                (ch,),
            )
            conn.execute(
                "INSERT INTO chapter_translations_audio "
                "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
                "VALUES (3, ?, 'zh-Hans', '/tmp/x.opus', 'test')",
                (ch,),
            )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/translate/stream",
            json={"audiobook_id": 3, "locale": "zh-Hans", "chapter_index": 0},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "cached"
        assert body["all_cached"] is True
        assert 0 in body["cached_chapters"]


class TestGetSegmentBitmap:
    """GET /api/translate/segments/<id>/<ch>/<locale>"""

    def test_empty_bitmap(self, app_client, streaming_db):
        resp = app_client.get("/api/translate/segments/99/0/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["completed"] == []
        assert body["total"] == 0

    def test_invalid_locale_400(self, app_client, streaming_db):
        resp = app_client.get("/api/translate/segments/1/0/..traversal")
        assert resp.status_code == 400

    def test_bitmap_reflects_completed(self, app_client, streaming_db):
        conn = sqlite3.connect(str(streaming_db))
        # Three segments, two completed, one pending
        for idx, state in [(0, "completed"), (1, "completed"), (2, "pending")]:
            conn.execute(
                "INSERT INTO streaming_segments "
                "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
                "VALUES (4, 0, ?, 'zh-Hans', ?, 1)",
                (idx, state),
            )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/translate/segments/4/0/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert set(body["completed"]) == {0, 1}
        assert body["total"] == 3
        assert body["all_cached"] is False

    def test_cached_subtitles_report_all_cached(self, app_client, streaming_db):
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/x.vtt', 'test')"
        )
        conn.commit()
        conn.close()
        resp = app_client.get("/api/translate/segments/1/0/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["all_cached"] is True


class TestGetSessionState:
    """GET /api/translate/session/<id>/<locale>"""

    def test_no_session_returns_none(self, app_client, streaming_db):
        resp = app_client.get("/api/translate/session/99/zh-Hans")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "none"

    def test_invalid_locale_400(self, app_client, streaming_db):
        resp = app_client.get("/api/translate/session/1/bad\nlocale")
        assert resp.status_code == 400

    def test_returns_existing_session(self, app_client, streaming_db):
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO streaming_sessions "
            "(audiobook_id, locale, active_chapter, buffer_threshold, state, gpu_warm) "
            "VALUES (1, 'zh-Hans', 2, 6, 'streaming', 1)"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/translate/session/1/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "streaming"
        assert body["active_chapter"] == 2
        assert body["buffer_threshold"] == 6
        assert body["gpu_warm"] is True


class TestWarmupGpu:
    """POST /api/translate/warmup"""

    def test_warmup_writes_hint(self, app_client, streaming_db):
        resp = app_client.post("/api/translate/warmup")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "warming"

        # Verify the hint row was written
        conn = sqlite3.connect(str(streaming_db))
        row = conn.execute(
            "SELECT state FROM streaming_sessions WHERE locale = 'warmup'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "warmup"


class TestHandleSeek:
    """POST /api/translate/seek"""

    def test_missing_audiobook_id_400(self, app_client, streaming_db):
        resp = app_client.post("/api/translate/seek", json={})
        assert resp.status_code == 400

    def test_invalid_locale_400(self, app_client, streaming_db):
        resp = app_client.post("/api/translate/seek", json={"audiobook_id": 1, "locale": "../bad"})
        assert resp.status_code == 400

    def test_seek_to_cached_segment(self, app_client, streaming_db):
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (1, 0, 3, 'zh-Hans', 'completed', 0)"
        )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/translate/seek",
            json={"audiobook_id": 1, "locale": "zh-Hans", "chapter_index": 0, "segment_index": 3},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "cached"
        assert body["segment_index"] == 3

    def test_seek_to_uncached_reprioritizes(self, app_client, streaming_db):
        # Seed an active session and some pending segments for chapter 1
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO streaming_sessions "
            "(audiobook_id, locale, active_chapter, buffer_threshold, state) "
            "VALUES (1, 'zh-Hans', 0, 6, 'streaming')"
        )
        for idx in range(10):
            conn.execute(
                "INSERT INTO streaming_segments "
                "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
                "VALUES (1, 1, ?, 'zh-Hans', 'pending', 1)",
                (idx,),
            )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/translate/seek",
            json={"audiobook_id": 1, "locale": "zh-Hans", "chapter_index": 1, "segment_index": 5},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "buffering"
        assert body["chapter_index"] == 1

        # Verify priority 0 on segments >=5 and <5+BUFFER_THRESHOLD
        conn = sqlite3.connect(str(streaming_db))
        rows = conn.execute(
            "SELECT segment_index, priority FROM streaming_segments "
            "WHERE audiobook_id = 1 AND chapter_index = 1 "
            "ORDER BY segment_index"
        ).fetchall()
        conn.close()
        priorities = {idx: pri for idx, pri in rows}
        assert priorities[5] == 0
        assert priorities[6] == 0


class TestSegmentComplete:
    """POST /api/translate/segment-complete — GPU worker callback."""

    def test_missing_fields_400(self, app_client, streaming_db):
        resp = app_client.post("/api/translate/segment-complete", json={})
        assert resp.status_code == 400

    def test_invalid_types_400(self, app_client, streaming_db):
        resp = app_client.post(
            "/api/translate/segment-complete",
            json={
                "audiobook_id": "abc",
                "locale": "zh-Hans",
                "chapter_index": 0,
                "segment_index": 0,
            },
        )
        assert resp.status_code == 400

    def test_segment_marked_completed(self, app_client, streaming_db):
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (1, 0, 0, 'zh-Hans', 'pending', 0)"
        )
        conn.commit()
        conn.close()

        resp = app_client.post(
            "/api/translate/segment-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "segment_index": 0,
                "locale": "zh-Hans",
                "vtt_content": "WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nHello",
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

        conn = sqlite3.connect(str(streaming_db))
        row = conn.execute(
            "SELECT state FROM streaming_segments "
            "WHERE audiobook_id = 1 AND chapter_index = 0 AND segment_index = 0"
        ).fetchone()
        conn.close()
        assert row[0] == "completed"


class TestChapterComplete:
    """POST /api/translate/chapter-complete — GPU worker callback."""

    def test_missing_fields_400(self, app_client, streaming_db):
        resp = app_client.post("/api/translate/chapter-complete", json={})
        assert resp.status_code == 400

    def test_invalid_locale_400(self, app_client, streaming_db):
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={"audiobook_id": 1, "chapter_index": 0, "locale": "..\ntrouble"},
        )
        assert resp.status_code == 400

    def test_chapter_insert_writes_subtitles_and_audio(self, app_client, streaming_db):
        # Use a relative path within the streaming audio root so _validate_audio_path accepts it.
        # Absolute /tmp paths are now rejected as path-injection defense (Phase 6c).
        audio_rel = "1/ch000/zh-Hans/chapter.opus"
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "locale": "zh-Hans",
                "source_vtt_path": "/tmp/source.vtt",  # nosec B108 -- DB row payload string, never written to disk
                "translated_vtt_path": "/tmp/translated.vtt",  # nosec B108 -- DB row payload string, never written to disk
                "audio_path": audio_rel,
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

        conn = sqlite3.connect(str(streaming_db))
        subs = conn.execute(
            "SELECT locale FROM chapter_subtitles WHERE audiobook_id = 1"
        ).fetchall()
        audio = conn.execute(
            "SELECT audio_path FROM chapter_translations_audio WHERE audiobook_id = 1"
        ).fetchone()
        conn.close()

        locales = {r[0] for r in subs}
        assert "zh-Hans" in locales
        assert "en" in locales  # source VTT stored as English
        assert audio is not None
        # Path validation resolves relative paths to absolute under the streaming audio root
        assert audio[0] is not None
        assert audio_rel in audio[0]

    def test_chapter_complete_minimal_body(self, app_client, streaming_db):
        """Only audiobook_id/chapter_index/locale — worker reported empty chapter."""
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={"audiobook_id": 1, "chapter_index": 1, "locale": "zh-Hans"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
