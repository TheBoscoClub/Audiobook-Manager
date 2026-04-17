"""
Authentication API Blueprint

Provides endpoints for:
- User login (TOTP verification)
- User registration (with email/SMS verification)
- Session management (logout, session info)
- Password-less authentication flow

All authentication data is stored in the encrypted auth.db (SQLCipher).
"""

import base64
import json
import logging
import os
import smtplib
import sys
import urllib.parse
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from pathlib import Path
from typing import Optional, Callable, Any

from flask import (
    Blueprint,
    Response,
    jsonify,
    make_response,
    redirect,
    request,
    g,
    current_app,
)

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from auth import (
    AuthDatabase,
    AuthType,
    User,
    UserRepository,
    Session,
    SessionRepository,
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
    hash_token,
    generate_verification_token,
    # Access Requests
    AccessRequestStatus,
    AccessRequestRepository,
    # WebAuthn
    WebAuthnCredential,
    webauthn_registration_options,
    webauthn_verify_registration,
    webauthn_authentication_options,
    webauthn_verify_authentication,
)
from auth.models import SystemSettingsRepository
from auth.totp import (
    setup_totp,
    verify_code as verify_totp,
    base32_to_secret,
    secret_to_base32,
    generate_qr_code,
    get_provisioning_uri,
)
from auth.backup_codes import BackupCodeRepository

# Blueprint
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
logger = logging.getLogger(__name__)

# Module-level state (initialized by init_auth_routes)
_auth_db: Optional[AuthDatabase] = None
_session_cookie_name = "audiobooks_session"
_session_cookie_secure = True  # Always use secure cookies
_session_cookie_httponly = True
_session_cookie_samesite = "Lax"

# Invitation claim tokens expire after this many hours
INVITATION_EXPIRY_HOURS = 48

# In-memory storage for pending TOTP setup secrets during auth method switch
_pending_totp_secrets: dict[int, str] = {}

# In-memory storage for pending WebAuthn challenges during auth method switch
_pending_webauthn_challenges: dict[int, str] = {}


# =============================================================================
# Extracted Validation & Formatting Helpers
# =============================================================================


def _validate_username(username: str) -> tuple[dict, int] | None:
    """Validate username format. Returns (error_dict, status) or None if valid."""
    if not username or len(username) < 3:
        return {"error": "Username must be at least 3 characters"}, 400
    if len(username) > 24:
        return {"error": "Username must be at most 24 characters"}, 400
    if not all(32 <= ord(c) <= 126 and c not in "<>\\" for c in username):
        return {"error": "Username contains invalid characters"}, 400
    if username != username.strip():
        return {"error": "Username cannot have leading or trailing spaces"}, 400
    return None


def _validate_username_strict(username: str) -> tuple[dict, int] | None:
    """Validate username with strict alphanumeric+hyphens rule (admin create)."""
    import re as _re

    if not username:
        return {"error": "Username is required"}, 400
    if len(username) < 3:
        return {"error": "Username must be at least 3 characters"}, 400
    if len(username) > 24:
        return {"error": "Username must be at most 24 characters"}, 400
    if not _re.match(r"^[a-zA-Z0-9-]+$", username):
        return {
            "error": "Username must contain only letters, numbers, and hyphens"
        }, 400
    return None


def _validate_email_format(email: str) -> tuple[dict, int] | None:
    """Validate email format. Returns (error_dict, status) or None if valid."""
    import re as _re

    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not _re.match(email_pattern, email):
        return {"error": "Invalid email format"}, 400
    return None


def _validate_webauthn_reg_input(
    token: str,
    data: dict,
    auth_type: str,
) -> tuple[dict, int] | None:
    """Validate inputs for WebAuthn registration completion."""
    if not token:
        return {"error": "Token, credential, and challenge are required"}, 400
    if not data.get("credential"):
        return {"error": "Token, credential, and challenge are required"}, 400
    if not data.get("challenge", "").strip():
        return {"error": "Token, credential, and challenge are required"}, 400
    if auth_type not in ("passkey", "fido2"):
        return {"error": "Invalid auth type"}, 400
    return None


def _recovery_warning(recovery_enabled: bool, auth_label: str = "authenticator") -> str:
    """Return the appropriate backup codes warning message."""
    if recovery_enabled:
        return (
            "Save your backup codes in a safe place. You can also recover"
            f" your account using your registered email/phone if you lose"
            f" your {auth_label}."
        )
    return (
        "IMPORTANT: Save these backup codes in a safe place! Without"
        " stored contact information, these codes are your ONLY way to"
        f" recover your account if you lose your {auth_label}."
        " Each code can only be used once."
    )


def _resolve_claim_error(
    status: str, message: str, code: int = 400
) -> tuple[None, None, None, tuple]:
    """Build a standard claim-token error return tuple."""
    return (
        None,
        None,
        None,
        (jsonify({"valid": False, "status": status, "error": message}), code),
    )


def _extract_recovery_fields(data: dict) -> tuple[str | None, str | None, bool]:
    """Extract recovery_email, recovery_phone, recovery_enabled from request data."""
    recovery_email = (data.get("recovery_email") or "").strip() or None
    recovery_phone = (data.get("recovery_phone") or "").strip() or None
    recovery_enabled = bool(recovery_email or recovery_phone)
    return recovery_email, recovery_phone, recovery_enabled


def _parse_invite_meta(backup_codes_json: str | None) -> bool:
    """Parse invite metadata from access request to extract can_download flag."""
    import json as _json

    if not backup_codes_json:
        return True
    try:
        meta = _json.loads(backup_codes_json)
        if isinstance(meta, dict) and meta.get("invited"):
            return meta.get("can_download", True)
    except (_json.JSONDecodeError, TypeError):
        pass
    return True


def _format_claim_token(raw_token: str) -> tuple[str, str]:
    """Truncate and format a claim token. Returns (truncated, formatted)."""
    truncated = raw_token[:16]
    formatted = "-".join(truncated[i : i + 4] for i in range(0, 16, 4))
    return truncated, formatted


def _user_dict(user, include_auth_type: bool = False) -> dict:
    """Build a standard user dict for API responses."""
    d = {
        "id": user.id,
        "username": user.username,
        "email": user.recovery_email,
        "is_admin": user.is_admin,
        "can_download": user.can_download,
        "multi_session": user.multi_session,
    }
    if include_auth_type:
        d["auth_type"] = user.auth_type.value
    return d


def _user_allows_multi_session(user, db=None) -> bool:
    """Check if a user is allowed multiple concurrent sessions.

    Resolution order: per-user override > global system setting.
    """
    if user.multi_session == "yes":
        return True
    if user.multi_session == "no":
        return False
    # 'default' — check global system setting
    if db is None:
        db = get_auth_db()
    repo = SystemSettingsRepository(db)
    return repo.get("multi_session_default") == "true"


def _setup_totp_data(username: str) -> tuple[bytes, str, str, dict]:
    """Generate TOTP credentials and setup data dict.

    Returns (secret_bytes, base32_secret, provisioning_uri, setup_data).
    """
    secret_bytes, base32_secret, provisioning_uri = setup_totp(username)
    qr_png = generate_qr_code(secret_bytes, username)
    qr_b64 = base64.b64encode(qr_png).decode("ascii")
    setup_data = {
        "secret": base32_secret,
        "qr_uri": provisioning_uri,
        "manual_key": base32_secret,
        "qr_base64": qr_b64,
    }
    return secret_bytes, base32_secret, provisioning_uri, setup_data


def _setup_passkey_data(db, username: str) -> dict:
    """Create pending registration for passkey and return setup_data dict."""
    from auth.models import PendingRegistration

    pending_reg, raw_token = PendingRegistration.create(
        db, username, expiry_minutes=60 * INVITATION_EXPIRY_HOURS
    )
    _, formatted_token = _format_claim_token(raw_token)
    encoded_name = urllib.parse.quote(username)
    claim_url = f"/claim.html?username={encoded_name}&token={formatted_token}"
    return {
        "claim_token": formatted_token,
        "claim_url": claim_url,
        "expires_at": (
            pending_reg.expires_at.isoformat() if pending_reg.expires_at else None
        ),
    }


def _switch_auth_method(
    user, db, auth_method: str, data: dict
) -> tuple[dict, tuple | None]:
    """Execute auth method switch for a user.

    Returns (setup_data, error_response_or_none).
    error_response is a (jsonify, status_code) tuple if validation fails.
    """
    setup_data: dict = {}

    if auth_method == "totp":
        secret_bytes, _, _, setup_data = _setup_totp_data(user.username)
        user.auth_type = AuthType.TOTP
        user.auth_credential = secret_bytes
        user.save(db)

    elif auth_method == "magic_link":
        email = data.get("email", "").strip() if data.get("email") else ""
        user_email = user.recovery_email or ""
        effective_email = email or user_email
        if not effective_email:
            return {}, (
                jsonify({"error": "Email is required for magic_link auth method"}),
                400,
            )
        user.auth_type = AuthType.MAGIC_LINK
        user.auth_credential = b""
        if email:
            user.recovery_email = email
        user.save(db)

    elif auth_method == "passkey":
        user.auth_type = AuthType.PASSKEY
        user.auth_credential = b"pending"
        user.save(db)
        setup_data = _setup_passkey_data(db, user.username)

    return setup_data, None


def _apply_claim_credentials_reset(
    existing_user,
    db,
    obj,
    auth_method,
    username,
    recovery_email,
    recovery_phone,
    recovery_enabled,
):
    """Handle credential reset for an existing user during claim flow.

    Returns a Flask JSON response.
    """
    if auth_method == "magic_link":
        existing_user.auth_type = AuthType.MAGIC_LINK
        existing_user.auth_credential = b""
        if recovery_email:
            existing_user.recovery_email = recovery_email
        if recovery_phone:
            existing_user.recovery_phone = recovery_phone
        existing_user.recovery_enabled = True
        existing_user.save(db)
        obj.consume(db)

        backup_codes = BackupCodeRepository(db).create_codes_for_user(existing_user.id)

        return jsonify(
            {
                "success": True,
                "auth_method": "magic_link",
                "username": username,
                "backup_codes": backup_codes,
                "message": (
                    "Your credentials have been reset! To sign in, click"
                    " 'Sign in with email link' on the login page."
                ),
                "warning": (
                    "IMPORTANT: Save your backup codes in a safe place!"
                    " These are your ONLY way to recover your account"
                    " if you lose access to your email."
                ),
            }
        )

    # TOTP reset
    totp_secret, totp_base32, totp_uri = setup_totp(username)
    existing_user.auth_type = AuthType.TOTP
    existing_user.auth_credential = totp_secret
    if recovery_email:
        existing_user.recovery_email = recovery_email
    if recovery_phone:
        existing_user.recovery_phone = recovery_phone
    existing_user.recovery_enabled = recovery_enabled
    existing_user.save(db)
    obj.consume(db)

    backup_codes = BackupCodeRepository(db).create_codes_for_user(existing_user.id)

    response_data = {
        "success": True,
        "username": username,
        "totp_secret": totp_base32,
        "totp_uri": totp_uri,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": (
            "Your credentials have been reset! Set up your authenticator app"
            " using the QR code or manual entry, then log in with your"
            " 6-digit code."
        ),
        "warning": (
            "IMPORTANT: Save your backup codes in a safe place! These are"
            " your ONLY way to recover your account if you lose your"
            " authenticator device."
        ),
    }

    try:
        qr_png = generate_qr_code(base32_to_secret(totp_base32), username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode("ascii")
    except ImportError:
        pass

    return jsonify(response_data)


def _apply_claim_new_user_totp(
    db,
    username,
    can_download,
    recovery_email,
    recovery_phone,
    recovery_enabled,
    access_req_id,
):
    """Create a new TOTP user during claim flow. Returns Flask JSON response."""
    totp_secret, totp_base32, totp_uri = setup_totp(username)

    new_user = User(
        username=username,
        auth_type=AuthType.TOTP,
        auth_credential=totp_secret,
        can_download=can_download,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    new_user.save(db)

    backup_codes = BackupCodeRepository(db).create_codes_for_user(new_user.id)
    AccessRequestRepository(db).mark_credentials_claimed(access_req_id)

    response_data = {
        "success": True,
        "username": username,
        "totp_secret": totp_base32,
        "totp_uri": totp_uri,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": (
            "Your account is ready! Set up your authenticator app using the"
            " QR code or manual entry, then log in with your 6-digit code."
            " Save your backup codes securely."
        ),
        "warning": (
            "IMPORTANT: Save your backup codes in a safe place! These are"
            " your ONLY way to recover your account if you lose your"
            " authenticator device."
        ),
    }

    try:
        qr_png = generate_qr_code(base32_to_secret(totp_base32), username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode("ascii")
    except ImportError:
        pass

    return jsonify(response_data)


def _apply_claim_new_user_magic_link(
    db,
    username,
    can_download,
    recovery_email,
    recovery_phone,
    access_req_id,
):
    """Create a new magic_link user during claim flow. Returns Flask JSON response."""
    new_user = User(
        username=username,
        auth_type=AuthType.MAGIC_LINK,
        auth_credential=b"",
        can_download=can_download,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=True,
    )
    new_user.save(db)

    backup_codes = BackupCodeRepository(db).create_codes_for_user(new_user.id)
    AccessRequestRepository(db).mark_credentials_claimed(access_req_id)

    return jsonify(
        {
            "success": True,
            "auth_method": "magic_link",
            "username": username,
            "backup_codes": backup_codes,
            "message": (
                "Your account is ready! To sign in, click"
                " 'Sign in with email link' on the login page."
                " We'll send a one-click link to your email."
            ),
            "warning": (
                "IMPORTANT: Save your backup codes in a safe place!"
                " These are your ONLY way to recover your account"
                " if you lose access to your email."
            ),
        }
    )


def _verify_webauthn_credential(data, origin, rp_id):
    """Verify a WebAuthn registration credential from request data.

    Returns (webauthn_cred, challenge_bytes, error_response).
    error_response is a (jsonify, status) tuple on failure, None on success.
    """
    from webauthn.helpers import base64url_to_bytes

    challenge_b64 = data.get("challenge", "").strip()
    credential = data.get("credential")

    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception as e:
        logger.warning("Invalid challenge format: %s", e)
        return None, None, (jsonify({"error": "Invalid challenge format"}), 400)

    credential_json = (
        json.dumps(credential) if isinstance(credential, dict) else credential
    )

    webauthn_cred = webauthn_verify_registration(
        credential_json=credential_json,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        return None, None, (jsonify({"error": "WebAuthn verification failed"}), 400)

    return webauthn_cred, challenge, None


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
    logger.info(
        "WebAuthn config: rp_id=%s, origin=%s, rp_name=%s", rp_id, origin, rp_name
    )


def get_auth_db() -> AuthDatabase:
    """Get the auth database instance."""
    if _auth_db is None:
        raise RuntimeError(
            "Auth routes not initialized. Call init_auth_routes() first."
        )
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
    if hasattr(g, "_current_user"):
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
    if not hasattr(g, "_current_session"):
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
        if remote_addr not in ("127.0.0.1", "::1", "localhost"):
            # Also check X-Forwarded-For if behind proxy
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                # Take the first address (client IP)
                remote_addr = forwarded.split(",")[0].strip()

            if remote_addr not in ("127.0.0.1", "::1", "localhost"):
                return (
                    jsonify({"error": "Access denied"}),
                    404,
                )  # Return 404 to hide existence
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


def guest_allowed(f: Callable) -> Callable:
    """
    Decorator to allow unauthenticated read access.

    Sets g.user if a valid session exists, None otherwise.
    Always allows the request through — never returns 401.

    When AUTH_ENABLED is False, behaves identically to no decorator.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if not current_app.config.get("AUTH_ENABLED", False):
            # Auth disabled — allow through (single-user mode)
            return f(*args, **kwargs)
        # Auth enabled — try to populate user, but allow guest access
        g.user = get_current_user()  # None for guests, User for logged-in
        return f(*args, **kwargs)

    return decorated


def admin_or_localhost(f: Callable) -> Callable:
    """
    Decorator for sensitive admin endpoints (service control, upgrades).

    Adapts security based on deployment mode:
    - AUTH_ENABLED=true (remote): Requires authenticated admin user
    - AUTH_ENABLED=false (standalone): Restricts to localhost only

    This ensures admin endpoints are never wide-open regardless of mode.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        if current_app.config.get("AUTH_ENABLED", False):
            # Remote mode: require authenticated admin
            user = get_current_user()
            if user is None:
                return jsonify({"error": "Authentication required"}), 401
            if not user.is_admin:
                return jsonify({"error": "Admin privileges required"}), 403
        else:
            # Standalone mode: localhost only
            remote_addr = request.remote_addr
            if remote_addr not in ("127.0.0.1", "::1", "localhost"):
                forwarded = request.headers.get("X-Forwarded-For", "")
                if forwarded:
                    remote_addr = forwarded.split(",")[0].strip()
                if remote_addr not in ("127.0.0.1", "::1", "localhost"):
                    return jsonify({"error": "Access denied"}), 404
        return f(*args, **kwargs)

    return decorated


# Session duration constants
SESSION_DURATION_DEFAULT = None  # Session cookie (cleared on browser close)
SESSION_DURATION_REMEMBER = 10 * 365 * 24 * 60 * 60  # ~10 years (until sign-out)


def set_session_cookie(
    response: Response, token: str, remember_me: bool = False
) -> Response:
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return redirect("/login.html", code=302)
    """
    Authenticate user with TOTP code.

    Request body:
        {
            "username": "string",
            "code": "123456",  // TOTP code
            "remember_me": false  // Optional: keep session indefinitely
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
    allow_multi = _user_allows_multi_session(user, db)
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
        allow_multi=allow_multi,
    )

    # Update last login
    user.update_last_login(db)

    # Build response — include session_token for client-side persistence
    response_data = {
        "success": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "can_download": user.can_download,
            "is_admin": user.is_admin,
        },
    }
    if remember_me:
        response_data["session_token"] = token
    response = jsonify(response_data)

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


@auth_bp.route("/session/restore", methods=["POST"])
def restore_session():
    """
    Restore a persistent session from a client-stored token.

    Called by the frontend when the session cookie is missing but the client
    has a stored token (localStorage or IndexedDB). Re-validates the token
    and re-sets the session cookie.

    Request body:
        {"token": "raw_session_token"}

    Returns:
        200: {"success": true, "user": {...}}
        401: {"error": "Invalid or expired session"}
    """
    data = request.get_json()
    if not data or not data.get("token"):
        return jsonify({"error": "Token is required"}), 400

    raw_token = data["token"].strip()
    db = get_auth_db()
    session_repo = SessionRepository(db)

    session = session_repo.get_by_token(raw_token)
    if session is None:
        return jsonify({"error": "Invalid or expired session"}), 401

    # Only allow restore for persistent sessions
    if not session.is_persistent:
        return jsonify({"error": "Session is not persistent"}), 401

    # Check if session is still valid (not stale)
    if session.is_stale():
        session.invalidate(db)
        return jsonify({"error": "Session has expired"}), 401

    # Touch session to update last_seen
    session.touch(db)

    # Get user info
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(session.user_id)
    if user is None:
        session.invalidate(db)
        return jsonify({"error": "User not found"}), 401

    response = jsonify(
        {
            "success": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "can_download": user.can_download,
                "is_admin": user.is_admin,
            },
        }
    )

    # Re-set the session cookie
    return set_session_cookie(response, raw_token, remember_me=True)


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

    return jsonify(
        {
            "user": {
                "id": user.id,
                "username": user.username,
                "email": user.recovery_email,
                "auth_type": user.auth_type.value,
                "can_download": user.can_download,
                "is_admin": user.is_admin,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login": user.last_login.isoformat() if user.last_login else None,
            },
            "session": {
                "created_at": (
                    session.created_at.isoformat() if session.created_at else None
                ),
                "last_seen": (
                    session.last_seen.isoformat() if session.last_seen else None
                ),
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
            ],
        }
    )


@auth_bp.route("/me", methods=["PUT"])
@login_required
def update_current_user():
    """
    Update the currently authenticated user's profile.

    JSON body:
        username: New username (optional, 3-24 chars, ASCII printable except <>\\)
        email: New email (optional, or null to remove)

    Returns:
        200: {"success": true, "user": {...}}
        400: {"error": "..."}
        409: {"error": "Username already taken"}
    """
    user = get_current_user()
    data = request.get_json() or {}
    db = get_auth_db()
    user_repo = UserRepository(db)

    new_username = data.get("username")
    if new_username is not None:
        err = _validate_username(new_username)
        if err:
            return jsonify(err[0]), err[1]
        if not user_repo.update_username(user.id, new_username):
            return jsonify({"error": "Username already taken"}), 409

    if "email" in data:
        new_email = data.get("email")
        if new_email is not None and new_email != "":
            err = _validate_email_format(new_email)
            if err:
                return jsonify(err[0]), err[1]
        else:
            new_email = None
        user_repo.update_email(user.id, new_email)

    updated_user = user_repo.get_by_id(user.id)
    _audit_profile_changes(db, user, data, new_username)

    return jsonify(
        {
            "success": True,
            "user": _user_dict(updated_user, include_auth_type=True),
        }
    )


def _audit_profile_changes(db, user, data, new_username):
    """Log audit entry if profile fields changed."""
    changes = {}
    if new_username is not None and new_username != user.username:
        changes["username"] = {"old": user.username, "new": new_username}
    if "email" in data:
        old_email = user.recovery_email
        new_email_val = data.get("email") or None
        if new_email_val != old_email:
            changes["email"] = {"old": old_email, "new": new_email_val}
    if changes:
        from auth.audit import AuditLogRepository

        AuditLogRepository(db).log(
            actor_id=user.id,
            target_id=user.id,
            action="update_profile",
            details={"changes": changes, "actor_username": user.username},
        )


@auth_bp.route("/me/auth-method", methods=["PUT"])
@login_required
def update_auth_method():
    """
    Update the user's authentication method.

    Supports switching between totp, passkey, and magic_link.

    JSON body:
        auth_method: "totp" | "passkey" | "magic_link"
        phase: "setup" | "confirm" (for totp/passkey multi-step)
        code: 6-digit TOTP code (for totp confirm phase)

    Returns:
        200: {"success": true, "auth_type": "..."}
        400: {"error": "..."}
    """
    user = get_current_user()
    data = request.get_json() or {}

    auth_method = data.get("auth_method", "").strip()
    phase = data.get("phase", "setup")

    if auth_method not in ("totp", "passkey", "magic_link"):
        return jsonify({"error": "Invalid auth method"}), 400

    db = get_auth_db()

    if auth_method == "magic_link":
        return _update_auth_to_magic_link(user, db)

    if auth_method == "totp":
        return _update_auth_totp_phase(user, data, db, phase)

    if auth_method == "passkey":
        return jsonify(
            {
                "success": True,
                "phase": "setup",
                "message": "Use the passkey registration flow to complete setup.",
                "registration_url": "/auth/register/webauthn/begin",
            }
        )

    return jsonify({"error": "Unsupported auth method"}), 400


def _update_auth_to_magic_link(user, db):
    """Switch user to magic_link auth. Requires email already set."""
    if not user.recovery_email:
        return (
            jsonify(
                {"error": "Email address required. Add an email in your profile first."}
            ),
            400,
        )
    with db.connection() as conn:
        conn.execute(
            "UPDATE users SET auth_type = ?, recovery_enabled = 1 WHERE id = ?",
            ("magic_link", user.id),
        )
    return jsonify({"success": True, "auth_type": "magic_link"})


def _update_auth_totp_phase(user, data, db, phase):
    """Handle TOTP setup or confirm phase for auth method switch."""
    import pyotp

    if phase == "setup":
        secret = pyotp.random_base32()
        totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=user.username, issuer_name="The Library"
        )
        _pending_totp_secrets[user.id] = secret
        return jsonify(
            {
                "success": True,
                "phase": "setup",
                "totp_secret": secret,
                "totp_uri": totp_uri,
            }
        )

    if phase == "confirm":
        return _confirm_totp_switch(user, data, db)

    return jsonify({"error": "Invalid phase"}), 400


def _confirm_totp_switch(user, data, db):
    """Confirm TOTP code and complete auth method switch."""
    import pyotp

    code = data.get("code", "").strip()
    if not code or len(code) != 6:
        return jsonify({"error": "6-digit code required"}), 400

    pending_secret = _pending_totp_secrets.get(user.id)
    if not pending_secret:
        return jsonify({"error": "No pending TOTP setup. Start over."}), 400

    if not pyotp.TOTP(pending_secret).verify(code, valid_window=1):
        return jsonify({"error": "Invalid code. Try again."}), 400

    raw_secret = base32_to_secret(pending_secret)
    with db.connection() as conn:
        conn.execute(
            "UPDATE users SET auth_type = ?, auth_credential = ? WHERE id = ?",
            ("totp", raw_secret, user.id),
        )
    _pending_totp_secrets.pop(user.id, None)
    return jsonify({"success": True, "auth_type": "totp"})


@auth_bp.route("/me/webauthn/begin", methods=["POST"])
@login_required
def begin_webauthn_switch():
    """
    Start WebAuthn registration for an authenticated user switching auth method.

    JSON body:
        auth_type: "passkey" | "fido2"

    Returns:
        200: {"options": {...}, "challenge": "..."}
        400: {"error": "..."}
    """
    from webauthn.helpers import bytes_to_base64url

    user = get_current_user()
    data = request.get_json() or {}
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type. Use 'passkey' or 'fido2'."}), 400

    rp_id, rp_name, _ = get_webauthn_config()
    authenticator_type = "platform" if auth_type == "passkey" else "cross-platform"

    options_json, challenge = webauthn_registration_options(
        username=user.username,
        rp_id=rp_id,
        rp_name=rp_name,
        authenticator_type=authenticator_type,
    )

    challenge_b64 = bytes_to_base64url(challenge)
    _pending_webauthn_challenges[user.id] = challenge_b64

    return jsonify({"options": options_json, "challenge": challenge_b64})


@auth_bp.route("/me/webauthn/complete", methods=["POST"])
@login_required
def complete_webauthn_switch():
    """
    Complete WebAuthn registration for an authenticated user switching auth method.

    JSON body:
        credential: {...}  (encoded WebAuthn credential)
        challenge: "..."   (base64url challenge from begin)
        auth_type: "passkey" | "fido2"

    Returns:
        200: {"success": true, "auth_type": "..."}
        400: {"error": "..."}
    """
    from webauthn.helpers import base64url_to_bytes

    user = get_current_user()
    data = request.get_json() or {}

    credential_data = data.get("credential")
    challenge_b64 = data.get("challenge", "")
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if not credential_data or not challenge_b64:
        return jsonify({"error": "Credential and challenge required"}), 400

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type"}), 400

    # Verify challenge matches
    pending_challenge = _pending_webauthn_challenges.get(user.id)
    if not pending_challenge or pending_challenge != challenge_b64:
        return jsonify({"error": "Invalid or expired challenge. Start over."}), 400

    rp_id, _, expected_origin = get_webauthn_config()
    challenge_bytes = base64url_to_bytes(challenge_b64)

    # Convert credential to JSON string if it's a dict
    credential_json = (
        json.dumps(credential_data)
        if isinstance(credential_data, dict)
        else credential_data
    )

    webauthn_cred = webauthn_verify_registration(
        credential_json=credential_json,
        expected_challenge=challenge_bytes,
        expected_origin=expected_origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        _pending_webauthn_challenges.pop(user.id, None)
        return jsonify({"error": "WebAuthn verification failed"}), 400

    # Store credential and switch auth type
    db = get_auth_db()

    with db.connection() as conn:
        conn.execute(
            "UPDATE users SET auth_type = ?, auth_credential = ? WHERE id = ?",
            (auth_type, webauthn_cred.to_json().encode("utf-8"), user.id),
        )

        # Store WebAuthn credential details
        from webauthn.helpers import bytes_to_base64url as b2b64

        conn.execute(
            """INSERT OR REPLACE INTO webauthn_credentials
               (user_id, credential_id, public_key, sign_count, transports, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (
                user.id,
                b2b64(webauthn_cred.credential_id),
                b2b64(webauthn_cred.public_key),
                webauthn_cred.sign_count,
                ",".join(credential_data.get("transports", [])),
            ),
        )

    _pending_webauthn_challenges.pop(user.id, None)

    return jsonify({"success": True, "auth_type": auth_type})


@auth_bp.route("/check", methods=["GET"])
def check_auth():
    """
    Check if the user is authenticated (lightweight endpoint).

    Returns:
        200: {"authenticated": true, "username": "..."} or {"authenticated": false}
    """
    user = get_current_user()
    if user:
        return jsonify(
            {
                "authenticated": True,
                "username": user.username,
                "is_admin": user.is_admin,
            }
        )
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

    err = _validate_username(username)
    if err:
        return jsonify(err[0]), err[1]

    if contact_email and ("@" not in contact_email or "." not in contact_email):
        return jsonify({"error": "Invalid email address format"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)
    request_repo = AccessRequestRepository(db)

    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 400

    dup_err = _check_duplicate_request(request_repo, username)
    if dup_err:
        return dup_err

    # First-user-is-admin bootstrap
    if user_repo.count() == 0:
        return _bootstrap_first_user(db, username)

    return _create_access_request(request_repo, username, contact_email)


def _check_duplicate_request(request_repo, username):
    """Check for duplicate access requests. Returns response tuple or None."""
    if not request_repo.has_any_request(username):
        return None
    if request_repo.has_pending_request(username):
        return jsonify(
            {"error": "Access request already pending for this username"}
        ), 400
    return jsonify({"error": "Username already has a previous access request"}), 400


def _bootstrap_first_user(db, username):
    """Create the first user as admin with TOTP. Returns JSON response."""
    totp_secret, totp_base32, totp_uri = setup_totp(username)
    new_user = User(
        username=username,
        auth_type=AuthType.TOTP,
        auth_credential=totp_secret,
        can_download=True,
        is_admin=True,
    )
    new_user.save(db)

    codes = BackupCodeRepository(db).create_codes_for_user(new_user.id)
    qr_png = generate_qr_code(base32_to_secret(totp_base32), username)
    qr_base64 = base64.b64encode(qr_png).decode("ascii")

    return jsonify(
        {
            "success": True,
            "first_user": True,
            "message": "You are the first user and have been granted admin access.",
            "totp_secret": totp_base32,
            "totp_uri": totp_uri,
            "totp_qr": qr_base64,
            "backup_codes": codes,
        }
    )


def _create_access_request(request_repo, username, contact_email):
    """Create a standard access request with claim token. Returns JSON response."""
    raw_claim_token, _ = generate_verification_token()
    truncated_token, formatted_token = _format_claim_token(raw_claim_token)
    claim_token_hash = hash_token(truncated_token)

    access_request = request_repo.create(username, claim_token_hash, contact_email)

    response_data = {
        "success": True,
        "message": (
            "Access request submitted. Save your claim token - you'll need it"
            " to complete setup after approval."
        ),
        "request_id": access_request.id,
        "claim_token": formatted_token,
        "username": username,
    }

    if contact_email:
        response_data["email_notification"] = True
        response_data["message"] += (
            f" We'll also notify you at {contact_email} when your request is reviewed."
        )

    return jsonify(response_data)


def _resolve_claim_token(username, claim_token):
    """
    Resolve a claim token from either access_requests (new user) or
    pending_registrations (existing user credential reset).

    Returns:
        (mode, obj, user_or_none, error_response)
        - mode: "new_user" | "credential_reset"
        - obj: AccessRequest or PendingRegistration
        - user_or_none: existing User for resets, None for new
        - error_response: (jsonify, status_code) tuple if invalid, else None
    """
    clean_token = claim_token.replace("-", "")
    db = get_auth_db()
    request_repo = AccessRequestRepository(db)
    user_repo = UserRepository(db)
    claim_token_hash = hash_token(clean_token)

    # Path 1: Check access_requests (new user registration)
    access_req = request_repo.get_pending_by_username_and_token(
        username, claim_token_hash
    )
    if access_req:
        return _resolve_access_request(access_req, user_repo, username)

    # Path 2: Check pending_registrations (existing user credential reset)
    return _resolve_pending_registration(db, user_repo, clean_token, username)


def _resolve_access_request(access_req, user_repo, username):
    """Validate an access request claim token. Returns resolve tuple."""
    if access_req.status == AccessRequestStatus.PENDING:
        return _resolve_claim_error(
            "pending", "Your request is still pending admin review"
        )
    if access_req.status == AccessRequestStatus.DENIED:
        return _resolve_claim_error(
            "denied", access_req.deny_reason or "Your request was denied"
        )
    if access_req.credentials_claimed or user_repo.username_exists(username):
        return _resolve_claim_error(
            "already_claimed", "Credentials have already been claimed."
        )
    if access_req.is_claim_expired():
        return _resolve_claim_error(
            "expired",
            "This invitation has expired. Please ask the admin to send a new one.",
        )
    return "new_user", access_req, None, None


def _resolve_pending_registration(db, user_repo, clean_token, username):
    """Validate a pending registration claim token. Returns resolve tuple."""
    reg_repo = PendingRegistrationRepository(db)
    pending_reg = reg_repo.get_by_token(clean_token)

    if not pending_reg or pending_reg.username != username:
        return (
            None,
            None,
            None,
            (
                jsonify({"valid": False, "error": "Invalid username or claim token"}),
                404,
            ),
        )

    if pending_reg.is_expired():
        pending_reg.consume(db)
        return _resolve_claim_error(
            "expired",
            "This reset token has expired. Please ask the admin for a new one.",
        )

    existing_user = user_repo.get_by_username(username)
    if not existing_user:
        return (
            None,
            None,
            None,
            (jsonify({"valid": False, "error": "User account not found"}), 404),
        )
    if existing_user.auth_credential != b"pending":
        return _resolve_claim_error(
            "already_claimed", "Credentials have already been set up."
        )

    return "credential_reset", pending_reg, existing_user, None


@auth_bp.route("/register/claim/validate", methods=["POST"])
def validate_claim_token():
    """
    Validate a claim token and return the approval status.

    Supports both new user registration (access_requests) and existing
    user credential resets (pending_registrations).

    Request body:
        {
            "username": "string",
            "claim_token": "XXXX-XXXX-XXXX-XXXX"
        }

    Returns:
        200: {
            "valid": true,
            "status": "approved",
            "mode": "new_user" | "credential_reset",
            "username": "..."
        }
        400: {"valid": false, "status": "pending|denied|already_claimed",
              "error": "..."}
        404: {"valid": false, "error": "Invalid username or claim token"}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()

    if not username or not claim_token:
        return jsonify({"error": "Username and claim_token are required"}), 400

    mode, obj, existing_user, error = _resolve_claim_token(username, claim_token)
    if error:
        return error

    return jsonify(
        {"valid": True, "status": "approved", "mode": mode, "username": username}
    )


@auth_bp.route("/register/claim", methods=["POST"])
def claim_credentials():
    """
    Claim credentials using TOTP or magic link authentication method.

    Supports both new user registration (via access_requests) and existing
    user credential resets (via pending_registrations).
    For passkey/FIDO2, use the /register/claim/webauthn endpoints.

    Request body:
        {
            "username": "string",
            "claim_token": "XXXX-XXXX-XXXX-XXXX",
            "auth_method": "totp" | "magic_link" (default: "totp"),
            "recovery_email": "optional (required for magic_link)",
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
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    err = _validate_claim_input(data)
    if err:
        return err

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    auth_method = data.get("auth_method", "totp").strip()
    recovery_email, recovery_phone, recovery_enabled = _extract_recovery_fields(data)

    mode, obj, existing_user, error = _resolve_claim_token(username, claim_token)
    if error:
        return error

    db = get_auth_db()

    if mode == "credential_reset":
        return _apply_claim_credentials_reset(
            existing_user,
            db,
            obj,
            auth_method,
            username,
            recovery_email,
            recovery_phone,
            recovery_enabled,
        )

    can_download = _parse_invite_meta(obj.backup_codes_json)

    if auth_method == "magic_link":
        return _apply_claim_new_user_magic_link(
            db,
            username,
            can_download,
            recovery_email,
            recovery_phone,
            obj.id,
        )

    return _apply_claim_new_user_totp(
        db,
        username,
        can_download,
        recovery_email,
        recovery_phone,
        recovery_enabled,
        obj.id,
    )


def _validate_claim_input(data: dict) -> tuple | None:
    """Validate claim_credentials input. Returns error response or None."""
    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    auth_method = data.get("auth_method", "totp").strip()

    if not username or not claim_token:
        return jsonify({"error": "Username and claim_token are required"}), 400
    if auth_method not in ("totp", "magic_link"):
        return jsonify(
            {"error": "Invalid auth_method. Use 'totp' or 'magic_link'"}
        ), 400
    recovery_email = (data.get("recovery_email") or "").strip() or None
    if auth_method == "magic_link" and not recovery_email:
        return jsonify(
            {"error": "Email address is required for magic link authentication"}
        ), 400
    return None


@auth_bp.route("/register/claim/webauthn/begin", methods=["POST"])
def claim_webauthn_begin():
    """
    Start WebAuthn registration for claim flow.

    Supports both new user registration and existing user credential resets.

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

    mode, obj, existing_user, error = _resolve_claim_token(username, claim_token)
    if error:
        return error

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

    return jsonify(
        {
            "options": options_json,
            "challenge": bytes_to_base64url(challenge),
        }
    )


@auth_bp.route("/register/claim/webauthn/complete", methods=["POST"])
def claim_webauthn_complete():
    """
    Complete WebAuthn registration for claim flow.

    Supports both new user registration and existing user credential resets.

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
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    err = _validate_claim_webauthn_input(data)
    if err:
        return err

    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()
    recovery_email, recovery_phone, recovery_enabled = _extract_recovery_fields(data)

    mode, obj, existing_user, error = _resolve_claim_token(username, claim_token)
    if error:
        return error

    rp_id, _, origin = get_webauthn_config()
    webauthn_cred, _, verify_err = _verify_webauthn_credential(data, origin, rp_id)
    if verify_err:
        return verify_err

    db = get_auth_db()

    if mode == "credential_reset":
        response_data, token = _claim_webauthn_reset(
            db,
            existing_user,
            obj,
            webauthn_cred,
            auth_type,
            username,
            recovery_email,
            recovery_phone,
            recovery_enabled,
        )
    else:
        response_data, token = _claim_webauthn_new_user(
            db,
            obj,
            webauthn_cred,
            auth_type,
            username,
            recovery_email,
            recovery_phone,
            recovery_enabled,
        )

    response_data["warning"] = _recovery_warning(recovery_enabled, "passkey")
    return set_session_cookie(jsonify(response_data), token)


def _validate_claim_webauthn_input(data: dict) -> tuple | None:
    """Validate claim_webauthn_complete input. Returns error response or None."""
    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    credential = data.get("credential")
    challenge = data.get("challenge", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if not username or not claim_token or not credential or not challenge:
        return jsonify(
            {"error": "Username, claim_token, credential, and challenge are required"}
        ), 400
    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type"}), 400
    return None


def _claim_webauthn_reset(
    db,
    existing_user,
    obj,
    webauthn_cred,
    auth_type,
    username,
    recovery_email,
    recovery_phone,
    recovery_enabled,
):
    """Handle WebAuthn credential reset for existing user. Returns (data, token)."""
    existing_user.auth_type = (
        AuthType.PASSKEY if auth_type == "passkey" else AuthType.FIDO2
    )
    existing_user.auth_credential = webauthn_cred.to_json().encode("utf-8")
    if recovery_email:
        existing_user.recovery_email = recovery_email
    if recovery_phone:
        existing_user.recovery_phone = recovery_phone
    existing_user.recovery_enabled = recovery_enabled
    existing_user.save(db)
    obj.consume(db)

    backup_codes = BackupCodeRepository(db).create_codes_for_user(existing_user.id)
    allow_multi = _user_allows_multi_session(existing_user, db)
    session, token = Session.create_for_user(
        db,
        existing_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        allow_multi=allow_multi,
    )
    existing_user.update_last_login(db)

    return {
        "success": True,
        "username": username,
        "user_id": existing_user.id,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": f"Credentials reset successfully with {auth_type} authentication.",
    }, token


def _claim_webauthn_new_user(
    db,
    obj,
    webauthn_cred,
    auth_type,
    username,
    recovery_email,
    recovery_phone,
    recovery_enabled,
):
    """Create new user via WebAuthn claim flow. Returns (data, token)."""
    can_download = _parse_invite_meta(obj.backup_codes_json)

    new_user = User(
        username=username,
        auth_type=AuthType.PASSKEY if auth_type == "passkey" else AuthType.FIDO2,
        auth_credential=webauthn_cred.to_json().encode("utf-8"),
        can_download=can_download,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    new_user.save(db)

    backup_codes = BackupCodeRepository(db).create_codes_for_user(new_user.id)
    AccessRequestRepository(db).mark_credentials_claimed(obj.id)

    allow_multi = _user_allows_multi_session(new_user, db)
    session, token = Session.create_for_user(
        db,
        new_user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        allow_multi=allow_multi,
    )
    new_user.update_last_login(db)

    return {
        "success": True,
        "username": username,
        "user_id": new_user.id,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": f"Account created successfully with {auth_type} authentication.",
    }, token


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
        return jsonify(
            {
                "status": "approved",
                "message": "Your access has been approved. You can now log in.",
            }
        )

    # Check access request
    access_request = request_repo.get_by_username(username)
    if not access_request:
        return jsonify({"error": "No access request found for this username"}), 404

    if access_request.status == AccessRequestStatus.PENDING:
        return jsonify(
            {
                "status": "pending",
                "message": "Your request is awaiting administrator review.",
            }
        )
    elif access_request.status == AccessRequestStatus.DENIED:
        return jsonify(
            {
                "status": "denied",
                "message": access_request.deny_reason or "Your request was denied.",
            }
        )
    else:
        return jsonify(
            {
                "status": access_request.status.value,
                "message": "Unknown status.",
            }
        )


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

    Returns:
        200: {
            "success": true,
            "username": "...",
            "totp_secret": "...",
            "totp_uri": "...",
            "totp_qr": "...",
            "backup_codes": [...],
            "recovery_enabled": bool,
            "warning": "..."
        }
        400: {"error": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    auth_type = data.get("auth_type", "totp").strip().lower()
    include_qr = data.get("include_qr", False)
    recovery_email, recovery_phone, recovery_enabled = _extract_recovery_fields(data)

    if not token:
        return jsonify({"error": "Verification token required"}), 400
    if auth_type not in ("totp",):
        return jsonify({"error": "Unsupported auth type. Use 'totp'."}), 400

    db = get_auth_db()
    reg = PendingRegistrationRepository(db).get_by_token(token)
    if reg is None:
        return jsonify({"error": "Invalid or expired verification token"}), 400
    if reg.is_expired():
        reg.consume(db)
        return jsonify({"error": "Verification token has expired"}), 400

    secret, base32_secret, uri = setup_totp(reg.username)
    user = User(
        username=reg.username,
        auth_type=AuthType.TOTP,
        auth_credential=secret,
        can_download=True,
        is_admin=False,
        recovery_email=recovery_email,
        recovery_phone=recovery_phone,
        recovery_enabled=recovery_enabled,
    )
    user.save(db)
    backup_codes = BackupCodeRepository(db).create_codes_for_user(user.id)
    reg.consume(db)

    response_data = {
        "success": True,
        "username": user.username,
        "user_id": user.id,
        "totp_secret": base32_secret,
        "totp_uri": uri,
        "backup_codes": backup_codes,
        "recovery_enabled": recovery_enabled,
        "message": "Account created. Scan the QR code or enter the secret in your authenticator app.",
        "warning": _recovery_warning(recovery_enabled),
    }

    if include_qr:
        qr_png = generate_qr_code(secret, user.username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode("ascii")

    return jsonify(response_data)


# =============================================================================
# WebAuthn/Passkey Registration Endpoints
# =============================================================================


def get_webauthn_config() -> tuple[str, str, str]:
    """Get WebAuthn configuration, deriving from deployment config if not explicit.

    Priority:
    1. Explicit WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN (env or audiobooks.conf)
    2. Auto-derived from AUDIOBOOKS_HOSTNAME + AUDIOBOOKS_WEB_PORT +
       AUDIOBOOKS_HTTPS_ENABLED
    3. Fallback to localhost defaults (development)
    """
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from config import get_config

    rp_id = get_config("WEBAUTHN_RP_ID") or _derive_rp_id(get_config)
    rp_name = get_config("WEBAUTHN_RP_NAME", "The Library")
    origin = get_config("WEBAUTHN_ORIGIN") or _derive_origin(rp_id, get_config)

    return rp_id, rp_name, origin


def _derive_rp_id(get_config) -> str:
    """Derive WebAuthn RP ID from hostname config."""
    import socket

    hostname = get_config("AUDIOBOOKS_HOSTNAME") or socket.getfqdn()
    is_local = (
        hostname in ("localhost", "127.0.0.1", "::1")
        or hostname.endswith((".local", ".localdomain", ".localhost"))
        or "." not in hostname
    )
    return "localhost" if is_local else hostname


def _derive_origin(rp_id: str, get_config) -> str:
    """Derive WebAuthn origin from proxy config."""
    https_enabled = get_config("AUDIOBOOKS_HTTPS_ENABLED", "true").lower() == "true"
    web_port = int(get_config("AUDIOBOOKS_WEB_PORT") or get_config("WEB_PORT", "8443"))
    scheme = "https" if https_enabled else "http"
    default_port = 443 if https_enabled else 80

    if rp_id == "localhost":
        return f"{scheme}://localhost:{web_port}"
    if web_port == default_port:
        return f"{scheme}://{rp_id}"
    return f"{scheme}://{rp_id}:{web_port}"


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

    return jsonify(
        {
            "options": options_json,  # Already JSON string
            "challenge": bytes_to_base64url(challenge),
            "token": token,  # Return for completion step
        }
    )


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
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    auth_type = data.get("auth_type", "passkey").strip().lower()
    recovery_email, recovery_phone, recovery_enabled = _extract_recovery_fields(data)

    err = _validate_webauthn_reg_input(token, data, auth_type)
    if err:
        return jsonify(err[0]), err[1]

    db = get_auth_db()
    reg = PendingRegistrationRepository(db).get_by_token(token)
    if reg is None:
        return jsonify({"error": "Invalid or expired verification token"}), 400
    if reg.is_expired():
        reg.consume(db)
        return jsonify({"error": "Verification token has expired"}), 400

    rp_id, _, origin = get_webauthn_config()
    webauthn_cred, _, verify_err = _verify_webauthn_credential(data, origin, rp_id)
    if verify_err:
        return verify_err

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
    backup_codes = BackupCodeRepository(db).create_codes_for_user(user.id)
    reg.consume(db)

    return jsonify(
        {
            "success": True,
            "username": user.username,
            "user_id": user.id,
            "backup_codes": backup_codes,
            "recovery_enabled": recovery_enabled,
            "message": "Account created successfully with passkey authentication.",
            "warning": _recovery_warning(recovery_enabled, "passkey"),
        }
    )


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
        return (
            jsonify(
                {
                    "error": "User does not use passkey authentication",
                    "auth_type": user.auth_type.value,
                }
            ),
            400,
        )

    # Parse stored credential
    try:
        webauthn_cred = WebAuthnCredential.from_json(
            user.auth_credential.decode("utf-8")
        )
    except Exception as e:
        # Log only the exception class — never the credential bytes or parser message
        # (parser errors may echo portions of the raw credential blob).
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure  # Reason: Log message only contains the phrase "Invalid stored credential" (not an actual credential value) plus user_id and exception class name; no secret material in the format string or arguments
        logger.error(
            "Invalid stored credential for user_id=%s error_class=%s",
            getattr(user, "id", "<unknown>"),
            type(e).__name__,
        )
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

    return jsonify(
        {
            "options": options_json,
            "challenge": bytes_to_base64url(challenge),
        }
    )


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
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = data.get("username", "").strip()
    credential = data.get("credential")
    challenge_b64 = data.get("challenge", "").strip()

    if not username or not credential or not challenge_b64:
        return jsonify(
            {"error": "Username, credential, and challenge are required"}
        ), 400

    db = get_auth_db()
    user = UserRepository(db).get_by_username(username)
    if user is None or user.auth_type not in (AuthType.PASSKEY, AuthType.FIDO2):
        return jsonify({"error": "Invalid credentials"}), 401

    webauthn_cred, challenge = _parse_webauthn_login(user, challenge_b64)
    if webauthn_cred is None:
        return jsonify({"error": "Invalid credentials"}), 401

    rp_id, _, origin = get_webauthn_config()
    credential_json = (
        json.dumps(credential) if isinstance(credential, dict) else credential
    )

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

    webauthn_cred.sign_count = new_sign_count
    user.auth_credential = webauthn_cred.to_json().encode("utf-8")
    user.save(db)

    remember_me = data.get("remember_me", True)
    allow_multi = _user_allows_multi_session(user, db)
    session, token = Session.create_for_user(
        db,
        user.id,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
        remember_me=remember_me,
        allow_multi=allow_multi,
    )
    user.update_last_login(db)

    response = jsonify(
        {
            "success": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "can_download": user.can_download,
                "is_admin": user.is_admin,
            },
        }
    )
    return set_session_cookie(response, token, remember_me=remember_me)


def _parse_webauthn_login(user, challenge_b64):
    """Parse stored credential and challenge for login. Returns (cred, challenge) or (None, None)."""
    from webauthn.helpers import base64url_to_bytes

    try:
        webauthn_cred = WebAuthnCredential.from_json(
            user.auth_credential.decode("utf-8")
        )
    except Exception as e:
        # Log only the exception class — never the credential bytes or parser message
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure  # Reason: Log message contains only the phrase "Failed to parse WebAuthn credential" (not an actual credential value) plus exception class name; no secret material in format string or arguments
        logger.warning(
            "Failed to parse WebAuthn credential: error_class=%s", type(e).__name__
        )
        return None, None
    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception as e:
        # Log only the exception class — challenge bytes are opaque and may leak context
        logger.warning(
            "Failed to parse WebAuthn challenge: error_class=%s", type(e).__name__
        )
        return None, None
    return webauthn_cred, challenge


@auth_bp.route("/login/auth-type", methods=["POST"])
def get_auth_type():
    """
    Get the authentication type for a user.

    Used by the frontend to determine which login flow to use.

    Request body:
        {"username": "string"}

    Returns:
        200: {"auth_type": "totp" | "passkey" | "fido2" | "magic_link"}
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

    return jsonify(
        {
            "success": True,
            "username": user.username,
            "totp_secret": base32_secret,
            "totp_uri": uri,
            "backup_codes": new_backup_codes,
            "remaining_old_codes": remaining,
            "message": (
                "Account recovered. Set up your new authenticator and"
                " save your new backup codes."
            ),
            "warning": (
                "Your old backup codes have been invalidated. Save these"
                " new codes in a safe place - they are your only recovery"
                " option if you lose your authenticator again."
                if not user.recovery_enabled
                else (
                    "Your old backup codes have been invalidated. You can"
                    " also recover using your registered email/phone if"
                    " needed."
                )
            ),
        }
    )


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

    return jsonify({"remaining": backup_repo.get_remaining_count(user.id)})


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

    return jsonify(
        {
            "success": True,
            "backup_codes": new_codes,
            "message": (
                "New backup codes generated. Your old codes are no longer valid."
            ),
            "warning": (
                "Save these codes in a safe place! They are your recovery option "
                "if you lose your authenticator."
            ),
        }
    )


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

    return jsonify(
        {
            "success": True,
            "recovery_enabled": user.recovery_enabled,
            "message": (
                "Recovery contact updated. You can now use magic link recovery."
                if user.recovery_enabled
                else "Recovery contact removed. Backup codes are now your"
                " only recovery option."
            ),
        }
    )


# =============================================================================
# Magic Link Login & Recovery
# =============================================================================


@auth_bp.route("/magic-link/login", methods=["POST"])
def magic_link_login():
    """
    Primary magic link login endpoint.

    For magic_link auth_type users: always sends a sign-in link.
    For other users with recovery_email: sends as recovery option.
    Anti-enumeration: always returns generic success message.

    Request body:
        {"identifier": "username_or_email"}

    Returns:
        200: {"success": true, "message": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    identifier = data.get("identifier", "").strip()
    if not identifier:
        return jsonify({"error": "Username or email is required"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    generic_message = (
        "If an account exists with that identifier, "
        "a sign-in link has been sent. Please check your email."
    )

    # Look up by username or email
    user = user_repo.get_by_username(identifier)
    if user is None:
        user = user_repo.get_by_email(identifier)

    if user is None:
        return jsonify({"success": True, "message": generic_message})

    # Magic link users always get a link; others need recovery_email
    if user.auth_type != AuthType.MAGIC_LINK:
        if not user.recovery_enabled or not user.recovery_email:
            return jsonify({"success": True, "message": generic_message})

    email = user.recovery_email
    if not email:
        return jsonify({"success": True, "message": generic_message})

    # Create recovery token (reuse PendingRecovery infrastructure)
    recovery_repo = PendingRecoveryRepository(db)
    recovery_repo.delete_for_user(user.id)

    remember_me = data.get("remember_me", True)

    recovery, raw_token = PendingRecovery.create(db, user.id, expiry_minutes=15)

    r_flag = "1" if remember_me else "0"
    magic_link_url = f"/verify.html?token={raw_token}&r={r_flag}"

    _send_magic_link_email(
        to_email=email,
        username=user.username,
        magic_link=magic_link_url,
        expires_minutes=15,
    )

    return jsonify({"success": True, "message": generic_message})


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
        expires_minutes=15,
    )

    if email_sent:
        return jsonify({"success": True, "message": generic_message})
    else:
        # Email failed, but still return success for privacy
        # Log the error internally
        current_app.logger.error(f"Failed to send magic link email to user {user.id}")
        return jsonify({"success": True, "message": generic_message})


@auth_bp.route("/magic-link/verify", methods=["POST"])
def verify_magic_link():
    """
    Verify a magic link token and create a session.

    Handles both login recovery and first-time activation.
    When activate=true and user has never logged in (last_login IS NULL),
    this is treated as an account activation.

    Request body:
        {
            "token": "verification_token",
            "activate": true  (optional, for first-time activation)
        }

    Returns:
        200: {"success": true, "message": "...", "activation": bool}
        400: {"error": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    token = data.get("token", "").strip()
    if not token:
        return jsonify({"error": "Token is required"}), 400

    is_activation = data.get("activate", False)

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

    # Detect first-time activation
    first_login = is_activation and user.last_login is None

    # Create a persistent session (magic link users get persistent by default)
    user_agent = request.headers.get("User-Agent", "")
    ip_address = request.remote_addr or ""

    remember_me = data.get("remember_me", True)
    allow_multi = _user_allows_multi_session(user, db)
    session, raw_token = Session.create_for_user(
        db,
        user.id,
        user_agent,
        ip_address,
        remember_me=remember_me,
        allow_multi=allow_multi,
    )

    # Update last login
    user.last_login = datetime.now()
    user.save(db)

    # Set session cookie and include token for client-side persistence
    message = "Welcome to The Library!" if first_login else "Login successful"
    response = jsonify(
        {
            "success": True,
            "message": message,
            "username": user.username,
            "activation": first_login,
            "session_token": raw_token,
        }
    )

    return set_session_cookie(response, raw_token, remember_me=remember_me)


def _get_base_url() -> str:
    """Get the base URL for email links, auto-detecting from request if not
    configured."""
    configured = os.environ.get("BASE_URL", "")
    if configured:
        return configured.rstrip("/")
    # Auto-detect from current request
    return request.host_url.rstrip("/")


def _get_email_config() -> tuple:
    """Get SMTP configuration from environment."""
    return (
        os.environ.get("SMTP_HOST", "localhost"),
        int(os.environ.get("SMTP_PORT", "25")),
        os.environ.get("SMTP_USER", ""),
        os.environ.get("SMTP_PASS", ""),
        os.environ.get("SMTP_FROM", "noreply@localhost"),
    )


def _send_magic_link_email(
    to_email: str,
    username: str,
    magic_link: str,
    expires_minutes: int,
    locale: str = "en",
) -> bool:
    """
    Send a magic link email for login recovery.

    Returns True if email was sent successfully, False otherwise.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    full_link = f"{base_url}{magic_link}"

    subject, text_content, html_content = render_email(
        "magic_link",
        locale,
        username=username,
        link=full_link,
        expires_minutes=expires_minutes,
    )

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


def _send_approval_email(to_email: str, username: str, locale: str = "en") -> bool:
    """
    Send an email notifying the user their access request was approved.

    Includes step-by-step instructions for setting up their authenticator
    and claiming their credentials.

    Returns True if email was sent successfully, False otherwise.
    """
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    claim_url = f"{base_url}/claim.html?username={urllib.parse.quote(username)}"

    subject, text_content, html_content = render_email(
        "approval", locale, username=username, claim_url=claim_url
    )

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


def _send_denial_email(
    to_email: str,
    username: str,
    reason: Optional[str] = None,
    locale: str = "en",
) -> bool:
    """
    Send an email notifying the user their access request was denied.

    Returns True if email was sent successfully, False otherwise.
    """
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()

    reason_text = reason if reason else "No specific reason was provided."

    subject, text_content, html_content = render_email(
        "denial", locale, username=username, reason=reason_text
    )

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
    assert user is not None  # guaranteed by @login_required
    assert user.id is not None  # persisted user always has id
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
        return jsonify(
            {
                "status": "ok",
                "auth_db": status["can_connect"],
                "schema_version": status["schema_version"],
                "user_count": status["user_count"],
            }
        )
    except Exception as e:
        logger.error("Auth database health check failed: %s", e)
        return (
            jsonify(
                {
                    "status": "error",
                    "auth_db": False,
                    "error": "Auth database health check failed",
                }
            ),
            500,
        )


# =============================================================================
# Auth Status (Public)
# =============================================================================


@auth_bp.route("/status", methods=["GET"])
def auth_status():
    """
    Public endpoint: returns auth state for frontend.

    No authentication required. Returns whether auth is enabled,
    the current user (if logged in), and whether the caller is a guest.
    """
    auth_enabled = current_app.config.get("AUTH_ENABLED", False)
    user = None
    if auth_enabled:
        user = get_current_user()

    user_dict = None
    if user:
        user_dict = {
            "id": user.id,
            "username": user.username,
            "is_admin": user.is_admin,
            "can_download": user.can_download,
            "auth_type": user.auth_type.value,
        }

    return jsonify(
        {
            "auth_enabled": auth_enabled,
            "user": user_dict,
            "guest": auth_enabled and user is None,
        }
    )


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
        reply_email=reply_email,
    )
    inbox_msg.save(db)

    # Send admin alert email
    _send_admin_alert(user.username, message[:100])

    return jsonify(
        {
            "success": True,
            "message_id": inbox_msg.id,
            "info": "Your message has been sent to the admin.",
        }
    )


def _send_admin_alert(username: str, message_preview: str) -> bool:
    """Send email alert to admin about new contact message."""
    smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from = _get_email_config()
    admin_email = os.environ.get("ADMIN_EMAIL", smtp_from)

    if not smtp_user:
        # SMTP not configured, skip alert
        return False

    subject = f"New message from {username} - The Library"
    body = f"""You have a new message from {username} in The Library inbox.

Preview: {message_preview}{"..." if len(message_preview) >= 100 else ""}

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

    return jsonify(
        {
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
        }
    )


@auth_bp.route("/admin/notifications", methods=["POST"])
@admin_required
def create_notification():
    """
    Create a new notification (admin only).

    Request body:
        {
            "message": str,           # Required
            "type": str,              # Optional: "info", "maintenance",
                                  # "outage", "personal"
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

    return jsonify({"success": True, "notification_id": notification.id})


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
        result.append(
            {
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
            }
        )

    return jsonify({"messages": result, "unread_count": unread_count})


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

    return jsonify(
        {
            "message": {
                "id": message.id,
                "from_user_id": message.from_user_id,
                "from_username": user.username if user else "[deleted]",
                "message": message.message,
                "reply_via": message.reply_via.value,
                "reply_email": message.reply_email,
                "status": message.status.value,
                "created_at": (
                    message.created_at.isoformat() if message.created_at else None
                ),
                "read_at": message.read_at.isoformat() if message.read_at else None,
                "replied_at": (
                    message.replied_at.isoformat() if message.replied_at else None
                ),
            }
        }
    )


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
        assert admin_user is not None  # guaranteed by @admin_required
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

    return jsonify({"success": True, "reply_method": reply_method})


def _send_reply_email(
    to_email: str, username: str, reply_text: str, locale: str = "en"
) -> bool:
    """Send email reply to user."""
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, smtp_from = _get_email_config()

    subject, body, html_content = render_email(
        "reply", locale, username=username, reply_text=reply_text
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to_email

        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_content, "html"))

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

    return jsonify(
        {
            "requests": [r.to_dict() for r in requests],
            "pending_count": request_repo.count_pending(),
        }
    )


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
            to_email=access_req.contact_email, username=access_req.username
        )

    return jsonify(
        {
            "success": True,
            "username": access_req.username,
            "email_sent": email_sent,
            "message": (
                f"Access request for '{access_req.username}' approved. "
                + (
                    "Email notification sent."
                    if email_sent
                    else "User can claim credentials with their token."
                )
            ),
        }
    )


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
            reason=reason,
        )

    return jsonify(
        {
            "success": True,
            "email_sent": email_sent,
            "message": f"Access request for '{access_req.username}' denied.",
        }
    )


@auth_bp.route("/admin/users/create", methods=["POST"])
@admin_required
def create_user():
    """
    Create a new user directly (admin only).

    JSON body:
        username: Username (3-24 chars, alphanumeric + hyphens)
        email: Optional email (required for magic_link)
        auth_method: "totp", "magic_link", or "passkey"
        is_admin: Boolean
        can_download: Boolean

    Returns:
        201: {"success": true, "user_id": int, "setup_data": {...}}
        400: {"error": "..."}
        409: {"error": "Username already taken"}
    """
    from auth.audit import AuditLogRepository

    data = request.get_json() or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip() if data.get("email") else ""
    auth_method = data.get("auth_method", "").strip()
    is_admin = bool(data.get("is_admin", False))
    can_download = bool(data.get("can_download", True))

    if auth_method not in ("totp", "magic_link", "passkey"):
        return jsonify(
            {"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}
        ), 400

    err = _validate_username_strict(username)
    if err:
        return jsonify(err[0]), err[1]

    if auth_method == "magic_link" and not email:
        return jsonify({"error": "Email is required for magic_link auth method"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)
    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 409

    new_user, setup_data = _create_user_by_method(
        db, username, email, auth_method, is_admin, can_download
    )

    admin_user = get_current_user()
    AuditLogRepository(db).log(
        actor_id=admin_user.id,
        target_id=new_user.id,
        action="create_user",
        details={
            "auth_method": auth_method,
            "is_admin": is_admin,
            "can_download": can_download,
            "actor_username": admin_user.username,
            "target_username": username,
        },
    )

    return jsonify(
        {"success": True, "user_id": new_user.id, "setup_data": setup_data}
    ), 201


def _create_user_by_method(db, username, email, auth_method, is_admin, can_download):
    """Create a user with the specified auth method. Returns (user, setup_data)."""
    if auth_method == "totp":
        secret_bytes, _, _, setup_data = _setup_totp_data(username)
        new_user = User(
            username=username,
            auth_type=AuthType.TOTP,
            auth_credential=secret_bytes,
            is_admin=is_admin,
            can_download=can_download,
        )
        if email:
            new_user.recovery_email = email
        new_user.save(db)
        return new_user, setup_data

    if auth_method == "magic_link":
        new_user = User(
            username=username,
            auth_type=AuthType.MAGIC_LINK,
            auth_credential=b"",
            is_admin=is_admin,
            can_download=can_download,
            recovery_email=email,
        )
        new_user.save(db)
        return new_user, {}

    # passkey
    new_user = User(
        username=username,
        auth_type=AuthType.PASSKEY,
        auth_credential=b"pending",
        is_admin=is_admin,
        can_download=can_download,
    )
    if email:
        new_user.recovery_email = email
    new_user.save(db)
    setup_data = _setup_passkey_data(db, username)
    return new_user, setup_data


def _user_to_entry(u: User) -> dict:
    """Convert a User object to a JSON-serializable dict."""
    return {
        "id": u.id,
        "username": u.username,
        "email": u.recovery_email,
        "auth_type": u.auth_type.value,
        "can_download": u.can_download,
        "is_admin": u.is_admin,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login": u.last_login.isoformat() if u.last_login else None,
    }


def _add_invite_expiry(
    entry: dict, u: User, db: AuthDatabase, request_repo: AccessRequestRepository
) -> None:
    """Add invitation expiry fields to a user entry if they haven't logged in."""
    if u.last_login:
        return

    if u.auth_type == AuthType.MAGIC_LINK:
        with db.connection() as conn:
            cursor = conn.execute(
                "SELECT expires_at, used_at FROM pending_recovery "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (u.id,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                expires_at = datetime.fromisoformat(row[0])
                entry["invite_expires_at"] = expires_at.isoformat()
                entry["invite_expired"] = datetime.now() > expires_at
    else:
        ar = request_repo.get_by_username(u.username)
        if ar and ar.claim_expires_at:
            entry["invite_expires_at"] = ar.claim_expires_at.isoformat()
            entry["invite_expired"] = ar.is_claim_expired()


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
    request_repo = AccessRequestRepository(db)

    all_users = user_repo.list_all()
    total = len(all_users)
    users = all_users[:limit]

    user_list = []
    for u in users:
        entry = _user_to_entry(u)
        _add_invite_expiry(entry, u, db, request_repo)
        user_list.append(entry)

    return jsonify({"users": user_list, "total": total})


@auth_bp.route("/admin/users/invite", methods=["POST"])
@admin_required
def invite_user():
    """
    Invite a new user with pre-approval (admin only).

    Supports two auth methods:
    - "totp" (default): Creates access request with claim token, sends claim email.
    - "magic_link": Creates user account directly, sends activation email.

    JSON body:
        username: Username (3-24 chars, printable ASCII)
        email: Email address to send invitation to (required)
        can_download: Optional download permission (default: true)
        auth_method: Optional auth method - "totp" (default) or "magic_link"

    Returns:
        200: {"success": true, "user": {...}, "email_sent": bool, ...}
        400: {"error": "..."}
        409: {"error": "Username already taken"}
    """
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    can_download = data.get("can_download", True)
    auth_method = data.get("auth_method", "totp").strip()

    if auth_method not in ("totp", "magic_link", "passkey"):
        return jsonify(
            {"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}
        ), 400

    err = _validate_username(username)
    if err:
        return jsonify(err[0]), err[1]

    if not email:
        return jsonify({"error": "Email is required for invitations"}), 400
    err = _validate_email_format(email)
    if err:
        return jsonify(err[0]), err[1]

    db = get_auth_db()
    user_repo = UserRepository(db)
    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 409

    if auth_method == "magic_link":
        return _invite_magic_link_user(db, user_repo, username, email, can_download)

    return _invite_claim_flow(db, username, email, can_download)


def _invite_claim_flow(db, username, email, can_download):
    """Create access request with claim token and send invitation email."""
    request_repo = AccessRequestRepository(db)

    existing = request_repo.get_by_username(username)
    if existing:
        request_repo.delete(existing.id)

    admin_user = get_current_user()
    admin_username = admin_user.username if admin_user else "system"

    raw_claim_token, _ = generate_verification_token()
    truncated_token, formatted_token = _format_claim_token(raw_claim_token)
    claim_token_hash = hash_token(truncated_token)

    claim_expires_at = datetime.now() + timedelta(hours=INVITATION_EXPIRY_HOURS)
    access_request = request_repo.create(
        username, claim_token_hash, email, claim_expires_at
    )
    request_repo.store_invite_metadata(access_request.id, can_download)
    request_repo.approve(access_request.id, admin_username)

    email_sent = _send_invitation_email(
        to_email=email, username=username, claim_token=formatted_token
    )

    return jsonify(
        {
            "success": True,
            "user": {
                "username": username,
                "email": email,
                "can_download": can_download,
                "is_admin": False,
            },
            "email_sent": email_sent,
            "claim_token": formatted_token,
            "message": (
                f"Invitation for '{username}' sent. "
                + (
                    "Email delivered."
                    if email_sent
                    else "Email failed - share claim token manually."
                )
            ),
        }
    )


def _invite_magic_link_user(db, user_repo, username, email, can_download):
    """
    Create a magic_link user account directly and send activation email.

    No claim step needed — user clicks the link and they're in.
    """
    # Create user account with magic_link auth type
    user = User(
        username=username,
        auth_type=AuthType.MAGIC_LINK,
        auth_credential=b"",  # No credential needed for magic link
        can_download=can_download,
        is_admin=False,
        recovery_email=email,
        recovery_enabled=True,
    )
    user.save(db)

    # Generate activation token (48h expiry for invitations)
    recovery, raw_token = PendingRecovery.create(
        db,
        user.id,
        expiry_minutes=60 * INVITATION_EXPIRY_HOURS,
    )

    # Send activation email
    email_sent = _send_activation_email(
        to_email=email,
        username=username,
        activation_token=raw_token,
    )

    return jsonify(
        {
            "success": True,
            "user": {
                "username": username,
                "email": email,
                "can_download": can_download,
                "is_admin": False,
                "auth_type": "magic_link",
            },
            "email_sent": email_sent,
            "message": (
                f"Magic link invitation for '{username}' sent. "
                + (
                    "Activation email delivered."
                    if email_sent
                    else "Email failed - admin can resend from user management."
                )
            ),
        }
    )


def _send_invitation_email(
    to_email: str, username: str, claim_token: str, locale: str = "en"
) -> bool:
    """
    Send an invitation email to a pre-approved user with their claim token.

    Returns True if email was sent successfully, False otherwise.
    """
    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    claim_url = (
        f"{base_url}/claim.html"
        f"?username={urllib.parse.quote(username)}"
        f"&token={urllib.parse.quote(claim_token)}"
    )

    subject, text_content, html_content = render_email(
        "invitation",
        locale,
        username=username,
        claim_url=claim_url,
        claim_token=claim_token,
        expires_hours=INVITATION_EXPIRY_HOURS,
    )

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


def _send_activation_email(
    to_email: str,
    username: str,
    activation_token: str,
    locale: str = "en",
) -> bool:
    """
    Send an activation email for magic link invitations.

    Much simpler than the TOTP claim email — just one button to click.
    Returns True if email was sent successfully, False otherwise.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    from backend.api_modular.email_templates import render_email

    smtp_host, smtp_port, smtp_user, smtp_pass, from_email = _get_email_config()
    base_url = _get_base_url()

    activation_url = f"{base_url}/verify.html?token={activation_token}&activate=1"

    subject, text_content, html_content = render_email(
        "activation",
        locale,
        username=username,
        activation_url=activation_url,
        expires_hours=INVITATION_EXPIRY_HOURS,
    )

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
        current_app.logger.error(f"Failed to send activation email: {type(e).__name__}")
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
    assert current_user is not None  # guaranteed by @admin_required
    if current_user.id == user_id and target_user.is_admin:
        return jsonify({"error": "Cannot demote yourself"}), 400

    # Toggle admin status
    new_admin_status = not target_user.is_admin
    user_repo.set_admin(user_id, new_admin_status)

    # Audit log
    from auth.audit import AuditLogRepository

    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=current_user.id,
        target_id=user_id,
        action="toggle_roles",
        details={
            "actor_username": current_user.username,
            "target_username": target_user.username,
            "field": "is_admin",
            "old": target_user.is_admin,
            "new": new_admin_status,
        },
    )

    return jsonify(
        {
            "success": True,
            "username": target_user.username,
            "is_admin": new_admin_status,
        }
    )


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

    # Audit log
    from auth.audit import AuditLogRepository

    current_user = get_current_user()
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=current_user.id if current_user else None,
        target_id=user_id,
        action="change_download",
        details={
            "actor_username": current_user.username if current_user else "system",
            "target_username": target_user.username,
            "old": target_user.can_download,
            "new": new_download_status,
        },
    )

    return jsonify(
        {
            "success": True,
            "username": target_user.username,
            "can_download": new_download_status,
        }
    )


def _apply_user_profile_updates(
    user_repo: "UserRepository",
    user_id: int,
    data: dict,
) -> tuple[dict, int] | None:
    """Apply username and/or email updates with validation. Returns error or None."""
    new_username = data.get("username")
    if new_username is not None:
        err = _validate_username(new_username)
        if err:
            return err
        if not user_repo.update_username(user_id, new_username):
            return {"error": "Username already taken"}, 409

    if "email" in data:
        new_email = data.get("email")
        if new_email is not None and new_email != "":
            err = _validate_email_format(new_email)
            if err:
                return err
        else:
            new_email = None
        user_repo.update_email(user_id, new_email)
    return None


@auth_bp.route("/admin/users/<int:user_id>", methods=["PUT"])
@admin_required
def update_user(user_id: int):
    """
    Update a user's profile (admin only).

    JSON body:
        username: New username (optional, 3-24 chars, ASCII printable except <>\\)
        email: New email (optional, or null to remove)

    Returns:
        200: {"success": true, "user": {...}}
        400: {"error": "..."}
        404: {"error": "User not found"}
        409: {"error": "Username already taken"}
    """
    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json() or {}

    err = _apply_user_profile_updates(user_repo, user_id, data)
    if err:
        return jsonify(err[0]), err[1]

    updated_user = user_repo.get_by_id(user_id)
    assert updated_user is not None
    return jsonify({"success": True, "user": _user_dict(updated_user)})


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

    # Last-admin guard
    if target_user.is_admin and user_repo.is_last_admin(user_id):
        return jsonify({"error": "Cannot delete last admin"}), 409

    # Delete user (cascades to sessions, positions, etc.)
    user_repo.delete(user_id)

    # Clean up any associated access request
    request_repo = AccessRequestRepository(db)
    request_repo.delete_for_username(target_user.username)

    # Audit log
    from auth.audit import AuditLogRepository, notify_admins

    audit_repo = AuditLogRepository(db)
    details = {
        "actor_username": current_user.username if current_user else "system",
        "username": target_user.username,
    }
    audit_repo.log(
        actor_id=current_user.id if current_user else None,
        target_id=None,
        action="delete_account",
        details=details,
    )
    notify_admins("delete_account", details, db)

    return jsonify(
        {
            "success": True,
            "message": f"User '{target_user.username}' deleted.",
        }
    )


# ============================================================
# Granular Admin User Management Endpoints (with audit logging)
# ============================================================


@auth_bp.route("/admin/users/<int:user_id>/username", methods=["PUT"])
@admin_required
def admin_change_username(user_id: int):
    """
    Change a user's username (admin only).

    JSON body: {"username": "newname"}
    Returns 200 with updated user, or 409 if duplicate.
    """
    from auth.audit import AuditLogRepository, notify_admins

    data = request.get_json() or {}
    new_username = data.get("username", "").strip() if data.get("username") else ""

    err = _validate_username(new_username)
    if err:
        return jsonify(err[0]), err[1]

    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    old_username = target_user.username
    if not user_repo.update_username(user_id, new_username):
        return jsonify({"error": "Username already taken"}), 409

    admin_user = get_current_user()
    assert admin_user is not None
    details = {
        "old": old_username,
        "new": new_username,
        "actor_username": admin_user.username,
        "target_username": new_username,
    }
    AuditLogRepository(db).log(
        actor_id=admin_user.id,
        target_id=user_id,
        action="change_username",
        details=details,
    )
    notify_admins("change_username", details, db)

    updated = user_repo.get_by_id(user_id)
    assert updated is not None
    return jsonify({"success": True, "user": _user_dict(updated)})


@auth_bp.route("/admin/users/<int:user_id>/email", methods=["PUT"])
@admin_required
def admin_change_email(user_id: int):
    """
    Change a user's email (admin only).

    JSON body: {"email": "new@example.com"} (empty string clears)
    Returns 200 with updated user.
    """
    import re

    from auth.audit import AuditLogRepository

    data = request.get_json() or {}
    new_email = data.get("email", "").strip() if data.get("email") else ""

    # Validate email format if non-empty
    if new_email:
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, new_email):
            return jsonify({"error": "Invalid email format"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    old_email = target_user.recovery_email
    email_val = new_email if new_email else None
    user_repo.update_email(user_id, email_val)

    # Audit log
    admin_user = get_current_user()
    assert admin_user is not None  # guaranteed by @admin_required
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=admin_user.id,
        target_id=user_id,
        action="change_email",
        details={
            "old": old_email,
            "new": email_val,
            "actor_username": admin_user.username,
            "target_username": target_user.username,
        },
    )

    updated = user_repo.get_by_id(user_id)
    assert updated is not None  # just updated this user
    return jsonify(
        {
            "success": True,
            "user": {
                "id": updated.id,
                "username": updated.username,
                "email": updated.recovery_email,
                "is_admin": updated.is_admin,
                "can_download": updated.can_download,
            },
        }
    )


def _apply_role_changes(
    user_repo: "UserRepository",
    user_id: int,
    data: dict,
) -> tuple[dict, int] | None:
    """Apply is_admin/can_download changes with last-admin guard. Returns error or None."""
    if "is_admin" in data:
        if not data["is_admin"] and user_repo.is_last_admin(user_id):
            return {"error": "Cannot remove last admin"}, 409
        user_repo.set_admin(user_id, bool(data["is_admin"]))
    if "can_download" in data:
        user_repo.set_download_permission(user_id, bool(data["can_download"]))
    if "multi_session" in data:
        value = data["multi_session"]
        if value not in ("default", "yes", "no"):
            return {"error": "multi_session must be 'default', 'yes', or 'no'"}, 400
        user_repo.set_multi_session(user_id, value)
    return None


@auth_bp.route("/admin/users/<int:user_id>/roles", methods=["PUT"])
@admin_required
def admin_change_roles(user_id: int):
    """
    Change a user's roles (admin only).

    JSON body: {"is_admin": bool, "can_download": bool} (either or both)
    Returns 200 with updated user, or 409 if last-admin guard triggers.
    """
    from auth.audit import AuditLogRepository

    data = request.get_json() or {}

    if (
        "is_admin" not in data
        and "can_download" not in data
        and "multi_session" not in data
    ):
        return jsonify(
            {"error": "Provide is_admin, can_download, and/or multi_session"}
        ), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    old_roles = {
        "is_admin": target_user.is_admin,
        "can_download": target_user.can_download,
    }

    err = _apply_role_changes(user_repo, user_id, data)
    if err:
        return jsonify(err[0]), err[1]

    admin_user = get_current_user()
    assert admin_user is not None
    updated = user_repo.get_by_id(user_id)
    assert updated is not None
    new_roles = {"is_admin": updated.is_admin, "can_download": updated.can_download}
    AuditLogRepository(db).log(
        actor_id=admin_user.id,
        target_id=user_id,
        action="toggle_roles",
        details={
            "old": old_roles,
            "new": new_roles,
            "actor_username": admin_user.username,
            "target_username": target_user.username,
        },
    )

    return jsonify({"success": True, "user": _user_dict(updated)})


@auth_bp.route("/admin/settings", methods=["GET"])
@admin_required
def get_admin_settings():
    """Get all system settings (admin only)."""
    db = get_auth_db()
    repo = SystemSettingsRepository(db)
    return jsonify(repo.get_all())


@auth_bp.route("/admin/settings", methods=["PATCH"])
@admin_required
def update_admin_settings():
    """Update one or more system settings (admin only).

    JSON body: {"setting_key": "value", ...}
    Only known setting keys are accepted.
    """
    ALLOWED_KEYS = {"multi_session_default"}

    data = request.get_json() or {}
    updates = {k: v for k, v in data.items() if k in ALLOWED_KEYS}
    if not updates:
        return jsonify({"error": "No valid settings provided"}), 400

    db = get_auth_db()
    repo = SystemSettingsRepository(db)
    for key, value in updates.items():
        repo.set(key, str(value))

    return jsonify({"success": True, "updated": updates})


@auth_bp.route("/admin/users/<int:user_id>/auth-method", methods=["PUT"])
@admin_required
def admin_change_auth_method(user_id: int):
    """
    Switch a user's authentication method (admin only).

    JSON body: {"auth_method": "totp"|"magic_link"|"passkey", "email": "..."}
    Returns 200 with setup_data.
    """
    from auth.audit import AuditLogRepository, notify_admins

    data = request.get_json() or {}
    auth_method = data.get("auth_method", "").strip()

    if auth_method not in ("totp", "magic_link", "passkey"):
        return jsonify(
            {"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}
        ), 400

    db = get_auth_db()
    target_user = UserRepository(db).get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    old_method = target_user.auth_type.value
    setup_data, err = _switch_auth_method(target_user, db, auth_method, data)
    if err:
        return err

    admin_user = get_current_user()
    assert admin_user is not None
    details = {
        "old": old_method,
        "new": auth_method,
        "actor_username": admin_user.username,
        "target_username": target_user.username,
    }
    AuditLogRepository(db).log(
        actor_id=admin_user.id,
        target_id=user_id,
        action="switch_auth_method",
        details=details,
    )
    notify_admins("switch_auth_method", details, db)

    return jsonify({"success": True, "setup_data": setup_data})


@auth_bp.route("/admin/users/<int:user_id>/reset-credentials", methods=["POST"])
@admin_required
def admin_reset_credentials(user_id: int):
    """
    Reset credentials for a user's current auth method (admin only).

    TOTP: new secret + QR/key
    Passkey: new claim token
    Magic Link: no-op (confirms email)
    """
    from auth.audit import AuditLogRepository, notify_admins
    from auth.models import PendingRegistration

    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    admin_user = get_current_user()
    assert admin_user is not None  # guaranteed by @admin_required
    setup_data: dict[str, str | None] = {}

    if target_user.auth_type == AuthType.TOTP:
        secret_bytes, base32_secret, provisioning_uri = setup_totp(target_user.username)
        target_user.auth_credential = secret_bytes
        target_user.save(db)
        qr_png = generate_qr_code(secret_bytes, target_user.username)
        qr_b64 = base64.b64encode(qr_png).decode("ascii")
        setup_data = {
            "secret": base32_secret,
            "qr_uri": provisioning_uri,
            "manual_key": base32_secret,
            "qr_base64": qr_b64,
        }

    elif target_user.auth_type in (AuthType.PASSKEY, AuthType.FIDO2):
        target_user.auth_credential = b"pending"
        target_user.save(db)

        pending_reg, raw_token = PendingRegistration.create(
            db, target_user.username, expiry_minutes=60 * INVITATION_EXPIRY_HOURS
        )
        truncated = raw_token[:16]
        formatted_token = "-".join(truncated[i : i + 4] for i in range(0, 16, 4))
        encoded_name = urllib.parse.quote(target_user.username)
        claim_url = f"/claim.html?username={encoded_name}&token={formatted_token}"
        setup_data = {
            "claim_token": formatted_token,
            "claim_url": claim_url,
            "expires_at": (
                pending_reg.expires_at.isoformat() if pending_reg.expires_at else None
            ),
        }

    elif target_user.auth_type == AuthType.MAGIC_LINK:
        setup_data = {"email": target_user.recovery_email or ""}

    # Audit log
    details = {
        "auth_method": target_user.auth_type.value,
        "actor_username": admin_user.username,
        "target_username": target_user.username,
    }
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=admin_user.id,
        target_id=user_id,
        action="reset_credentials",
        details=details,
    )
    notify_admins("reset_credentials", details, db)

    return jsonify({"success": True, "setup_data": setup_data})


@auth_bp.route("/admin/users/<int:user_id>/delete", methods=["DELETE"])
@admin_required
def admin_delete_user_v2(user_id: int):
    """
    Delete a user with audit logging (admin only).

    Checks last-admin guard. Logs audit BEFORE deletion.
    """
    from auth.audit import AuditLogRepository, notify_admins

    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    # Prevent self-deletion
    admin_user = get_current_user()
    assert admin_user is not None  # guaranteed by @admin_required
    if admin_user.id == user_id:
        return jsonify({"error": "Cannot delete yourself"}), 400

    # Last-admin guard
    if target_user.is_admin and user_repo.is_last_admin(user_id):
        return jsonify({"error": "Cannot delete last admin"}), 409

    # Audit BEFORE deletion (capture username)
    details = {
        "username": target_user.username,
        "actor_username": admin_user.username,
        "target_username": target_user.username,
    }
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=admin_user.id,
        target_id=user_id,
        action="delete_account",
        details=details,
    )
    notify_admins("delete_account", details, db)

    # Delete user (cascades to sessions, positions, etc.)
    user_repo.delete(user_id)

    # Clean up any associated access request
    request_repo = AccessRequestRepository(db)
    request_repo.delete_for_username(target_user.username)

    return jsonify(
        {"success": True, "message": f"User '{target_user.username}' deleted."}
    )


@auth_bp.route("/admin/audit-log", methods=["GET"])
@admin_required
def admin_audit_log():
    """
    List audit log entries (admin only).

    Query params: limit, offset, action, user_id
    """
    from auth.audit import AuditLogRepository

    limit = min(int(request.args.get("limit", 50)), 500)
    offset = int(request.args.get("offset", 0))
    action_filter = request.args.get("action")
    user_filter = request.args.get("user_id", type=int)

    db = get_auth_db()
    audit_repo = AuditLogRepository(db)

    entries = audit_repo.list(
        limit=limit,
        offset=offset,
        action_filter=action_filter,
        user_filter=user_filter,
    )
    total = audit_repo.count(
        action_filter=action_filter,
        user_filter=user_filter,
    )

    return jsonify(
        {
            "entries": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp,
                    "actor_id": e.actor_id,
                    "target_id": e.target_id,
                    "action": e.action,
                    "details": (
                        json.loads(e.details)
                        if isinstance(e.details, str)
                        else e.details
                    ),
                }
                for e in entries
            ],
            "total": total,
        }
    )


def _totp_setup_data(user: User) -> dict[str, str | None]:
    """Build TOTP setup data (QR code, secret) for a user."""
    if not user.auth_credential:
        return {}
    b32 = secret_to_base32(user.auth_credential)
    qr_uri = get_provisioning_uri(user.auth_credential, user.username)
    qr_png = generate_qr_code(user.auth_credential, user.username)
    qr_b64 = base64.b64encode(qr_png).decode("ascii")
    return {"secret": b32, "qr_uri": qr_uri, "manual_key": b32, "qr_base64": qr_b64}


def _passkey_setup_data(db: AuthDatabase, username: str) -> dict[str, str | None]:
    """Build passkey/FIDO2 setup data from pending registrations."""
    with db.connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM pending_registrations WHERE username = ? "
            "ORDER BY id DESC LIMIT 1",
            (username,),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        from auth.models import PendingRegistration as PR

        pending = PR.from_row(row)
        return {
            "claim_token": "pending",
            "expires_at": pending.expires_at.isoformat()
            if pending.expires_at
            else None,
        }


@auth_bp.route("/admin/users/<int:user_id>/setup-info", methods=["GET"])
@admin_required
def admin_setup_info(user_id: int):
    """
    Get setup data for a user who hasn't logged in yet (admin only).

    Returns 404 if user has already logged in or doesn't exist.
    """
    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    if target_user.last_login is not None:
        return jsonify({"error": "User has already logged in"}), 404

    if target_user.auth_type == AuthType.TOTP:
        setup_data = _totp_setup_data(target_user)
    elif target_user.auth_type in (AuthType.PASSKEY, AuthType.FIDO2):
        setup_data = _passkey_setup_data(db, target_user.username)
    elif target_user.auth_type == AuthType.MAGIC_LINK:
        setup_data = {"email": target_user.recovery_email or ""}
    else:
        setup_data = {}

    return jsonify({"setup_data": setup_data})


# ---------------------------------------------------------------------------
# Self-service account endpoints (/auth/account/*)
# ---------------------------------------------------------------------------


@auth_bp.route("/account", methods=["GET"])
@login_required
def account_get():
    """
    Get the authenticated user's own profile.

    Returns 200 with profile fields.
    """
    user = get_current_user()
    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "email": user.recovery_email,
            "auth_type": user.auth_type.value,
            "can_download": user.can_download,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }
    )


@auth_bp.route("/account/username", methods=["PUT"])
@login_required
def account_change_username():
    """
    Change the authenticated user's own username.

    JSON body: {"username": "newname"}
    Returns 200 with new username, 400 if empty, 409 if duplicate.
    """
    from auth.audit import AuditLogRepository, notify_admins

    data = request.get_json() or {}
    new_username = data.get("username", "").strip() if data.get("username") else ""

    err = _validate_username(new_username)
    if err:
        return jsonify(err[0]), err[1]

    user = get_current_user()
    db = get_auth_db()
    old_username = user.username

    if not UserRepository(db).update_username(user.id, new_username):
        return jsonify({"error": "Username already taken"}), 409

    details = {
        "old": old_username,
        "new": new_username,
        "actor_username": old_username,
        "target_username": new_username,
    }
    AuditLogRepository(db).log(
        actor_id=user.id,
        target_id=user.id,
        action="change_username",
        details=details,
    )
    notify_admins("change_username", details, db)

    return jsonify({"success": True, "username": new_username})


@auth_bp.route("/account/email", methods=["PUT"])
@login_required
def account_change_email():
    """
    Change the authenticated user's own email (or clear it).

    JSON body: {"email": "new@example.com"} (empty string clears)
    Returns 200.
    """
    import re

    from auth.audit import AuditLogRepository

    data = request.get_json() or {}
    new_email = data.get("email", "").strip() if data.get("email") else ""

    # Validate email format if non-empty
    if new_email:
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, new_email):
            return jsonify({"error": "Invalid email format"}), 400

    user = get_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    old_email = user.recovery_email
    email_val = new_email if new_email else None
    user_repo.update_email(user.id, email_val)

    # Audit log (no notify_admins for self-service email change per spec)
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=user.id,
        target_id=user.id,
        action="change_email",
        details={
            "old": old_email,
            "new": email_val,
            "actor_username": user.username,
            "target_username": user.username,
        },
    )

    return jsonify({"success": True})


@auth_bp.route("/account/auth-method", methods=["PUT"])
@login_required
def account_switch_auth_method():
    """
    Switch the authenticated user's own authentication method.

    JSON body: {"auth_method": "totp"|"magic_link"|"passkey", "email": "..."}
    Returns 200 with setup_data.
    """
    from auth.audit import AuditLogRepository, notify_admins

    data = request.get_json() or {}
    auth_method = data.get("auth_method", "").strip()

    if auth_method not in ("totp", "magic_link", "passkey"):
        return jsonify(
            {"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}
        ), 400

    user = get_current_user()
    db = get_auth_db()
    old_method = user.auth_type.value

    setup_data, err = _switch_auth_method(user, db, auth_method, data)
    if err:
        return err

    details = {
        "old": old_method,
        "new": auth_method,
        "actor_username": user.username,
        "target_username": user.username,
    }
    AuditLogRepository(db).log(
        actor_id=user.id,
        target_id=user.id,
        action="switch_auth_method",
        details=details,
    )
    notify_admins("switch_auth_method", details, db)

    return jsonify({"success": True, "setup_data": setup_data})


@auth_bp.route("/account/reset-credentials", methods=["POST"])
@login_required
def account_reset_credentials():
    """
    Reset credentials for the authenticated user's current auth method.

    TOTP: new secret + QR/key
    Passkey: new claim token
    Magic Link: confirms email
    Returns 200 with setup_data.
    """
    from auth.audit import AuditLogRepository, notify_admins
    from auth.models import PendingRegistration

    user = get_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    # Re-fetch to get current state
    current_user = user_repo.get_by_id(user.id)
    if not current_user:
        return jsonify({"error": "User not found"}), 404

    setup_data = {}

    if current_user.auth_type == AuthType.TOTP:
        secret_bytes, base32_secret, provisioning_uri = setup_totp(
            current_user.username
        )
        current_user.auth_credential = secret_bytes
        current_user.save(db)
        qr_png = generate_qr_code(secret_bytes, current_user.username)
        qr_b64 = base64.b64encode(qr_png).decode("ascii")
        setup_data = {
            "secret": base32_secret,
            "qr_uri": provisioning_uri,
            "manual_key": base32_secret,
            "qr_base64": qr_b64,
        }

    elif current_user.auth_type in (AuthType.PASSKEY, AuthType.FIDO2):
        current_user.auth_credential = b"pending"
        current_user.save(db)

        pending_reg, raw_token = PendingRegistration.create(
            db, current_user.username, expiry_minutes=60 * INVITATION_EXPIRY_HOURS
        )
        truncated = raw_token[:16]
        formatted_token = "-".join(truncated[i : i + 4] for i in range(0, 16, 4))
        encoded_name = urllib.parse.quote(current_user.username)
        claim_url = f"/claim.html?username={encoded_name}&token={formatted_token}"
        setup_data = {
            "claim_token": formatted_token,
            "claim_url": claim_url,
            "expires_at": (
                pending_reg.expires_at.isoformat() if pending_reg.expires_at else None
            ),
        }

    elif current_user.auth_type == AuthType.MAGIC_LINK:
        setup_data = {"email": current_user.recovery_email or ""}

    # Audit log
    details = {
        "auth_method": current_user.auth_type.value,
        "actor_username": current_user.username,
        "target_username": current_user.username,
    }
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=current_user.id,
        target_id=current_user.id,
        action="reset_credentials",
        details=details,
    )
    notify_admins("reset_credentials", details, db)

    return jsonify({"success": True, "setup_data": setup_data})


@auth_bp.route("/account", methods=["DELETE"])
@login_required
def account_delete():
    """
    Delete the authenticated user's own account.

    Checks last-admin guard (cannot delete if sole admin).
    Logs audit BEFORE deletion. Clears session cookie in response.
    Returns 200, or 409 if last admin.
    """
    from auth.audit import AuditLogRepository, notify_admins

    user = get_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    # Last-admin guard
    if user_repo.is_last_admin(user.id):
        return jsonify({"error": "Cannot delete last admin"}), 409

    # Audit BEFORE deletion (username is lost after delete)
    details = {
        "username": user.username,
        "actor_username": user.username,
        "target_username": user.username,
    }
    audit_repo = AuditLogRepository(db)
    audit_repo.log(
        actor_id=user.id,
        target_id=user.id,
        action="delete_account",
        details=details,
    )
    notify_admins("delete_account", details, db)

    # Delete user
    user_repo.delete(user.id)

    # Clear session cookie so browser logs out
    resp = make_response(jsonify({"success": True, "message": "Account deleted"}))
    resp.delete_cookie("audiobooks_session")
    return resp
