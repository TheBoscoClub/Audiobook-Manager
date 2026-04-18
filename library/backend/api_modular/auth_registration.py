"""
Registration and claim-flow endpoints (/register/* and /login/auth-type).

Extracted from `auth.py` to reduce module size and improve maintainability.
All routes register onto `auth_bp` imported from `.auth`; the parent module
triggers registration via `from . import auth_registration` at its bottom.

Test-patch compatibility:
    `api_modular.auth.get_webauthn_config` is the patch target used by
    `test_auth_webauthn_flows.py` and sibling suites. Routes here look it up
    dynamically through `_auth_module.get_webauthn_config()` so the patch
    installed on the `auth` namespace remains effective when execution
    reaches this module's routes.
"""

import base64

from auth import (
    AccessRequestStatus,
    AuthType,
    PendingRegistrationRepository,
    Session,
    generate_verification_token,
    hash_token,
)
from flask import jsonify, request

# Test-patch compatibility: tests patch.object(auth_mod, "UserRepository"),
# "AccessRequestRepository", "BackupCodeRepository", "setup_totp",
# "generate_qr_code", "base32_to_secret", "User" — look them up dynamically
# through _auth_module so patches on the `auth` namespace remain effective.
from . import auth as _auth_module
from .auth import (
    _extract_recovery_fields,
    _format_claim_token,
    _recovery_warning,
    _user_allows_multi_session,
    _validate_username,
    _verify_webauthn_credential,
    auth_bp,
    get_auth_db,
    set_session_cookie,
)

# =============================================================================
# Claim-flow helpers (moved from auth.py)
# =============================================================================


def _resolve_claim_error(
    status: str, message: str, code: int = 400
) -> tuple[None, None, None, tuple]:
    """Build a standard claim-token error return tuple."""
    return (None, None, None, (jsonify({"valid": False, "status": status, "error": message}), code))


def _parse_invite_meta(backup_codes_json: str | None) -> bool:
    """Parse invite metadata from access request to extract can_download flag."""
    import json as _json

    if not backup_codes_json:
        return True
    try:
        meta = _json.loads(backup_codes_json)
        if isinstance(meta, dict) and meta.get("invited"):
            return meta.get("can_download", True)
    except _json.JSONDecodeError, TypeError:
        pass
    return True


def _apply_claim_credentials_reset(
    existing_user, db, obj, auth_method, username, recovery_email, recovery_phone, recovery_enabled
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

        backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(existing_user.id)

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

    totp_secret, totp_base32, totp_uri = _auth_module.setup_totp(username)
    existing_user.auth_type = AuthType.TOTP
    existing_user.auth_credential = totp_secret
    if recovery_email:
        existing_user.recovery_email = recovery_email
    if recovery_phone:
        existing_user.recovery_phone = recovery_phone
    existing_user.recovery_enabled = recovery_enabled
    existing_user.save(db)
    obj.consume(db)

    backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(existing_user.id)

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
        qr_png = _auth_module.generate_qr_code(_auth_module.base32_to_secret(totp_base32), username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode("ascii")
    except ImportError:
        pass

    return jsonify(response_data)


def _apply_claim_new_user_totp(
    db, username, can_download, recovery_email, recovery_phone, recovery_enabled, access_req_id
):
    """Create a new TOTP user during claim flow. Returns Flask JSON response."""
    totp_secret, totp_base32, totp_uri = _auth_module.setup_totp(username)

    new_user = _auth_module.User(
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

    backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(new_user.ensured_id)
    _auth_module.AccessRequestRepository(db).mark_credentials_claimed(access_req_id)

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
        qr_png = _auth_module.generate_qr_code(_auth_module.base32_to_secret(totp_base32), username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode("ascii")
    except ImportError:
        pass

    return jsonify(response_data)


def _apply_claim_new_user_magic_link(
    db, username, can_download, recovery_email, recovery_phone, access_req_id
):
    """Create a new magic_link user during claim flow. Returns Flask JSON response."""
    new_user = _auth_module.User(
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

    backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(new_user.ensured_id)
    _auth_module.AccessRequestRepository(db).mark_credentials_claimed(access_req_id)

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
    user_repo = _auth_module.UserRepository(db)
    request_repo = _auth_module.AccessRequestRepository(db)

    if user_repo.username_exists(username):
        return jsonify({"error": "Username already taken"}), 400

    dup_err = _check_duplicate_request(request_repo, username)
    if dup_err:
        return dup_err

    if user_repo.count() == 0:
        return _bootstrap_first_user(db, username)

    return _create_access_request(request_repo, username, contact_email)


def _check_duplicate_request(request_repo, username):
    """Check for duplicate access requests. Returns response tuple or None."""
    if not request_repo.has_any_request(username):
        return None
    if request_repo.has_pending_request(username):
        return jsonify({"error": "Access request already pending for this username"}), 400
    return jsonify({"error": "Username already has a previous access request"}), 400


def _bootstrap_first_user(db, username):
    """Create the first user as admin with TOTP. Returns JSON response."""
    totp_secret, totp_base32, totp_uri = _auth_module.setup_totp(username)
    new_user = _auth_module.User(
        username=username,
        auth_type=AuthType.TOTP,
        auth_credential=totp_secret,
        can_download=True,
        is_admin=True,
    )
    new_user.save(db)

    codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(new_user.ensured_id)
    qr_png = _auth_module.generate_qr_code(_auth_module.base32_to_secret(totp_base32), username)
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
    _, formatted_token = _format_claim_token(raw_claim_token)
    claim_token_hash = hash_token(formatted_token.replace("-", ""))

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
        response_data[
            "message"
        ] += f" We'll also notify you at {contact_email} when your request is reviewed."

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
    request_repo = _auth_module.AccessRequestRepository(db)
    user_repo = _auth_module.UserRepository(db)
    claim_token_hash = hash_token(clean_token)

    access_req = request_repo.get_pending_by_username_and_token(username, claim_token_hash)
    if access_req:
        return _resolve_access_request(access_req, user_repo, username)

    return _resolve_pending_registration(db, user_repo, clean_token, username)


def _resolve_access_request(access_req, user_repo, username):
    """Validate an access request claim token. Returns resolve tuple."""
    if access_req.status == AccessRequestStatus.PENDING:
        return _resolve_claim_error("pending", "Your request is still pending admin review")
    if access_req.status == AccessRequestStatus.DENIED:
        return _resolve_claim_error("denied", access_req.deny_reason or "Your request was denied")
    if access_req.credentials_claimed or user_repo.username_exists(username):
        return _resolve_claim_error("already_claimed", "Credentials have already been claimed.")
    if access_req.is_claim_expired():
        return _resolve_claim_error(
            "expired", "This invitation has expired. Please ask the admin to send a new one."
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
            (jsonify({"valid": False, "error": "Invalid username or claim token"}), 404),
        )

    if pending_reg.is_expired():
        pending_reg.consume(db)
        return _resolve_claim_error(
            "expired", "This reset token has expired. Please ask the admin for a new one."
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
        return _resolve_claim_error("already_claimed", "Credentials have already been set up.")

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

    mode, _, _, error = _resolve_claim_token(username, claim_token)
    if error:
        return error

    return jsonify({"valid": True, "status": "approved", "mode": mode, "username": username})


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
        200: { "success": true, "totp_secret": "...", ... }
        400: {"error": "..."}
        404: {"error": "..."}
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
            db, username, can_download, recovery_email, recovery_phone, obj.id
        )

    return _apply_claim_new_user_totp(
        db, username, can_download, recovery_email, recovery_phone, recovery_enabled, obj.id
    )


def _validate_claim_input(data: dict) -> tuple | None:
    """Validate claim_credentials input. Returns error response or None."""
    username = data.get("username", "").strip()
    claim_token = data.get("claim_token", "").strip()
    auth_method = data.get("auth_method", "totp").strip()

    if not username or not claim_token:
        return jsonify({"error": "Username and claim_token are required"}), 400
    if auth_method not in ("totp", "magic_link"):
        return jsonify({"error": "Invalid auth_method. Use 'totp' or 'magic_link'"}), 400
    recovery_email = (data.get("recovery_email") or "").strip() or None
    if auth_method == "magic_link" and not recovery_email:
        return jsonify({"error": "Email address is required for magic link authentication"}), 400
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
        200: {"options": {...}, "challenge": "..."}
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

    _, _, _, error = _resolve_claim_token(username, claim_token)
    if error:
        return error

    rp_id, rp_name, _ = _auth_module.get_webauthn_config()

    authenticator_type = "platform" if auth_type == "passkey" else "cross-platform"

    options_json, challenge = _auth_module.webauthn_registration_options(
        username=username, rp_id=rp_id, rp_name=rp_name, authenticator_type=authenticator_type
    )

    return jsonify({"options": options_json, "challenge": bytes_to_base64url(challenge)})


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
        200: {"success": true, "username": "...", "backup_codes": [...]}
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

    rp_id, _, origin = _auth_module.get_webauthn_config()
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
        return (
            jsonify({"error": "Username, claim_token, credential, and challenge are required"}),
            400,
        )
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
    existing_user.auth_type = AuthType.PASSKEY if auth_type == "passkey" else AuthType.FIDO2
    existing_user.auth_credential = webauthn_cred.to_json().encode("utf-8")
    if recovery_email:
        existing_user.recovery_email = recovery_email
    if recovery_phone:
        existing_user.recovery_phone = recovery_phone
    existing_user.recovery_enabled = recovery_enabled
    existing_user.save(db)
    obj.consume(db)

    backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(existing_user.id)
    allow_multi = _user_allows_multi_session(existing_user, db)
    _, token = Session.create_for_user(
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
    db, obj, webauthn_cred, auth_type, username, recovery_email, recovery_phone, recovery_enabled
):
    """Create new user via WebAuthn claim flow. Returns (data, token)."""
    can_download = _parse_invite_meta(obj.backup_codes_json)

    new_user = _auth_module.User(
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

    backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(new_user.ensured_id)
    _auth_module.AccessRequestRepository(db).mark_credentials_claimed(obj.id)

    allow_multi = _user_allows_multi_session(new_user, db)
    _, token = Session.create_for_user(
        db,
        new_user.ensured_id,
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
        {"username": "string"}

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
    request_repo = _auth_module.AccessRequestRepository(db)
    user_repo = _auth_module.UserRepository(db)

    if user_repo.username_exists(username):
        return jsonify(
            {"status": "approved", "message": "Your access has been approved. You can now log in."}
        )

    access_request = request_repo.get_by_username(username)
    if not access_request:
        return jsonify({"error": "No access request found for this username"}), 404

    if access_request.status == AccessRequestStatus.PENDING:
        return jsonify(
            {"status": "pending", "message": "Your request is awaiting administrator review."}
        )
    elif access_request.status == AccessRequestStatus.DENIED:
        return jsonify(
            {
                "status": "denied",
                "message": access_request.deny_reason or "Your request was denied.",
            }
        )
    else:
        return jsonify({"status": access_request.status.value, "message": "Unknown status."})


@auth_bp.route("/register/verify", methods=["POST"])
def verify_registration():
    """
    Verify registration token and complete account setup.

    Request body:
        {
            "token": "verification_token",
            "auth_type": "totp",
            "recovery_email": "optional",
            "recovery_phone": "optional",
            "include_qr": false
        }

    Returns:
        200: { "success": true, ... }
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

    secret, base32_secret, uri = _auth_module.setup_totp(reg.username)
    user = _auth_module.User(
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
    backup_codes = _auth_module.BackupCodeRepository(db).create_codes_for_user(user.ensured_id)
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
        qr_png = _auth_module.generate_qr_code(secret, user.username)
        response_data["totp_qr"] = base64.b64encode(qr_png).decode("ascii")

    return jsonify(response_data)


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
    user_repo = _auth_module.UserRepository(db)

    user = user_repo.get_by_username(username)
    if user is None:
        return jsonify({"auth_type": "totp"}), 200

    return jsonify({"auth_type": user.auth_type.value})
