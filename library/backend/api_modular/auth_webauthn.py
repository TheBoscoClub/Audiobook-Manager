"""
WebAuthn / Passkey registration and authentication endpoints
(/register/webauthn/* and /login/webauthn/*).

Extracted from `auth.py` to reduce module size and improve maintainability.
All routes register onto `auth_bp` imported from `.auth`; the parent module
triggers registration via `from . import auth_webauthn` at its bottom.

Test-patch compatibility:
    Tests patch `api_modular.auth.get_webauthn_config` (20 call sites across
    test_auth_webauthn_flows.py, test_credential_reset_claim_lifecycle.py, and
    test_auth_email_and_config.py). To keep those patches effective after this
    extraction, the routes below look up `get_webauthn_config` dynamically via
    `_auth_module.get_webauthn_config()` instead of binding the name at import
    time. `auth.py` re-exports `get_webauthn_config` at its bottom via
    `from .auth_webauthn import get_webauthn_config`, so
    `api_modular.auth.get_webauthn_config` remains a valid patch target that
    points at this module's implementation until a test replaces it.
"""

import json
import logging
import sys
from pathlib import Path

from flask import jsonify, request

from auth import (
    AuthType,
    PendingRegistrationRepository,
    User,
    UserRepository,
    WebAuthnCredential,
)
from auth.backup_codes import BackupCodeRepository

from . import auth as _auth_module
from .auth import (
    auth_bp,
    get_auth_db,
    set_session_cookie,
    _user_allows_multi_session,
    _extract_recovery_fields,
    _validate_webauthn_reg_input,
    _verify_webauthn_credential,
    _recovery_warning,
)
from auth import Session

logger = logging.getLogger(__name__)


# =============================================================================
# WebAuthn Configuration Discovery
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


# =============================================================================
# WebAuthn / Passkey Registration Endpoints
# =============================================================================


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

    reg = reg_repo.get_by_token(token)
    if reg is None:
        return jsonify({"error": "Invalid or expired verification token"}), 400

    if reg.is_expired():
        reg.consume(db)
        return jsonify({"error": "Verification token has expired"}), 400

    rp_id, rp_name, _ = _auth_module.get_webauthn_config()

    authenticator_type = "platform" if auth_type == "passkey" else "cross-platform"

    options_json, challenge = _auth_module.webauthn_registration_options(
        username=reg.username, rp_id=rp_id, rp_name=rp_name, authenticator_type=authenticator_type
    )

    return jsonify(
        {
            "options": options_json,
            "challenge": bytes_to_base64url(challenge),
            "token": token,
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

    rp_id, _, origin = _auth_module.get_webauthn_config()
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
    backup_codes = BackupCodeRepository(db).create_codes_for_user(user.ensured_id)
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
# WebAuthn / Passkey Authentication Endpoints
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

    user = user_repo.get_by_username(username)
    if user is None:
        return jsonify({"error": "Invalid credentials"}), 401

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

    try:
        webauthn_cred = WebAuthnCredential.from_json(user.auth_credential.decode("utf-8"))
    except Exception as e:
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure  # Reason: log text is a fixed phrase (no secret material); only user_id and exception class name are interpolated
        logger.error(
            "Invalid stored credential for user_id=%s error_class=%s",
            getattr(user, "id", "<unknown>"),
            type(e).__name__,
        )
        return jsonify({"error": "Invalid stored credential"}), 500

    rp_id, _, _ = _auth_module.get_webauthn_config()

    options_json, challenge = _auth_module.webauthn_authentication_options(
        user_id=user.ensured_id,
        credential_id=webauthn_cred.credential_id,
        rp_id=rp_id,
        username=username,
    )

    return jsonify({"options": options_json, "challenge": bytes_to_base64url(challenge)})


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
        return jsonify({"error": "Username, credential, and challenge are required"}), 400

    db = get_auth_db()
    user = UserRepository(db).get_by_username(username)
    if user is None or user.auth_type not in (AuthType.PASSKEY, AuthType.FIDO2):
        return jsonify({"error": "Invalid credentials"}), 401

    webauthn_cred, challenge = _parse_webauthn_login(user, challenge_b64)
    if webauthn_cred is None:
        return jsonify({"error": "Invalid credentials"}), 401

    rp_id, _, origin = _auth_module.get_webauthn_config()
    credential_json = json.dumps(credential) if isinstance(credential, dict) else credential

    new_sign_count = _auth_module.webauthn_verify_authentication(
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
    _, token = Session.create_for_user(
        db,
        user.ensured_id,
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
        webauthn_cred = WebAuthnCredential.from_json(user.auth_credential.decode("utf-8"))
    except Exception as e:
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure  # Reason: fixed phrase plus exception class name only
        logger.warning("Failed to parse WebAuthn credential: error_class=%s", type(e).__name__)
        return None, None
    try:
        challenge = base64url_to_bytes(challenge_b64)
    except Exception as e:
        logger.warning("Failed to parse WebAuthn challenge: error_class=%s", type(e).__name__)
        return None, None
    return webauthn_cred, challenge
