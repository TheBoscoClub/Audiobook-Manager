"""The importer's post-insert hook queues every new book for translation (4j4).

Before this fix the "Translation queue" hook called
``enqueue_book_all_locales(audiobook_id)`` with no database: in the
mover/scanner process ``init_queue()`` never runs, so every call raised
"Localization queue is not initialized", the registry printed it as a
non-fatal warning, and no imported book was ever queued. These tests run the
hook the way the importer does — a fresh process state with the module-level
queue database unset — against a real SQLite file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import localization.config as loc_cfg
import localization.queue as lq
import pytest
import scanner.post_insert as post_insert


@pytest.fixture
def db(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "audiobooks.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT)")
    conn.executemany("INSERT INTO audiobooks VALUES (?, 't')", [(1,), (2,)])
    conn.commit()
    conn.close()
    # Importer process state: the API-side init_queue() has never run.
    monkeypatch.setattr(lq, "_db_path", None)
    monkeypatch.setattr(loc_cfg, "SUPPORTED_LOCALES", ["en", "zh-Hans", "ja"])
    return path


def _hook():
    return dict(post_insert.registered_post_insert_hooks())["Translation queue"]


def _rows(db: Path) -> list[tuple[int, str, str]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT audiobook_id, locale, state FROM translation_queue ORDER BY audiobook_id, locale"
        ).fetchall()
    finally:
        conn.close()


def test_hook_enqueues_every_non_english_locale_without_init_queue(db: Path):
    _hook()(1, db)
    assert _rows(db) == [(1, "ja", "pending"), (1, "zh-Hans", "pending")]


def test_hook_is_idempotent(db: Path):
    _hook()(1, db)
    _hook()(1, db)
    assert len(_rows(db)) == 2


def test_english_and_unsafe_locales_never_queued(db: Path, monkeypatch):
    monkeypatch.setattr(
        loc_cfg, "SUPPORTED_LOCALES", ["en", " zh-Hans", "", "en-GB", "../x", "zh-Hans", "x\n"]
    )
    _hook()(2, db)
    assert _rows(db) == [(2, "zh-Hans", "pending")]


def test_english_only_queues_nothing(db: Path, monkeypatch):
    monkeypatch.setattr(loc_cfg, "SUPPORTED_LOCALES", ["en"])
    _hook()(1, db)
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert "translation_queue" not in tables or _rows(db) == []


def test_full_registry_fanout_queues_the_book(db: Path, monkeypatch):
    """The real run_post_insert_hooks path, other hooks stubbed out."""
    hooks = [
        (label, h) for label, h in post_insert._POST_INSERT_HOOKS if label == "Translation queue"
    ]
    monkeypatch.setattr(post_insert, "_POST_INSERT_HOOKS", hooks)
    post_insert.run_post_insert_hooks(1, db, quiet=False)
    assert [r[1] for r in _rows(db)] == ["ja", "zh-Hans"]


def test_enqueue_failure_never_fails_the_import(db: Path, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(lq, "enqueue_book_all_locales", boom)
    hooks = [
        (label, h) for label, h in post_insert._POST_INSERT_HOOKS if label == "Translation queue"
    ]
    monkeypatch.setattr(post_insert, "_POST_INSERT_HOOKS", hooks)
    post_insert.run_post_insert_hooks(1, db)  # must not raise
    assert "Translation queue error (non-fatal): database is locked" in capsys.readouterr().err


def test_import_single_queues_new_book(tmp_path: Path, db: Path, monkeypatch):
    """End to end through the mover's importer: a newly inserted row is queued."""
    import scanner.import_single as imp

    conn = sqlite3.connect(db)
    monkeypatch.setattr(imp, "get_file_metadata", lambda *a, **k: {"title": "T", "author": "A"})
    monkeypatch.setattr(imp, "extract_cover_art", lambda *a, **k: None)
    monkeypatch.setattr(imp, "insert_audiobook", lambda c, m, cp: 2)
    hooks = [
        (label, h) for label, h in post_insert._POST_INSERT_HOOKS if label == "Translation queue"
    ]
    monkeypatch.setattr(post_insert, "_POST_INSERT_HOOKS", hooks)
    result = imp._import_single_file(tmp_path / "book.opus", conn, tmp_path, tmp_path, db)
    conn.close()
    assert result == "added"
    assert [(r[0], r[1]) for r in _rows(db)] == [(2, "ja"), (2, "zh-Hans")]
