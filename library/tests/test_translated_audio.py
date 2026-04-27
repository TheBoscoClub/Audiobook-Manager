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
import time
from pathlib import Path

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
        resp = app_client.post("/api/translated-audio/generate", json={"locale": "zh-Hans"})
        assert resp.status_code == 400

    def test_book_not_found_404(self, app_client, audio_db):
        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 99999, "locale": "zh-Hans"}
        )
        assert resp.status_code == 404

    def test_no_subtitles_400(self, app_client, audio_db):
        """Without translated subtitles, generation should fail early."""
        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
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
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
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
        resp = app_client.post("/api/user/translated-audio/request", json={"locale": "zh-Hans"})
        assert resp.status_code == 400

    def test_no_subtitles_400(self, app_client, audio_db):
        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 400
        assert "subtitles" in resp.get_json()["error"].lower()

    def test_book_not_found_404(self, app_client, audio_db):
        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 99999, "locale": "zh-Hans"}
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
            "/api/user/translated-audio/request", json={"audiobook_id": 5, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "exists"

    def test_existing_running_job_returns_already_running(self, app_client, audio_db):
        """If a job is already running, user request should short-circuit."""
        _set_status(1, "zh-Hans", state="running", phase="synth", message="busy")
        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "already_running"


# ── Pure helper functions ──


class TestExtractUserId:
    """_extract_user_id — pull id from dict/object/None."""

    def test_none_returns_none(self):
        assert ta._extract_user_id(None) is None

    def test_dict_form(self):
        assert ta._extract_user_id({"id": 42}) == 42

    def test_dict_missing_id(self):
        assert ta._extract_user_id({}) is None

    def test_object_attribute(self):
        class User:
            id = 7

        assert ta._extract_user_id(User()) == 7

    def test_object_without_id(self):
        class Anon:
            pass

        assert ta._extract_user_id(Anon()) is None


class TestCheckUserCooldown:
    """_check_user_cooldown — rate-limit per-user per-book."""

    def setup_method(self):
        _user_requests.clear()

    def test_anonymous_user_no_cooldown(self, flask_app):
        with flask_app.app_context():
            assert ta._check_user_cooldown(None, 1) is None

    def test_first_request_records_and_passes(self, flask_app):
        with flask_app.app_context():
            result = ta._check_user_cooldown(5, 1)
            assert result is None
            assert (5, 1) in _user_requests

    def test_repeat_within_cooldown_returns_429(self, flask_app):
        _user_requests[(5, 1)] = time.time()
        with flask_app.app_context():
            result = ta._check_user_cooldown(5, 1)
            assert result is not None
            response, status = result
            assert status == 429
            body = response.get_json()
            assert body["status"] == "cooldown"
            assert "retry_after" in body

    def test_repeat_after_cooldown_passes(self, flask_app):
        _user_requests[(5, 1)] = time.time() - (ta._USER_COOLDOWN_SEC + 5)
        with flask_app.app_context():
            result = ta._check_user_cooldown(5, 1)
            assert result is None


class TestLoadTranslatedAudioContext:
    """_load_translated_audio_context — prerequisite validation for user-facing TTS."""

    def test_book_not_found(self, app_client, audio_db):
        with app_client.application.app_context():
            resp, status = ta._load_translated_audio_context(99999, "zh-Hans")
        assert status == 404

    def test_no_subtitles(self, app_client, audio_db):
        with app_client.application.app_context():
            resp, status = ta._load_translated_audio_context(1, "zh-Hans")
        assert status == 400

    def test_audio_already_exists(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/x.vtt', 'whisper')"
        )
        conn.execute(
            "INSERT INTO chapter_translations_audio "
            "(audiobook_id, chapter_index, locale, audio_path, tts_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/x.opus', 'edge-tts')"
        )
        conn.commit()
        conn.close()

        with app_client.application.app_context():
            resp, status = ta._load_translated_audio_context(1, "zh-Hans")
        assert status == 200
        assert resp.get_json()["status"] == "exists"

    def test_happy_path_returns_paths(self, app_client, audio_db):
        conn = sqlite3.connect(str(audio_db))
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
            "VALUES (1, 0, 'zh-Hans', '/tmp/x.vtt', 'whisper')"
        )
        conn.commit()
        conn.close()

        with app_client.application.app_context():
            vtt_path, audio_path = ta._load_translated_audio_context(1, "zh-Hans")
        assert vtt_path == Path(
            "/tmp/x.vtt"
        )  # nosec B108  # asserting DB round-trip of synthetic fixture path; no file I/O
        assert audio_path == Path(
            "/tmp/test1.opus"
        )  # nosec B108  # asserting DB round-trip of synthetic fixture path; no file I/O


# ── Admin _generate closure coverage ──


class _DummyTTS:
    def __init__(self, name="dummy-tts"):
        self.name = name


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def threading_capture(monkeypatch):
    """Capture the target of threading.Thread(...) invocations.

    Returns a list[callable] so tests can drive the background closure
    synchronously. We neuter .start() so no real thread runs.
    """
    captured: list = []

    class _FakeThread:
        def __init__(self, *args, target=None, **kwargs):
            self._target = target
            captured.append(target)

        def start(self):
            pass

    monkeypatch.setattr(ta.threading, "Thread", _FakeThread)
    return captured


@pytest.fixture
def closure_prereqs(audio_db, tmp_path):
    """Seed subtitles + VTT file on disk so closure can read text."""
    vtt_file = tmp_path / "sub.vtt"
    vtt_file.write_text(
        "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHello world\n\n"
        "2\n00:00:02.000 --> 00:00:04.000\nFrom translated audio\n\n",
        encoding="utf-8",
    )
    audio_file = tmp_path / "book.opus"
    audio_file.write_bytes(b"fake")

    conn = sqlite3.connect(str(audio_db))
    conn.execute("DELETE FROM audiobooks WHERE id = 1")
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type) "
        "VALUES (1, 'T', 'A', ?, 'opus', 5.0, 'Product')",
        (str(audio_file),),
    )
    conn.execute(
        "INSERT INTO chapter_subtitles "
        "(audiobook_id, chapter_index, locale, vtt_path, stt_provider) "
        "VALUES (1, 0, 'zh-Hans', ?, 'whisper')",
        (str(vtt_file),),
    )
    conn.commit()
    conn.close()
    return {"vtt": vtt_file, "audio": audio_file, "db": audio_db}


class TestAdminGenerateClosure:
    """Drive the admin /api/translated-audio/generate background closure.

    We patch threading.Thread to capture the target, then invoke it
    synchronously with mocks in place of the GPU + ffmpeg subcommands.
    """

    def setup_method(self):
        _job_status.clear()

    def _stub_tts_imports(self, monkeypatch, tts_obj, raise_on_get=False):
        """Install fake modules into sys.modules so the closure's deferred
        `from library.localization...` imports resolve to our stubs.

        The production module (`translated_audio.py`) does
        `from library.localization.selection import WorkloadHint` inside the
        closure. In pytest, `library` is not importable as a package, but
        Python's import machinery consults `sys.modules` first — so we can
        register fakes there and bypass the import entirely.
        """
        import sys
        import types

        factory = types.SimpleNamespace(
            get_tts_provider=(
                (lambda *a, **kw: (_ for _ in ()).throw(ValueError("boom")))
                if raise_on_get
                else (lambda *a, **kw: tts_obj)
            ),
            synthesize_with_fallback=(lambda tts, text, locale, voice, out: out.write_bytes(b"x")),
        )
        selection = types.SimpleNamespace(WorkloadHint=types.SimpleNamespace(LONG_FORM="LF"))
        # We need parent packages to exist in sys.modules before leaf imports work
        monkeypatch.setitem(sys.modules, "library", types.ModuleType("library"))
        monkeypatch.setitem(
            sys.modules, "library.localization", types.ModuleType("library.localization")
        )
        monkeypatch.setitem(sys.modules, "library.localization.selection", selection)
        monkeypatch.setitem(
            sys.modules, "library.localization.tts", types.ModuleType("library.localization.tts")
        )
        monkeypatch.setitem(sys.modules, "library.localization.tts.factory", factory)

    def test_tts_init_failure_sets_failed_state(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        self._stub_tts_imports(monkeypatch, _DummyTTS(), raise_on_get=True)

        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200

        # Drive the captured closure
        assert len(threading_capture) == 1
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "failed"
        assert status["phase"] == "error"

    def test_happy_path_writes_row(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        self._stub_tts_imports(monkeypatch, _DummyTTS("edge-tts"))

        # Stub subprocess.run for ffmpeg + ffprobe
        calls = []

        def _run(cmd, **kw):
            calls.append(cmd[0])
            if cmd[0] == "ffmpeg":
                # Write the output file so existence checks pass
                Path(cmd[-1]).write_bytes(b"opus")
                return _FakeCompletedProcess(returncode=0)
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(returncode=0, stdout="123.45\n")
            return _FakeCompletedProcess(returncode=0)

        # subprocess is imported inside the closure, so we monkeypatch the module
        import subprocess

        monkeypatch.setattr(subprocess, "run", _run)

        resp = app_client.post(
            "/api/translated-audio/generate",
            json={"audiobook_id": 1, "locale": "zh-Hans", "voice": "v1"},
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "completed"

        conn = sqlite3.connect(str(audio_db))
        row = conn.execute(
            "SELECT audio_path, duration_seconds FROM chapter_translations_audio "
            "WHERE audiobook_id=1 AND locale='zh-Hans'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert abs(row[1] - 123.45) < 0.01

    def test_missing_vtt_on_disk_returns_silently(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        # Remove the VTT file so vtt_path.exists() is False
        closure_prereqs["vtt"].unlink()
        self._stub_tts_imports(monkeypatch, _DummyTTS())

        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        # No "completed" state; closure exits early
        status = _get_status(1, "zh-Hans")
        # The only statuses the closure set are "starting" (loading_tts) and
        # "running" (gpu_spinup); closure returns after detecting missing VTT.
        assert status["state"] == "running"

    def test_empty_vtt_returns_silently(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        closure_prereqs["vtt"].write_text("WEBVTT\n\n", encoding="utf-8")
        self._stub_tts_imports(monkeypatch, _DummyTTS())

        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        # Closure returns after detecting no text; last status was "running"
        assert status["state"] == "running"

    def test_transcode_failure_keeps_intermediate(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        self._stub_tts_imports(monkeypatch, _DummyTTS("xtts-vastai"))  # wav intermediate

        def _run(cmd, **kw):
            if cmd[0] == "ffmpeg":
                return _FakeCompletedProcess(returncode=1, stderr="bad codec")
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(returncode=1)
            return _FakeCompletedProcess(returncode=0)

        import subprocess

        monkeypatch.setattr(subprocess, "run", _run)

        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "completed"
        # Intermediate should remain because transcode failed
        intermediate = (
            closure_prereqs["audio"].parent
            / "translated"
            / (closure_prereqs["audio"].stem + ".zh-Hans.tts.wav")
        )
        assert intermediate.exists()

    def test_generic_exception_sets_failed_state(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        import sys
        import types

        tts = _DummyTTS("xtts-vastai")

        def _raise_synth(*a, **kw):
            raise RuntimeError("network dead")

        factory = types.SimpleNamespace(
            get_tts_provider=lambda *a, **kw: tts, synthesize_with_fallback=_raise_synth
        )
        selection = types.SimpleNamespace(WorkloadHint=types.SimpleNamespace(LONG_FORM="LF"))
        monkeypatch.setitem(sys.modules, "library", types.ModuleType("library"))
        monkeypatch.setitem(
            sys.modules, "library.localization", types.ModuleType("library.localization")
        )
        monkeypatch.setitem(sys.modules, "library.localization.selection", selection)
        monkeypatch.setitem(
            sys.modules, "library.localization.tts", types.ModuleType("library.localization.tts")
        )
        monkeypatch.setitem(sys.modules, "library.localization.tts.factory", factory)

        resp = app_client.post(
            "/api/translated-audio/generate", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "failed"
        assert "network dead" in status.get("error", "")


class TestUserRequestClosure:
    """Drive the user-facing background closure under the same patching pattern."""

    def setup_method(self):
        _job_status.clear()
        _user_requests.clear()

    def _stub_tts_imports(self, monkeypatch, tts_obj, synth_raises=None):
        import sys
        import types

        def _synth(tts, text, locale, voice, out):
            if synth_raises:
                raise synth_raises
            out.write_bytes(b"x")

        factory = types.SimpleNamespace(
            get_tts_provider=lambda *a, **kw: tts_obj, synthesize_with_fallback=_synth
        )
        selection = types.SimpleNamespace(WorkloadHint=types.SimpleNamespace(LONG_FORM="LF"))
        monkeypatch.setitem(sys.modules, "library", types.ModuleType("library"))
        monkeypatch.setitem(
            sys.modules, "library.localization", types.ModuleType("library.localization")
        )
        monkeypatch.setitem(sys.modules, "library.localization.selection", selection)
        monkeypatch.setitem(
            sys.modules, "library.localization.tts", types.ModuleType("library.localization.tts")
        )
        monkeypatch.setitem(sys.modules, "library.localization.tts.factory", factory)

    def test_user_request_happy_path(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        self._stub_tts_imports(monkeypatch, _DummyTTS("edge-tts"))

        def _run(cmd, **kw):
            if cmd[0] == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"opus")
                return _FakeCompletedProcess(returncode=0)
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(returncode=0, stdout="55.0\n")
            return _FakeCompletedProcess(returncode=0)

        import subprocess

        monkeypatch.setattr(subprocess, "run", _run)

        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "completed"

    def test_user_request_missing_vtt_sets_failed(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        closure_prereqs["vtt"].unlink()
        self._stub_tts_imports(monkeypatch, _DummyTTS())

        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        # User flow sets explicit failed state when VTT is missing on disk.
        assert status["state"] == "failed"
        assert "missing" in status["message"].lower()

    def test_user_request_empty_vtt_sets_failed(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        closure_prereqs["vtt"].write_text("WEBVTT\n\n", encoding="utf-8")
        self._stub_tts_imports(monkeypatch, _DummyTTS())

        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "failed"

    def test_user_request_synth_exception_sets_failed(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        self._stub_tts_imports(
            monkeypatch, _DummyTTS("xtts-vastai"), synth_raises=RuntimeError("gpu gone")
        )

        resp = app_client.post(
            "/api/user/translated-audio/request", json={"audiobook_id": 1, "locale": "zh-Hans"}
        )
        assert resp.status_code == 200
        threading_capture[0]()
        status = _get_status(1, "zh-Hans")
        assert status["state"] == "failed"
        assert "gpu gone" in status["error"]

    def test_user_cooldown_blocks_second_request(
        self, app_client, audio_db, closure_prereqs, threading_capture, monkeypatch
    ):
        # Seed cooldown for user 0 (anonymous has None user_id; skip)
        from flask import g as flask_g

        self._stub_tts_imports(monkeypatch, _DummyTTS("edge-tts"))

        def _run(*a, **kw):
            return _FakeCompletedProcess(returncode=0)

        import subprocess

        monkeypatch.setattr(subprocess, "run", _run)

        # Simulate an authenticated user via request context
        with app_client.application.test_request_context():
            flask_g.user = {"id": 10}

        # Force cooldown pre-populated for user 10, book 1
        _user_requests[(10, 1)] = time.time()

        # Directly test the helper (endpoint uses `g.user` from request ctx,
        # which the test client doesn't populate cleanly).
        with app_client.application.app_context():
            result = ta._check_user_cooldown(10, 1)
        assert result is not None
        _, status = result
        assert status == 429
