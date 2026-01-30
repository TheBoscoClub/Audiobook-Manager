"""
Auth Models for User Management, Sessions, and Notifications

These models provide a clean interface to the encrypted auth database.
All credential data is stored encrypted via SQLCipher.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List
from enum import Enum
import json

from .database import AuthDatabase, hash_token, generate_session_token, generate_verification_token


class AuthType(Enum):
    """Supported authentication methods."""
    PASSKEY = "passkey"
    FIDO2 = "fido2"
    TOTP = "totp"


class NotificationType(Enum):
    """Types of notifications."""
    INFO = "info"
    MAINTENANCE = "maintenance"
    OUTAGE = "outage"
    PERSONAL = "personal"


class InboxStatus(Enum):
    """Status of inbox messages."""
    UNREAD = "unread"
    READ = "read"
    REPLIED = "replied"
    ARCHIVED = "archived"


class ReplyMethod(Enum):
    """How to reply to user messages."""
    IN_APP = "in-app"
    EMAIL = "email"


@dataclass
class User:
    """
    Represents an authenticated user.

    Attributes:
        id: Database primary key
        username: Unique username (5-16 chars)
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

    @classmethod
    def from_row(cls, row: tuple) -> "User":
        """Create User from database row."""
        # Handle both old (8 columns) and new (11 columns) schema
        if len(row) >= 11:
            return cls(
                id=row[0],
                username=row[1],
                auth_type=AuthType(row[2]),
                auth_credential=row[3] if row[3] else b"",
                can_download=bool(row[4]),
                is_admin=bool(row[5]),
                created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                last_login=datetime.fromisoformat(row[7]) if row[7] else None,
                recovery_email=row[8],
                recovery_phone=row[9],
                recovery_enabled=bool(row[10]) if row[10] is not None else False,
            )
        else:
            # Old schema without recovery fields
            return cls(
                id=row[0],
                username=row[1],
                auth_type=AuthType(row[2]),
                auth_credential=row[3] if row[3] else b"",
                can_download=bool(row[4]),
                is_admin=bool(row[5]),
                created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                last_login=datetime.fromisoformat(row[7]) if row[7] else None,
            )

    def save(self, db: AuthDatabase) -> "User":
        """Save user to database (insert or update)."""
        with db.connection() as conn:
            if self.id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO users (username, auth_type, auth_credential, can_download, is_admin,
                                       recovery_email, recovery_phone, recovery_enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (self.username, self.auth_type.value, self.auth_credential,
                     self.can_download, self.is_admin,
                     self.recovery_email, self.recovery_phone, self.recovery_enabled)
                )
                self.id = cursor.lastrowid
                # Fetch the created_at timestamp
                cursor = conn.execute(
                    "SELECT created_at FROM users WHERE id = ?", (self.id,)
                )
                self.created_at = datetime.fromisoformat(cursor.fetchone()[0])
            else:
                conn.execute(
                    """
                    UPDATE users SET
                        username = ?, auth_type = ?, auth_credential = ?,
                        can_download = ?, is_admin = ?, last_login = ?,
                        recovery_email = ?, recovery_phone = ?, recovery_enabled = ?
                    WHERE id = ?
                    """,
                    (self.username, self.auth_type.value, self.auth_credential,
                     self.can_download, self.is_admin,
                     self.last_login.isoformat() if self.last_login else None,
                     self.recovery_email, self.recovery_phone, self.recovery_enabled,
                     self.id)
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
                (self.last_login.isoformat(), self.id)
            )


class UserRepository:
    """Repository for User operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            )
            row = cursor.fetchone()
            return User.from_row(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username (case-sensitive)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = cursor.fetchone()
            return User.from_row(row) if row else None

    def username_exists(self, username: str) -> bool:
        """Check if username is taken."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            )
            return cursor.fetchone() is not None

    def list_all(self, include_admin: bool = True) -> List[User]:
        """List all users."""
        with self.db.connection() as conn:
            if include_admin:
                cursor = conn.execute("SELECT * FROM users ORDER BY username")
            else:
                cursor = conn.execute(
                    "SELECT * FROM users WHERE is_admin = 0 ORDER BY username"
                )
            return [User.from_row(row) for row in cursor.fetchall()]

    def count(self) -> int:
        """Count total users."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM users")
            return cursor.fetchone()[0]

    def set_admin(self, user_id: int, is_admin: bool) -> bool:
        """Set admin status for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET is_admin = ? WHERE id = ?",
                (is_admin, user_id)
            )
            return cursor.rowcount > 0

    def set_download_permission(self, user_id: int, can_download: bool) -> bool:
        """Set download permission for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET can_download = ? WHERE id = ?",
                (can_download, user_id)
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
                "UPDATE users SET username = ? WHERE id = ?",
                (new_username, user_id)
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
                "UPDATE users SET recovery_email = ? WHERE id = ?",
                (email, user_id)
            )
            return cursor.rowcount > 0

    def delete(self, user_id: int) -> bool:
        """Delete a user (cascades to sessions, positions, etc.)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM users WHERE id = ?", (user_id,)
            )
            return cursor.rowcount > 0

    def has_any_admin(self) -> bool:
        """Check if any admin user exists."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1"
            )
            return cursor.fetchone() is not None


@dataclass
class Session:
    """
    Represents an active user session.

    Only one session per user is allowed. New logins invalidate existing sessions.
    """
    id: Optional[int] = None
    user_id: int = 0
    token_hash: str = ""
    created_at: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Session":
        """Create Session from database row."""
        return cls(
            id=row[0],
            user_id=row[1],
            token_hash=row[2],
            created_at=datetime.fromisoformat(row[3]) if row[3] else None,
            last_seen=datetime.fromisoformat(row[4]) if row[4] else None,
            expires_at=datetime.fromisoformat(row[5]) if row[5] else None,
            user_agent=row[6],
            ip_address=row[7],
        )

    @classmethod
    def create_for_user(
        cls,
        db: AuthDatabase,
        user_id: int,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> tuple["Session", str]:
        """
        Create new session for user, invalidating any existing sessions.

        Returns:
            Tuple of (Session, raw_token)
            - raw_token should be sent to client
        """
        raw_token, token_hash = generate_session_token()

        with db.connection() as conn:
            # Invalidate existing sessions
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

            # Create new session
            cursor = conn.execute(
                """
                INSERT INTO sessions (user_id, token_hash, user_agent, ip_address)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, token_hash, user_agent, ip_address)
            )
            session_id = cursor.lastrowid

            # Fetch complete session
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            session = cls.from_row(cursor.fetchone())

        return session, raw_token

    def touch(self, db: AuthDatabase) -> None:
        """Update last_seen timestamp."""
        self.last_seen = datetime.now()
        with db.connection() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE id = ?",
                (self.last_seen.isoformat(), self.id)
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
        """Check if session is stale (no activity within grace period)."""
        if self.last_seen is None:
            return True
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
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
            )
            row = cursor.fetchone()
            return Session.from_row(row) if row else None

    def get_by_user_id(self, user_id: int) -> Optional[Session]:
        """Get active session for user (if any)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()
            return Session.from_row(row) if row else None

    def invalidate_user_sessions(self, user_id: int) -> int:
        """Invalidate all sessions for a user. Returns count of deleted sessions."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount

    def cleanup_stale(self, grace_minutes: int = 30) -> int:
        """Remove stale sessions. Returns count of deleted sessions."""
        threshold = datetime.now() - timedelta(minutes=grace_minutes)
        # Use SQLite-compatible format (space separator) to match DEFAULT CURRENT_TIMESTAMP
        threshold_str = threshold.strftime('%Y-%m-%d %H:%M:%S')
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE last_seen < ?",
                (threshold_str,)
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
                INSERT INTO user_positions (user_id, audiobook_id, position_ms, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_id, audiobook_id) DO UPDATE SET
                    position_ms = excluded.position_ms,
                    updated_at = excluded.updated_at
                """,
                (self.user_id, self.audiobook_id, self.position_ms,
                 self.updated_at.isoformat())
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
                (user_id, audiobook_id)
            )
            row = cursor.fetchone()
            return UserPosition.from_row(row) if row else None

    def get_all_for_user(self, user_id: int) -> List[UserPosition]:
        """Get all positions for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM user_positions WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,)
            )
            return [UserPosition.from_row(row) for row in cursor.fetchall()]

    def delete_for_user(self, user_id: int) -> int:
        """Delete all positions for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM user_positions WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount


@dataclass
class Notification:
    """
    System notification for users.

    Can be targeted to all users or a specific user.
    """
    id: Optional[int] = None
    message: str = ""
    type: NotificationType = NotificationType.INFO
    target_user_id: Optional[int] = None
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    dismissable: bool = True
    priority: int = 0
    created_at: Optional[datetime] = None
    created_by: str = "admin"

    @classmethod
    def from_row(cls, row: tuple) -> "Notification":
        """Create Notification from database row."""
        return cls(
            id=row[0],
            message=row[1],
            type=NotificationType(row[2]),
            target_user_id=row[3],
            starts_at=datetime.fromisoformat(row[4]) if row[4] else None,
            expires_at=datetime.fromisoformat(row[5]) if row[5] else None,
            dismissable=bool(row[6]),
            priority=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            created_by=row[9],
        )

    def save(self, db: AuthDatabase) -> "Notification":
        """Save notification to database."""
        with db.connection() as conn:
            if self.id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO notifications
                    (message, type, target_user_id, starts_at, expires_at, dismissable, priority, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (self.message, self.type.value, self.target_user_id,
                     self.starts_at.isoformat() if self.starts_at else None,
                     self.expires_at.isoformat() if self.expires_at else None,
                     self.dismissable, self.priority, self.created_by)
                )
                self.id = cursor.lastrowid
                cursor = conn.execute(
                    "SELECT created_at FROM notifications WHERE id = ?", (self.id,)
                )
                self.created_at = datetime.fromisoformat(cursor.fetchone()[0])
            else:
                conn.execute(
                    """
                    UPDATE notifications SET
                        message = ?, type = ?, target_user_id = ?, starts_at = ?,
                        expires_at = ?, dismissable = ?, priority = ?
                    WHERE id = ?
                    """,
                    (self.message, self.type.value, self.target_user_id,
                     self.starts_at.isoformat() if self.starts_at else None,
                     self.expires_at.isoformat() if self.expires_at else None,
                     self.dismissable, self.priority, self.id)
                )
        return self

    def delete(self, db: AuthDatabase) -> bool:
        """Delete notification."""
        if self.id is None:
            return False
        with db.connection() as conn:
            conn.execute("DELETE FROM notifications WHERE id = ?", (self.id,))
        return True

    def is_active(self) -> bool:
        """Check if notification is currently active."""
        now = datetime.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.expires_at and now > self.expires_at:
            return False
        return True


class NotificationRepository:
    """Repository for Notification operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_active_for_user(self, user_id: int) -> List[Notification]:
        """Get active notifications for a user (including global ones)."""
        now = datetime.now().isoformat()
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                SELECT n.* FROM notifications n
                WHERE (n.target_user_id IS NULL OR n.target_user_id = ?)
                  AND (n.starts_at IS NULL OR n.starts_at <= ?)
                  AND (n.expires_at IS NULL OR n.expires_at > ?)
                  AND n.id NOT IN (
                      SELECT notification_id FROM notification_dismissals
                      WHERE user_id = ?
                  )
                ORDER BY n.priority DESC, n.created_at DESC
                """,
                (user_id, now, now, user_id)
            )
            return [Notification.from_row(row) for row in cursor.fetchall()]

    def dismiss(self, notification_id: int, user_id: int) -> bool:
        """Dismiss a notification for a user."""
        with self.db.connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO notification_dismissals (notification_id, user_id)
                    VALUES (?, ?)
                    """,
                    (notification_id, user_id)
                )
                return True
            except Exception:
                return False  # Already dismissed

    def list_all(self) -> List[Notification]:
        """List all notifications (admin)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC"
            )
            return [Notification.from_row(row) for row in cursor.fetchall()]


@dataclass
class InboxMessage:
    """
    Message from user to admin.
    """
    id: Optional[int] = None
    from_user_id: int = 0
    message: str = ""
    reply_via: ReplyMethod = ReplyMethod.IN_APP
    reply_email: Optional[str] = None
    status: InboxStatus = InboxStatus.UNREAD
    created_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: tuple) -> "InboxMessage":
        """Create InboxMessage from database row."""
        return cls(
            id=row[0],
            from_user_id=row[1],
            message=row[2],
            reply_via=ReplyMethod(row[3]),
            reply_email=row[4],
            status=InboxStatus(row[5]),
            created_at=datetime.fromisoformat(row[6]) if row[6] else None,
            read_at=datetime.fromisoformat(row[7]) if row[7] else None,
            replied_at=datetime.fromisoformat(row[8]) if row[8] else None,
        )

    def save(self, db: AuthDatabase) -> "InboxMessage":
        """Save message to database."""
        with db.connection() as conn:
            if self.id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO inbox (from_user_id, message, reply_via, reply_email)
                    VALUES (?, ?, ?, ?)
                    """,
                    (self.from_user_id, self.message, self.reply_via.value,
                     self.reply_email)
                )
                self.id = cursor.lastrowid
                cursor = conn.execute(
                    "SELECT created_at FROM inbox WHERE id = ?", (self.id,)
                )
                self.created_at = datetime.fromisoformat(cursor.fetchone()[0])

                # Log contact (audit trail without content)
                conn.execute(
                    "INSERT INTO contact_log (user_id) VALUES (?)",
                    (self.from_user_id,)
                )
            else:
                conn.execute(
                    """
                    UPDATE inbox SET
                        status = ?, read_at = ?, replied_at = ?, reply_email = ?
                    WHERE id = ?
                    """,
                    (self.status.value,
                     self.read_at.isoformat() if self.read_at else None,
                     self.replied_at.isoformat() if self.replied_at else None,
                     self.reply_email, self.id)
                )
        return self

    def mark_read(self, db: AuthDatabase) -> None:
        """Mark message as read."""
        self.status = InboxStatus.READ
        self.read_at = datetime.now()
        self.save(db)

    def mark_replied(self, db: AuthDatabase) -> None:
        """Mark message as replied and clear email if present."""
        self.status = InboxStatus.REPLIED
        self.replied_at = datetime.now()
        self.reply_email = None  # Clear PII after reply
        self.save(db)


class InboxRepository:
    """Repository for InboxMessage operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_by_id(self, message_id: int) -> Optional[InboxMessage]:
        """Get message by ID."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM inbox WHERE id = ?", (message_id,)
            )
            row = cursor.fetchone()
            return InboxMessage.from_row(row) if row else None

    def list_unread(self) -> List[InboxMessage]:
        """List unread messages."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM inbox WHERE status = 'unread' ORDER BY created_at DESC"
            )
            return [InboxMessage.from_row(row) for row in cursor.fetchall()]

    def list_all(self, include_archived: bool = False) -> List[InboxMessage]:
        """List all messages."""
        with self.db.connection() as conn:
            if include_archived:
                cursor = conn.execute(
                    "SELECT * FROM inbox ORDER BY created_at DESC"
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM inbox WHERE status != 'archived' ORDER BY created_at DESC"
                )
            return [InboxMessage.from_row(row) for row in cursor.fetchall()]

    def count_unread(self) -> int:
        """Count unread messages."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM inbox WHERE status = 'unread'"
            )
            return cursor.fetchone()[0]

    def get_messages_by_user(self, user_id: int) -> List[InboxMessage]:
        """Get all messages from a specific user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM inbox WHERE from_user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return [InboxMessage.from_row(row) for row in cursor.fetchall()]


@dataclass
class PendingRegistration:
    """
    Pending user registration awaiting verification.
    """
    id: Optional[int] = None
    username: str = ""
    token_hash: str = ""
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: tuple) -> "PendingRegistration":
        """Create from database row."""
        return cls(
            id=row[0],
            username=row[1],
            token_hash=row[2],
            created_at=datetime.fromisoformat(row[3]) if row[3] else None,
            expires_at=datetime.fromisoformat(row[4]) if row[4] else None,
        )

    @classmethod
    def create(
        cls,
        db: AuthDatabase,
        username: str,
        expiry_minutes: int = 15
    ) -> tuple["PendingRegistration", str]:
        """
        Create pending registration.

        Returns:
            Tuple of (PendingRegistration, raw_token)
            - raw_token is sent to user via email/SMS
        """
        raw_token, token_hash = generate_verification_token()
        expires_at = datetime.now() + timedelta(minutes=expiry_minutes)

        with db.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_registrations (username, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (username, token_hash, expires_at.isoformat())
            )
            reg_id = cursor.lastrowid

            cursor = conn.execute(
                "SELECT * FROM pending_registrations WHERE id = ?", (reg_id,)
            )
            reg = cls.from_row(cursor.fetchone())

        return reg, raw_token

    def is_expired(self) -> bool:
        """Check if registration has expired."""
        if self.expires_at is None:
            return True
        return datetime.now() > self.expires_at

    def consume(self, db: AuthDatabase) -> bool:
        """Delete this pending registration (single-use)."""
        if self.id is None:
            return False
        with db.connection() as conn:
            conn.execute(
                "DELETE FROM pending_registrations WHERE id = ?", (self.id,)
            )
        return True


class PendingRegistrationRepository:
    """Repository for PendingRegistration operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_by_token(self, raw_token: str) -> Optional[PendingRegistration]:
        """Get pending registration by raw token."""
        token_hash = hash_token(raw_token)
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM pending_registrations WHERE token_hash = ?",
                (token_hash,)
            )
            row = cursor.fetchone()
            return PendingRegistration.from_row(row) if row else None

    def cleanup_expired(self) -> int:
        """Remove expired pending registrations."""
        now = datetime.now().isoformat()
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pending_registrations WHERE expires_at < ?", (now,)
            )
            return cursor.rowcount

    def delete_for_username(self, username: str) -> int:
        """Delete all pending registrations for a username."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pending_registrations WHERE username = ?", (username,)
            )
            return cursor.rowcount


@dataclass
class PendingRecovery:
    """
    Pending account recovery awaiting verification (magic link).
    """
    id: Optional[int] = None
    user_id: int = 0
    token_hash: str = ""
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: tuple) -> "PendingRecovery":
        """Create from database row."""
        return cls(
            id=row[0],
            user_id=row[1],
            token_hash=row[2],
            created_at=datetime.fromisoformat(row[3]) if row[3] else None,
            expires_at=datetime.fromisoformat(row[4]) if row[4] else None,
            used_at=datetime.fromisoformat(row[5]) if row[5] else None,
        )

    @classmethod
    def create(
        cls,
        db: AuthDatabase,
        user_id: int,
        expiry_minutes: int = 15
    ) -> tuple["PendingRecovery", str]:
        """
        Create pending recovery request.

        Returns:
            Tuple of (PendingRecovery, raw_token)
            - raw_token is sent to user via email/SMS
        """
        raw_token, token_hash = generate_verification_token()
        expires_at = datetime.now() + timedelta(minutes=expiry_minutes)

        with db.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_recovery (user_id, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (user_id, token_hash, expires_at.isoformat())
            )
            recovery_id = cursor.lastrowid

            cursor = conn.execute(
                "SELECT * FROM pending_recovery WHERE id = ?", (recovery_id,)
            )
            recovery = cls.from_row(cursor.fetchone())

        return recovery, raw_token

    def is_expired(self) -> bool:
        """Check if recovery has expired."""
        if self.expires_at is None:
            return True
        return datetime.now() > self.expires_at

    def is_used(self) -> bool:
        """Check if recovery has been used."""
        return self.used_at is not None

    def mark_used(self, db: AuthDatabase) -> bool:
        """Mark this recovery as used."""
        if self.id is None:
            return False
        with db.connection() as conn:
            conn.execute(
                "UPDATE pending_recovery SET used_at = ? WHERE id = ?",
                (datetime.now().isoformat(), self.id)
            )
        self.used_at = datetime.now()
        return True


class PendingRecoveryRepository:
    """Repository for PendingRecovery operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def get_by_token(self, raw_token: str) -> Optional[PendingRecovery]:
        """Get pending recovery by raw token."""
        token_hash = hash_token(raw_token)
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM pending_recovery WHERE token_hash = ?",
                (token_hash,)
            )
            row = cursor.fetchone()
            return PendingRecovery.from_row(row) if row else None

    def cleanup_expired(self) -> int:
        """Remove expired pending recoveries."""
        now = datetime.now().isoformat()
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pending_recovery WHERE expires_at < ?", (now,)
            )
            return cursor.rowcount

    def delete_for_user(self, user_id: int) -> int:
        """Delete all pending recoveries for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pending_recovery WHERE user_id = ?", (user_id,)
            )
            return cursor.rowcount


class AccessRequestStatus(Enum):
    """Status of access requests."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass
class AccessRequest:
    """
    Access request awaiting admin approval.

    Users submit requests which admins can approve or deny.
    Includes claim token for secure credential retrieval without email.
    """
    id: Optional[int] = None
    username: str = ""
    requested_at: Optional[datetime] = None
    status: AccessRequestStatus = AccessRequestStatus.PENDING
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    deny_reason: Optional[str] = None
    # Claim token for credential retrieval (hashed)
    claim_token_hash: Optional[str] = None
    # Optional contact email (user's choice to provide)
    contact_email: Optional[str] = None
    # Credentials stored after approval, retrieved via claim
    totp_secret: Optional[str] = None  # Base32 encoded
    totp_uri: Optional[str] = None
    backup_codes_json: Optional[str] = None  # JSON array of codes
    credentials_claimed: bool = False

    @classmethod
    def from_row(cls, row: tuple) -> "AccessRequest":
        """Create from database row."""
        # Handle both old (7 columns) and new (14 columns) schema
        if len(row) >= 13:
            return cls(
                id=row[0],
                username=row[1],
                requested_at=datetime.fromisoformat(row[2]) if row[2] else None,
                status=AccessRequestStatus(row[3]) if row[3] else AccessRequestStatus.PENDING,
                reviewed_at=datetime.fromisoformat(row[4]) if row[4] else None,
                reviewed_by=row[5],
                deny_reason=row[6],
                claim_token_hash=row[7],
                contact_email=row[8],
                totp_secret=row[9],
                totp_uri=row[10],
                backup_codes_json=row[11],
                credentials_claimed=bool(row[12]) if row[12] is not None else False,
            )
        else:
            # Old schema without claim fields
            return cls(
                id=row[0],
                username=row[1],
                requested_at=datetime.fromisoformat(row[2]) if row[2] else None,
                status=AccessRequestStatus(row[3]) if row[3] else AccessRequestStatus.PENDING,
                reviewed_at=datetime.fromisoformat(row[4]) if row[4] else None,
                reviewed_by=row[5],
                deny_reason=row[6],
            )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "username": self.username,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "status": self.status.value,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by": self.reviewed_by,
            "deny_reason": self.deny_reason,
            "has_email": bool(self.contact_email),
            "credentials_claimed": self.credentials_claimed,
        }


class AccessRequestRepository:
    """Repository for AccessRequest operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        """Create table if it doesn't exist, and migrate if needed."""
        with self.db.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS access_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
                    reviewed_at TIMESTAMP,
                    reviewed_by TEXT,
                    deny_reason TEXT,
                    claim_token_hash TEXT,
                    contact_email TEXT,
                    totp_secret TEXT,
                    totp_uri TEXT,
                    backup_codes_json TEXT,
                    credentials_claimed BOOLEAN DEFAULT FALSE,
                    CHECK (length(username) >= 5 AND length(username) <= 16)
                )
            """)

            # Migrate existing table if needed (add new columns) - MUST run before index creation
            cursor = conn.execute("PRAGMA table_info(access_requests)")
            columns = {row[1] for row in cursor.fetchall()}
            if "claim_token_hash" not in columns:
                conn.execute("ALTER TABLE access_requests ADD COLUMN claim_token_hash TEXT")
            if "contact_email" not in columns:
                conn.execute("ALTER TABLE access_requests ADD COLUMN contact_email TEXT")
            if "totp_secret" not in columns:
                conn.execute("ALTER TABLE access_requests ADD COLUMN totp_secret TEXT")
            if "totp_uri" not in columns:
                conn.execute("ALTER TABLE access_requests ADD COLUMN totp_uri TEXT")
            if "backup_codes_json" not in columns:
                conn.execute("ALTER TABLE access_requests ADD COLUMN backup_codes_json TEXT")
            if "credentials_claimed" not in columns:
                conn.execute("ALTER TABLE access_requests ADD COLUMN credentials_claimed BOOLEAN DEFAULT FALSE")

            # Create indexes AFTER columns exist
            conn.execute("CREATE INDEX IF NOT EXISTS idx_access_requests_status ON access_requests(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_access_requests_username ON access_requests(username)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_access_requests_claim_token ON access_requests(claim_token_hash)")

    def create(self, username: str, claim_token_hash: str, contact_email: Optional[str] = None) -> AccessRequest:
        """Create a new access request with claim token."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO access_requests (username, claim_token_hash, contact_email) VALUES (?, ?, ?)",
                (username, claim_token_hash, contact_email)
            )
            request_id = cursor.lastrowid
            cursor = conn.execute(
                "SELECT * FROM access_requests WHERE id = ?", (request_id,)
            )
            return AccessRequest.from_row(cursor.fetchone())

    def get_by_id(self, request_id: int) -> Optional[AccessRequest]:
        """Get access request by ID."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM access_requests WHERE id = ?", (request_id,)
            )
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None

    def get_by_username(self, username: str) -> Optional[AccessRequest]:
        """Get access request by username."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM access_requests WHERE username = ?", (username,)
            )
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None

    def list_pending(self, limit: int = 50) -> List[AccessRequest]:
        """List all pending access requests."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM access_requests WHERE status = 'pending' ORDER BY requested_at ASC LIMIT ?",
                (limit,)
            )
            return [AccessRequest.from_row(row) for row in cursor.fetchall()]

    def list_all(self, limit: int = 100) -> List[AccessRequest]:
        """List all access requests (any status)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM access_requests ORDER BY requested_at DESC LIMIT ?",
                (limit,)
            )
            return [AccessRequest.from_row(row) for row in cursor.fetchall()]

    def approve(self, request_id: int, admin_username: str) -> bool:
        """Approve an access request."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE access_requests
                SET status = 'approved', reviewed_at = ?, reviewed_by = ?
                WHERE id = ? AND status = 'pending'
                """,
                (datetime.now().isoformat(), admin_username, request_id)
            )
            return cursor.rowcount > 0

    def deny(self, request_id: int, admin_username: str, reason: Optional[str] = None) -> bool:
        """Deny an access request."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE access_requests
                SET status = 'denied', reviewed_at = ?, reviewed_by = ?, deny_reason = ?
                WHERE id = ? AND status = 'pending'
                """,
                (datetime.now().isoformat(), admin_username, reason, request_id)
            )
            return cursor.rowcount > 0

    def delete(self, request_id: int) -> bool:
        """Delete an access request."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM access_requests WHERE id = ?", (request_id,)
            )
            return cursor.rowcount > 0

    def delete_for_username(self, username: str) -> int:
        """Delete all access requests for a username."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM access_requests WHERE username = ?", (username,)
            )
            return cursor.rowcount

    def has_pending_request(self, username: str) -> bool:
        """Check if username has a pending request."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM access_requests WHERE username = ? AND status = 'pending'",
                (username,)
            )
            return cursor.fetchone() is not None

    def count_pending(self) -> int:
        """Count pending access requests."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM access_requests WHERE status = 'pending'"
            )
            return cursor.fetchone()[0]

    def get_by_claim_token(self, claim_token_hash: str) -> Optional[AccessRequest]:
        """Get access request by claim token hash."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM access_requests WHERE claim_token_hash = ?",
                (claim_token_hash,)
            )
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None

    def store_credentials(
        self,
        request_id: int,
        totp_secret: str,
        totp_uri: str,
        backup_codes_json: str
    ) -> bool:
        """Store TOTP credentials after approval for later claim."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE access_requests
                SET totp_secret = ?, totp_uri = ?, backup_codes_json = ?
                WHERE id = ?
                """,
                (totp_secret, totp_uri, backup_codes_json, request_id)
            )
            return cursor.rowcount > 0

    def mark_credentials_claimed(self, request_id: int) -> bool:
        """Mark credentials as claimed (one-time retrieval)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE access_requests
                SET credentials_claimed = TRUE
                WHERE id = ? AND credentials_claimed = FALSE
                """,
                (request_id,)
            )
            return cursor.rowcount > 0

    def get_pending_by_username_and_token(self, username: str, claim_token_hash: str) -> Optional[AccessRequest]:
        """Get access request by username and claim token (for status check)."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM access_requests WHERE username = ? AND claim_token_hash = ?",
                (username, claim_token_hash)
            )
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None
