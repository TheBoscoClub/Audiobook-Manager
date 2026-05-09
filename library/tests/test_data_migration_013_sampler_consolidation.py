"""Data migration 013 — backfill sampler-completion chapter consolidation.

Verifies that the v8.3.10.6 backfill script processes only the (audiobook,
locale) tuples that need consolidation, calls the existing
_consolidate_chapter_audio helper, and produces chapter_translations_audio
rows for sampler chapters that previously had none.

Driven by the same Python helper the shell migration embeds, executed
directly from a temp DB fixture (no shell, no upgrade.sh dispatcher) — the
dispatcher itself is covered by test_upgrade_data_migration_dispatch.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch


def _seed_schema(db_path: Path) -> None:
    """Apply schema.sql so the migration sees the production tables."""
    project_root = Path(__file__).resolve().parents[2]
    schema = (project_root / "library" / "backend" / "schema.sql").read_text()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()


def _seed_book_with_complete_sampler(
    db_path: Path,
    streaming_root: Path,
    *,
    book_id: int,
    locale: str,
    chapters: list[int],
    segments_per_chapter: int = 12,
) -> None:
    """Insert audiobook + sampler_jobs (status='complete') + per-chapter
    sampler segments (state='completed', audio_path populated). Chapter
    rows are NOT inserted — that's what the migration is supposed to do.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path, duration_hours, chapter_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (book_id, f"Book {book_id}", f"/test/book_{book_id}.opus", 1.0, max(6, len(chapters))),
    )
    for ch_idx in chapters:
        ch_dir = streaming_root / str(book_id) / f"ch{ch_idx:03d}" / locale
        ch_dir.mkdir(parents=True, exist_ok=True)
        for seg_idx in range(segments_per_chapter):
            seg_path = ch_dir / f"seg{seg_idx:04d}.webm"
            seg_path.write_bytes(b"fake-opus-" + str(seg_idx).encode())
            conn.execute(
                "INSERT INTO streaming_segments "
                "(audiobook_id, chapter_index, segment_index, locale, state, "
                " priority, origin, vtt_content, audio_path) "
                "VALUES (?, ?, ?, ?, 'completed', 2, 'sampler', ?, ?)",
                (
                    book_id,
                    ch_idx,
                    seg_idx,
                    locale,
                    f"WEBVTT\n\n1\n00:00:00.000 --> 00:00:30.000\nseg{seg_idx}",
                    f"{book_id}/ch{ch_idx:03d}/{locale}/seg{seg_idx:04d}.webm",
                ),
            )
    conn.execute(
        "INSERT INTO sampler_jobs "
        "(audiobook_id, locale, status, segments_target, segments_done) "
        "VALUES (?, ?, 'complete', ?, ?)",
        (
            book_id,
            locale,
            segments_per_chapter * len(chapters),
            segments_per_chapter * len(chapters),
        ),
    )
    conn.commit()
    conn.close()


def _run_backfill_helper(db_path: Path, streaming_root: Path) -> dict:
    """Execute the migration's Python driver directly, with subprocess.run
    mocked so we count ffmpeg/ffprobe invocations without invoking the
    real binaries. Returns counters {ffmpeg_calls, ffprobe_calls}.

    The driver lives inside data-migrations/013_backfill_sampler_consolidation.sh
    as a heredoc. We replicate it here verbatim so the test exercises the
    same logic; if the shell wrapper changes the embedded script the test
    must be updated alongside.
    """
    import importlib
    import sys

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root / "library") not in sys.path:
        sys.path.insert(0, str(project_root / "library"))

    # Force a fresh import so module-level _streaming_audio_root rebinding
    # doesn't bleed across tests.
    if "backend.api_modular.streaming_translate" in sys.modules:
        importlib.reload(sys.modules["backend.api_modular.streaming_translate"])
    from backend.api_modular import streaming_translate as st

    st._streaming_audio_root = streaming_root.resolve()

    counts = {"ffmpeg": 0, "ffprobe": 0}

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "ffmpeg":
            counts["ffmpeg"] += 1
            out = Path(cmd[-1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake-chapter-webm")

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()
        if cmd and cmd[0] == "ffprobe":
            counts["ffprobe"] += 1

            class _R:
                returncode = 0
                stdout = "180.0\n"
                stderr = ""

            return _R()

        class _Other:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Other()

    with patch.object(st.subprocess, "run", side_effect=fake_run):
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row

        sampler_rows = conn.execute(
            "SELECT audiobook_id, locale FROM sampler_jobs "
            "WHERE status = 'complete' "
            "ORDER BY audiobook_id, locale"
        ).fetchall()

        for r in sampler_rows:
            audiobook_id = r["audiobook_id"]
            locale = r["locale"]
            sampler_chapters = [
                row["chapter_index"]
                for row in conn.execute(
                    "SELECT DISTINCT chapter_index FROM streaming_segments "
                    "WHERE audiobook_id = ? AND locale = ? AND origin = 'sampler' "
                    "AND state = 'completed' "
                    "ORDER BY chapter_index",
                    (audiobook_id, locale),
                ).fetchall()
            ]
            for ch_idx in sampler_chapters:
                existing = conn.execute(
                    "SELECT 1 FROM chapter_translations_audio "
                    "WHERE audiobook_id = ? AND chapter_index = ? AND locale = ?",
                    (audiobook_id, ch_idx, locale),
                ).fetchone()
                if existing is not None:
                    continue
                st._consolidate_chapter_audio(conn, audiobook_id, int(ch_idx), locale)
        conn.commit()
        conn.close()
    return counts


# ── tests ──


def test_backfill_inserts_chapter_rows_for_complete_samplers(tmp_path):
    """A book with sampler complete but no chapter rows gets one row per
    sampler chapter after the migration runs.
    """
    db_path = tmp_path / "audiobooks.db"
    _seed_schema(db_path)
    streaming_root = tmp_path / "streaming-audio"
    streaming_root.mkdir()

    _seed_book_with_complete_sampler(
        db_path, streaming_root, book_id=42, locale="zh-Hans", chapters=[0]
    )

    counts = _run_backfill_helper(db_path, streaming_root)

    assert counts["ffmpeg"] == 1, "expected one ffmpeg invocation per sampler chapter"
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT chapter_index, locale, tts_provider FROM chapter_translations_audio "
        "WHERE audiobook_id = 42"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0] == (0, "zh-Hans", "streaming")


def test_backfill_skips_chapters_already_consolidated(tmp_path):
    """If the chapter row already exists (full-chapter consolidation
    happened mid-flight), the migration must NOT re-run ffmpeg or
    overwrite the row.
    """
    db_path = tmp_path / "audiobooks.db"
    _seed_schema(db_path)
    streaming_root = tmp_path / "streaming-audio"
    streaming_root.mkdir()

    _seed_book_with_complete_sampler(
        db_path, streaming_root, book_id=43, locale="zh-Hans", chapters=[0]
    )
    # Pre-insert the chapter row (simulating a full-chapter consolidation
    # that happened before the migration ran).
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chapter_translations_audio "
        "(audiobook_id, chapter_index, locale, audio_path, "
        " tts_provider, tts_voice, duration_seconds) "
        "VALUES (43, 0, 'zh-Hans', '/preexisting/full.webm', "
        "'streaming', 'zh-CN-XiaoxiaoNeural', 600.0)",
    )
    conn.commit()
    conn.close()

    counts = _run_backfill_helper(db_path, streaming_root)

    # No ffmpeg invocation — the chapter was already consolidated.
    assert counts["ffmpeg"] == 0
    # Row still points to the pre-existing full-chapter file (not overwritten).
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT audio_path, duration_seconds FROM chapter_translations_audio "
        "WHERE audiobook_id = 43 AND chapter_index = 0 AND locale = 'zh-Hans'"
    ).fetchone()
    conn.close()
    assert row == ("/preexisting/full.webm", 600.0)


def test_backfill_handles_multi_chapter_sampler_scope(tmp_path):
    """Sampler scope can span chapters 0 and 1 for short opening chapters.
    Both must be consolidated by the backfill.
    """
    db_path = tmp_path / "audiobooks.db"
    _seed_schema(db_path)
    streaming_root = tmp_path / "streaming-audio"
    streaming_root.mkdir()

    _seed_book_with_complete_sampler(
        db_path, streaming_root, book_id=44, locale="zh-Hans", chapters=[0, 1]
    )

    counts = _run_backfill_helper(db_path, streaming_root)

    assert counts["ffmpeg"] == 2, "two sampler chapters → two ffmpeg invocations"
    conn = sqlite3.connect(str(db_path))
    rows = sorted(
        conn.execute(
            "SELECT chapter_index FROM chapter_translations_audio "
            "WHERE audiobook_id = 44 AND locale = 'zh-Hans'"
        ).fetchall()
    )
    conn.close()
    assert [r[0] for r in rows] == [0, 1]


def test_backfill_is_idempotent_when_re_run(tmp_path):
    """Running the helper twice in a row must not duplicate ffmpeg work
    or create duplicate chapter rows.
    """
    db_path = tmp_path / "audiobooks.db"
    _seed_schema(db_path)
    streaming_root = tmp_path / "streaming-audio"
    streaming_root.mkdir()

    _seed_book_with_complete_sampler(
        db_path, streaming_root, book_id=45, locale="zh-Hans", chapters=[0]
    )

    first = _run_backfill_helper(db_path, streaming_root)
    second = _run_backfill_helper(db_path, streaming_root)

    assert first["ffmpeg"] == 1
    assert second["ffmpeg"] == 0, "second run must skip already-consolidated chapter"

    conn = sqlite3.connect(str(db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM chapter_translations_audio WHERE audiobook_id = 45"
    ).fetchone()[0]
    conn.close()
    assert count == 1
