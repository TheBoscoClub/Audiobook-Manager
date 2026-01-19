"""
Backup Codes Module for Account Recovery

Generates and verifies single-use backup codes for users who choose
not to store recovery contact information.

Each user receives 8 backup codes at registration. Each code can only
be used once to recover access to their account.
"""

import secrets
import hashlib
from typing import List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime

from .database import AuthDatabase, hash_token


# Number of backup codes to generate
NUM_BACKUP_CODES = 8

# Code format: 4 groups of 4 alphanumeric characters (e.g., "ABCD-1234-EFGH-5678")
CODE_GROUP_LENGTH = 4
CODE_NUM_GROUPS = 4


def generate_backup_code() -> str:
    """
    Generate a single backup code.

    Format: XXXX-XXXX-XXXX-XXXX (alphanumeric, uppercase)
    Total entropy: ~77 bits (16 chars from 36-char alphabet)

    Returns:
        Formatted backup code string
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No 0, O, 1, I to avoid confusion
    groups = []
    for _ in range(CODE_NUM_GROUPS):
        group = ''.join(secrets.choice(alphabet) for _ in range(CODE_GROUP_LENGTH))
        groups.append(group)
    return '-'.join(groups)


def normalize_code(code: str) -> str:
    """
    Normalize a backup code for comparison.

    Removes dashes/spaces, converts to uppercase.

    Args:
        code: User-entered code (may have formatting variations)

    Returns:
        Normalized code string
    """
    return code.upper().replace('-', '').replace(' ', '')


def hash_backup_code(code: str) -> str:
    """
    Hash a backup code for storage.

    Args:
        code: Raw or normalized backup code

    Returns:
        SHA-256 hash of the normalized code
    """
    normalized = normalize_code(code)
    return hashlib.sha256(normalized.encode()).hexdigest()


def generate_backup_codes(count: int = NUM_BACKUP_CODES) -> Tuple[List[str], List[str]]:
    """
    Generate a set of backup codes.

    Args:
        count: Number of codes to generate

    Returns:
        Tuple of (raw_codes, code_hashes)
        - raw_codes: List of formatted codes to show user
        - code_hashes: List of hashes to store in database
    """
    raw_codes = []
    code_hashes = []

    for _ in range(count):
        code = generate_backup_code()
        raw_codes.append(code)
        code_hashes.append(hash_backup_code(code))

    return raw_codes, code_hashes


@dataclass
class BackupCode:
    """Represents a backup code record."""
    id: Optional[int] = None
    user_id: int = 0
    code_hash: str = ""
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: tuple) -> "BackupCode":
        """Create BackupCode from database row."""
        return cls(
            id=row[0],
            user_id=row[1],
            code_hash=row[2],
            used_at=datetime.fromisoformat(row[3]) if row[3] else None,
            created_at=datetime.fromisoformat(row[4]) if row[4] else None,
        )

    @property
    def is_used(self) -> bool:
        """Check if this code has been used."""
        return self.used_at is not None


class BackupCodeRepository:
    """Repository for backup code operations."""

    def __init__(self, db: AuthDatabase):
        self.db = db

    def create_codes_for_user(self, user_id: int, count: int = NUM_BACKUP_CODES) -> List[str]:
        """
        Generate and store backup codes for a user.

        Deletes any existing unused codes first.

        Args:
            user_id: User ID
            count: Number of codes to generate

        Returns:
            List of raw backup codes to display to user
        """
        raw_codes, code_hashes = generate_backup_codes(count)

        with self.db.connection() as conn:
            # Delete existing unused codes
            conn.execute(
                "DELETE FROM backup_codes WHERE user_id = ? AND used_at IS NULL",
                (user_id,)
            )

            # Insert new codes
            for code_hash in code_hashes:
                conn.execute(
                    "INSERT INTO backup_codes (user_id, code_hash) VALUES (?, ?)",
                    (user_id, code_hash)
                )

        return raw_codes

    def verify_and_consume(self, user_id: int, code: str) -> bool:
        """
        Verify a backup code and mark it as used.

        Args:
            user_id: User ID
            code: User-entered backup code

        Returns:
            True if code was valid and consumed, False otherwise
        """
        code_hash = hash_backup_code(code)

        with self.db.connection() as conn:
            # Find unused code matching hash
            cursor = conn.execute(
                """
                SELECT id FROM backup_codes
                WHERE user_id = ? AND code_hash = ? AND used_at IS NULL
                """,
                (user_id, code_hash)
            )
            row = cursor.fetchone()

            if row is None:
                return False

            # Mark as used
            conn.execute(
                "UPDATE backup_codes SET used_at = ? WHERE id = ?",
                (datetime.now().isoformat(), row[0])
            )

        return True

    def get_remaining_count(self, user_id: int) -> int:
        """
        Get count of remaining unused backup codes.

        Args:
            user_id: User ID

        Returns:
            Number of unused backup codes
        """
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM backup_codes WHERE user_id = ? AND used_at IS NULL",
                (user_id,)
            )
            return cursor.fetchone()[0]

    def get_all_for_user(self, user_id: int) -> List[BackupCode]:
        """
        Get all backup codes for a user (for admin view).

        Args:
            user_id: User ID

        Returns:
            List of BackupCode objects
        """
        with self.db.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM backup_codes WHERE user_id = ? ORDER BY created_at",
                (user_id,)
            )
            return [BackupCode.from_row(row) for row in cursor.fetchall()]

    def delete_all_for_user(self, user_id: int) -> int:
        """
        Delete all backup codes for a user.

        Args:
            user_id: User ID

        Returns:
            Number of codes deleted
        """
        with self.db.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM backup_codes WHERE user_id = ?",
                (user_id,)
            )
            return cursor.rowcount


def format_codes_for_display(codes: List[str]) -> str:
    """
    Format backup codes for display to user.

    Args:
        codes: List of backup codes

    Returns:
        Formatted string for display/printing
    """
    lines = [
        "╔══════════════════════════════════════════════════════════╗",
        "║              BACKUP RECOVERY CODES                       ║",
        "║                                                          ║",
        "║  Save these codes in a safe place. Each code can only   ║",
        "║  be used once to recover your account.                  ║",
        "║                                                          ║",
        "╠══════════════════════════════════════════════════════════╣",
    ]

    for i, code in enumerate(codes, 1):
        lines.append(f"║  {i}. {code}                              ║")

    lines.extend([
        "║                                                          ║",
        "║  ⚠️  If you lose these codes AND your authenticator,    ║",
        "║     your account cannot be recovered.                   ║",
        "╚══════════════════════════════════════════════════════════╝",
    ])

    return '\n'.join(lines)
