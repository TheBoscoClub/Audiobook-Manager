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
    PendingRecovery,
    PendingRecoveryRepository,
    AccessRequestStatus,
    AccessRequest,
    AccessRequestRepository,
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

from .passkey import (
    WebAuthnCredential,
    WebAuthnChallenge,
    create_registration_options as webauthn_registration_options,
    verify_registration as webauthn_verify_registration,
    create_authentication_options as webauthn_authentication_options,
    verify_authentication as webauthn_verify_authentication,
    get_pending_challenge as webauthn_get_pending_challenge,
    clear_challenge as webauthn_clear_challenge,
    cleanup_expired_challenges as webauthn_cleanup_challenges,
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
    "PendingRecovery",
    # Repositories
    "UserRepository",
    "SessionRepository",
    "PositionRepository",
    "NotificationRepository",
    "InboxRepository",
    "PendingRegistrationRepository",
    "PendingRecoveryRepository",
    # Access Requests
    "AccessRequestStatus",
    "AccessRequest",
    "AccessRequestRepository",
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
