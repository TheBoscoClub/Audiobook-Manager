"""
Encrypted Auth Database using SQLCipher

Provides secure storage for user credentials, sessions, and positions.
All data is encrypted at rest with AES-256.
"""
from __future__ import annotations

import os
import secrets
import hashlib
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Generator

try:
    import sqlcipher3 as sqlcipher
except ImportError:
    sqlcipher = None


class AuthDatabaseError(Exception):
    """Base exception for auth database errors."""


class EncryptionKeyError(AuthDatabaseError):
    """Error loading or generating encryption key."""


class AuthDatabase:
    """
    Encrypted SQLite database using SQLCipher.

    Key Management:
    - Production: Key stored in /etc/audiobooks/auth.key (root readable only)
    - Development: Key stored in project dev/auth-dev.key
    - Key is 64 hex characters (256 bits)

    Usage:
        db = AuthDatabase(db_path=os.environ.get("AUTH_DB_PATH", "auth.db"))
        db.initialize()

        with db.connection() as conn:
            cursor = conn.execute("SELECT * FROM users")
    """

    SCHEMA_VERSION = 1
    KEY_LENGTH = 32  # 256 bits

    def __init__(
        self,
        db_path: str,
        key_path: Optional[str] = None,
        is_dev: bool = False
    ):
        """
        Initialize auth database.

        Args:
            db_path: Path to the SQLCipher database file
            key_path: Path to encryption key file (auto-detected if None)
            is_dev: Development mode (relaxed key permissions)
        """
        if sqlcipher is None:
            raise AuthDatabaseError(
                "SQLCipher not available. Install with: pip install sqlcipher3"
            )

        self.db_path = Path(db_path)
        self.is_dev = is_dev

        if key_path is None:
            key_path = self._default_key_path()
        self.key_path = Path(key_path)

        self._key: Optional[str] = None

    def _default_key_path(self) -> str:
        """Determine default key path based on mode."""
        if self.is_dev:
            # Development: key in project dev directory
            return str(self.db_path.parent.parent / "dev" / "auth-dev.key")
        else:
            # Production: key in /etc/audiobooks
            return "/etc/audiobooks/auth.key"

    def _load_or_generate_key(self) -> str:
        """Load existing key or generate new one."""
        if self.key_path.exists():
            return self._load_key()
        else:
            return self._generate_key()

    def _load_key(self) -> str:
        """Load encryption key from file."""
        try:
            # Check file permissions in production
            if not self.is_dev:
                stat = self.key_path.stat()
                mode = stat.st_mode & 0o777
                if mode != 0o600:
                    raise EncryptionKeyError(
                        f"Key file {self.key_path} has insecure permissions "
                        f"({oct(mode)}). Should be 0600."
                    )

            key = self.key_path.read_text().strip()

            # Validate key format (64 hex chars = 256 bits)
            if len(key) != 64 or not all(c in '0123456789abcdef' for c in key.lower()):
                raise EncryptionKeyError("Invalid key format. Expected 64 hex characters.")

            return key

        except PermissionError:
            raise EncryptionKeyError(
                f"Cannot read key file {self.key_path}. Check permissions."
            )
        except FileNotFoundError:
            raise EncryptionKeyError(f"Key file not found: {self.key_path}")

    def _generate_key(self) -> str:
        """Generate new encryption key and save to file."""
        key = secrets.token_hex(self.KEY_LENGTH)

        # Ensure parent directory exists
        self.key_path.parent.mkdir(parents=True, exist_ok=True)

        # Write key with restricted permissions
        self.key_path.touch(mode=0o600)
        self.key_path.write_text(key)

        if not self.is_dev:
            # Double-check permissions in production
            os.chmod(self.key_path, 0o600)

        return key

    @property
    def key(self) -> str:
        """Get encryption key (loading or generating as needed)."""
        if self._key is None:
            self._key = self._load_or_generate_key()
        return self._key

    def _create_connection(self) -> sqlcipher.Connection:
        """Create new encrypted database connection."""
        conn = sqlcipher.connect(str(self.db_path))

        # CRITICAL: Set encryption key FIRST, before any other operations
        conn.execute(f"PRAGMA key = \"x'{self.key}'\"")

        # Verify encryption is working
        try:
            conn.execute("SELECT count(*) FROM sqlite_master")
        except sqlcipher.DatabaseError as e:
            conn.close()
            if "file is not a database" in str(e).lower():
                raise AuthDatabaseError(
                    "Cannot decrypt database. Wrong key or database is corrupted."
                )
            raise

        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")

        return conn

    @contextmanager
    def connection(self) -> Generator[sqlcipher.Connection, None, None]:
        """
        Context manager for database connections.

        Usage:
            with db.connection() as conn:
                cursor = conn.execute("SELECT * FROM users")
        """
        conn = self._create_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> bool:
        """
        Initialize the database schema.

        Returns:
            True if database was created, False if already existed
        """
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        created = not self.db_path.exists()

        # Load schema SQL
        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text()

        with self.connection() as conn:
            conn.executescript(schema_sql)

        return created

    def verify(self) -> dict:
        """
        Verify database integrity and return status.

        Returns:
            Dict with verification results
        """
        result = {
            "db_exists": self.db_path.exists(),
            "key_exists": self.key_path.exists(),
            "can_connect": False,
            "schema_version": None,
            "table_count": 0,
            "user_count": 0,
            "errors": []
        }

        if not result["db_exists"]:
            result["errors"].append("Database file does not exist")
            return result

        if not result["key_exists"]:
            result["errors"].append("Key file does not exist")
            return result

        try:
            with self.connection() as conn:
                result["can_connect"] = True

                # Get schema version
                cursor = conn.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                )
                row = cursor.fetchone()
                result["schema_version"] = row[0] if row else 0

                # Count tables
                cursor = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                )
                result["table_count"] = cursor.fetchone()[0]

                # Count users
                cursor = conn.execute("SELECT count(*) FROM users")
                result["user_count"] = cursor.fetchone()[0]

        except Exception as e:
            result["errors"].append(str(e))

        return result


# Module-level singleton for convenience
_auth_db: Optional[AuthDatabase] = None


def get_auth_db(
    db_path: Optional[str] = None,
    key_path: Optional[str] = None,
    is_dev: bool = False
) -> AuthDatabase:
    """
    Get or create the auth database singleton.

    Args:
        db_path: Path to database (uses default if None)
        key_path: Path to key file (auto-detected if None)
        is_dev: Development mode flag

    Returns:
        AuthDatabase instance
    """
    global _auth_db

    if _auth_db is None:
        if db_path is None:
            # Default paths based on mode
            if is_dev:
                db_path = str(Path(__file__).parent.parent / "backend" / "auth-dev.db")
            else:
                var_dir = os.environ.get("AUDIOBOOKS_VAR_DIR", "/var/lib/audiobooks")
                db_path = os.path.join(var_dir, "auth.db")

        _auth_db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=is_dev)

    return _auth_db


def hash_token(token: str) -> str:
    """
    Hash a token for storage.

    Args:
        token: The raw token string

    Returns:
        SHA-256 hash of the token (64 hex chars)
    """
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_token() -> tuple[str, str]:
    """
    Generate a new session token.

    Returns:
        Tuple of (raw_token, token_hash)
        - raw_token: Send to client
        - token_hash: Store in database
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_token(raw_token)
    return raw_token, token_hash


def generate_verification_token() -> tuple[str, str]:
    """
    Generate a verification token for registration.

    Returns:
        Tuple of (raw_token, token_hash)
    """
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    raw_token = "".join(secrets.choice(alphabet) for _ in range(32))
    token_hash = hash_token(raw_token)
    return raw_token, token_hash
