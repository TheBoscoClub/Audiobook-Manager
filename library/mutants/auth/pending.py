"""
Pending Registration and Recovery flows.

Split from models.py to improve maintainability index. Holds one-time
verification tokens for self-service account creation and account recovery
(magic-link style).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .database import AuthDatabase, generate_verification_token, hash_token

logger = logging.getLogger(__name__)


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
        cls, db: AuthDatabase, username: str, expiry_minutes: int = 15
    ) -> tuple["PendingRegistration", str]:
        """
        Create pending registration.

        Returns:
            Tuple of (PendingRegistration, raw_token)
            - raw_token is the 16-char truncated token for claim URLs
        """
        full_token, _ = generate_verification_token()
        # Truncate to 16 chars — callers format as XXXX-XXXX-XXXX-XXXX.
        # The stored hash must match hash(truncated) so validate works.
        raw_token = full_token[:16]
        token_hash = hash_token(raw_token)
        expires_at = datetime.now() + timedelta(minutes=expiry_minutes)

        with db.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pending_registrations (username, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (username, token_hash, expires_at.isoformat()),
            )
            reg_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM pending_registrations WHERE id = ?", (reg_id,))
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
            conn.execute("DELETE FROM pending_registrations WHERE id = ?", (self.id,))
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
                "SELECT * FROM pending_registrations WHERE token_hash = ?", (token_hash,)
            )
            row = cursor.fetchone()
            return PendingRegistration.from_row(row) if row else None

    def cleanup_expired(self) -> int:
        """Remove expired pending registrations."""
        now = datetime.now().isoformat()
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM pending_registrations WHERE expires_at < ?", (now,))
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
        cls, db: AuthDatabase, user_id: int, expiry_minutes: int = 15
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
                (user_id, token_hash, expires_at.isoformat()),
            )
            recovery_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM pending_recovery WHERE id = ?", (recovery_id,))
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
                (datetime.now().isoformat(), self.id),
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
                "SELECT * FROM pending_recovery WHERE token_hash = ?", (token_hash,)
            )
            row = cursor.fetchone()
            return PendingRecovery.from_row(row) if row else None

    def cleanup_expired(self) -> int:
        """Remove expired pending recoveries."""
        now = datetime.now().isoformat()
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM pending_recovery WHERE expires_at < ?", (now,))
            return cursor.rowcount

    def delete_for_user(self, user_id: int) -> int:
        """Delete all pending recoveries for a user."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM pending_recovery WHERE user_id = ?", (user_id,))
            return cursor.rowcount
