"""
Recovery and magic-link endpoints for the auth blueprint.

Routes registered:
- `/auth/recover/backup-code`        — backup code recovery
- `/auth/recover/remaining-codes`    — count remaining backup codes
- `/auth/recover/regenerate-codes`   — issue new backup code set
- `/auth/recover/update-contact`     — update recovery email/phone
- `/auth/magic-link/login`           — primary magic-link login
- `/auth/magic-link`                 — request magic link (legacy)
- `/auth/magic-link/verify`          — verify magic-link token

Extracted from `auth.py` to reduce module size and improve maintainability.
All routes register onto `auth_bp` imported from `.auth`; the parent module
triggers registration via `from . import auth_recovery` at its bottom.

None of the helpers or routes in this module are imported or patched by any
other module, so no re-exports from auth.py are required.
"""

from datetime import datetime

from auth import (
    AuthType,
    PendingRecovery,
    PendingRecoveryRepository,
    Session,
    SessionRepository,
    UserRepository,
)
from auth.backup_codes import BackupCodeRepository
from auth.totp import setup_totp
from flask import current_app, jsonify, request

from .auth import (
    _user_allows_multi_session,
    auth_bp,
    get_auth_db,
    login_required,
    require_current_user,
    set_session_cookie,
)
from .auth_email import _send_magic_link_email

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
    if not backup_repo.verify_and_consume(user.ensured_id, backup_code):
        return jsonify({"error": "Invalid username or backup code"}), 401

    # Check remaining codes before we replace them
    remaining = backup_repo.get_remaining_count(user.ensured_id)

    # Generate new TOTP secret
    secret, base32_secret, uri = setup_totp(user.username)

    # Update user's auth credential
    user.auth_credential = secret
    user.auth_type = AuthType.TOTP
    user.save(db)

    # Generate new backup codes (replaces old unused codes)
    new_backup_codes = backup_repo.create_codes_for_user(user.ensured_id)

    # Invalidate any existing sessions (force re-login with new TOTP)
    session_repo = SessionRepository(db)
    session_repo.invalidate_user_sessions(user.ensured_id)

    return jsonify(
        {
            "success": True,
            "username": user.username,
            "totp_secret": base32_secret,
            "totp_uri": uri,
            "backup_codes": new_backup_codes,
            "remaining_old_codes": remaining,
            "message": (
                "Account recovered. Set up your new authenticator and save your new backup codes."
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
    user = require_current_user()
    db = get_auth_db()
    backup_repo = BackupCodeRepository(db)

    return jsonify({"remaining": backup_repo.get_remaining_count(user.ensured_id)})


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
    user = require_current_user()
    db = get_auth_db()
    backup_repo = BackupCodeRepository(db)

    # Generate new codes (this deletes old unused codes)
    new_codes = backup_repo.create_codes_for_user(user.ensured_id)

    return jsonify(
        {
            "success": True,
            "backup_codes": new_codes,
            "message": ("New backup codes generated. Your old codes are no longer valid."),
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

    user = require_current_user()
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
                else "Recovery contact removed. Backup codes are now your only recovery option."
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
    recovery_repo.delete_for_user(user.ensured_id)

    remember_me = data.get("remember_me", True)

    _, raw_token = PendingRecovery.create(db, user.ensured_id, expiry_minutes=15)

    r_flag = "1" if remember_me else "0"
    magic_link_url = f"/verify.html?token={raw_token}&r={r_flag}"

    _send_magic_link_email(
        to_email=email, username=user.username, magic_link=magic_link_url, expires_minutes=15
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
    recovery_repo.delete_for_user(user.ensured_id)  # Remove any existing tokens

    _, raw_token = PendingRecovery.create(db, user.ensured_id, expiry_minutes=15)

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
    _, raw_token = Session.create_for_user(
        db,
        user.ensured_id,
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
