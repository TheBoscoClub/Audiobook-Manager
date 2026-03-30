# Multi-Session Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow admin-controlled multi-session logins so users can stay logged in across multiple devices/browsers simultaneously.

**Architecture:** Add a tri-state `multi_session` column to `users` table (`'default'`/`'yes'`/`'no'`), a new `system_settings` key-value table for the global default, a resolution function that checks per-user then global, and a conditional skip of the session-delete in `Session.create_for_user()`. Admin UI gets a global checkbox and per-user dropdown on the existing Users tab.

**Tech Stack:** Python/Flask (backend), SQLCipher (auth DB), vanilla JS (frontend)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `library/auth/migrations/009_multi_session.sql` | Create | Migration: `system_settings` table, `multi_session` column on `users`, seed default |
| `library/auth/schema.sql` | Modify | Add `system_settings` table DDL, add `multi_session` column to `users` DDL |
| `library/auth/database.py` | Modify | Bump `SCHEMA_VERSION` to 9, add additive migration for `multi_session` + `system_settings` |
| `library/auth/models.py` | Modify | Add `multi_session` field to `User`, add `SystemSettingsRepository` class, add `allow_multi` param to `Session.create_for_user()` |
| `library/backend/api_modular/auth.py` | Modify | Add `_user_allows_multi_session()`, update 5 call sites, add admin settings endpoints, include `multi_session` in `_user_dict()` and `_apply_role_changes()` |
| `library/web-v2/utilities.html` | Modify | Add global multi-session checkbox above user list |
| `library/web-v2/js/utilities.js` | Modify | Load/save system settings, add per-user multi-session selector in user actions |
| `library/tests/test_multi_session.py` | Create | All tests for multi-session feature |

---

### Task 1: Database Migration

**Files:**
- Create: `library/auth/migrations/009_multi_session.sql`
- Modify: `library/auth/schema.sql`
- Modify: `library/auth/database.py`
- Test: `library/tests/test_multi_session.py`

- [ ] **Step 1: Write the failing test**

Create `library/tests/test_multi_session.py`:

```python
"""Tests for multi-session login feature."""

import os
import tempfile

import pytest

from auth.database import AuthDatabase
from auth.models import (
    AuthType,
    Session,
    SessionRepository,
    User,
)


@pytest.fixture
def temp_db():
    """Create a temporary encrypted database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test-auth.db")
        key_path = os.path.join(tmpdir, "test.key")
        db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
        db.initialize()
        yield db


class TestMultiSessionMigration:
    """Tests for migration 009: system_settings table and multi_session column."""

    def test_system_settings_table_exists(self, temp_db):
        """system_settings table should exist after initialization."""
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='system_settings'"
            )
            assert cursor.fetchone() is not None

    def test_multi_session_default_seeded(self, temp_db):
        """multi_session_default should be seeded as 'false'."""
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key = 'multi_session_default'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "false"

    def test_users_have_multi_session_column(self, temp_db):
        """Users table should have multi_session column defaulting to 'default'."""
        with temp_db.connection() as conn:
            conn.execute(
                "INSERT INTO users (username, auth_type, auth_credential) "
                "VALUES ('testuser', 'totp', X'00')"
            )
            cursor = conn.execute(
                "SELECT multi_session FROM users WHERE username = 'testuser'"
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == "default"

    def test_schema_version_is_9(self, temp_db):
        """Schema version should be 9 after migration."""
        with temp_db.connection() as conn:
            cursor = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            )
            assert cursor.fetchone()[0] >= 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestMultiSessionMigration -v`
Expected: FAIL — `system_settings` table doesn't exist, `multi_session` column doesn't exist

- [ ] **Step 3: Create migration file**

Create `library/auth/migrations/009_multi_session.sql`:

```sql
-- Migration 009: Multi-session login support
-- Adds system_settings table for global admin settings
-- and multi_session column to users for per-user override.

CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);

-- Seed the multi-session global default (disabled = current behavior)
INSERT OR IGNORE INTO system_settings (setting_key, setting_value)
VALUES ('multi_session_default', 'false');

INSERT OR IGNORE INTO schema_version (version) VALUES (9);
```

- [ ] **Step 4: Update schema.sql**

Add to `library/auth/schema.sql` — after the `audit_log` table (before the final `INSERT OR IGNORE INTO schema_version`):

```sql
-- System settings table (global admin configuration)
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL
);
```

Add `multi_session` column to the `users` CREATE TABLE statement, after `last_audit_seen_id`:

```sql
    multi_session TEXT NOT NULL DEFAULT 'default',
```

Update the schema_version INSERT from `VALUES (8)` to `VALUES (9)`.

- [ ] **Step 5: Update database.py**

In `library/auth/database.py`:

1. Change `SCHEMA_VERSION = 8` to `SCHEMA_VERSION = 9`

2. Add additive migration block in `initialize()` after the `last_audit_seen_id` block (around line 251):

```python
            # Migration: add system_settings table if not exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )
            """)
            # Seed multi_session_default if not present
            conn.execute(
                "INSERT OR IGNORE INTO system_settings (setting_key, setting_value) "
                "VALUES ('multi_session_default', 'false')"
            )

            # Migration: add multi_session column to users if not exists
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN multi_session TEXT NOT NULL DEFAULT 'default'"
                )
            except Exception:
                pass  # Column already exists
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestMultiSessionMigration -v`
Expected: All 4 tests PASS

- [ ] **Step 7: Commit**

```bash
git add library/auth/migrations/009_multi_session.sql library/auth/schema.sql library/auth/database.py library/tests/test_multi_session.py
git commit -m "feat: add multi-session migration — system_settings table and users.multi_session column"
```

---

### Task 2: User Model and SystemSettingsRepository

**Files:**
- Modify: `library/auth/models.py`
- Test: `library/tests/test_multi_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_multi_session.py`:

```python
class TestUserMultiSessionField:
    """Tests for User.multi_session field."""

    def test_user_has_multi_session_default(self, temp_db):
        """New users should have multi_session='default'."""
        user = User(
            username="ms_test1", auth_type=AuthType.TOTP, auth_credential=b"secret"
        )
        user.save(temp_db)

        # Re-fetch to confirm DB round-trip
        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,))
            fetched = User.from_row(cursor.fetchone())
        assert fetched.multi_session == "default"

    def test_user_multi_session_save_and_load(self, temp_db):
        """User.save() should persist multi_session value."""
        user = User(
            username="ms_test2",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            multi_session="yes",
        )
        user.save(temp_db)

        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,))
            fetched = User.from_row(cursor.fetchone())
        assert fetched.multi_session == "yes"

    def test_user_multi_session_update(self, temp_db):
        """Updating multi_session on an existing user should persist."""
        user = User(
            username="ms_test3", auth_type=AuthType.TOTP, auth_credential=b"secret"
        )
        user.save(temp_db)
        assert user.multi_session == "default"

        user.multi_session = "no"
        user.save(temp_db)

        with temp_db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user.id,))
            fetched = User.from_row(cursor.fetchone())
        assert fetched.multi_session == "no"


class TestSystemSettingsRepository:
    """Tests for SystemSettingsRepository."""

    def test_get_existing_setting(self, temp_db):
        """Should return seeded multi_session_default value."""
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        assert repo.get("multi_session_default") == "false"

    def test_get_nonexistent_setting(self, temp_db):
        """Should return None for missing keys."""
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        assert repo.get("nonexistent_key") is None

    def test_get_with_default(self, temp_db):
        """Should return default for missing keys when default provided."""
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        assert repo.get("nonexistent_key", "fallback") == "fallback"

    def test_set_new_setting(self, temp_db):
        """Should insert a new setting."""
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        repo.set("test_key", "test_value")
        assert repo.get("test_key") == "test_value"

    def test_set_overwrites_existing(self, temp_db):
        """Should overwrite existing setting value."""
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        repo.set("multi_session_default", "true")
        assert repo.get("multi_session_default") == "true"

    def test_get_all(self, temp_db):
        """Should return all settings as a dict."""
        from auth.models import SystemSettingsRepository

        repo = SystemSettingsRepository(temp_db)
        repo.set("extra_key", "extra_val")
        all_settings = repo.get_all()
        assert all_settings["multi_session_default"] == "false"
        assert all_settings["extra_key"] == "extra_val"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestUserMultiSessionField library/tests/test_multi_session.py::TestSystemSettingsRepository -v`
Expected: FAIL — `multi_session` attribute not on User, `SystemSettingsRepository` doesn't exist

- [ ] **Step 3: Add multi_session field to User class**

In `library/auth/models.py`, class `User` (line ~56):

1. Add field after `last_audit_seen_id: int = 0` (line 85):

```python
    multi_session: str = "default"
```

2. Update `from_row()` — after the `last_audit_seen_id` block (line 118-119), add:

```python
        # Multi-session override (schema v9+, column 12)
        if len(row) >= 13:
            fields["multi_session"] = row[12] if row[12] is not None else "default"
```

3. Update `save()` INSERT — add `multi_session` to the column list and values:

In the INSERT statement (line ~127-148), add `multi_session` after `last_audit_seen_id`:

```python
                    """
                    INSERT INTO users (
                        username, auth_type, auth_credential,
                        can_download, is_admin,
                        recovery_email, recovery_phone, recovery_enabled,
                        last_audit_seen_id, multi_session
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.username,
                        self.auth_type.value,
                        self.auth_credential,
                        self.can_download,
                        self.is_admin,
                        self.recovery_email,
                        self.recovery_phone,
                        self.recovery_enabled,
                        self.last_audit_seen_id,
                        self.multi_session,
                    ),
```

4. Update `save()` UPDATE — add `multi_session` to the SET clause:

```python
                    """
                    UPDATE users SET
                        username = ?, auth_type = ?, auth_credential = ?,
                        can_download = ?, is_admin = ?, last_login = ?,
                        recovery_email = ?, recovery_phone = ?, recovery_enabled = ?,
                        last_audit_seen_id = ?, multi_session = ?
                    WHERE id = ?
                    """,
                    (
                        self.username,
                        self.auth_type.value,
                        self.auth_credential,
                        self.can_download,
                        self.is_admin,
                        self.last_login.isoformat() if self.last_login else None,
                        self.recovery_email,
                        self.recovery_phone,
                        self.recovery_enabled,
                        self.last_audit_seen_id,
                        self.multi_session,
                        self.id,
                    ),
```

- [ ] **Step 4: Add SystemSettingsRepository class**

In `library/auth/models.py`, add after the `UserSettingsRepository` class (end of file):

```python
class SystemSettingsRepository:
    """Repository for global system settings (admin-only key-value store)."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get a system setting value by key."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key = ?",
                (key,),
            )
            row = cursor.fetchone()
            return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        """Set a system setting (insert or update)."""
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO system_settings (setting_key, setting_value) "
                "VALUES (?, ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                (key, value),
            )

    def get_all(self) -> dict[str, str]:
        """Get all system settings as a dict."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT setting_key, setting_value FROM system_settings")
            return {row[0]: row[1] for row in cursor.fetchall()}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestUserMultiSessionField library/tests/test_multi_session.py::TestSystemSettingsRepository -v`
Expected: All 9 tests PASS

- [ ] **Step 6: Run full existing test suite to verify no regressions**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_auth.py -v --tb=short`
Expected: All tests PASS (the extra column is handled gracefully by `from_row`)

- [ ] **Step 7: Commit**

```bash
git add library/auth/models.py library/tests/test_multi_session.py
git commit -m "feat: add User.multi_session field and SystemSettingsRepository"
```

---

### Task 3: Session Logic — allow_multi Parameter

**Files:**
- Modify: `library/auth/models.py`
- Test: `library/tests/test_multi_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_multi_session.py`:

```python
class TestSessionAllowMulti:
    """Tests for Session.create_for_user() allow_multi parameter."""

    def _make_user(self, temp_db, username="session_user"):
        user = User(
            username=username, auth_type=AuthType.TOTP, auth_credential=b"secret"
        )
        user.save(temp_db)
        return user

    def test_default_behavior_single_session(self, temp_db):
        """Default (allow_multi=False) should invalidate existing sessions."""
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)

        session1, token1 = Session.create_for_user(temp_db, user.id)
        session2, token2 = Session.create_for_user(temp_db, user.id)

        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is not None

    def test_allow_multi_preserves_sessions(self, temp_db):
        """allow_multi=True should keep existing sessions alive."""
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)

        session1, token1 = Session.create_for_user(temp_db, user.id)
        session2, token2 = Session.create_for_user(
            temp_db, user.id, allow_multi=True
        )

        # Both sessions should be valid
        assert repo.get_by_token(token1) is not None
        assert repo.get_by_token(token2) is not None

    def test_allow_multi_false_still_deletes(self, temp_db):
        """Explicit allow_multi=False should still invalidate sessions."""
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)

        session1, token1 = Session.create_for_user(temp_db, user.id)
        session2, token2 = Session.create_for_user(
            temp_db, user.id, allow_multi=False
        )

        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is not None

    def test_allow_multi_three_sessions(self, temp_db):
        """Multiple allow_multi=True logins should all coexist."""
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)

        _, token1 = Session.create_for_user(temp_db, user.id)
        _, token2 = Session.create_for_user(temp_db, user.id, allow_multi=True)
        _, token3 = Session.create_for_user(temp_db, user.id, allow_multi=True)

        assert repo.get_by_token(token1) is not None
        assert repo.get_by_token(token2) is not None
        assert repo.get_by_token(token3) is not None

    def test_single_session_after_multi_clears_all(self, temp_db):
        """A single-session login after multi-session should clear ALL prior sessions."""
        user = self._make_user(temp_db)
        repo = SessionRepository(temp_db)

        _, token1 = Session.create_for_user(temp_db, user.id)
        _, token2 = Session.create_for_user(temp_db, user.id, allow_multi=True)
        _, token3 = Session.create_for_user(temp_db, user.id, allow_multi=False)

        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is None
        assert repo.get_by_token(token3) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestSessionAllowMulti -v`
Expected: FAIL — `create_for_user()` doesn't accept `allow_multi` parameter

- [ ] **Step 3: Add allow_multi parameter to Session.create_for_user()**

In `library/auth/models.py`, modify `Session.create_for_user()` (line ~371):

1. Add `allow_multi: bool = False` to the method signature:

```python
    @classmethod
    def create_for_user(
        cls,
        db: AuthDatabase,
        user_id: int,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        remember_me: bool = False,
        allow_multi: bool = False,
    ) -> tuple["Session", str]:
```

2. Update the docstring to include:

```python
            allow_multi: If True, keep existing sessions (multi-device support)
```

3. Make the DELETE conditional (line ~397):

```python
            # Invalidate existing sessions (unless multi-session is allowed)
            if not allow_multi:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestSessionAllowMulti -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full auth tests to confirm no regression**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_auth.py -v --tb=short`
Expected: All tests PASS (default `allow_multi=False` preserves existing behavior)

- [ ] **Step 6: Commit**

```bash
git add library/auth/models.py library/tests/test_multi_session.py
git commit -m "feat: add allow_multi parameter to Session.create_for_user()"
```

---

### Task 4: Resolution Function and Call Sites

**Files:**
- Modify: `library/backend/api_modular/auth.py`
- Modify: `library/auth/models.py` (import only)
- Test: `library/tests/test_multi_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_multi_session.py`:

```python
class TestUserAllowsMultiSession:
    """Tests for _user_allows_multi_session() resolution logic."""

    def _make_user(self, temp_db, username, multi_session="default"):
        user = User(
            username=username,
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            multi_session=multi_session,
        )
        user.save(temp_db)
        return user

    def test_user_yes_overrides_global_false(self, temp_db):
        """User with multi_session='yes' should allow multi even if global is false."""
        from auth.models import SystemSettingsRepository

        SystemSettingsRepository(temp_db).set("multi_session_default", "false")
        user = self._make_user(temp_db, "override_yes", multi_session="yes")

        from backend.api_modular.auth import _user_allows_multi_session

        assert _user_allows_multi_session(user, temp_db) is True

    def test_user_no_overrides_global_true(self, temp_db):
        """User with multi_session='no' should deny multi even if global is true."""
        from auth.models import SystemSettingsRepository

        SystemSettingsRepository(temp_db).set("multi_session_default", "true")
        user = self._make_user(temp_db, "override_no", multi_session="no")

        from backend.api_modular.auth import _user_allows_multi_session

        assert _user_allows_multi_session(user, temp_db) is False

    def test_user_default_follows_global_false(self, temp_db):
        """User with multi_session='default' should follow global=false."""
        from auth.models import SystemSettingsRepository

        SystemSettingsRepository(temp_db).set("multi_session_default", "false")
        user = self._make_user(temp_db, "follow_false")

        from backend.api_modular.auth import _user_allows_multi_session

        assert _user_allows_multi_session(user, temp_db) is False

    def test_user_default_follows_global_true(self, temp_db):
        """User with multi_session='default' should follow global=true."""
        from auth.models import SystemSettingsRepository

        SystemSettingsRepository(temp_db).set("multi_session_default", "true")
        user = self._make_user(temp_db, "follow_true")

        from backend.api_modular.auth import _user_allows_multi_session

        assert _user_allows_multi_session(user, temp_db) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestUserAllowsMultiSession -v`
Expected: FAIL — `_user_allows_multi_session` doesn't exist

- [ ] **Step 3: Add _user_allows_multi_session() to auth.py**

In `library/backend/api_modular/auth.py`, add the import at the top (with the existing auth.models imports):

```python
from auth.models import SystemSettingsRepository
```

Add the resolution function near the top helper functions (after `_user_dict`, around line 232):

```python
def _user_allows_multi_session(user, db=None) -> bool:
    """Check if a user is allowed multiple concurrent sessions.

    Resolution order: per-user override > global system setting.
    """
    if user.multi_session == "yes":
        return True
    if user.multi_session == "no":
        return False
    # 'default' — check global system setting
    if db is None:
        db = get_auth_db()
    repo = SystemSettingsRepository(db)
    return repo.get("multi_session_default") == "true"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestUserAllowsMultiSession -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Update all 5 call sites**

Each call site needs to: (a) fetch the user if not already available, (b) resolve `_user_allows_multi_session()`, (c) pass `allow_multi=` to `Session.create_for_user()`.

**Call site 1 — `login()` (line ~902):**

The `user` variable is already in scope. Replace:

```python
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
    )
```

With:

```python
    allow_multi = _user_allows_multi_session(user, db)
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
        allow_multi=allow_multi,
    )
```

**Call site 2 — `_claim_webauthn_reset()` (line ~1933):**

`existing_user` is in scope. Replace:

```python
    session, token = Session.create_for_user(
        db,
        existing_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
    )
```

With:

```python
    allow_multi = _user_allows_multi_session(existing_user, db)
    session, token = Session.create_for_user(
        db,
        existing_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        allow_multi=allow_multi,
    )
```

**Call site 3 — `_claim_webauthn_new_user()` (line ~1979):**

`new_user` is in scope (just created, `multi_session` will be `'default'`). Replace:

```python
    session, token = Session.create_for_user(
        db,
        new_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
    )
```

With:

```python
    allow_multi = _user_allows_multi_session(new_user, db)
    session, token = Session.create_for_user(
        db,
        new_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        allow_multi=allow_multi,
    )
```

**Call site 4 — `login_webauthn_complete()` (line ~2479):**

`user` is in scope. Replace:

```python
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
    )
```

With:

```python
    allow_multi = _user_allows_multi_session(user, db)
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
        allow_multi=allow_multi,
    )
```

**Call site 5 — `complete_magic_link()` (line ~2968):**

`user` is in scope. Replace:

```python
    session, raw_token = Session.create_for_user(
        db, user.id, user_agent, ip_address, remember_me=remember_me
    )
```

With:

```python
    allow_multi = _user_allows_multi_session(user, db)
    session, raw_token = Session.create_for_user(
        db, user.id, user_agent, ip_address, remember_me=remember_me,
        allow_multi=allow_multi,
    )
```

- [ ] **Step 6: Run full auth tests**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_auth.py library/tests/test_auth_api.py library/tests/test_multi_session.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add library/backend/api_modular/auth.py library/tests/test_multi_session.py
git commit -m "feat: add _user_allows_multi_session() and wire up all 5 login call sites"
```

---

### Task 5: Admin Settings API Endpoints

**Files:**
- Modify: `library/backend/api_modular/auth.py`
- Test: `library/tests/test_multi_session.py`

- [ ] **Step 1: Write the failing tests**

Append to `library/tests/test_multi_session.py`:

```python
class TestAdminSettingsAPI:
    """Tests for GET/PATCH /api/admin/settings endpoints."""

    @pytest.fixture
    def app_client(self, temp_db, monkeypatch):
        """Create a Flask test client with auth DB wired up."""
        import sys

        # Ensure library is on path
        lib_dir = os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir
        )
        if lib_dir not in sys.path:
            sys.path.insert(0, os.path.abspath(lib_dir))

        from backend.api_modular.auth import auth_bp, get_auth_db
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(auth_bp)

        monkeypatch.setattr(
            "backend.api_modular.auth.get_auth_db", lambda: temp_db
        )

        # Create admin user and session for auth
        admin = User(
            username="admin_settings",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            is_admin=True,
        )
        admin.save(temp_db)
        session, token = Session.create_for_user(temp_db, admin.id)

        client = app.test_client()
        # Set session cookie
        client.set_cookie("session_token", token, domain="localhost")
        return client

    def test_get_settings(self, app_client):
        """GET /auth/admin/settings should return all system settings."""
        resp = app_client.get("/auth/admin/settings")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "multi_session_default" in data
        assert data["multi_session_default"] == "false"

    def test_patch_settings(self, app_client):
        """PATCH /auth/admin/settings should update settings."""
        resp = app_client.patch(
            "/auth/admin/settings",
            json={"multi_session_default": "true"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify it persisted
        resp = app_client.get("/auth/admin/settings")
        data = resp.get_json()
        assert data["multi_session_default"] == "true"

    def test_patch_settings_rejects_empty(self, app_client):
        """PATCH /auth/admin/settings with empty body should return 400."""
        resp = app_client.patch("/auth/admin/settings", json={})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestAdminSettingsAPI -v`
Expected: FAIL — endpoints don't exist (404)

- [ ] **Step 3: Add admin settings endpoints to auth.py**

In `library/backend/api_modular/auth.py`, add after the existing admin user management endpoints (after `admin_change_auth_method`, around the end of the admin section):

```python
@auth_bp.route("/admin/settings", methods=["GET"])
@admin_required
def get_admin_settings():
    """Get all system settings (admin only)."""
    db = get_auth_db()
    repo = SystemSettingsRepository(db)
    return jsonify(repo.get_all())


@auth_bp.route("/admin/settings", methods=["PATCH"])
@admin_required
def update_admin_settings():
    """
    Update one or more system settings (admin only).

    JSON body: {"setting_key": "value", ...}
    Only known setting keys are accepted.
    """
    ALLOWED_KEYS = {"multi_session_default"}

    data = request.get_json() or {}
    updates = {k: v for k, v in data.items() if k in ALLOWED_KEYS}
    if not updates:
        return jsonify({"error": "No valid settings provided"}), 400

    db = get_auth_db()
    repo = SystemSettingsRepository(db)
    for key, value in updates.items():
        repo.set(key, str(value))

    return jsonify({"success": True, "updated": updates})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestAdminSettingsAPI -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add library/backend/api_modular/auth.py library/tests/test_multi_session.py
git commit -m "feat: add GET/PATCH /auth/admin/settings endpoints for system settings"
```

---

### Task 6: Expose multi_session in User API Responses

**Files:**
- Modify: `library/backend/api_modular/auth.py`
- Test: `library/tests/test_multi_session.py`

- [ ] **Step 1: Write the failing test**

Append to `library/tests/test_multi_session.py`:

```python
class TestUserDictMultiSession:
    """Tests for multi_session in _user_dict() and role endpoints."""

    def test_user_dict_includes_multi_session(self, temp_db):
        """_user_dict() should include multi_session field."""
        from backend.api_modular.auth import _user_dict

        user = User(
            username="dict_test",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            multi_session="yes",
        )
        user.save(temp_db)

        d = _user_dict(user)
        assert "multi_session" in d
        assert d["multi_session"] == "yes"

    def test_user_dict_default_value(self, temp_db):
        """_user_dict() should show 'default' for new users."""
        from backend.api_modular.auth import _user_dict

        user = User(
            username="dict_default",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)

        d = _user_dict(user)
        assert d["multi_session"] == "default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestUserDictMultiSession -v`
Expected: FAIL — `multi_session` not in dict

- [ ] **Step 3: Update _user_dict() and _apply_role_changes()**

In `library/backend/api_modular/auth.py`:

1. In `_user_dict()` (line ~220), add `multi_session` to the dict:

```python
def _user_dict(user, include_auth_type: bool = False) -> dict:
    """Build a standard user dict for API responses."""
    d = {
        "id": user.id,
        "username": user.username,
        "email": user.recovery_email,
        "is_admin": user.is_admin,
        "can_download": user.can_download,
        "multi_session": user.multi_session,
    }
    if include_auth_type:
        d["auth_type"] = user.auth_type.value
    return d
```

2. In `_apply_role_changes()` (line ~5205), add `multi_session` handling:

After the `can_download` block, add:

```python
    if "multi_session" in data:
        value = data["multi_session"]
        if value not in ("default", "yes", "no"):
            return {"error": "multi_session must be 'default', 'yes', or 'no'"}, 400
        user_repo.set_multi_session(user_id, value)
```

3. Add `set_multi_session()` to `UserRepository` in `library/auth/models.py`:

After `set_download_permission()` (around line 270):

```python
    def set_multi_session(self, user_id: int, value: str) -> bool:
        """Set multi-session override for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET multi_session = ? WHERE id = ?", (value, user_id)
            )
            return cursor.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py::TestUserDictMultiSession -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Run broader tests to confirm no regression**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py library/tests/test_auth.py -v --tb=short`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add library/backend/api_modular/auth.py library/auth/models.py library/tests/test_multi_session.py
git commit -m "feat: expose multi_session in user API responses and admin role changes"
```

---

### Task 7: Back Office UI — Global Toggle and Per-User Selector

**Files:**
- Modify: `library/web-v2/utilities.html`
- Modify: `library/web-v2/js/utilities.js`

- [ ] **Step 1: Add global multi-session checkbox to utilities.html**

In `library/web-v2/utilities.html`, inside the `#users-tab` div (after line 1023 `<div class="user-tab-content active" id="users-tab">`), add before the `<div class="user-list" id="user-list">`:

```html
                                <div class="system-settings-row" id="multi-session-toggle" style="padding: 8px 12px; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid var(--border-color, #333);">
                                    <label style="display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.9em;" title="When enabled, users can stay logged in on multiple devices simultaneously">
                                        <input type="checkbox" id="multi-session-default-checkbox">
                                        Allow multiple sessions by default
                                    </label>
                                </div>
```

- [ ] **Step 2: Add global toggle JS logic to utilities.js**

In `library/web-v2/js/utilities.js`, add a function to load and save the global setting. Add near the existing `loadUsers()` function:

```javascript
async function loadSystemSettings() {
  try {
    const settings = await api.get("/auth/admin/settings", { toast: false });
    const checkbox = document.getElementById("multi-session-default-checkbox");
    if (checkbox && settings) {
      checkbox.checked = settings.multi_session_default === "true";
      checkbox.addEventListener("change", async () => {
        try {
          await api.patch("/auth/admin/settings", {
            multi_session_default: checkbox.checked ? "true" : "false",
          });
          showToast(
            checkbox.checked
              ? "Multiple sessions enabled by default"
              : "Multiple sessions disabled by default",
            "success"
          );
        } catch (err) {
          checkbox.checked = !checkbox.checked; // Revert on failure
          showToast("Failed to update setting: " + err.message, "error");
        }
      });
    }
  } catch {
    // Non-admin or settings not available — hide the toggle
    const toggle = document.getElementById("multi-session-toggle");
    if (toggle) toggle.style.display = "none";
  }
}
```

Call `loadSystemSettings()` alongside `loadUsers()` — find where `loadUsers()` is called on page init (around line 2998) and add:

```javascript
loadSystemSettings();
```

- [ ] **Step 3: Add per-user multi-session selector**

In `library/web-v2/js/utilities.js`, in the user item rendering function (the function that creates user action buttons, around line 3564-3574), add after the download toggle button and before the "View Setup" button:

```javascript
  // Multi-session selector
  const msSelect = document.createElement("select");
  msSelect.className = "user-action-btn";
  msSelect.title = `Multi-session setting for ${user.username}`;
  msSelect.style.cssText = "padding: 4px 6px; font-size: 0.85em; cursor: pointer;";
  ["default", "yes", "no"].forEach((val) => {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = val === "default" ? "Sessions: Default" : val === "yes" ? "Sessions: Yes" : "Sessions: No";
    if (user.multi_session === val) opt.selected = true;
    msSelect.appendChild(opt);
  });
  msSelect.addEventListener("change", async () => {
    try {
      await api.put(`/auth/admin/users/${user.id}/roles`, {
        multi_session: msSelect.value,
      });
      showToast(`Multi-session set to '${msSelect.value}' for ${user.username}`, "success");
    } catch (err) {
      showToast("Failed to update: " + err.message, "error");
      msSelect.value = user.multi_session; // Revert on failure
    }
  });
  actions.appendChild(msSelect);
```

- [ ] **Step 4: Verify the `api.patch` helper exists**

Search `utilities.js` for an existing `api.patch` method. If not present, add it alongside the existing `api.get`/`api.post`/`api.put` methods:

```javascript
  async patch(url, body, opts = {}) {
    return this._request("PATCH", url, body, opts);
  },
```

(This is only needed if `api.patch` doesn't already exist — the `api` object likely has a generic `_request` method that makes this trivial.)

- [ ] **Step 5: Manual smoke test**

1. Open the Back Office in a browser
2. Navigate to Users tab
3. Verify the "Allow multiple sessions by default" checkbox appears at the top
4. Verify each user row has a "Sessions: Default/Yes/No" dropdown
5. Toggle the checkbox — confirm toast appears and value persists on refresh
6. Change a user's dropdown — confirm toast and persistence

- [ ] **Step 6: Commit**

```bash
git add library/web-v2/utilities.html library/web-v2/js/utilities.js
git commit -m "feat: add multi-session UI — global checkbox and per-user selector in Back Office"
```

---

### Task 8: Final Integration Test and Cleanup

**Files:**
- Test: `library/tests/test_multi_session.py`
- Verify: all files from Tasks 1-7

- [ ] **Step 1: Write end-to-end integration test**

Append to `library/tests/test_multi_session.py`:

```python
class TestMultiSessionIntegration:
    """End-to-end integration tests for the full multi-session flow."""

    def test_global_enabled_allows_multi_login(self, temp_db):
        """With global multi-session enabled, two logins should coexist."""
        from auth.models import SystemSettingsRepository

        # Enable globally
        SystemSettingsRepository(temp_db).set("multi_session_default", "true")

        user = User(
            username="integration1",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)

        from backend.api_modular.auth import _user_allows_multi_session

        assert _user_allows_multi_session(user, temp_db) is True

        repo = SessionRepository(temp_db)
        _, token1 = Session.create_for_user(temp_db, user.id, allow_multi=True)
        _, token2 = Session.create_for_user(temp_db, user.id, allow_multi=True)

        assert repo.get_by_token(token1) is not None
        assert repo.get_by_token(token2) is not None

    def test_per_user_no_overrides_global_true(self, temp_db):
        """Per-user 'no' should enforce single session even when global is enabled."""
        from auth.models import SystemSettingsRepository

        SystemSettingsRepository(temp_db).set("multi_session_default", "true")

        user = User(
            username="integration2",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            multi_session="no",
        )
        user.save(temp_db)

        from backend.api_modular.auth import _user_allows_multi_session

        assert _user_allows_multi_session(user, temp_db) is False

        repo = SessionRepository(temp_db)
        _, token1 = Session.create_for_user(temp_db, user.id)
        _, token2 = Session.create_for_user(temp_db, user.id, allow_multi=False)

        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is not None

    def test_backwards_compat_default_global_false(self, temp_db):
        """Default state (global=false, user=default) should enforce single session."""
        user = User(
            username="integration3",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        )
        user.save(temp_db)

        from backend.api_modular.auth import _user_allows_multi_session

        # Global default is 'false' (seeded by migration)
        assert _user_allows_multi_session(user, temp_db) is False

        repo = SessionRepository(temp_db)
        _, token1 = Session.create_for_user(temp_db, user.id)
        _, token2 = Session.create_for_user(temp_db, user.id)

        assert repo.get_by_token(token1) is None
        assert repo.get_by_token(token2) is not None
```

- [ ] **Step 2: Run all multi-session tests**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/test_multi_session.py -v`
Expected: All tests PASS (migration + model + session + resolution + API + integration)

- [ ] **Step 3: Run the full test suite**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && python -m pytest library/tests/ -v --tb=short -x`
Expected: All existing tests continue to pass with zero regressions

- [ ] **Step 4: Run linters**

Run: `cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager && ruff check library/auth/models.py library/backend/api_modular/auth.py library/tests/test_multi_session.py && ruff format --check library/auth/models.py library/backend/api_modular/auth.py library/tests/test_multi_session.py`
Expected: No errors

- [ ] **Step 5: Commit test file**

```bash
git add library/tests/test_multi_session.py
git commit -m "test: add end-to-end integration tests for multi-session login"
```

- [ ] **Step 6: Update CHANGELOG.md**

Add under `## [Unreleased]` → `### Added`:

```markdown
- Multi-session login: admin-controllable toggle for concurrent device logins (global default + per-user override)
```

- [ ] **Step 7: Commit changelog**

```bash
git add CHANGELOG.md
git commit -m "docs: add multi-session login to unreleased changelog"
```
