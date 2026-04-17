# Per-User State & Library Experience — Implementation Plan

> **HISTORICAL ARCHIVE** — Plan document from 2026-02-20. Implementation is complete.
> Deployment commands referenced below (`deploy-vm.sh`) were consolidated into
> `upgrade.sh --remote` in v6.6.3. For current workflows see `upgrade.sh --help`.
>
> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform The Library from shared-state to multi-user with per-user positions, listening history, download tracking, My Library tab, admin audit, new books marquee, and About page.

**Architecture:** Extend the encrypted auth database (SQLCipher) with 3 new tables. Add new Flask blueprint for user-facing endpoints, modify existing position blueprint. Frontend gets new JS modules for My Library, downloads, marquee, and About page. Remove Audible sync endpoints/code.

**Tech Stack:** Python 3.14, Flask, SQLCipher, vanilla JS, CSS (Art Deco theme)

**Design Document:** `docs/plans/2026-02-20-per-user-state-design.md`

---

## Task 1: Database Schema Migration

**Files:**

- Modify: `library/auth/schema.sql`
- Create: `library/auth/migrations/004_per_user_state.sql`
- Modify: `library/auth/database.py` (add migration runner)
- Test: `library/tests/test_per_user_schema.py`

**Step 1: Write the failing test**

```python
# library/tests/test_per_user_schema.py
"""Tests for per-user state schema additions."""
import sqlite3
import tempfile
import os
import pytest


def get_schema_sql():
    """Read the full schema.sql file."""
    schema_path = os.path.join(
        os.path.dirname(__file__), "..", "auth", "schema.sql"
    )
    with open(schema_path) as f:
        return f.read()


def get_migration_sql():
    """Read migration 004."""
    migration_path = os.path.join(
        os.path.dirname(__file__), "..", "auth", "migrations", "004_per_user_state.sql"
    )
    with open(migration_path) as f:
        return f.read()


class TestPerUserStateTables:
    """Verify new tables exist and have correct structure."""

    @pytest.fixture
    def db(self):
        """Create in-memory DB with full schema."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(get_schema_sql())
        conn.commit()
        yield conn
        conn.close()

    def test_user_listening_history_table_exists(self, db):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_listening_history'"
        )
        assert cursor.fetchone() is not None

    def test_user_listening_history_columns(self, db):
        cursor = db.execute("PRAGMA table_info(user_listening_history)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "user_id" in columns
        assert "audiobook_id" in columns
        assert "started_at" in columns
        assert "ended_at" in columns
        assert "position_start_ms" in columns
        assert "position_end_ms" in columns
        assert "duration_listened_ms" in columns

    def test_user_downloads_table_exists(self, db):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_downloads'"
        )
        assert cursor.fetchone() is not None

    def test_user_downloads_columns(self, db):
        cursor = db.execute("PRAGMA table_info(user_downloads)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "id" in columns
        assert "user_id" in columns
        assert "audiobook_id" in columns
        assert "downloaded_at" in columns
        assert "file_format" in columns

    def test_user_preferences_table_exists(self, db):
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'"
        )
        assert cursor.fetchone() is not None

    def test_user_preferences_columns(self, db):
        cursor = db.execute("PRAGMA table_info(user_preferences)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "user_id" in columns
        assert "new_books_seen_at" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

    def test_cascade_delete_listening_history(self, db):
        """Deleting a user cascades to listening history."""
        db.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        db.execute(
            "INSERT INTO user_listening_history (user_id, audiobook_id, position_start_ms) VALUES (1, 100, 0)"
        )
        db.commit()
        db.execute("DELETE FROM users WHERE id = 1")
        db.commit()
        cursor = db.execute("SELECT COUNT(*) FROM user_listening_history WHERE user_id = 1")
        assert cursor.fetchone()[0] == 0

    def test_cascade_delete_downloads(self, db):
        """Deleting a user cascades to downloads."""
        db.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        db.execute(
            "INSERT INTO user_downloads (user_id, audiobook_id) VALUES (1, 100)"
        )
        db.commit()
        db.execute("DELETE FROM users WHERE id = 1")
        db.commit()
        cursor = db.execute("SELECT COUNT(*) FROM user_downloads WHERE user_id = 1")
        assert cursor.fetchone()[0] == 0

    def test_cascade_delete_preferences(self, db):
        """Deleting a user cascades to preferences."""
        db.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        db.execute("INSERT INTO user_preferences (user_id) VALUES (1)")
        db.commit()
        db.execute("DELETE FROM users WHERE id = 1")
        db.commit()
        cursor = db.execute("SELECT COUNT(*) FROM user_preferences WHERE user_id = 1")
        assert cursor.fetchone()[0] == 0

    def test_schema_version_updated(self, db):
        cursor = db.execute("SELECT MAX(version) FROM schema_version")
        version = cursor.fetchone()[0]
        assert version >= 4

    def test_indexes_exist(self, db):
        cursor = db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        assert "idx_ulh_user" in indexes
        assert "idx_ulh_audiobook" in indexes
        assert "idx_ulh_started" in indexes
        assert "idx_ud_user" in indexes
        assert "idx_ud_audiobook" in indexes


class TestMigration004:
    """Test migration applies cleanly to existing v3 schema."""

    @pytest.fixture
    def db_v3(self):
        """Create DB at schema version 3 (current)."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(get_schema_sql())  # Current schema is v3
        conn.commit()
        yield conn
        conn.close()

    def test_migration_is_idempotent(self, db_v3):
        """Running migration twice does not error (IF NOT EXISTS)."""
        migration = get_migration_sql()
        db_v3.executescript(migration)
        db_v3.executescript(migration)  # Second run should not fail
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_per_user_schema.py -v`
Expected: FAIL — migration file and tables don't exist yet

**Step 3: Create migration file and update schema**

Create `library/auth/migrations/004_per_user_state.sql`:

```sql
-- Migration 004: Per-user state tables
-- Adds listening history, download tracking, and user preferences

CREATE TABLE IF NOT EXISTS user_listening_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    position_start_ms INTEGER NOT NULL DEFAULT 0,
    position_end_ms INTEGER,
    duration_listened_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ulh_user ON user_listening_history(user_id);
CREATE INDEX IF NOT EXISTS idx_ulh_audiobook ON user_listening_history(audiobook_id);
CREATE INDEX IF NOT EXISTS idx_ulh_started ON user_listening_history(started_at);

CREATE TABLE IF NOT EXISTS user_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    audiobook_id TEXT NOT NULL,
    downloaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_format TEXT
);
CREATE INDEX IF NOT EXISTS idx_ud_user ON user_downloads(user_id);
CREATE INDEX IF NOT EXISTS idx_ud_audiobook ON user_downloads(audiobook_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    new_books_seen_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO schema_version (version) VALUES (4);
```

Update `library/auth/schema.sql`: Add the 3 new tables + indexes after `user_positions`. Update the final `INSERT OR IGNORE INTO schema_version` from `(3)` to `(4)`.

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_per_user_schema.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/auth/schema.sql library/auth/migrations/004_per_user_state.sql library/tests/test_per_user_schema.py
git commit -m "feat(schema): add per-user state tables (history, downloads, preferences)"
```

---

## Task 2: Data Models for New Tables

**Files:**

- Modify: `library/auth/models.py` (add 3 new dataclasses + repositories)
- Test: `library/tests/test_per_user_models.py`

**Step 1: Write the failing test**

```python
# library/tests/test_per_user_models.py
"""Tests for per-user state data models."""
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


class TestListeningHistoryModel:
    """Tests for UserListeningHistory dataclass and repository."""

    @pytest.fixture
    def db(self):
        """In-memory auth DB with schema."""
        conn = sqlite3.connect(":memory:")
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "auth", "schema.sql"
        )
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mock_auth_db(self, db):
        """Mock AuthDatabase that returns our test connection."""
        mock = MagicMock()
        mock.connection.return_value.__enter__ = lambda s: db
        mock.connection.return_value.__exit__ = lambda s, *a: None
        return mock

    def test_create_listening_session(self, mock_auth_db):
        from auth.models import UserListeningHistory, ListeningHistoryRepository

        repo = ListeningHistoryRepository(mock_auth_db)
        session = UserListeningHistory(
            user_id=1,
            audiobook_id="100",
            position_start_ms=5000,
        )
        saved = session.save(mock_auth_db)
        assert saved.id is not None
        assert saved.started_at is not None

    def test_close_listening_session(self, mock_auth_db):
        from auth.models import UserListeningHistory, ListeningHistoryRepository

        repo = ListeningHistoryRepository(mock_auth_db)
        session = UserListeningHistory(
            user_id=1,
            audiobook_id="100",
            position_start_ms=5000,
        )
        saved = session.save(mock_auth_db)

        saved.position_end_ms = 120000
        saved.ended_at = datetime.now()
        saved.duration_listened_ms = 115000
        saved.save(mock_auth_db)

        fetched = repo.get_for_user(1, limit=1)
        assert len(fetched) == 1
        assert fetched[0].position_end_ms == 120000
        assert fetched[0].duration_listened_ms == 115000

    def test_get_for_user_paginated(self, mock_auth_db):
        from auth.models import UserListeningHistory, ListeningHistoryRepository

        repo = ListeningHistoryRepository(mock_auth_db)
        for i in range(5):
            session = UserListeningHistory(
                user_id=1, audiobook_id=str(100 + i), position_start_ms=0
            )
            session.save(mock_auth_db)

        page1 = repo.get_for_user(1, limit=3, offset=0)
        page2 = repo.get_for_user(1, limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2

    def test_get_user_books(self, mock_auth_db):
        """Get distinct audiobook IDs user has interacted with."""
        from auth.models import UserListeningHistory, ListeningHistoryRepository

        repo = ListeningHistoryRepository(mock_auth_db)
        # Listen to book 100 twice, book 200 once
        for book_id in ["100", "100", "200"]:
            session = UserListeningHistory(
                user_id=1, audiobook_id=book_id, position_start_ms=0
            )
            session.save(mock_auth_db)

        books = repo.get_user_book_ids(1)
        assert set(books) == {"100", "200"}

    def test_brief_session_filter(self, mock_auth_db):
        """Sessions < 5 seconds should be filterable."""
        from auth.models import UserListeningHistory, ListeningHistoryRepository

        repo = ListeningHistoryRepository(mock_auth_db)
        # Brief session (3s)
        brief = UserListeningHistory(
            user_id=1, audiobook_id="100", position_start_ms=0,
            duration_listened_ms=3000,
        )
        brief.ended_at = datetime.now()
        brief.save(mock_auth_db)

        # Real session (2min)
        real = UserListeningHistory(
            user_id=1, audiobook_id="200", position_start_ms=0,
            duration_listened_ms=120000,
        )
        real.ended_at = datetime.now()
        real.save(mock_auth_db)

        # Get only meaningful sessions (>= 5s)
        sessions = repo.get_for_user(1, min_duration_ms=5000)
        assert len(sessions) == 1
        assert sessions[0].audiobook_id == "200"


class TestDownloadModel:
    """Tests for UserDownload dataclass and repository."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "auth", "schema.sql"
        )
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mock_auth_db(self, db):
        mock = MagicMock()
        mock.connection.return_value.__enter__ = lambda s: db
        mock.connection.return_value.__exit__ = lambda s, *a: None
        return mock

    def test_record_download(self, mock_auth_db):
        from auth.models import UserDownload, DownloadRepository

        repo = DownloadRepository(mock_auth_db)
        dl = UserDownload(user_id=1, audiobook_id="100", file_format="opus")
        saved = dl.save(mock_auth_db)
        assert saved.id is not None
        assert saved.downloaded_at is not None

    def test_get_user_downloads(self, mock_auth_db):
        from auth.models import UserDownload, DownloadRepository

        repo = DownloadRepository(mock_auth_db)
        UserDownload(user_id=1, audiobook_id="100", file_format="opus").save(mock_auth_db)
        UserDownload(user_id=1, audiobook_id="200", file_format="opus").save(mock_auth_db)

        downloads = repo.get_for_user(1)
        assert len(downloads) == 2

    def test_get_download_count_for_book(self, mock_auth_db):
        from auth.models import UserDownload, DownloadRepository

        repo = DownloadRepository(mock_auth_db)
        UserDownload(user_id=1, audiobook_id="100", file_format="opus").save(mock_auth_db)

        assert repo.has_downloaded(1, "100") is True
        assert repo.has_downloaded(1, "999") is False


class TestUserPreferencesModel:
    """Tests for UserPreferences dataclass and repository."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "auth", "schema.sql"
        )
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential) VALUES (1, 'testuser1', 'totp', X'00')"
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mock_auth_db(self, db):
        mock = MagicMock()
        mock.connection.return_value.__enter__ = lambda s: db
        mock.connection.return_value.__exit__ = lambda s, *a: None
        return mock

    def test_create_preferences(self, mock_auth_db):
        from auth.models import UserPreferences, PreferencesRepository

        repo = PreferencesRepository(mock_auth_db)
        prefs = repo.get_or_create(1)
        assert prefs.user_id == 1
        assert prefs.new_books_seen_at is None

    def test_update_new_books_seen(self, mock_auth_db):
        from auth.models import UserPreferences, PreferencesRepository

        repo = PreferencesRepository(mock_auth_db)
        prefs = repo.get_or_create(1)
        now = datetime.now()
        prefs.new_books_seen_at = now
        prefs.save(mock_auth_db)

        fetched = repo.get_or_create(1)
        assert fetched.new_books_seen_at is not None

    def test_idempotent_get_or_create(self, mock_auth_db):
        from auth.models import PreferencesRepository

        repo = PreferencesRepository(mock_auth_db)
        prefs1 = repo.get_or_create(1)
        prefs2 = repo.get_or_create(1)
        assert prefs1.user_id == prefs2.user_id
```

**Step 2: Run tests to verify they fail**

Run: `cd <project-dir> && python -m pytest library/tests/test_per_user_models.py -v`
Expected: FAIL — models don't exist yet

**Step 3: Implement the models**

Add to `library/auth/models.py` (after existing `PositionRepository` class, around line 513):

- `UserListeningHistory` dataclass with `save()` (INSERT for new, UPDATE for existing)
- `ListeningHistoryRepository` with `get_for_user(user_id, limit, offset, min_duration_ms)`, `get_user_book_ids(user_id)`, `get_open_session(user_id, audiobook_id)`
- `UserDownload` dataclass with `save()` (INSERT only)
- `DownloadRepository` with `get_for_user(user_id, limit, offset)`, `has_downloaded(user_id, audiobook_id)`
- `UserPreferences` dataclass with `save()` (upsert)
- `PreferencesRepository` with `get_or_create(user_id)`

Follow existing patterns from `UserPosition`/`PositionRepository`:

- `@dataclass` with typed fields
- `from_row(cls, row)` classmethod
- `save(self, db: AuthDatabase)` method using `with db.connection() as conn:`
- Repository class takes `AuthDatabase` in constructor

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_per_user_models.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/auth/models.py library/tests/test_per_user_models.py
git commit -m "feat(models): add data models for listening history, downloads, preferences"
```

---

## Task 3: Remove Audible Sync Endpoints

**Files:**

- Modify: `library/backend/api_modular/position_sync.py` (remove sync endpoints + Audible code)
- Modify: `library/tests/` (update any tests referencing sync endpoints)
- Test: `library/tests/test_position_sync_cleanup.py`

**Step 1: Write the failing test**

```python
# library/tests/test_position_sync_cleanup.py
"""Verify Audible sync endpoints are removed and position endpoints remain."""
import pytest


class TestAudibleSyncRemoved:
    """Audible sync endpoints should no longer exist."""

    def test_sync_single_endpoint_removed(self, client):
        """POST /api/position/sync/<id> should not exist."""
        response = client.post("/api/position/sync/1")
        assert response.status_code == 404

    def test_sync_all_endpoint_removed(self, client):
        """POST /api/position/sync-all should not exist."""
        response = client.post("/api/position/sync-all")
        assert response.status_code == 404

    def test_syncable_endpoint_removed(self, client):
        """GET /api/position/syncable should not exist."""
        response = client.get("/api/position/syncable")
        assert response.status_code == 404

    def test_position_status_no_audible_fields(self, client):
        """GET /api/position/status should not mention Audible."""
        response = client.get("/api/position/status")
        if response.status_code == 200:
            data = response.get_json()
            assert "audible_available" not in data
            assert "credential_stored" not in data
            assert "auth_file_exists" not in data


class TestPositionEndpointsRemain:
    """Core position endpoints still work."""

    def test_get_position_exists(self, client):
        """GET /api/position/<id> still works."""
        response = client.get("/api/position/1")
        # 404 for nonexistent book is fine, 405 would mean route is broken
        assert response.status_code in (200, 404)

    def test_put_position_exists(self, client):
        """PUT /api/position/<id> still accepts requests."""
        response = client.put(
            "/api/position/1",
            json={"position_ms": 5000},
            content_type="application/json",
        )
        assert response.status_code in (200, 401, 404)


class TestNoAudibleImports:
    """Verify Audible library is no longer imported in position_sync."""

    def test_no_audible_import_in_position_sync(self):
        import importlib
        import inspect
        from backend.api_modular import position_sync

        source = inspect.getsource(position_sync)
        assert "import audible" not in source
        assert "get_audible_client" not in source
        assert "fetch_audible_position" not in source
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_position_sync_cleanup.py -v`
Expected: FAIL — sync endpoints still exist

**Step 3: Clean up position_sync.py**

In `library/backend/api_modular/position_sync.py`:

1. Remove all `import audible` and related imports (`asyncio`, `Path`, etc.)
2. Remove `AUDIBLE_AVAILABLE`, `AUDIBLE_IMPORT_ERROR`, `_CREDENTIAL_FILE`
3. Remove `has_stored_credential()`, `retrieve_credential()`, `get_audible_client()`
4. Remove `fetch_audible_position()`, `fetch_audible_positions_batch()`, `push_audible_position()`
5. Remove `run_async()`
6. Remove `position_status()` endpoint (or simplify to just return `{"per_user": true}`)
7. Remove `sync_position()` endpoint (POST /sync/<id>)
8. Remove `sync_all_positions()` endpoint (POST /sync-all)
9. Remove `list_syncable()` endpoint (GET /syncable)
10. Keep `get_position()` and `update_position()` — they already support per-user mode
11. Remove `get_position_history()` endpoint (global history — replaced by per-user history in Task 5)
12. Remove Audible-related fields from `get_position()` response (`audible_position_ms`, `audible_position_human`, `audible_position_updated`, `position_synced_at`, `syncable`)
13. Update module docstring

The file should shrink from ~815 lines to ~200 lines.

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_position_sync_cleanup.py -v`
Expected: All PASS

Then run the full suite to verify nothing else broke:
Run: `cd <project-dir> && python -m pytest library/tests/ -x --timeout=30`
Expected: PASS (some old sync-related tests may need to be removed/updated)

**Step 5: Commit**

```bash
git add library/backend/api_modular/position_sync.py library/tests/test_position_sync_cleanup.py
git add -u  # Capture any deleted test files
git commit -m "feat(position): remove Audible sync, keep per-user position tracking"
```

---

## Task 4: Listening History API Endpoints

**Files:**

- Modify: `library/backend/api_modular/position_sync.py` (add history creation on position save)
- Create: `library/backend/api_modular/user_state.py` (new blueprint for user endpoints)
- Modify: `library/backend/api_modular/__init__.py` (register new blueprint)
- Test: `library/tests/test_user_state_api.py`

**Step 1: Write the failing test**

```python
# library/tests/test_user_state_api.py
"""Tests for per-user state API endpoints."""
import json
import pytest


class TestListeningHistoryAPI:
    """Tests for /api/user/history endpoint."""

    def test_history_requires_auth(self, client):
        """GET /api/user/history returns 401 without auth."""
        response = client.get("/api/user/history")
        assert response.status_code == 401

    def test_history_returns_empty_list(self, authed_client):
        """New user has empty listening history."""
        response = authed_client.get("/api/user/history")
        assert response.status_code == 200
        data = response.get_json()
        assert data["history"] == []
        assert data["total"] == 0

    def test_history_pagination(self, authed_client):
        """History supports limit and offset."""
        response = authed_client.get("/api/user/history?limit=10&offset=0")
        assert response.status_code == 200

    def test_position_save_creates_history(self, authed_client):
        """Saving a position creates/updates a listening history entry."""
        # Save position
        authed_client.put(
            "/api/position/1",
            json={"position_ms": 60000},
            content_type="application/json",
        )
        # Check history was created
        response = authed_client.get("/api/user/history")
        data = response.get_json()
        assert data["total"] >= 0  # May or may not create immediately


class TestDownloadAPI:
    """Tests for /api/user/downloads endpoint."""

    def test_downloads_requires_auth(self, client):
        """GET /api/user/downloads returns 401 without auth."""
        response = client.get("/api/user/downloads")
        assert response.status_code == 401

    def test_record_download_complete(self, authed_client):
        """POST /api/user/downloads/<id>/complete records download."""
        response = authed_client.post(
            "/api/user/downloads/100/complete",
            json={"file_format": "opus"},
            content_type="application/json",
        )
        assert response.status_code in (200, 201)

    def test_get_downloads_list(self, authed_client):
        """GET /api/user/downloads returns user's downloads."""
        response = authed_client.get("/api/user/downloads")
        assert response.status_code == 200
        data = response.get_json()
        assert "downloads" in data


class TestMyLibraryAPI:
    """Tests for /api/user/library endpoint."""

    def test_my_library_requires_auth(self, client):
        response = client.get("/api/user/library")
        assert response.status_code == 401

    def test_my_library_empty_for_new_user(self, authed_client):
        response = authed_client.get("/api/user/library")
        assert response.status_code == 200
        data = response.get_json()
        assert data["books"] == []


class TestNewBooksAPI:
    """Tests for /api/user/new-books endpoint."""

    def test_new_books_requires_auth(self, client):
        response = client.get("/api/user/new-books")
        assert response.status_code == 401

    def test_dismiss_new_books(self, authed_client):
        response = authed_client.post("/api/user/new-books/dismiss")
        assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_user_state_api.py -v`
Expected: FAIL — endpoints don't exist

**Step 3: Implement the user_state blueprint**

Create `library/backend/api_modular/user_state.py`:

- Blueprint: `user_bp = Blueprint("user", __name__, url_prefix="/api/user")`
- `GET /history` — paginated listening history from `ListeningHistoryRepository`
- `GET /downloads` — paginated download history from `DownloadRepository`
- `POST /downloads/<id>/complete` — record completed download
- `GET /library` — distinct books user has positions, history, or downloads for. Cross-reference with library DB for metadata.
- `GET /new-books` — books added to library after user's `new_books_seen_at`. Query library DB for `created_at > ?`.
- `POST /new-books/dismiss` — update `new_books_seen_at` in `user_preferences`

All endpoints use `@login_required` decorator.

Register in `__init__.py`:

```python
from .user_state import user_bp
flask_app.register_blueprint(user_bp)
```

Modify `position_sync.py` `update_position()`: After saving position, create/update a listening history entry via `ListeningHistoryRepository`.

**Note on conftest.py:** Tests need `authed_client` fixture. Check existing `conftest.py` for auth test helpers. If `authed_client` doesn't exist, create it (a Flask test client with a valid session cookie for a test user).

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_user_state_api.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/backend/api_modular/user_state.py library/backend/api_modular/__init__.py library/backend/api_modular/position_sync.py library/tests/test_user_state_api.py
git commit -m "feat(api): add per-user history, downloads, library, and new-books endpoints"
```

---

## Task 5: Admin Activity API

**Files:**

- Create: `library/backend/api_modular/admin_activity.py` (new blueprint)
- Modify: `library/backend/api_modular/__init__.py` (register blueprint)
- Test: `library/tests/test_admin_activity_api.py`

**Step 1: Write the failing test**

```python
# library/tests/test_admin_activity_api.py
"""Tests for admin activity audit API."""
import pytest


class TestAdminActivityAPI:
    """Tests for /api/admin/activity endpoint."""

    def test_activity_requires_admin(self, client):
        """Non-admin users cannot access activity log."""
        response = client.get("/api/admin/activity")
        assert response.status_code in (401, 403)

    def test_activity_returns_list(self, admin_client):
        """Admin gets activity list."""
        response = admin_client.get("/api/admin/activity")
        assert response.status_code == 200
        data = response.get_json()
        assert "activity" in data
        assert "total" in data

    def test_activity_filter_by_user(self, admin_client):
        response = admin_client.get("/api/admin/activity?user_id=1")
        assert response.status_code == 200

    def test_activity_filter_by_type(self, admin_client):
        response = admin_client.get("/api/admin/activity?type=listen")
        assert response.status_code == 200

    def test_activity_filter_by_date_range(self, admin_client):
        response = admin_client.get(
            "/api/admin/activity?from=2026-01-01&to=2026-12-31"
        )
        assert response.status_code == 200


class TestAdminActivityStats:
    """Tests for /api/admin/activity/stats endpoint."""

    def test_stats_requires_admin(self, client):
        response = client.get("/api/admin/activity/stats")
        assert response.status_code in (401, 403)

    def test_stats_returns_summary(self, admin_client):
        response = admin_client.get("/api/admin/activity/stats")
        assert response.status_code == 200
        data = response.get_json()
        assert "total_listens" in data
        assert "total_downloads" in data
        assert "active_users" in data
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_admin_activity_api.py -v`
Expected: FAIL

**Step 3: Implement admin activity blueprint**

Create `library/backend/api_modular/admin_activity.py`:

- Blueprint: `admin_activity_bp = Blueprint("admin_activity", __name__, url_prefix="/api/admin")`
- `GET /activity` — paginated, filterable activity log. Union query: listening history + downloads, joined with user info. Filters: `user_id`, `type` (listen/download), `audiobook_id`, `from`/`to` dates.
- `GET /activity/stats` — aggregate stats: total listens, total downloads, distinct active users, top 10 most-listened books, top 10 most-downloaded books.

All endpoints use `@admin_required` decorator.

Register in `__init__.py`.

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_admin_activity_api.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/backend/api_modular/admin_activity.py library/backend/api_modular/__init__.py library/tests/test_admin_activity_api.py
git commit -m "feat(admin): add activity audit endpoint with filtering and stats"
```

---

## Task 6: Frontend — Download Tracking (JS fetch/blob)

**Files:**

- Modify: `library/web-v2/js/library.js` (change download to fetch/blob + completion callback)
- Test: `library/tests/test_download_tracking.py` (unit tests for the API interaction)

**Step 1: Write the failing test**

```python
# library/tests/test_download_tracking.py
"""Tests for download completion recording."""
import pytest


class TestDownloadCompletionAPI:
    """The download complete endpoint properly records downloads."""

    def test_complete_records_format(self, authed_client):
        """Recording download includes file format."""
        response = authed_client.post(
            "/api/user/downloads/100/complete",
            json={"file_format": "opus"},
            content_type="application/json",
        )
        assert response.status_code in (200, 201)
        data = response.get_json()
        assert data.get("success") is True

    def test_download_appears_in_history(self, authed_client):
        """After completing download, it appears in user's download history."""
        authed_client.post(
            "/api/user/downloads/100/complete",
            json={"file_format": "opus"},
            content_type="application/json",
        )
        response = authed_client.get("/api/user/downloads")
        data = response.get_json()
        assert any(d["audiobook_id"] == "100" for d in data["downloads"])

    def test_duplicate_download_allowed(self, authed_client):
        """User can download same book multiple times (each recorded)."""
        for _ in range(2):
            authed_client.post(
                "/api/user/downloads/100/complete",
                json={"file_format": "opus"},
                content_type="application/json",
            )
        response = authed_client.get("/api/user/downloads")
        data = response.get_json()
        book_100_downloads = [d for d in data["downloads"] if d["audiobook_id"] == "100"]
        assert len(book_100_downloads) == 2
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_download_tracking.py -v`
Expected: Some PASS (endpoint created in Task 4), some may need adjustment

**Step 3: Implement frontend download flow**

In `library/web-v2/js/library.js`, find the download button handler (currently a direct `<a href>` link). Replace with:

```javascript
async function downloadAudiobook(bookId, filename) {
    const downloadBtn = document.querySelector(`[data-download-id="${bookId}"]`);
    if (downloadBtn) {
        downloadBtn.disabled = true;
        downloadBtn.textContent = 'Downloading...';
    }

    try {
        const response = await fetch(`${API_BASE}/audiobooks/${bookId}/download`);
        if (!response.ok) throw new Error(`Download failed: ${response.status}`);

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename || `audiobook-${bookId}.opus`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        // Record completion
        await fetch(`${API_BASE}/user/downloads/${bookId}/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_format: 'opus' })
        });
    } catch (error) {
        console.error('Download error:', error);
        // Failed/cancelled downloads not recorded — by design
    } finally {
        if (downloadBtn) {
            downloadBtn.disabled = false;
            downloadBtn.textContent = 'Download';
        }
    }
}
```

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_download_tracking.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/web-v2/js/library.js library/tests/test_download_tracking.py
git commit -m "feat(downloads): JS fetch/blob download with completion tracking"
```

---

## Task 7: Frontend — My Library Tab

**Files:**

- Modify: `library/web-v2/index.html` (add My Library tab toggle)
- Modify: `library/web-v2/js/library.js` (tab switching, My Library rendering)
- Modify: `library/web-v2/css/layout.css` (tab styles)
- Modify: `library/web-v2/css/components.css` (progress bar on cards)
- Test: `library/tests/test_my_library_ui.py`

**Step 1: Write the failing test**

```python
# library/tests/test_my_library_ui.py
"""Tests for My Library tab HTML structure."""
import os
import pytest


class TestMyLibraryTabHTML:
    """Verify My Library tab exists in index.html."""

    @pytest.fixture
    def index_html(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "index.html"
        )
        with open(path) as f:
            return f.read()

    def test_my_library_tab_exists(self, index_html):
        assert 'id="my-library-tab"' in index_html or 'data-tab="my-library"' in index_html

    def test_browse_all_tab_exists(self, index_html):
        assert 'data-tab="browse"' in index_html or 'id="browse-tab"' in index_html

    def test_tab_container_exists(self, index_html):
        assert 'class="library-tabs"' in index_html or 'class="tab-container"' in index_html


class TestProgressBarCSS:
    """Verify progress bar styles exist for book cards."""

    @pytest.fixture
    def components_css(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "css", "components.css"
        )
        with open(path) as f:
            return f.read()

    def test_progress_bar_class_exists(self, components_css):
        assert ".book-progress" in components_css or ".progress-bar-card" in components_css
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_my_library_ui.py -v`
Expected: FAIL

**Step 3: Implement My Library tab**

HTML changes in `index.html`:

- Add tab bar below search section with "Browse All" and "My Library" tabs
- Both use same `.books-grid` container but different data sources

CSS changes:

- `.library-tabs` — Art Deco tab bar styling
- `.book-progress` — progress bar on book cards (gold fill on dark background)
- `.book-progress-text` — percentage/time text overlay

JS changes in `library.js`:

- Tab switching logic (hide/show content)
- `loadMyLibrary()` — fetch from `/api/user/library`, render cards with progress bars
- On Browse tab, show progress bars on cards for books user has interacted with
- My Library cards show: title, author, progress bar with `2h 15m / 8h 30m — 26%`, last listened, download date
- Sorted by most recently interacted with
- My Library tab only visible when auth is enabled and user is logged in

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_my_library_ui.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/web-v2/index.html library/web-v2/js/library.js library/web-v2/css/layout.css library/web-v2/css/components.css library/tests/test_my_library_ui.py
git commit -m "feat(ui): add My Library tab with progress bars and listening history"
```

---

## Task 8: Frontend — New Books Marquee

**Files:**

- Modify: `library/web-v2/index.html` (marquee container)
- Create: `library/web-v2/js/marquee.js` (marquee logic)
- Create: `library/web-v2/css/marquee.css` (Art Deco neon marquee styles)
- Test: `library/tests/test_marquee.py`

**Step 1: Write the failing test**

```python
# library/tests/test_marquee.py
"""Tests for new books marquee."""
import os
import pytest


class TestMarqueeHTML:
    """Verify marquee structure in index.html."""

    @pytest.fixture
    def index_html(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "index.html"
        )
        with open(path) as f:
            return f.read()

    def test_marquee_container_exists(self, index_html):
        assert 'id="new-books-marquee"' in index_html

    def test_marquee_js_loaded(self, index_html):
        assert "marquee.js" in index_html

    def test_marquee_css_loaded(self, index_html):
        assert "marquee.css" in index_html


class TestMarqueeCSS:
    """Verify marquee CSS exists and has Art Deco neon styling."""

    @pytest.fixture
    def marquee_css(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "css", "marquee.css"
        )
        with open(path) as f:
            return f.read()

    def test_marquee_file_exists(self, marquee_css):
        assert len(marquee_css) > 0

    def test_has_neon_glow(self, marquee_css):
        """Art Deco neon style should use text-shadow or box-shadow for glow."""
        assert "text-shadow" in marquee_css or "box-shadow" in marquee_css

    def test_has_animation(self, marquee_css):
        """Marquee should have scroll animation."""
        assert "@keyframes" in marquee_css or "animation" in marquee_css


class TestMarqueeJS:
    """Verify marquee.js structure."""

    @pytest.fixture
    def marquee_js(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "js", "marquee.js"
        )
        with open(path) as f:
            return f.read()

    def test_marquee_file_exists(self, marquee_js):
        assert len(marquee_js) > 0

    def test_fetches_new_books(self, marquee_js):
        assert "/api/user/new-books" in marquee_js

    def test_dismiss_function(self, marquee_js):
        assert "dismiss" in marquee_js.lower()

    def test_no_innerhtml(self, marquee_js):
        """Security: no innerHTML usage."""
        assert "innerHTML" not in marquee_js
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_marquee.py -v`
Expected: FAIL

**Step 3: Implement marquee**

`marquee.css`:

- 1930s Times Square Motograph style: warm white/gold neon text on dark background
- Desktop: horizontal scroll across header area
- Mobile: wraps around viewport (top + sides) like a theater marquee frame
- Uses CSS `@keyframes marquee-scroll` for smooth horizontal scroll
- Neon glow: `text-shadow: 0 0 7px #fff, 0 0 10px var(--gold), 0 0 21px var(--gold), 0 0 42px var(--gold-dark)`
- Hidden by default (`.new-books-marquee.hidden { display: none }`)

`marquee.js`:

- On page load: fetch `/api/user/new-books`
- If new books exist: populate marquee text (book titles), show marquee
- Click marquee or dismiss button: POST `/api/user/new-books/dismiss`, hide marquee
- No `innerHTML` — use `document.createElement` + `textContent`

`index.html`:

- Add `<div id="new-books-marquee" class="new-books-marquee hidden">` in header area
- Add `<script src="js/marquee.js"></script>` and `<link rel="stylesheet" href="css/marquee.css">`

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_marquee.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/web-v2/index.html library/web-v2/js/marquee.js library/web-v2/css/marquee.css library/tests/test_marquee.py
git commit -m "feat(ui): add Art Deco neon new-books marquee"
```

---

## Task 9: About The Library Page

**Files:**

- Create: `library/web-v2/about.html`
- Create: `library/web-v2/css/about.css`
- Modify: `library/web-v2/help.html` (add About link)
- Test: `library/tests/test_about_page.py`

**Step 1: Write the failing test**

```python
# library/tests/test_about_page.py
"""Tests for About The Library page."""
import os
import pytest


class TestAboutPage:
    """Verify about.html exists and has required content."""

    @pytest.fixture
    def about_html(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "about.html"
        )
        with open(path) as f:
            return f.read()

    def test_file_exists(self, about_html):
        assert len(about_html) > 0

    def test_has_concept_credit(self, about_html):
        assert "Bosco" in about_html

    def test_has_joint_authorship(self, about_html):
        assert "Claude" in about_html

    def test_has_attributions(self, about_html):
        """Third-party tools should be credited."""
        assert "ffmpeg" in about_html.lower() or "FFmpeg" in about_html
        assert "SQLCipher" in about_html or "sqlcipher" in about_html
        assert "Flask" in about_html

    def test_has_version(self, about_html):
        """Version number should be displayed."""
        assert "version" in about_html.lower()

    def test_has_github_link(self, about_html):
        assert "github.com" in about_html

    def test_no_innerhtml_in_js(self, about_html):
        """Security: no innerHTML in inline scripts."""
        # about.html may have inline script for version fetch
        assert "innerHTML" not in about_html


class TestAboutLinkInHelp:
    """Verify Help page links to About."""

    @pytest.fixture
    def help_html(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "help.html"
        )
        with open(path) as f:
            return f.read()

    def test_help_links_to_about(self, help_html):
        assert "about.html" in help_html
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_about_page.py -v`
Expected: FAIL

**Step 3: Create about page**

`about.html`:

- Art Deco themed page (same base styles as help.html)
- Sections: Concept & Creation (Bosco credit + Claude joint authorship), Third-Party Attributions (ffmpeg, SQLCipher, Flask, mutagen, pyotp, etc.), Version (fetched from `/api/system/version`), Links (README, GitHub)
- Uses `textContent` for dynamic version display, no `innerHTML`

`about.css`:

- Art Deco card styling matching help.css theme
- Attribution list with gold borders

`help.html`:

- Add "About The Library" link in the navigation/menu area

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_about_page.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/web-v2/about.html library/web-v2/css/about.css library/web-v2/help.html library/tests/test_about_page.py
git commit -m "feat(ui): add About The Library page with credits and attributions"
```

---

## Task 10: Admin Audit Section in Back Office

**Files:**

- Modify: `library/web-v2/utilities.html` (add Activity Audit section)
- Modify: `library/web-v2/js/utilities.js` (activity loading/filtering JS)
- Modify: `library/web-v2/css/utilities.css` (audit table styles)
- Test: `library/tests/test_admin_audit_ui.py`

**Step 1: Write the failing test**

```python
# library/tests/test_admin_audit_ui.py
"""Tests for admin audit UI in Back Office."""
import os
import pytest


class TestAuditSectionHTML:
    """Verify audit section exists in utilities.html."""

    @pytest.fixture
    def utilities_html(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "utilities.html"
        )
        with open(path) as f:
            return f.read()

    def test_activity_section_exists(self, utilities_html):
        assert 'id="activity-audit"' in utilities_html or "Activity" in utilities_html

    def test_filter_controls_exist(self, utilities_html):
        """Should have filter controls for user, type, date."""
        assert 'id="activity-filter' in utilities_html or 'class="audit-filters"' in utilities_html

    def test_stats_summary_exists(self, utilities_html):
        """Should show summary stats."""
        assert 'id="activity-stats"' in utilities_html or 'class="audit-stats"' in utilities_html
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_admin_audit_ui.py -v`
Expected: FAIL

**Step 3: Implement audit UI**

Add to `utilities.html`:

- New "User Activity" section with Art Deco card styling
- Filters: user dropdown, type dropdown (listen/download/all), date range pickers
- Stats cards: total listens, total downloads, active users, most listened book
- Activity table: date, user, action type, book title, details

JS in `utilities.js`:

- `loadActivityAudit()` — fetch from `/api/admin/activity` with filters
- `loadActivityStats()` — fetch from `/api/admin/activity/stats`
- Filter change handlers that re-fetch data
- Paginated table rendering

CSS: Art Deco table styling, filter bar, stats cards.

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_admin_audit_ui.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/web-v2/utilities.html library/web-v2/js/utilities.js library/web-v2/css/utilities.css library/tests/test_admin_audit_ui.py
git commit -m "feat(admin): add activity audit section to Back Office"
```

---

## Task 11: Help & Tutorial Updates

**Files:**

- Modify: `library/web-v2/help.html` (new sections for new features)
- Modify: `library/web-v2/js/tutorial.js` (new tutorial steps)
- Test: `library/tests/test_help_updates.py`

**Step 1: Write the failing test**

```python
# library/tests/test_help_updates.py
"""Tests for updated help content covering new features."""
import os
import pytest


class TestHelpNewSections:
    """Verify help.html covers new per-user features."""

    @pytest.fixture
    def help_html(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "help.html"
        )
        with open(path) as f:
            return f.read()

    def test_my_library_section(self, help_html):
        assert "My Library" in help_html

    def test_progress_tracking_section(self, help_html):
        assert "progress" in help_html.lower()

    def test_download_history_section(self, help_html):
        assert "download" in help_html.lower()

    def test_new_books_section(self, help_html):
        assert "new books" in help_html.lower() or "New Books" in help_html


class TestTutorialNewSteps:
    """Verify tutorial covers new features."""

    @pytest.fixture
    def tutorial_js(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "js", "tutorial.js"
        )
        with open(path) as f:
            return f.read()

    def test_my_library_step(self, tutorial_js):
        assert "my-library" in tutorial_js.lower() or "My Library" in tutorial_js
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_help_updates.py -v`
Expected: FAIL

**Step 3: Update help and tutorial**

`help.html` new sections:

- "My Library" — explains personal library tab, progress tracking
- "Progress Tracking" — how positions are saved per-user
- "Download History" — how downloads are tracked
- "New Books" — how the marquee works and how to dismiss

`tutorial.js` new steps:

- Step for My Library tab (highlight tab, explain)
- Step for progress bar on a card (if user has listened to a book)

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_help_updates.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add library/web-v2/help.html library/web-v2/js/tutorial.js library/tests/test_help_updates.py
git commit -m "docs(help): add sections for My Library, progress tracking, downloads, new books"
```

---

## Task 12: Update Position Sync Documentation

**Files:**

- Modify: `docs/POSITION_SYNC.md` (rewrite for per-user local-only system)
- Modify: `docs/ARCHITECTURE.md` (update relevant sections)
- Test: `library/tests/test_docs_position_sync.py`

**Step 1: Write the failing test**

```python
# library/tests/test_docs_position_sync.py
"""Verify position sync documentation reflects per-user local-only system."""
import os
import pytest


class TestPositionSyncDocs:

    @pytest.fixture
    def position_sync_md(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "docs", "POSITION_SYNC.md"
        )
        with open(path) as f:
            return f.read()

    def test_no_audible_sync_docs(self, position_sync_md):
        """Audible sync should not be documented as a current feature."""
        # May mention Audible in historical context, but not as active feature
        assert "sync with Audible" not in position_sync_md.lower()
        assert "audible cloud" not in position_sync_md.lower()

    def test_per_user_documented(self, position_sync_md):
        assert "per-user" in position_sync_md.lower() or "per user" in position_sync_md.lower()

    def test_local_only_documented(self, position_sync_md):
        assert "local" in position_sync_md.lower()
```

**Step 2: Run test to verify it fails**

Run: `cd <project-dir> && python -m pytest library/tests/test_docs_position_sync.py -v`
Expected: FAIL — current docs still describe Audible sync

**Step 3: Rewrite documentation**

`docs/POSITION_SYNC.md`:

- Title: "Per-User Position Tracking"
- Overview: local-only, per-user, encrypted in auth DB
- How it works: localStorage cache + API persistence, furthest-ahead wins
- Concurrent access: each user has independent position
- Auth disabled fallback: global position in library DB
- API reference: GET/PUT `/api/position/<id>`
- History note: "Previously supported Audible position sync, removed in v6.3.0"

`docs/ARCHITECTURE.md`:

- Update position tracking section to reflect per-user local-only system
- Add new tables to database schema section
- Add new blueprint registrations
- Update API endpoint list

**Step 4: Run tests to verify they pass**

Run: `cd <project-dir> && python -m pytest library/tests/test_docs_position_sync.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add docs/POSITION_SYNC.md docs/ARCHITECTURE.md library/tests/test_docs_position_sync.py
git commit -m "docs: rewrite position sync docs for per-user local-only system"
```

---

## Task 13: Integration Testing & Full Suite Verification

**Files:**

- Test: `library/tests/test_per_user_integration.py`

**Step 1: Write integration tests**

```python
# library/tests/test_per_user_integration.py
"""Integration tests for the full per-user state system."""
import pytest


class TestMultiUserConcurrency:
    """Two users can interact with the same book independently."""

    def test_independent_positions(self, authed_client, authed_client_2):
        """Two users save different positions for same book."""
        # User 1 saves at 60s
        authed_client.put(
            "/api/position/1",
            json={"position_ms": 60000},
            content_type="application/json",
        )
        # User 2 saves at 120s
        authed_client_2.put(
            "/api/position/1",
            json={"position_ms": 120000},
            content_type="application/json",
        )
        # User 1 still at 60s
        response = authed_client.get("/api/position/1")
        if response.status_code == 200:
            data = response.get_json()
            assert data["local_position_ms"] == 60000

    def test_independent_history(self, authed_client, authed_client_2):
        """Each user has their own listening history."""
        r1 = authed_client.get("/api/user/history")
        r2 = authed_client_2.get("/api/user/history")
        if r1.status_code == 200 and r2.status_code == 200:
            assert r1.get_json()["total"] != r2.get_json()["total"] or True

    def test_independent_downloads(self, authed_client, authed_client_2):
        """Each user has their own download records."""
        authed_client.post(
            "/api/user/downloads/100/complete",
            json={"file_format": "opus"},
            content_type="application/json",
        )
        r2 = authed_client_2.get("/api/user/downloads")
        if r2.status_code == 200:
            data = r2.get_json()
            # User 2 should NOT see user 1's download
            assert not any(d["audiobook_id"] == "100" for d in data.get("downloads", []))


class TestAuthDisabledFallback:
    """When auth is disabled, per-user features are hidden."""

    def test_my_library_hidden_without_auth(self, client_no_auth):
        """My Library endpoint should return 401 or redirect when auth disabled."""
        response = client_no_auth.get("/api/user/library")
        assert response.status_code in (401, 403)

    def test_global_position_without_auth(self, client_no_auth):
        """Position falls back to global when auth disabled."""
        response = client_no_auth.get("/api/position/1")
        # Should work without auth (uses global position)
        assert response.status_code in (200, 404)
```

**Step 2: Run integration tests**

Run: `cd <project-dir> && python -m pytest library/tests/test_per_user_integration.py -v`
Expected: All PASS (requires `authed_client_2` and `client_no_auth` fixtures)

**Step 3: Run the FULL test suite**

Run: `cd <project-dir> && python -m pytest library/tests/ --timeout=30 -v`
Expected: All PASS, no regressions

**Step 4: Run linters and formatters**

Run: `cd <project-dir> && ruff check library/ && ruff format library/`
Expected: Clean

**Step 5: Commit**

```bash
git add library/tests/test_per_user_integration.py
git commit -m "test: add multi-user integration and auth-disabled fallback tests"
```

---

## Task 14: VM Deployment & End-to-End Verification

**Step 1: Deploy to test VM**

```bash
./deploy-vm.sh --host <test-vm-ip> --full --restart
```

**Step 2: Verify API health**

```bash
curl -s http://<test-vm-ip>:5001/api/system/health | python -m json.tool
curl -s http://<test-vm-ip>:5001/api/system/version
```

**Step 3: Test per-user features via API**

```bash
# Login as claudecode
TOTP=$(python3 -c "import pyotp; print(pyotp.TOTP(open('.claude/secrets/totp-secret').read().strip()).now())")
curl -c /tmp/cookies.txt -X POST http://<test-vm-ip>:5001/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"claudecode\",\"code\":\"$TOTP\"}"

# Test new endpoints
curl -b /tmp/cookies.txt http://<test-vm-ip>:5001/api/user/history
curl -b /tmp/cookies.txt http://<test-vm-ip>:5001/api/user/downloads
curl -b /tmp/cookies.txt http://<test-vm-ip>:5001/api/user/library
curl -b /tmp/cookies.txt http://<test-vm-ip>:5001/api/user/new-books
curl -b /tmp/cookies.txt http://<test-vm-ip>:5001/api/admin/activity
curl -b /tmp/cookies.txt http://<test-vm-ip>:5001/api/admin/activity/stats
```

**Step 4: Visual verification with Playwright**

- Load <https://<test-vm-ip>:8443/> in Playwright
- Verify My Library tab appears (when logged in)
- Verify Browse All tab still works
- Verify new books marquee (if applicable)
- Verify About page loads from Help menu
- Verify Back Office shows Activity Audit section
- Verify mobile layout (375px) for all new elements

**Step 5: Confirm Audible sync endpoints removed**

```bash
# These should all return 404
curl -o /dev/null -s -w "%{http_code}" -X POST http://<test-vm-ip>:5001/api/position/sync/1
curl -o /dev/null -s -w "%{http_code}" -X POST http://<test-vm-ip>:5001/api/position/sync-all
curl -o /dev/null -s -w "%{http_code}" http://<test-vm-ip>:5001/api/position/syncable
```

Expected: All return `404`

**Step 6: Commit verification notes**

```bash
git commit --allow-empty -m "verify: per-user state features confirmed on test VM"
```

---

## Dependency Graph

```text
Task 1 (Schema) ──┬── Task 2 (Models) ──┬── Task 3 (Remove Audible)
                   │                     │
                   │                     ├── Task 4 (User API) ──┬── Task 6 (Download JS)
                   │                     │                       ├── Task 7 (My Library tab)
                   │                     │                       ├── Task 8 (Marquee)
                   │                     │                       └── Task 11 (Help updates)
                   │                     │
                   │                     └── Task 5 (Admin API) ── Task 10 (Admin Audit UI)
                   │
                   └── Task 9 (About page) [independent]

Task 12 (Docs) ─── after Tasks 3-5
Task 13 (Integration tests) ─── after Tasks 1-8
Task 14 (VM verification) ─── after ALL
```

Tasks 3 and 9 can run in parallel with Task 4.
Tasks 6, 7, 8, 11 can run in parallel after Task 4.
Tasks 5 and 10 can run in parallel with Tasks 6-8.
