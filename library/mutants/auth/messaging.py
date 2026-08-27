"""
User Messaging: Notifications and Inbox.

Split from models.py to improve maintainability index. Notifications are
admin-sent messages targeted at users (or broadcast); InboxMessages are
user-to-admin communications.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

from .database import AuthDatabase

logger = logging.getLogger(__name__)


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
                    (message, type, target_user_id, starts_at,
                     expires_at, dismissable, priority, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.message,
                        self.type.value,
                        self.target_user_id,
                        self.starts_at.isoformat() if self.starts_at else None,
                        self.expires_at.isoformat() if self.expires_at else None,
                        self.dismissable,
                        self.priority,
                        self.created_by,
                    ),
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
                    (
                        self.message,
                        self.type.value,
                        self.target_user_id,
                        self.starts_at.isoformat() if self.starts_at else None,
                        self.expires_at.isoformat() if self.expires_at else None,
                        self.dismissable,
                        self.priority,
                        self.id,
                    ),
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
                (user_id, now, now, user_id),
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
                    (notification_id, user_id),
                )
                return True
            except Exception:
                return False  # Already dismissed

    def list_all(self) -> List[Notification]:
        """List all notifications (admin)."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT * FROM notifications ORDER BY created_at DESC")
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
                    (self.from_user_id, self.message, self.reply_via.value, self.reply_email),
                )
                self.id = cursor.lastrowid
                cursor = conn.execute("SELECT created_at FROM inbox WHERE id = ?", (self.id,))
                self.created_at = datetime.fromisoformat(cursor.fetchone()[0])

                # Log contact (audit trail without content)
                conn.execute("INSERT INTO contact_log (user_id) VALUES (?)", (self.from_user_id,))
            else:
                conn.execute(
                    """
                    UPDATE inbox SET
                        status = ?, read_at = ?, replied_at = ?, reply_email = ?
                    WHERE id = ?
                    """,
                    (
                        self.status.value,
                        self.read_at.isoformat() if self.read_at else None,
                        self.replied_at.isoformat() if self.replied_at else None,
                        self.reply_email,
                        self.id,
                    ),
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
            cursor = conn.execute("SELECT * FROM inbox WHERE id = ?", (message_id,))
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
                cursor = conn.execute("SELECT * FROM inbox ORDER BY created_at DESC")
            else:
                cursor = conn.execute(
                    "SELECT * FROM inbox WHERE status != 'archived' ORDER BY created_at DESC"
                )
            return [InboxMessage.from_row(row) for row in cursor.fetchall()]

    def count_unread(self) -> int:
        """Count unread messages."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM inbox WHERE status = 'unread'")
            return cursor.fetchone()[0]

    def get_messages_by_user(self, user_id: int) -> List[InboxMessage]:
        """Get all messages from a specific user."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM inbox WHERE from_user_id = ? ORDER BY created_at DESC", (user_id,)
            )
            return [InboxMessage.from_row(row) for row in cursor.fetchall()]
