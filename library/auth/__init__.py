"""
Audiobook Manager - Authentication Module

Provides encrypted user authentication and session management using SQLCipher.
"""

from .database import (
    AuthDatabase,
    get_auth_db,
    hash_token,
    generate_session_token,
    generate_verification_token,
)

from .models import (
    AuthType,
    NotificationType,
    InboxStatus,
    ReplyMethod,
    User,
    UserRepository,
    Session,
    SessionRepository,
    UserPosition,
    PositionRepository,
    Notification,
    NotificationRepository,
    InboxMessage,
    InboxRepository,
    PendingRegistration,
    PendingRegistrationRepository,
)

from .totp import (
    generate_secret as generate_totp_secret,
    secret_to_base32,
    get_provisioning_uri,
    verify_code as verify_totp_code,
    setup_totp,
    TOTPAuthenticator,
)

from .backup_codes import (
    BackupCode,
    BackupCodeRepository,
    generate_backup_code,
    generate_backup_codes,
    hash_backup_code,
    normalize_code as normalize_backup_code,
    format_codes_for_display,
    NUM_BACKUP_CODES,
)

__all__ = [
    # Database
    "AuthDatabase",
    "get_auth_db",
    "hash_token",
    "generate_session_token",
    "generate_verification_token",
    # Enums
    "AuthType",
    "NotificationType",
    "InboxStatus",
    "ReplyMethod",
    # Models
    "User",
    "Session",
    "UserPosition",
    "Notification",
    "InboxMessage",
    "PendingRegistration",
    # Repositories
    "UserRepository",
    "SessionRepository",
    "PositionRepository",
    "NotificationRepository",
    "InboxRepository",
    "PendingRegistrationRepository",
    # TOTP
    "generate_totp_secret",
    "secret_to_base32",
    "get_provisioning_uri",
    "verify_totp_code",
    "setup_totp",
    "TOTPAuthenticator",
    # Backup Codes
    "BackupCode",
    "BackupCodeRepository",
    "generate_backup_code",
    "generate_backup_codes",
    "hash_backup_code",
    "normalize_backup_code",
    "format_codes_for_display",
    "NUM_BACKUP_CODES",
]
