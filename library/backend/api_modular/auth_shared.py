"""
Shared auth contract — leaf module that breaks the cyclic-import edges
between ``auth.py`` and the ``auth_{registration,account,admin,recovery,
webauthn}`` blueprints.

This module owns the symbols every auth-family module needs:

- the Flask ``auth_bp`` Blueprint (so submodule routes can register on it
  without importing ``auth``);
- the ``_auth_db`` module-level handle plus ``init_auth_routes()`` /
  ``get_auth_db()`` accessors;
- session-cookie config and the ``set_session_cookie`` /
  ``clear_session_cookie`` helpers;
- the in-memory pending-state dicts used during auth-method switches;
- all the ``@*_required`` / ``@*_if_enabled`` route decorators;
- session-resolution helpers (``get_current_user``, ``require_current_user``,
  …);
- pure validation / formatting helpers (``_validate_username``,
  ``_user_dict``, ``_setup_totp_data``, ``_switch_auth_method``,
  ``_verify_webauthn_credential``, …).

Nothing in this module imports from any other ``auth_*`` module in
``api_modular``. ``auth.py`` re-exports every public name below so existing
``from .auth import X`` callers, and tests that patch
``backend.api_modular.auth.X``, continue to work unchanged.
"""

import base64
import json
import logging
import sys
import urllib.parse
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Blueprint, Response, current_app, g, jsonify, request

# Add parent paths for imports of the top-level `auth` package
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from auth import (
    AuthDatabase,
    AuthType,
    NotificationRepository,  # noqa: F401  (re-exported via auth.py for tests)
    Session,
    SessionRepository,
    User,
    UserRepository,
)
from auth import webauthn_verify_registration as _shared_webauthn_verify_registration
from auth.models import SystemSettingsRepository
from auth.totp import generate_qr_code, setup_totp

# =============================================================================
# Blueprint + module-level state
# =============================================================================

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
logger = logging.getLogger(__name__)

# Auth database handle (initialized by init_auth_routes)
_auth_db: Optional[AuthDatabase] = None

# Session cookie configuration
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

# Session duration constants
SESSION_DURATION_DEFAULT = None  # Session cookie (cleared on browser close)
SESSION_DURATION_REMEMBER = 10 * 365 * 24 * 60 * 60  # ~10 years (until sign-out)


# =============================================================================
# DB init / accessor
# =============================================================================


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

    # NOTE: pre-W-A8, ``_auth_db`` lived directly in ``auth.py`` and each
    # loaded copy of that module (long-path ``backend.api_modular.auth``
    # and short-path ``api_modular.auth``) had its own independent
    # ``_auth_db``. The W-A8 refactor moved ``_auth_db`` here to
    # ``auth_shared.py``. Per Python's import system, the same source
    # file loaded under two different module names creates two distinct
    # module objects — so each loaded auth_shared still gets its own
    # ``_auth_db`` global. That preserves the pre-W-A8 isolation: when
    # ``backend.api_modular.create_app`` runs, it calls THIS init (in the
    # long-path auth_shared) which rebinds ONLY long-path
    # auth_shared._auth_db. The short-path auth_shared (used by a
    # separate test app created via ``from api_modular import create_app``)
    # is unaffected. The PEP-562 ``__getattr__`` in the matching auth.py
    # resolves ``_auth_db`` via sys.modules at the matching path, keeping
    # each Flask app's auth handle isolated from the others.
    #
    # Wipe any stale direct binding on the matching parent ``auth`` alias
    # so the PEP-562 ``__getattr__`` forwarding (not a snapshotted value)
    # handles attribute lookup — this matters for tests that
    # monkeypatched ``...auth._auth_db = <prev_db>`` directly into the
    # module's __dict__ and didn't clean up.
    import sys as _sys

    # Only target the auth.py alias that matches THIS auth_shared's
    # package — long-path auth_shared's init touches only long-path
    # auth.py's __dict__, preserving isolation between Flask apps.
    _own_pkg = __name__.rsplit(".", 1)[0] if "." in __name__ else ""
    _own_auth_alias = f"{_own_pkg}.auth" if _own_pkg else "auth"
    _mod = _sys.modules.get(_own_auth_alias)
    if _mod is not None and "_auth_db" in _mod.__dict__:
        del _mod.__dict__["_auth_db"]

    # In dev mode, allow non-secure cookies for localhost
    if is_dev:
        _session_cookie_secure = False

    # Log WebAuthn configuration at startup. Resolved through sys.modules to
    # avoid a cyclic-import edge: auth_webauthn imports this module, so a
    # direct ``from .auth_webauthn import get_webauthn_config`` would create
    # a back-edge that pylint flags as cyclic-import. By the time
    # init_auth_routes() runs (during Flask app startup), auth_webauthn is
    # fully loaded, so the lookup is safe.
    import sys as _sys

    auth_webauthn = (
        _sys.modules.get("backend.api_modular.auth_webauthn")
        or _sys.modules.get("api_modular.auth_webauthn")
        or _sys.modules.get("library.backend.api_modular.auth_webauthn")
    )
    if auth_webauthn is not None:
        rp_id, rp_name, origin = auth_webauthn.get_webauthn_config()
        logger.info("WebAuthn config: rp_id=%s, origin=%s, rp_name=%s", rp_id, origin, rp_name)


def get_auth_db() -> AuthDatabase:
    """Get the auth database instance.

    Honor any explicit override on the matching-path ``auth`` module
    (e.g. ``monkeypatch.setattr("backend.api_modular.auth._auth_db",
    db)``). Only the matching-path alias is checked — pre-W-A8 each
    auth.py module had its own ``_auth_db`` and Flask-app isolation
    relied on that, so we don't cross paths here.
    """
    import sys as _sys

    # Look up matching-path auth.py only (the auth module that lives
    # in the SAME package as this auth_shared). A monkeypatch that
    # lands in the matching-path module's ``__dict__`` shadows the
    # PEP-562 ``__getattr__`` and is honored here. Other path aliases
    # (e.g. short-path when this is long-path) belong to a different
    # Flask app's auth handle and must not contaminate this lookup.
    _own_pkg = __name__.rsplit(".", 1)[0] if "." in __name__ else ""
    _own_auth_alias = f"{_own_pkg}.auth" if _own_pkg else "auth"
    _mod = _sys.modules.get(_own_auth_alias)
    if _mod is not None and "_auth_db" in _mod.__dict__:
        _override = _mod.__dict__["_auth_db"]
        if _override is not None and _override is not _auth_db:
            return _override
    if _auth_db is None:
        raise RuntimeError("Auth routes not initialized. Call init_auth_routes() first.")
    return _auth_db


# =============================================================================
# Validation & Formatting Helpers
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

    The actual verifier is looked up via ``sys.modules`` on the parent
    ``api_modular.auth`` module so tests that
    ``@patch("api_modular.auth.webauthn_verify_registration")`` see their
    mocks at call time. Falls back to the locally-bound implementation if
    the parent module isn't on ``sys.modules`` yet (e.g. very early init).
    """
    import sys as _sys

    from webauthn.helpers import base64url_to_bytes

    challenge_b64 = data.get("challenge", "").strip()
    credential = data.get("credential")

    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception as e:
        logger.warning("Invalid challenge format: %s", e)
        return None, None, (jsonify({"error": "Invalid challenge format"}), 400)

    credential_json = json.dumps(credential) if isinstance(credential, dict) else credential

    auth_mod = (
        _sys.modules.get("api_modular.auth")
        or _sys.modules.get("backend.api_modular.auth")
        or _sys.modules.get("library.backend.api_modular.auth")
    )
    verify_fn = (
        getattr(auth_mod, "webauthn_verify_registration", None) if auth_mod is not None else None
    ) or _shared_webauthn_verify_registration

    webauthn_cred = verify_fn(
        credential_json=credential_json,
        expected_challenge=challenge,
        expected_origin=origin,
        expected_rp_id=rp_id,
    )

    if webauthn_cred is None:
        return None, None, (jsonify({"error": "WebAuthn verification failed"}), 400)

    return webauthn_cred, challenge, None


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

    # Check if session is stale. Uses Session.DEFAULT_GRACE_MINUTES (120)
    # so audio listening — which bypasses /api/* and doesn't refresh
    # last_seen — doesn't trigger a silent 401 mid-chapter.
    if session.is_stale():
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
        RuntimeError: if called outside a protected route (bug — use
        ``get_current_user()`` and handle None explicitly instead).
    """
    user = get_current_user()
    if user is None:
        raise RuntimeError("require_current_user() called without @login_required/@admin_required")
    return user


def require_current_user_id() -> int:
    """Return the current authenticated user's ID (narrowed to ``int``).

    A persisted User always has a non-None ``id``; since ``@login_required``
    guarantees the session resolves to a persisted user, the id is likewise
    guaranteed non-None inside a decorated route body. This helper makes that
    invariant visible to mypy so callers can pass the id to repository
    functions typed ``int``.

    Raises:
        RuntimeError: if called outside a protected route, or if the
        resolved User somehow has ``id is None`` (would indicate a
        data-integrity bug in session / user persistence).
    """
    user = require_current_user()
    if user.id is None:
        raise RuntimeError("persisted User must have non-None id")
    return user.id


def require_current_session() -> Session:
    """Return the current authenticated session or raise RuntimeError.

    ONLY call this inside route handlers decorated with ``@login_required``
    or ``@admin_required``. See :func:`require_current_user` for rationale.
    """
    session = get_current_session()
    if session is None:
        raise RuntimeError(
            "require_current_session() called without @login_required/@admin_required"
        )
    return session


# =============================================================================
# Decorators
# =============================================================================


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


# =============================================================================
# Session Cookie Helpers
# =============================================================================


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
