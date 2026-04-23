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

import os
import sqlite3
from pathlib import Path

import pytest
from backend.api_modular import streaming_translate as st
from backend.api_modular.streaming_translate import (
    _LOG_SCRUB_RE,
    _SAFE_LOCALE_RE,
    _chapter_segment_count,
    _safe_join_under,
    _safe_log_value,
    _safe_subtitles_path,
    _sanitize_locale,
    _validate_audio_path,
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


class TestSafeJoinUnder:
    """_safe_join_under — defense-in-depth path-injection guard (CodeQL py/path-injection).

    This helper is the only generic base+parts join used by the streaming
    translate module for chapter audio consolidation and chapter-subtitle
    validation. The happy path must resolve under ``base``; every traversal
    or null-byte form must raise ``ValueError`` before any filesystem I/O.
    """

    def test_happy_path_single_component(self, tmp_path):
        p = _safe_join_under(tmp_path, "good.txt")
        assert p == (tmp_path.resolve() / "good.txt").resolve()
        assert p.is_relative_to(tmp_path.resolve())

    def test_happy_path_multi_component(self, tmp_path):
        p = _safe_join_under(tmp_path, "sub", "dir", "file.vtt")
        assert p.is_relative_to(tmp_path.resolve())
        assert p.name == "file.vtt"

    def test_happy_path_numeric_component_coerced(self, tmp_path):
        # ints are valid parts — coerced via str()
        p = _safe_join_under(tmp_path, 42, "ch001.vtt")
        assert p.is_relative_to(tmp_path.resolve())
        assert "42" in str(p)

    def test_rejects_parent_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="path traversal rejected"):
            _safe_join_under(tmp_path, "..", "etc", "passwd")

    def test_rejects_nested_parent_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="path traversal rejected"):
            _safe_join_under(tmp_path, "sub", "..", "..", "etc")

    def test_rejects_absolute_path_component(self, tmp_path):
        # An absolute component would reset the join; must not escape base
        with pytest.raises(ValueError, match="path traversal rejected"):
            _safe_join_under(tmp_path, "/etc/passwd")

    def test_rejects_null_byte_in_component(self, tmp_path):
        with pytest.raises(ValueError, match="null byte"):
            _safe_join_under(tmp_path, "good\x00bad.txt")

    def test_rejects_null_byte_in_later_component(self, tmp_path):
        with pytest.raises(ValueError, match="null byte"):
            _safe_join_under(tmp_path, "sub", "ok", "bad\x00.txt")

    def test_happy_path_with_dot_in_filename(self, tmp_path):
        # Legitimate filenames contain dots — must NOT be rejected
        p = _safe_join_under(tmp_path, "ch000.zh-Hans.vtt")
        assert p.is_relative_to(tmp_path.resolve())
        assert p.name == "ch000.zh-Hans.vtt"

    def test_result_resolves_symlink_free_relative(self, tmp_path):
        # The returned path must be the resolved form (no symlinks, no ..)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        p = _safe_join_under(tmp_path, "a", "b", "c.vtt")
        assert p == (tmp_path.resolve() / "a" / "b" / "c.vtt").resolve()


class TestValidateAudioPath:
    """_validate_audio_path — worker-callback audio_path containment guard.

    Exercises the branches added in phase 6 security hardening: None input,
    missing root, relative-path resolution against the configured root, and
    traversal rejection.
    """

    def test_none_returns_none(self):
        """None audio_path is a legitimate no-audio signal — pass through."""
        assert _validate_audio_path(None) is None

    def test_returns_none_when_root_unconfigured(self, monkeypatch):
        """If _streaming_audio_root is None, every path is rejected."""
        monkeypatch.setattr(st, "_streaming_audio_root", None)
        assert _validate_audio_path("anything.webm") is None

    def test_rejects_absolute_path_outside_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(st, "_streaming_audio_root", tmp_path)
        assert _validate_audio_path("/etc/passwd") is None

    def test_accepts_relative_path_resolves_under_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(st, "_streaming_audio_root", tmp_path)
        result = _validate_audio_path("1/ch000/zh-Hans/chapter.webm")
        assert result is not None
        assert result.is_relative_to(tmp_path.resolve())

    def test_accepts_absolute_path_inside_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(st, "_streaming_audio_root", tmp_path)
        inside = tmp_path / "good" / "path.webm"
        result = _validate_audio_path(str(inside))
        assert result is not None
        assert result.is_relative_to(tmp_path.resolve())

    def test_rejects_traversal_in_relative_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(st, "_streaming_audio_root", tmp_path)
        assert _validate_audio_path("../../etc/passwd") is None


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


# Book IDs and their chapter counts seeded by the streaming_db fixture.
# _resolve_chapter_count reads audiobooks.chapter_count directly; seeding it
# avoids triggering the ffprobe backfill path (which would fail on these
# synthetic file_paths).
_SEEDED_BOOKS = {1: 3, 2: 3, 3: 2, 4: 3}


def _seed_audiobooks(db_path: Path) -> None:
    """Seed audiobooks rows with chapter_count for streaming tests."""
    conn = sqlite3.connect(str(db_path))
    for book_id, ch_count in _SEEDED_BOOKS.items():
        conn.execute(
            "INSERT OR REPLACE INTO audiobooks "
            "(id, title, file_path, format, duration_hours, chapter_count) "
            "VALUES (?, ?, ?, 'opus', 1.0, ?)",
            (book_id, f"Test Book {book_id}", f"/nonexistent/book{book_id}.opus", ch_count),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def streaming_db(flask_app, session_temp_dir):
    """Provide the session DB path with seeded audiobooks rows.

    Re-binds ``st._db_path`` to the session DB because other test modules
    (e.g. ``test_enriched_api.py``) spin up their own Flask app with a
    different DB and overwrite the module-level global. Also clears the
    process-wide chapter-count memo so stale entries from earlier tests
    can't mask a DB miss.
    """
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    st._db_path = db_path
    st._chapter_count_memo.clear()
    _seed_audiobooks(db_path)
    yield db_path
    # Clean up rows we created so we don't pollute other tests
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM streaming_segments")
    conn.execute("DELETE FROM streaming_sessions")
    conn.execute("DELETE FROM chapter_subtitles")
    conn.execute("DELETE FROM chapter_translations_audio")
    conn.executemany(
        "DELETE FROM audiobooks WHERE id = ?",
        [(bid,) for bid in _SEEDED_BOOKS],
    )
    conn.commit()
    conn.close()
    st._chapter_count_memo.clear()


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
        # Book 1 is seeded by the fixture with chapter_count=3
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
        # Book 3 is seeded by the fixture with chapter_count=2
        conn = sqlite3.connect(str(streaming_db))
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
                "VALUES (3, ?, 'zh-Hans', '/tmp/x.webm', 'test')",
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
        # v8.3.2: bitmap shape stays self-consistent — completed is always a
        # list and cache_source identifies the cache origin so progress UIs
        # never see the contradictory total:0 + all_cached:true sentinel.
        assert body["completed"] == []
        assert body["total"] == 0
        assert body["cache_source"] == "batch"

    def test_cache_source_streaming(self, app_client, streaming_db):
        # All streaming segments completed and no batch-cached chapter row →
        # cache_source must be 'streaming'.
        conn = sqlite3.connect(str(streaming_db))
        for idx in range(2):
            conn.execute(
                "INSERT INTO streaming_segments "
                "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
                "VALUES (5, 0, ?, 'zh-Hans', 'completed', 1)",
                (idx,),
            )
        conn.commit()
        conn.close()
        resp = app_client.get("/api/translate/segments/5/0/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["all_cached"] is True
        assert body["total"] == 2
        assert body["cache_source"] == "streaming"

    def test_cache_source_none_when_in_progress(self, app_client, streaming_db):
        conn = sqlite3.connect(str(streaming_db))
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
            "VALUES (6, 0, 0, 'zh-Hans', 'pending', 1)"
        )
        conn.commit()
        conn.close()
        resp = app_client.get("/api/translate/segments/6/0/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["all_cached"] is False
        assert body["cache_source"] == "none"


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
        # Use paths within the streaming roots — absolute paths outside the allowed
        # roots are rejected as path-injection defense (Phase 6c).
        audio_rel = "1/ch000/zh-Hans/chapter.webm"
        subtitles_root = Path(os.environ["AUDIOBOOKS_STREAMING_SUBTITLES_DIR"])
        source_vtt_path = subtitles_root / "1" / "ch000.en.vtt"
        translated_vtt_path = subtitles_root / "1" / "ch000.zh-Hans.vtt"
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "locale": "zh-Hans",
                "source_vtt_path": str(source_vtt_path),
                "translated_vtt_path": str(translated_vtt_path),
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

    def test_chapter_complete_rejects_audio_path_outside_root(self, app_client, streaming_db):
        """audio_path that escapes streaming audio root → 400 (py/path-injection defense)."""
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "locale": "zh-Hans",
                "audio_path": "/etc/passwd",
            },
        )
        assert resp.status_code == 400
        assert "audio_path" in resp.get_json()["error"]

    def test_chapter_complete_rejects_translated_vtt_outside_root(self, app_client, streaming_db):
        """translated_vtt_path outside subtitles root → 400."""
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "locale": "zh-Hans",
                "translated_vtt_path": "/etc/shadow",
            },
        )
        assert resp.status_code == 400
        assert "translated_vtt_path" in resp.get_json()["error"]

    def test_chapter_complete_rejects_source_vtt_outside_root(self, app_client, streaming_db):
        """source_vtt_path outside subtitles root → 400."""
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "locale": "zh-Hans",
                "source_vtt_path": "/tmp/../etc/hosts",  # nosec B108 - security test input (path traversal rejection), never touches FS
            },
        )
        assert resp.status_code == 400
        assert "source_vtt_path" in resp.get_json()["error"]

    def test_chapter_complete_rejects_null_byte_in_vtt_path(self, app_client, streaming_db):
        """VTT path with null byte → 400 (OSError from Path resolution)."""
        resp = app_client.post(
            "/api/translate/chapter-complete",
            json={
                "audiobook_id": 1,
                "chapter_index": 0,
                "locale": "zh-Hans",
                "translated_vtt_path": "/var/lib/audiobooks/streaming-subtitles/bad\x00.vtt",
            },
        )
        assert resp.status_code == 400
        assert "translated_vtt_path" in resp.get_json()["error"]
