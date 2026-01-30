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
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
    Notification,
    NotificationType,
    NotificationRepository,
    InboxMessage,
    InboxStatus,
    InboxRepository,
    ReplyMethod,
    UserPosition,
    PositionRepository,
    hash_token,
    generate_verification_token,
    # Access Requests
    AccessRequest,
    AccessRequestStatus,
    AccessRequestRepository,
    # WebAuthn
    WebAuthnCredential,
    webauthn_registration_options,
    webauthn_verify_registration,
    webauthn_authentication_options,
    webauthn_verify_authentication,
    webauthn_get_pending_challenge,
    webauthn_clear_challenge,
)
from auth.totp import (
    setup_totp,
    verify_code as verify_totp,
    secret_to_base32,
    base32_to_secret,
    get_provisioning_uri,
    generate_qr_code,
)
from auth.backup_codes import (
    BackupCodeRepository,
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

    # Log WebAuthn configuration at startup
    rp_id, rp_name, origin = get_webauthn_config()
    print(f"WebAuthn config: rp_id={rp_id}, origin={origin}, rp_name={rp_name}")


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


# Session duration constants
SESSION_DURATION_DEFAULT = None  # Session cookie (cleared on browser close)
SESSION_DURATION_REMEMBER = 365 * 24 * 60 * 60  # 1 year in seconds (effectively permanent)


def set_session_cookie(response: Response, token: str, remember_me: bool = False) -> Response:
    """Set the session cookie on a response."""
    max_age = SESSION_DURATION_REMEMBER if remember_me else SESSION_DURATION_DEFAULT
    response.set_cookie(
        _session_cookie_name,
        token,
        httponly=_session_cookie_httponly,
        secure=_session_cookie_secure,
        samesite=_session_cookie_samesite,
        max_age=max_age,
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
            "code": "123456",  // TOTP code
            "remember_me": false  // Optional: keep session for 30 days
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
    remember_me = data.get("remember_me", False)

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

    # Set session cookie (persistent if remember_me is true)
    return set_session_cookie(response, token, remember_me=remember_me)


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
            "email": user.recovery_email,
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


@auth_bp.route("/me", methods=["PUT"])
@login_required
def update_current_user():
    """
    Update the currently authenticated user's profile.

    JSON body:
        username: New username (optional, 3-32 chars, alphanumeric + underscore)
        email: New email (optional, or null to remove)

    Returns:
        200: {"success": true, "user": {...}}
        400: {"error": "..."}
        409: {"error": "Username already taken"}
    """
    import re
    user = get_current_user()
    data = request.get_json() or {}
    db = get_auth_db()
    user_repo = UserRepository(db)

    new_username = data.get("username")
    if new_username is not None:
        # Validate username format
        if not new_username or len(new_username) < 3:
            return jsonify({"error": "Username must be at least 3 characters"}), 400
        if len(new_username) > 32:
            return jsonify({"error": "Username must be at most 32 characters"}), 400
        # Allow ASCII printable (32-126) except angle brackets (HTML) and backslash
        if not all(32 <= ord(c) <= 126 and c not in '<>\\' for c in new_username):
            return jsonify({"error": "Username contains invalid characters"}), 400
        # No leading/trailing whitespace
        if new_username != new_username.strip():
            return jsonify({"error": "Username cannot have leading or trailing spaces"}), 400

        if not user_repo.update_username(user.id, new_username):
            return jsonify({"error": "Username already taken"}), 409

    # Handle email update (can be set to null to remove)
    if "email" in data:
        new_email = data.get("email")
        if new_email is not None and new_email != "":
            # Validate email format
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, new_email):
                return jsonify({"error": "Invalid email format"}), 400
        else:
            new_email = None  # Remove email
        user_repo.update_email(user.id, new_email)

    # Fetch updated user data
    updated_user = user_repo.get_by_id(user.id)

    return jsonify({
        "success": True,
        "user": {
            "id": updated_user.id,
            "username": updated_user.username,
            "email": updated_user.recovery_email,
            "can_download": updated_user.can_download,
            "is_admin": updated_user.is_admin,
        }
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
    Submit an access request for admin approval.

    Creates a pending access request that an admin must approve.
    Returns a claim token that the user must save to retrieve credentials later.

    Request body:
        {
            "username": "string",
            "contact_email": "string" (optional)
        }

    Returns:
        200: {"success": true, "claim_token": "...", "message": "..."}
        400: {"error": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    contact_email = data.get("contact_email", "").strip() or None

    # Validate username
    if len(username) < 5:
        return jsonify({"error": "Username must be at least 5 characters"}), 400
    if len(username) > 16:
        return jsonify({"error": "Username must be at most 16 characters"}), 400
    # Allow ASCII printable (32-126) except angle brackets (HTML) and backslash
    if not all(32 <= ord(c) <= 126 and c not in '<>\\' for c in username):
        return jsonify({"error": "Username contains invalid characters"}), 400
    # No leading/trailing whitespace
    if username != username.strip():
        return jsonify({"error": "Username cannot have leading or trailing spaces"}), 400

    # Basic email validation if provided
    if contact_email:
        if "@" not in contact_email or "." not in contact_email:
            return jsonify({"error": "Invalid email address format"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)
    request_repo = AccessRequestRepository(db)

    # Check if username exists
    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 400

    # Check if there's already a pending request
    if request_repo.has_pending_request(username):
        return jsonify({"error": "Access request already pending for this username"}), 400

    # First-user-is-admin bootstrap: if no users exist, auto-approve as admin
    if user_repo.count() == 0:
        import base64

        # Create the first user as admin directly
        totp_secret, totp_base32, totp_uri = setup_totp(username)
        new_user = User(
            username=username,
            auth_type=AuthType.TOTP,
            auth_credential=totp_secret,
            can_download=True,
            is_admin=True,  # First user becomes admin
        )
        new_user.save(db)
        created_user = new_user

        # Generate backup codes
        backup_repo = BackupCodeRepository(db)
        codes = backup_repo.create_codes_for_user(created_user.id)

        # Generate QR code (convert base32 string back to bytes)
        qr_png = generate_qr_code(base32_to_secret(totp_base32), username)
        qr_base64 = base64.b64encode(qr_png).decode('ascii')

        return jsonify({
            "success": True,
            "first_user": True,
            "message": "You are the first user and have been granted admin access.",
            "totp_secret": totp_base32,
            "totp_uri": totp_uri,
            "totp_qr": qr_base64,
            "backup_codes": format_codes_for_display(codes),
        })

    # Generate claim token for credential retrieval
    raw_claim_token, _ = generate_verification_token()

    # Truncate to 16 chars for user-friendly display (XXXX-XXXX-XXXX-XXXX)
    truncated_token = raw_claim_token[:16]
    formatted_token = "-".join(truncated_token[i:i+4] for i in range(0, 16, 4))

    # Hash the truncated token (this is what user will provide when claiming)
    claim_token_hash = hash_token(truncated_token)

    # Create access request with claim token hash
    access_request = request_repo.create(username, claim_token_hash, contact_email)

    response_data = {
        "success": True,
        "message": "Access request submitted. Save your claim token - you'll need it to complete setup after approval.",
        "request_id": access_request.id,
        "claim_token": formatted_token,
        "username": username,
    }

    if contact_email:
        response_data["email_notification"] = True
        response_data["message"] += f" We'll also notify you at {contact_email} when your request is reviewed."

    return jsonify(response_data)


@auth_bp.route("/register/claim/validate", methods=["POST"])
def validate_claim_token():
    """
    Validate a claim token and return the approval status.

    This is the first step in the claim flow - validate the token
    before presenting auth method options to the user.

    Request body:
        {
            "username": "string",
            "claim_token": "XXXX-XXXX-XXXX-XXXX"
        }

    Returns:
        200: {
            "valid": true,
            "status": "approved",
            "username": "..."
        }
        400: {"valid": false, "status": "pending|denied|already_claimed", "error": "..."}
        404: {"valid": false, "error": "Invalid username or claim token"}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()

    if not username or not claim_token:
        return jsonify({"error": "Username and claim_token are required"}), 400

    # Remove dashes from token if formatted
    clean_token = claim_token.replace("-", "")

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)

    # Hash the token for lookup
    claim_token_hash = hash_token(clean_token)

    # Find the access request
    access_req = request_repo.get_pending_by_username_and_token(username, claim_token_hash)
    if not access_req:
        return jsonify({"valid": False, "error": "Invalid username or claim token"}), 404

    # Check status
    if access_req.status == AccessRequestStatus.PENDING:
        return jsonify({
            "valid": False,
            "status": "pending",
            "error": "Your request is still pending admin review"
        }), 400

    if access_req.status == AccessRequestStatus.DENIED:
        return jsonify({
            "valid": False,
            "status": "denied",
            "error": access_req.deny_reason or "Your request was denied"
        }), 400

    # Check if already claimed (user already exists)
    if access_req.credentials_claimed or user_repo.username_exists(username):
        return jsonify({
            "valid": False,
            "status": "already_claimed",
            "error": "Credentials have already been claimed. If you lost your authenticator, use the recovery page."
        }), 400

    # Token is valid and approved
    return jsonify({
        "valid": True,
        "status": "approved",
        "username": access_req.username
    })


@auth_bp.route("/register/claim", methods=["POST"])
def claim_credentials():
    """
    Claim credentials using TOTP authentication method.

    Creates the user account with TOTP as the auth method.
    For passkey/FIDO2, use the /register/claim/webauthn endpoints.

    Request body:
        {
            "username": "string",
            "claim_token": "XXXX-XXXX-XXXX-XXXX",
            "recovery_email": "optional",
            "recovery_phone": "optional"
        }

    Returns:
        200: {
            "success": true,
            "totp_secret": "...",
            "totp_uri": "...",
            "totp_qr": "...",
            "backup_codes": [...],
            "message": "..."
        }
        400: {"error": "..."} - Invalid token or already claimed
        404: {"error": "..."} - Request not found
    """
    import base64

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    recovery_email = (data.get("recovery_email") or "").strip() or None
    recovery_phone = (data.get("recovery_phone") or "").strip() or None
    recovery_enabled = bool(recovery_email or recovery_phone)

    if not username or not claim_token:
        return jsonify({"error": "Username and claim_token are required"}), 400

    # Remove dashes from token if formatted
    clean_token = claim_token.replace("-", "")

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)

    # Hash the token for lookup
    claim_token_hash = hash_token(clean_token)

    # Find the access request
    access_req = request_repo.get_pending_by_username_and_token(username, claim_token_hash)
    if not access_req:
        return jsonify({"error": "Invalid username or claim token"}), 404

    # Check status
    if access_req.status == AccessRequestStatus.PENDING:
        return jsonify({
            "error": "Your request is still pending admin review",
            "status": "pending"
        }), 400

    if access_req.status == AccessRequestStatus.DENIED:
        return jsonify({
            "error": access_req.deny_reason or "Your request was denied",
            "status": "denied"
        }), 400

    # Check if already claimed
    if access_req.credentials_claimed or user_repo.username_exists(username):
        return jsonify({
            "error": "Credentials have already been claimed. If you lost your authenticator, use the recovery page.",
            "status": "already_claimed"
        }), 400

    # Create TOTP credentials and user account
    totp_secret, totp_base32, totp_uri = setup_totp(username)

    # Create the user with TOTP
    new_user = User(
        username=username,
        auth_type=AuthType.TOTP,
        auth_credential=totp_secret,
        can_download=True,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    new_user.save(db)

    # Generate backup codes
    backup_repo = BackupCodeRepository(db)
    backup_codes = backup_repo.create_codes_for_user(new_user.id)
    backup_codes_formatted = format_codes_for_display(backup_codes)

    # Mark as claimed
    request_repo.mark_credentials_claimed(access_req.id)

    # Generate QR code
    qr_png = generate_qr_code(base32_to_secret(totp_base32), username)
    qr_base64 = base64.b64encode(qr_png).decode('ascii')

    return jsonify({
        "success": True,
        "username": username,
        "totp_secret": totp_base32,
        "totp_uri": totp_uri,
        "totp_qr": qr_base64,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": (
            "Your account is ready! Set up your authenticator app using the QR code or "
            "manual entry, then log in with your 6-digit code. Save your backup codes securely."
        ),
        "warning": (
            "IMPORTANT: Save your backup codes in a safe place! These are your ONLY way to "
            "recover your account if you lose your authenticator device."
        )
    })


@auth_bp.route("/register/claim/webauthn/begin", methods=["POST"])
def claim_webauthn_begin():
    """
    Start WebAuthn registration for claim flow.

    Request body:
        {
            "username": "string",
            "claim_token": "XXXX-XXXX-XXXX-XXXX",
            "auth_type": "passkey" | "fido2"
        }

    Returns:
        200: {
            "options": {...},
            "challenge": "..."
        }
        400: {"error": "..."}
    """
    from webauthn.helpers import bytes_to_base64url

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if not username or not claim_token:
        return jsonify({"error": "Username and claim_token are required"}), 400

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type. Use 'passkey' or 'fido2'."}), 400

    # Remove dashes from token if formatted
    clean_token = claim_token.replace("-", "")

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)

    # Hash the token for lookup
    claim_token_hash = hash_token(clean_token)

    # Find and validate the access request
    access_req = request_repo.get_pending_by_username_and_token(username, claim_token_hash)
    if not access_req:
        return jsonify({"error": "Invalid username or claim token"}), 400

    if access_req.status != AccessRequestStatus.APPROVED:
        return jsonify({"error": "Request not approved"}), 400

    if access_req.credentials_claimed or user_repo.username_exists(username):
        return jsonify({"error": "Credentials already claimed"}), 400

    # Get WebAuthn configuration
    rp_id, rp_name, _ = get_webauthn_config()

    # Determine authenticator type
    authenticator_type = "platform" if auth_type == "passkey" else "cross-platform"

    # Generate registration options
    options_json, challenge = webauthn_registration_options(
        username=username,
        rp_id=rp_id,
        rp_name=rp_name,
        authenticator_type=authenticator_type,
    )

    return jsonify({
        "options": options_json,
        "challenge": bytes_to_base64url(challenge),
    })


@auth_bp.route("/register/claim/webauthn/complete", methods=["POST"])
def claim_webauthn_complete():
    """
    Complete WebAuthn registration for claim flow.

    Request body:
        {
            "username": "string",
            "claim_token": "XXXX-XXXX-XXXX-XXXX",
            "credential": {...},
            "challenge": "...",
            "auth_type": "passkey" | "fido2",
            "recovery_email": "optional",
            "recovery_phone": "optional"
        }

    Returns:
        200: {
            "success": true,
            "username": "...",
            "backup_codes": [...]
        }
        400: {"error": "..."}
    """
    from webauthn.helpers import base64url_to_bytes
    import json

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    credential = data.get("credential")
    challenge_b64 = data.get("challenge", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()
    recovery_email = (data.get("recovery_email") or "").strip() or None
    recovery_phone = (data.get("recovery_phone") or "").strip() or None
    recovery_enabled = bool(recovery_email or recovery_phone)

    if not username or not claim_token or not credential or not challenge_b64:
        return jsonify({"error": "Username, claim_token, credential, and challenge are required"}), 400

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type"}), 400

    # Remove dashes from token if formatted
    clean_token = claim_token.replace("-", "")

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)

    # Hash the token for lookup
    claim_token_hash = hash_token(clean_token)

    # Find and validate the access request
    access_req = request_repo.get_pending_by_username_and_token(username, claim_token_hash)
    if not access_req:
        return jsonify({"error": "Invalid username or claim token"}), 400

    if access_req.status != AccessRequestStatus.APPROVED:
        return jsonify({"error": "Request not approved"}), 400

    if access_req.credentials_claimed or user_repo.username_exists(username):
        return jsonify({"error": "Credentials already claimed"}), 400

    # Get WebAuthn configuration
    rp_id, _, origin = get_webauthn_config()

    # Decode challenge
    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception:
        return jsonify({"error": "Invalid challenge format"}), 400

    # Convert credential to JSON string if it's a dict
    credential_json = json.dumps(credential) if isinstance(credential, dict) else credential

    # Verify registration
    webauthn_cred = webauthn_verify_registration(
        credential_json=credential_json,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        return jsonify({"error": "WebAuthn verification failed"}), 400

    # Create user with WebAuthn credential
    new_user = User(
        username=username,
        auth_type=AuthType.PASSKEY if auth_type == "passkey" else AuthType.FIDO2,
        auth_credential=webauthn_cred.to_json().encode("utf-8"),
        can_download=True,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    new_user.save(db)

    # Generate backup codes
    backup_repo = BackupCodeRepository(db)
    backup_codes = backup_repo.create_codes_for_user(new_user.id)

    # Mark as claimed
    request_repo.mark_credentials_claimed(access_req.id)

    # Create session so user is logged in immediately after claiming
    session, token = Session.create_for_user(
        db,
        new_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
    )
    new_user.update_last_login(db)

    # Build response
    response_data = {
        "success": True,
        "username": username,
        "user_id": new_user.id,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": f"Account created successfully with {auth_type} authentication.",
    }

    if recovery_enabled:
        response_data["warning"] = (
            "Save your backup codes in a safe place. You can also recover your account "
            "using your registered email/phone if you lose your passkey."
        )
    else:
        response_data["warning"] = (
            "IMPORTANT: Save these backup codes in a safe place! Without stored contact "
            "information, these codes are your ONLY way to recover your account if you "
            "lose your passkey. Each code can only be used once."
        )

    response = jsonify(response_data)
    return set_session_cookie(response, token)


@auth_bp.route("/register/status", methods=["POST"])
def check_request_status():
    """
    Check the status of an access request.

    Request body:
        {
            "username": "string"
        }

    Returns:
        200: {"status": "pending|approved|denied", "message": "..."}
        404: {"error": "No request found"}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Username required"}), 400

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)

    # Check if user already exists (approved)
    if user_repo.username_exists(username):
        return jsonify({
            "status": "approved",
            "message": "Your access has been approved. You can now log in.",
        })

    # Check access request
    access_request = request_repo.get_by_username(username)
    if not access_request:
        return jsonify({"error": "No access request found for this username"}), 404

    if access_request.status == AccessRequestStatus.PENDING:
        return jsonify({
            "status": "pending",
            "message": "Your request is awaiting administrator review.",
        })
    elif access_request.status == AccessRequestStatus.DENIED:
        return jsonify({
            "status": "denied",
            "message": access_request.deny_reason or "Your request was denied.",
        })
    else:
        return jsonify({
            "status": access_request.status.value,
            "message": "Unknown status.",
        })


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
    recovery_email = (data.get("recovery_email") or "").strip() or None
    recovery_phone = (data.get("recovery_phone") or "").strip() or None
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
# WebAuthn/Passkey Registration Endpoints
# =============================================================================


def get_webauthn_config() -> tuple[str, str, str]:
    """Get WebAuthn configuration, deriving from deployment config if not explicit.

    Priority:
    1. Explicit WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN (env or audiobooks.conf)
    2. Auto-derived from AUDIOBOOKS_HOSTNAME + AUDIOBOOKS_WEB_PORT + AUDIOBOOKS_HTTPS_ENABLED
    3. Fallback to localhost defaults (development)
    """
    import socket
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from config import get_config

    # Explicit overrides take priority
    rp_id = get_config("WEBAUTHN_RP_ID")
    rp_name = get_config("WEBAUTHN_RP_NAME", "The Library")
    origin = get_config("WEBAUTHN_ORIGIN")

    # Auto-derive RP ID from hostname if not set
    if not rp_id:
        hostname = get_config("AUDIOBOOKS_HOSTNAME") or socket.getfqdn()
        # Use "localhost" for loopback, local suffixes, and single-label hostnames
        # (no dots = not a real FQDN, e.g. "myserver" or "test-vm-cachyos")
        is_local = (
            hostname in ("localhost", "127.0.0.1", "::1")
            or hostname.endswith((".local", ".localdomain", ".localhost"))
            or "." not in hostname
        )
        rp_id = "localhost" if is_local else hostname

    # Auto-derive origin from proxy config if not set
    if not origin:
        https_enabled = get_config("AUDIOBOOKS_HTTPS_ENABLED", "true").lower() == "true"
        web_port = int(get_config("AUDIOBOOKS_WEB_PORT") or get_config("WEB_PORT", "8443"))
        scheme = "https" if https_enabled else "http"
        default_port = 443 if https_enabled else 80

        if rp_id == "localhost":
            origin = f"{scheme}://localhost:{web_port}"
        elif web_port == default_port:
            origin = f"{scheme}://{rp_id}"
        else:
            origin = f"{scheme}://{rp_id}:{web_port}"

    return rp_id, rp_name, origin


@auth_bp.route("/register/webauthn/begin", methods=["POST"])
def register_webauthn_begin():
    """
    Start WebAuthn registration ceremony.

    Request body:
        {
            "token": "verification_token",
            "auth_type": "passkey" | "fido2",
            "recovery_email": "optional",
            "recovery_phone": "optional"
        }

    Returns:
        200: {
            "options": {...},  // WebAuthn registration options (JSON)
            "challenge": "..."  // Base64URL challenge for completion
        }
        400: {"error": "..."}
    """
    from webauthn.helpers import bytes_to_base64url

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if not token:
        return jsonify({"error": "Verification token required"}), 400

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type. Use 'passkey' or 'fido2'."}), 400

    db = get_auth_db()
    reg_repo = PendingRegistrationRepository(db)

    # Find pending registration
    reg = reg_repo.get_by_token(token)
    if reg is None:
        return jsonify({"error": "Invalid or expired verification token"}), 400

    if reg.is_expired():
        reg.consume(db)
        return jsonify({"error": "Verification token has expired"}), 400

    # Get WebAuthn configuration
    rp_id, rp_name, _ = get_webauthn_config()

    # Determine authenticator type
    authenticator_type = "platform" if auth_type == "passkey" else "cross-platform"

    # Generate registration options
    options_json, challenge = webauthn_registration_options(
        username=reg.username,
        rp_id=rp_id,
        rp_name=rp_name,
        authenticator_type=authenticator_type,
    )

    return jsonify({
        "options": options_json,  # Already JSON string
        "challenge": bytes_to_base64url(challenge),
        "token": token,  # Return for completion step
    })


@auth_bp.route("/register/webauthn/complete", methods=["POST"])
def register_webauthn_complete():
    """
    Complete WebAuthn registration ceremony.

    Request body:
        {
            "token": "verification_token",
            "credential": {...},  // WebAuthn credential response
            "challenge": "...",   // Base64URL challenge
            "auth_type": "passkey" | "fido2",
            "recovery_email": "optional",
            "recovery_phone": "optional"
        }

    Returns:
        200: {
            "success": true,
            "username": "...",
            "backup_codes": [...],
            "recovery_enabled": bool
        }
        400: {"error": "..."}
    """
    from webauthn.helpers import base64url_to_bytes

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    credential = data.get("credential")
    challenge_b64 = data.get("challenge", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()

    # Recovery preferences
    recovery_email = (data.get("recovery_email") or "").strip() or None
    recovery_phone = (data.get("recovery_phone") or "").strip() or None
    recovery_enabled = bool(recovery_email or recovery_phone)

    if not token or not credential or not challenge_b64:
        return jsonify({"error": "Token, credential, and challenge are required"}), 400

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type"}), 400

    db = get_auth_db()
    reg_repo = PendingRegistrationRepository(db)

    # Find pending registration
    reg = reg_repo.get_by_token(token)
    if reg is None:
        return jsonify({"error": "Invalid or expired verification token"}), 400

    if reg.is_expired():
        reg.consume(db)
        return jsonify({"error": "Verification token has expired"}), 400

    # Get WebAuthn configuration
    rp_id, _, origin = get_webauthn_config()

    # Decode challenge
    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception:
        return jsonify({"error": "Invalid challenge format"}), 400

    # Convert credential to JSON string if it's a dict
    import json
    credential_json = json.dumps(credential) if isinstance(credential, dict) else credential

    # Verify registration
    webauthn_cred = webauthn_verify_registration(
        credential_json=credential_json,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        return jsonify({"error": "WebAuthn verification failed"}), 400

    # Create user with WebAuthn credential
    user = User(
        username=reg.username,
        auth_type=AuthType.PASSKEY if auth_type == "passkey" else AuthType.FIDO2,
        auth_credential=webauthn_cred.to_json().encode("utf-8"),
        can_download=True,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    user.save(db)

    # Generate backup codes
    backup_repo = BackupCodeRepository(db)
    backup_codes = backup_repo.create_codes_for_user(user.id)

    # Consume the pending registration
    reg.consume(db)

    # Build response
    response_data = {
        "success": True,
        "username": user.username,
        "user_id": user.id,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": "Account created successfully with passkey authentication.",
    }

    if recovery_enabled:
        response_data["warning"] = (
            "Save your backup codes in a safe place. You can also recover your account "
            "using your registered email/phone if you lose your passkey."
        )
    else:
        response_data["warning"] = (
            "IMPORTANT: Save these backup codes in a safe place! Without stored contact "
            "information, these codes are your ONLY way to recover your account if you "
            "lose your passkey. Each code can only be used once."
        )

    return jsonify(response_data)


# =============================================================================
# WebAuthn/Passkey Authentication Endpoints
# =============================================================================


@auth_bp.route("/login/webauthn/begin", methods=["POST"])
def login_webauthn_begin():
    """
    Start WebAuthn authentication ceremony.

    Request body:
        {
            "username": "string"
        }

    Returns:
        200: {
            "options": {...},  // WebAuthn authentication options
            "challenge": "..."  // Base64URL challenge
        }
        400: {"error": "..."} - User not found or not using WebAuthn
    """
    from webauthn.helpers import bytes_to_base64url

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Username required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    # Find user
    user = user_repo.get_by_username(username)
    if user is None:
        # Don't reveal if user exists
        return jsonify({"error": "Invalid credentials"}), 401

    # Check user uses WebAuthn
    if user.auth_type not in (AuthType.PASSKEY, AuthType.FIDO2):
        return jsonify({
            "error": "User does not use passkey authentication",
            "auth_type": user.auth_type.value
        }), 400

    # Parse stored credential
    try:
        webauthn_cred = WebAuthnCredential.from_json(user.auth_credential.decode("utf-8"))
    except Exception:
        return jsonify({"error": "Invalid stored credential"}), 500

    # Get WebAuthn configuration
    rp_id, _, _ = get_webauthn_config()

    # Generate authentication options
    options_json, challenge = webauthn_authentication_options(
        user_id=user.id,
        credential_id=webauthn_cred.credential_id,
        rp_id=rp_id,
        username=username,
    )

    return jsonify({
        "options": options_json,
        "challenge": bytes_to_base64url(challenge),
    })


@auth_bp.route("/login/webauthn/complete", methods=["POST"])
def login_webauthn_complete():
    """
    Complete WebAuthn authentication ceremony.

    Request body:
        {
            "username": "string",
            "credential": {...},  // WebAuthn assertion response
            "challenge": "..."    // Base64URL challenge
        }

    Returns:
        200: {"success": true, "user": {...}}
        401: {"error": "Invalid credentials"}
    """
    from webauthn.helpers import base64url_to_bytes

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    credential = data.get("credential")
    challenge_b64 = data.get("challenge", "").strip()

    if not username or not credential or not challenge_b64:
        return jsonify({"error": "Username, credential, and challenge are required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    # Find user
    user = user_repo.get_by_username(username)
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401

    # Check user uses WebAuthn
    if user.auth_type not in (AuthType.PASSKEY, AuthType.FIDO2):
        return jsonify({"error": "Invalid credentials"}), 401

    # Parse stored credential
    try:
        webauthn_cred = WebAuthnCredential.from_json(user.auth_credential.decode("utf-8"))
    except Exception:
        return jsonify({"error": "Invalid credentials"}), 401

    # Decode challenge
    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception:
        return jsonify({"error": "Invalid challenge format"}), 400

    # Get WebAuthn configuration
    rp_id, _, origin = get_webauthn_config()

    # Convert credential to JSON string if it's a dict
    import json
    credential_json = json.dumps(credential) if isinstance(credential, dict) else credential

    # Verify authentication
    new_sign_count = webauthn_verify_authentication(
        credential_json=credential_json,
        expected_challenge=challenge,
        credential_public_key=webauthn_cred.public_key,
        credential_current_sign_count=webauthn_cred.sign_count,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )

    if new_sign_count is None:
        return jsonify({"error": "Invalid credentials"}), 401

    # Update sign count in stored credential
    webauthn_cred.sign_count = new_sign_count
    user.auth_credential = webauthn_cred.to_json().encode("utf-8")
    user.save(db)

    # Create session
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

    return set_session_cookie(response, token)


@auth_bp.route("/login/auth-type", methods=["POST"])
def get_auth_type():
    """
    Get the authentication type for a user.

    Used by the frontend to determine which login flow to use.

    Request body:
        {"username": "string"}

    Returns:
        200: {"auth_type": "totp" | "passkey" | "fido2"}
        404: {"error": "User not found"}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    if not username:
        return jsonify({"error": "Username required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    user = user_repo.get_by_username(username)
    if user is None:
        # Don't reveal if user exists - return generic auth type
        # This prevents username enumeration
        return jsonify({"auth_type": "totp"}), 200

    return jsonify({"auth_type": user.auth_type.value})


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
        # Log error type only, not full message (may contain email address)
        current_app.logger.error(f"Failed to send magic link email: {type(e).__name__}")
        return False


def _send_approval_email(to_email: str, username: str) -> bool:
    """
    Send an email notifying the user their access request was approved.

    Includes step-by-step instructions for setting up their authenticator
    and claiming their credentials.

    Returns True if email was sent successfully, False otherwise.
    """
    # Email configuration
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "library@thebosco.club")
    base_url = os.environ.get("BASE_URL", "https://audiobooks.thebosco.club")

    claim_url = f"{base_url}/claim.html"

    subject = "Your Access to The Library Has Been Approved!"

    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body style="font-family: Georgia, serif; background-color: #1a1a1a; color: #f5f5dc; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #2a2a2a; padding: 30px; border: 1px solid #8b7355;">
        <h1 style="color: #daa520; text-align: center; margin-bottom: 20px;">📚 Welcome to The Library!</h1>

        <p style="color: #f5f5dc; line-height: 1.6;">
            Hello {username},
        </p>

        <p style="color: #f5f5dc; line-height: 1.6;">
            Great news! Your access request has been approved. You're almost ready to start
            exploring the collection.
        </p>

        <h2 style="color: #daa520; border-bottom: 1px solid #8b7355; padding-bottom: 10px;">
            Before You Log In
        </h2>

        <p style="color: #f5f5dc; line-height: 1.6;">
            The Library uses an <strong>authenticator app</strong> instead of passwords.
            This is more secure and means you never have to remember a password.
        </p>

        <p style="color: #f5f5dc; line-height: 1.6;">
            If you don't already have one installed, here are some free options:
        </p>

        <div style="background-color: #3a3a3a; padding: 15px; margin: 15px 0; border-left: 3px solid #daa520;">
            <p style="color: #f5f5dc; margin: 5px 0;">
                <strong>📱 Authy</strong> (Recommended - syncs across devices)<br>
                <a href="https://apps.apple.com/app/authy/id494168017" style="color: #daa520;">iPhone/iPad</a> |
                <a href="https://play.google.com/store/apps/details?id=com.authy.authy" style="color: #daa520;">Android</a> |
                <a href="https://authy.com/download/" style="color: #daa520;">Desktop</a>
            </p>
            <p style="color: #f5f5dc; margin: 5px 0;">
                <strong>📱 Google Authenticator</strong><br>
                <a href="https://apps.apple.com/app/google-authenticator/id388497605" style="color: #daa520;">iPhone/iPad</a> |
                <a href="https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2" style="color: #daa520;">Android</a>
            </p>
            <p style="color: #f5f5dc; margin: 5px 0;">
                <strong>📱 Microsoft Authenticator</strong><br>
                <a href="https://apps.apple.com/app/microsoft-authenticator/id983156458" style="color: #daa520;">iPhone/iPad</a> |
                <a href="https://play.google.com/store/apps/details?id=com.azure.authenticator" style="color: #daa520;">Android</a>
            </p>
        </div>

        <h2 style="color: #daa520; border-bottom: 1px solid #8b7355; padding-bottom: 10px;">
            Step-by-Step Setup
        </h2>

        <ol style="color: #f5f5dc; line-height: 1.8;">
            <li><strong>Install an authenticator app</strong> on your phone (if you don't have one)</li>
            <li><strong>Get your claim token ready</strong> - this is the code you saved when you requested access
                (it looks like: <code style="background: #3a3a3a; padding: 2px 6px;">XXXX-XXXX-XXXX-XXXX</code>)</li>
            <li><strong>Visit the claim page:</strong>
                <div style="text-align: center; margin: 15px 0;">
                    <a href="{claim_url}"
                       style="background: linear-gradient(to bottom, #ffd700, #daa520, #8b7355);
                              color: #1a1a1a;
                              padding: 12px 25px;
                              text-decoration: none;
                              font-weight: bold;
                              letter-spacing: 1px;
                              display: inline-block;">
                        CLAIM YOUR CREDENTIALS
                    </a>
                </div>
            </li>
            <li><strong>Enter your username</strong> ({username}) and claim token</li>
            <li><strong>Scan the QR code</strong> with your authenticator app
                <div style="background-color: #3a3a3a; padding: 10px; margin: 10px 0; font-size: 0.9em;">
                    <em>In your authenticator app, tap the + button, then "Scan QR code"</em>
                </div>
            </li>
            <li><strong>Save your backup codes</strong> - write them down or save them somewhere safe.
                These let you recover your account if you lose your phone.</li>
            <li><strong>Log in</strong> using the 6-digit code shown in your authenticator app</li>
        </ol>

        <div style="background-color: #4a3a2a; padding: 15px; margin: 20px 0; border: 1px solid #8b7355;">
            <p style="color: #ffcc00; margin: 0; font-weight: bold;">
                ⚠️ Important: Can't find your claim token?
            </p>
            <p style="color: #f5f5dc; margin: 10px 0 0 0; font-size: 0.9em;">
                The claim token was shown when you first requested access. If you didn't save it,
                you'll need to contact the administrator to reset your request.
            </p>
        </div>

        <hr style="border: none; border-top: 1px solid #8b7355; margin: 20px 0;">

        <p style="color: #888; font-size: 0.8em; text-align: center;">
            If the button doesn't work, copy and paste this link:<br>
            <a href="{claim_url}" style="color: #daa520;">{claim_url}</a>
        </p>
    </div>
</body>
</html>
"""

    text_content = f"""Welcome to The Library!

Hello {username},

Great news! Your access request has been approved.

BEFORE YOU LOG IN:
The Library uses an authenticator app instead of passwords. If you don't have one, install one of these:

- Authy (recommended): https://authy.com/download/
- Google Authenticator: Search your app store
- Microsoft Authenticator: Search your app store

STEP-BY-STEP SETUP:
1. Install an authenticator app on your phone (if needed)
2. Get your claim token ready (the XXXX-XXXX-XXXX-XXXX code from registration)
3. Visit: {claim_url}
4. Enter your username ({username}) and claim token
5. Scan the QR code with your authenticator app
6. Save your backup codes somewhere safe
7. Log in using the 6-digit code from your authenticator

Can't find your claim token? Contact the administrator.
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
        current_app.logger.error(f"Failed to send approval email: {type(e).__name__}")
        return False


def _send_denial_email(to_email: str, username: str, reason: Optional[str] = None) -> bool:
    """
    Send an email notifying the user their access request was denied.

    Returns True if email was sent successfully, False otherwise.
    """
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "library@thebosco.club")

    subject = "Update on Your Access Request - The Library"

    reason_text = reason if reason else "No specific reason was provided."

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
            We've reviewed your access request, and unfortunately we're unable to
            approve it at this time.
        </p>

        <div style="background-color: #3a3a3a; padding: 15px; margin: 15px 0; border-left: 3px solid #8b7355;">
            <p style="color: #f5f5dc; margin: 0;">
                <strong>Reason:</strong> {reason_text}
            </p>
        </div>

        <p style="color: #f5f5dc; line-height: 1.6;">
            If you believe this was in error, you may submit a new request.
        </p>

        <hr style="border: none; border-top: 1px solid #8b7355; margin: 20px 0;">

        <p style="color: #888; font-size: 0.8em; text-align: center;">
            This is an automated message from The Library.
        </p>
    </div>
</body>
</html>
"""

    text_content = f"""Hello {username},

We've reviewed your access request, and unfortunately we're unable to approve it at this time.

Reason: {reason_text}

If you believe this was in error, you may submit a new request.
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
        current_app.logger.error(f"Failed to send denial email: {type(e).__name__}")
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


# =============================================================================
# Contact (User to Admin messaging)
# =============================================================================

@auth_bp.route("/contact", methods=["POST"])
@login_required
def send_contact_message():
    """
    Send a message to the admin.

    Request body:
        {
            "message": str,           # Required: message content
            "reply_via": str,         # Optional: "in-app" (default) or "email"
            "reply_email": str        # Required if reply_via is "email"
        }

    Returns:
        200: {"success": true, "message_id": int}
        400: {"error": "..."}
    """
    user = get_current_user()
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400

    if len(message) > 2000:
        return jsonify({"error": "Message too long (max 2000 characters)"}), 400

    reply_via = data.get("reply_via", "in-app")
    if reply_via not in ("in-app", "email"):
        return jsonify({"error": "reply_via must be 'in-app' or 'email'"}), 400

    reply_email = None
    if reply_via == "email":
        reply_email = data.get("reply_email", "").strip()
        if not reply_email or "@" not in reply_email:
            return jsonify({"error": "Valid reply_email required for email reply"}), 400

    db = get_auth_db()

    # Create the message
    inbox_msg = InboxMessage(
        from_user_id=user.id,
        message=message,
        reply_via=ReplyMethod(reply_via),
        reply_email=reply_email
    )
    inbox_msg.save(db)

    # Send admin alert email
    _send_admin_alert(user.username, message[:100])

    return jsonify({
        "success": True,
        "message_id": inbox_msg.id,
        "info": "Your message has been sent to the admin."
    })


def _send_admin_alert(username: str, message_preview: str) -> bool:
    """Send email alert to admin about new contact message."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_from = os.environ.get("SMTP_FROM", "library@thebosco.club")
    admin_email = os.environ.get("ADMIN_EMAIL", smtp_from)

    if not smtp_user:
        # SMTP not configured, skip alert
        return False

    subject = f"New message from {username} - The Library"
    body = f"""You have a new message from {username} in The Library inbox.

Preview: {message_preview}{'...' if len(message_preview) >= 100 else ''}

View all messages:
  audiobook-inbox list
  audiobook-inbox read <id>
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = admin_email

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, admin_email, msg.as_string())

        return True
    except Exception as e:
        # Log error type only, not full message (may contain email addresses)
        current_app.logger.error(f"Failed to send admin alert: {type(e).__name__}")
        return False


# =============================================================================
# Admin Endpoints (localhost only in production)
# =============================================================================

@auth_bp.route("/admin/notifications", methods=["GET"])
@admin_required
def list_notifications():
    """
    List all notifications (admin only).

    Returns:
        200: {"notifications": [...]}
    """
    db = get_auth_db()
    notif_repo = NotificationRepository(db)
    notifications = notif_repo.list_all()

    return jsonify({
        "notifications": [
            {
                "id": n.id,
                "message": n.message,
                "type": n.type.value,
                "target_user_id": n.target_user_id,
                "starts_at": n.starts_at.isoformat() if n.starts_at else None,
                "expires_at": n.expires_at.isoformat() if n.expires_at else None,
                "dismissable": n.dismissable,
                "priority": n.priority,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "created_by": n.created_by,
            }
            for n in notifications
        ]
    })


@auth_bp.route("/admin/notifications", methods=["POST"])
@admin_required
def create_notification():
    """
    Create a new notification (admin only).

    Request body:
        {
            "message": str,           # Required
            "type": str,              # Optional: "info", "maintenance", "outage", "personal"
            "target_user_id": int,    # Optional: null for global
            "starts_at": str,         # Optional: ISO datetime
            "expires_at": str,        # Optional: ISO datetime
            "dismissable": bool,      # Optional: default true
            "priority": int           # Optional: default 0
        }

    Returns:
        200: {"success": true, "notification_id": int}
        400: {"error": "..."}
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Message is required"}), 400

    notif_type = data.get("type", "info")
    if notif_type not in ("info", "maintenance", "outage", "personal"):
        return jsonify({"error": "Invalid notification type"}), 400

    # Personal notifications require a target user
    if notif_type == "personal" and not data.get("target_user_id"):
        return jsonify({"error": "Personal notifications require target_user_id"}), 400

    db = get_auth_db()
    user = get_current_user()

    # Parse optional datetime fields
    starts_at = None
    expires_at = None
    if data.get("starts_at"):
        try:
            starts_at = datetime.fromisoformat(data["starts_at"])
        except ValueError:
            return jsonify({"error": "Invalid starts_at format"}), 400

    if data.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
        except ValueError:
            return jsonify({"error": "Invalid expires_at format"}), 400

    notification = Notification(
        message=message,
        type=NotificationType(notif_type),
        target_user_id=data.get("target_user_id"),
        starts_at=starts_at,
        expires_at=expires_at,
        dismissable=data.get("dismissable", True),
        priority=data.get("priority", 0),
        created_by=user.username,
    )
    notification.save(db)

    return jsonify({
        "success": True,
        "notification_id": notification.id
    })


@auth_bp.route("/admin/notifications/<int:notification_id>", methods=["DELETE"])
@admin_required
def delete_notification(notification_id: int):
    """
    Delete a notification (admin only).

    Returns:
        200: {"success": true}
        404: {"error": "Notification not found"}
    """
    db = get_auth_db()
    notif_repo = NotificationRepository(db)

    # Check if notification exists
    notifications = notif_repo.list_all()
    notif = next((n for n in notifications if n.id == notification_id), None)

    if not notif:
        return jsonify({"error": "Notification not found"}), 404

    notif.delete(db)
    return jsonify({"success": True})


@auth_bp.route("/admin/inbox", methods=["GET"])
@admin_required
def list_inbox():
    """
    List inbox messages (admin only).

    Query params:
        include_archived: bool (default false)

    Returns:
        200: {"messages": [...], "unread_count": int}
    """
    include_archived = request.args.get("include_archived", "false").lower() == "true"

    db = get_auth_db()
    inbox_repo = InboxRepository(db)
    user_repo = UserRepository(db)

    messages = inbox_repo.list_all(include_archived=include_archived)
    unread_count = inbox_repo.count_unread()

    # Get usernames for messages
    result = []
    for m in messages:
        user = user_repo.get_by_id(m.from_user_id)
        result.append({
            "id": m.id,
            "from_user_id": m.from_user_id,
            "from_username": user.username if user else "[deleted]",
            "message": m.message,
            "reply_via": m.reply_via.value,
            "has_reply_email": bool(m.reply_email),
            "status": m.status.value,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "read_at": m.read_at.isoformat() if m.read_at else None,
            "replied_at": m.replied_at.isoformat() if m.replied_at else None,
        })

    return jsonify({
        "messages": result,
        "unread_count": unread_count
    })


@auth_bp.route("/admin/inbox/<int:message_id>", methods=["GET"])
@admin_required
def get_inbox_message(message_id: int):
    """
    Get a single inbox message and mark it as read (admin only).

    Returns:
        200: {"message": {...}}
        404: {"error": "Message not found"}
    """
    db = get_auth_db()
    inbox_repo = InboxRepository(db)
    user_repo = UserRepository(db)

    message = inbox_repo.get_by_id(message_id)
    if not message:
        return jsonify({"error": "Message not found"}), 404

    # Mark as read
    if message.status == InboxStatus.UNREAD:
        message.mark_read(db)

    user = user_repo.get_by_id(message.from_user_id)

    return jsonify({
        "message": {
            "id": message.id,
            "from_user_id": message.from_user_id,
            "from_username": user.username if user else "[deleted]",
            "message": message.message,
            "reply_via": message.reply_via.value,
            "reply_email": message.reply_email,
            "status": message.status.value,
            "created_at": message.created_at.isoformat() if message.created_at else None,
            "read_at": message.read_at.isoformat() if message.read_at else None,
            "replied_at": message.replied_at.isoformat() if message.replied_at else None,
        }
    })


@auth_bp.route("/admin/inbox/<int:message_id>/reply", methods=["POST"])
@admin_required
def reply_to_message(message_id: int):
    """
    Reply to an inbox message (admin only).

    Request body:
        {
            "reply": str  # Required: reply message
        }

    Returns:
        200: {"success": true, "reply_method": "in-app"|"email"}
        400: {"error": "..."}
        404: {"error": "Message not found"}
    """
    db = get_auth_db()
    inbox_repo = InboxRepository(db)
    user_repo = UserRepository(db)

    message = inbox_repo.get_by_id(message_id)
    if not message:
        return jsonify({"error": "Message not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    reply_text = data.get("reply", "").strip()
    if not reply_text:
        return jsonify({"error": "Reply is required"}), 400

    user = user_repo.get_by_id(message.from_user_id)
    username = user.username if user else "User"

    reply_method = message.reply_via.value

    if message.reply_via == ReplyMethod.EMAIL and message.reply_email:
        # Send email reply
        success = _send_reply_email(message.reply_email, username, reply_text)
        if not success:
            return jsonify({"error": "Failed to send email reply"}), 500
    else:
        # Create in-app notification
        admin_user = get_current_user()
        notification = Notification(
            message=f"Reply from {admin_user.username}: {reply_text}",
            type=NotificationType.PERSONAL,
            target_user_id=message.from_user_id,
            dismissable=True,
            created_by=admin_user.username,
        )
        notification.save(db)
        reply_method = "in-app"

    # Mark message as replied (clears reply_email for privacy)
    message.mark_replied(db)

    return jsonify({
        "success": True,
        "reply_method": reply_method
    })


def _send_reply_email(to_email: str, username: str, reply_text: str) -> bool:
    """Send email reply to user."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_from = os.environ.get("SMTP_FROM", "library@thebosco.club")

    subject = "Reply from The Library"
    body = f"""Hi {username},

{reply_text}

---
This is a reply to your message to The Library.
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to_email

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, to_email, msg.as_string())

        return True
    except Exception as e:
        # Log error type only, not full message (may contain email addresses)
        current_app.logger.error(f"Failed to send reply email: {type(e).__name__}")
        return False


@auth_bp.route("/admin/inbox/<int:message_id>/archive", methods=["POST"])
@admin_required
def archive_message(message_id: int):
    """
    Archive an inbox message (admin only).

    Returns:
        200: {"success": true}
        404: {"error": "Message not found"}
    """
    db = get_auth_db()
    inbox_repo = InboxRepository(db)

    message = inbox_repo.get_by_id(message_id)
    if not message:
        return jsonify({"error": "Message not found"}), 404

    message.status = InboxStatus.ARCHIVED
    message.reply_email = None  # Clear PII
    message.save(db)

    return jsonify({"success": True})


# =============================================================================
# Admin User Management Endpoints
# =============================================================================

@auth_bp.route("/admin/access-requests", methods=["GET"])
@admin_required
def list_access_requests():
    """
    List access requests (admin only).

    Query params:
        status: Filter by status ('pending', 'approved', 'denied', or 'all')
        limit: Maximum number to return (default 50)

    Returns:
        200: {"requests": [...], "pending_count": int}
    """
    status_filter = request.args.get("status", "pending")
    limit = min(int(request.args.get("limit", 50)), 100)

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)

    if status_filter == "all":
        requests = request_repo.list_all(limit=limit)
    elif status_filter == "pending":
        requests = request_repo.list_pending(limit=limit)
    else:
        # Filter by specific status
        all_requests = request_repo.list_all(limit=limit)
        requests = [r for r in all_requests if r.status.value == status_filter]

    return jsonify({
        "requests": [r.to_dict() for r in requests],
        "pending_count": request_repo.count_pending(),
    })


@auth_bp.route("/admin/access-requests/<int:request_id>/approve", methods=["POST"])
@admin_required
def approve_access_request(request_id: int):
    """
    Approve an access request. User creation is deferred to claim time.

    This allows users to choose their preferred authentication method
    (TOTP, Passkey, or FIDO2 security key) when they claim their credentials.

    The user can retrieve their credentials using their claim token.
    If they provided an email, a notification email is sent.

    Returns:
        200: {"success": true, "username": "...", "email_sent": bool}
        400: {"error": "..."}
        404: {"error": "Request not found"}
    """
    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)

    # Get the access request
    access_req = request_repo.get_by_id(request_id)
    if not access_req:
        return jsonify({"error": "Request not found"}), 404

    if access_req.status != AccessRequestStatus.PENDING:
        return jsonify({"error": f"Request already {access_req.status.value}"}), 400

    # Check if username is still available
    if user_repo.username_exists(access_req.username):
        return jsonify({"error": "Username already taken"}), 400

    # Get admin username for audit
    admin_user = get_current_user()
    admin_username = admin_user.username if admin_user else "system"

    # Mark request as approved (user creation deferred to claim time)
    # This allows the user to choose their auth method when claiming
    request_repo.approve(request_id, admin_username)

    # Send email notification if user provided email
    email_sent = False
    if access_req.contact_email:
        email_sent = _send_approval_email(
            to_email=access_req.contact_email,
            username=access_req.username
        )

    return jsonify({
        "success": True,
        "username": access_req.username,
        "email_sent": email_sent,
        "message": (
            f"Access request for '{access_req.username}' approved. "
            + ("Email notification sent." if email_sent else "User can claim credentials with their token.")
        ),
    })


@auth_bp.route("/admin/access-requests/<int:request_id>/deny", methods=["POST"])
@admin_required
def deny_access_request(request_id: int):
    """
    Deny an access request (admin only).

    If the user provided an email, a notification is sent.

    Request body (optional):
        {"reason": "Optional denial reason"}

    Returns:
        200: {"success": true, "email_sent": bool}
        404: {"error": "Request not found"}
    """
    data = request.get_json() or {}
    reason = data.get("reason")

    db = get_auth_db()
    request_repo = AccessRequestRepository(db)

    # Get the access request
    access_req = request_repo.get_by_id(request_id)
    if not access_req:
        return jsonify({"error": "Request not found"}), 404

    if access_req.status != AccessRequestStatus.PENDING:
        return jsonify({"error": f"Request already {access_req.status.value}"}), 400

    # Get admin username for audit
    admin_user = get_current_user()
    admin_username = admin_user.username if admin_user else "system"

    # Mark request as denied
    request_repo.deny(request_id, admin_username, reason)

    # Send email notification if user provided email
    email_sent = False
    if access_req.contact_email:
        email_sent = _send_denial_email(
            to_email=access_req.contact_email,
            username=access_req.username,
            reason=reason
        )

    return jsonify({
        "success": True,
        "email_sent": email_sent,
        "message": f"Access request for '{access_req.username}' denied.",
    })


@auth_bp.route("/admin/users", methods=["GET"])
@admin_required
def list_users():
    """
    List all users (admin only).

    Query params:
        limit: Maximum number to return (default 100)

    Returns:
        200: {"users": [...], "total": int}
    """
    limit = min(int(request.args.get("limit", 100)), 500)

    db = get_auth_db()
    user_repo = UserRepository(db)

    all_users = user_repo.list_all()
    total = len(all_users)
    users = all_users[:limit]  # Apply limit in Python since list_all() doesn't support it

    return jsonify({
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.recovery_email,
                "auth_type": u.auth_type.value,
                "can_download": u.can_download,
                "is_admin": u.is_admin,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login": u.last_login.isoformat() if u.last_login else None,
            }
            for u in users
        ],
        "total": total,
    })


@auth_bp.route("/admin/users/invite", methods=["POST"])
@admin_required
def invite_user():
    """
    Invite a new user with pre-approval (admin only).

    Creates a pre-approved access request and immediately sends a claim email
    to the provided email address. The user can then claim their credentials
    and set up their authenticator.

    JSON body:
        username: Username (5-16 chars, alphanumeric)
        email: Email address to send invitation to (required)
        can_download: Optional download permission (default: true)

    Returns:
        200: {"success": true, "user": {...}, "email_sent": bool, "claim_token": "..."}
        400: {"error": "..."}
        409: {"error": "Username already taken"}
    """
    import re
    import json as json_module

    data = request.get_json() or {}

    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    can_download = data.get("can_download", True)

    # Validate username
    if not username:
        return jsonify({"error": "Username is required"}), 400
    if len(username) < 5:
        return jsonify({"error": "Username must be at least 5 characters"}), 400
    if len(username) > 16:
        return jsonify({"error": "Username must be at most 16 characters"}), 400
    # Allow ASCII printable (32-126) except angle brackets (HTML) and backslash
    if not all(32 <= ord(c) <= 126 and c not in '<>\\' for c in username):
        return jsonify({"error": "Username contains invalid characters"}), 400
    # No leading/trailing whitespace
    if username != username.strip():
        return jsonify({"error": "Username cannot have leading or trailing spaces"}), 400

    # Validate email (required for invitations)
    if not email:
        return jsonify({"error": "Email is required for invitations"}), 400
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return jsonify({"error": "Invalid email format"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)
    request_repo = AccessRequestRepository(db)

    # Check if username exists
    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 409

    # Check for existing pending request
    existing = request_repo.get_by_username(username)
    if existing:
        return jsonify({"error": "Access request already exists for this username"}), 409

    # Get admin username for audit
    admin_user = get_current_user()
    admin_username = admin_user.username if admin_user else "system"

    # Generate claim token
    raw_claim_token, _ = generate_verification_token()
    truncated_token = raw_claim_token[:16]
    formatted_token = "-".join(truncated_token[i:i+4] for i in range(0, 16, 4))
    claim_token_hash = hash_token(truncated_token)

    # Create access request with claim token hash
    access_request = request_repo.create(username, claim_token_hash, email)

    # Create TOTP secret
    totp_secret, totp_base32, totp_uri = setup_totp(username)

    # Create the user
    new_user = User(
        username=username,
        auth_type=AuthType.TOTP,
        auth_credential=totp_secret,
        can_download=can_download,
        is_admin=False,
        recovery_email=email,
    )
    new_user.save(db)
    created_user = user_repo.get_by_username(username)

    # Generate backup codes
    backup_repo = BackupCodeRepository(db)
    codes = backup_repo.create_codes_for_user(created_user.id)

    # Update access request with credentials for claim
    request_repo.store_credentials(
        access_request.id,
        totp_base32,
        totp_uri,
        json_module.dumps(format_codes_for_display(codes))
    )

    # Mark as approved
    request_repo.approve(access_request.id, admin_username)

    # Send invitation email with claim token
    email_sent = _send_invitation_email(
        to_email=email,
        username=username,
        claim_token=formatted_token
    )

    return jsonify({
        "success": True,
        "user": {
            "id": created_user.id,
            "username": created_user.username,
            "email": created_user.recovery_email,
            "can_download": created_user.can_download,
            "is_admin": created_user.is_admin,
        },
        "email_sent": email_sent,
        "claim_token": formatted_token,  # Also return token so admin can share manually if email fails
        "message": (
            f"User '{username}' created. "
            + ("Invitation email sent." if email_sent else "Email failed - share claim token manually.")
        ),
    })


def _send_invitation_email(to_email: str, username: str, claim_token: str) -> bool:
    """
    Send an invitation email to a pre-approved user with their claim token.

    Returns True if email was sent successfully, False otherwise.
    """
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("SMTP_FROM", "library@thebosco.club")
    base_url = os.environ.get("BASE_URL", "https://audiobooks.thebosco.club")

    claim_url = f"{base_url}/claim.html"

    subject = "You've Been Invited to The Library!"

    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body style="font-family: Georgia, serif; background-color: #1a1a1a; color: #f5f5dc; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #2a2a2a; padding: 30px; border: 1px solid #8b7355;">
        <h1 style="color: #daa520; text-align: center; margin-bottom: 20px;">📚 Welcome to The Library!</h1>

        <p style="color: #f5f5dc; line-height: 1.6;">
            Hello {username},
        </p>

        <p style="color: #f5f5dc; line-height: 1.6;">
            You've been invited to join The Library - a private audiobook collection!
        </p>

        <div style="background-color: #3a3a3a; padding: 20px; margin: 20px 0; border: 2px solid #daa520; text-align: center;">
            <p style="color: #f5f5dc; margin: 0 0 10px 0; font-size: 0.9em;">Your Claim Token:</p>
            <p style="color: #daa520; font-family: 'Courier New', monospace; font-size: 1.5em; letter-spacing: 0.1em; margin: 0;">
                {claim_token}
            </p>
        </div>

        <p style="color: #ff9999; font-weight: bold;">
            ⚠️ Save this token! You'll need it to complete your account setup.
        </p>

        <h2 style="color: #daa520; border-bottom: 1px solid #8b7355; padding-bottom: 10px;">
            Before You Begin
        </h2>

        <p style="color: #f5f5dc; line-height: 1.6;">
            The Library uses an <strong>authenticator app</strong> instead of passwords.
            If you don't have one installed, here are some free options:
        </p>

        <div style="background-color: #3a3a3a; padding: 15px; margin: 15px 0; border-left: 3px solid #daa520;">
            <p style="color: #f5f5dc; margin: 5px 0;">
                <strong>📱 Authy</strong> (Recommended)<br>
                <a href="https://authy.com/download/" style="color: #daa520;">authy.com/download</a>
            </p>
            <p style="color: #f5f5dc; margin: 5px 0;">
                <strong>📱 Google Authenticator</strong> or <strong>Microsoft Authenticator</strong><br>
                Available in your app store
            </p>
        </div>

        <h2 style="color: #daa520; border-bottom: 1px solid #8b7355; padding-bottom: 10px;">
            How to Set Up Your Account
        </h2>

        <ol style="color: #f5f5dc; line-height: 1.8;">
            <li>Install an authenticator app on your phone</li>
            <li>Visit: <a href="{claim_url}" style="color: #daa520;">{claim_url}</a></li>
            <li>Enter your username: <strong>{username}</strong></li>
            <li>Enter your claim token (shown above)</li>
            <li>Scan the QR code with your authenticator app</li>
            <li>Save your backup codes somewhere safe</li>
            <li>Log in using the 6-digit code from your authenticator!</li>
        </ol>

        <hr style="border: none; border-top: 1px solid #8b7355; margin: 20px 0;">

        <p style="color: #888; font-size: 0.8em; text-align: center;">
            This is an automated invitation from The Library.
        </p>
    </div>
</body>
</html>
"""

    text_content = f"""Welcome to The Library!

Hello {username},

You've been invited to join The Library - a private audiobook collection!

YOUR CLAIM TOKEN:
{claim_token}

⚠️ Save this token! You'll need it to complete your account setup.

BEFORE YOU BEGIN:
The Library uses an authenticator app instead of passwords. Install one if needed:
- Authy (recommended): https://authy.com/download/
- Google Authenticator or Microsoft Authenticator from your app store

HOW TO SET UP YOUR ACCOUNT:
1. Install an authenticator app on your phone
2. Visit: {claim_url}
3. Enter your username: {username}
4. Enter your claim token (shown above)
5. Scan the QR code with your authenticator app
6. Save your backup codes somewhere safe
7. Log in using the 6-digit code from your authenticator!
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
        current_app.logger.error(f"Failed to send invitation email: {type(e).__name__}")
        return False


@auth_bp.route("/admin/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def toggle_user_admin(user_id: int):
    """
    Toggle admin status for a user (admin only).

    Cannot demote yourself to prevent lockout.

    Returns:
        200: {"success": true, "is_admin": bool}
        400: {"error": "..."}
        404: {"error": "User not found"}
    """
    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    # Prevent self-demotion
    current_user = get_current_user()
    if current_user and current_user.id == user_id and target_user.is_admin:
        return jsonify({"error": "Cannot demote yourself"}), 400

    # Toggle admin status
    new_admin_status = not target_user.is_admin
    user_repo.set_admin(user_id, new_admin_status)

    return jsonify({
        "success": True,
        "username": target_user.username,
        "is_admin": new_admin_status,
    })


@auth_bp.route("/admin/users/<int:user_id>/toggle-download", methods=["POST"])
@admin_required
def toggle_user_download(user_id: int):
    """
    Toggle download permission for a user (admin only).

    Returns:
        200: {"success": true, "can_download": bool}
        404: {"error": "User not found"}
    """
    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    # Toggle download permission
    new_download_status = not target_user.can_download
    user_repo.set_download_permission(user_id, new_download_status)

    return jsonify({
        "success": True,
        "username": target_user.username,
        "can_download": new_download_status,
    })


@auth_bp.route("/admin/users/<int:user_id>", methods=["PUT"])
@admin_required
def update_user(user_id: int):
    """
    Update a user's profile (admin only).

    JSON body:
        username: New username (optional, 3-32 chars, alphanumeric + underscore)
        email: New email (optional, or null to remove)

    Returns:
        200: {"success": true, "user": {...}}
        400: {"error": "..."}
        404: {"error": "User not found"}
        409: {"error": "Username already taken"}
    """
    import re
    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json() or {}

    new_username = data.get("username")
    if new_username is not None:
        # Validate username format
        if not new_username or len(new_username) < 3:
            return jsonify({"error": "Username must be at least 3 characters"}), 400
        if len(new_username) > 32:
            return jsonify({"error": "Username must be at most 32 characters"}), 400
        # Allow ASCII printable (32-126) except angle brackets (HTML) and backslash
        if not all(32 <= ord(c) <= 126 and c not in '<>\\' for c in new_username):
            return jsonify({"error": "Username contains invalid characters"}), 400
        # No leading/trailing whitespace
        if new_username != new_username.strip():
            return jsonify({"error": "Username cannot have leading or trailing spaces"}), 400

        # Update username
        if not user_repo.update_username(user_id, new_username):
            return jsonify({"error": "Username already taken"}), 409

    # Handle email update (can be set to null to remove)
    if "email" in data:
        new_email = data.get("email")
        if new_email is not None and new_email != "":
            # Validate email format
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, new_email):
                return jsonify({"error": "Invalid email format"}), 400
        else:
            new_email = None  # Remove email
        user_repo.update_email(user_id, new_email)

    # Fetch updated user data
    updated_user = user_repo.get_by_id(user_id)

    return jsonify({
        "success": True,
        "user": {
            "id": updated_user.id,
            "username": updated_user.username,
            "email": updated_user.recovery_email,
            "can_download": updated_user.can_download,
            "is_admin": updated_user.is_admin,
        }
    })


@auth_bp.route("/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id: int):
    """
    Delete a user (admin only).

    Cannot delete yourself.

    Returns:
        200: {"success": true}
        400: {"error": "..."}
        404: {"error": "User not found"}
    """
    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    # Prevent self-deletion
    current_user = get_current_user()
    if current_user and current_user.id == user_id:
        return jsonify({"error": "Cannot delete yourself"}), 400

    # Delete user (cascades to sessions, positions, etc.)
    user_repo.delete(user_id)

    return jsonify({
        "success": True,
        "message": f"User '{target_user.username}' deleted.",
    })
