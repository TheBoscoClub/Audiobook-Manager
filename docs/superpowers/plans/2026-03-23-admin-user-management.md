# Admin User Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web-based user lifecycle management — admin creation, self-service settings, audit logging, and notifications — eliminating routine need to access the encrypted auth DB directly.

**Architecture:** New USERS tab in Back Office for admin operations, My Account modal in shell header for self-service, shared backend logic with authorization as the differentiator, audit_log table in SQLCipher auth DB, three-tier admin notifications (in-app toast, email, audit highlight).

**Tech Stack:** Python/Flask (backend), SQLCipher (auth DB), vanilla JS (frontend), Resend SMTP (email notifications), existing Art Deco CSS framework.

**Spec:** `docs/superpowers/specs/2026-03-23-admin-user-management-design.md`

**Branch:** `user-management` (off `main`)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `library/auth/schema.sql` | Modify | Add `audit_log` table, add `last_audit_seen_id` to users |
| `library/auth/models.py` | Modify | Add `AuditLog` dataclass, `AuditLogRepository`, audit helper |
| `library/auth/audit.py` | Create | Audit logging + admin notification logic (email + in-app) |
| `library/backend/api_modular/auth.py` | Modify | New admin + self-service endpoints (~15 endpoints) |
| `library/web-v2/utilities.html` | Modify | New USERS tab markup, audit log section in Activity tab |
| `library/web-v2/js/utilities.js` | Modify | USERS tab logic, audit log rendering, notification badges |
| `library/web-v2/shell.html` | Modify | Add header bar with My Account trigger |
| `library/web-v2/shell.css` | Modify | Header bar + My Account modal styles |
| `library/web-v2/js/account.js` | Create | My Account modal logic |
| `library/tests/test_audit_log.py` | Create | Audit log model + repository tests |
| `library/tests/test_admin_user_management.py` | Create | Admin endpoint tests |
| `library/tests/test_self_service.py` | Create | Self-service endpoint tests |
| `library/tests/test_last_admin_guard.py` | Create | Last-admin deletion guard tests |

---

## Task 1: Create Branch + Schema Migration

**Files:**
- Modify: `library/auth/schema.sql` (after line ~213)
- Modify: `library/auth/models.py` (User dataclass, ~line 54)

- [ ] **Step 1: Create feature branch**

```bash
git checkout -b user-management main
```

- [ ] **Step 2: Add audit_log table to schema.sql**

Add after the existing `schema_version` table:

```sql
-- Audit log for user management actions (nullable FKs survive user deletion)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    target_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
```

- [ ] **Step 3: Add last_audit_seen_id to users table in schema.sql**

Add column to the CREATE TABLE users statement:

```sql
last_audit_seen_id INTEGER DEFAULT 0
```

- [ ] **Step 4: Add last_audit_seen_id to User dataclass in models.py**

In the `User` dataclass (line ~54), add field:

```python
last_audit_seen_id: int = 0
```

Update `User.from_row()` to handle the new column. The existing pattern uses **positional tuple indexing** (`row[0]`, `row[1]`, etc.) — NOT dict-style access. Add after the existing recovery fields:

```python
# In the >= 11 branch, add after recovery_enabled:
last_audit_seen_id=int(row[11]) if len(row) >= 12 and row[11] is not None else 0,
```

**IMPORTANT**: All `from_row()` methods in this codebase use positional indexing. Never use `row["column_name"]` — the DB connections don't set `row_factory`.

- [ ] **Step 5: Add migration logic for existing databases**

In `library/auth/database.py`, in the migration/init section, add:

```python
# Migration: add audit_log table if not exists
conn.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        target_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        details TEXT
    )
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")

# Migration: add last_audit_seen_id to users if not exists
try:
    conn.execute("ALTER TABLE users ADD COLUMN last_audit_seen_id INTEGER DEFAULT 0")
except Exception:
    pass  # Column already exists
```

- [ ] **Step 6: Commit**

```bash
git add library/auth/schema.sql library/auth/models.py library/auth/database.py
git commit -m "feat(auth): add audit_log schema and user last_audit_seen_id"
```

---

## Task 2: Audit Log Model + Repository (TDD)

**Files:**
- Create: `library/auth/audit.py`
- Create: `library/tests/test_audit_log.py`
- Modify: `library/auth/models.py` (add AuditLog dataclass)

- [ ] **Step 1: Write failing tests for AuditLog model and repository**

Create `library/tests/test_audit_log.py`:

```python
"""Tests for audit log model and repository."""
import json
import pytest
from datetime import datetime
from auth.models import AuditLog
from auth.audit import AuditLogRepository


@pytest.fixture
def audit_repo(auth_db):
    """AuditLogRepository backed by test auth DB."""
    return AuditLogRepository(auth_db)


@pytest.fixture
def sample_user(auth_db):
    """Create a test user, return user ID."""
    from auth.models import User, AuthType
    user = User(username="testuser", auth_type=AuthType.TOTP, auth_credential=b"secret")
    user = user.save(auth_db)
    return user.id


class TestAuditLogRepository:
    def test_log_action_creates_entry(self, audit_repo, sample_user):
        entry = audit_repo.log(
            actor_id=sample_user,
            target_id=sample_user,
            action="change_username",
            details={"old": "testuser", "new": "newname", "actor_username": "testuser", "target_username": "testuser"},
        )
        assert entry.id is not None
        assert entry.action == "change_username"
        assert entry.actor_id == sample_user
        assert entry.target_id == sample_user

    def test_log_stores_details_as_json(self, audit_repo, sample_user):
        details = {"old": "totp", "new": "magic_link", "actor_username": "testuser", "target_username": "testuser"}
        entry = audit_repo.log(
            actor_id=sample_user,
            target_id=sample_user,
            action="switch_auth_method",
            details=details,
        )
        fetched = audit_repo.get_by_id(entry.id)
        parsed = json.loads(fetched.details)
        assert parsed["old"] == "totp"
        assert parsed["new"] == "magic_link"

    def test_list_returns_newest_first(self, audit_repo, sample_user):
        audit_repo.log(actor_id=sample_user, target_id=sample_user, action="action_1", details={})
        audit_repo.log(actor_id=sample_user, target_id=sample_user, action="action_2", details={})
        entries = audit_repo.list(limit=10)
        assert entries[0].action == "action_2"
        assert entries[1].action == "action_1"

    def test_list_filters_by_action(self, audit_repo, sample_user):
        audit_repo.log(actor_id=sample_user, target_id=sample_user, action="change_username", details={})
        audit_repo.log(actor_id=sample_user, target_id=sample_user, action="delete_account", details={})
        entries = audit_repo.list(action_filter="change_username")
        assert len(entries) == 1
        assert entries[0].action == "change_username"

    def test_list_filters_by_user(self, audit_repo, auth_db):
        from auth.models import User, AuthType
        user1 = User(username="user1", auth_type=AuthType.TOTP, auth_credential=b"s1").save(auth_db)
        user2 = User(username="user2", auth_type=AuthType.TOTP, auth_credential=b"s2").save(auth_db)
        audit_repo.log(actor_id=user1.id, target_id=user1.id, action="change_email", details={})
        audit_repo.log(actor_id=user2.id, target_id=user2.id, action="change_email", details={})
        entries = audit_repo.list(user_filter=user1.id)
        assert len(entries) == 1

    def test_list_pagination(self, audit_repo, sample_user):
        for i in range(5):
            audit_repo.log(actor_id=sample_user, target_id=sample_user, action=f"action_{i}", details={})
        page1 = audit_repo.list(limit=2, offset=0)
        page2 = audit_repo.list(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].action != page2[0].action

    def test_count_unseen_for_admin(self, audit_repo, sample_user):
        audit_repo.log(actor_id=sample_user, target_id=sample_user, action="change_username", details={})
        audit_repo.log(actor_id=sample_user, target_id=sample_user, action="delete_account", details={})
        count = audit_repo.count_unseen(last_seen_id=0)
        assert count == 2

    def test_entries_survive_user_deletion(self, audit_repo, auth_db):
        from auth.models import User, AuthType, UserRepository
        user = User(username="ephemeral", auth_type=AuthType.TOTP, auth_credential=b"s").save(auth_db)
        repo = UserRepository(auth_db)
        audit_repo.log(
            actor_id=user.id, target_id=user.id,
            action="delete_account",
            details={"username": "ephemeral", "actor_username": "ephemeral", "target_username": "ephemeral"},
        )
        repo.delete(user.id)
        entries = audit_repo.list(limit=10)
        assert len(entries) >= 1
        assert entries[0].action == "delete_account"
        assert entries[0].actor_id is None  # SET NULL after deletion
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager
python -m pytest library/tests/test_audit_log.py -v
```

Expected: ImportError / ModuleNotFoundError (audit module doesn't exist yet)

- [ ] **Step 3: Create AuditLog dataclass in models.py**

Add after the `User` dataclass:

```python
@dataclass
class AuditLog:
    """Audit log entry for user management actions."""
    id: Optional[int] = None
    timestamp: Optional[str] = None
    actor_id: Optional[int] = None
    target_id: Optional[int] = None
    action: str = ""
    details: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "AuditLog":
        """Create AuditLog from database row (positional tuple indexing)."""
        if row is None:
            return None
        return cls(
            id=row[0],
            timestamp=row[1],
            actor_id=row[2],
            target_id=row[3],
            action=row[4],
            details=row[5],
        )
```

- [ ] **Step 4: Create audit.py with AuditLogRepository**

**Note:** After creating `audit.py`, verify `library/auth/__init__.py` exports `AuditLog` and `AuditLogRepository` so `from auth.audit import ...` works in tests and endpoints.

Create `library/auth/audit.py`:

```python
"""Audit logging for user management actions."""
import json
from typing import Dict, List, Optional

from .models import AuditLog


class AuditLogRepository:
    """Repository for audit log CRUD operations."""

    def __init__(self, db):
        self.db = db

    def log(
        self,
        actor_id: int,
        target_id: int,
        action: str,
        details: Optional[Dict] = None,
    ) -> AuditLog:
        """Create an audit log entry. Returns the created entry."""
        details_json = json.dumps(details) if details else None
        with self.db.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO audit_log (actor_id, target_id, action, details) VALUES (?, ?, ?, ?)",
                (actor_id, target_id, action, details_json),
            )
            conn.commit()
            return self.get_by_id(cursor.lastrowid)

    def get_by_id(self, entry_id: int) -> Optional[AuditLog]:
        """Get a single audit log entry by ID."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,))
            row = cursor.fetchone()
            return AuditLog.from_row(row)

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        action_filter: Optional[str] = None,
        user_filter: Optional[int] = None,
    ) -> List[AuditLog]:
        """List audit log entries, newest first. Optionally filter by action or user."""
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if action_filter:
            query += " AND action = ?"
            params.append(action_filter)
        if user_filter is not None:
            query += " AND (actor_id = ? OR target_id = ?)"
            params.extend([user_filter, user_filter])
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.db.connection() as conn:
            cursor = conn.execute(query, params)
            return [AuditLog.from_row(row) for row in cursor.fetchall()]

    def count(
        self,
        action_filter: Optional[str] = None,
        user_filter: Optional[int] = None,
    ) -> int:
        """Count total audit log entries (for pagination)."""
        query = "SELECT COUNT(*) FROM audit_log WHERE 1=1"
        params = []
        if action_filter:
            query += " AND action = ?"
            params.append(action_filter)
        if user_filter is not None:
            query += " AND (actor_id = ? OR target_id = ?)"
            params.extend([user_filter, user_filter])
        with self.db.connection() as conn:
            return conn.execute(query, params).fetchone()[0]

    def count_unseen(self, last_seen_id: int) -> int:
        """Count entries newer than the given ID (for badge count)."""
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE id > ?", (last_seen_id,)
            ).fetchone()[0]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest library/tests/test_audit_log.py -v
```

Expected: All 9 tests PASS

- [ ] **Step 6: Commit**

```bash
git add library/auth/audit.py library/auth/models.py library/tests/test_audit_log.py
git commit -m "feat(auth): add audit log model and repository with tests"
```

---

## Task 3: Admin Notification Helper

**Files:**
- Modify: `library/auth/audit.py` (add notification functions)

- [ ] **Step 1: Add notification helper to audit.py**

Add to `library/auth/audit.py`:

```python
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Actions that trigger admin notifications
CRITICAL_ACTIONS = {
    "change_username",
    "switch_auth_method",
    "reset_credentials",
    "delete_account",
}


def notify_admins(action: str, details: Dict, db) -> None:
    """Send notifications to all admins for critical actions.

    In-app: handled by badge count (count_unseen).
    Email: sent to all admins with a recovery_email set.
    """
    if action not in CRITICAL_ACTIONS:
        return

    from .models import UserRepository
    user_repo = UserRepository(db)
    admins = [u for u in user_repo.list_all() if u.is_admin and u.recovery_email]

    if not admins:
        return

    subject, body = _format_notification(action, details)
    for admin in admins:
        _send_notification_email(admin.recovery_email, subject, body)


def _format_notification(action: str, details: Dict) -> tuple:
    """Format email subject and body for an audit action."""
    actor = details.get("actor_username", "Unknown")
    target = details.get("target_username", actor)
    action_labels = {
        "change_username": f'{target} changed username to "{details.get("new", "?")}"',
        "switch_auth_method": f"{target} switched auth method to {details.get('new', '?')}",
        "reset_credentials": f"{target} reset their credentials",
        "delete_account": f'{details.get("username", target)} deleted their account',
    }
    description = action_labels.get(action, f"{action} on {target}")
    subject = f"[Audiobook Library] Account change: {description}"
    body = (
        f"{description} at {details.get('timestamp', 'unknown time')}.\n\n"
        f"Actor: {actor}\n"
        f"Review in Back Office \u2192 Users \u2192 Audit Log."
    )
    return subject, body


def _send_notification_email(to_email: str, subject: str, body: str) -> bool:
    """Send a notification email via configured SMTP."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "noreply@localhost")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error("Failed to send audit notification to %s: %s", to_email, e)
        return False
```

- [ ] **Step 2: Commit**

```bash
git add library/auth/audit.py
git commit -m "feat(auth): add admin notification helpers for critical actions"
```

---

## Task 4: Last-Admin Guard (TDD)

**Files:**
- Create: `library/tests/test_last_admin_guard.py`
- Modify: `library/auth/models.py` (add `count_admins()` to UserRepository)

- [ ] **Step 1: Write failing test**

Create `library/tests/test_last_admin_guard.py`:

```python
"""Tests for last-admin deletion guard."""
import pytest
from auth.models import UserRepository


class TestLastAdminGuard:
    def test_count_admins_returns_correct_count(self, auth_db):
        from auth.models import User, AuthType
        repo = UserRepository(auth_db)
        User(username="admin1", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        User(username="admin2", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        User(username="regular", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=False).save(auth_db)
        assert repo.count_admins() == 2

    def test_is_last_admin_true_when_only_one(self, auth_db):
        from auth.models import User, AuthType
        repo = UserRepository(auth_db)
        admin = User(username="sole_admin", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        assert repo.is_last_admin(admin.id) is True

    def test_is_last_admin_false_when_multiple(self, auth_db):
        from auth.models import User, AuthType
        repo = UserRepository(auth_db)
        admin1 = User(username="admin1", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        User(username="admin2", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        assert repo.is_last_admin(admin1.id) is False

    def test_is_last_admin_false_for_non_admin(self, auth_db):
        from auth.models import User, AuthType
        repo = UserRepository(auth_db)
        User(username="admin1", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        regular = User(username="regular", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=False).save(auth_db)
        assert repo.is_last_admin(regular.id) is False

    def test_cannot_revoke_last_admin_role(self, auth_db):
        """Revoking admin from the sole admin should also be blocked."""
        from auth.models import User, AuthType
        repo = UserRepository(auth_db)
        admin = User(username="onlyadmin", auth_type=AuthType.TOTP, auth_credential=b"s", is_admin=True).save(auth_db)
        assert repo.is_last_admin(admin.id) is True
        # The endpoint must check is_last_admin before allowing role toggle
```

- [ ] **Step 2: Run tests to verify failure**

```bash
python -m pytest library/tests/test_last_admin_guard.py -v
```

Expected: AttributeError (`count_admins` / `is_last_admin` not found)

- [ ] **Step 3: Implement count_admins and is_last_admin in UserRepository**

Add to `UserRepository` in `models.py`:

```python
def count_admins(self) -> int:
    """Count the number of admin users."""
    with self.db.connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1"
        ).fetchone()[0]

def is_last_admin(self, user_id: int) -> bool:
    """Check if this user is the only admin."""
    user = self.get_by_id(user_id)
    if not user or not user.is_admin:
        return False
    return self.count_admins() == 1
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest library/tests/test_last_admin_guard.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add library/auth/models.py library/tests/test_last_admin_guard.py
git commit -m "feat(auth): add last-admin guard with tests"
```

---

## Task 4b: Test Fixtures for Auth Endpoint Tests

**Files:**
- Modify: `library/tests/conftest.py`

The existing `conftest.py` has `auth_app` (session-scoped Flask app with auth DB) and generic `app_client`, but no pre-authenticated client fixtures. All endpoint tests in Tasks 5-7 depend on these.

- [ ] **Step 1: Add auth test fixtures to conftest.py**

Add these fixtures (adapt to existing conftest patterns — the app is `auth_app`, the DB is `auth_app.auth_db`):

```python
@pytest.fixture
def auth_db(auth_app):
    """Auth database instance."""
    return auth_app.auth_db


@pytest.fixture
def admin_client(auth_app):
    """Test client authenticated as an admin user."""
    from auth.models import User, AuthType
    db = auth_app.auth_db
    admin = User(
        username="testadmin", auth_type=AuthType.TOTP,
        auth_credential=b"secret", is_admin=True, can_download=True,
    ).save(db)
    client = auth_app.test_client()
    # Set session cookie — follow existing test pattern for auth
    with client.session_transaction() as sess:
        sess["user_id"] = admin.id
        sess["username"] = admin.username
    return client


@pytest.fixture
def user_client(auth_app, test_user):
    """Test client authenticated as a regular (non-admin) user."""
    client = auth_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = test_user.id
        sess["username"] = test_user.username
    return client


@pytest.fixture
def anon_client(auth_app):
    """Test client with no session (unauthenticated)."""
    return auth_app.test_client()


@pytest.fixture
def test_user(auth_app):
    """A regular (non-admin) TOTP user."""
    from auth.models import User, AuthType
    user = User(
        username="regularuser", auth_type=AuthType.TOTP,
        auth_credential=b"secret", is_admin=False, can_download=True,
    ).save(auth_app.auth_db)
    return user


@pytest.fixture
def sole_admin(auth_app):
    """An admin user who is the ONLY admin in the database."""
    from auth.models import User, AuthType
    # Note: ensure no other admins exist in test DB
    admin = User(
        username="soleadmin", auth_type=AuthType.TOTP,
        auth_credential=b"secret", is_admin=True,
    ).save(auth_app.auth_db)
    return admin


@pytest.fixture
def sole_admin_client(auth_app, sole_admin):
    """Test client authenticated as the sole admin."""
    client = auth_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = sole_admin.id
        sess["username"] = sole_admin.username
    return client


@pytest.fixture
def test_magic_link_user(auth_app):
    """A Magic Link user."""
    from auth.models import User, AuthType
    user = User(
        username="mluser", auth_type=AuthType.MAGIC_LINK,
        auth_credential=b"", recovery_email="ml@test.com",
    ).save(auth_app.auth_db)
    return user


@pytest.fixture
def magic_link_user_client(auth_app, test_magic_link_user):
    """Test client authenticated as a magic link user."""
    client = auth_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = test_magic_link_user.id
        sess["username"] = test_magic_link_user.username
    return client


@pytest.fixture
def logged_in_user(auth_app):
    """A user whose last_login is set (not NULL)."""
    from auth.models import User, AuthType
    from datetime import datetime
    user = User(
        username="loggedinuser", auth_type=AuthType.TOTP,
        auth_credential=b"secret", last_login=datetime.now(),
    ).save(auth_app.auth_db)
    return user
```

**IMPORTANT**: Check how `session_transaction()` works with the existing auth middleware. The `@login_required` decorator in `auth.py:187` reads `session["user_id"]` — verify the fixture sets the right key. Adapt if the existing tests use a different session setup pattern.

- [ ] **Step 2: Verify fixtures work**

```bash
python -m pytest library/tests/conftest.py --co -v  # collect-only to verify fixtures are loadable
```

- [ ] **Step 3: Commit**

```bash
git add library/tests/conftest.py
git commit -m "test: add auth client fixtures for user management tests"
```

---

## Task 5: Admin Create User Endpoint (TDD)

**Files:**
- Create: `library/tests/test_admin_user_management.py`
- Modify: `library/backend/api_modular/auth.py`

- [ ] **Step 1: Write failing tests for POST /auth/admin/users/create**

Create `library/tests/test_admin_user_management.py`:

```python
"""Tests for admin user management endpoints."""
import json
import pytest


class TestAdminCreateUser:
    def test_create_totp_user(self, admin_client):
        """Admin creates a TOTP user — gets QR data back."""
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "newuser",
            "auth_method": "totp",
            "is_admin": False,
            "can_download": True,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["user_id"] is not None
        assert "secret" in data["setup_data"]
        assert "qr_uri" in data["setup_data"]
        assert "manual_key" in data["setup_data"]

    def test_create_magic_link_user_requires_email(self, admin_client):
        """Magic Link user requires email."""
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "mluser",
            "auth_method": "magic_link",
        })
        assert resp.status_code == 400
        assert "email" in resp.get_json()["error"].lower()

    def test_create_magic_link_user_with_email(self, admin_client):
        """Magic Link user with email succeeds."""
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "mluser",
            "auth_method": "magic_link",
            "email": "ml@example.com",
        })
        assert resp.status_code == 201

    def test_create_passkey_user_gets_claim_url(self, admin_client):
        """Passkey user gets a claim token and URL."""
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "pkuser",
            "auth_method": "passkey",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert "claim_token" in data["setup_data"]
        assert "claim_url" in data["setup_data"]

    def test_create_duplicate_username_fails(self, admin_client):
        """Duplicate username returns 409."""
        admin_client.post("/auth/admin/users/create", json={
            "username": "dupeuser", "auth_method": "totp",
        })
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "dupeuser", "auth_method": "totp",
        })
        assert resp.status_code == 409

    def test_create_user_logs_audit_entry(self, admin_client, auth_db):
        """Creating a user creates an audit log entry."""
        from auth.audit import AuditLogRepository
        admin_client.post("/auth/admin/users/create", json={
            "username": "audituser", "auth_method": "totp",
        })
        audit_repo = AuditLogRepository(auth_db)
        entries = audit_repo.list(action_filter="create_user")
        assert len(entries) >= 1
        assert "audituser" in entries[0].details

    def test_create_user_requires_admin(self, user_client):
        """Non-admin gets 403."""
        resp = user_client.post("/auth/admin/users/create", json={
            "username": "blocked", "auth_method": "totp",
        })
        assert resp.status_code == 403

    def test_username_validation_too_short(self, admin_client):
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "ab", "auth_method": "totp",
        })
        assert resp.status_code == 400

    def test_username_validation_too_long(self, admin_client):
        resp = admin_client.post("/auth/admin/users/create", json={
            "username": "a" * 25, "auth_method": "totp",
        })
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify failure**

```bash
python -m pytest library/tests/test_admin_user_management.py -v
```

Expected: 404 (endpoint doesn't exist)

- [ ] **Step 3: Implement POST /auth/admin/users/create**

Add to `auth.py` after the existing `list_users()` endpoint (~line 4047):

```python
@auth_bp.route("/admin/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    """Create a new user with specified auth method (admin only)."""
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    auth_method = data.get("auth_method", "totp")
    email = data.get("email", "").strip() or None
    is_admin = bool(data.get("is_admin", False))
    can_download = bool(data.get("can_download", False))

    # Validate username
    if len(username) < 3 or len(username) > 24:
        return jsonify({"error": "Username must be 3-24 characters"}), 400
    if not all(c.isalnum() or c == "-" for c in username):
        return jsonify({"error": "Username must be alphanumeric or hyphens"}), 400

    # Validate auth method
    if auth_method not in ("totp", "magic_link", "passkey"):
        return jsonify({"error": "Invalid auth method"}), 400
    if auth_method == "magic_link" and not email:
        return jsonify({"error": "Email is required for Magic Link auth"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    # Check for duplicate username
    existing = user_repo.get_by_username(username)
    if existing:
        return jsonify({"error": "Username already exists"}), 409

    setup_data = {}

    if auth_method == "totp":
        from auth.totp import setup_totp
        secret_bytes, base32_secret, provisioning_uri = setup_totp(username)
        user = user_repo.create(
            username=username,
            auth_type="totp",
            auth_credential=secret_bytes,
            is_admin=is_admin,
            can_download=can_download,
        )
        if email:
            user_repo.update_email(user.id, email)
        setup_data = {
            "secret": base32_secret,
            "qr_uri": provisioning_uri,
            "manual_key": base32_secret,
        }

    elif auth_method == "magic_link":
        user = user_repo.create(
            username=username,
            auth_type="magic_link",
            auth_credential=b"",
            is_admin=is_admin,
            can_download=can_download,
        )
        user_repo.update_email(user.id, email)

    elif auth_method == "passkey":
        import secrets as secrets_mod
        claim_token = secrets_mod.token_urlsafe(32)
        user = user_repo.create(
            username=username,
            auth_type="passkey",
            auth_credential=b"pending",
            is_admin=is_admin,
            can_download=can_download,
        )
        if email:
            user_repo.update_email(user.id, email)
        # Store claim token (reuse existing invite mechanism)
        _store_claim_token(db, user.id, claim_token)
        host = request.headers.get("X-Forwarded-Host", request.host)
        proto = request.headers.get("X-Forwarded-Proto", "https")
        setup_data = {
            "claim_token": claim_token,
            "claim_url": f"{proto}://{host}/auth/claim?token={claim_token}",
            "expires_at": _get_claim_expiry(),
        }

    # Audit log
    from auth.audit import AuditLogRepository, notify_admins
    audit_repo = AuditLogRepository(db)
    actor = get_current_user()
    details = {
        "auth_method": auth_method,
        "is_admin": is_admin,
        "can_download": can_download,
        "actor_username": actor.username,
        "target_username": username,
    }
    audit_repo.log(actor_id=actor.id, target_id=user.id, action="create_user", details=details)

    return jsonify({"user_id": user.id, "setup_data": setup_data}), 201
```

**Existing patterns to reuse:**
- `get_current_user()` is at `auth.py:135` — returns the authenticated User from session
- Claim tokens: The existing invite flow at `auth.py:1062-1081` shows the pattern — `generate_verification_token()` creates the token, `hash_token()` hashes it, and `request_repo.create()` stores it via `PendingRegistrationRepository`. For passkey creation, reuse this same flow — create a `PendingRegistration` entry with the claim token hash.
- `_get_claim_expiry()`: Check `PendingRegistration.create()` in `models.py:1218` for the default expiry (likely 72h). Reuse the same duration.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest library/tests/test_admin_user_management.py -v
```

Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add library/backend/api_modular/auth.py library/tests/test_admin_user_management.py
git commit -m "feat(auth): add admin create user endpoint with tests"
```

---

## Task 6: Admin User Management Endpoints (TDD)

**Files:**
- Modify: `library/tests/test_admin_user_management.py` (add tests)
- Modify: `library/backend/api_modular/auth.py` (add endpoints)

These endpoints share a pattern — test each, implement, verify, commit.

- [ ] **Step 1: Write tests for PUT /auth/admin/users/<id>/username**

```python
class TestAdminChangeUsername:
    def test_change_username(self, admin_client, test_user):
        resp = admin_client.put(f"/auth/admin/users/{test_user.id}/username",
                                json={"username": "renamed"})
        assert resp.status_code == 200
        assert resp.get_json()["username"] == "renamed"

    def test_change_username_duplicate(self, admin_client, test_user):
        admin_client.post("/auth/admin/users/create",
                          json={"username": "taken", "auth_method": "totp"})
        resp = admin_client.put(f"/auth/admin/users/{test_user.id}/username",
                                json={"username": "taken"})
        assert resp.status_code == 409
```

- [ ] **Step 2: Write tests for PUT /auth/admin/users/<id>/email**

```python
class TestAdminChangeEmail:
    def test_change_email(self, admin_client, test_user):
        resp = admin_client.put(f"/auth/admin/users/{test_user.id}/email",
                                json={"email": "new@example.com"})
        assert resp.status_code == 200

    def test_change_email_empty_clears(self, admin_client, test_user):
        resp = admin_client.put(f"/auth/admin/users/{test_user.id}/email",
                                json={"email": ""})
        assert resp.status_code == 200
```

- [ ] **Step 3: Write tests for PUT /auth/admin/users/<id>/roles**

```python
class TestAdminToggleRoles:
    def test_toggle_admin(self, admin_client, test_user):
        resp = admin_client.put(f"/auth/admin/users/{test_user.id}/roles",
                                json={"is_admin": True})
        assert resp.status_code == 200
        assert resp.get_json()["is_admin"] is True

    def test_toggle_download(self, admin_client, test_user):
        resp = admin_client.put(f"/auth/admin/users/{test_user.id}/roles",
                                json={"can_download": False})
        assert resp.status_code == 200
        assert resp.get_json()["can_download"] is False
```

- [ ] **Step 4: Write tests for PUT /auth/admin/users/<id>/auth-method**

```python
class TestAdminSwitchAuth:
    def test_switch_to_totp(self, admin_client, test_magic_link_user):
        resp = admin_client.put(
            f"/auth/admin/users/{test_magic_link_user.id}/auth-method",
            json={"auth_method": "totp"})
        assert resp.status_code == 200
        assert "setup_data" in resp.get_json()
```

- [ ] **Step 5: Write tests for POST /auth/admin/users/<id>/reset-credentials**

```python
class TestAdminResetCredentials:
    def test_reset_totp_returns_new_secret(self, admin_client, test_user):
        resp = admin_client.post(f"/auth/admin/users/{test_user.id}/reset-credentials")
        assert resp.status_code == 200
        assert "setup_data" in resp.get_json()
```

- [ ] **Step 6: Write tests for DELETE /auth/admin/users/<id>**

```python
class TestAdminDeleteUser:
    def test_delete_user(self, admin_client, test_user, auth_db):
        resp = admin_client.delete(f"/auth/admin/users/{test_user.id}")
        assert resp.status_code == 200
        from auth.models import UserRepository
        assert UserRepository(auth_db).get_by_id(test_user.id) is None

    def test_cannot_delete_last_admin(self, admin_client, sole_admin):
        resp = admin_client.delete(f"/auth/admin/users/{sole_admin.id}")
        assert resp.status_code == 409
        assert "last admin" in resp.get_json()["error"].lower()
```

- [ ] **Step 7: Write tests for GET /auth/admin/audit-log**

```python
class TestAdminAuditLog:
    def test_get_audit_log(self, admin_client):
        # Create a user to generate an audit entry
        admin_client.post("/auth/admin/users/create",
                          json={"username": "logtest", "auth_method": "totp"})
        resp = admin_client.get("/auth/admin/audit-log")
        assert resp.status_code == 200
        assert len(resp.get_json()["entries"]) >= 1

    def test_audit_log_filter_by_action(self, admin_client):
        admin_client.post("/auth/admin/users/create",
                          json={"username": "filtertest", "auth_method": "totp"})
        resp = admin_client.get("/auth/admin/audit-log?action=create_user")
        assert resp.status_code == 200
        for entry in resp.get_json()["entries"]:
            assert entry["action"] == "create_user"
```

- [ ] **Step 8: Write tests for GET /auth/admin/users/<id>/setup-info**

```python
class TestAdminSetupInfo:
    def test_get_setup_info_before_login(self, admin_client):
        resp = admin_client.post("/auth/admin/users/create",
                                 json={"username": "setuptest", "auth_method": "totp"})
        user_id = resp.get_json()["user_id"]
        resp = admin_client.get(f"/auth/admin/users/{user_id}/setup-info")
        assert resp.status_code == 200
        assert "secret" in resp.get_json()["setup_data"]

    def test_setup_info_redacted_after_login(self, admin_client, logged_in_user):
        resp = admin_client.get(f"/auth/admin/users/{logged_in_user.id}/setup-info")
        assert resp.status_code == 404
```

- [ ] **Step 9: Run all tests to verify failure**

```bash
python -m pytest library/tests/test_admin_user_management.py -v
```

- [ ] **Step 10: Implement all admin endpoints**

Add each endpoint to `auth.py`, following the same pattern as Task 5's create endpoint. Each endpoint:
1. Validates input
2. Performs the action via `UserRepository`
3. Logs to `AuditLogRepository`
4. Calls `notify_admins()` for critical actions
5. Returns the updated user + audit entry ID

- [ ] **Step 11: Run tests to verify they pass**

```bash
python -m pytest library/tests/test_admin_user_management.py -v
```

Expected: All tests PASS

- [ ] **Step 12: Commit**

```bash
git add library/backend/api_modular/auth.py library/tests/test_admin_user_management.py
git commit -m "feat(auth): add admin user management endpoints with tests"
```

---

## Task 7: Self-Service Endpoints (TDD)

**Files:**
- Create: `library/tests/test_self_service.py`
- Modify: `library/backend/api_modular/auth.py`

- [ ] **Step 1: Write tests for all self-service endpoints**

Create `library/tests/test_self_service.py`:

```python
"""Tests for user self-service endpoints."""
import pytest


class TestGetAccount:
    def test_get_own_profile(self, user_client, test_user):
        resp = user_client.get("/auth/account")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["username"] == test_user.username
        assert "auth_type" in data

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.get("/auth/account")
        assert resp.status_code == 401


class TestChangeOwnUsername:
    def test_change_own_username(self, user_client):
        resp = user_client.put("/auth/account/username",
                               json={"username": "mynewname"})
        assert resp.status_code == 200
        assert resp.get_json()["username"] == "mynewname"

    def test_change_username_triggers_audit(self, user_client, auth_db):
        user_client.put("/auth/account/username", json={"username": "audited"})
        from auth.audit import AuditLogRepository
        entries = AuditLogRepository(auth_db).list(action_filter="change_username")
        assert len(entries) >= 1


class TestChangeOwnEmail:
    def test_change_own_email(self, user_client):
        resp = user_client.put("/auth/account/email",
                               json={"email": "me@new.com"})
        assert resp.status_code == 200


class TestSwitchOwnAuth:
    def test_initiate_switch_to_totp(self, magic_link_user_client):
        resp = magic_link_user_client.put("/auth/account/auth-method",
                                          json={"auth_method": "totp"})
        assert resp.status_code == 200
        assert "setup_data" in resp.get_json()


class TestResetOwnCredentials:
    def test_reset_totp_credentials(self, user_client):
        resp = user_client.post("/auth/account/reset-credentials")
        assert resp.status_code == 200
        assert "setup_data" in resp.get_json()


class TestDeleteOwnAccount:
    def test_delete_own_account(self, user_client, test_user, auth_db):
        resp = user_client.delete("/auth/account")
        assert resp.status_code == 200
        from auth.models import UserRepository
        assert UserRepository(auth_db).get_by_id(test_user.id) is None

    def test_last_admin_cannot_self_delete(self, sole_admin_client, sole_admin):
        resp = sole_admin_client.delete("/auth/account")
        assert resp.status_code == 409
        assert "last admin" in resp.get_json()["error"].lower()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
python -m pytest library/tests/test_self_service.py -v
```

- [ ] **Step 3: Implement self-service endpoints**

Add to `auth.py`. These share backend logic with admin endpoints but use `@login_required` and operate on the authenticated user's own account:

```python
@auth_bp.route("/account", methods=["GET"])
@login_required
def get_own_account():
    """Get authenticated user's profile."""
    user = get_current_user()
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.recovery_email,
        "auth_type": user.auth_type.value,  # AuthType is an enum — serialize to string
        "is_admin": user.is_admin,
        "can_download": user.can_download,
        "created_at": str(user.created_at) if user.created_at else None,
    })


@auth_bp.route("/account/username", methods=["PUT"])
@login_required
def change_own_username():
    """Change authenticated user's username."""
    # Same logic as admin endpoint but actor = target = current user
    # ... validate, update, audit log, notify admins ...


@auth_bp.route("/account/email", methods=["PUT"])
@login_required
def change_own_email():
    # ...


@auth_bp.route("/account/auth-method", methods=["PUT"])
@login_required
def switch_own_auth_method():
    # Two-step: initiate (returns setup_data) then confirm
    # ...


@auth_bp.route("/account/reset-credentials", methods=["POST"])
@login_required
def reset_own_credentials():
    # ...


@auth_bp.route("/account", methods=["DELETE"])
@login_required
def delete_own_account():
    user = get_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    if user_repo.is_last_admin(user.id):
        return jsonify({"error": "Cannot delete the last admin account"}), 409

    # Audit log BEFORE deletion (capture username)
    from auth.audit import AuditLogRepository, notify_admins
    audit_repo = AuditLogRepository(db)
    details = {"username": user.username, "actor_username": user.username, "target_username": user.username}
    audit_repo.log(actor_id=user.id, target_id=user.id, action="delete_account", details=details)
    notify_admins("delete_account", details, db)

    user_repo.delete(user.id)
    # Clear session
    session.clear()
    return jsonify({"message": "Account deleted"})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest library/tests/test_self_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add library/backend/api_modular/auth.py library/tests/test_self_service.py
git commit -m "feat(auth): add self-service account endpoints with tests"
```

---

## Task 8: Back Office USERS Tab (UI)

**Files:**
- Modify: `library/web-v2/utilities.html` (add USERS tab + markup)
- Modify: `library/web-v2/js/utilities.js` (add USERS tab logic)
- Modify: `library/web-v2/css/utilities.css` (styles for new tab content)

- [ ] **Step 1: Add USERS tab button to utilities.html**

In the `<div class="cabinet-tabs">` section (~line 118), add before the System tab:

```html
<button class="cabinet-tab" data-section="users" title="Manage user accounts, permissions, and authentication">
    Users
    <span class="notification-badge" id="users-badge" hidden>0</span>
</button>
```

- [ ] **Step 2: Add USERS section markup to utilities.html**

Add before the `system-section`:

```html
<section class="drawer-content" id="users-section">
    <div class="section-header">
        <h2>User Management</h2>
        <p class="section-desc">Create, manage, and audit user accounts</p>
    </div>

    <div class="card-catalog">
        <!-- Create User Card -->
        <div class="catalog-card wide">
            <div class="card-header">
                <span>Create New User</span>
            </div>
            <div class="card-body">
                <form id="create-user-form" class="admin-form">
                    <div class="form-row">
                        <label for="new-username">Username</label>
                        <input type="text" id="new-username" name="username"
                               minlength="3" maxlength="24" pattern="[a-zA-Z0-9-]+"
                               required placeholder="3-24 chars, alphanumeric or hyphens"
                               title="Username: 3-24 characters, letters, numbers, and hyphens">
                    </div>
                    <div class="form-row">
                        <label for="new-email">Email (optional)</label>
                        <input type="email" id="new-email" name="email"
                               placeholder="Required for Magic Link auth"
                               title="Email address for Magic Link authentication or recovery">
                    </div>
                    <div class="form-row">
                        <label>Auth Method</label>
                        <div class="radio-group">
                            <label title="Time-based one-time password (authenticator app)">
                                <input type="radio" name="auth_method" value="totp" checked> TOTP
                            </label>
                            <label title="Login link sent via email">
                                <input type="radio" name="auth_method" value="magic_link"> Magic Link
                            </label>
                            <label title="FIDO2 hardware key or biometric">
                                <input type="radio" name="auth_method" value="passkey"> Passkey
                            </label>
                        </div>
                    </div>
                    <div class="form-row">
                        <label>Roles</label>
                        <div class="checkbox-group">
                            <label title="Grant administrative access to Back Office">
                                <input type="checkbox" name="is_admin"> Admin
                            </label>
                            <label title="Allow downloading audiobook files">
                                <input type="checkbox" name="can_download"> Download
                            </label>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary"
                            title="Create the user account">Create User</button>
                </form>
                <!-- Setup data panel (shown after creation) -->
                <div id="setup-data-panel" hidden>
                    <div id="setup-qr-container"></div>
                    <div id="setup-manual-key"></div>
                    <div id="setup-claim-url"></div>
                    <button id="download-setup-btn" class="btn btn-secondary" hidden
                            title="Download QR code as PNG image">Download QR</button>
                </div>
            </div>
        </div>

        <!-- User List Card -->
        <div class="catalog-card wide">
            <div class="card-header">
                <span>All Users</span>
                <span class="badge" id="user-count-badge">0</span>
            </div>
            <div class="card-body">
                <table class="data-table" id="users-table">
                    <thead>
                        <tr>
                            <th>Username</th>
                            <th>Auth</th>
                            <th>Roles</th>
                            <th>Created</th>
                            <th>Last Login</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="users-table-body"></tbody>
                </table>
            </div>
        </div>

        <!-- Audit Log Card -->
        <div class="catalog-card wide">
            <div class="card-header">
                <span>Audit Log</span>
                <select id="audit-action-filter" title="Filter audit log by action type">
                    <option value="">All Actions</option>
                    <option value="create_user">Create User</option>
                    <option value="change_username">Username Change</option>
                    <option value="change_email">Email Change</option>
                    <option value="switch_auth_method">Auth Method Switch</option>
                    <option value="reset_credentials">Credential Reset</option>
                    <option value="toggle_roles">Role Change</option>
                    <option value="delete_account">Account Deletion</option>
                </select>
            </div>
            <div class="card-body">
                <table class="data-table" id="audit-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Actor</th>
                            <th>Target</th>
                            <th>Action</th>
                            <th>Details</th>
                        </tr>
                    </thead>
                    <tbody id="audit-table-body"></tbody>
                </table>
                <div class="pagination" id="audit-pagination"></div>
            </div>
        </div>
    </div>
</section>
```

- [ ] **Step 3: Add USERS tab JS logic to utilities.js**

Add initialization call in the `initTabs()` function, and add these functions:

```javascript
// ── Users Tab ──────────────────────────────────────────

async function initUsersSection() {
    loadUserList();
    loadAuditLog();
    initCreateUserForm();
    loadUnseenBadge();
}

async function loadUserList() { /* fetch GET /auth/admin/users, render table */ }
async function loadAuditLog(action, page) { /* fetch GET /auth/admin/audit-log, render */ }
function initCreateUserForm() { /* form submit → POST /auth/admin/users/create */ }
function renderSetupData(data, username) { /* show QR/claim/manual key */ }
function downloadQrPng(username) { /* canvas → PNG download as username_MMDD-HMS.png */ }
async function loadUnseenBadge() { /* fetch unseen count, update badge */ }

// User action handlers (called from table action buttons)
async function adminChangeUsername(userId) { /* prompt → PUT */ }
async function adminChangeEmail(userId) { /* prompt → PUT */ }
async function adminToggleRoles(userId, field, value) { /* PUT */ }
async function adminSwitchAuth(userId) { /* modal → PUT */ }
async function adminResetCredentials(userId) { /* confirm → POST */ }
async function adminDeleteUser(userId) { /* confirm → DELETE */ }
async function adminViewSetupInfo(userId) { /* GET setup-info */ }
```

- [ ] **Step 4: Move existing user management out of System tab**

Remove the user-related cards from the System tab section. The System tab keeps only: Service Status, Upgrade, Email Config.

- [ ] **Step 5: Add notification badge CSS**

```css
.notification-badge {
    background: var(--accent-gold, #d4a843);
    color: var(--bg-dark, #1a1a2e);
    border-radius: 50%;
    padding: 0.1em 0.45em;
    font-size: 0.7em;
    font-weight: bold;
    margin-left: 0.3em;
    vertical-align: super;
}

.audit-critical {
    border-left: 3px solid #d4a843;
}
```

- [ ] **Step 6: Test manually on dev server**

```bash
cd /hddRaid1/ClaudeCodeProjects/Audiobook-Manager/library
python -m backend.api_server  # or however dev server starts
```

Open the Back Office, verify USERS tab appears, create user form works, audit log populates.

- [ ] **Step 7: Commit**

```bash
git add library/web-v2/utilities.html library/web-v2/js/utilities.js library/web-v2/css/utilities.css
git commit -m "feat(ui): add USERS tab to Back Office with user management and audit log"
```

---

## Task 9: Shell "My Account" Modal

**Files:**
- Modify: `library/web-v2/shell.html` (add header bar + modal markup)
- Modify: `library/web-v2/shell.css` (header + modal styles)
- Create: `library/web-v2/js/account.js` (modal logic)

- [ ] **Step 1: Add header bar to shell.html**

Above the iframe, add:

```html
<div id="shell-header">
    <span class="header-title">Audiobook Library</span>
    <button id="my-account-btn" class="header-btn" title="My Account settings"
            onclick="openAccountModal()">
        <span id="account-username">Account</span>
    </button>
</div>
```

- [ ] **Step 2: Add My Account modal markup to shell.html**

```html
<div class="modal" id="account-modal">
    <div class="modal-content modal-small">
        <div class="modal-header">
            <h2>My Account</h2>
            <button class="modal-close" onclick="closeAccountModal()"
                    title="Close My Account">&times;</button>
        </div>
        <div class="modal-body">
            <!-- Profile Section -->
            <div class="account-section">
                <h3>Profile</h3>
                <div class="account-field">
                    <label>Username</label>
                    <span id="acct-username" class="editable-field"
                          title="Click to edit username"></span>
                    <input id="acct-username-input" class="edit-input" hidden
                           maxlength="24" title="New username">
                    <button class="btn-inline" id="acct-username-save" hidden
                            title="Save username change">Save</button>
                </div>
                <div class="account-field">
                    <label>Email</label>
                    <span id="acct-email" class="editable-field"
                          title="Click to edit email"></span>
                    <input id="acct-email-input" class="edit-input" hidden
                           type="email" title="New email address">
                    <button class="btn-inline" id="acct-email-save" hidden
                            title="Save email change">Save</button>
                </div>
                <div class="account-field">
                    <label>Member since</label>
                    <span id="acct-created"></span>
                </div>
            </div>

            <!-- Authentication Section -->
            <div class="account-section">
                <h3>Authentication</h3>
                <div class="account-field">
                    <label>Current method</label>
                    <span id="acct-auth-badge" class="auth-badge"></span>
                </div>
                <button class="btn btn-secondary" onclick="initAuthSwitch()"
                        title="Switch to a different authentication method">Switch Method</button>
                <button class="btn btn-secondary" onclick="resetCredentials()"
                        title="Reset your authentication credentials">Reset Credentials</button>
                <div id="auth-switch-panel" hidden>
                    <!-- Populated dynamically during switch flow -->
                </div>
            </div>

            <!-- Danger Zone -->
            <div class="account-section danger-zone">
                <h3>Danger Zone</h3>
                <button class="btn btn-danger" onclick="deleteOwnAccount()"
                        title="Permanently delete your account">Delete My Account</button>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 3: Add shell header + modal CSS to shell.css**

```css
#shell-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.3em 1em;
    background: var(--bg-dark, #1a1a2e);
    border-bottom: 1px solid var(--border-gold, #d4a843);
    z-index: 100;
}

.header-title {
    font-family: var(--font-display, serif);
    color: var(--accent-gold, #d4a843);
    font-size: 0.95em;
}

.header-btn {
    background: transparent;
    border: 1px solid var(--border-gold, #d4a843);
    color: var(--text-light, #e0d6c8);
    padding: 0.25em 0.75em;
    cursor: pointer;
    font-size: 0.85em;
}
.header-btn:hover {
    background: rgba(212, 168, 67, 0.15);
}

/* Account modal sections */
.account-section { margin-bottom: 1.5em; }
.account-section h3 { color: var(--accent-gold); margin-bottom: 0.5em; }
.account-field { display: flex; align-items: center; gap: 0.5em; margin: 0.4em 0; }
.account-field label { min-width: 6em; color: var(--text-muted); }
.editable-field { cursor: pointer; border-bottom: 1px dashed var(--text-muted); }
.editable-field:hover { color: var(--accent-gold); }
.danger-zone { border-top: 1px solid #8b0000; padding-top: 1em; margin-top: 2em; }
.btn-danger { background: #8b0000; color: white; border: none; padding: 0.4em 1em; cursor: pointer; }
.btn-danger:hover { background: #a50000; }
```

- [ ] **Step 4: Create account.js**

Create `library/web-v2/js/account.js`:

```javascript
/**
 * My Account modal — self-service account management.
 * Loaded in shell.html, operates via /auth/account/* endpoints.
 */
(function () {
    "use strict";

    var API_BASE = "";  // Same origin

    function openAccountModal() {
        loadAccountData();
        document.getElementById("account-modal").classList.add("show");
    }

    function closeAccountModal() {
        document.getElementById("account-modal").classList.remove("show");
    }

    async function loadAccountData() {
        try {
            var resp = await fetch(API_BASE + "/auth/account", { credentials: "same-origin" });
            if (!resp.ok) throw new Error("Not authenticated");
            var data = await resp.json();
            document.getElementById("acct-username").textContent = data.username;
            document.getElementById("acct-email").textContent = data.email || "(none)";
            document.getElementById("acct-created").textContent = data.created_at || "Unknown";
            document.getElementById("acct-auth-badge").textContent = data.auth_type.toUpperCase();
            document.getElementById("account-username").textContent = data.username;
        } catch (e) {
            // Not logged in — hide the button
            document.getElementById("my-account-btn").hidden = true;
        }
    }

    // Inline editing for username
    // ... (click handler → show input, save handler → PUT, update display)

    // Inline editing for email
    // ... (same pattern)

    // Auth method switch — two-step flow
    async function initAuthSwitch() { /* show radio panel, on select → PUT initiate */ }
    async function confirmAuthSwitch(data) { /* verify code/passkey → PUT confirm */ }

    // Credential reset
    async function resetCredentials() {
        if (!confirm("Reset your authentication credentials?")) return;
        // POST /auth/account/reset-credentials → show new setup data
    }

    // Account deletion with custom confirmation text
    async function deleteOwnAccount() {
        var msg = "This will permanently delete your account and all of your listening history. "
            + "You will likely experience intermittent swattings and harassment. "
            + "Can\u2019t be helped \u2014 this is normal and should be expected, because you "
            + "already knew who was behind this bullshit webapp when you signed up in the first place.";
        if (!confirm(msg)) return;
        // DELETE /auth/account → redirect to login
    }

    // Expose to global scope for onclick handlers
    window.openAccountModal = openAccountModal;
    window.closeAccountModal = closeAccountModal;
    window.initAuthSwitch = initAuthSwitch;
    window.resetCredentials = resetCredentials;
    window.deleteOwnAccount = deleteOwnAccount;

    // Load account data on page load to populate header username
    loadAccountData();
})();
```

- [ ] **Step 5: Add account.js script tag to shell.html**

```html
<script src="js/account.js?v=..."></script>
```

- [ ] **Step 6: Test manually**

Open shell via browser, verify header appears, My Account opens, profile loads, inline editing works.

- [ ] **Step 7: Commit**

```bash
git add library/web-v2/shell.html library/web-v2/shell.css library/web-v2/js/account.js
git commit -m "feat(ui): add My Account modal to shell header for self-service"
```

---

## Task 10: WebSocket Notification Push

**Files:**
- Modify: `library/backend/api_modular/auth.py` (emit WS event on audit log write)
- Modify: `library/web-v2/js/websocket.js` (handle `audit_notify` message type)
- Modify: `library/web-v2/js/utilities.js` (listen for event, update badge)

- [ ] **Step 1: Add audit_notify to WebSocket message handler**

In `websocket.js`, add to the `ws.onmessage` handler:

```javascript
} else if (msg.type === "audit_notify") {
    dispatch("audit-notify", msg);
}
```

- [ ] **Step 2: Add event listener in utilities.js**

```javascript
document.addEventListener("audit-notify", function () {
    loadUnseenBadge();
    if (currentSection === "users") {
        loadAuditLog();
    }
});
```

- [ ] **Step 3: Emit WebSocket message from backend after audit log write**

In the audit logging helper, after writing the log entry, broadcast to connected WebSocket clients:

```python
# In audit.py or auth.py, after audit_repo.log():
try:
    from backend.websocket_manager import broadcast
    broadcast({"type": "audit_notify", "action": action})
except Exception:
    pass  # WebSocket broadcast is best-effort
```

Check existing WebSocket broadcast pattern in the codebase — the maintenance announcement system likely has one.

- [ ] **Step 4: Commit**

```bash
git add library/web-v2/js/websocket.js library/web-v2/js/utilities.js library/backend/api_modular/auth.py
git commit -m "feat(ws): push audit notifications to connected admins via WebSocket"
```

---

## Task 11: Documentation Updates

**Files:**
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/AUTH_RUNBOOK.md`

- [ ] **Step 1: Update README.md**

Add to feature list:
- Web-based user management (admin create/edit/delete users)
- Self-service My Account (change username, email, auth method, credentials)
- Audit logging for all user management actions
- Admin notifications (in-app + email) for critical account changes

- [ ] **Step 2: Update ARCHITECTURE.md**

Add:
- USERS tab component in Back Office section
- My Account modal in Shell section
- audit_log table in Database section
- New API endpoints in Auth API section
- Admin notification flow

- [ ] **Step 3: Update AUTH_RUNBOOK.md**

Add sections:
- "Creating a user (admin)" — step-by-step for each auth method
- "Self-service account management" — what users can do from My Account
- "Audit log" — where to find it, what's logged
- "Admin notifications" — what triggers them, how to configure email

- [ ] **Step 4: Commit**

```bash
git add README.md docs/ARCHITECTURE.md docs/AUTH_RUNBOOK.md
git commit -m "docs: update README, architecture, and auth runbook for user management feature"
```

---

## Task 12: Integration Testing on VM

**Files:** None (testing only)

- [ ] **Step 1: Deploy to test-audiobook-cachyos**

```bash
./upgrade.sh --from-project . --remote 192.168.122.104 --yes
```

- [ ] **Step 2: Verify schema migration**

SSH to VM, check that `audit_log` table exists in the auth DB.

- [ ] **Step 3: Test admin create user flow through full external path**

Open `https://library.thebosco.club` (or equivalent test URL), log in as admin, go to Back Office → Users, create a TOTP user, verify QR code displays, download QR PNG.

- [ ] **Step 4: Test self-service flows**

Log in as the newly created user, open My Account, change username, change email, verify audit log populates.

- [ ] **Step 5: Test admin notifications**

Verify admin sees toast/badge after user self-service action. Verify email sent to admin (check Resend dashboard or admin inbox).

- [ ] **Step 6: Test last-admin guard**

Attempt to delete the only admin account — should be blocked with error.

- [ ] **Step 7: Test account deletion**

Create a throwaway user, log in as them, delete account, verify redirect to login, verify audit log entry survives.

- [ ] **Step 8: Verify WebSocket notification push**

Have two browser tabs — admin in Back Office, user in My Account. User changes username → admin's badge updates in real time.

---

## Task 13: Merge to Main

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest library/tests/ -v
```

- [ ] **Step 2: Run linters**

```bash
ruff check library/
ruff format --check library/
```

- [ ] **Step 3: Merge**

```bash
git checkout main
git merge user-management
git push
```

- [ ] **Step 4: Clean up branch**

```bash
git branch -d user-management
```
