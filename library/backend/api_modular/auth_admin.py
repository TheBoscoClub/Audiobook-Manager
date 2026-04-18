"""
Admin endpoints for the auth blueprint (/auth/admin/*).

Extracted from `auth.py` to reduce module size and improve maintainability.
All routes register onto `auth_bp` imported from `.auth`; the parent module
triggers registration via `from . import auth_admin` at its bottom.

None of the helpers or routes in this module are imported or patched by any
other module, so no re-exports from auth.py are required.
"""

import base64
import json
import urllib.parse
from datetime import datetime, timedelta

from auth import (
    AccessRequestRepository,
    AccessRequestStatus,
    AuthDatabase,
    AuthType,
    InboxRepository,
    InboxStatus,
    Notification,
    NotificationRepository,
    NotificationType,
    PendingRecovery,
    ReplyMethod,
    User,
    UserRepository,
    generate_verification_token,
    hash_token,
)
from auth.models import SystemSettingsRepository
from auth.totp import generate_qr_code, get_provisioning_uri, secret_to_base32, setup_totp
from flask import jsonify, request

from .auth import (
    INVITATION_EXPIRY_HOURS,
    _format_claim_token,
    _send_activation_email,
    _send_approval_email,
    _send_denial_email,
    _send_invitation_email,
    _send_reply_email,
    _setup_passkey_data,
    _setup_totp_data,
    _switch_auth_method,
    _user_dict,
    _validate_email_format,
    _validate_username,
    _validate_username_strict,
    admin_required,
    auth_bp,
    get_auth_db,
    require_current_user,
)

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
    user = require_current_user()

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
                "created_at": (message.created_at.isoformat() if message.created_at else None),
                "read_at": message.read_at.isoformat() if message.read_at else None,
                "replied_at": (message.replied_at.isoformat() if message.replied_at else None),
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
        admin_user = require_current_user()
        # require_current_user() raises RuntimeError if called outside @admin_required
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
        {"requests": [r.to_dict() for r in requests], "pending_count": request_repo.count_pending()}
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
    admin_user = require_current_user()
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
    admin_user = require_current_user()
    admin_username = admin_user.username if admin_user else "system"

    # Mark request as denied
    request_repo.deny(request_id, admin_username, reason)

    # Send email notification if user provided email
    email_sent = False
    if access_req.contact_email:
        email_sent = _send_denial_email(
            to_email=access_req.contact_email, username=access_req.username, reason=reason
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
        return (
            jsonify({"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}),
            400,
        )

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

    admin_user = require_current_user()
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

    return jsonify({"success": True, "user_id": new_user.id, "setup_data": setup_data}), 201


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
        return (
            jsonify({"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}),
            400,
        )

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
        request_repo.delete(existing.ensured_id)

    admin_user = require_current_user()
    admin_username = admin_user.username if admin_user else "system"

    raw_claim_token, _ = generate_verification_token()
    truncated_token, formatted_token = _format_claim_token(raw_claim_token)
    claim_token_hash = hash_token(truncated_token)

    claim_expires_at = datetime.now() + timedelta(hours=INVITATION_EXPIRY_HOURS)
    access_request = request_repo.create(username, claim_token_hash, email, claim_expires_at)
    request_repo.store_invite_metadata(access_request.ensured_id, can_download)
    request_repo.approve(access_request.ensured_id, admin_username)

    email_sent = _send_invitation_email(
        to_email=email,
        username=username,
        claim_token=formatted_token,
        expires_hours=INVITATION_EXPIRY_HOURS,
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


def _invite_magic_link_user(
    db, user_repo, username, email, can_download
):  # pylint: disable=unused-argument  # user_repo reserved for future lookup/update path; currently saves directly via User.save(db)
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
    _, raw_token = PendingRecovery.create(
        db, user.ensured_id, expiry_minutes=60 * INVITATION_EXPIRY_HOURS
    )

    # Send activation email
    email_sent = _send_activation_email(
        to_email=email,
        username=username,
        activation_token=raw_token,
        expires_hours=INVITATION_EXPIRY_HOURS,
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
    current_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
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
        {"success": True, "username": target_user.username, "is_admin": new_admin_status}
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

    current_user = require_current_user()
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
        {"success": True, "username": target_user.username, "can_download": new_download_status}
    )


def _apply_user_profile_updates(
    user_repo: "UserRepository", user_id: int, data: dict
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
    if updated_user is None:
        return jsonify({"error": "User not found after update"}), 404
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
    current_user = require_current_user()
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

    return jsonify({"success": True, "message": f"User '{target_user.username}' deleted."})


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

    admin_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
    details = {
        "old": old_username,
        "new": new_username,
        "actor_username": admin_user.username,
        "target_username": new_username,
    }
    AuditLogRepository(db).log(
        actor_id=admin_user.id, target_id=user_id, action="change_username", details=details
    )
    notify_admins("change_username", details, db)

    updated = user_repo.get_by_id(user_id)
    if updated is None:
        return jsonify({"error": "User not found after update"}), 404
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
    admin_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
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
    if updated is None:
        return jsonify({"error": "User not found after update"}), 404
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
    user_repo: "UserRepository", user_id: int, data: dict
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

    if "is_admin" not in data and "can_download" not in data and "multi_session" not in data:
        return jsonify({"error": "Provide is_admin, can_download, and/or multi_session"}), 400

    db = get_auth_db()
    user_repo = UserRepository(db)

    target_user = user_repo.get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    old_roles = {"is_admin": target_user.is_admin, "can_download": target_user.can_download}

    err = _apply_role_changes(user_repo, user_id, data)
    if err:
        return jsonify(err[0]), err[1]

    admin_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
    updated = user_repo.get_by_id(user_id)
    if updated is None:
        return jsonify({"error": "User not found after role update"}), 404
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
        return (
            jsonify({"error": "Invalid auth_method. Use 'totp', 'magic_link', or 'passkey'"}),
            400,
        )

    db = get_auth_db()
    target_user = UserRepository(db).get_by_id(user_id)
    if not target_user:
        return jsonify({"error": "User not found"}), 404

    old_method = target_user.auth_type.value
    setup_data, err = _switch_auth_method(target_user, db, auth_method, data)
    if err:
        return err

    admin_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
    details = {
        "old": old_method,
        "new": auth_method,
        "actor_username": admin_user.username,
        "target_username": target_user.username,
    }
    AuditLogRepository(db).log(
        actor_id=admin_user.id, target_id=user_id, action="switch_auth_method", details=details
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

    admin_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
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
            "expires_at": (pending_reg.expires_at.isoformat() if pending_reg.expires_at else ""),
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
        actor_id=admin_user.id, target_id=user_id, action="reset_credentials", details=details
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
    admin_user = require_current_user()
    # require_current_user() raises RuntimeError if called outside @admin_required
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
        actor_id=admin_user.id, target_id=user_id, action="delete_account", details=details
    )
    notify_admins("delete_account", details, db)

    # Delete user (cascades to sessions, positions, etc.)
    user_repo.delete(user_id)

    # Clean up any associated access request
    request_repo = AccessRequestRepository(db)
    request_repo.delete_for_username(target_user.username)

    return jsonify({"success": True, "message": f"User '{target_user.username}' deleted."})


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
        limit=limit, offset=offset, action_filter=action_filter, user_filter=user_filter
    )
    total = audit_repo.count(action_filter=action_filter, user_filter=user_filter)

    return jsonify(
        {
            "entries": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp,
                    "actor_id": e.actor_id,
                    "target_id": e.target_id,
                    "action": e.action,
                    "details": (json.loads(e.details) if isinstance(e.details, str) else e.details),
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
            "SELECT * FROM pending_registrations WHERE username = ? ORDER BY id DESC LIMIT 1",
            (username,),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        from auth.models import PendingRegistration as PR

        pending = PR.from_row(row)
        return {
            "claim_token": "pending",
            "expires_at": pending.expires_at.isoformat() if pending.expires_at else None,
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
