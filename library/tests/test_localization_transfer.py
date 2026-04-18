"""Unit tests for ``localization.transfer`` export/import pipeline.

Exercises the public ``export_translations`` and ``import_translations``
functions plus their pure helpers against an in-memory SQLite schema that
mirrors the production layout. VTT and audio files are created as small
byte fixtures in a ``tempfile.TemporaryDirectory`` so the tarball's
file-extraction logic runs end-to-end without touching the real library.
"""

from __future__ import annotations

import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from localization import transfer  # noqa: E402

# ── Schema helpers ─────────────────────────────────────────────────────


def _build_db(db_path: Path) -> None:
    """Create a schema that matches the subset transfer touches."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL
        );
        CREATE TABLE chapter_subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            chapter_title TEXT,
            locale TEXT NOT NULL,
            vtt_path TEXT NOT NULL,
            stt_provider TEXT,
            translation_provider TEXT
        );
        CREATE TABLE chapter_translations_audio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            audio_path TEXT NOT NULL,
            tts_provider TEXT,
            tts_voice TEXT,
            duration_seconds REAL
        );
        CREATE TABLE audiobook_translations (
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            title TEXT,
            author_display TEXT,
            series_display TEXT,
            description TEXT,
            translator TEXT,
            pinyin_sort TEXT,
            PRIMARY KEY (audiobook_id, locale)
        );
        CREATE TABLE collection_translations (
            collection_id TEXT NOT NULL,
            locale TEXT NOT NULL,
            name TEXT NOT NULL,
            translator TEXT,
            PRIMARY KEY (collection_id, locale)
        );
        CREATE TABLE string_translations (
            source_hash TEXT NOT NULL,
            locale TEXT NOT NULL,
            source TEXT NOT NULL,
            translation TEXT NOT NULL,
            translator TEXT,
            PRIMARY KEY (source_hash, locale)
        );
        CREATE TABLE translation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            state TEXT DEFAULT 'pending',
            step TEXT,
            finished_at TIMESTAMP,
            UNIQUE(audiobook_id, locale)
        );
        """)
    conn.close()


def _populate_source(db_path: Path, vtt_dir: Path, audio_dir: Path) -> None:
    """Seed the source DB + produce the VTT/audio files it references."""
    vtt_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    vtt_en = vtt_dir / "ch000.en.vtt"
    vtt_zh = vtt_dir / "ch000.zh-Hans.vtt"
    audio_zh = audio_dir / "ch000.zh-Hans.opus"
    vtt_en.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n")
    vtt_zh.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n你好\n")
    audio_zh.write_bytes(b"OggS-fake-opus-body")

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (?, ?, ?)",
        (1, "The Fellowship of the Ring", str(audio_dir / "fellowship.opus")),
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (?, ?, ?)",
        (2, "The Two Towers", str(audio_dir / "towers.opus")),
    )

    conn.execute(
        "INSERT INTO chapter_subtitles "
        "(audiobook_id, chapter_index, chapter_title, locale, vtt_path, stt_provider) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (1, 0, "Prologue", "en", str(vtt_en), "whisper"),
    )
    conn.execute(
        "INSERT INTO chapter_subtitles "
        "(audiobook_id, chapter_index, chapter_title, locale, vtt_path, "
        "stt_provider, translation_provider) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 0, "Prologue", "zh-Hans", str(vtt_zh), "whisper", "deepl"),
    )
    conn.execute(
        "INSERT INTO chapter_translations_audio "
        "(audiobook_id, chapter_index, locale, audio_path, tts_provider, "
        "tts_voice, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 0, "zh-Hans", str(audio_zh), "edge-tts", "zh-CN-XiaoxiaoNeural", 3.5),
    )
    conn.execute(
        "INSERT INTO audiobook_translations "
        "(audiobook_id, locale, title, author_display, translator) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, "zh-Hans", "护戒联盟", "托尔金", "邓嘉宛"),
    )
    conn.execute(
        "INSERT INTO collection_translations "
        "(collection_id, locale, name, translator) VALUES (?, ?, ?, ?)",
        ("epic-fantasy", "zh-Hans", "史诗奇幻", "deepl"),
    )
    conn.execute(
        "INSERT INTO string_translations "
        "(source_hash, locale, source, translation, translator) "
        "VALUES (?, ?, ?, ?, ?)",
        ("hash001", "zh-Hans", "Library", "图书馆", "deepl"),
    )
    conn.execute(
        "INSERT INTO translation_queue "
        "(audiobook_id, locale, state, step, finished_at) "
        "VALUES (?, ?, 'completed', 'tts', CURRENT_TIMESTAMP)",
        (1, "zh-Hans"),
    )
    conn.commit()
    conn.close()


def _populate_target(db_path: Path, audio_dir: Path) -> None:
    """Target DB has the same titles but different IDs — simulating a separate environment."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    # Different IDs — import must remap by title
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (?, ?, ?)",
        (101, "The Fellowship of the Ring", str(audio_dir / "fellowship.opus")),
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, file_path) VALUES (?, ?, ?)",
        (102, "The Two Towers", str(audio_dir / "towers.opus")),
    )
    conn.commit()
    conn.close()


# ── Pure helpers ───────────────────────────────────────────────────────


class TestConnect:
    def test_returns_connection_with_row_factory(self, tmp_path):
        db = tmp_path / "x.db"
        _build_db(db)
        conn = transfer._connect(str(db))
        try:
            assert conn.row_factory is sqlite3.Row
            row = conn.execute("SELECT 1 AS x").fetchone()
            assert row["x"] == 1
        finally:
            conn.close()


class TestTableExists:
    def test_returns_true_when_present(self, tmp_path):
        db = tmp_path / "x.db"
        _build_db(db)
        conn = transfer._connect(str(db))
        try:
            assert transfer._table_exists(conn, "audiobooks") is True
        finally:
            conn.close()

    def test_returns_false_when_absent(self, tmp_path):
        db = tmp_path / "x.db"
        _build_db(db)
        conn = transfer._connect(str(db))
        try:
            assert transfer._table_exists(conn, "nonexistent_table") is False
        finally:
            conn.close()


class TestFetchLocale:
    def test_fetches_all_when_no_locale(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")
        conn = transfer._connect(str(db))
        try:
            rows = transfer._fetch_locale(conn, "subs_other", None)
            assert len(rows) == 1
            assert rows[0]["locale"] == "zh-Hans"
        finally:
            conn.close()

    def test_filters_by_locale(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")
        conn = transfer._connect(str(db))
        try:
            rows = transfer._fetch_locale(conn, "audio", "zh-Hans")
            assert len(rows) == 1
            rows_absent = transfer._fetch_locale(conn, "audio", "fr")
            assert rows_absent == []
        finally:
            conn.close()

    def test_subs_en_has_no_locale_filter(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")
        conn = transfer._connect(str(db))
        try:
            rows = transfer._fetch_locale(conn, "subs_en", "ignored")
            assert len(rows) == 1
            assert rows[0]["locale"] == "en"
        finally:
            conn.close()


class TestFetchOptional:
    def test_returns_empty_when_table_missing(self, tmp_path):
        db = tmp_path / "src.db"
        # Minimal DB without collection_translations
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE audiobooks (id INTEGER)")
        conn.close()
        conn = transfer._connect(str(db))
        try:
            rows = transfer._fetch_optional(conn, "collection_translations", "collections", None)
            assert rows == []
        finally:
            conn.close()

    def test_fetches_when_table_present(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")
        conn = transfer._connect(str(db))
        try:
            rows = transfer._fetch_optional(conn, "collection_translations", "collections", None)
            assert len(rows) == 1
        finally:
            conn.close()


class TestBuildManifest:
    def test_shape(self):
        # Build a manifest from a synthetic data bundle
        data = {
            "en_subs": [],
            "subs": [],
            "audio": [],
            "meta": [],
            "collections": [],
            "strings": [],
            "queue": [],
            "books": {
                1: {"title": "x", "file_path": "/tmp/x"}
            },  # nosec B108 -- DB string fixture, no filesystem write
        }
        manifest = transfer._build_manifest(data)
        assert manifest["version"] == 2
        assert set(manifest.keys()) >= {
            "version",
            "subtitles",
            "translated_audio",
            "audiobook_translations",
            "collection_translations",
            "string_translations",
            "queue_completed",
            "books",
        }


class TestBuildIdMap:
    def test_maps_matching_titles(self):
        manifest = {
            "books": {
                "1": {"title": "Fellowship", "file_path": "/x"},
                "2": {"title": "Two Towers", "file_path": "/y"},
            }
        }
        books_by_title = {"Fellowship": 101, "Two Towers": 102}
        id_map = transfer._build_id_map(manifest, books_by_title)
        assert id_map == {1: 101, 2: 102}

    def test_skips_unmatched_titles(self):
        manifest = {
            "books": {
                "1": {"title": "Fellowship", "file_path": "/x"},
                "99": {"title": "Unknown", "file_path": "/z"},
            }
        }
        books_by_title = {"Fellowship": 101}
        id_map = transfer._build_id_map(manifest, books_by_title)
        assert id_map == {1: 101}

    def test_empty_when_no_matches(self):
        manifest = {"books": {"1": {"title": "X", "file_path": "/"}}}
        id_map = transfer._build_id_map(manifest, {"Y": 1})
        assert id_map == {}


# ── Export ─────────────────────────────────────────────────────────────


class TestExportTranslations:
    def test_export_all_locales(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")

        out = tmp_path / "export.tar.gz"
        summary = transfer.export_translations(str(db), str(out))

        assert out.exists()
        assert summary["en_subtitles"] == 1
        assert summary["translated_subtitles"] == 1
        assert summary["audio_files"] == 1
        assert summary["metadata"] == 1
        assert summary["collections"] == 1
        assert summary["strings"] == 1
        assert summary["books"] == 2

        # Tarball contains manifest + referenced files
        with tarfile.open(str(out), "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        assert "manifest.json" in names
        assert any(n.startswith("vtt/") for n in names)
        assert any(n.startswith("audio/") for n in names)

    def test_export_filtered_by_locale(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")

        out = tmp_path / "zh.tar.gz"
        summary = transfer.export_translations(str(db), str(out), locale="zh-Hans")

        assert summary["translated_subtitles"] == 1
        assert summary["audio_files"] == 1

    def test_export_filtered_locale_without_data(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")

        out = tmp_path / "fr.tar.gz"
        summary = transfer.export_translations(str(db), str(out), locale="fr")

        # EN is always included (no locale filter); other asset types should be 0
        assert summary["translated_subtitles"] == 0
        assert summary["audio_files"] == 0

    def test_export_skips_missing_files(self, tmp_path):
        db = tmp_path / "src.db"
        _build_db(db)
        # Populate DB but intentionally do NOT create the referenced VTT/audio files
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'A', '/x')")
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) VALUES (1, 0, 'en', '/missing.vtt')"
        )
        conn.commit()
        conn.close()

        out = tmp_path / "out.tar.gz"
        transfer.export_translations(str(db), str(out))

        with tarfile.open(str(out), "r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
        # Only manifest, no missing vtt included
        assert "manifest.json" in names
        assert not any(n.startswith("vtt/") for n in names)


# ── Import ─────────────────────────────────────────────────────────────


class TestImportTranslations:
    def test_end_to_end_export_then_import(self, tmp_path):
        src_db = tmp_path / "src.db"
        _build_db(src_db)
        _populate_source(src_db, tmp_path / "src_vtt", tmp_path / "src_audio")

        # Export into a tarball
        archive = tmp_path / "bundle.tar.gz"
        transfer.export_translations(str(src_db), str(archive))

        # Build a fresh target DB with matching titles, different IDs
        tgt_db = tmp_path / "tgt.db"
        _build_db(tgt_db)
        _populate_target(tgt_db, tmp_path / "tgt_audio")

        # Import
        summary = transfer.import_translations(str(tgt_db), str(archive))

        assert summary["matched"] == 2  # both books matched by title
        assert summary["total_books"] == 2
        assert summary["subtitles"] == 2  # en + zh
        assert summary["audio"] == 1
        assert summary["metadata"] == 1
        assert summary["collections"] == 1
        assert summary["strings"] == 1

        # Confirm DB rows were remapped to target IDs (101, 102)
        conn = sqlite3.connect(str(tgt_db))
        rows = conn.execute(
            "SELECT audiobook_id, locale FROM chapter_subtitles ORDER BY locale"
        ).fetchall()
        conn.close()
        assert all(r[0] == 101 for r in rows)  # all under ID 101 (Fellowship)

    def test_import_missing_manifest_exits(self, tmp_path):
        # Make a tarball with NO manifest.json — tar.extractfile raises KeyError
        archive = tmp_path / "bad.tar.gz"
        bogus = tmp_path / "bogus.txt"
        bogus.write_text("nothing")
        with tarfile.open(str(archive), "w:gz") as tar:
            tar.add(str(bogus), arcname="bogus.txt")

        with pytest.raises((KeyError, SystemExit)):
            transfer._read_manifest(str(archive))

    def test_import_no_matching_books_warns(self, tmp_path, capsys):
        src_db = tmp_path / "src.db"
        _build_db(src_db)
        _populate_source(src_db, tmp_path / "src_vtt", tmp_path / "src_audio")

        archive = tmp_path / "bundle.tar.gz"
        transfer.export_translations(str(src_db), str(archive))

        # Target DB with completely different titles
        tgt_db = tmp_path / "tgt.db"
        _build_db(tgt_db)
        conn = sqlite3.connect(str(tgt_db))
        conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'Unrelated', '/x')")
        conn.commit()
        conn.close()

        summary = transfer.import_translations(str(tgt_db), str(archive))
        assert summary["matched"] == 0
        err = capsys.readouterr().err
        assert "No matching books" in err


# ── CLI ────────────────────────────────────────────────────────────────


class TestMain:
    def test_main_export_invokes(self, tmp_path, monkeypatch):
        db = tmp_path / "src.db"
        _build_db(db)
        _populate_source(db, tmp_path / "vtt", tmp_path / "audio")

        out = tmp_path / "out.tar.gz"
        monkeypatch.setattr(sys, "argv", ["transfer", "--db", str(db), "export", "-o", str(out)])
        transfer.main()
        assert out.exists()

    def test_main_import_invokes(self, tmp_path, monkeypatch):
        src_db = tmp_path / "src.db"
        _build_db(src_db)
        _populate_source(src_db, tmp_path / "vtt", tmp_path / "audio")
        archive = tmp_path / "out.tar.gz"
        transfer.export_translations(str(src_db), str(archive))

        tgt_db = tmp_path / "tgt.db"
        _build_db(tgt_db)
        _populate_target(tgt_db, tmp_path / "tgt_audio")

        monkeypatch.setattr(
            sys, "argv", ["transfer", "--db", str(tgt_db), "import", "-a", str(archive)]
        )
        transfer.main()

        # Verify import happened
        conn = sqlite3.connect(str(tgt_db))
        cnt = conn.execute("SELECT COUNT(*) FROM chapter_subtitles").fetchone()[0]
        conn.close()
        assert cnt > 0
