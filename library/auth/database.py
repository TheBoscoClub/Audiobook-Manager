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

    SCHEMA_VERSION = 10
    KEY_LENGTH = 32  # 256 bits

    def __init__(
        self, db_path: str, key_path: Optional[str] = None, is_dev: bool = False
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
            if len(key) != 64 or not all(c in "0123456789abcdef" for c in key.lower()):
                raise EncryptionKeyError(
                    "Invalid key format. Expected 64 hex characters."
                )

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

        # CRITICAL: Set encryption key FIRST, before any other operations.
        # SQLCipher PRAGMA key does NOT accept parameterized bind values
        # (documented limitation). `self.key` is a 64-hex string loaded from
        # auth.key (0600, root:audiobooks), never user-controlled. The only
        # way to set the encryption key is via string interpolation here.
        conn.execute(f"PRAGMA key = \"x'{self.key}'\"")  # nosec B608  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query

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

        if not created:
            # Existing database: apply migrations BEFORE schema.sql
            # (schema.sql inserts current version, which would mask pending migrations)
            self._apply_migrations()

        # Load and apply schema SQL (safe for existing DBs: CREATE TABLE IF NOT EXISTS)
        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text()

        with self.connection() as conn:
            conn.executescript(schema_sql)

        # Apply additive migrations for columns/tables not covered by schema.sql
        # (ALTER TABLE is not idempotent; schema.sql only handles CREATE TABLE IF NOT EXISTS)
        with self.connection() as conn:
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp"
                " ON audit_log(timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)"
            )

            # Migration: add user_hidden_books table if not exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_hidden_books (
                    user_id INTEGER NOT NULL,
                    audiobook_id INTEGER NOT NULL,
                    hidden_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, audiobook_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Migration: add last_audit_seen_id to users if not exists
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN last_audit_seen_id INTEGER DEFAULT 0"
                )
            except Exception:
                pass  # Column already exists

            # Migration: add system_settings table if not exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO system_settings (setting_key, setting_value) "
                "VALUES ('multi_session_default', 'false')"
            )
            # Migration: add multi_session column to users if not exists
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
            if "multi_session" not in cols:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN multi_session TEXT NOT NULL DEFAULT 'default'"
                )
            if "preferred_locale" not in cols:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN preferred_locale TEXT DEFAULT 'en'"
                )

        return created

    def _apply_migrations(self) -> None:
        """Apply any pending database migrations in version order."""
        migrations_dir = Path(__file__).parent / "migrations"
        if not migrations_dir.exists():
            return

        conn = self._create_connection()
        try:
            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            current_version = cursor.fetchone()[0] or 0

            for migration_file in sorted(migrations_dir.glob("*.sql")):
                # Extract version from filename (e.g., 004_xxx.sql -> 4)
                version = int(migration_file.stem.split("_")[0])
                if version > current_version:
                    if version == 5:
                        self._migrate_v4_to_v5(conn)
                    elif version == 7:
                        self._migrate_v6_to_v7(conn)
                    else:
                        migration_sql = migration_file.read_text()
                        conn.executescript(migration_sql)
                    current_version = version
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _v5_needs_users_recreate(conn) -> bool:
        """Test whether the users table already supports magic_link auth type."""
        try:
            conn.execute(
                "INSERT INTO users (username, auth_type, auth_credential) "
                "VALUES ('__migration_test__', 'magic_link', X'00')"
            )
            conn.execute("DELETE FROM users WHERE username = '__migration_test__'")
            return False
        except Exception:
            return True

    @staticmethod
    def _v5_recreate_users_table(conn, logger) -> None:
        """Recreate users table with expanded CHECK constraint for magic_link."""
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("""
            CREATE TABLE users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                auth_type TEXT NOT NULL CHECK (
                    auth_type IN ('passkey', 'fido2', 'totp', 'magic_link')
                ),
                auth_credential BLOB NOT NULL,
                can_download BOOLEAN DEFAULT FALSE,
                is_admin BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                recovery_email TEXT,
                recovery_phone TEXT,
                recovery_enabled BOOLEAN DEFAULT FALSE,
                CHECK (length(username) >= 3 AND length(username) <= 24)
            )
        """)
        conn.execute("INSERT INTO users_new SELECT * FROM users")
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        logger.info("Recreated users table with magic_link support")

    # Allowlist of tables/columns/definitions permitted in schema migrations.
    # Defense-in-depth: all call sites pass hardcoded constants, but an
    # allowlist check prevents a future caller from accidentally passing
    # user-controlled values into the interpolated DDL below.
    _V5_MIGRATION_ALLOWLIST: tuple[tuple[str, str, str], ...] = (
        ("sessions", "is_persistent", "BOOLEAN DEFAULT 0"),
        ("access_requests", "preferred_auth_method", "TEXT DEFAULT 'totp'"),
    )
    _V5_ALLOWED_TABLES_FOR_PRAGMA: frozenset[str] = frozenset(
        {
            "sessions",
            "access_requests",
            "users",
            "user_listening_history",
            "user_downloads",
        }
    )

    @staticmethod
    def _v5_add_column_if_missing(
        conn, table: str, column: str, definition: str, logger
    ) -> None:
        """Add a column to a table if it doesn't already exist."""
        if (table, column, definition) not in AuthDatabase._V5_MIGRATION_ALLOWLIST:
            raise ValueError(
                f"Schema migration not permitted: {table}.{column} {definition!r} "
                "is not in _V5_MIGRATION_ALLOWLIST"
            )
        if table not in AuthDatabase._V5_ALLOWED_TABLES_FOR_PRAGMA:
            raise ValueError(f"PRAGMA table_info on unlisted table: {table}")
        cols = {
            row[1]
            for row in conn.execute(  # nosec B608  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                f"PRAGMA table_info({table})"  # table validated against allowlist above
            ).fetchall()
        }
        if column not in cols:
            # All three interpolated values validated against allowlist above.
            conn.execute(  # nosec B608  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )
            logger.info("Added %s to %s", column, table)

    @staticmethod
    def _v5_table_exists(conn, table: str) -> bool:
        """Check if a table exists in the database."""
        return bool(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
        )

    def _migrate_v4_to_v5(self, conn) -> None:
        """
        Migrate schema from v4 to v5: magic_link auth, persistent sessions.
        """
        import shutil
        import logging

        logger = logging.getLogger("auth.migration")

        # Pre-migration counts for validation
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        ar_exists = self._v5_table_exists(conn, "access_requests")

        logger.info("v4→v5 migration: %d users, %d sessions", user_count, session_count)

        # Backup database before migration
        backup_path = str(self.db_path) + ".pre-v5-backup"
        if self.db_path.exists() and not Path(backup_path).exists():
            shutil.copy2(str(self.db_path), backup_path)
            logger.info("Backup saved to %s", backup_path)

        # Step 1: Recreate users table if needed
        if self._v5_needs_users_recreate(conn):
            self._v5_recreate_users_table(conn, logger)
        else:
            logger.info("users table already supports magic_link")

        # Step 2: Add is_persistent to sessions
        self._v5_add_column_if_missing(
            conn, "sessions", "is_persistent", "BOOLEAN DEFAULT 0", logger
        )

        # Step 3: Add preferred_auth_method to access_requests
        if ar_exists:
            self._v5_add_column_if_missing(
                conn,
                "access_requests",
                "preferred_auth_method",
                "TEXT DEFAULT 'totp'",
                logger,
            )

        # Step 4: Update schema version
        conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (5)")

        # Step 5: Post-migration validation
        post_user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        post_session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

        if post_user_count != user_count:
            raise RuntimeError(
                f"Migration validation failed: users {user_count} → {post_user_count}"
            )
        if post_session_count != session_count:
            raise RuntimeError(
                f"Migration validation failed: sessions"
                f" {session_count} → {post_session_count}"
            )

        logger.info(
            "v4→v5 migration complete. Validated: %d users, %d sessions intact.",
            post_user_count,
            post_session_count,
        )

    def _migrate_v6_to_v7(self, conn) -> None:
        """
        Migrate schema from v6 to v7: add denormalized title to activity tables.

        Adds a title TEXT column to user_listening_history and user_downloads
        if those tables exist. Tables may not exist if upgrading from a schema
        that predates migration 004 (per-user state).
        """
        import logging

        logger = logging.getLogger("auth.migration")

        for table in ("user_listening_history", "user_downloads"):
            # Check if table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cursor.fetchone():
                logger.info("v6→v7: table %s does not exist, skipping", table)
                continue

            # Check if column already exists
            # table is from hardcoded tuple ("user_listening_history", "user_downloads")
            cols = {
                row[1]
                for row in conn.execute(  # nosec B608  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    f"PRAGMA table_info({table})"
                )
            }
            if "title" not in cols:
                conn.execute(  # nosec B608  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                    f"ALTER TABLE {table} ADD COLUMN title TEXT"
                )
                logger.info("v6→v7: added title column to %s", table)
            else:
                logger.info("v6→v7: %s already has title column", table)

        conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (7)")
        logger.info("v6→v7 migration complete")

    def verify(self) -> dict:
        """
        Verify database integrity and return status.

        Returns:
            Dict with verification results
        """
        result: dict[str, object] = {
            "db_exists": self.db_path.exists(),
            "key_exists": self.key_path.exists(),
            "can_connect": False,
            "schema_version": None,
            "table_count": 0,
            "user_count": 0,
            "errors": [],
        }
        errors: list[str] = []
        result["errors"] = errors

        if not result["db_exists"]:
            errors.append("Database file does not exist")
            return result

        if not result["key_exists"]:
            errors.append("Key file does not exist")
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
            errors.append(str(e))

        return result


# Module-level singleton for convenience
_auth_db: Optional[AuthDatabase] = None


def get_auth_db(
    db_path: Optional[str] = None, key_path: Optional[str] = None, is_dev: bool = False
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
