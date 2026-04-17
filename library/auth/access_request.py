"""
Access Request model and repository.

Split from models.py to improve maintainability index. Users submit access
requests which admins can approve or deny. Each approved request carries a
one-time claim token for credential retrieval (no email required).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional

from .database import AuthDatabase


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
    # Preferred auth method (totp, passkey, magic_link)
    preferred_auth_method: str = "totp"
    # Expiry for claim token (invitations only)
    claim_expires_at: Optional[datetime] = None
    # Locale for guest-facing emails
    preferred_locale: str = "en"

    @property
    def ensured_id(self) -> int:
        """Return self.id narrowed to int. Panics if record was never saved."""
        assert self.id is not None, "AccessRequest.ensured_id accessed before save"
        return self.id

    @staticmethod
    def _parse_claim_expires(val) -> Optional[datetime]:
        """Parse claim_expires_at from various stored types."""
        if val and isinstance(val, str):
            return datetime.fromisoformat(val)
        if val and isinstance(val, (int, float)):
            return datetime.fromtimestamp(val)
        return None

    @classmethod
    def _base_ar_fields(cls, row: tuple) -> dict:
        """Extract base fields common to all AccessRequest schema versions."""
        return {
            "id": row[0],
            "username": row[1],
            "requested_at": datetime.fromisoformat(row[2]) if row[2] else None,
            "status": (AccessRequestStatus(row[3]) if row[3] else AccessRequestStatus.PENDING),
            "reviewed_at": datetime.fromisoformat(row[4]) if row[4] else None,
            "reviewed_by": row[5],
            "deny_reason": row[6],
        }

    @classmethod
    def from_row(cls, row: tuple) -> "AccessRequest":
        """Create from database row."""
        fields = cls._base_ar_fields(row)

        # Old schema (7 columns) — no claim fields
        if len(row) < 13:
            return cls(**fields)

        # New schema: claim fields (columns 7-12)
        fields.update(
            {
                "claim_token_hash": row[7],
                "contact_email": row[8],
                "totp_secret": row[9],
                "totp_uri": row[10],
                "backup_codes_json": row[11],
                "credentials_claimed": bool(row[12]) if row[12] is not None else False,
            }
        )

        if len(row) > 13:
            fields["preferred_auth_method"] = row[13] or "totp"
        if len(row) > 14:
            fields["claim_expires_at"] = cls._parse_claim_expires(row[14])
        if len(row) > 15:
            fields["preferred_locale"] = row[15] or "en"

        return cls(**fields)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "username": self.username,
            "requested_at": (self.requested_at.isoformat() if self.requested_at else None),
            "status": self.status.value,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by": self.reviewed_by,
            "deny_reason": self.deny_reason,
            "has_email": bool(self.contact_email),
            "credentials_claimed": self.credentials_claimed,
            "claim_expires_at": (
                self.claim_expires_at.isoformat() if self.claim_expires_at else None
            ),
        }

    def is_claim_expired(self) -> bool:
        """Check if the claim token has expired."""
        if self.claim_expires_at is None:
            return False
        return datetime.now() > self.claim_expires_at


class AccessRequestRepository:
    """Repository for AccessRequest operations."""

    # Explicit column list for SELECT queries — guarantees positional order
    # matches from_row() regardless of physical table column order.
    # schema.sql and _ensure_table() may create columns in different orders
    # (ALTER TABLE appends at end), so SELECT * is unsafe.
    # nosemgrep: sqlalchemy-execute-raw-query
    _AR_SELECT = (
        "SELECT id, username, requested_at, status, reviewed_at, reviewed_by, "
        "deny_reason, claim_token_hash, contact_email, totp_secret, totp_uri, "
        "backup_codes_json, credentials_claimed, preferred_auth_method, "
        "claim_expires_at, preferred_locale FROM access_requests"
    )

    def __init__(self, db: AuthDatabase):
        self.db = db
        self._ensure_table()

    # Columns added after the original table schema. Each entry is
    # (column_name, SQL fragment appended to `ALTER TABLE ... ADD COLUMN`).
    _MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
        ("claim_token_hash", "claim_token_hash TEXT"),
        ("contact_email", "contact_email TEXT"),
        ("totp_secret", "totp_secret TEXT"),
        ("totp_uri", "totp_uri TEXT"),
        ("backup_codes_json", "backup_codes_json TEXT"),
        ("credentials_claimed", "credentials_claimed BOOLEAN DEFAULT FALSE"),
        ("claim_expires_at", "claim_expires_at TIMESTAMP"),
        ("preferred_auth_method", "preferred_auth_method TEXT DEFAULT 'totp'"),
        ("preferred_locale", "preferred_locale TEXT DEFAULT 'en'"),
    )

    _INDEX_STATEMENTS: tuple[str, ...] = (
        "CREATE INDEX IF NOT EXISTS idx_access_requests_status ON access_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_access_requests_username ON access_requests(username)",
        "CREATE INDEX IF NOT EXISTS idx_access_requests_claim_token"
        " ON access_requests(claim_token_hash)",
    )

    def _ensure_table(self):
        """Create table if it doesn't exist, and migrate if needed."""
        with self.db.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS access_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'denied')),
                    reviewed_at TIMESTAMP,
                    reviewed_by TEXT,
                    deny_reason TEXT,
                    claim_token_hash TEXT,
                    contact_email TEXT,
                    totp_secret TEXT,
                    totp_uri TEXT,
                    backup_codes_json TEXT,
                    credentials_claimed BOOLEAN DEFAULT FALSE,
                    preferred_auth_method TEXT DEFAULT 'totp',
                    claim_expires_at TIMESTAMP,
                    preferred_locale TEXT DEFAULT 'en',
                    CHECK (length(username) >= 3 AND length(username) <= 24)
                )
            """)

            # Migrate existing table if needed (add new columns)
            # - MUST run before index creation
            # - column_sql comes from the class-level _MIGRATION_COLUMNS tuple,
            #   which is a hardcoded constant — no untrusted input.
            cursor = conn.execute("PRAGMA table_info(access_requests)")
            existing = {row[1] for row in cursor.fetchall()}
            for column_name, column_sql in self._MIGRATION_COLUMNS:
                if column_name not in existing:
                    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    conn.execute(f"ALTER TABLE access_requests ADD COLUMN {column_sql}")  # nosec B608

            # Create indexes AFTER columns exist
            for index_sql in self._INDEX_STATEMENTS:
                conn.execute(index_sql)

    def create(
        self,
        username: str,
        claim_token_hash: str,
        contact_email: Optional[str] = None,
        claim_expires_at: Optional[datetime] = None,
    ) -> AccessRequest:
        """Create a new access request with claim token."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO access_requests"
                " (username, claim_token_hash, contact_email, claim_expires_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    username,
                    claim_token_hash,
                    contact_email,
                    claim_expires_at.isoformat() if claim_expires_at else None,
                ),
            )
            request_id = cursor.lastrowid
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._AR_SELECT + " WHERE id = ?", (request_id,))
            return AccessRequest.from_row(cursor.fetchone())

    def get_by_id(self, request_id: int) -> Optional[AccessRequest]:
        """Get access request by ID."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._AR_SELECT + " WHERE id = ?", (request_id,))
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None

    def get_by_username(self, username: str) -> Optional[AccessRequest]:
        """Get access request by username."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._AR_SELECT + " WHERE username = ?", (username,))
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None

    def list_pending(self, limit: int = 50) -> List[AccessRequest]:
        """List all pending access requests."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(
                self._AR_SELECT + " WHERE status = 'pending' ORDER BY requested_at ASC LIMIT ?",
                (limit,),
            )
            return [AccessRequest.from_row(row) for row in cursor.fetchall()]

    def list_all(self, limit: int = 100) -> List[AccessRequest]:
        """List all access requests (any status)."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(self._AR_SELECT + " ORDER BY requested_at DESC LIMIT ?", (limit,))
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
                (datetime.now().isoformat(), admin_username, request_id),
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
                (datetime.now().isoformat(), admin_username, reason, request_id),
            )
            return cursor.rowcount > 0

    def delete(self, request_id: int) -> bool:
        """Delete an access request."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM access_requests WHERE id = ?", (request_id,))
            return cursor.rowcount > 0

    def delete_for_username(self, username: str) -> int:
        """Delete all access requests for a username."""
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM access_requests WHERE username = ?", (username,))
            return cursor.rowcount

    def has_pending_request(self, username: str) -> bool:
        """Check if username has a pending request."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM access_requests WHERE username = ? AND status = 'pending'",
                (username,),
            )
            return cursor.fetchone() is not None

    def has_any_request(self, username: str) -> bool:
        """Check if username has any request (pending, approved, or denied)."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT 1 FROM access_requests WHERE username = ?", (username,))
            return cursor.fetchone() is not None

    def count_pending(self) -> int:
        """Count pending access requests."""
        with self.db.connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'")
            return cursor.fetchone()[0]

    def get_by_claim_token(self, claim_token_hash: str) -> Optional[AccessRequest]:
        """Get access request by claim token hash."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(
                self._AR_SELECT + " WHERE claim_token_hash = ?", (claim_token_hash,)
            )
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None

    def store_credentials(
        self, request_id: int, totp_secret: str, totp_uri: str, backup_codes_json: str
    ) -> bool:
        """Store TOTP credentials after approval for later claim."""
        with self.db.connection() as conn:
            cursor = conn.execute(
                """
                UPDATE access_requests
                SET totp_secret = ?, totp_uri = ?, backup_codes_json = ?
                WHERE id = ?
                """,
                (totp_secret, totp_uri, backup_codes_json, request_id),
            )
            return cursor.rowcount > 0

    def store_invite_metadata(self, request_id: int, can_download: bool) -> bool:
        """Store invite metadata (permissions set by admin) for use during claim."""
        import json

        metadata = json.dumps({"can_download": can_download, "invited": True})
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE access_requests SET backup_codes_json = ? WHERE id = ?",
                (metadata, request_id),
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
                (request_id,),
            )
            return cursor.rowcount > 0

    def get_pending_by_username_and_token(
        self, username: str, claim_token_hash: str
    ) -> Optional[AccessRequest]:
        """Get access request by username and claim token (for status check)."""
        with self.db.connection() as conn:
            # nosemgrep: sqlalchemy-execute-raw-query
            cursor = conn.execute(
                self._AR_SELECT + " WHERE username = ? AND claim_token_hash = ?",
                (username, claim_token_hash),
            )
            row = cursor.fetchone()
            return AccessRequest.from_row(row) if row else None
