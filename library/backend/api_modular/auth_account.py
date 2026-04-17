"""
Self-service account endpoints for the auth blueprint (/auth/account/*).

Extracted from `auth.py` to reduce module size and improve maintainability.
All routes register onto `auth_bp` imported from `.auth`; the parent module
triggers registration via `from . import auth_account` at its bottom.

None of the helpers or routes here are imported or patched by any other
module, so no re-exports from auth.py are required.
"""

import base64
import re
import urllib.parse
from typing import Any

from flask import jsonify, make_response, request

from auth import AuthType, UserRepository
from auth.totp import setup_totp, generate_qr_code

from .auth import (
    auth_bp,
    login_required,
    INVITATION_EXPIRY_HOURS,
    get_auth_db,
    require_current_user,
    _switch_auth_method,
    _validate_username,
)


@auth_bp.route("/account", methods=["GET"])
@login_required
def account_get():
    """
    Get the authenticated user's own profile.

    Returns 200 with profile fields.
    """
    user = require_current_user()
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

    user = require_current_user()
    db = get_auth_db()
    old_username = user.username

    if not UserRepository(db).update_username(user.ensured_id, new_username):
        return jsonify({"error": "Username already taken"}), 409

    details = {
        "old": old_username,
        "new": new_username,
        "actor_username": old_username,
        "target_username": new_username,
    }
    AuditLogRepository(db).log(
        actor_id=user.id, target_id=user.id, action="change_username", details=details
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
    from auth.audit import AuditLogRepository

    data = request.get_json() or {}
    new_email = data.get("email", "").strip() if data.get("email") else ""

    # Validate email format if non-empty
    if new_email:
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_pattern, new_email):
            return jsonify({"error": "Invalid email format"}), 400

    user = require_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    old_email = user.recovery_email
    email_val = new_email if new_email else None
    user_repo.update_email(user.ensured_id, email_val)

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

    user = require_current_user()
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
        actor_id=user.id, target_id=user.id, action="switch_auth_method", details=details
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

    user = require_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    # Re-fetch to get current state
    current_user = user_repo.get_by_id(user.ensured_id)
    if not current_user:
        return jsonify({"error": "User not found"}), 404

    setup_data: dict[str, Any] = {}

    if current_user.auth_type == AuthType.TOTP:
        secret_bytes, base32_secret, provisioning_uri = setup_totp(current_user.username)
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
            "expires_at": (pending_reg.expires_at.isoformat() if pending_reg.expires_at else ""),
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

    user = require_current_user()
    db = get_auth_db()
    user_repo = UserRepository(db)

    # Last-admin guard
    if user_repo.is_last_admin(user.ensured_id):
        return jsonify({"error": "Cannot delete last admin"}), 409

    # Audit BEFORE deletion (username is lost after delete)
    details = {
        "username": user.username,
        "actor_username": user.username,
        "target_username": user.username,
    }
    audit_repo = AuditLogRepository(db)
    audit_repo.log(actor_id=user.id, target_id=user.id, action="delete_account", details=details)
    notify_admins("delete_account", details, db)

    # Delete user
    user_repo.delete(user.ensured_id)

    # Clear session cookie so browser logs out
    resp = make_response(jsonify({"success": True, "message": "Account deleted"}))
    resp.delete_cookie("audiobooks_session")
    return resp
