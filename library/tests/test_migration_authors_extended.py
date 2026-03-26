"""
Extended unit tests for migrate_to_normalized_authors — targeting uncovered lines.

Covers: _normalize_group_case empty string (line 47), _find_canonical quality
upgrade (lines 67-68), _name_quality scoring (line 85), migrate dry_run (line 150),
narrator brand exclusion (lines 197-198), narrator junk exclusion (lines 195-196),
narrator dedup (lines 203-204*), empty sort_name skip (line 209),
and __main__ block (lines 255-273).
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from backend.migrations.migrate_to_normalized_authors import (
    _normalize_group_case,
    _find_canonical,
    _name_quality,
    migrate,
)


def create_test_db(db_path):
    """Create a minimal DB with schema and test data."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            narrator TEXT,
            author_last_name TEXT,
            author_first_name TEXT,
            narrator_last_name TEXT,
            narrator_first_name TEXT,
            file_path TEXT UNIQUE NOT NULL,
            content_type TEXT DEFAULT 'Product'
        )
    """)
    migration_sql = (
        Path(__file__).parent.parent
        / "backend"
        / "migrations"
        / "011_multi_author_narrator.sql"
    ).read_text()
    conn.executescript(migration_sql)
    return conn


class TestNormalizeGroupCase:
    """Test line 47: _normalize_group_case edge cases."""

    def test_empty_string(self):
        """Line 47: Empty string returns empty string."""
        assert _normalize_group_case("") == ""

    def test_known_group_name(self):
        """Line 50: Known group name returns preferred display form."""
        assert _normalize_group_case("full cast") == "Full Cast"
        assert _normalize_group_case("FULL CAST") == "Full Cast"
        assert _normalize_group_case("bbc radio") == "BBC Radio"

    def test_unknown_name_passes_through(self):
        """Line 51: Unknown name is returned as-is."""
        assert _normalize_group_case("Stephen King") == "Stephen King"

    def test_various_authors(self):
        """Line 50: Various Authors group name."""
        assert _normalize_group_case("various authors") == "Various Authors"


class TestFindCanonical:
    """Test lines 63, 67-68: _find_canonical dedup logic."""

    def test_new_name_returns_none(self):
        """Line 70-71: First encounter returns None (new name)."""
        seen = {}
        result = _find_canonical(seen, "Stephen King")
        assert result is None
        assert "Stephen King" in seen.values()

    def test_existing_name_returns_canonical(self):
        """Line 69: Duplicate returns canonical name."""
        seen = {}
        _find_canonical(seen, "Stephen King")
        result = _find_canonical(seen, "stephen king")
        assert result is not None

    def test_higher_quality_replaces(self):
        """Lines 67-68: Higher quality name replaces existing."""
        seen = {}
        _find_canonical(seen, "stephen king")  # all lower
        result = _find_canonical(seen, "Stephen King")  # title case — higher quality
        assert result == "Stephen King"

    def test_accented_replaces_unaccented(self):
        """Lines 67-68: Accented version replaces unaccented."""
        seen = {}
        _find_canonical(seen, "Mieville")
        result = _find_canonical(seen, "Miéville")
        # Miéville has higher quality due to accents
        assert result == "Miéville"

    def test_lower_quality_keeps_existing(self):
        """Line 69: Lower quality duplicate keeps existing canonical."""
        seen = {}
        _find_canonical(seen, "Stephen King")  # title case
        result = _find_canonical(seen, "stephen king")  # lower case — lower quality
        assert result == "Stephen King"


class TestNameQuality:
    """Test line 85: _name_quality scoring."""

    def test_title_case_scores_higher(self):
        """Line 81-82: Title case gets +10 score."""
        assert _name_quality("Stephen King") > _name_quality("stephen king")

    def test_accented_scores_higher(self):
        """Line 84-85: Accented characters get +5 score."""
        assert _name_quality("Miéville") > _name_quality("Mieville")

    def test_longer_scores_higher(self):
        """Line 87: Longer name gets slightly higher score."""
        assert _name_quality("M. R. James") > _name_quality("M.R. James")

    def test_all_lower_scores_zero_case_bonus(self):
        """Line 81: All lower gets no case bonus."""
        score = _name_quality("king")
        # Score should be just length
        assert score == len("king")


class TestMigrateDryRun:
    """Test lines 150, 155, 157: dry_run mode."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.conn = create_test_db(self.db_path)

    def teardown_method(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_dry_run_does_not_write(self):
        """Line 150: Dry run does not insert data."""
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("It", "Stephen King", "Steven Weber", "/fake/it.opus"),
        )
        self.conn.commit()

        stats = migrate(self.db_path, dry_run=True)

        # Should have processed books but not written
        assert stats["books_processed"] == 1
        # Tables should be empty in dry run (no INSERT)
        authors = self.conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        assert authors == 0

    def test_dry_run_stats_returned(self):
        """Lines 155, 157: Stats are populated even in dry run."""
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("It", "Stephen King", "Steven Weber", "/fake/it.opus"),
        )
        self.conn.commit()

        stats = migrate(self.db_path, dry_run=True)
        assert "books_processed" in stats
        assert "authors_created" in stats
        assert "narrators_created" in stats


class TestNarratorExclusions:
    """Test lines 195-199: narrator junk and brand exclusions."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.conn = create_test_db(self.db_path)

    def teardown_method(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_narrator_junk_excluded(self):
        """Lines 195-196: Junk narrator names excluded."""
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("Test", "Real Author", "Unknown", "/fake/test.opus"),
        )
        self.conn.commit()

        stats = migrate(self.db_path)
        # "Unknown" is in JUNK_NAMES, should not create narrator
        narrators = self.conn.execute("SELECT name FROM narrators").fetchall()
        narrator_names = {n[0] for n in narrators}
        assert "Unknown" not in narrator_names
        assert stats["junk_excluded"] >= 1

    def test_narrator_brand_excluded(self):
        """Lines 197-199: Brand narrator names excluded."""
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("Test", "Real Author", "Audible Studios", "/fake/test2.opus"),
        )
        self.conn.commit()

        stats = migrate(self.db_path)
        narrators = self.conn.execute("SELECT name FROM narrators").fetchall()
        narrator_names = {n[0] for n in narrators}
        assert "Audible Studios" not in narrator_names
        assert stats["brand_excluded"] >= 1

    def test_null_narrator_skipped(self):
        """Lines 194-196: Null/empty narrator names skipped."""
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, NULL, ?)",
            ("Test", "Real Author", "/fake/test3.opus"),
        )
        self.conn.commit()

        stats = migrate(self.db_path)
        narrators = self.conn.execute("SELECT COUNT(*) FROM narrators").fetchone()[0]
        assert narrators == 0


class TestNarratorDedup:
    """Test lines 203-204: narrator deduplication."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.conn = create_test_db(self.db_path)

    def teardown_method(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_narrator_dedup_merges(self):
        """Lines 202-204: Same narrator on different books is deduplicated."""
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("Book 1", "Author A", "Frank Muller", "/fake/b1.opus"),
        )
        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("Book 2", "Author B", "Frank Muller", "/fake/b2.opus"),
        )
        self.conn.commit()

        stats = migrate(self.db_path)
        narrators = self.conn.execute("SELECT COUNT(*) FROM narrators").fetchone()[0]
        assert narrators == 1
        # dedup_merged counts increments for the second occurrence
        assert stats["dedup_merged"] >= 1


class TestEmptySortNameSkip:
    """Test line 209: narrator with empty sort_name is skipped."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.conn = create_test_db(self.db_path)

    def teardown_method(self):
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_empty_sort_name_skipped(self):
        """Line 176/209: Names that produce empty sort_name are skipped."""
        from unittest.mock import patch

        self.conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("Test", "Real Author", "Valid Narrator", "/fake/testsort.opus"),
        )
        self.conn.commit()

        # Patch generate_sort_name to return empty for narrator
        with patch("backend.migrations.migrate_to_normalized_authors.generate_sort_name") as mock_sort:
            mock_sort.return_value = ""
            stats = migrate(self.db_path)

        # No authors or narrators created since sort_name is empty
        authors = self.conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        narrators = self.conn.execute("SELECT COUNT(*) FROM narrators").fetchone()[0]
        assert authors == 0
        assert narrators == 0


class TestMainBlock:
    """Test lines 255-273: __main__ block."""

    def test_main_with_explicit_db_path(self):
        """Lines 255-273: __main__ with --db-path runs migration."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        conn = create_test_db(db_path)
        conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("It", "Stephen King", "Steven Weber", "/fake/it.opus"),
        )
        conn.commit()
        conn.close()

        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.migrations.migrate_to_normalized_authors",
                "--db-path",
                db_path,
            ],
            capture_output=True,
            text=True,
            cwd=str(LIBRARY_DIR),
            timeout=30,
        )
        assert result.returncode == 0
        assert "Migration stats" in result.stdout

        Path(db_path).unlink(missing_ok=True)

    def test_main_with_dry_run(self):
        """Lines 260-261: __main__ with --dry-run flag."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        conn = create_test_db(db_path)
        conn.execute(
            "INSERT INTO audiobooks (title, author, narrator, file_path)"
            " VALUES (?, ?, ?, ?)",
            ("It", "Stephen King", "Steven Weber", "/fake/it.opus"),
        )
        conn.commit()
        conn.close()

        import subprocess
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.migrations.migrate_to_normalized_authors",
                "--db-path",
                db_path,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            cwd=str(LIBRARY_DIR),
            timeout=30,
        )
        assert result.returncode == 0

        # Verify no data was written
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        assert count == 0
        conn.close()

        Path(db_path).unlink(missing_ok=True)
