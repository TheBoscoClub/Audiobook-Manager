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
import sys
import urllib.parse
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Blueprint, Response, current_app, g, jsonify, redirect, request

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from auth import AccessRequestRepository  # noqa: F401  (re-export for tests)
from auth import BackupCodeRepository  # noqa: F401  (re-export for tests)
from auth import webauthn_authentication_options  # noqa: F401  (re-export for tests)
from auth import webauthn_verify_authentication  # noqa: F401  (re-export for tests)
from auth import (  # Re-exported for tests that patch.object(auth_mod, "...") or; @patch("api_modular.auth....") — submodules look these up; dynamically via _auth_module so the patch target lives on this module.
    AuthDatabase,
    AuthType,
    InboxMessage,
    NotificationRepository,
    ReplyMethod,
    Session,
    SessionRepository,
    User,
    UserRepository,
    webauthn_registration_options,
    webauthn_verify_registration,
)
from auth.models import SystemSettingsRepository
from auth.totp import base32_to_secret, generate_qr_code, setup_totp
from auth.totp import verify_code as verify_totp

# Email senders live in auth_email.py; re-exported here so that existing
# imports (`from backend.api_modular.auth import _send_admin_alert`) and
# @patch("backend.api_modular.auth._send_*_email") mocks keep working.
from .auth_email import (  # noqa: F401  (re-export)
    _get_base_url,
    _get_email_config,
    _send_activation_email,
    _send_admin_alert,
    _send_approval_email,
    _send_denial_email,
    _send_invitation_email,
    _send_magic_link_email,
    _send_reply_email,
)

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
        return {"error": "Username must contain only letters, numbers, and hyphens"}, 400
    return None


def _validate_email_format(email: str) -> tuple[dict, int] | None:
    """Validate email format. Returns (error_dict, status) or None if valid."""
    import re as _re

    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not _re.match(email_pattern, email):
        return {"error": "Invalid email format"}, 400
    return None


def _validate_webauthn_reg_input(token: str, data: dict, auth_type: str) -> tuple[dict, int] | None:
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


def _extract_recovery_fields(data: dict) -> tuple[str | None, str | None, bool]:
    """Extract recovery_email, recovery_phone, recovery_enabled from request data."""
    recovery_email = (data.get("recovery_email") or "").strip() or None
    recovery_phone = (data.get("recovery_phone") or "").strip() or None
    recovery_enabled = bool(recovery_email or recovery_phone)
    return recovery_email, recovery_phone, recovery_enabled


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
        "expires_at": (pending_reg.expires_at.isoformat() if pending_reg.expires_at else None),
    }


def _switch_auth_method(user, db, auth_method: str, data: dict) -> tuple[dict, tuple | None]:
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
            return {}, (jsonify({"error": "Email is required for magic_link auth method"}), 400)
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

    credential_json = json.dumps(credential) if isinstance(credential, dict) else credential

    webauthn_cred = webauthn_verify_registration(
        credential_json=credential_json,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        return None, None, (jsonify({"error": "WebAuthn verification failed"}), 400)

    return webauthn_cred, challenge, None


def init_auth_routes(auth_db_path: Path, auth_key_path: Path, is_dev: bool = False) -> None:
    """
    Initialize auth routes with dependencies.

    Args:
        auth_db_path: Path to encrypted auth database
        auth_key_path: Path to encryption key file
        is_dev: Development mode (relaxed security)
    """
    global _auth_db, _session_cookie_secure

    _auth_db = AuthDatabase(db_path=str(auth_db_path), key_path=str(auth_key_path), is_dev=is_dev)
    _auth_db.initialize()

    # In dev mode, allow non-secure cookies for localhost
    if is_dev:
        _session_cookie_secure = False

    # Log WebAuthn configuration at startup
    rp_id, rp_name, origin = get_webauthn_config()
    logger.info("WebAuthn config: rp_id=%s, origin=%s, rp_name=%s", rp_id, origin, rp_name)


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


def require_current_user() -> User:
    """Return the current authenticated user or raise assertion error.

    ONLY call this inside route handlers decorated with ``@login_required``
    or ``@admin_required`` — those decorators guarantee the user is not None
    before the handler body runs. The assert is a mypy type-narrowing aid
    with no runtime behavior change in decorated routes.

    Raises:
        AssertionError: if called outside a protected route (bug — use
        ``get_current_user()`` and handle None explicitly instead).
    """
    user = get_current_user()
    assert user is not None, "require_current_user() called without @login_required/@admin_required"
    return user


def require_current_user_id() -> int:
    """Return the current authenticated user's ID (narrowed to ``int``).

    A persisted User always has a non-None ``id``; since ``@login_required``
    guarantees the session resolves to a persisted user, the id is likewise
    guaranteed non-None inside a decorated route body. This helper makes that
    invariant visible to mypy so callers can pass the id to repository
    functions typed ``int``.

    Raises:
        AssertionError: if called outside a protected route, or if the
        resolved User somehow has ``id is None`` (would indicate a
        data-integrity bug in session / user persistence).
    """
    user = require_current_user()
    assert user.id is not None, "persisted User must have non-None id"
    return user.id


def require_current_session() -> Session:
    """Return the current authenticated session or raise assertion error.

    ONLY call this inside route handlers decorated with ``@login_required``
    or ``@admin_required``. See :func:`require_current_user` for rationale.
    """
    session = get_current_session()
    assert (
        session is not None
    ), "require_current_session() called without @login_required/@admin_required"
    return session


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
                return (jsonify({"error": "Access denied"}), 404)  # Return 404 to hide existence
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
    _, token = Session.create_for_user(
        db,
        user.ensured_id,
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
    user = require_current_user()
    session = require_current_session()

    # Get active notifications
    db = get_auth_db()
    notif_repo = NotificationRepository(db)
    notifications = notif_repo.get_active_for_user(user.ensured_id)

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
                "created_at": (session.created_at.isoformat() if session.created_at else None),
                "last_seen": (session.last_seen.isoformat() if session.last_seen else None),
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
    user = require_current_user()
    data = request.get_json() or {}
    db = get_auth_db()
    user_repo = UserRepository(db)

    new_username = data.get("username")
    if new_username is not None:
        err = _validate_username(new_username)
        if err:
            return jsonify(err[0]), err[1]
        if not user_repo.update_username(user.ensured_id, new_username):
            return jsonify({"error": "Username already taken"}), 409

    if "email" in data:
        new_email = data.get("email")
        if new_email is not None and new_email != "":
            err = _validate_email_format(new_email)
            if err:
                return jsonify(err[0]), err[1]
        else:
            new_email = None
        user_repo.update_email(user.ensured_id, new_email)

    updated_user = user_repo.get_by_id(user.ensured_id)
    _audit_profile_changes(db, user, data, new_username)

    return jsonify({"success": True, "user": _user_dict(updated_user, include_auth_type=True)})


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
    user = require_current_user()
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
            jsonify({"error": "Email address required. Add an email in your profile first."}),
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
            {"success": True, "phase": "setup", "totp_secret": secret, "totp_uri": totp_uri}
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

    user = require_current_user()
    data = request.get_json() or {}
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type. Use 'passkey' or 'fido2'."}), 400

    rp_id, rp_name, _ = get_webauthn_config()
    authenticator_type = "platform" if auth_type == "passkey" else "cross-platform"

    options_json, challenge = webauthn_registration_options(
        username=user.username, rp_id=rp_id, rp_name=rp_name, authenticator_type=authenticator_type
    )

    challenge_b64 = bytes_to_base64url(challenge)
    _pending_webauthn_challenges[user.ensured_id] = challenge_b64

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

    user = require_current_user()
    data = request.get_json() or {}

    credential_data = data.get("credential")
    challenge_b64 = data.get("challenge", "")
    auth_type = data.get("auth_type", "passkey").strip().lower()

    if not credential_data or not challenge_b64:
        return jsonify({"error": "Credential and challenge required"}), 400

    if auth_type not in ("passkey", "fido2"):
        return jsonify({"error": "Invalid auth type"}), 400

    # Verify challenge matches
    pending_challenge = _pending_webauthn_challenges.get(user.ensured_id)
    if not pending_challenge or pending_challenge != challenge_b64:
        return jsonify({"error": "Invalid or expired challenge. Start over."}), 400

    rp_id, _, expected_origin = get_webauthn_config()
    challenge_bytes = base64url_to_bytes(challenge_b64)

    # Convert credential to JSON string if it's a dict
    credential_json = (
        json.dumps(credential_data) if isinstance(credential_data, dict) else credential_data
    )

    webauthn_cred = webauthn_verify_registration(
        credential_json=credential_json,
        expected_challenge=challenge_bytes,
        expected_origin=expected_origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        _pending_webauthn_challenges.pop(user.ensured_id, None)
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

    _pending_webauthn_challenges.pop(user.ensured_id, None)

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
            {"authenticated": True, "username": user.username, "is_admin": user.is_admin}
        )
    return jsonify({"authenticated": False})


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
    user = require_current_user()
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
                {"status": "error", "auth_db": False, "error": "Auth database health check failed"}
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
        {"auth_enabled": auth_enabled, "user": user_dict, "guest": auth_enabled and user is None}
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
    user = require_current_user()
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
        from_user_id=user.ensured_id,
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


# ---------------------------------------------------------------------------
# Submodule route imports. Importing each submodule at the bottom of this file
# registers its @auth_bp.route(...) endpoints on the shared blueprint. These
# MUST stay at the bottom — they import helpers defined above (_user_dict,
# _switch_auth_method, etc.), so those bindings must exist before import runs.
# ---------------------------------------------------------------------------
from . import auth_account  # noqa: F401,E402  — registers /account/* routes
from . import auth_admin  # noqa: F401,E402  — registers /admin/* routes
from . import auth_recovery  # noqa: F401,E402  — registers /recover/* and /magic-link/* routes
from . import (  # noqa: F401,E402  — registers /register/* + /login/auth-type routes; noqa: F401,E402  — registers /register/webauthn/* + /login/webauthn/* routes
    auth_registration,
    auth_webauthn,
)
from .auth_webauthn import (  # noqa: F401,E402  — re-export so @patch("api_modular.auth.get_webauthn_config") keeps working
    get_webauthn_config,
)
