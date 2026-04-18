"""Task 9/10 — streaming TTS per-segment synthesis + chapter consolidation.

This module covers the edge-tts/ffmpeg per-segment pipeline introduced in
v8.3.2. The first half (Task 9) locks in the worker helpers: VTT→plain-text
extraction, locale→voice mapping, and the opus output layout under
``AUDIOBOOKS_STREAMING_AUDIO_DIR``. The second half (Task 10, chapter
consolidation) is added separately.

The worker lives at ``scripts/stream-translate-worker.py`` — the hyphenated
filename is not a valid Python module name, so we side-load it via
``importlib.util.spec_from_file_location``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKER_PATH = REPO / "scripts" / "stream-translate-worker.py"


def _load_worker(env_streaming_dir: Path | None = None):
    """Side-load the hyphenated worker script as an importable module.

    The module reads ``AUDIOBOOKS_STREAMING_AUDIO_DIR`` at import time, so
    tests that need a scratch root must pass ``env_streaming_dir`` and the
    helper will set the env var BEFORE exec.
    """
    import os

    if env_streaming_dir is not None:
        os.environ["AUDIOBOOKS_STREAMING_AUDIO_DIR"] = str(env_streaming_dir)

    # Ensure the library/ directory is on sys.path so the worker's
    # `from localization.tts.factory import get_tts_provider` top-level
    # import resolves the same way it does in production.
    lib_dir = REPO / "library"
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))

    spec = importlib.util.spec_from_file_location(
        "stream_translate_worker_iso", WORKER_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stream_translate_worker_iso"] = mod
    spec.loader.exec_module(mod)
    return mod


VTT_SAMPLE = """WEBVTT

1
00:00:00.000 --> 00:00:03.000
你好，世界。

2
00:00:03.000 --> 00:00:06.000
这是一段测试。
"""


# ── _vtt_to_plain ──


def test_vtt_to_plain_drops_headers_and_timings():
    w = _load_worker()
    plain = w._vtt_to_plain(VTT_SAMPLE)
    assert "WEBVTT" not in plain
    assert "-->" not in plain
    assert "你好，世界。" in plain
    assert "这是一段测试。" in plain


def test_vtt_to_plain_empty_input_returns_empty():
    w = _load_worker()
    assert w._vtt_to_plain("") == ""
    assert w._vtt_to_plain("WEBVTT\n\n") == ""


# ── _default_voice_for_locale ──


def test_default_voice_for_locale_zh_hans():
    w = _load_worker()
    assert w._default_voice_for_locale("zh-Hans") == "zh-CN-XiaoxiaoNeural"


def test_default_voice_for_locale_fallback_unknown_locale():
    w = _load_worker()
    assert w._default_voice_for_locale("xx-YY") == "en-US-AriaNeural"


# ── _synthesize_segment_audio ──


def test_synthesize_segment_audio_empty_text_returns_none(tmp_path):
    """Empty VTT (or header-only) short-circuits before calling edge-tts."""
    w = _load_worker(env_streaming_dir=tmp_path)
    assert w._synthesize_segment_audio("", 1, 0, 0, "zh-Hans") is None
    assert w._synthesize_segment_audio("WEBVTT\n\n", 1, 0, 0, "zh-Hans") is None


def test_synthesize_segment_audio_happy_path_mocks_edge_tts_and_ffmpeg(tmp_path):
    """Full pipeline with edge-tts + ffmpeg mocked — verifies path layout."""
    w = _load_worker(env_streaming_dir=tmp_path)

    # Mock: edge-tts provider writes a fake mp3 to output_path
    def fake_synth(text, language, voice, output_path):
        Path(output_path).write_bytes(b"fake-mp3")
        return Path(output_path)

    # Mock ffmpeg: write fake opus to the final location (last cmd arg)
    def fake_run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-opus")

        class R:
            returncode = 0

        return R()

    class FakeTTS:
        def synthesize(self, text, language, voice, output_path):
            return fake_synth(text, language, voice, output_path)

    with patch.object(w, "get_tts_provider", return_value=FakeTTS()):
        with patch.object(w.subprocess, "run", side_effect=fake_run):
            out = w._synthesize_segment_audio(
                VTT_SAMPLE,
                audiobook_id=1,
                chapter_index=0,
                segment_index=5,
                locale="zh-Hans",
            )

    assert out is not None
    assert out.exists()
    assert out.read_bytes() == b"fake-opus"
    assert out.name == "seg0005.opus"
    assert out.parent.name == "zh-Hans"
    assert out.parent.parent.name == "ch000"
    assert out.parent.parent.parent.name == "1"  # audiobook_id subdir
    # Root must be the scratch path we injected via env var
    assert out.is_relative_to(tmp_path)


# ── segment-complete callback backward-compat ──


@pytest.fixture
def streaming_db(flask_app, session_temp_dir):
    """Local copy of the fixture from test_streaming_translate.py.

    Re-binds ``streaming_translate._db_path`` to the session DB — other test
    modules that spin up their own Flask app overwrite this module-level
    global, so each streaming-test module must reset it.
    """
    import sqlite3

    from backend.api_modular import streaming_translate as st

    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    st._db_path = db_path
    # Pre-create translation_queue (streaming endpoints fall back to it)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            state TEXT DEFAULT 'pending',
            total_chapters INTEGER,
            UNIQUE(audiobook_id, locale)
        )
        """
    )
    conn.commit()
    conn.close()
    yield db_path
    # Clean up our rows so we don't pollute other tests
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM streaming_segments")
    conn.execute("DELETE FROM streaming_sessions")
    conn.execute("DELETE FROM chapter_subtitles")
    conn.execute("DELETE FROM chapter_translations_audio")
    conn.execute("DELETE FROM translation_queue")
    conn.commit()
    conn.close()


def test_segment_complete_backward_compat_without_audio_path(
    app_client, streaming_db
):
    """v8.3.1 workers send no ``audio_path`` — must still update state + vtt."""
    import sqlite3

    conn = sqlite3.connect(str(streaming_db))
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
        "VALUES (7, 0, 0, 'zh-Hans', 'pending', 0)"
    )
    conn.commit()
    conn.close()

    # Old-worker payload: no audio_path field at all.
    resp = app_client.post(
        "/api/translate/segment-complete",
        json={
            "audiobook_id": 7,
            "chapter_index": 0,
            "segment_index": 0,
            "locale": "zh-Hans",
            "vtt_content": "WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nHi",
        },
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(str(streaming_db))
    row = conn.execute(
        "SELECT state, vtt_content, audio_path FROM streaming_segments "
        "WHERE audiobook_id = 7 AND chapter_index = 0 AND segment_index = 0"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "completed"
    assert row[1] and "Hi" in row[1]
    assert row[2] is None  # No audio_path was sent → NULL in DB


def test_segment_complete_persists_audio_path_when_present(
    app_client, streaming_db
):
    """v8.3.2 workers include ``audio_path`` → must land in DB column."""
    import sqlite3

    conn = sqlite3.connect(str(streaming_db))
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, priority) "
        "VALUES (8, 0, 0, 'zh-Hans', 'pending', 0)"
    )
    conn.commit()
    conn.close()

    resp = app_client.post(
        "/api/translate/segment-complete",
        json={
            "audiobook_id": 8,
            "chapter_index": 0,
            "segment_index": 0,
            "locale": "zh-Hans",
            "vtt_content": "WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nHi",
            "audio_path": "8/ch000/zh-Hans/seg0000.opus",
        },
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(str(streaming_db))
    row = conn.execute(
        "SELECT state, audio_path FROM streaming_segments "
        "WHERE audiobook_id = 8 AND chapter_index = 0 AND segment_index = 0"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "completed"
    assert row[1] == "8/ch000/zh-Hans/seg0000.opus"
