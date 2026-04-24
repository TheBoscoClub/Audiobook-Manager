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

    spec = importlib.util.spec_from_file_location("stream_translate_worker_iso", WORKER_PATH)
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
                VTT_SAMPLE, audiobook_id=1, chapter_index=0, segment_index=5, locale="zh-Hans"
            )

    assert out is not None
    assert out.exists()
    assert out.read_bytes() == b"fake-opus"
    assert out.name == "seg0005.webm"
    assert out.parent.name == "zh-Hans"
    assert out.parent.parent.name == "ch000"
    assert out.parent.parent.parent.name == "1"  # audiobook_id subdir
    # Root must be the scratch path we injected via env var
    assert out.is_relative_to(tmp_path)


# ── process_segment: graceful degrade on TTS failure ──


def test_process_segment_tts_failure_degrades_to_text_only(tmp_path):
    """TTS synthesis failure must NOT fail the segment — payload carries
    ``audio_path=None`` and the segment still reports completion.

    Regression guard for the Task 9 review bug: if TTS raises, the worker
    must still POST the callback with ``audio_path=None``, AND must never
    unlink the permanent TTS opus (since none was produced here, the
    stricter invariant is verified in the variable-split review — this
    test locks in the degrade behavior).
    """
    import json as _json
    from pathlib import Path as _Path
    from unittest.mock import MagicMock

    w = _load_worker(env_streaming_dir=tmp_path)

    # Fake extracted-segment path: a real temp file so the finally-block
    # cleanup has something to operate on without erroring.
    fake_seg = tmp_path / "fake_seg.opus"
    fake_seg.write_bytes(b"fake-slice")

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        # urllib.request.Request carries its body in .data
        captured["payload"] = _json.loads(req.data.decode())

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        return _Resp()

    # Fake generate_subtitles: write a minimal VTT into output_dir and
    # return (source_vtt, translated_vtt).
    def fake_generate_subtitles(audio_path, output_dir, target_locale, chapter_name, stt_provider):
        src = _Path(output_dir) / "source.vtt"
        tr = _Path(output_dir) / "translated.vtt"
        src.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\nhello\n")
        tr.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\n你好\n")
        return src, tr

    segment = {"audiobook_id": 42, "chapter_index": 1, "segment_index": 3, "locale": "zh-Hans"}

    with (
        patch.object(w, "split_audio_segment", return_value=fake_seg),
        patch.object(w, "_synthesize_segment_audio", side_effect=RuntimeError("synth failed")),
        patch("localization.pipeline.generate_subtitles", side_effect=fake_generate_subtitles),
        patch("localization.pipeline.get_stt_provider", return_value=MagicMock()),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        result = w.process_segment(
            db_path=str(tmp_path / "unused.db"),
            segment=segment,
            audio_path=_Path("/nonexistent/book.opus"),
            chapter_start_sec=0.0,
            chapter_duration_sec=600.0,
            api_base="http://localhost:5001",
        )

    # Segment succeeds despite TTS raising
    assert result is True, "TTS failure must not fail the segment"

    # Callback fired with audio_path=None (text-only mode)
    assert "payload" in captured, "segment-complete callback was never invoked"
    assert captured["payload"]["audio_path"] is None, (
        f"audio_path must be None when TTS fails; got {captured['payload']['audio_path']!r}"
    )
    # Sanity: VTT still flows through
    assert captured["payload"]["vtt_content"]
    assert captured["payload"]["audiobook_id"] == 42
    assert captured["payload"]["chapter_index"] == 1
    assert captured["payload"]["segment_index"] == 3


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


def test_segment_complete_backward_compat_without_audio_path(app_client, streaming_db):
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


def test_segment_complete_persists_audio_path_when_present(app_client, streaming_db):
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
            "audio_path": "8/ch000/zh-Hans/seg0000.webm",
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
    # Path validation resolves relative paths to absolute under the streaming audio root;
    # verify the relative segment path components are preserved in the stored absolute path.
    assert row[1] is not None
    assert "8/ch000/zh-Hans/seg0000.webm" in row[1]


# ── Task 10: chapter-level WebM-Opus consolidation ──


def test_consolidate_chapter_produces_audio(app_client, streaming_db, tmp_path, monkeypatch):
    """Per-segment WebM-Opus files concat into chapter.webm + chapter_translations_audio row.

    Mocks ffmpeg/ffprobe so the test does not depend on the host having
    a working libopus or real audio tooling. Verifies:
      - chapter.webm is written under the expected path layout
      - chapter_translations_audio row is inserted with tts_provider='streaming',
        the locale-mapped voice, and the probed duration
      - audio_path stored is absolute (matching batch pipeline convention)
    """
    import sqlite3

    from backend.api_modular import streaming_translate as st

    # Point the streaming audio root at a scratch dir
    streaming_root = tmp_path / "streaming-audio"
    seg_dir = streaming_root / "9" / "ch000" / "zh-Hans"
    seg_dir.mkdir(parents=True)
    monkeypatch.setattr(st, "_streaming_audio_root", streaming_root)
    # VTT consolidation writes outside the install tree (ProtectSystem=strict);
    # point that root at a scratch dir too so the test does not need
    # /var/lib/audiobooks to exist.
    subtitles_root = tmp_path / "streaming-subtitles"
    subtitles_root.mkdir()
    monkeypatch.setattr(st, "_streaming_subtitles_root", subtitles_root)

    # Write 3 fake per-segment WebM-Opus files with known bytes and seed DB rows
    seg_paths = []
    for i in range(3):
        p = seg_dir / f"seg{i:04d}.webm"
        p.write_bytes(b"fake-opus-seg-" + str(i).encode())
        seg_paths.append(p)

    conn = sqlite3.connect(str(streaming_db))
    for i, p in enumerate(seg_paths):
        rel = p.relative_to(streaming_root)
        conn.execute(
            "INSERT INTO streaming_segments "
            "(audiobook_id, chapter_index, segment_index, locale, state, "
            " priority, vtt_content, audio_path) "
            "VALUES (9, 0, ?, 'zh-Hans', 'completed', 0, ?, ?)",
            (i, f"WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nseg{i}", str(rel)),
        )
    conn.commit()
    conn.close()

    # Stub subprocess.run to emulate both ffmpeg (write fake chapter.webm)
    # and ffprobe (return fixed duration).
    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "ffmpeg":
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake-chapter-opus")

            class _RFfmpeg:
                returncode = 0
                stdout = ""
                stderr = ""

            return _RFfmpeg()
        if cmd and cmd[0] == "ffprobe":

            class _RFfprobe:
                returncode = 0
                stdout = "90.0\n"
                stderr = ""

            return _RFfprobe()

        class _ROther:
            returncode = 0
            stdout = ""
            stderr = ""

        return _ROther()

    monkeypatch.setattr(st.subprocess, "run", fake_run)

    # Drive consolidation directly — the module-level _library_path is already
    # wired by init_streaming_routes during Flask app setup, so VTT
    # consolidation will succeed too.
    db = sqlite3.connect(str(streaming_db))
    db.row_factory = sqlite3.Row
    st._consolidate_chapter(db, 9, 0, "zh-Hans")
    db.commit()

    # Verify chapter_translations_audio row
    row = db.execute(
        "SELECT audio_path, tts_provider, tts_voice, duration_seconds "
        "FROM chapter_translations_audio "
        "WHERE audiobook_id = 9 AND chapter_index = 0 AND locale = 'zh-Hans'"
    ).fetchone()
    db.close()
    assert row is not None, "chapter_translations_audio row not inserted"
    assert row["tts_provider"] == "streaming"
    assert row["tts_voice"] == "zh-CN-XiaoxiaoNeural"
    assert row["duration_seconds"] == 90.0
    audio_p = Path(row["audio_path"])
    assert audio_p.is_absolute()
    assert audio_p.exists()
    assert audio_p.read_bytes() == b"fake-chapter-opus"
    assert audio_p.name == "chapter.webm"
    # Path layout: <root>/<book>/ch<NNN>/<locale>/chapter.webm
    assert audio_p.parent.name == "zh-Hans"
    assert audio_p.parent.parent.name == "ch000"
    assert audio_p.parent.parent.parent.name == "9"


def test_consolidate_chapter_skips_audio_when_any_segment_missing_audio(
    app_client, streaming_db, tmp_path, monkeypatch
):
    """If any completed segment lacks audio_path, chapter audio is not generated.

    Guards against shipping a chapter.webm with silent gaps: when the TTS
    pipeline degrades to text-only for any segment (Task 9 regression guard),
    the chapter-level audio row MUST NOT be inserted. VTT consolidation is
    unaffected and still writes the chapter_subtitles row.
    """
    import sqlite3

    from backend.api_modular import streaming_translate as st

    streaming_root = tmp_path / "streaming-audio"
    streaming_root.mkdir()
    monkeypatch.setattr(st, "_streaming_audio_root", streaming_root)
    subtitles_root = tmp_path / "streaming-subtitles"
    subtitles_root.mkdir()
    monkeypatch.setattr(st, "_streaming_subtitles_root", subtitles_root)

    conn = sqlite3.connect(str(streaming_db))
    # 2 segments: first has audio_path, second does NOT (TTS degraded)
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, "
        " priority, vtt_content, audio_path) "
        "VALUES (10, 0, 0, 'zh-Hans', 'completed', 0, ?, ?)",
        ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nHi", "10/ch000/zh-Hans/seg0000.webm"),
    )
    conn.execute(
        "INSERT INTO streaming_segments "
        "(audiobook_id, chapter_index, segment_index, locale, state, "
        " priority, vtt_content, audio_path) "
        "VALUES (10, 0, 1, 'zh-Hans', 'completed', 0, ?, NULL)",
        ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nBye",),
    )
    conn.commit()
    conn.close()

    # ffmpeg must NOT be called when any segment is missing audio_path.
    # ffprobe is not expected either.
    def forbidden(cmd, **kwargs):
        if cmd and cmd[0] in ("ffmpeg", "ffprobe"):
            raise AssertionError(f"{cmd[0]} unexpectedly called: {cmd}")

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(st.subprocess, "run", forbidden)

    db = sqlite3.connect(str(streaming_db))
    db.row_factory = sqlite3.Row
    st._consolidate_chapter(db, 10, 0, "zh-Hans")
    db.commit()

    row = db.execute(
        "SELECT 1 FROM chapter_translations_audio "
        "WHERE audiobook_id = 10 AND chapter_index = 0 AND locale = 'zh-Hans'"
    ).fetchone()
    db.close()
    assert row is None, (
        "chapter_translations_audio must not exist when any segment is missing audio_path"
    )


# ── process_segment: idempotent TTS-only regen (v8.3.8.7 repair path) ──


def test_process_segment_skips_stt_when_vtt_content_already_present(tmp_path):
    """Regression guard for the orphan-repair primitive.

    When a pending row already has ``vtt_content`` populated (e.g. a
    broken-burst row reset to pending, or a legacy .opus orphan whose
    audio file is missing), the worker MUST skip STT + translation and
    regenerate only the per-segment TTS. This preserves the translated
    text (saves DeepL per-char cost and is the only way to recover the
    400 legacy .opus rows whose source audio may no longer exist) and
    avoids a RunPod/Vast.ai GPU hit (~$0.30-1/hr, unnecessary for
    recovery work).

    The test pins: (1) generate_subtitles is NOT called, (2)
    split_audio_segment is NOT called, (3) _synthesize_segment_audio IS
    called, and (4) the segment-complete payload uses the PRE-EXISTING
    vtt_content/source_vtt_content verbatim.
    """
    import json as _json
    from pathlib import Path as _Path

    w = _load_worker(env_streaming_dir=tmp_path)

    # Real webm bytes the worker's mocked synth will "produce"
    final_webm = tmp_path / "42" / "ch001" / "zh-Hans" / "seg0003.webm"

    def fake_synth(vtt, ab_id, ch, seg, locale):
        final_webm.parent.mkdir(parents=True, exist_ok=True)
        final_webm.write_bytes(b"fake-webm-regen")
        return final_webm

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = _json.loads(req.data.decode())

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        return _Resp()

    # Sentinels that WILL fail loudly if the STT branch is taken
    def forbidden_split(*a, **k):
        raise AssertionError(
            "split_audio_segment must NOT be called when vtt_content is pre-populated"
        )

    def forbidden_subtitles(*a, **k):
        raise AssertionError(
            "generate_subtitles must NOT be called when vtt_content is pre-populated"
        )

    def forbidden_stt_provider(*a, **k):
        raise AssertionError(
            "get_stt_provider must NOT be called when vtt_content is pre-populated"
        )

    existing_vtt = (
        "WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\n"
        "你好。这是一个测试。\n"
    )
    existing_source_vtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\nHello. This is a test.\n"

    segment = {
        "audiobook_id": 42,
        "chapter_index": 1,
        "segment_index": 3,
        "locale": "zh-Hans",
        "origin": "live",
        "vtt_content": existing_vtt,
        "source_vtt_content": existing_source_vtt,
    }

    with (
        patch.object(w, "split_audio_segment", side_effect=forbidden_split),
        patch.object(w, "_synthesize_segment_audio", side_effect=fake_synth),
        patch("localization.pipeline.generate_subtitles", side_effect=forbidden_subtitles),
        patch("localization.pipeline.get_stt_provider", side_effect=forbidden_stt_provider),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        result = w.process_segment(
            db_path=str(tmp_path / "unused.db"),
            segment=segment,
            audio_path=_Path("/nonexistent/book.opus"),
            chapter_start_sec=0.0,
            chapter_duration_sec=600.0,
            api_base="http://localhost:5001",
        )

    assert result is True
    payload = captured["payload"]
    # Pre-existing content flows verbatim to the callback
    assert payload["vtt_content"] == existing_vtt
    assert payload["source_vtt_content"] == existing_source_vtt
    # New audio_path reflects the regenerated webm (relative to streaming root)
    assert payload["audio_path"] == "42/ch001/zh-Hans/seg0003.webm"


def test_process_segment_skips_stt_when_only_vtt_content_present_no_source(tmp_path):
    """Legacy .opus orphan rows only have vtt_content (no source_vtt_content).

    The TTS-only regen path MUST still trigger — the worker checks
    ``vtt_content``, not ``source_vtt_content``. The source_vtt_content
    in the callback payload will just be an empty string.
    """
    import json as _json
    from pathlib import Path as _Path

    w = _load_worker(env_streaming_dir=tmp_path)

    final_webm = tmp_path / "99" / "ch000" / "zh-Hans" / "seg0000.webm"

    def fake_synth(vtt, ab_id, ch, seg, locale):
        final_webm.parent.mkdir(parents=True, exist_ok=True)
        final_webm.write_bytes(b"fake-webm")
        return final_webm

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = _json.loads(req.data.decode())

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        return _Resp()

    existing_vtt = (
        "WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.000\n代理我们的未来。\n"
    )
    segment = {
        "audiobook_id": 99,
        "chapter_index": 0,
        "segment_index": 0,
        "locale": "zh-Hans",
        "origin": "live",
        "vtt_content": existing_vtt,
        "source_vtt_content": None,  # legacy .opus row — no source
    }

    with (
        patch.object(
            w,
            "split_audio_segment",
            side_effect=AssertionError("STT must not run"),
        ),
        patch.object(w, "_synthesize_segment_audio", side_effect=fake_synth),
        patch(
            "localization.pipeline.generate_subtitles",
            side_effect=AssertionError("STT must not run"),
        ),
        patch(
            "localization.pipeline.get_stt_provider",
            side_effect=AssertionError("STT must not run"),
        ),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        result = w.process_segment(
            db_path=str(tmp_path / "unused.db"),
            segment=segment,
            audio_path=_Path("/nonexistent/book.opus"),
            chapter_start_sec=0.0,
            chapter_duration_sec=300.0,
            api_base="http://localhost:5001",
        )

    assert result is True
    payload = captured["payload"]
    assert payload["vtt_content"] == existing_vtt
    # source_vtt_content fills in as empty string (not None) — callback expects string
    assert payload["source_vtt_content"] == ""
    assert payload["audio_path"] == "99/ch000/zh-Hans/seg0000.webm"


def test_process_segment_runs_full_stt_when_vtt_content_empty(tmp_path):
    """Fresh-claim path — when vtt_content is empty/None, STT + translation run.

    Regression guard: the idempotent shortcut must trigger ONLY on
    non-empty vtt_content. A row with empty string, whitespace-only, or
    None must take the full pipeline.
    """
    from pathlib import Path as _Path
    from unittest.mock import MagicMock

    w = _load_worker(env_streaming_dir=tmp_path)

    fake_seg = tmp_path / "fake_seg.opus"
    fake_seg.write_bytes(b"fake-slice")

    split_calls: list = []
    subtitles_calls: list = []

    def fake_split(*a, **k):
        split_calls.append(1)
        return fake_seg

    def fake_generate_subtitles(audio_path, output_dir, target_locale, chapter_name, stt_provider):
        subtitles_calls.append(1)
        src = _Path(output_dir) / "source.vtt"
        tr = _Path(output_dir) / "translated.vtt"
        src.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\nhello\n")
        tr.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:03.000\n你好\n")
        return src, tr

    def fake_urlopen(req, timeout=None):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        return _Resp()

    for vtt_case in ("", None, "   \n\n  "):
        split_calls.clear()
        subtitles_calls.clear()
        segment = {
            "audiobook_id": 1,
            "chapter_index": 0,
            "segment_index": 0,
            "locale": "zh-Hans",
            "origin": "live",
            "vtt_content": vtt_case,
            "source_vtt_content": None,
        }
        with (
            patch.object(w, "split_audio_segment", side_effect=fake_split),
            patch.object(w, "_synthesize_segment_audio", return_value=None),
            patch("localization.pipeline.generate_subtitles", side_effect=fake_generate_subtitles),
            patch("localization.pipeline.get_stt_provider", return_value=MagicMock()),
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            result = w.process_segment(
                db_path=str(tmp_path / "unused.db"),
                segment=segment,
                audio_path=_Path("/nonexistent/book.opus"),
                chapter_start_sec=0.0,
                chapter_duration_sec=300.0,
                api_base="http://localhost:5001",
            )
        assert result is True, f"vtt_case={vtt_case!r} should still succeed"
        assert split_calls, f"split_audio_segment must run for empty vtt_case={vtt_case!r}"
        assert subtitles_calls, f"generate_subtitles must run for empty vtt_case={vtt_case!r}"
