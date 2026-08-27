"""Every Sources-tree walk must be recursive (Audiobook-Manager-0hb).

The Sources tree used to be walked recursively in two places (the checksum
collector and the conversion counter) and non-recursively in four others
(ASIN-from-voucher, ASIN-from-filename, the enrichment backfill and the
metadata updater). They agreed only because Sources happens to be flat
today. The first per-author subdirectory, structure-preserving batch import
or manual tidy-up would have split them: books with checksums and a
conversion count, but no ASIN and no enrichment, and no error anywhere.

Every fixture below nests its source files **one directory deep and puts
nothing at the top level**, so a non-recursive ``glob`` finds nothing at all
and each site returns its not-found answer. A flat fixture would prove
nothing here — flat is exactly the case that already worked.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).resolve().parents[1]
if str(LIBRARY_DIR) not in sys.path:
    sys.path.insert(0, str(LIBRARY_DIR))

# The nested source pair every test shares. The ASIN is 10 chars starting
# with 'B' so it satisfies both filename regexes in play.
NESTED_ASIN = "B0NESTED01"
NESTED_SUBDIR = "Nested Author"
NESTED_STEM = f"{NESTED_ASIN}_Nested_Book-AAX_22_32"
BOOK_TITLE = "Nested Book"


def _nested_sources(root: Path) -> Path:
    """A Sources tree whose only ``.aaxc``/``.voucher`` pair is in a subdir."""
    sources = root / "Sources"
    nested = sources / NESTED_SUBDIR
    nested.mkdir(parents=True)
    (nested / f"{NESTED_STEM}.aaxc").write_bytes(b"\x00")
    (nested / f"{NESTED_STEM}.voucher").write_text(
        json.dumps({"content_license": {"asin": NESTED_ASIN}})
    )
    # Nothing at the Sources root: a non-recursive glob must come up empty.
    assert not [p for p in sources.iterdir() if p.is_file()]
    return sources


def _library_book(root: Path) -> Path:
    """A Library-side audiobook file whose stem matches the nested source."""
    book_dir = root / "Library" / "Nested Author" / BOOK_TITLE
    book_dir.mkdir(parents=True)
    book = book_dir / f"{BOOK_TITLE}.opus"
    book.write_bytes(b"\x00")
    return book


# ── metadata_utils: ASIN extraction (two call sites) ───────────────────


def test_asin_from_voucher_finds_a_nested_voucher(tmp_path):
    from scanner.metadata_utils import _extract_asin_from_voucher

    sources = _nested_sources(tmp_path)
    book = _library_book(tmp_path)
    assert _extract_asin_from_voucher(book, sources) == NESTED_ASIN


def test_asin_from_filename_finds_a_nested_source(tmp_path):
    from scanner.metadata_utils import _extract_asin_from_filename

    sources = _nested_sources(tmp_path)
    book = _library_book(tmp_path)
    assert _extract_asin_from_filename(book, sources) == NESTED_ASIN


def test_extract_asin_end_to_end_finds_a_nested_source(tmp_path):
    """The public entry point, with no ``chapters.json`` to short-circuit it."""
    from scanner.metadata_utils import extract_asin

    sources = _nested_sources(tmp_path)
    book = _library_book(tmp_path)
    assert not (book.parent / "chapters.json").exists()
    assert extract_asin(book, sources) == NESTED_ASIN


# ── backfill_enrichment: phase 1 ASIN recovery ─────────────────────────


def _asin_recovery_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE audiobooks (id INTEGER PRIMARY KEY, title TEXT, author TEXT, asin TEXT)"
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, asin) VALUES (1, ?, ?, NULL)",
        (BOOK_TITLE, "Nested Author"),
    )
    conn.commit()
    conn.close()


def test_phase1_asin_recovery_finds_a_nested_voucher(tmp_path):
    from scripts.backfill_enrichment import phase1_asin_recovery

    sources = _nested_sources(tmp_path)
    db_path = tmp_path / "audiobooks.db"
    _asin_recovery_db(db_path)

    assert phase1_asin_recovery(db_path, sources, dry_run=False) == 1

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT asin FROM audiobooks WHERE id = 1").fetchone()[0] == NESTED_ASIN
    finally:
        conn.close()


# ── update_metadata_from_source: source-file lookup ────────────────────


def test_find_source_file_finds_a_nested_source(tmp_path, monkeypatch):
    from scripts import update_metadata_from_source

    sources = _nested_sources(tmp_path)
    book = _library_book(tmp_path)
    # Module-level SOURCES_DIR comes from config (AUDIOBOOKS_SOURCES); point it
    # at the fixture rather than hardcoding any real path.
    monkeypatch.setattr(update_metadata_from_source, "SOURCES_DIR", sources)

    found = update_metadata_from_source.find_source_file(BOOK_TITLE, book)
    assert found is not None
    assert found.name == f"{NESTED_STEM}.aaxc"
    assert found.parent.name == NESTED_SUBDIR


# ── the two sites converted in the prior pass, pinned here too ─────────


def test_checksum_and_conversion_counter_agree_on_the_nested_tree(tmp_path):
    """All five walks must return the same answer for the same tree."""
    from scanner.utils.canonical import iter_source_files

    sources = _nested_sources(tmp_path)
    assert [p.name for p in iter_source_files(sources)] == [f"{NESTED_STEM}.aaxc"]
    assert [p.name for p in iter_source_files(sources, ("*.voucher",))] == [
        f"{NESTED_STEM}.voucher"
    ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
