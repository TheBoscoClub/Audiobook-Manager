"""
Auth Models for User Management, Sessions, and Notifications

These models provide a clean interface to the encrypted auth database.
All credential data is stored encrypted via SQLCipher.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

from .access_request import AccessRequest, AccessRequestRepository, AccessRequestStatus
from .database import AuthDatabase, generate_session_token, generate_verification_token, hash_token
from .messaging import (
    InboxMessage,
    InboxRepository,
    InboxStatus,
    Notification,
    NotificationRepository,
    NotificationType,
    ReplyMethod,
)
from .pending import (
    PendingRecovery,
    PendingRecoveryRepository,
    PendingRegistration,
    PendingRegistrationRepository,
)

# Backward-compat re-exports: the classes above used to live directly in this
# module. Importers like `from auth.models import Notification` must keep working.
__all__ = [
    "AccessRequest",
    "AccessRequestRepository",
    "AccessRequestStatus",
    "AuthDatabase",
    "InboxMessage",
    "InboxRepository",
    "InboxStatus",
    "Notification",
    "NotificationRepository",
    "NotificationType",
    "PendingRecovery",
    "PendingRecoveryRepository",
    "PendingRegistration",
    "PendingRegistrationRepository",
    "ReplyMethod",
    "generate_session_token",
    "generate_verification_token",
    "hash_token",
]

logger = logging.getLogger(__name__)


class AuthType(Enum):
    """Supported authentication methods."""

    PASSKEY = "passkey"
    FIDO2 = "fido2"
    TOTP = "totp"
    MAGIC_LINK = "magic_link"


# Valid values for User.multi_session column
MULTI_SESSION_DEFAULT = "default"
MULTI_SESSION_YES = "yes"
MULTI_SESSION_NO = "no"
_VALID_MULTI_SESSION = {MULTI_SESSION_DEFAULT, MULTI_SESSION_YES, MULTI_SESSION_NO}


@dataclass
class User:
    """
    Represents an authenticated user.

    Attributes:
        id: Database primary key
        username: Unique username (3-24 chars)
        auth_type: Authentication method
        auth_credential: Encrypted credential data (WebAuthn or TOTP secret)
        can_download: Permission to download audio files
        is_admin: Administrator flag
        created_at: Account creation timestamp
        last_login: Last successful login timestamp
        recovery_email: Optional recovery email (user's choice to store)
        recovery_phone: Optional recovery phone (user's choice to store)
        recovery_enabled: Whether user chose to enable contact-based recovery
    """

    id: Optional[int] = None
    username: str = ""
    auth_type: AuthType = AuthType.TOTP
    auth_credential: bytes = b""
    can_download: bool = True  # Default: allow downloads for offline listening
    is_admin: bool = False
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    recovery_email: Optional[str] = None
    recovery_phone: Optional[str] = None
    recovery_enabled: bool = False
    last_audit_seen_id: int = 0
    multi_session: str = "default"
    preferred_locale: str = "en"

    @staticmethod
    def _parse_timestamp(val) -> Optional[datetime]:
        """Parse a timestamp value from a database row."""
        return datetime.fromisoformat(val) if val else None

    @classmethod
    def _base_fields(cls, row: tuple) -> dict:
        """Extract base fields common to all schema versions (columns 0-7)."""
        return {
            "id": row[0],
            "username": row[1],
            "auth_type": AuthType(row[2]),
            "auth_credential": row[3] if row[3] else b"",
            "can_download": bool(row[4]),
            "is_admin": bool(row[5]),
            "created_at": cls._parse_timestamp(row[6]),
            "last_login": cls._parse_timestamp(row[7]),
        }

    @classmethod
    def from_row(cls, row: tuple) -> "User":
        """Create User from database row."""
        fields = cls._base_fields(row)

        # Recovery fields (schema v4+, columns 8-10)
        if len(row) >= 11:
            fields["recovery_email"] = row[8]
            fields["recovery_phone"] = row[9]
            fields["recovery_enabled"] = bool(row[10]) if row[10] is not None else False

        # Audit seen ID (schema v7+, column 11)
        if len(row) >= 12:
            fields["last_audit_seen_id"] = int(row[11]) if row[11] is not None else 0

        # Multi-session override (schema v9+, column 12)
        if len(row) >= 13:
            fields["multi_session"] = row[12] if row[12] is not None else "default"

        # Preferred locale (schema v10+, column 13)
        if len(row) >= 14:
            fields["preferred_locale"] = row[13] if row[13] is not None else "en"

        return cls(**fields)

    @property
    def ensured_id(self) -> int:
        """Return self.id narrowed to int. Panics if user was never saved.

        Use this at call sites where the User has been persisted and the
        Optional[int] type needs to be narrowed to int for type checking.
        Authentication decorators guarantee the current user is persisted,
        and .save() always sets .id after insert.
        """
        if self.id is None:
            raise RuntimeError("User.ensured_id accessed before .save()")
        return self.id

    def save(self, db: AuthDatabase) -> "User":
        """Save user to database (insert or update)."""
        if self.multi_session not in _VALID_MULTI_SESSION:
            raise ValueError(f"Invalid multi_session value: {self.multi_session!r}")
        with db.connection() as conn:
            if self.id is None:
                cursor = conn.execute(
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
                )
                self.id = cursor.lastrowid
                # Fetch the created_at timestamp
                cursor = conn.execute("SELECT created_at FROM users WHERE id = ?", (self.id,))
                self.created_at = datetime.fromisoformat(cursor.fetchone()[0])
            else:
                conn.execute(
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
                )
        return self

    def delete(self, db: AuthDatabase) -> bool:
        """Delete user from database. Returns True if deleted."""
        if self.id is None:
            return False
        with db.connection() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (self.id,))
        return True

    def update_last_login(self, db: AuthDatabase) -> None:
        """Update last_login to current time."""
        self.last_login = datetime.now()
        with db.connection() as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (self.last_login.isoformat(), self.id),
            )


class UserRepository:
    """Repository for User operations."""

    # Explicit column list — guarantees positional order matches from_row()
    # regardless of physical table column order (schema.sql vs ALTER TABLE).
    # nosemgrep: sqlalchemy-execute-raw-query
    _USER_SELECT = (
        "SELECT id, username, auth_type, auth_credential, can_download, is_admin, "
        "created_at, last_login, recovery_email, recovery_phone, recovery_enabled, "
        "last_audit_seen_id, multi_session, preferred_locale FROM users"
    )

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._USER_SELECT + " WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return User.from_row(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username (case-sensitive)."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._USER_SELECT + " WHERE username = ?", (username,))
            row = cursor.fetchone()
            return User.from_row(row) if row else None

    def username_exists(self, username: str) -> bool:
        """Check if username is taken."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            return cursor.fetchone() is not None

    def list_all(self, include_admin: bool = True) -> List[User]:
        """List all users."""
        with self.db.connection() as conn:
            if include_admin:
                # nosemgrep: sqlalchemy-execute-raw-query
                cursor = conn.execute(self._USER_SELECT + " ORDER BY username")
            else:
                # nosemgrep: sqlalchemy-execute-raw-query
                cursor = conn.execute(self._USER_SELECT + " WHERE is_admin = 0 ORDER BY username")
            return [User.from_row(row) for row in cursor.fetchall()]

    def count(self) -> int:
        """Count total users."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            return cursor.fetchone()[0]

    def set_admin(self, user_id: int, is_admin: bool) -> bool:
        """Set admin status for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (is_admin, user_id))
            return cursor.rowcount > 0

    def count_admins(self) -> int:
        """Count the number of admin users."""
        with self.db.connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]

    def is_last_admin(self, user_id: int) -> bool:
        """Check if this user is the only admin."""
        user = self.get_by_id(user_id)
        if not user or not user.is_admin:
            return False
        return self.count_admins() == 1

    def set_download_permission(self, user_id: int, can_download: bool) -> bool:
        """Set download permission for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET can_download = ? WHERE id = ?", (can_download, user_id)
            )
            return cursor.rowcount > 0

    def set_multi_session(self, user_id: int, value: str) -> bool:
        """Set multi-session override for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET multi_session = ? WHERE id = ?", (value, user_id)
            )
            return cursor.rowcount > 0

    def update_username(self, user_id: int, new_username: str) -> bool:
        """
        Update a user's username.

        Args:
            user_id: The user ID to update
            new_username: The new username (must be unique)

        Returns:
            True if updated, False if user not found or username taken
        """
        # Check if new username is already taken by another user
        existing = self.get_by_username(new_username)
        if existing and existing.id != user_id:
            return False

        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET username = ? WHERE id = ?", (new_username, user_id)
            )
            return cursor.rowcount > 0

    def update_email(self, user_id: int, email: Optional[str]) -> bool:
        """
        Update a user's email address.

        Args:
            user_id: The user ID to update
            email: The new email address, or None to remove

        Returns:
            True if updated, False if user not found
        """
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET recovery_email = ? WHERE id = ?", (email, user_id)
            )
            return cursor.rowcount > 0

    def delete(self, user_id: int) -> bool:
        """Delete a user (cascades to sessions, positions, etc.)."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0

    def get_by_email(self, email: str) -> Optional[User]:
        """Get user by recovery email address."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._USER_SELECT + " WHERE recovery_email = ?", (email,))
            row = cursor.fetchone()
            return User.from_row(row) if row else None

    def has_any_admin(self) -> bool:
        """Check if any admin user exists."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1")
            return cursor.fetchone() is not None


@dataclass
class Session:
    """
    Represents an active user session.

    By default, only one session per user is allowed and new logins invalidate
    existing sessions. Pass allow_multi=True to create_for_user() to preserve
    existing sessions and support concurrent multi-device logins.
    """

    id: Optional[int] = None
    user_id: int = 0
    token_hash: str = ""
    created_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    is_persistent: bool = False

    @classmethod
    def from_row(cls, row: tuple) -> "Session":
        """Create Session from database row."""
        session = cls(
            id=row[0],
            user_id=row[1],
            token_hash=row[2],
            created_at=datetime.fromisoformat(row[3]) if row[3] else None,
            last_seen=datetime.fromisoformat(row[4]) if row[4] else None,
            expires_at=datetime.fromisoformat(row[5]) if row[5] else None,
            user_agent=row[6],
            ip_address=row[7],
        )
        # Handle is_persistent column (added in v5 migration)
        if len(row) > 8:
            session.is_persistent = bool(row[8]) if row[8] is not None else False
        return session

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
        """
        Create new session for user, optionally invalidating existing sessions.

        Args:
            db: Auth database instance
            user_id: User to create session for
            user_agent: Client user agent string
            ip_address: Client IP address
            remember_me: If True, create a persistent session (no inactivity timeout)
            allow_multi: If True, keep existing sessions (multi-device support)

        Returns:
            Tuple of (Session, raw_token)
            - raw_token should be sent to client
        """
        raw_token, token_hash = generate_session_token()

        with db.connection() as conn:
            # Invalidate existing sessions unless multi-session is allowed
            if not allow_multi:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

            # Create new session
            cursor = conn.execute(
                """
                INSERT INTO sessions (
                    user_id, token_hash, user_agent, ip_address, is_persistent
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, token_hash, user_agent, ip_address, remember_me),
            )
            session_id = cursor.lastrowid

            # Fetch complete session
            cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            session = cls.from_row(cursor.fetchone())

        return session, raw_token

    def touch(self, db: AuthDatabase) -> None:
        """Update last_seen timestamp."""
        self.last_seen = datetime.now()
        with db.connection() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE id = ?",
                (self.last_seen.isoformat(), self.id),
            )

    def invalidate(self, db: AuthDatabase) -> None:
        """Invalidate this session (logout)."""
        with db.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (self.id,))

    def is_valid(self) -> bool:
        """Check if session is still valid (not expired)."""
        if self.expires_at and datetime.now() > self.expires_at:
            return False
        return True

    def is_stale(self, grace_minutes: int = 30) -> bool:
        """Check if session is stale (no activity within grace period).

        Persistent sessions never expire from inactivity — they last
        until the user signs out.
        """
        if self.last_seen is None:
            return True
        if self.is_persistent:
            return False
        threshold = datetime.now() - timedelta(minutes=grace_minutes)
        return self.last_seen < threshold


class SessionRepository:
    """Repository for Session operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_by_token(self, raw_token: str) -> Optional[Session]:
        """Get session by raw token."""
        token_hash = hash_token(raw_token)
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
            row = cursor.fetchone()
            return Session.from_row(row) if row else None

    def get_by_user_id(self, user_id: int) -> Optional[Session]:
        """Get active session for user (if any)."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return Session.from_row(row) if row else None

    def invalidate_user_sessions(self, user_id: int) -> int:
        """Invalidate all sessions for a user. Returns count of deleted sessions."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            return cursor.rowcount

    def cleanup_stale(self, grace_minutes: int = 30) -> int:
        """Remove stale sessions. Returns count of deleted sessions.

        Persistent sessions never expire from inactivity — only
        non-persistent sessions are cleaned up based on the grace period.
        """
        threshold = datetime.now() - timedelta(minutes=grace_minutes)
        # Use SQLite-compatible format (space separator) to match
        # DEFAULT CURRENT_TIMESTAMP
        threshold_str = threshold.strftime("%Y-%m-%d %H:%M:%S")
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE is_persistent = 0 AND last_seen < ?", (threshold_str,)
            )
            return cursor.rowcount


@dataclass
class UserPosition:
    """
    User's playback position for an audiobook.

    Each user has their own position tracking, never synced to Audible.
    """

    user_id: int = 0
    audiobook_id: int = 0
    position_ms: int = 0
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: tuple) -> "UserPosition":
        """Create UserPosition from database row."""
        return cls(
            user_id=row[0],
            audiobook_id=row[1],
            position_ms=row[2],
            updated_at=datetime.fromisoformat(row[3]) if row[3] else None,
        )

    def save(self, db: AuthDatabase) -> "UserPosition":
        """Save position (upsert)."""
        self.updated_at = datetime.now()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO user_positions (
                    user_id, audiobook_id, position_ms, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_id, audiobook_id) DO UPDATE SET
                    position_ms = excluded.position_ms,
                    updated_at = excluded.updated_at
                """,
                (self.user_id, self.audiobook_id, self.position_ms, self.updated_at.isoformat()),
            )
        return self


class PositionRepository:
    """Repository for UserPosition operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get(self, user_id: int, audiobook_id: int) -> Optional[UserPosition]:
        """Get position for user and audiobook."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM user_positions WHERE user_id = ? AND audiobook_id = ?",
                (user_id, audiobook_id),
            )
            row = cursor.fetchone()
            return UserPosition.from_row(row) if row else None

    def get_all_for_user(self, user_id: int) -> List[UserPosition]:
        """Get all positions for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM user_positions WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            )
            return [UserPosition.from_row(row) for row in cursor.fetchall()]

    def delete_for_user(self, user_id: int) -> int:
        """Delete all positions for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM user_positions WHERE user_id = ?", (user_id,))
            return cursor.rowcount


@dataclass
class UserListeningHistory:
    """
    A listening session for a user and audiobook.

    Tracks when the user started listening, when they stopped,
    and the playback positions during the session.
    """

    # Column list for SELECT queries (avoids SELECT * column-order issues
    # between fresh schema and ALTER TABLE migrations)
    _COLUMNS = (
        "id, user_id, audiobook_id, title, started_at, ended_at, "
        "position_start_ms, position_end_ms, duration_listened_ms"
    )

    id: Optional[int] = None
    user_id: int = 0
    audiobook_id: str = ""
    title: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    position_start_ms: int = 0
    position_end_ms: Optional[int] = None
    duration_listened_ms: Optional[int] = None

    @classmethod
    def from_row(cls, row: tuple) -> "UserListeningHistory":
        """Create UserListeningHistory from database row (matches _COLUMNS order)."""
        return cls(
            id=row[0],
            user_id=row[1],
            audiobook_id=row[2],
            title=row[3],
            started_at=datetime.fromisoformat(row[4]) if row[4] else None,
            ended_at=datetime.fromisoformat(row[5]) if row[5] else None,
            position_start_ms=row[6],
            position_end_ms=row[7],
            duration_listened_ms=row[8],
        )

    def save(self, db: AuthDatabase) -> "UserListeningHistory":
        """Save listening session (insert new or update existing).

        For existing sessions (id is set), only ended_at, position_end_ms,
        and duration_listened_ms are updated.
        """
        with db.connection() as conn:
            if self.id is None:
                # New session — insert
                if self.started_at is None:
                    self.started_at = datetime.now()
                cursor = conn.execute(
                    """
                    INSERT INTO user_listening_history
                        (user_id, audiobook_id, title, started_at, ended_at,
                         position_start_ms, position_end_ms, duration_listened_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.user_id,
                        self.audiobook_id,
                        self.title,
                        self.started_at.isoformat() if self.started_at else None,
                        self.ended_at.isoformat() if self.ended_at else None,
                        self.position_start_ms,
                        self.position_end_ms,
                        self.duration_listened_ms,
                    ),
                )
                self.id = cursor.lastrowid
            else:
                # Existing session — update
                conn.execute(
                    """
                    UPDATE user_listening_history
                    SET ended_at = ?, position_end_ms = ?, duration_listened_ms = ?
                    WHERE id = ?
                    """,
                    (
                        self.ended_at.isoformat() if self.ended_at else None,
                        self.position_end_ms,
                        self.duration_listened_ms,
                        self.id,
                    ),
                )
        return self


class ListeningHistoryRepository:
    """Repository for UserListeningHistory operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_for_user(
        self, user_id: int, limit: int = 50, offset: int = 0, min_duration_ms: Optional[int] = None
    ) -> List[UserListeningHistory]:
        """Get listening history for a user, ordered by most recent first."""
        cols = UserListeningHistory._COLUMNS
        with self.db.connection() as conn:
            if min_duration_ms is not None:
                query = (
                    f"SELECT {cols} FROM user_listening_history"  # nosec B608  # noqa: S608
                    " WHERE user_id = ?"
                    " AND duration_listened_ms IS NOT NULL"
                    " AND duration_listened_ms >= ?"
                    " ORDER BY started_at DESC"
                    " LIMIT ? OFFSET ?"
                )
                cursor = conn.execute(query, (user_id, min_duration_ms, limit, offset))
            else:
                query = (
                    f"SELECT {cols} FROM user_listening_history"  # nosec B608  # noqa: S608
                    " WHERE user_id = ?"
                    " ORDER BY started_at DESC"
                    " LIMIT ? OFFSET ?"
                )
                cursor = conn.execute(query, (user_id, limit, offset))
            return [UserListeningHistory.from_row(row) for row in cursor.fetchall()]

    def get_user_book_ids(self, user_id: int) -> List[str]:
        """Get distinct audiobook IDs the user has listened to."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT audiobook_id FROM user_listening_history
                WHERE user_id = ?
                """,
                (user_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def get_open_session(self, user_id: int, audiobook_id: str) -> Optional[UserListeningHistory]:
        """Get the current open (not yet ended) listening session, if any."""
        cols = UserListeningHistory._COLUMNS
        with self.db.connection() as conn:
            cursor = conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                f"""
                SELECT {cols} FROM user_listening_history
                WHERE user_id = ? AND audiobook_id = ? AND ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """,  # nosec B608  # noqa: S608
                (user_id, audiobook_id),
            )
            row = cursor.fetchone()
            return UserListeningHistory.from_row(row) if row else None


@dataclass
class UserDownload:
    """
    Record of a user downloading an audiobook.

    Downloads are immutable records — once recorded, they are not updated.
    """

    _COLUMNS = "id, user_id, audiobook_id, title, downloaded_at, file_format"

    id: Optional[int] = None
    user_id: int = 0
    audiobook_id: str = ""
    title: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    file_format: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "UserDownload":
        """Create UserDownload from database row (matches _COLUMNS order)."""
        return cls(
            id=row[0],
            user_id=row[1],
            audiobook_id=row[2],
            title=row[3],
            downloaded_at=datetime.fromisoformat(row[4]) if row[4] else None,
            file_format=row[5],
        )

    def save(self, db: AuthDatabase) -> "UserDownload":
        """Save download record (insert only — downloads are immutable)."""
        with db.connection() as conn:
            if self.downloaded_at is None:
                self.downloaded_at = datetime.now()
            cursor = conn.execute(
                """
                INSERT INTO user_downloads
                    (user_id, audiobook_id, title, downloaded_at, file_format)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self.user_id,
                    self.audiobook_id,
                    self.title,
                    self.downloaded_at.isoformat(),
                    self.file_format,
                ),
            )
            self.id = cursor.lastrowid
        return self


class DownloadRepository:
    """Repository for UserDownload operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_for_user(self, user_id: int, limit: int = 50, offset: int = 0) -> List[UserDownload]:
        """Get download history for a user, ordered by most recent first."""
        cols = UserDownload._COLUMNS
        with self.db.connection() as conn:
            cursor = conn.execute(  # nosec B608  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                f"""
                SELECT {cols} FROM user_downloads
                WHERE user_id = ?
                ORDER BY downloaded_at DESC
                LIMIT ? OFFSET ?
                """,  # nosec B608  # noqa: S608
                (user_id, limit, offset),
            )
            return [UserDownload.from_row(row) for row in cursor.fetchall()]

    def get_user_book_ids(self, user_id: int) -> List[str]:
        """Get distinct audiobook IDs the user has downloaded."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT audiobook_id FROM user_downloads
                WHERE user_id = ?
                """,
                (user_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def has_downloaded(self, user_id: int, audiobook_id: str) -> bool:
        """Check if a user has downloaded a specific audiobook."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM user_downloads
                WHERE user_id = ? AND audiobook_id = ?
                LIMIT 1
                """,
                (user_id, audiobook_id),
            )
            return cursor.fetchone() is not None


@dataclass
class UserPreferences:
    """
    Per-user preferences and UI state.

    Uses INSERT ... ON CONFLICT DO UPDATE SET to upsert, which preserves
    the original created_at value while updating only the changed fields.
    """

    user_id: int = 0
    new_books_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: tuple) -> "UserPreferences":
        """Create UserPreferences from database row."""
        return cls(
            user_id=row[0],
            new_books_seen_at=datetime.fromisoformat(row[1]) if row[1] else None,
            created_at=datetime.fromisoformat(row[2]) if row[2] else None,
            updated_at=datetime.fromisoformat(row[3]) if row[3] else None,
        )

    def save(self, db: AuthDatabase) -> "UserPreferences":
        """Save preferences (upsert). Explicitly sets updated_at."""
        self.updated_at = datetime.now()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences
                    (user_id, new_books_seen_at, created_at, updated_at)
                VALUES (?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
                ON CONFLICT (user_id) DO UPDATE SET
                    new_books_seen_at = excluded.new_books_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    self.user_id,
                    (self.new_books_seen_at.isoformat() if self.new_books_seen_at else None),
                    self.created_at.isoformat() if self.created_at else None,
                    self.updated_at.isoformat(),
                ),
            )
        return self


class PreferencesRepository:
    """Repository for UserPreferences operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_or_create(self, user_id: int) -> UserPreferences:
        """Get preferences for a user, creating default if none exist."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return UserPreferences.from_row(row)

            # Create default preferences
            conn.execute(
                """
                INSERT INTO user_preferences (user_id)
                VALUES (?)
                """,
                (user_id,),
            )
            # Fetch back to get defaults (created_at, updated_at)
            cursor = conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return UserPreferences.from_row(row)


class HiddenBookRepository:
    """Repository for managing books hidden from My Library view."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_hidden_ids(self, user_id: int) -> set[str]:
        """Get set of audiobook IDs hidden by this user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT audiobook_id FROM user_hidden_books WHERE user_id = ?", (user_id,)
            )
            return {str(row[0]) for row in cursor.fetchall()}

    def hide(self, user_id: int, audiobook_ids: list[int]) -> int:
        """Hide one or more books. Returns count of newly hidden books."""
        count = 0
        with self.db.connection() as conn:
            for aid in audiobook_ids:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_hidden_books"
                        " (user_id, audiobook_id) VALUES (?, ?)",
                        (user_id, aid),
                    )
                    count += conn.total_changes  # will be 1 if inserted, 0 if ignored
                except Exception as e:
                    logger.debug(
                        "hide audiobook %s for user %s failed (non-fatal): %s", aid, user_id, e
                    )
        return count

    def unhide(self, user_id: int, audiobook_ids: list[int]) -> int:
        """Unhide one or more books. Returns count of unhidden books."""
        if not audiobook_ids:
            return 0
        with self.db.connection() as conn:
            placeholders = ",".join("?" * len(audiobook_ids))
            cursor = conn.execute(
                "DELETE FROM user_hidden_books WHERE user_id = ?"  # nosec B608  # noqa: S608
                f" AND audiobook_id IN ({placeholders})",
                [user_id] + list(audiobook_ids),
            )
            return cursor.rowcount


class UserSettingsRepository:
    """
    Key-value settings repository for per-user preferences (v8).

    All values are stored as strings. The frontend handles type coercion.
    Unknown keys are rejected at the repository level.
    """

    VALID_KEYS = frozenset(
        {
            # Browsing
            "sort_order",
            "view_mode",
            "items_per_page",
            "default_collection",
            "content_filter",
            # Playback
            "playback_speed",
            "sleep_timer",
            "auto_play_series",
            # Accessibility
            "font_size",
            "contrast",
            "bg_opacity",
            "line_spacing",
            "reduce_animations",
            "high_contrast",
            "color_temperature",
            # Localization
            "locale",
        }
    )

    DEFAULTS = {
        "sort_order": "title_asc",
        "view_mode": "grid",
        "items_per_page": "50",
        "default_collection": "",
        "content_filter": "all",
        "playback_speed": "1",
        "sleep_timer": "0",
        "auto_play_series": "false",
        "font_size": "16",
        "contrast": "normal",
        "bg_opacity": "100",
        "line_spacing": "1.5",
        "reduce_animations": "false",
        "high_contrast": "false",
        "color_temperature": "neutral",
        "locale": "en",
    }

    def __init__(self, db: "AuthDatabase"):
        self.db = db

    def _validate_key(self, key: str) -> None:
        if key not in self.VALID_KEYS:
            raise ValueError(f"Unknown setting key: {key}")

    def get(self, user_id: int, key: str) -> str:
        """Get a single setting value, returning the default if unset."""
        self._validate_key(key)
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT setting_value FROM user_settings WHERE user_id = ? AND setting_key = ?",
                (user_id, key),
            )
            row = cursor.fetchone()
            return row[0] if row else self.DEFAULTS[key]

    def get_all(self, user_id: int) -> dict:
        """Get all settings for a user, merging stored values over defaults."""
        result = dict(self.DEFAULTS)
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT setting_key, setting_value FROM user_settings WHERE user_id = ?", (user_id,)
            )
            for row in cursor.fetchall():
                key, value = row[0], row[1]
                if key in self.VALID_KEYS:
                    result[key] = value
        return result

    def set(self, user_id: int, key: str, value: str) -> None:
        """Set a single setting (upsert)."""
        self._validate_key(key)
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO user_settings (user_id, setting_key, setting_value)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT (user_id, setting_key)"
                " DO UPDATE SET setting_value = excluded.setting_value,"
                " updated_at = CURRENT_TIMESTAMP",
                (user_id, key, value),
            )

    def set_many(self, user_id: int, settings: dict) -> int:
        """Set multiple settings at once. Returns count of valid keys set."""
        count = 0
        with self.db.connection() as conn:
            for key, value in settings.items():
                if key in self.VALID_KEYS and isinstance(value, str):
                    conn.execute(
                        "INSERT INTO user_settings"
                        " (user_id, setting_key, setting_value)"
                        " VALUES (?, ?, ?)"
                        " ON CONFLICT (user_id, setting_key)"
                        " DO UPDATE SET setting_value = excluded.setting_value,"
                        " updated_at = CURRENT_TIMESTAMP",
                        (user_id, key, value),
                    )
                    count += 1
        return count

    def delete(self, user_id: int, key: str) -> None:
        """Delete a single setting (reverts to default)."""
        self._validate_key(key)
        with self.db.connection() as conn:
            conn.execute(
                "DELETE FROM user_settings WHERE user_id = ? AND setting_key = ?", (user_id, key)
            )

    def delete_all(self, user_id: int) -> int:
        """Delete all settings for a user. Returns count deleted."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            return cursor.rowcount


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
    def from_row(cls, row) -> Optional["AuditLog"]:
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


class SystemSettingsRepository:
    """Repository for global system settings (admin-only key-value store)."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get(self, key: str, default: str | None = None) -> str | None:
        """Get a system setting value by key."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key = ?", (key,)
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
