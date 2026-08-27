"""Tests for the GET /api/audiobooks/<id>/chapters endpoint.

Covers:
  - Returns the chapter list when ffprobe yields chapters.
  - Returns 404 when audiobook id does not exist.
  - Returns ``{"chapters": []}`` when the source file has no chapter metadata.
  - Helper degrades gracefully (returns []) on ffprobe failure.
  - Per-worker mtime-keyed cache prevents redundant ffprobe runs for repeat
    requests against the same file.
  - Response carries ``Cache-Control: public, max-age=86400``.
  - Auth gating mirrors existing audiobook playback endpoints
    (``@auth_if_enabled``: 401 to unauthenticated callers when AUTH_ENABLED).
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


def _ensure_book_row(db_path: Path, audiobook_id: int, file_path: str) -> None:
    """Insert (or overwrite) a minimal audiobook row for chapters-endpoint tests.

    The endpoint only reads ``file_path`` from ``audiobooks``, so we don't need
    related tables. Uses INSERT OR REPLACE to keep the fixture idempotent
    across the session-scoped flask_app + per-test cleanup.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO audiobooks "
        "(id, title, author, file_path, format, content_type, file_size_mb, duration_hours) "
        "VALUES (?, 'Test Book', 'Test Author', ?, 'opus', 'Product', 100.0, 5.0)",
        (audiobook_id, file_path),
    )
    conn.commit()
    conn.close()


def _delete_book_row(db_path: Path, audiobook_id: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM audiobooks WHERE id = ?", (audiobook_id,))
    conn.commit()
    conn.close()


@pytest.fixture
def chapters_book(flask_app, session_temp_dir):
    """Create a real on-disk file + DB row for a test audiobook.

    The file is empty — ffprobe is mocked at the helper boundary, so the
    bytes don't matter. What does matter is that ``stat()`` succeeds so the
    endpoint can read ``mtime_ns`` for the cache key.
    """
    audio_dir = session_temp_dir / "chapters_test_audio"
    audio_dir.mkdir(exist_ok=True)
    audio_file = audio_dir / "book_5001.opus"
    audio_file.write_bytes(b"")  # placeholder — real bytes not needed; ffprobe is mocked

    db_path = flask_app.config["DATABASE_PATH"]
    audiobook_id = 5001
    _ensure_book_row(db_path, audiobook_id, str(audio_file))

    # Reset the per-worker LRU cache so each test starts clean.
    from backend.api_modular import audiobooks as audiobooks_mod

    audiobooks_mod._chapters_cache.clear()

    yield {"id": audiobook_id, "file": audio_file, "db_path": db_path}

    _delete_book_row(db_path, audiobook_id)
    audiobooks_mod._chapters_cache.clear()


# ─── Endpoint behavior with mocked ffprobe ─────────────────────────────────


def test_chapters_endpoint_returns_chapters_from_real_file(app_client, chapters_book):
    """Endpoint serializes ffprobe output into the documented shape."""
    fake_ffprobe_json = (
        b'{"chapters":['
        b'{"id":0,"start_time":"0.000000","end_time":"36.453875",'
        b'"tags":{"title":"Introduction"}},'
        b'{"id":1,"start_time":"36.453875","end_time":"67.918250",'
        b'"tags":{"title":"Chapter 1"}}'
        b"]}"
    )

    class _Result:
        returncode = 0
        stdout = fake_ffprobe_json.decode("utf-8")
        stderr = ""

    with patch("localization.chapters.subprocess.run", return_value=_Result()) as mock_run:
        resp = app_client.get(f"/api/audiobooks/{chapters_book['id']}/chapters")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "chapters" in body
    chapters = body["chapters"]
    assert len(chapters) == 2

    assert chapters[0] == {
        "index": 0,
        "start_ms": 0,
        "end_ms": 36454,  # 36.453875 * 1000 → rounded
        "title": "Introduction",
    }
    assert chapters[1] == {
        "index": 1,
        "start_ms": 36454,
        "end_ms": 67918,  # 67.918250 * 1000 → rounded
        "title": "Chapter 1",
    }

    # ffprobe must have been invoked exactly once for the first request.
    assert mock_run.call_count == 1


def test_chapters_endpoint_404_when_audiobook_missing(app_client):
    """Unknown audiobook id returns 404 with a JSON error body."""
    resp = app_client.get("/api/audiobooks/99999999/chapters")
    assert resp.status_code == 404
    body = resp.get_json()
    assert "error" in body


def test_chapters_endpoint_returns_empty_for_chapterless_file(app_client, chapters_book):
    """Empty ffprobe chapters → empty response array, not 404 / 500."""

    class _Result:
        returncode = 0
        stdout = '{"chapters":[]}'
        stderr = ""

    with patch("localization.chapters.subprocess.run", return_value=_Result()):
        resp = app_client.get(f"/api/audiobooks/{chapters_book['id']}/chapters")

    assert resp.status_code == 200
    assert resp.get_json() == {"chapters": []}


def test_chapters_helper_handles_ffprobe_failure(tmp_path):
    """`_ffprobe_chapters` swallows subprocess errors and returns [].

    Exercises the failure path of ``localization.chapters.extract_chapters``
    via the audiobooks-blueprint adapter — guarantees no exception escapes
    into the request handler.
    """
    from backend.api_modular.audiobooks import _ffprobe_chapters

    fake_path = tmp_path / "no_such.opus"
    fake_path.write_bytes(b"")

    import subprocess as _sp

    with patch(
        "localization.chapters.subprocess.run",
        side_effect=_sp.TimeoutExpired(cmd="ffprobe", timeout=30),
    ):
        result = _ffprobe_chapters(fake_path)

    assert result == []


def test_chapters_endpoint_cache_avoids_double_ffprobe(app_client, chapters_book):
    """Repeat requests for the same (id, mtime) hit the cache, not ffprobe."""

    class _Result:
        returncode = 0
        stdout = '{"chapters":[{"id":0,"start_time":"0","end_time":"10","tags":{"title":"Only"}}]}'
        stderr = ""

    with patch("localization.chapters.subprocess.run", return_value=_Result()) as mock_run:
        first = app_client.get(f"/api/audiobooks/{chapters_book['id']}/chapters")
        second = app_client.get(f"/api/audiobooks/{chapters_book['id']}/chapters")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json() == second.get_json()
    # Cache hit on second call → ffprobe runs exactly once across both requests.
    assert mock_run.call_count == 1


def test_chapters_response_has_cache_control_max_age(app_client, chapters_book):
    """Response carries the documented Cache-Control: public, max-age=86400."""

    class _Result:
        returncode = 0
        stdout = '{"chapters":[]}'
        stderr = ""

    with patch("localization.chapters.subprocess.run", return_value=_Result()):
        resp = app_client.get(f"/api/audiobooks/{chapters_book['id']}/chapters")

    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "public, max-age=86400"


# ─── Auth gating ───────────────────────────────────────────────────────────


def test_chapters_endpoint_requires_login_when_auth_enabled(anon_client, auth_app):
    """When AUTH_ENABLED=True, an unauthenticated caller gets 401.

    Mirrors the auth posture of ``/api/stream/<id>`` (also ``@auth_if_enabled``).
    The ``anon_client`` fixture is a test client against the auth-enabled app
    with no session cookie set.
    """
    assert auth_app.config.get("AUTH_ENABLED") is True, (
        "anon_client must hit the auth-enabled app for this assertion to mean anything"
    )
    # Use any audiobook id — the auth check fires before any DB lookup.
    resp = anon_client.get("/api/audiobooks/1/chapters")
    assert resp.status_code == 401, (
        f"Expected 401 (auth required), got {resp.status_code}: {resp.get_data(as_text=True)}"
    )
