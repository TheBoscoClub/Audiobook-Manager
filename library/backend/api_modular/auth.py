"""
Authentication API Blueprint

Provides endpoints for:
- User login (TOTP verification)
- User registration (with email/SMS verification)
- Session management (logout, session info)
- Password-less authentication flow

All authentication data is stored in the encrypted auth.db (SQLCipher).

Module layout
-------------
The auth contract that every blueprint module needs (Blueprint instance,
DB handle, decorators, validators, session helpers, cookie helpers, the
``_pending_*`` dicts) lives in ``auth_shared.py`` — the leaf module that
breaks the cyclic-import edges between this module and the
``auth_{registration,account,admin,recovery,webauthn}`` siblings.

This module is dedicated to:
1. Re-exporting every shared symbol so historical
   ``from .auth import X`` callers and ``@patch("backend.api_modular.auth.X")``
   targets keep working unchanged.
2. The login / logout / session-restore / /me / /me/auth-method /
   /me/webauthn / /check / /health / /status / /contact / /notifications
   route handlers that originated here.
3. Triggering submodule import at the bottom so each ``auth_*`` module's
   ``@auth_bp.route`` decorators register on the shared blueprint.
"""

import json
import logging
import sys
from pathlib import Path
from flask import jsonify, redirect, request

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Top-level `auth` package re-exports (kept on this module so tests that do
# `patch.object(auth_mod, "UserRepository")` or
# `@patch("api_modular.auth.webauthn_verify_registration")` continue to work).
from auth import (  # noqa: F401  (re-export for tests and downstream submodules)
    AccessRequestRepository,
    AuthDatabase,
    AuthType,
    BackupCodeRepository,
    InboxMessage,
    NotificationRepository,
    ReplyMethod,
    Session,
    SessionRepository,
    User,
    UserRepository,
    webauthn_authentication_options,
    webauthn_registration_options,
    webauthn_verify_authentication,
    webauthn_verify_registration,
)
from auth.totp import base32_to_secret  # noqa: F401  (re-export for tests)
from auth.totp import generate_qr_code  # noqa: F401  (re-export for tests)
from auth.totp import setup_totp  # noqa: F401  (re-export for tests)
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

# All of the shared auth contract — Blueprint, decorators, session helpers,
# validators, the cookie helpers, and the _pending_* dicts — lives in
# auth_shared.py. Re-exported here so callers and test patches that target
# `backend.api_modular.auth.<name>` keep working unchanged.
#
# IMPORTANT: ``_auth_db`` is the one shared name that gets *rebound* (not just
# mutated) in auth_shared at runtime — ``init_auth_routes()`` runs
# ``_auth_db = AuthDatabase(...)`` mid-process, and tests rebind it again via
# fixtures. A direct ``from .auth_shared import _auth_db`` here would snapshot
# the value at import time, so the rebind would never propagate. ``_auth_db``
# is therefore handled via the PEP 562 module ``__getattr__`` hook below,
# which forwards attribute lookup to auth_shared at call time. Tests that do
# ``monkeypatch.setattr("backend.api_modular.auth._auth_db", X)`` still work
# because Python checks the module ``__dict__`` before falling back to
# ``__getattr__``.
#
# The ``_pending_*`` dicts and ``_session_cookie_*`` flags are imported
# directly: the dicts are mutated (same object identity, so a snapshot is
# fine), and the cookie flags are read-only after init from this module's
# perspective (only ``set_session_cookie`` / ``clear_session_cookie`` in
# auth_shared write them, and they read auth_shared's own bindings).
from .auth_shared import (  # noqa: F401  (re-export)
    INVITATION_EXPIRY_HOURS,
    SESSION_DURATION_DEFAULT,
    SESSION_DURATION_REMEMBER,
    _extract_recovery_fields,
    _format_claim_token,
    _pending_totp_secrets,
    _pending_webauthn_challenges,
    _recovery_warning,
    _session_cookie_httponly,
    _session_cookie_name,
    _session_cookie_samesite,
    _session_cookie_secure,
    _setup_passkey_data,
    _setup_totp_data,
    _switch_auth_method,
    _user_allows_multi_session,
    _user_dict,
    _validate_email_format,
    _validate_username,
    _validate_username_strict,
    _validate_webauthn_reg_input,
    _verify_webauthn_credential,
    admin_if_enabled,
    admin_or_localhost,
    admin_required,
    auth_bp,
    auth_if_enabled,
    clear_session_cookie,
    download_permission_required,
    get_auth_db,
    get_current_session,
    get_current_user,
    guest_allowed,
    init_auth_routes,
    login_required,
    localhost_only,
    require_current_session,
    require_current_user,
    require_current_user_id,
    set_session_cookie,
)


# ``_auth_db`` is rebound (not just mutated) in auth_shared at runtime, so
# its lookup must defer to auth_shared at access time rather than snapshot at
# import time. See module-level note above.
_DEFERRED_ATTRS = frozenset({"_auth_db"})


def __getattr__(name):
    """Forward access of rebindable auth_shared state to the live module.

    Direct ``from .auth_shared import _auth_db`` would snapshot the value
    at import time; subsequent rebinding in auth_shared (e.g. via
    ``init_auth_routes``) would not be visible here. The module
    ``__getattr__`` hook (PEP 562) forwards attribute access to the
    matching-path auth_shared at call time, keeping callers and
    ``monkeypatch.setattr("...auth._auth_db", ...)`` semantics in sync.

    The ``from . import auth_shared`` resolution intentionally uses
    THIS module's package — long-path ``backend.api_modular.auth``
    forwards to long-path ``backend.api_modular.auth_shared``, and
    short-path ``api_modular.auth`` forwards to short-path
    ``api_modular.auth_shared``. This preserves pre-W-A8 isolation:
    each Flask app (one created from each path) has its own
    independently-initialized ``_auth_db`` and the two apps do not
    cross-contaminate. ``init_auth_routes`` rebinds only the
    matching-path auth_shared._auth_db, so each path's
    ``__getattr__`` returns the correct live handle for its app.
    """
    if name in _DEFERRED_ATTRS:
        from . import auth_shared

        return getattr(auth_shared, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


logger = logging.getLogger(__name__)


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
    user_id = require_current_user_id()
    # require_current_user_id() raises RuntimeError if called outside @login_required
    db = get_auth_db()
    notif_repo = NotificationRepository(db)

    if notif_repo.dismiss(notification_id, user_id):
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
    from flask import current_app

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
# MUST stay at the bottom — the submodules import from .auth_shared (which
# this module also re-exports), so the shared bindings exist before submodule
# import runs regardless. Bottom placement preserves the established
# registration order.
# ---------------------------------------------------------------------------
from . import (  # noqa: F401,E402  — registers /register/* + /login/auth-type routes; noqa: F401,E402  — registers /register/webauthn/* + /login/webauthn/* routes
    auth_account,  # noqa: F401,E402  — registers /account/* routes
    auth_admin,  # noqa: F401,E402  — registers /admin/* routes
    auth_recovery,  # noqa: F401,E402  — registers /recover/* and /magic-link/* routes
    auth_registration,
    auth_webauthn,
)
from .auth_webauthn import (  # noqa: F401,E402  — re-export so @patch("api_modular.auth.get_webauthn_config") keeps working
    get_webauthn_config,
)
