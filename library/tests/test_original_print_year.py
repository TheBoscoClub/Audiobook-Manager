"""
Tests for the Open Library original print-publication year feature
(Audiobook-Manager-nfx, v8.4.2.0).

Covers:
- schema: audiobooks.original_publish_year column present in schema.sql
- data migration 014: adds the column to an existing DB (run against a COPY
  of the dev database, never the original), dry-run makes no change,
  re-run is idempotent
- sort: _build_sort_clause("original_publish_year") emits the COALESCE
  fallback chain (OL year -> published_year), and real SQLite ordering
  honors it with NULLS LAST
- lookup: ISBN chain, title-search chain, fuzzy-mismatch rejection,
  implausible-year rejection
- populate: writes the year, skips already-populated rows (row-level
  resumability), leaves NULL on no-match
- backfill: dry-run performs no network calls and no writes; execute
  updates only rows missing the year (resumable)
- post-insert hook: registered and dispatches to the populate function
"""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
LIBRARY_DIR = Path(__file__).parent.parent
MIGRATION = PROJECT_ROOT / "data-migrations" / "014_original_publish_year.sh"
DEV_DB = LIBRARY_DIR / "backend" / "audiobooks-dev.db"
SCHEMA = LIBRARY_DIR / "backend" / "schema.sql"

sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "scripts"))

from scripts.original_print_year import (  # noqa: E402
    backfill,
    lookup_original_publish_year,
    populate_original_publish_year,
)

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def temp_db(tmp_path):
    """Minimal audiobooks table with the v8.4.2.0 column."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            isbn TEXT,
            published_year INTEGER,
            original_publish_year INTEGER,
            release_date TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db


def _insert(db, title, author=None, isbn=None, published=None, original=None, release=None):
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO audiobooks (title, author, isbn, published_year,"
        " original_publish_year, release_date) VALUES (?, ?, ?, ?, ?, ?)",
        (title, author, isbn, published, original, release),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def _mock_client(search_docs=None, isbn_edition=None, work=None):
    client = MagicMock()
    client.search.return_value = search_docs or []
    client.lookup_isbn.return_value = isbn_edition
    client.get_work.return_value = work
    return client


# ============================================================
# 1. Schema
# ============================================================


class TestSchema:
    def test_schema_sql_has_column(self):
        assert "original_publish_year INTEGER" in SCHEMA.read_text()

    def test_fresh_schema_creates_column(self, tmp_path):
        db = tmp_path / "fresh.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA.read_text())
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audiobooks)")]
        conn.close()
        assert "original_publish_year" in cols
        assert "published_year" in cols  # back-compat column kept


# ============================================================
# 2. Data migration 014 (against a COPY of the dev DB)
# ============================================================


def _run_migration(db_path, dry_run="false"):
    """Source the migration the way upgrade.sh's apply_data_migrations does."""
    script = f'DB_PATH="{db_path}"; USE_SUDO=""; DRY_RUN="{dry_run}"; source "{MIGRATION}"'
    return subprocess.run(  # nosec B602 — fixed bash invocation of a repo-tracked migration with test-controlled paths
        ["bash", "-c", script], capture_output=True, text=True, timeout=60
    )


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(audiobooks)")]
    conn.close()
    return cols


@pytest.mark.skipif(not DEV_DB.exists(), reason="dev database not present")
class TestMigration014:
    @pytest.fixture
    def dev_db_copy(self, tmp_path):
        """A COPY of the dev database — the original is never touched."""
        copy = tmp_path / "audiobooks-dev-copy.db"
        shutil.copy2(DEV_DB, copy)
        return copy

    def test_dry_run_makes_no_change(self, dev_db_copy):
        assert "original_publish_year" not in _columns(dev_db_copy)
        result = _run_migration(dev_db_copy, dry_run="true")
        assert result.returncode == 0, result.stderr
        assert "DRY-RUN" in result.stdout
        assert "original_publish_year" not in _columns(dev_db_copy)

    def test_adds_column(self, dev_db_copy):
        result = _run_migration(dev_db_copy)
        assert result.returncode == 0, result.stderr
        assert "Added audiobooks.original_publish_year" in result.stdout
        assert "original_publish_year" in _columns(dev_db_copy)

    def test_rerun_is_idempotent(self, dev_db_copy):
        assert _run_migration(dev_db_copy).returncode == 0
        result = _run_migration(dev_db_copy)
        assert result.returncode == 0, result.stderr
        assert "Added" not in result.stdout  # second run: guarded no-op
        assert _columns(dev_db_copy).count("original_publish_year") == 1

    def test_missing_db_is_noop(self, tmp_path):
        result = _run_migration(tmp_path / "does-not-exist.db")
        assert result.returncode == 0, result.stderr


# ============================================================
# 3. Sort clause + SQL ordering fallback chain
# ============================================================


class TestSortClause:
    def _clause(self, order="desc"):
        from backend.api_modular.audiobooks import _build_sort_clause

        return _build_sort_clause("original_publish_year", order)

    def test_mapping_registered(self):
        from backend.api_modular.audiobooks import _SORT_MAPPINGS

        assert _SORT_MAPPINGS["original_publish_year"] == "original_publish_year"

    def test_clause_uses_coalesce_fallback(self):
        sort_sql, sort_order = self._clause("desc")
        assert "COALESCE(original_publish_year, published_year)" in sort_sql
        assert sort_order == ""  # direction embedded in the clause

    def test_unknown_sort_still_defaults_to_title(self):
        from backend.api_modular.audiobooks import _build_sort_clause

        sort_sql, _ = _build_sort_clause("no_such_field", "asc")
        assert sort_sql == "title COLLATE NOCASE"

    @pytest.mark.parametrize("order", ["asc", "desc"])
    def test_sqlite_ordering_with_fallback(self, temp_db, order):
        """Rows OL resolved sort by original year; unresolved rows fall back
        to published_year; rows with neither go last."""
        # original=1954 beats published=2010 (OL value wins over Audible's)
        a = _insert(temp_db, "LOTR", published=2010, original=1954)
        # no OL match -> falls back to published_year 1999
        b = _insert(temp_db, "Fallback Book", published=1999, original=None)
        # both years present, newest original
        c = _insert(temp_db, "Modern Book", published=2020, original=2018)
        # no year at all -> NULLS LAST regardless of direction
        d = _insert(temp_db, "Yearless", published=None, original=None)

        sort_sql, _ = self._clause(order)
        conn = sqlite3.connect(temp_db)
        ids = [r[0] for r in conn.execute(f"SELECT id FROM audiobooks ORDER BY {sort_sql}")]  # nosec B608 — clause from code-defined allowlist under test
        conn.close()

        if order == "asc":
            assert ids == [a, b, c, d]  # 1954, 1999, 2018, NULL-last
        else:
            assert ids == [c, b, a, d]  # 2018, 1999, 1954, NULL-last


# ============================================================
# 4. Open Library lookup chain
# ============================================================


class TestLookup:
    def test_isbn_chain_wins(self):
        edition = MagicMock(work_id="OL1W")
        work = MagicMock(first_publish_year=1937)
        client = _mock_client(isbn_edition=edition, work=work)
        year = lookup_original_publish_year(client, "The Hobbit", "Tolkien", "9780261103573")
        assert year == 1937
        client.search.assert_not_called()

    def test_search_fallback_on_isbn_miss(self):
        client = _mock_client(search_docs=[{"title": "The Hobbit", "first_publish_year": 1937}])
        year = lookup_original_publish_year(client, "The Hobbit", "Tolkien", "0000000000")
        assert year == 1937
        client.lookup_isbn.assert_called_once()

    def test_exact_title_match_preferred(self):
        client = _mock_client(
            search_docs=[
                {"title": "The Hobbit: Annotated Companion", "first_publish_year": 2002},
                {"title": "The Hobbit", "first_publish_year": 1937},
            ]
        )
        assert lookup_original_publish_year(client, "The Hobbit", "Tolkien") == 1937

    def test_fuzzy_mismatch_rejected(self):
        """A wrong-book year is worse than no year — dissimilar titles yield None."""
        client = _mock_client(
            search_docs=[{"title": "Completely Different Title", "first_publish_year": 1900}]
        )
        assert lookup_original_publish_year(client, "The Hobbit", "Tolkien") is None

    @pytest.mark.parametrize("bad_year", [19, 0, -5, 3050, None, "abc"])
    def test_implausible_year_rejected(self, bad_year):
        client = _mock_client(search_docs=[{"title": "The Hobbit", "first_publish_year": bad_year}])
        assert lookup_original_publish_year(client, "The Hobbit", "Tolkien") is None

    def test_no_results_returns_none(self):
        client = _mock_client(search_docs=[])
        assert lookup_original_publish_year(client, "Obscurity", "Nobody") is None


# ============================================================
# 5. populate_original_publish_year
# ============================================================


class TestPopulate:
    def test_writes_year(self, temp_db):
        book_id = _insert(temp_db, "The Hobbit", author="Tolkien", published=2012)
        client = _mock_client(search_docs=[{"title": "The Hobbit", "first_publish_year": 1937}])
        assert populate_original_publish_year(book_id, db_path=temp_db, client=client) == 1937
        conn = sqlite3.connect(temp_db)
        stored = conn.execute(
            "SELECT original_publish_year FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()[0]
        conn.close()
        assert stored == 1937

    def test_already_populated_row_skipped_without_api_call(self, temp_db):
        """Row-level resumability: populated rows never re-hit the API."""
        book_id = _insert(temp_db, "Done Book", original=1888)
        client = _mock_client()
        assert populate_original_publish_year(book_id, db_path=temp_db, client=client) == 1888
        client.search.assert_not_called()
        client.lookup_isbn.assert_not_called()

    def test_no_match_leaves_null(self, temp_db):
        book_id = _insert(temp_db, "Obscure Book", published=2001)
        client = _mock_client(search_docs=[])
        assert populate_original_publish_year(book_id, db_path=temp_db, client=client) is None
        conn = sqlite3.connect(temp_db)
        stored = conn.execute(
            "SELECT original_publish_year FROM audiobooks WHERE id = ?", (book_id,)
        ).fetchone()[0]
        conn.close()
        assert stored is None

    def test_missing_book_id_is_noop(self, temp_db):
        client = _mock_client()
        assert populate_original_publish_year(99999, db_path=temp_db, client=client) is None


# ============================================================
# 6. Backfill (dry-run + resumable execute)
# ============================================================


class TestBackfill:
    def test_dry_run_no_api_calls_no_writes(self, temp_db, capsys):
        _insert(temp_db, "Book A", published=2000)
        _insert(temp_db, "Book B", published=2001)
        client = _mock_client()

        summary = backfill(db_path=temp_db, execute=False, client=client)

        assert summary["candidates"] == 2
        assert summary["updated"] == 0
        client.search.assert_not_called()
        client.lookup_isbn.assert_not_called()
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        conn = sqlite3.connect(temp_db)
        nulls = conn.execute(
            "SELECT COUNT(*) FROM audiobooks WHERE original_publish_year IS NULL"
        ).fetchone()[0]
        conn.close()
        assert nulls == 2

    def test_execute_updates_only_missing_rows(self, temp_db):
        """Resumability: rows already populated are not selected at all."""
        _insert(temp_db, "Already Done", original=1950)
        _insert(temp_db, "Needs Lookup", author="X", published=2005)
        client = _mock_client(search_docs=[{"title": "Needs Lookup", "first_publish_year": 1971}])

        with patch("scripts.original_print_year.time.sleep"):  # no politeness pause in tests
            summary = backfill(db_path=temp_db, execute=True, client=client)

        assert summary == {"candidates": 1, "updated": 1, "unmatched": 0, "errors": 0}
        conn = sqlite3.connect(temp_db)
        rows = dict(conn.execute("SELECT title, original_publish_year FROM audiobooks"))
        conn.close()
        assert rows == {"Already Done": 1950, "Needs Lookup": 1971}

    def test_execute_counts_unmatched_and_continues_on_error(self, temp_db):
        _insert(temp_db, "No Match", published=2000)
        _insert(temp_db, "Match", published=2001)

        client = _mock_client()

        def search_side_effect(title=None, author=None, isbn=None, limit=5):
            if title == "No Match":
                return []
            if title == "Match":
                return [{"title": "Match", "first_publish_year": 1980}]
            raise RuntimeError("unexpected title")

        client.search.side_effect = search_side_effect

        with patch("scripts.original_print_year.time.sleep"):
            summary = backfill(db_path=temp_db, execute=True, client=client)

        assert summary["updated"] == 1
        assert summary["unmatched"] == 1
        assert summary["errors"] == 0

    def test_limit_caps_candidates(self, temp_db):
        for i in range(5):
            _insert(temp_db, f"Book {i}", published=2000 + i)
        summary = backfill(db_path=temp_db, execute=False, limit=3)
        assert summary["candidates"] == 3


# ============================================================
# 7. Post-insert hook wiring
# ============================================================


class TestPostInsertHook:
    def test_hook_registered(self):
        from scanner.post_insert import registered_post_insert_hooks

        labels = [label for label, _ in registered_post_insert_hooks()]
        assert "Original print year" in labels

    def test_hook_dispatches_to_populate(self, temp_db):
        from scanner.post_insert import registered_post_insert_hooks

        hook = dict(registered_post_insert_hooks())["Original print year"]
        with patch("scripts.original_print_year.populate_original_publish_year") as mock_populate:
            hook(42, temp_db)
            mock_populate.assert_called_once_with(42, db_path=temp_db, quiet=True)


# ============================================================
# 8. UI wiring (dropdown + i18n + tutorial)
# ============================================================


class TestUiWiring:
    def test_sort_dropdown_has_both_directions_with_tooltips(self):
        html = (LIBRARY_DIR / "web-v2" / "index.html").read_text()
        assert 'value="original_publish_year:desc"' in html
        assert 'value="original_publish_year:asc"' in html
        # UI/UX standard: action items need title tooltips
        for value in ("original_publish_year:desc", "original_publish_year:asc"):
            option_line = next(line for line in html.splitlines() if f'value="{value}"' in line)
            assert 'title="' in option_line
            assert "data-i18n=" in option_line

    @pytest.mark.parametrize("locale", ["en", "zh-Hans"])
    def test_locales_have_curated_strings(self, locale):
        import json

        data = json.loads((LIBRARY_DIR / "locales" / f"{locale}.json").read_text())
        for key in (
            "sort.newestPrintYear",
            "sort.oldestPrintYear",
            "sort.title.newestPrintYear",
            "sort.title.oldestPrintYear",
        ):
            assert key in data, f"{locale} missing {key}"
            assert data[key].strip()

    def test_tutorial_mentions_original_print_year(self):
        tutorial = (LIBRARY_DIR / "web-v2" / "js" / "tutorial.js").read_text()
        assert "original print year" in tutorial.lower()
