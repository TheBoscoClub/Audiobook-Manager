"""
Tests for the /streaming-audio route (Task 13, v8.3.2).

The route serves per-segment opus files produced by the streaming
translation worker. Because the segment files live on disk (rather than
in the DB), the route is the MSE client's fetch target — one HTTP GET
per 30-second opus segment, range-aware for ``appendBuffer``.

Exercised invariants:
- Locale must be in ``backend.i18n.SUPPORTED_LOCALES`` (reject unknown)
- Path must resolve strictly under ``_streaming_audio_root`` (defense in
  depth vs ``..`` traversal)
- Missing files return 404 cleanly (no stack leak)
- Served files carry ``Content-Type: audio/ogg; codecs=opus`` so MSE
  ``SourceBuffer.appendBuffer`` accepts the frames
- Flask's ``conditional=True`` serves HTTP 206 on Range requests, which
  is what MSE uses to resume after backgrounded tabs.
"""

from __future__ import annotations

import pytest
from backend.api_modular import streaming_translate as st


@pytest.fixture
def streaming_audio_tmpdir(tmp_path, monkeypatch):
    """Swap ``_streaming_audio_root`` for a scratch dir for one test.

    Uses ``monkeypatch`` to restore the module global after the test.
    """
    monkeypatch.setattr(st, "_streaming_audio_root", tmp_path)
    return tmp_path


@pytest.fixture
def seeded_segment(streaming_audio_tmpdir):
    """Write a fake opus file at the canonical segment path."""
    book_id = 42
    ch = 0
    seg = 0
    locale = "zh-Hans"
    seg_dir = streaming_audio_tmpdir / str(book_id) / f"ch{ch:03d}" / locale
    seg_dir.mkdir(parents=True, exist_ok=True)
    seg_path = seg_dir / f"seg{seg:04d}.opus"
    # OggS magic + a few bytes — enough to confirm send_file serves the
    # actual bytes. We don't need a valid opus container for the route
    # contract.
    seg_path.write_bytes(b"OggS\x00\x02" + b"\x00" * 26 + b"fakeopus")
    return {
        "root": streaming_audio_tmpdir,
        "book_id": book_id,
        "ch": ch,
        "seg": seg,
        "locale": locale,
        "path": seg_path,
    }


# ── Path traversal ──


def test_streaming_audio_route_rejects_traversal(app_client, streaming_audio_tmpdir):
    """A locale like ``../../../etc`` must NOT escape the streaming root.

    Flask/werkzeug handles this at several layers:
    - Literal ``..`` in the locale slot falls through the
      ``SUPPORTED_LOCALES`` whitelist and returns 404.
    - A URL-encoded ``%2F`` inside a path-param slot triggers werkzeug's
      strict-slash routing and yields 405 (URL doesn't match any rule).
    - Any multi-level ``../../../`` is URL-decoded into separate path
      segments and routes to a non-existent path → 404.

    All three outcomes are acceptable — the point is no 200 body ever
    escapes the streaming root.
    """
    # Encoded-slash form. werkzeug may 405 this (URL doesn't match) or
    # 404 it (whitelist rejection); never 200.
    r1 = app_client.get("/streaming-audio/1/0/0/..%2F..%2F..%2Fetc%2Fpasswd")
    assert r1.status_code in (403, 404, 405)

    # Literal ".." in the locale slot — hits the handler, whitelist rejects.
    r2 = app_client.get("/streaming-audio/1/0/0/..")
    assert r2.status_code in (403, 404)


# ── Missing / not-yet-produced segments ──


def test_streaming_audio_route_404_when_missing(app_client, streaming_audio_tmpdir):
    """Nonexistent segment file returns 404, not a stack trace."""
    r = app_client.get("/streaming-audio/999/0/0/zh-Hans")
    assert r.status_code == 404


# ── Locale whitelist ──


def test_streaming_audio_route_404_on_invalid_locale(app_client, streaming_audio_tmpdir):
    """A locale not in SUPPORTED_LOCALES returns 404 (whitelist check)."""
    # 'xx' is not in the default SUPPORTED_LOCALES set (en, zh-Hans) and
    # does not match any AUDIOBOOKS_SUPPORTED_LOCALES override in the test
    # environment.
    r = app_client.get("/streaming-audio/1/0/0/xx")
    assert r.status_code == 404


# ── Happy path: serves opus bytes ──


def test_streaming_audio_route_serves_opus(app_client, seeded_segment):
    """GET an existing segment returns 200 + audio/ogg opus Content-Type."""
    r = app_client.get(
        f"/streaming-audio/{seeded_segment['book_id']}"
        f"/{seeded_segment['ch']}"
        f"/{seeded_segment['seg']}"
        f"/{seeded_segment['locale']}"
    )
    assert r.status_code == 200
    assert "audio/ogg" in r.headers["Content-Type"]
    # Body starts with the OggS magic we wrote
    assert r.data.startswith(b"OggS")


# ── Range request support (MSE requirement) ──


def test_streaming_audio_route_supports_range_requests(app_client, seeded_segment):
    """A ``Range: bytes=0-99`` request returns 206 with Content-Range.

    MSE SourceBuffer.appendBuffer can request partial content when
    resuming buffering; Flask's ``send_file(conditional=True)`` handles
    HTTP byte-range semantics. This test confirms the route opts into
    that behaviour rather than always returning 200 full bodies.
    """
    r = app_client.get(
        f"/streaming-audio/{seeded_segment['book_id']}"
        f"/{seeded_segment['ch']}"
        f"/{seeded_segment['seg']}"
        f"/{seeded_segment['locale']}",
        headers={"Range": "bytes=0-9"},
    )
    assert r.status_code == 206
    assert "Content-Range" in r.headers
    # First 10 bytes of the fake file we wrote
    assert len(r.data) == 10


# ── Root-not-configured handling ──


def test_streaming_audio_route_503_when_root_not_configured(app_client, monkeypatch):
    """If ``_streaming_audio_root`` is None, return 503 not 404."""
    monkeypatch.setattr(st, "_streaming_audio_root", None)
    r = app_client.get("/streaming-audio/1/0/0/zh-Hans")
    assert r.status_code == 503


# ── Guard against resolved-path escape (symlink/race defense) ──


def test_streaming_audio_route_rejects_resolved_escape(
    app_client, streaming_audio_tmpdir, tmp_path_factory
):
    """A symlink planted inside the root that points outside must be refused.

    Defense in depth: the path-traversal guard compares resolved paths,
    so a symlink aimed at /etc/passwd inside the book/ch/locale tree
    still fails the containment check.
    """
    outside = tmp_path_factory.mktemp("outside")
    outside_file = outside / "not-opus.opus"
    outside_file.write_bytes(b"outside")

    # Seed the canonical directory tree but make the seg file a symlink
    # pointing outside the root.
    locale = "zh-Hans"
    book_id = 77
    ch = 0
    seg = 0
    seg_dir = streaming_audio_tmpdir / str(book_id) / f"ch{ch:03d}" / locale
    seg_dir.mkdir(parents=True, exist_ok=True)
    (seg_dir / f"seg{seg:04d}.opus").symlink_to(outside_file)

    r = app_client.get(f"/streaming-audio/{book_id}/{ch}/{seg}/{locale}")
    # Resolve escape must be 403; we forbid symlink escape explicitly.
    assert r.status_code == 403
