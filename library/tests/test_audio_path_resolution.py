"""Tests for ``resolve_local_audio_path`` — the production-parity helper that
lets the same DB rows resolve to different on-disk locations across
environments (e.g., the path captured at scan time may differ from the local
install's library root, but the relative subpath under ``Library/`` is
constant by project convention).

These tests use only synthetic paths under pytest's ``tmp_path``; the helper
itself contains zero operator-specific path literals, and neither does this
test module.

Also includes one Flask-test-client integration test exercising the
``/api/stream/<id>`` route end-to-end with a foreign DB-stored path that
rebases successfully under the local root.
"""

import sqlite3
from pathlib import Path

import pytest

from backend.api_modular._audio_paths import (
    DEFAULT_AUDIOBOOKS_LIBRARY,
    DEFAULT_AUDIOBOOKS_SUPPLEMENTS,
    resolve_local_audio_path,
    resolve_local_supplement_path,
)


# ─── Unit tests for resolve_local_audio_path ──────────────────────────────


class TestResolveLocalAudioPath:
    def test_identity_case_returns_stored_path_when_it_exists(self, tmp_path):
        """When the stored path itself exists, return it unchanged."""
        f = tmp_path / "Library" / "Author" / "Book" / "book.opus"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"fake audio")

        assert resolve_local_audio_path(str(f)) == f

    def test_rebase_resolves_foreign_prefix_to_local_audiobooks_library(
        self, tmp_path, monkeypatch
    ):
        """Stored path scanned in environment A; file lives under local env_b root.

        The relative subpath after ``Library/`` is the same in both — only the
        prefix differs. Helper must rebase under the local AUDIOBOOKS_LIBRARY.
        """
        env_a_root = tmp_path / "env_a" / "Library"
        env_b_root = tmp_path / "env_b" / "Library"
        env_b_root.mkdir(parents=True)

        # File EXISTS at the local (env_b) root — NOT at the stored (env_a) path
        local_file = env_b_root / "Author" / "Book" / "book.opus"
        local_file.parent.mkdir(parents=True)
        local_file.write_bytes(b"fake audio")

        # DB has the env_a (foreign) path; file isn't there
        stored = env_a_root / "Author" / "Book" / "book.opus"
        assert not stored.exists()

        monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

        resolved = resolve_local_audio_path(str(stored))
        assert resolved == local_file.resolve()

    def test_returns_none_when_neither_stored_nor_rebased_exists(self, tmp_path, monkeypatch):
        """Both candidates missing — return None so caller can 404."""
        env_b_root = tmp_path / "env_b" / "Library"
        env_b_root.mkdir(parents=True)
        monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

        # Stored path doesn't exist; rebased candidate doesn't exist either
        stored = tmp_path / "env_a" / "Library" / "Author" / "Book" / "missing.opus"
        assert resolve_local_audio_path(str(stored)) is None

    def test_returns_none_when_stored_path_lacks_library_segment(self, tmp_path):
        """Stored path doesn't follow the ``Library/`` convention — no rebase."""
        stored = tmp_path / "weird" / "path" / "book.opus"
        # File doesn't exist and there's no Library segment to anchor a rebase
        assert resolve_local_audio_path(str(stored)) is None

    def test_no_directory_traversal_via_dotdot(self, tmp_path, monkeypatch):
        """Defensive: ``..`` segments after Library/ must not escape the local root."""
        env_b_root = tmp_path / "env_b" / "Library"
        env_b_root.mkdir(parents=True)
        # Create a file OUTSIDE env_b that traversal might try to target
        outside = tmp_path / "outside-secret.txt"
        outside.write_bytes(b"secret")

        monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

        # Stored path contains traversal that would escape env_b_root after rebase
        stored = "/foreign/Library/../../../" + outside.name
        result = resolve_local_audio_path(stored)
        assert result is None  # MUST refuse to resolve outside the local root

    def test_helper_uses_default_when_env_var_unset(self, tmp_path, monkeypatch):
        """When AUDIOBOOKS_LIBRARY is unset, the canonical project default is used.

        That default is the canonical project install convention (also used by
        duplicates.py) — agnostic, not operator-specific. The rebased candidate
        won't exist on the test machine, so the helper returns None gracefully
        without raising.
        """
        monkeypatch.delenv("AUDIOBOOKS_LIBRARY", raising=False)
        stored = tmp_path / "foreign" / "Library" / "Synth" / "Book" / "book.opus"
        assert resolve_local_audio_path(str(stored)) is None

    def test_helper_accepts_path_like_object(self, tmp_path):
        """Helper's signature accepts ``str | os.PathLike[str]`` per its annotation."""
        f = tmp_path / "Library" / "A" / "B" / "book.opus"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x")

        # Pass a Path object rather than a string — should work identically.
        assert resolve_local_audio_path(f) == f

    def test_rebase_preserves_multi_segment_relative_subpath(self, tmp_path, monkeypatch):
        """Rebase must preserve the full relative subpath after ``Library/``."""
        env_a_root = tmp_path / "env_a" / "Library"
        env_b_root = tmp_path / "env_b" / "Library"

        deep = env_b_root / "Genre" / "Author Name" / "Series" / "Book Title" / "ch.opus"
        deep.parent.mkdir(parents=True)
        deep.write_bytes(b"x")

        monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

        stored = env_a_root / "Genre" / "Author Name" / "Series" / "Book Title" / "ch.opus"
        assert resolve_local_audio_path(str(stored)) == deep.resolve()

    def test_default_constant_is_canonical_project_default(self):
        """``DEFAULT_AUDIOBOOKS_LIBRARY`` matches the canonical project install path.

        This isn't operator-specific — it's the default path the project's own
        ``library/config.py`` and ``duplicates.py`` use when the operator hasn't
        overridden via env var. Asserting it stays in sync prevents accidental
        drift.
        """
        assert DEFAULT_AUDIOBOOKS_LIBRARY.endswith("/Library")
        assert "/srv/audiobooks" in DEFAULT_AUDIOBOOKS_LIBRARY


# ─── Unit tests for resolve_local_supplement_path ─────────────────────────


class TestResolveLocalSupplementPath:
    """Mirror of ``TestResolveLocalAudioPath`` for the supplements root.

    Same algorithm, different segment + env var + default — verifies the
    shared underlying ``_resolve_under_root`` works correctly when wired
    to the supplements root.
    """

    def test_identity_case_returns_stored_path_when_it_exists(self, tmp_path):
        """When the stored path itself exists, return it unchanged."""
        f = tmp_path / "Supplements" / "book.pdf"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"fake pdf")

        assert resolve_local_supplement_path(str(f)) == f

    def test_rebase_resolves_foreign_prefix_to_local_supplements_root(self, tmp_path, monkeypatch):
        """Stored path scanned in environment A; file lives under local env_b root.

        The relative subpath after ``Supplements/`` is the same in both —
        only the prefix differs. Helper must rebase under the local
        ``AUDIOBOOKS_SUPPLEMENTS``.
        """
        env_a_root = tmp_path / "env_a" / "Supplements"
        env_b_root = tmp_path / "env_b" / "Supplements"
        env_b_root.mkdir(parents=True)

        local_file = env_b_root / "book.pdf"
        local_file.write_bytes(b"fake pdf")

        stored = env_a_root / "book.pdf"
        assert not stored.exists()

        monkeypatch.setenv("AUDIOBOOKS_SUPPLEMENTS", str(env_b_root))

        resolved = resolve_local_supplement_path(str(stored))
        assert resolved == local_file.resolve()

    def test_returns_none_when_neither_stored_nor_rebased_exists(self, tmp_path, monkeypatch):
        """Both candidates missing — return None so caller can 404."""
        env_b_root = tmp_path / "env_b" / "Supplements"
        env_b_root.mkdir(parents=True)
        monkeypatch.setenv("AUDIOBOOKS_SUPPLEMENTS", str(env_b_root))

        stored = tmp_path / "env_a" / "Supplements" / "missing.pdf"
        assert resolve_local_supplement_path(str(stored)) is None

    def test_returns_none_when_stored_path_lacks_supplements_segment(self, tmp_path):
        """Stored path doesn't follow the ``Supplements/`` convention — no rebase."""
        stored = tmp_path / "weird" / "path" / "book.pdf"
        assert resolve_local_supplement_path(str(stored)) is None

    def test_no_directory_traversal_via_dotdot(self, tmp_path, monkeypatch):
        """Defensive: ``..`` segments after Supplements/ must not escape the local root."""
        env_b_root = tmp_path / "env_b" / "Supplements"
        env_b_root.mkdir(parents=True)
        outside = tmp_path / "outside-secret.txt"
        outside.write_bytes(b"secret")

        monkeypatch.setenv("AUDIOBOOKS_SUPPLEMENTS", str(env_b_root))

        stored = "/foreign/Supplements/../../../" + outside.name
        result = resolve_local_supplement_path(stored)
        assert result is None

    def test_default_constant_is_canonical_project_default(self):
        """``DEFAULT_AUDIOBOOKS_SUPPLEMENTS`` matches the canonical install path.

        Mirrors the audiobooks-library default check; agnostic of operator.
        """
        assert DEFAULT_AUDIOBOOKS_SUPPLEMENTS.endswith("/Supplements")
        assert "/srv/audiobooks" in DEFAULT_AUDIOBOOKS_SUPPLEMENTS


# ─── Integration: stream_audiobook resolves a foreign DB path ─────────────


def _insert_book_row(db_path: Path, audiobook_id: int, file_path: str) -> None:
    """Insert a minimal audiobook row pointing at ``file_path``.

    The streaming endpoint only reads ``file_path`` and ``format``, so we
    don't need to populate related tables.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO audiobooks "
        "(id, title, author, file_path, format, content_type, file_size_mb, duration_hours) "
        "VALUES (?, 'Test Book', 'Test Author', ?, 'opus', 'Product', 1.0, 0.1)",
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
def foreign_path_book(flask_app, tmp_path, monkeypatch):
    """Set up: DB row carries a path under ``env_a/Library/...`` that does NOT
    exist on disk, but the same relative subpath DOES exist under
    ``env_b/Library/...`` which is the local AUDIOBOOKS_LIBRARY.
    """
    env_a_root = tmp_path / "env_a" / "Library"  # foreign — never created
    env_b_root = tmp_path / "env_b" / "Library"
    env_b_root.mkdir(parents=True)

    relative = Path("Synth Author") / "Synth Book" / "book.opus"
    local_file = env_b_root / relative
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"fake opus data")

    foreign_stored = str(env_a_root / relative)

    monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

    db_path = flask_app.config["DATABASE_PATH"]
    audiobook_id = 7042
    _insert_book_row(db_path, audiobook_id, foreign_stored)

    yield {
        "id": audiobook_id,
        "stored": foreign_stored,
        "local_file": local_file,
        "env_b_root": env_b_root,
    }

    _delete_book_row(db_path, audiobook_id)


def test_stream_endpoint_resolves_foreign_db_path_to_local_file(app_client, foreign_path_book):
    """``/api/stream/<id>`` returns 200 when the DB path is foreign but the
    rebased local file exists.

    This is the production-parity case: dev/QA VM has the same DB rows as
    prod (whose absolute paths point at prod's library root), but the actual
    files live under the VM's own AUDIOBOOKS_LIBRARY. Without the helper,
    every play would 404.
    """
    resp = app_client.get(f"/api/stream/{foreign_path_book['id']}")
    assert resp.status_code == 200
    assert resp.mimetype == "audio/ogg"
    # Body should be the bytes we wrote into the local file
    assert resp.data == b"fake opus data"


def test_stream_endpoint_returns_404_when_neither_path_exists(
    app_client, flask_app, tmp_path, monkeypatch
):
    """Regression check: the helper still 404s when nothing resolves.

    Mirrors the existing ``test_stream_file_not_on_disk`` behavior in
    test_audiobooks_extended.py — important to confirm the new resolver
    didn't accidentally start serving wrong files.
    """
    env_b_root = tmp_path / "env_b" / "Library"
    env_b_root.mkdir(parents=True)
    monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

    db_path = flask_app.config["DATABASE_PATH"]
    audiobook_id = 7043
    foreign_stored = str(tmp_path / "env_a" / "Library" / "Author" / "Book" / "missing.opus")
    _insert_book_row(db_path, audiobook_id, foreign_stored)

    try:
        resp = app_client.get(f"/api/stream/{audiobook_id}")
        assert resp.status_code == 404
        assert "File not found" in resp.get_json()["error"]
    finally:
        _delete_book_row(db_path, audiobook_id)


# ─── Integration: download_supplement resolves a foreign DB path ──────────


def _insert_supplement_row(
    db_path: Path, supplement_id: int, file_path: str, filename: str
) -> None:
    """Insert a minimal supplement row pointing at ``file_path``."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO supplements "
        "(id, audiobook_id, type, filename, file_path, file_size_mb) "
        "VALUES (?, NULL, 'pdf', ?, ?, 0.001)",
        (supplement_id, filename, file_path),
    )
    conn.commit()
    conn.close()


def _delete_supplement_row(db_path: Path, supplement_id: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM supplements WHERE id = ?", (supplement_id,))
    conn.commit()
    conn.close()


def test_supplement_endpoint_resolves_foreign_db_path_to_local_file(
    app_client, flask_app, tmp_path, monkeypatch
):
    """``/api/supplements/<id>/download`` returns 200 when the DB path is
    foreign but the rebased local file exists.

    Same production-parity case as the stream endpoint test above:
    dev/QA installs whose ``AUDIOBOOKS_SUPPLEMENTS`` differs from the
    scan-time root must still serve the supplement.
    """
    env_a_root = tmp_path / "env_a" / "Supplements"  # foreign — never created
    env_b_root = tmp_path / "env_b" / "Supplements"
    env_b_root.mkdir(parents=True)

    relative_name = "synth-companion.pdf"
    local_file = env_b_root / relative_name
    local_file.write_bytes(b"fake pdf bytes")

    foreign_stored = str(env_a_root / relative_name)
    monkeypatch.setenv("AUDIOBOOKS_SUPPLEMENTS", str(env_b_root))

    db_path = flask_app.config["DATABASE_PATH"]
    supplement_id = 8042
    _insert_supplement_row(db_path, supplement_id, foreign_stored, relative_name)

    try:
        resp = app_client.get(f"/api/supplements/{supplement_id}/download")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert resp.data == b"fake pdf bytes"
    finally:
        _delete_supplement_row(db_path, supplement_id)


def test_supplement_endpoint_returns_404_when_neither_path_exists(
    app_client, flask_app, tmp_path, monkeypatch
):
    """Regression check: missing supplements still 404 cleanly."""
    env_b_root = tmp_path / "env_b" / "Supplements"
    env_b_root.mkdir(parents=True)
    monkeypatch.setenv("AUDIOBOOKS_SUPPLEMENTS", str(env_b_root))

    db_path = flask_app.config["DATABASE_PATH"]
    supplement_id = 8043
    foreign_stored = str(tmp_path / "env_a" / "Supplements" / "missing.pdf")
    _insert_supplement_row(db_path, supplement_id, foreign_stored, "missing.pdf")

    try:
        resp = app_client.get(f"/api/supplements/{supplement_id}/download")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()
    finally:
        _delete_supplement_row(db_path, supplement_id)


# ─── Integration: subtitle generation resolves a foreign DB path ──────────


def test_subtitle_user_request_resolves_foreign_db_path(
    app_client, flask_app, tmp_path, monkeypatch
):
    """``/api/user/subtitles/request`` accepts a book whose DB ``file_path``
    is foreign as long as the local rebased audio file exists.

    Without the helper, the endpoint would 404 with "Audio file not found"
    on every dev/QA install whose ``AUDIOBOOKS_LIBRARY`` differs from prod.
    The helper rebases under the local root and the request proceeds.

    We assert HTTP < 500 (not specifically 200) because the request may be
    accepted (started/cooldown/queued) depending on background-thread state;
    the critical regression we are guarding against is the 404 "Audio file
    not found on disk" path, which the helper eliminates.
    """
    env_a_root = tmp_path / "env_a" / "Library"
    env_b_root = tmp_path / "env_b" / "Library"
    env_b_root.mkdir(parents=True)

    relative = Path("Synth Author") / "Synth Book" / "book.opus"
    local_file = env_b_root / relative
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"fake opus data")

    foreign_stored = str(env_a_root / relative)
    monkeypatch.setenv("AUDIOBOOKS_LIBRARY", str(env_b_root))

    db_path = flask_app.config["DATABASE_PATH"]
    audiobook_id = 7044
    _insert_book_row(db_path, audiobook_id, foreign_stored)

    try:
        resp = app_client.post(
            "/api/user/subtitles/request",
            json={"audiobook_id": audiobook_id, "locale": "zh-Hans"},
        )
        # Whatever it returns, it MUST NOT be the 404 "Audio file not found"
        # path that this fix targets. The DB path is foreign; only the
        # helper can find the file on disk.
        body_text = resp.get_data(as_text=True)
        assert "Audio file not found on disk" not in body_text
        # Sanity bound: not a 500 (which would mean the resolution worked
        # but something else exploded).
        assert resp.status_code < 500
    finally:
        _delete_book_row(db_path, audiobook_id)
