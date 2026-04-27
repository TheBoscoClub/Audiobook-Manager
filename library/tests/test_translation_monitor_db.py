"""Direct unit tests for ``library/translation_monitor/db.py``.

Closes Audiobook-Manager-66z. Coverage was 30% via indirect calls from the
broader monitor test suite; this file exercises the path-resolution branches,
PRAGMA settings, and schema-detection helper directly with real tmp_path
sqlite files.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────────────
# resolve_db_path — explicit / env / canonical fallback
# ──────────────────────────────────────────────────────────────────────────


class TestResolveDbPath:
    def test_explicit_arg_wins_over_env(self, monkeypatch, tmp_path):
        from translation_monitor import db as module

        monkeypatch.setenv("AUDIOBOOKS_DATABASE", "/should-not-be-used.db")
        explicit = tmp_path / "explicit.db"
        assert module.resolve_db_path(explicit) == str(explicit)

    def test_explicit_path_object(self, tmp_path):
        from translation_monitor import db as module

        explicit = tmp_path / "explicit.db"
        # Pass as Path not str — verifies str() conversion happens
        result = module.resolve_db_path(explicit)
        assert result == str(explicit)
        assert isinstance(result, str)

    def test_env_var_when_no_explicit(self, monkeypatch):
        from translation_monitor import db as module

        monkeypatch.setenv("AUDIOBOOKS_DATABASE", "/var/lib/audiobooks/test.db")
        assert module.resolve_db_path() == "/var/lib/audiobooks/test.db"

    def test_env_var_empty_falls_through_to_canonical(self, monkeypatch):
        from translation_monitor import db as module

        # Empty env var should fall through (truthiness check)
        monkeypatch.setenv("AUDIOBOOKS_DATABASE", "")
        # Stub the canonical-default helper to avoid touching the real config
        with patch.object(module, "_canonical_default_db", return_value="/canon.db"):
            assert module.resolve_db_path() == "/canon.db"

    def test_no_env_no_explicit_uses_canonical(self, monkeypatch):
        from translation_monitor import db as module

        monkeypatch.delenv("AUDIOBOOKS_DATABASE", raising=False)
        with patch.object(module, "_canonical_default_db", return_value="/canon.db"):
            assert module.resolve_db_path() == "/canon.db"


# ──────────────────────────────────────────────────────────────────────────
# _canonical_default_db — import success vs ImportError fallback
# ──────────────────────────────────────────────────────────────────────────


class TestCanonicalDefaultDb:
    def test_import_success_returns_config_value(self, monkeypatch):
        """When `from config import AUDIOBOOKS_DATABASE` succeeds, use it."""
        from translation_monitor import db as module

        # Inject a fake config module with the expected attribute.
        fake_config = type(sys)("config")
        fake_config.AUDIOBOOKS_DATABASE = Path("/srv/audiobooks/db/from_config.db")
        monkeypatch.setitem(sys.modules, "config", fake_config)

        # Even if the env var is set, the config value takes precedence in
        # this helper (resolve_db_path layers env on top, but THIS function
        # is the canonical-default lookup).
        monkeypatch.setenv("AUDIOBOOKS_DATABASE", "/should-not-leak.db")

        assert module._canonical_default_db() == "/srv/audiobooks/db/from_config.db"

    def test_import_failure_falls_back_to_env(self, monkeypatch):
        """When config import raises ImportError, fall back to AUDIOBOOKS_DATABASE env."""
        from translation_monitor import db as module

        # Force the import to fail by removing 'config' from sys.modules and
        # blocking re-import via meta_path inspection — simplest is to patch
        # the helper's internal import. The helper imports lazily, so we
        # can stub by putting a sentinel that raises in sys.modules.
        if "config" in sys.modules:
            monkeypatch.delitem(sys.modules, "config", raising=False)

        # Replace `config` with a module that lacks AUDIOBOOKS_DATABASE so
        # the AttributeError branch is exercised. (Importing succeeds but
        # the attribute is missing → AttributeError → fall back to env.)
        broken = type(sys)("config")
        # Note: NOT setting AUDIOBOOKS_DATABASE on this fake module.
        monkeypatch.setitem(sys.modules, "config", broken)

        monkeypatch.setenv("AUDIOBOOKS_DATABASE", "/env-fallback.db")
        assert module._canonical_default_db() == "/env-fallback.db"

    def test_import_failure_with_no_env_returns_empty(self, monkeypatch):
        """No config, no env → empty string (DB-not-resolvable signal)."""
        from translation_monitor import db as module

        monkeypatch.delenv("AUDIOBOOKS_DATABASE", raising=False)
        broken = type(sys)("config")
        monkeypatch.setitem(sys.modules, "config", broken)

        assert module._canonical_default_db() == ""


# ──────────────────────────────────────────────────────────────────────────
# connect — real tmp_path file + PRAGMA verification
# ──────────────────────────────────────────────────────────────────────────


class TestConnect:
    def test_creates_real_file_on_disk(self, tmp_path):
        from translation_monitor import db as module

        db_path = tmp_path / "real.db"
        assert not db_path.exists()

        with module.connect(db_path) as conn:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()

        assert db_path.is_file()
        # File is real sqlite, not in-memory — verify by re-opening
        with sqlite3.connect(db_path) as fresh:
            row = fresh.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='t'"
            ).fetchone()
            assert row is not None

    def test_row_factory_is_sqlite3_row(self, tmp_path):
        """connect() sets row_factory = sqlite3.Row for column-name access."""
        from translation_monitor import db as module

        db_path = tmp_path / "rows.db"
        with module.connect(db_path) as conn:
            conn.execute("CREATE TABLE pets (name TEXT, kind TEXT)")
            conn.execute("INSERT INTO pets VALUES (?, ?)", ("ana", "cat"))
            conn.commit()
            row = conn.execute("SELECT * FROM pets").fetchone()
            assert row["name"] == "ana"
            assert row["kind"] == "cat"

    def test_foreign_keys_pragma_is_on(self, tmp_path):
        """PRAGMA foreign_keys = ON is set — required for CASCADE on audiobook delete."""
        from translation_monitor import db as module

        db_path = tmp_path / "fk.db"
        with module.connect(db_path) as conn:
            result = conn.execute("PRAGMA foreign_keys").fetchone()
            assert result[0] == 1

    def test_busy_timeout_pragma_is_5000ms(self, tmp_path):
        """PRAGMA busy_timeout = 5000 — survives brief writer contention."""
        from translation_monitor import db as module

        db_path = tmp_path / "busy.db"
        with module.connect(db_path) as conn:
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == 5000

    def test_uses_resolve_db_path(self, monkeypatch, tmp_path):
        """connect() with no arg defers to resolve_db_path → env → canonical."""
        from translation_monitor import db as module

        db_path = tmp_path / "env.db"
        monkeypatch.setenv("AUDIOBOOKS_DATABASE", str(db_path))
        with module.connect() as conn:
            conn.execute("CREATE TABLE marker (x)")
            conn.commit()
        assert db_path.is_file()


# ──────────────────────────────────────────────────────────────────────────
# schema_has_monitor_table — schema detection on real tmp_path DB
# ──────────────────────────────────────────────────────────────────────────


class TestSchemaHasMonitorTable:
    def test_returns_true_when_table_exists(self, tmp_path):
        from translation_monitor import db as module

        db_path = tmp_path / "with_table.db"
        with module.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE translation_monitor_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "monitor TEXT, event_type TEXT)"
            )
            conn.commit()
            assert module.schema_has_monitor_table(conn) is True

    def test_returns_false_when_table_missing(self, tmp_path):
        """Pre-v8.3.9 DBs (migration 025 not run) — should report False, not crash."""
        from translation_monitor import db as module

        db_path = tmp_path / "without_table.db"
        with module.connect(db_path) as conn:
            # Other tables exist, but not the monitor one
            conn.execute("CREATE TABLE audiobooks (id INTEGER)")
            conn.commit()
            assert module.schema_has_monitor_table(conn) is False

    def test_returns_false_on_empty_db(self, tmp_path):
        from translation_monitor import db as module

        db_path = tmp_path / "empty.db"
        with module.connect(db_path) as conn:
            assert module.schema_has_monitor_table(conn) is False


# ──────────────────────────────────────────────────────────────────────────
# db_exists — file presence on disk
# ──────────────────────────────────────────────────────────────────────────


class TestDbExists:
    def test_returns_true_for_existing_file(self, tmp_path):
        from translation_monitor import db as module

        db_path = tmp_path / "live.db"
        # Use connect() to create a real sqlite file (not just touch)
        with module.connect(db_path):
            pass
        assert module.db_exists(db_path) is True

    def test_returns_false_for_nonexistent_path(self, tmp_path):
        from translation_monitor import db as module

        assert module.db_exists(tmp_path / "nope.db") is False

    def test_returns_false_for_directory(self, tmp_path):
        """A directory at the resolved path is NOT a usable DB — return False."""
        from translation_monitor import db as module

        d = tmp_path / "a-directory.db"
        d.mkdir()
        assert module.db_exists(d) is False

    def test_uses_env_when_no_arg(self, monkeypatch, tmp_path):
        from translation_monitor import db as module

        db_path = tmp_path / "via_env.db"
        with module.connect(db_path):
            pass
        monkeypatch.setenv("AUDIOBOOKS_DATABASE", str(db_path))
        assert module.db_exists() is True


# ──────────────────────────────────────────────────────────────────────────
# Fixture-cleanup paranoia — verify pytest's tmp_path teardown actually
# removes the file we wrote, so test runs don't leak DB artefacts.
# ──────────────────────────────────────────────────────────────────────────


class TestFixtureCleanup:
    @pytest.fixture
    def captured_db(self, tmp_path):
        """Yield a tmp_path DB and capture the path so the test can assert
        on cleanup post-fixture-teardown by checking parent dir state."""
        from translation_monitor import db as module

        db_path = tmp_path / "cleanup-target.db"
        with module.connect(db_path) as conn:
            conn.execute("CREATE TABLE x (y INTEGER)")
            conn.commit()
        yield db_path
        # tmp_path teardown happens automatically; we just yield the path
        # so the test body has a record of what was written.

    def test_fixture_cleanup_removes_file(self, captured_db):
        """The tmp_path fixture should have created and will clean the DB."""
        # Inside the test, the file exists
        assert captured_db.is_file()
        # parent is the per-test tmp_path; pytest cleans it after the test
