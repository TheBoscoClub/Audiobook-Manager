"""
Audiobook Manager - Authentication Module

Provides encrypted user authentication and session management using SQLCipher.
"""

from .audit import AuditLogRepository
from .backup_codes import (
    NUM_BACKUP_CODES,
    BackupCode,
    BackupCodeRepository,
    format_codes_for_display,
    generate_backup_code,
    generate_backup_codes,
    hash_backup_code,
)
from .backup_codes import normalize_code as normalize_backup_code
from .database import (
    AuthDatabase,
    generate_session_token,
    generate_verification_token,
    get_auth_db,
    hash_token,
)
from .models import (
    AccessRequest,
    AccessRequestRepository,
    AccessRequestStatus,
    AuditLog,
    AuthType,
    DownloadRepository,
    HiddenBookRepository,
    InboxMessage,
    InboxRepository,
    InboxStatus,
    ListeningHistoryRepository,
    Notification,
    NotificationRepository,
    NotificationType,
    PendingRecovery,
    PendingRecoveryRepository,
    PendingRegistration,
    PendingRegistrationRepository,
    PositionRepository,
    PreferencesRepository,
    ReplyMethod,
    Session,
    SessionRepository,
    User,
    UserDownload,
    UserListeningHistory,
    UserPosition,
    UserPreferences,
    UserRepository,
    UserSettingsRepository,
)
from .passkey import (
    WebAuthnChallenge,
    WebAuthnCredential,
)
from .passkey import cleanup_expired_challenges as webauthn_cleanup_challenges
from .passkey import clear_challenge as webauthn_clear_challenge
from .passkey import create_authentication_options as webauthn_authentication_options
from .passkey import create_registration_options as webauthn_registration_options
from .passkey import get_pending_challenge as webauthn_get_pending_challenge
from .passkey import verify_authentication as webauthn_verify_authentication
from .passkey import verify_registration as webauthn_verify_registration
from .totp import (
    TOTPAuthenticator,
)
from .totp import generate_secret as generate_totp_secret
from .totp import (
    get_provisioning_uri,
    secret_to_base32,
    setup_totp,
)
from .totp import verify_code as verify_totp_code

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
    "PendingRecovery",
    # Repositories
    "UserRepository",
    "SessionRepository",
    "PositionRepository",
    "UserListeningHistory",
    "ListeningHistoryRepository",
    "UserDownload",
    "DownloadRepository",
    "UserPreferences",
    "PreferencesRepository",
    "HiddenBookRepository",
    "NotificationRepository",
    "InboxRepository",
    "PendingRegistrationRepository",
    "PendingRecoveryRepository",
    # Access Requests
    "AccessRequestStatus",
    "AccessRequest",
    "AccessRequestRepository",
    # Audit Log
    "AuditLog",
    "UserSettingsRepository",
    "AuditLogRepository",
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
    # WebAuthn
    "WebAuthnCredential",
    "WebAuthnChallenge",
    "webauthn_registration_options",
    "webauthn_verify_registration",
    "webauthn_authentication_options",
    "webauthn_verify_authentication",
    "webauthn_get_pending_challenge",
    "webauthn_clear_challenge",
    "webauthn_cleanup_challenges",
]
