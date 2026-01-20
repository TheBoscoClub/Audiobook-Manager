"""
Authentication API Blueprint

Provides endpoints for:
- User login (TOTP verification)
- User registration (with email/SMS verification)
- Session management (logout, session info)
- Password-less authentication flow

All authentication data is stored in the encrypted auth.db (SQLCipher).
"""

import os
import sys
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, Any

from flask import Blueprint, Response, jsonify, request, g, current_app

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from auth import (
    AuthDatabase,
    AuthType,
    User,
    UserRepository,
    Session,
    SessionRepository,
    PendingRegistration,
    PendingRegistrationRepository,
    PendingRecovery,
    PendingRecoveryRepository,
    NotificationRepository,
    UserPosition,
    PositionRepository,
    hash_token,
)
from auth.totp import (
    setup_totp,
    verify_code as verify_totp,
    secret_to_base32,
    get_provisioning_uri,
    generate_qr_code,
)
from auth.backup_codes import (
    BackupCodeRepository,
    generate_backup_codes,
    format_codes_for_display,
)

# Blueprint
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# Module-level state (initialized by init_auth_routes)
_auth_db: Optional[AuthDatabase] = None
_session_cookie_name = "audiobooks_session"
_session_cookie_secure = True  # Always use secure cookies
_session_cookie_httponly = True
_session_cookie_samesite = "Lax"


def init_auth_routes(
    auth_db_path: Path,
    auth_key_path: Path,
    is_dev: bool = False,
) -> None:
    """
    Initialize auth routes with dependencies.

    Args:
        auth_db_path: Path to encrypted auth database
        auth_key_path: Path to encryption key file
        is_dev: Development mode (relaxed security)
    """
    global _auth_db, _session_cookie_secure

    _auth_db = AuthDatabase(
        db_path=str(auth_db_path),
        key_path=str(auth_key_path),
        is_dev=is_dev,
    )
    _auth_db.initialize()

    # In dev mode, allow non-secure cookies for localhost
    if is_dev:
        _session_cookie_secure = False


def get_auth_db() -> AuthDatabase:
    """Get the auth database instance."""
    if _auth_db is None:
        raise RuntimeError("Auth routes not initialized. Call init_auth_routes() first.")
    return _auth_db


# =============================================================================
# Session Middleware
# =============================================================================

def get_current_user() -> Optional[User]:
    """
    Get the currently authenticated user from the session cookie.

    Returns:
        User object if authenticated, None otherwise
    """
    if hasattr(g, '_current_user'):
        return g._current_user

    g._current_user = None
    g._current_session = None

    # Get session token from cookie
    token = request.cookies.get(_session_cookie_name)
    if not token:
        return None

    db = get_auth_db()
    session_repo = SessionRepository(db)
    user_repo = UserRepository(db)

    # Look up session
    session = session_repo.get_by_token(token)
    if session is None:
        return None

    # Check if session is stale (30 minute grace period)
    if session.is_stale(grace_minutes=30):
        session.invalidate(db)
        return None

    # Get user
    user = user_repo.get_by_id(session.user_id)
    if user is None:
        session.invalidate(db)
        return None

    # Update last seen
    session.touch(db)

    g._current_user = user
    g._current_session = session
    return user


def get_current_session() -> Optional[Session]:
    """Get the current session (call get_current_user first)."""
    if not hasattr(g, '_current_session'):
        get_current_user()
    return g._current_session


def login_required(f: Callable) -> Callable:
    """
    Decorator to require authentication for a route.

    Returns 401 if not authenticated.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f: Callable) -> Callable:
    """
    Decorator to require admin privileges.

    Returns 401 if not authenticated, 403 if not admin.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        if not user.is_admin:
            return jsonify({"error": "Admin privileges required"}), 403
        return f(*args, **kwargs)
    return decorated


def localhost_only(f: Callable) -> Callable:
    """
    Decorator to restrict endpoint to localhost access only.

    Used for admin/back-office functions.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        # Check if request is from localhost
        remote_addr = request.remote_addr
        if remote_addr not in ('127.0.0.1', '::1', 'localhost'):
            # Also check X-Forwarded-For if behind proxy
            forwarded = request.headers.get('X-Forwarded-For', '')
            if forwarded:
                # Take the first address (client IP)
                remote_addr = forwarded.split(',')[0].strip()

            if remote_addr not in ('127.0.0.1', '::1', 'localhost'):
                return jsonify({"error": "Access denied"}), 404  # Return 404 to hide existence
        return f(*args, **kwargs)
    return decorated


def auth_if_enabled(f: Callable) -> Callable:
    """
    Decorator to require authentication only if auth is enabled.

    When AUTH_ENABLED is False (single-user mode), allows through without auth.
    When AUTH_ENABLED is True (multi-user mode), requires login.

    Use this for endpoints that should work in both single-user and multi-user modes.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not current_app.config.get("AUTH_ENABLED", False):
            # Auth disabled - allow through
            return f(*args, **kwargs)
        # Auth enabled - require login
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def download_permission_required(f: Callable) -> Callable:
    """
    Decorator to require download permission.

    When AUTH_ENABLED is False, allows through (single-user has all permissions).
    When AUTH_ENABLED is True, requires login AND can_download permission.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not current_app.config.get("AUTH_ENABLED", False):
            # Auth disabled - allow through
            return f(*args, **kwargs)
        # Auth enabled - require login + download permission
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        if not user.can_download:
            return jsonify({"error": "Download permission required"}), 403
        return f(*args, **kwargs)
    return decorated


def admin_if_enabled(f: Callable) -> Callable:
    """
    Decorator to require admin only if auth is enabled.

    When AUTH_ENABLED is False, allows through (single-user is admin).
    When AUTH_ENABLED is True, requires login AND admin flag.
    """
    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not current_app.config.get("AUTH_ENABLED", False):
            # Auth disabled - allow through (single-user mode = admin)
            return f(*args, **kwargs)
        # Auth enabled - require admin
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        if not user.is_admin:
            return jsonify({"error": "Admin privileges required"}), 403
        return f(*args, **kwargs)
    return decorated


def set_session_cookie(response: Response, token: str) -> Response:
    """Set the session cookie on a response."""
    response.set_cookie(
        _session_cookie_name,
        token,
        httponly=_session_cookie_httponly,
        secure=_session_cookie_secure,
        samesite=_session_cookie_samesite,
        max_age=None,  # Session cookie (cleared on browser close)
        path="/",
    )
    return response


def clear_session_cookie(response: Response) -> Response:
    """Clear the session cookie."""
    response.delete_cookie(_session_cookie_name, path="/")
    return response


# =============================================================================
# Auth Endpoints
# =============================================================================

@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Authenticate user with TOTP code.

    Request body:
        {
            "username": "string",
            "code": "123456"  // TOTP code
        }

    Returns:
        200: {"success": true, "user": {...}}
        400: {"error": "Missing username or code"}
        401: {"error": "Invalid credentials"}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    code = data.get("code", "").strip()

    if not username or not code:
        return jsonify({"error": "Username and code are required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    # Find user
    user = user_repo.get_by_username(username)
    if user is None:
        # Don't reveal if user exists
        return jsonify({"error": "Invalid credentials"}), 401

    # Verify TOTP code
    if user.auth_type == AuthType.TOTP:
        if not verify_totp(user.auth_credential, code):
            return jsonify({"error": "Invalid credentials"}), 401
    else:
        # Passkey/FIDO2 not implemented yet
        return jsonify({"error": "Authentication method not supported"}), 400

    # Create session (invalidates any existing session)
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
    )

    # Update last login
    user.update_last_login(db)

    # Build response
    response = jsonify({
        "success": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "can_download": user.can_download,
            "is_admin": user.is_admin,
        }
    })

    # Set session cookie
    return set_session_cookie(response, token)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """
    Log out the current user.

    Returns:
        200: {"success": true}
    """
    session = get_current_session()
    if session:
        session.invalidate(get_auth_db())

    response = jsonify({"success": True})
    return clear_session_cookie(response)


@auth_bp.route("/me", methods=["GET"])
@login_required
def get_current_user_info():
    """
    Get information about the currently authenticated user.

    Returns:
        200: {"user": {...}, "session": {...}}
    """
    user = get_current_user()
    session = get_current_session()

    # Get active notifications
    db = get_auth_db()
    notif_repo = NotificationRepository(db)
    notifications = notif_repo.get_active_for_user(user.id)

    return jsonify({
        "user": {
            "id": user.id,
            "username": user.username,
            "can_download": user.can_download,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
        },
        "session": {
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "last_seen": session.last_seen.isoformat() if session.last_seen else None,
        },
        "notifications": [
            {
                "id": n.id,
                "message": n.message,
                "type": n.type.value,
                "dismissable": n.dismissable,
                "priority": n.priority,
            }
            for n in notifications
        ]
    })


@auth_bp.route("/check", methods=["GET"])
def check_auth():
    """
    Check if the user is authenticated (lightweight endpoint).

    Returns:
        200: {"authenticated": true, "username": "..."} or {"authenticated": false}
    """
    user = get_current_user()
    if user:
        return jsonify({
            "authenticated": True,
            "username": user.username,
            "is_admin": user.is_admin,
        })
    return jsonify({"authenticated": False})


# =============================================================================
# Registration Endpoints
# =============================================================================

@auth_bp.route("/register/start", methods=["POST"])
def start_registration():
    """
    Start the registration process.

    In a full implementation, this would send a verification email/SMS.
    For now, it creates a pending registration and returns the token directly
    (for development/testing).

    Request body:
        {
            "username": "string"
        }

    Returns:
        200: {"success": true, "message": "...", "verify_token": "..." (dev only)}
        400: {"error": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()

    # Validate username
    if len(username) < 5:
        return jsonify({"error": "Username must be at least 5 characters"}), 400
    if len(username) > 16:
        return jsonify({"error": "Username must be at most 16 characters"}), 400
    if not all(32 <= ord(c) <= 126 for c in username):
        return jsonify({"error": "Username must contain only printable ASCII characters"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)
    reg_repo = PendingRegistrationRepository(db)

    # Check if username exists
    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 400

    # Clean up any existing pending registrations for this username
    reg_repo.delete_for_username(username)

    # Create pending registration (15 minute expiry)
    reg, token = PendingRegistration.create(db, username, expiry_minutes=15)

    # In production, send verification email/SMS here
    # For now, return the token directly (dev mode)
    is_dev = current_app.config.get("AUTH_DEV_MODE", False)

    response_data = {
        "success": True,
        "message": "Registration started. Please check your email/SMS for verification.",
        "expires_in_minutes": 15,
    }

    if is_dev:
        response_data["verify_token"] = token
        response_data["_dev_note"] = "Token returned directly in dev mode. In production, it would be sent via email/SMS."

    return jsonify(response_data)


@auth_bp.route("/register/verify", methods=["POST"])
def verify_registration():
    """
    Verify registration token and complete account setup.

    Request body:
        {
            "token": "verification_token",
            "auth_type": "totp",          // Only "totp" supported currently
            "recovery_email": "optional",  // Store for magic link recovery
            "recovery_phone": "optional",  // Store for magic link recovery
            "include_qr": false           // Include QR code as base64 PNG
        }

    Recovery options:
        - If recovery_email or recovery_phone is provided, user can use magic link recovery
        - If neither is provided, backup codes are the only recovery method
        - Backup codes are ALWAYS generated regardless of recovery settings

    Returns:
        200: {
            "success": true,
            "username": "...",
            "totp_secret": "...",      // Base32 secret for authenticator
            "totp_uri": "...",         // Provisioning URI for QR code
            "totp_qr": "...",          // Base64 PNG (if requested)
            "backup_codes": [...],     // 8 single-use recovery codes
            "recovery_enabled": bool,  // Whether contact recovery is enabled
            "warning": "..."           // Important security notice
        }
        400: {"error": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    auth_type = data.get("auth_type", "totp").strip().lower()
    include_qr = data.get("include_qr", False)

    # Recovery preferences (optional)
    recovery_email = data.get("recovery_email", "").strip() or None
    recovery_phone = data.get("recovery_phone", "").strip() or None
    recovery_enabled = bool(recovery_email or recovery_phone)

    if not token:
        return jsonify({"error": "Verification token required"}), 400

    if auth_type not in ("totp",):  # Only TOTP for now
        return jsonify({"error": "Unsupported auth type. Use 'totp'."}), 400

    db = get_auth_db()
    reg_repo = PendingRegistrationRepository(db)

    # Find pending registration
    reg = reg_repo.get_by_token(token)
    if reg is None:
        return jsonify({"error": "Invalid or expired verification token"}), 400

    if reg.is_expired():
        reg.consume(db)  # Clean up
        return jsonify({"error": "Verification token has expired"}), 400

    # Generate TOTP secret
    secret, base32_secret, uri = setup_totp(reg.username)

    # Create user with recovery preferences
    user = User(
        username=reg.username,
        auth_type=AuthType.TOTP,
        auth_credential=secret,
        can_download=True,  # Default: allow downloads for offline listening
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    user.save(db)

    # Generate backup codes (always, regardless of recovery settings)
    backup_repo = BackupCodeRepository(db)
    backup_codes = backup_repo.create_codes_for_user(user.id)

    # Consume (delete) the pending registration
    reg.consume(db)

    # Build response
    response_data = {
        "success": True,
        "username": user.username,
        "user_id": user.id,
        "totp_secret": base32_secret,
        "totp_uri": uri,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": "Account created. Scan the QR code or enter the secret in your authenticator app.",
    }

    # Add appropriate warning based on recovery settings
    if recovery_enabled:
        response_data["warning"] = (
            "Save your backup codes in a safe place. You can also recover your account "
            "using your registered email/phone if you lose access to your authenticator."
        )
    else:
        response_data["warning"] = (
            "IMPORTANT: Save these backup codes in a safe place! Without stored contact "
            "information, these codes are your ONLY way to recover your account if you "
            "lose your authenticator. Each code can only be used once."
        )

    if include_qr:
        import base64
        qr_png = generate_qr_code(secret, user.username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode('ascii')

    return jsonify(response_data)


# =============================================================================
# Recovery Endpoints
# =============================================================================

@auth_bp.route("/recover/backup-code", methods=["POST"])
def recover_with_backup_code():
    """
    Recover account access using a backup code.

    This endpoint allows users who have lost their authenticator to regain
    access using one of their single-use backup codes. Upon successful
    verification, the user receives a new TOTP secret and new backup codes.

    Request body:
        {
            "username": "string",
            "backup_code": "XXXX-XXXX-XXXX-XXXX"
        }

    Returns:
        200: {
            "success": true,
            "username": "...",
            "totp_secret": "...",      // New base32 secret
            "totp_uri": "...",         // New provisioning URI
            "backup_codes": [...],     // New set of 8 backup codes
            "remaining_old_codes": N,  // How many old codes remain
            "warning": "..."
        }
        400: {"error": "..."} - Missing fields
        401: {"error": "..."} - Invalid code
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    backup_code = data.get("backup_code", "").strip()

    if not username or not backup_code:
        return jsonify({"error": "Username and backup_code are required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)
    backup_repo = BackupCodeRepository(db)

    # Find user (don't reveal if user exists)
    user = user_repo.get_by_username(username)
    if user is None:
        return jsonify({"error": "Invalid username or backup code"}), 401

    # Verify and consume backup code
    if not backup_repo.verify_and_consume(user.id, backup_code):
        return jsonify({"error": "Invalid username or backup code"}), 401

    # Check remaining codes before we replace them
    remaining = backup_repo.get_remaining_count(user.id)

    # Generate new TOTP secret
    secret, base32_secret, uri = setup_totp(user.username)

    # Update user's auth credential
    user.auth_credential = secret
    user.auth_type = AuthType.TOTP
    user.save(db)

    # Generate new backup codes (replaces old unused codes)
    new_backup_codes = backup_repo.create_codes_for_user(user.id)

    # Invalidate any existing sessions (force re-login with new TOTP)
    session_repo = SessionRepository(db)
    session_repo.invalidate_user_sessions(user.id)

    return jsonify({
        "success": True,
        "username": user.username,
        "totp_secret": base32_secret,
        "totp_uri": uri,
        "backup_codes": new_backup_codes,
        "remaining_old_codes": remaining,
        "message": "Account recovered. Set up your new authenticator and save your new backup codes.",
        "warning": (
            "Your old backup codes have been invalidated. Save these new codes "
            "in a safe place - they are your only recovery option if you lose "
            "your authenticator again."
            if not user.recovery_enabled else
            "Your old backup codes have been invalidated. You can also recover "
            "using your registered email/phone if needed."
        )
    })


@auth_bp.route("/recover/remaining-codes", methods=["POST"])
@login_required
def get_remaining_backup_codes():
    """
    Get count of remaining unused backup codes for current user.

    Returns:
        200: {"remaining": N}
    """
    user = get_current_user()
    db = get_auth_db()
    backup_repo = BackupCodeRepository(db)

    return jsonify({
        "remaining": backup_repo.get_remaining_count(user.id)
    })


@auth_bp.route("/recover/regenerate-codes", methods=["POST"])
@login_required
def regenerate_backup_codes():
    """
    Generate new backup codes (invalidates old unused codes).

    Requires current authentication. Used when user wants fresh codes
    or suspects their codes have been compromised.

    Returns:
        200: {
            "success": true,
            "backup_codes": [...],
            "warning": "..."
        }
    """
    user = get_current_user()
    db = get_auth_db()
    backup_repo = BackupCodeRepository(db)

    # Generate new codes (this deletes old unused codes)
    new_codes = backup_repo.create_codes_for_user(user.id)

    return jsonify({
        "success": True,
        "backup_codes": new_codes,
        "message": "New backup codes generated. Your old codes are no longer valid.",
        "warning": (
            "Save these codes in a safe place! They are your recovery option "
            "if you lose your authenticator."
        )
    })


@auth_bp.route("/recover/update-contact", methods=["POST"])
@login_required
def update_recovery_contact():
    """
    Update recovery contact information.

    Allows authenticated users to add, update, or remove their recovery
    email/phone. Removing contact info means backup codes become the
    only recovery method.

    Request body:
        {
            "recovery_email": "email@example.com" or null,
            "recovery_phone": "+1234567890" or null
        }

    Returns:
        200: {"success": true, "recovery_enabled": bool}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    user = get_current_user()
    db = get_auth_db()

    # Update recovery fields
    if "recovery_email" in data:
        email = data["recovery_email"]
        user.recovery_email = email.strip() if email else None

    if "recovery_phone" in data:
        phone = data["recovery_phone"]
        user.recovery_phone = phone.strip() if phone else None

    # Update recovery_enabled flag
    user.recovery_enabled = bool(user.recovery_email or user.recovery_phone)
    user.save(db)

    return jsonify({
        "success": True,
        "recovery_enabled": user.recovery_enabled,
        "message": (
            "Recovery contact updated. You can now use magic link recovery."
            if user.recovery_enabled else
            "Recovery contact removed. Backup codes are now your only recovery option."
        )
    })


# =============================================================================
# Magic Link Recovery
# =============================================================================

@auth_bp.route("/magic-link", methods=["POST"])
def request_magic_link():
    """
    Request a magic link for login recovery.

    This endpoint sends a one-time login link to the user's registered
    email address (if they have one).

    Request body:
        {
            "username": "string"
        }

    Returns:
        200: {"success": true, "message": "..."}  (always returns success for privacy)
        400: {"error": "..."}  (only for invalid requests, not for user lookup)

    Note: To prevent username enumeration, this endpoint always returns success
    even if the username doesn't exist or has no recovery email. The message
    is intentionally vague.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Username is required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    # Generic message to prevent username enumeration
    generic_message = (
        "If an account exists with that username and has a registered email, "
        "a login link has been sent. Please check your email."
    )

    user = user_repo.get_by_username(username)
    if user is None:
        # User doesn't exist, but don't reveal this
        return jsonify({"success": True, "message": generic_message})

    if not user.recovery_enabled or not user.recovery_email:
        # User exists but has no recovery email
        return jsonify({"success": True, "message": generic_message})

    # Create recovery token
    recovery_repo = PendingRecoveryRepository(db)
    recovery_repo.delete_for_user(user.id)  # Remove any existing tokens

    recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=15)

    # Send email with magic link
    magic_link_url = f"/verify.html?token={raw_token}"

    # Attempt to send email
    email_sent = _send_magic_link_email(
        to_email=user.recovery_email,
        username=user.username,
        magic_link=magic_link_url,
        expires_minutes=15
    )

    if email_sent:
        return jsonify({
            "success": True,
            "message": generic_message
        })
    else:
        # Email failed, but still return success for privacy
        # Log the error internally
        current_app.logger.error(f"Failed to send magic link email to user {user.id}")
        return jsonify({
            "success": True,
            "message": generic_message
        })


@auth_bp.route("/magic-link/verify", methods=["POST"])
def verify_magic_link():
    """
    Verify a magic link token and create a session.

    Request body:
        {
            "token": "verification_token"
        }

    Returns:
        200: {"success": true, "message": "..."}
        400: {"error": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "Token is required"}), 400

    db = get_auth_db()
    recovery_repo = PendingRecoveryRepository(db)

    # Find the recovery request
    recovery = recovery_repo.get_by_token(token)
    if recovery is None:
        return jsonify({"error": "Invalid or expired token"}), 400

    if recovery.is_expired():
        return jsonify({"error": "Token has expired. Please request a new link."}), 400

    if recovery.is_used():
        return jsonify({"error": "This link has already been used"}), 400

    # Get the user
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(recovery.user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 400

    # Mark recovery as used
    recovery.mark_used(db)

    # Create a new session
    session_repo = SessionRepository(db)
    session_repo.invalidate_user_sessions(user.id)  # Single session enforcement

    user_agent = request.headers.get("User-Agent", "")
    ip_address = request.remote_addr or ""

    session, raw_token = Session.create_for_user(db, user.id, user_agent, ip_address)

    # Update last login
    user.last_login = datetime.now()
    user.save(db)

    # Set session cookie
    response = jsonify({
        "success": True,
        "message": "Login successful",
        "username": user.username,
    })

    response.set_cookie(
        _session_cookie_name,
        raw_token,
        httponly=_session_cookie_httponly,
        secure=_session_cookie_secure,
        samesite=_session_cookie_samesite,
        max_age=60 * 60 * 24 * 365,  # 1 year
    )

    return response


def _send_magic_link_email(
    to_email: str,
    username: str,
    magic_link: str,
    expires_minutes: int
) -> bool:
    """
    Send a magic link email for login recovery.

    Returns True if email was sent successfully, False otherwise.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    # Email configuration - read from environment or config
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "library@thebosco.club")
    base_url = os.environ.get("BASE_URL", "https://audiobooks.thebosco.club")

    full_link = f"{base_url}{magic_link}"

    subject = "Sign In to The Library"
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body style="font-family: Georgia, serif; background-color: #1a1a1a; color: #f5f5dc; padding: 20px;">
    <div style="max-width: 500px; margin: 0 auto; background-color: #2a2a2a; padding: 30px; border: 1px solid #8b7355;">
        <h1 style="color: #daa520; text-align: center; margin-bottom: 20px;">The Library</h1>

        <p style="color: #f5f5dc; line-height: 1.6;">
            Hello {username},
        </p>

        <p style="color: #f5f5dc; line-height: 1.6;">
            You requested a sign-in link for The Library. Click the button below to sign in:
        </p>

        <div style="text-align: center; margin: 30px 0;">
            <a href="{full_link}"
               style="background: linear-gradient(to bottom, #ffd700, #daa520, #8b7355);
                      color: #1a1a1a;
                      padding: 15px 30px;
                      text-decoration: none;
                      font-weight: bold;
                      letter-spacing: 2px;">
                SIGN IN
            </a>
        </div>

        <p style="color: #f5f5dc; line-height: 1.6; font-size: 0.9em;">
            This link will expire in {expires_minutes} minutes.
        </p>

        <p style="color: #f5f5dc; line-height: 1.6; font-size: 0.9em;">
            If you didn't request this link, you can safely ignore this email.
            Someone may have entered your username by mistake.
        </p>

        <hr style="border: none; border-top: 1px solid #8b7355; margin: 20px 0;">

        <p style="color: #888; font-size: 0.8em; text-align: center;">
            If the button doesn't work, copy and paste this link into your browser:
            <br>
            <a href="{full_link}" style="color: #daa520;">{full_link}</a>
        </p>
    </div>
</body>
</html>
"""

    text_content = f"""
Hello {username},

You requested a sign-in link for The Library.

Click here to sign in: {full_link}

This link will expire in {expires_minutes} minutes.

If you didn't request this link, you can safely ignore this email.
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_user and smtp_pass:
                server.starttls()
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True

    except Exception as e:
        current_app.logger.error(f"Failed to send magic link email: {e}")
        return False


# =============================================================================
# Notification Endpoints
# =============================================================================

@auth_bp.route("/notifications/dismiss/<int:notification_id>", methods=["POST"])
@login_required
def dismiss_notification(notification_id: int):
    """
    Dismiss a notification for the current user.

    Returns:
        200: {"success": true}
        400: {"error": "..."}
    """
    user = get_current_user()
    db = get_auth_db()
    notif_repo = NotificationRepository(db)

    if notif_repo.dismiss(notification_id, user.id):
        return jsonify({"success": True})
    return jsonify({"error": "Notification not found or already dismissed"}), 400


# =============================================================================
# Health Check
# =============================================================================

@auth_bp.route("/health", methods=["GET"])
def auth_health():
    """
    Check auth system health.

    Returns:
        200: {"status": "ok", "auth_db": true}
    """
    try:
        db = get_auth_db()
        status = db.verify()
        return jsonify({
            "status": "ok",
            "auth_db": status["can_connect"],
            "schema_version": status["schema_version"],
            "user_count": status["user_count"],
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "auth_db": False,
            "error": str(e),
        }), 500
