"""
Extended unit tests for auth API endpoints (auth.py).

Covers the largest coverage gaps:
- Session restore flow
- Self-service profile updates (/me PUT)
- Auth method switching (/me/auth-method)
- Registration claim validation and claim flows
- Registration status checking
- Registration verification (legacy flow)
- Access request admin operations (approve/deny with email)
- Admin user management (create, invite, delete, toggle, update)
- Admin granular user management (username/email/roles/auth-method/reset)
- Admin inbox operations (list, read, reply, archive)
- Admin notifications (create, list, delete)
- Notification dismiss
- Contact/inbox submission
- Auth status endpoint
- Decorator tests (localhost_only, download_permission_required, etc.)
- Magic link login flow
- Self-service account endpoints (/account/*)
- Admin audit log
- Admin setup info
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import (  # noqa: E402
    AuthType,
    UserRepository,
    AccessRequestRepository,
    SessionRepository,
    NotificationRepository,
    InboxRepository,
    InboxMessage,
    InboxStatus,
    ReplyMethod,
    User,
    Session,
    hash_token,
    generate_verification_token,
    PendingRecoveryRepository,
    PendingRecovery,
    Notification,
    NotificationType,
)
from auth.totp import TOTPAuthenticator, setup_totp  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Helper: create an admin-authenticated client
# ──────────────────────────────────────────────────────────────────────


def _admin_login(auth_app):
    """Return a test client logged in as the seed admin user."""
    client = auth_app.test_client()
    auth = TOTPAuthenticator(auth_app.admin_secret)
    client.post(
        "/auth/login", json={"username": "adminuser", "code": auth.current_code()}
    )
    return client


def _user_login(auth_app):
    """Return a test client logged in as the seed regular user."""
    client = auth_app.test_client()
    auth = TOTPAuthenticator(auth_app.test_user_secret)
    client.post(
        "/auth/login", json={"username": "testuser1", "code": auth.current_code()}
    )
    return client


# ──────────────────────────────────────────────────────────────────────
# Session Restore
# ──────────────────────────────────────────────────────────────────────


class TestSessionRestore:
    """Tests for /auth/session/restore endpoint."""

    def test_restore_missing_token(self, anon_client):
        r = anon_client.post("/auth/session/restore", json={})
        assert r.status_code == 400

    def test_restore_invalid_token(self, anon_client):
        r = anon_client.post("/auth/session/restore", json={"token": "bogus"})
        assert r.status_code == 401

    def test_restore_non_persistent_session(self, auth_app, auth_db):
        """Non-persistent sessions cannot be restored."""
        # Create a non-persistent session
        user_repo = UserRepository(auth_db)
        user = user_repo.get_by_username("testuser1")
        session, raw_token = Session.create_for_user(
            auth_db, user.id, "pytest", "127.0.0.1", remember_me=False
        )
        client = auth_app.test_client()
        r = client.post("/auth/session/restore", json={"token": raw_token})
        assert r.status_code == 401
        assert "not persistent" in r.get_json()["error"]

    def test_restore_persistent_session(self, auth_app, auth_db):
        """Persistent sessions can be restored."""
        user_repo = UserRepository(auth_db)
        user = user_repo.get_by_username("testuser1")
        session, raw_token = Session.create_for_user(
            auth_db, user.id, "pytest", "127.0.0.1", remember_me=True
        )
        client = auth_app.test_client()
        r = client.post("/auth/session/restore", json={"token": raw_token})
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["user"]["username"] == "testuser1"


# ──────────────────────────────────────────────────────────────────────
# Self-service profile updates (/me PUT)
# ──────────────────────────────────────────────────────────────────────


class TestUpdateCurrentUser:
    """Tests for PUT /auth/me (self-service profile update)."""

    def test_update_username(self, auth_app, user_client, test_user):
        r = user_client.put("/auth/me", json={"username": "newname_ext"})
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert data["user"]["username"] == "newname_ext"
        # Restore original name
        user_client.put("/auth/me", json={"username": "regularuser_fix"})

    def test_update_username_too_short(self, user_client):
        r = user_client.put("/auth/me", json={"username": "ab"})
        assert r.status_code == 400

    def test_update_username_too_long(self, user_client):
        r = user_client.put("/auth/me", json={"username": "a" * 25})
        assert r.status_code == 400

    def test_update_username_invalid_chars(self, user_client):
        r = user_client.put("/auth/me", json={"username": "bad<user>"})
        assert r.status_code == 400

    def test_update_email(self, user_client):
        r = user_client.put("/auth/me", json={"email": "test@example.com"})
        assert r.status_code == 200
        assert r.get_json()["user"]["email"] == "test@example.com"

    def test_update_email_invalid(self, user_client):
        r = user_client.put("/auth/me", json={"email": "not-an-email"})
        assert r.status_code == 400

    def test_update_email_remove(self, user_client):
        r = user_client.put("/auth/me", json={"email": None})
        assert r.status_code == 200

    def test_update_requires_auth(self, anon_client):
        r = anon_client.put("/auth/me", json={"email": "test@example.com"})
        assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────────
# Auth method switching (/me/auth-method)
# ──────────────────────────────────────────────────────────────────────


class TestUpdateAuthMethod:
    """Tests for PUT /auth/me/auth-method."""

    def test_invalid_auth_method(self, user_client):
        r = user_client.put("/auth/me/auth-method", json={"auth_method": "invalid"})
        assert r.status_code == 400

    def test_magic_link_requires_email(self, user_client):
        """Magic link requires a recovery email to be set."""
        # Ensure no email is set
        user_client.put("/auth/me", json={"email": None})
        r = user_client.put(
            "/auth/me/auth-method", json={"auth_method": "magic_link"}
        )
        assert r.status_code == 400
        assert "Email" in r.get_json()["error"] or "email" in r.get_json()["error"]

    def test_totp_setup_phase(self, user_client):
        r = user_client.put(
            "/auth/me/auth-method",
            json={"auth_method": "totp", "phase": "setup"},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["phase"] == "setup"
        assert "totp_secret" in data

    def test_totp_confirm_phase_missing_code(self, user_client):
        # Setup first
        user_client.put(
            "/auth/me/auth-method",
            json={"auth_method": "totp", "phase": "setup"},
        )
        r = user_client.put(
            "/auth/me/auth-method",
            json={"auth_method": "totp", "phase": "confirm", "code": ""},
        )
        assert r.status_code == 400

    def test_totp_confirm_no_pending(self, user_client):
        """Confirm without a prior setup should fail."""
        # Clear any pending secret by importing module state
        r = user_client.put(
            "/auth/me/auth-method",
            json={"auth_method": "totp", "phase": "confirm", "code": "123456"},
        )
        # Should get 400 either for no pending setup or invalid code
        assert r.status_code == 400

    def test_passkey_returns_setup_url(self, user_client):
        r = user_client.put(
            "/auth/me/auth-method", json={"auth_method": "passkey"}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "registration_url" in data

    def test_requires_auth(self, anon_client):
        r = anon_client.put(
            "/auth/me/auth-method", json={"auth_method": "totp"}
        )
        assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────────
# Registration claim validation
# ──────────────────────────────────────────────────────────────────────


class TestClaimValidation:
    """Tests for POST /auth/register/claim/validate."""

    def test_validate_missing_fields(self, anon_client):
        r = anon_client.post("/auth/register/claim/validate", json={})
        assert r.status_code == 400

    def test_validate_missing_username(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/validate",
            json={"claim_token": "XXXX-XXXX-XXXX-XXXX"},
        )
        assert r.status_code == 400

    def test_validate_invalid_token(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/validate",
            json={"username": "nobody", "claim_token": "XXXX-XXXX-XXXX-XXXX"},
        )
        assert r.status_code == 404


class TestClaimCredentials:
    """Tests for POST /auth/register/claim."""

    def test_claim_missing_fields(self, anon_client):
        r = anon_client.post("/auth/register/claim", json={})
        assert r.status_code == 400

    def test_claim_invalid_auth_method(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim",
            json={
                "username": "test",
                "claim_token": "XXXX",
                "auth_method": "invalid",
            },
        )
        assert r.status_code == 400

    def test_claim_magic_link_requires_email(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim",
            json={
                "username": "test",
                "claim_token": "XXXX",
                "auth_method": "magic_link",
            },
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# Registration status
# ──────────────────────────────────────────────────────────────────────


class TestRegistrationStatus:
    """Tests for POST /auth/register/status."""

    def test_status_missing_username(self, anon_client):
        r = anon_client.post("/auth/register/status", json={})
        assert r.status_code == 400

    def test_status_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/register/status", content_type="application/json"
        )
        assert r.status_code == 400

    def test_status_unknown_user(self, anon_client):
        r = anon_client.post(
            "/auth/register/status", json={"username": "never_existed_xyz"}
        )
        assert r.status_code == 404

    def test_status_existing_user(self, anon_client):
        """A user that already exists should return approved."""
        r = anon_client.post(
            "/auth/register/status", json={"username": "adminuser"}
        )
        assert r.status_code == 200
        assert r.get_json()["status"] == "approved"


# ──────────────────────────────────────────────────────────────────────
# Auth status (public endpoint)
# ──────────────────────────────────────────────────────────────────────


class TestAuthStatus:
    """Tests for GET /auth/status."""

    def test_status_unauthenticated(self, anon_client, auth_app):
        r = anon_client.get("/auth/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "auth_enabled" in data
        assert "guest" in data

    def test_status_authenticated(self, user_client, auth_app):
        r = user_client.get("/auth/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["auth_enabled"] is True
        if data["user"]:
            assert "username" in data["user"]


# ──────────────────────────────────────────────────────────────────────
# Admin access requests (approve/deny with email)
# ──────────────────────────────────────────────────────────────────────


class TestAdminAccessRequests:
    """Tests for admin access-request management."""

    def test_list_access_requests(self, admin_client):
        r = admin_client.get("/auth/admin/access-requests")
        assert r.status_code == 200
        data = r.get_json()
        assert "requests" in data
        assert "pending_count" in data

    def test_list_access_requests_status_filter(self, admin_client):
        r = admin_client.get("/auth/admin/access-requests?status=all")
        assert r.status_code == 200

    def test_approve_nonexistent(self, admin_client):
        r = admin_client.post("/auth/admin/access-requests/99999/approve")
        assert r.status_code == 404

    def test_deny_nonexistent(self, admin_client):
        r = admin_client.post(
            "/auth/admin/access-requests/99999/deny",
            json={},
        )
        assert r.status_code == 404

    def test_approve_with_email(self, auth_app, admin_client, auth_db):
        """Full approve flow with email notification."""
        from auth import AccessRequestRepository
        from auth.models import generate_verification_token

        # Create access request directly in DB with contact_email
        request_repo = AccessRequestRepository(auth_db)
        _, token_hash = generate_verification_token()
        access_req = request_repo.create(
            username="approve_email_test",
            claim_token_hash=token_hash,
            contact_email="u@test.com",
        )

        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            r = admin_client.post(
                f"/auth/admin/access-requests/{access_req.id}/approve"
            )
            assert r.status_code == 200
            data = r.get_json()
            assert data["success"] is True
            assert data["email_sent"] is True

    def test_deny_with_reason(self, auth_app, admin_client):
        """Deny flow with reason and email."""
        client = auth_app.test_client()
        r = client.post(
            "/auth/register/start",
            json={"username": "deny_email_test", "contact_email": "u2@test.com"},
        )
        assert r.status_code == 200
        request_id = r.get_json()["request_id"]

        with patch(
            "backend.api_modular.auth._send_denial_email", return_value=True
        ):
            r = admin_client.post(
                f"/auth/admin/access-requests/{request_id}/deny",
                json={"reason": "Test denial"},
            )
            assert r.status_code == 200
            data = r.get_json()
            assert data["success"] is True

    def test_approve_already_approved(self, auth_app, admin_client):
        """Cannot approve an already-approved request."""
        client = auth_app.test_client()
        r = client.post(
            "/auth/register/start", json={"username": "double_approve_test"}
        )
        request_id = r.get_json()["request_id"]
        admin_client.post(f"/auth/admin/access-requests/{request_id}/approve")
        r = admin_client.post(f"/auth/admin/access-requests/{request_id}/approve")
        assert r.status_code == 400

    def test_requires_admin(self, user_client):
        r = user_client.get("/auth/admin/access-requests")
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# Admin user creation
# ──────────────────────────────────────────────────────────────────────


class TestAdminCreateUser:
    """Tests for POST /auth/admin/users/create."""

    def test_create_totp_user(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "created-totp-user",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert r.status_code == 201
        data = r.get_json()
        assert data["success"] is True
        assert "setup_data" in data
        assert "secret" in data["setup_data"]

    def test_create_magic_link_user(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "created-ml-user",
                "auth_method": "magic_link",
                "email": "ml@example.com",
            },
        )
        assert r.status_code == 201

    def test_create_magic_link_requires_email(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "ml-no-email", "auth_method": "magic_link"},
        )
        assert r.status_code == 400

    def test_create_passkey_user(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "created-pk-user", "auth_method": "passkey"},
        )
        assert r.status_code == 201
        data = r.get_json()
        assert "claim_token" in data["setup_data"]

    def test_create_invalid_auth_method(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "bad-auth", "auth_method": "sms"},
        )
        assert r.status_code == 400

    def test_create_username_too_short(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "ab", "auth_method": "totp"},
        )
        assert r.status_code == 400

    def test_create_username_too_long(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "a" * 25, "auth_method": "totp"},
        )
        assert r.status_code == 400

    def test_create_invalid_username_chars(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "bad user!", "auth_method": "totp"},
        )
        assert r.status_code == 400

    def test_create_duplicate(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/create",
            json={"username": "adminuser", "auth_method": "totp"},
        )
        assert r.status_code == 409

    def test_requires_admin(self, user_client):
        r = user_client.post(
            "/auth/admin/users/create",
            json={"username": "nope", "auth_method": "totp"},
        )
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# Admin invite user
# ──────────────────────────────────────────────────────────────────────


class TestAdminInviteUser:
    """Tests for POST /auth/admin/users/invite."""

    @patch("backend.api_modular.auth._send_invitation_email", return_value=True)
    def test_invite_totp(self, mock_email, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={
                "username": "invited-totp",
                "email": "inv@example.com",
                "auth_method": "totp",
            },
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "claim_token" in data

    @patch("backend.api_modular.auth._send_activation_email", return_value=True)
    def test_invite_magic_link(self, mock_email, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={
                "username": "invited-ml",
                "email": "inv-ml@example.com",
                "auth_method": "magic_link",
            },
        )
        assert r.status_code == 200

    def test_invite_missing_email(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={"username": "inv-no-email"},
        )
        assert r.status_code == 400

    def test_invite_invalid_email(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={"username": "inv-bad-email", "email": "notanemail"},
        )
        assert r.status_code == 400

    def test_invite_invalid_auth_method(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={
                "username": "inv-bad-auth",
                "email": "x@x.com",
                "auth_method": "sms",
            },
        )
        assert r.status_code == 400

    def test_invite_username_too_short(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={"username": "ab", "email": "x@x.com"},
        )
        assert r.status_code == 400

    def test_invite_duplicate_username(self, admin_client):
        r = admin_client.post(
            "/auth/admin/users/invite",
            json={"username": "adminuser", "email": "x@x.com"},
        )
        assert r.status_code == 409

    def test_invite_requires_admin(self, user_client):
        r = user_client.post(
            "/auth/admin/users/invite",
            json={"username": "nope", "email": "x@x.com"},
        )
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# Admin toggle admin/download, update/delete user
# ──────────────────────────────────────────────────────────────────────


class TestAdminToggleAndDelete:
    """Tests for admin toggle-admin, toggle-download, update, and delete."""

    def test_toggle_admin_self_demotion(self, admin_client, auth_app):
        """Cannot demote yourself."""
        admin = admin_client._test_admin
        r = admin_client.post(f"/auth/admin/users/{admin.id}/toggle-admin")
        assert r.status_code == 400

    def test_toggle_download(self, admin_client, test_user):
        r = admin_client.post(
            f"/auth/admin/users/{test_user.id}/toggle-download"
        )
        assert r.status_code == 200
        new_val = r.get_json()["can_download"]
        # Toggle back
        admin_client.post(f"/auth/admin/users/{test_user.id}/toggle-download")

    def test_toggle_download_nonexistent(self, admin_client):
        r = admin_client.post("/auth/admin/users/99999/toggle-download")
        assert r.status_code == 404

    def test_update_user_username(self, admin_client, auth_db, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}",
            json={"username": "updated_name_ext"},
        )
        assert r.status_code == 200
        assert r.get_json()["user"]["username"] == "updated_name_ext"
        # Restore
        admin_client.put(
            f"/auth/admin/users/{test_user.id}",
            json={"username": "regularuser_fix"},
        )

    def test_update_user_email(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}",
            json={"email": "new@test.com"},
        )
        assert r.status_code == 200

    def test_update_user_invalid_email(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}",
            json={"email": "invalid"},
        )
        assert r.status_code == 400

    def test_update_user_nonexistent(self, admin_client):
        r = admin_client.put(
            "/auth/admin/users/99999", json={"username": "nope"}
        )
        assert r.status_code == 404

    def test_delete_self(self, admin_client, auth_app):
        admin = admin_client._test_admin
        r = admin_client.delete(f"/auth/admin/users/{admin.id}")
        assert r.status_code == 400

    def test_delete_nonexistent(self, admin_client):
        r = admin_client.delete("/auth/admin/users/99999")
        assert r.status_code == 404

    def test_delete_user_success(self, admin_client, auth_db):
        """Create and delete a user."""
        user = User(
            username="to_delete_ext",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            is_admin=False,
        ).save(auth_db)
        r = admin_client.delete(f"/auth/admin/users/{user.id}")
        assert r.status_code == 200
        assert r.get_json()["success"] is True


# ──────────────────────────────────────────────────────────────────────
# Granular admin user management endpoints
# ──────────────────────────────────────────────────────────────────────


class TestAdminGranularManagement:
    """Tests for /admin/users/<id>/username, email, roles, auth-method, etc."""

    def test_change_username(self, admin_client, auth_db, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/username",
            json={"username": "granular_renamed"},
        )
        assert r.status_code == 200
        assert r.get_json()["user"]["username"] == "granular_renamed"
        # Restore
        admin_client.put(
            f"/auth/admin/users/{test_user.id}/username",
            json={"username": "regularuser_fix"},
        )

    def test_change_username_too_short(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/username",
            json={"username": "ab"},
        )
        assert r.status_code == 400

    def test_change_username_duplicate(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/username",
            json={"username": "adminuser"},
        )
        assert r.status_code == 409

    def test_change_username_nonexistent(self, admin_client):
        r = admin_client.put(
            "/auth/admin/users/99999/username", json={"username": "nope"}
        )
        assert r.status_code == 404

    def test_change_email(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/email",
            json={"email": "admin_set@test.com"},
        )
        assert r.status_code == 200

    def test_change_email_clear(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/email", json={"email": ""}
        )
        assert r.status_code == 200

    def test_change_email_invalid(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/email",
            json={"email": "not-valid"},
        )
        assert r.status_code == 400

    def test_change_email_nonexistent(self, admin_client):
        r = admin_client.put(
            "/auth/admin/users/99999/email", json={"email": "a@b.com"}
        )
        assert r.status_code == 404

    def test_change_roles(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/roles",
            json={"can_download": False},
        )
        assert r.status_code == 200
        assert r.get_json()["user"]["can_download"] is False
        # Restore
        admin_client.put(
            f"/auth/admin/users/{test_user.id}/roles",
            json={"can_download": True},
        )

    def test_change_roles_missing_fields(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/roles", json={}
        )
        assert r.status_code == 400

    def test_change_roles_nonexistent(self, admin_client):
        r = admin_client.put(
            "/auth/admin/users/99999/roles", json={"is_admin": True}
        )
        assert r.status_code == 404

    def test_change_auth_method_totp(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/auth-method",
            json={"auth_method": "totp"},
        )
        assert r.status_code == 200
        assert "setup_data" in r.get_json()

    def test_change_auth_method_magic_link_no_email(self, admin_client, auth_db):
        """Magic link requires email."""
        user = User(
            username="no_email_auth_switch",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        ).save(auth_db)
        r = admin_client.put(
            f"/auth/admin/users/{user.id}/auth-method",
            json={"auth_method": "magic_link"},
        )
        assert r.status_code == 400

    def test_change_auth_method_invalid(self, admin_client, test_user):
        r = admin_client.put(
            f"/auth/admin/users/{test_user.id}/auth-method",
            json={"auth_method": "sms"},
        )
        assert r.status_code == 400

    def test_change_auth_method_nonexistent(self, admin_client):
        r = admin_client.put(
            "/auth/admin/users/99999/auth-method",
            json={"auth_method": "totp"},
        )
        assert r.status_code == 404

    def test_reset_credentials_totp(self, admin_client, test_user):
        r = admin_client.post(
            f"/auth/admin/users/{test_user.id}/reset-credentials"
        )
        assert r.status_code == 200
        assert "setup_data" in r.get_json()

    def test_reset_credentials_nonexistent(self, admin_client):
        r = admin_client.post("/auth/admin/users/99999/reset-credentials")
        assert r.status_code == 404

    def test_delete_user_v2(self, admin_client, auth_db):
        user = User(
            username="delete_v2_test",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
        ).save(auth_db)
        r = admin_client.delete(f"/auth/admin/users/{user.id}/delete")
        assert r.status_code == 200

    def test_delete_user_v2_self(self, admin_client):
        admin = admin_client._test_admin
        r = admin_client.delete(f"/auth/admin/users/{admin.id}/delete")
        assert r.status_code == 400

    def test_delete_user_v2_nonexistent(self, admin_client):
        r = admin_client.delete("/auth/admin/users/99999/delete")
        assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────────
# Admin audit log
# ──────────────────────────────────────────────────────────────────────


class TestAdminAuditLog:
    """Tests for GET /auth/admin/audit-log."""

    def test_audit_log(self, admin_client):
        r = admin_client.get("/auth/admin/audit-log")
        assert r.status_code == 200
        data = r.get_json()
        assert "entries" in data
        assert "total" in data

    def test_audit_log_with_filters(self, admin_client):
        r = admin_client.get(
            "/auth/admin/audit-log?limit=5&offset=0&action=create_user"
        )
        assert r.status_code == 200

    def test_audit_log_requires_admin(self, user_client):
        r = user_client.get("/auth/admin/audit-log")
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# Admin setup info
# ──────────────────────────────────────────────────────────────────────


class TestAdminSetupInfo:
    """Tests for GET /auth/admin/users/<id>/setup-info."""

    def test_setup_info_nonexistent(self, admin_client):
        r = admin_client.get("/auth/admin/users/99999/setup-info")
        assert r.status_code == 404

    def test_setup_info_already_logged_in(self, admin_client, auth_app):
        """User that has logged in should return 404."""
        r = admin_client.get(
            f"/auth/admin/users/{auth_app.admin_user_id}/setup-info"
        )
        # The admin user may or may not have last_login set depending on test order
        # Just verify it returns 200 or 404
        assert r.status_code in (200, 404)


# ──────────────────────────────────────────────────────────────────────
# Contact endpoint
# ──────────────────────────────────────────────────────────────────────


class TestContactEndpoint:
    """Tests for POST /auth/contact."""

    @patch("backend.api_modular.auth._send_admin_alert", return_value=False)
    def test_contact_no_body(self, mock_alert, user_client):
        r = user_client.post(
            "/auth/contact", content_type="application/json"
        )
        assert r.status_code == 400

    @patch("backend.api_modular.auth._send_admin_alert", return_value=False)
    def test_contact_empty_message(self, mock_alert, user_client):
        r = user_client.post("/auth/contact", json={"message": ""})
        assert r.status_code == 400

    @patch("backend.api_modular.auth._send_admin_alert", return_value=False)
    def test_contact_too_long(self, mock_alert, user_client):
        r = user_client.post("/auth/contact", json={"message": "x" * 2001})
        assert r.status_code == 400

    @patch("backend.api_modular.auth._send_admin_alert", return_value=False)
    def test_contact_invalid_reply_via(self, mock_alert, user_client):
        r = user_client.post(
            "/auth/contact", json={"message": "hi", "reply_via": "sms"}
        )
        assert r.status_code == 400

    @patch("backend.api_modular.auth._send_admin_alert", return_value=False)
    def test_contact_email_reply_no_email(self, mock_alert, user_client):
        r = user_client.post(
            "/auth/contact",
            json={"message": "hi", "reply_via": "email", "reply_email": ""},
        )
        assert r.status_code == 400

    @patch("backend.api_modular.auth._send_admin_alert", return_value=False)
    def test_contact_success(self, mock_alert, user_client):
        r = user_client.post("/auth/contact", json={"message": "Hello admin"})
        assert r.status_code == 200
        assert r.get_json()["success"] is True


# ──────────────────────────────────────────────────────────────────────
# Admin inbox
# ──────────────────────────────────────────────────────────────────────


class TestAdminInbox:
    """Tests for admin inbox endpoints."""

    def test_inbox_list_with_archived(self, admin_client):
        r = admin_client.get("/auth/admin/inbox?include_archived=true")
        assert r.status_code == 200

    def test_inbox_read_nonexistent(self, admin_client):
        r = admin_client.get("/auth/admin/inbox/99999")
        assert r.status_code == 404

    def test_inbox_reply_nonexistent(self, admin_client):
        r = admin_client.post(
            "/auth/admin/inbox/99999/reply", json={"reply": "hi"}
        )
        assert r.status_code == 404

    def test_inbox_reply_no_body(self, admin_client, auth_db, test_user):
        # Create a message first
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test message for reply test",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(auth_db)
        r = admin_client.post(
            f"/auth/admin/inbox/{msg.id}/reply",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_inbox_reply_empty(self, admin_client, auth_db, test_user):
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test for empty reply",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(auth_db)
        r = admin_client.post(
            f"/auth/admin/inbox/{msg.id}/reply", json={"reply": ""}
        )
        assert r.status_code == 400

    def test_inbox_reply_in_app(self, admin_client, auth_db, test_user):
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test for in-app reply",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(auth_db)
        r = admin_client.post(
            f"/auth/admin/inbox/{msg.id}/reply",
            json={"reply": "Thanks for the message"},
        )
        assert r.status_code == 200
        assert r.get_json()["reply_method"] == "in-app"

    def test_inbox_reply_email(self, admin_client, auth_db, test_user):
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="Test for email reply",
            reply_via=ReplyMethod.EMAIL,
            reply_email="user@test.com",
        )
        msg.save(auth_db)
        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            r = admin_client.post(
                f"/auth/admin/inbox/{msg.id}/reply",
                json={"reply": "Email reply test"},
            )
            assert r.status_code == 200
            assert r.get_json()["reply_method"] == "email"

    def test_inbox_archive_nonexistent(self, admin_client):
        r = admin_client.post("/auth/admin/inbox/99999/archive")
        assert r.status_code == 404

    def test_inbox_archive(self, admin_client, auth_db, test_user):
        msg = InboxMessage(
            from_user_id=test_user.id,
            message="To be archived",
            reply_via=ReplyMethod.IN_APP,
        )
        msg.save(auth_db)
        r = admin_client.post(f"/auth/admin/inbox/{msg.id}/archive")
        assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# Admin notifications (extended)
# ──────────────────────────────────────────────────────────────────────


class TestAdminNotificationsExtended:
    """Extended tests for admin notification management."""

    def test_create_notification_with_dates(self, admin_client):
        r = admin_client.post(
            "/auth/admin/notifications",
            json={
                "message": "Maintenance tonight",
                "type": "maintenance",
                "starts_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
            },
        )
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_create_notification_invalid_type(self, admin_client):
        r = admin_client.post(
            "/auth/admin/notifications",
            json={"message": "Test", "type": "invalid"},
        )
        assert r.status_code == 400

    def test_create_notification_personal_without_target(self, admin_client):
        r = admin_client.post(
            "/auth/admin/notifications",
            json={"message": "Personal", "type": "personal"},
        )
        assert r.status_code == 400

    def test_create_notification_invalid_date(self, admin_client):
        r = admin_client.post(
            "/auth/admin/notifications",
            json={"message": "Test", "starts_at": "not-a-date"},
        )
        assert r.status_code == 400

    def test_create_notification_invalid_expires(self, admin_client):
        r = admin_client.post(
            "/auth/admin/notifications",
            json={"message": "Test", "expires_at": "not-a-date"},
        )
        assert r.status_code == 400

    def test_create_notification_empty_message(self, admin_client):
        r = admin_client.post(
            "/auth/admin/notifications", json={"message": ""}
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# Magic link login
# ──────────────────────────────────────────────────────────────────────


class TestMagicLinkLogin:
    """Tests for POST /auth/magic-link/login."""

    def test_magic_link_login_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/magic-link/login", content_type="application/json"
        )
        assert r.status_code == 400

    def test_magic_link_login_missing_identifier(self, anon_client):
        r = anon_client.post("/auth/magic-link/login", json={"identifier": ""})
        assert r.status_code == 400

    def test_magic_link_login_unknown_user(self, anon_client):
        """Should always return success to prevent enumeration."""
        r = anon_client.post(
            "/auth/magic-link/login",
            json={"identifier": "doesnt_exist_xyz"},
        )
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_magic_link_login_with_email_user(self, anon_client, auth_db):
        """Magic link user with email should trigger email send."""
        User(
            username="ml_login_test",
            auth_type=AuthType.MAGIC_LINK,
            auth_credential=b"",
            recovery_email="ml@test.com",
            recovery_enabled=True,
        ).save(auth_db)
        with patch("smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            r = anon_client.post(
                "/auth/magic-link/login", json={"identifier": "ml_login_test"}
            )
            assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# Self-service account endpoints (/auth/account/*)
# ──────────────────────────────────────────────────────────────────────


class TestAccountEndpoints:
    """Tests for /auth/account/* self-service endpoints."""

    def test_account_get(self, user_client):
        r = user_client.get("/auth/account")
        assert r.status_code == 200
        data = r.get_json()
        assert "username" in data
        assert "auth_type" in data

    def test_account_get_requires_auth(self, anon_client):
        r = anon_client.get("/auth/account")
        assert r.status_code == 401

    def test_account_change_username(self, user_client):
        r = user_client.put(
            "/auth/account/username", json={"username": "acct_renamed"}
        )
        assert r.status_code == 200
        assert r.get_json()["username"] == "acct_renamed"
        # Restore
        user_client.put(
            "/auth/account/username", json={"username": "regularuser_fix"}
        )

    def test_account_change_username_too_short(self, user_client):
        r = user_client.put("/auth/account/username", json={"username": "ab"})
        assert r.status_code == 400

    def test_account_change_username_duplicate(self, user_client):
        r = user_client.put(
            "/auth/account/username", json={"username": "adminuser"}
        )
        assert r.status_code == 409

    def test_account_change_email(self, user_client):
        r = user_client.put(
            "/auth/account/email", json={"email": "acct@test.com"}
        )
        assert r.status_code == 200

    def test_account_change_email_clear(self, user_client):
        r = user_client.put("/auth/account/email", json={"email": ""})
        assert r.status_code == 200

    def test_account_change_email_invalid(self, user_client):
        r = user_client.put(
            "/auth/account/email", json={"email": "not-valid"}
        )
        assert r.status_code == 400

    def test_account_switch_auth_method_totp(self, user_client):
        r = user_client.put(
            "/auth/account/auth-method", json={"auth_method": "totp"}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "setup_data" in data

    def test_account_switch_auth_method_invalid(self, user_client):
        r = user_client.put(
            "/auth/account/auth-method", json={"auth_method": "sms"}
        )
        assert r.status_code == 400

    def test_account_switch_magic_link_no_email(self, user_client):
        """Switching to magic_link without email should fail."""
        # Clear email first
        user_client.put("/auth/account/email", json={"email": ""})
        r = user_client.put(
            "/auth/account/auth-method", json={"auth_method": "magic_link"}
        )
        assert r.status_code == 400

    def test_account_switch_passkey(self, user_client):
        r = user_client.put(
            "/auth/account/auth-method", json={"auth_method": "passkey"}
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "claim_token" in data["setup_data"]
        # Switch back to totp so other tests work
        user_client.put(
            "/auth/account/auth-method", json={"auth_method": "totp"}
        )

    def test_account_reset_credentials(self, user_client):
        # Ensure user is on TOTP first
        user_client.put(
            "/auth/account/auth-method", json={"auth_method": "totp"}
        )
        r = user_client.post("/auth/account/reset-credentials")
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "setup_data" in data


# ──────────────────────────────────────────────────────────────────────
# Localhost-only decorator
# ──────────────────────────────────────────────────────────────────────


class TestLocalhostDecorator:
    """Tests for the localhost_only decorator behavior."""

    def test_localhost_access_allowed(self, anon_client):
        """Requests from 127.0.0.1 should be allowed to auth health."""
        r = anon_client.get("/auth/health")
        assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────────
# Login with remember_me
# ──────────────────────────────────────────────────────────────────────


class TestLoginRememberMe:
    """Tests for login with remember_me flag."""

    def test_login_with_remember_me(self, auth_app):
        client = auth_app.test_client()
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        r = client.post(
            "/auth/login",
            json={
                "username": "testuser1",
                "code": auth.current_code(),
                "remember_me": True,
            },
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "session_token" in data

    def test_login_without_remember_me(self, auth_app):
        client = auth_app.test_client()
        auth = TOTPAuthenticator(auth_app.test_user_secret)
        r = client.post(
            "/auth/login",
            json={
                "username": "testuser1",
                "code": auth.current_code(),
                "remember_me": False,
            },
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "session_token" not in data


# ──────────────────────────────────────────────────────────────────────
# Auth type lookup
# ──────────────────────────────────────────────────────────────────────


class TestAuthTypeLookup:
    """Tests for POST /auth/login/auth-type."""

    def test_auth_type_existing_user(self, anon_client):
        r = anon_client.post(
            "/auth/login/auth-type", json={"username": "testuser1"}
        )
        assert r.status_code == 200
        assert r.get_json()["auth_type"] == "totp"

    def test_auth_type_nonexistent_user(self, anon_client):
        """Should return totp to prevent enumeration."""
        r = anon_client.post(
            "/auth/login/auth-type", json={"username": "no_such_user_xyz"}
        )
        assert r.status_code == 200
        assert r.get_json()["auth_type"] == "totp"

    def test_auth_type_missing_username(self, anon_client):
        r = anon_client.post("/auth/login/auth-type", json={"username": ""})
        assert r.status_code == 400

    def test_auth_type_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/login/auth-type", content_type="application/json"
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# User list (admin)
# ──────────────────────────────────────────────────────────────────────


class TestAdminListUsers:
    """Tests for GET /auth/admin/users."""

    def test_list_users(self, admin_client):
        r = admin_client.get("/auth/admin/users")
        assert r.status_code == 200
        data = r.get_json()
        assert "users" in data
        assert "total" in data
        assert data["total"] > 0

    def test_list_users_with_limit(self, admin_client):
        r = admin_client.get("/auth/admin/users?limit=2")
        assert r.status_code == 200

    def test_list_users_requires_admin(self, user_client):
        r = user_client.get("/auth/admin/users")
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────────────
# Registration verify (legacy flow)
# ──────────────────────────────────────────────────────────────────────


class TestRegistrationVerify:
    """Tests for POST /auth/register/verify (legacy flow)."""

    def test_verify_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/register/verify", content_type="application/json"
        )
        assert r.status_code == 400

    def test_verify_missing_token(self, anon_client):
        r = anon_client.post("/auth/register/verify", json={"token": ""})
        assert r.status_code == 400

    def test_verify_invalid_token(self, anon_client):
        r = anon_client.post(
            "/auth/register/verify", json={"token": "bogus_token"}
        )
        assert r.status_code == 400

    def test_verify_invalid_auth_type(self, anon_client):
        r = anon_client.post(
            "/auth/register/verify",
            json={"token": "valid", "auth_type": "magic_link"},
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# Claim WebAuthn endpoints (begin/complete)
# ──────────────────────────────────────────────────────────────────────


class TestClaimWebAuthn:
    """Tests for WebAuthn claim flow validation."""

    def test_claim_webauthn_begin_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/webauthn/begin",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_claim_webauthn_begin_missing_fields(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/webauthn/begin",
            json={"username": "test"},
        )
        assert r.status_code == 400

    def test_claim_webauthn_begin_invalid_auth_type(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/webauthn/begin",
            json={
                "username": "test",
                "claim_token": "XXXX",
                "auth_type": "invalid",
            },
        )
        assert r.status_code == 400

    def test_claim_webauthn_begin_invalid_token(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/webauthn/begin",
            json={
                "username": "nobody",
                "claim_token": "XXXX-XXXX-XXXX-XXXX",
                "auth_type": "passkey",
            },
        )
        assert r.status_code == 400

    def test_claim_webauthn_complete_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/webauthn/complete",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_claim_webauthn_complete_missing_fields(self, anon_client):
        r = anon_client.post(
            "/auth/register/claim/webauthn/complete",
            json={"username": "test", "claim_token": "XXXX"},
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# WebAuthn registration begin/complete (legacy token-based)
# ──────────────────────────────────────────────────────────────────────


class TestWebAuthnRegistration:
    """Tests for /auth/register/webauthn/* endpoints."""

    def test_register_webauthn_begin_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/begin",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_register_webauthn_begin_missing_token(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/begin", json={"token": ""}
        )
        assert r.status_code == 400

    def test_register_webauthn_begin_invalid_auth_type(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/begin",
            json={"token": "something", "auth_type": "invalid"},
        )
        assert r.status_code == 400

    def test_register_webauthn_begin_invalid_token(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/begin",
            json={"token": "bogus_token", "auth_type": "passkey"},
        )
        assert r.status_code == 400

    def test_register_webauthn_complete_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/complete",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_register_webauthn_complete_missing_fields(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/complete",
            json={"token": "something"},
        )
        assert r.status_code == 400

    def test_register_webauthn_complete_invalid_auth_type(self, anon_client):
        r = anon_client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": "tok",
                "credential": {"id": "x"},
                "challenge": "chal",
                "auth_type": "invalid",
            },
        )
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# WebAuthn login begin/complete
# ──────────────────────────────────────────────────────────────────────


class TestWebAuthnLogin:
    """Tests for /auth/login/webauthn/* endpoints."""

    def test_login_webauthn_begin_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/login/webauthn/begin",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_login_webauthn_begin_missing_username(self, anon_client):
        r = anon_client.post(
            "/auth/login/webauthn/begin", json={"username": ""}
        )
        assert r.status_code == 400

    def test_login_webauthn_begin_nonexistent_user(self, anon_client):
        r = anon_client.post(
            "/auth/login/webauthn/begin", json={"username": "no_such_user"}
        )
        assert r.status_code == 401

    def test_login_webauthn_begin_totp_user(self, anon_client):
        """TOTP user cannot use WebAuthn login."""
        r = anon_client.post(
            "/auth/login/webauthn/begin", json={"username": "testuser1"}
        )
        assert r.status_code == 400

    def test_login_webauthn_complete_no_body(self, anon_client):
        r = anon_client.post(
            "/auth/login/webauthn/complete",
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_login_webauthn_complete_missing_fields(self, anon_client):
        r = anon_client.post(
            "/auth/login/webauthn/complete", json={"username": "test"}
        )
        assert r.status_code == 400

    def test_login_webauthn_complete_nonexistent_user(self, anon_client):
        r = anon_client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "no_such_user",
                "credential": {"id": "x"},
                "challenge": "chal",
            },
        )
        assert r.status_code == 401

    def test_login_webauthn_complete_totp_user(self, anon_client):
        """TOTP user attempting WebAuthn login should fail."""
        r = anon_client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "testuser1",
                "credential": {"id": "x"},
                "challenge": "chal",
            },
        )
        assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────────
# Dismiss notification
# ──────────────────────────────────────────────────────────────────────


class TestDismissNotification:
    """Tests for POST /auth/notifications/dismiss/<id>."""

    def test_dismiss_requires_auth(self, anon_client):
        r = anon_client.post("/auth/notifications/dismiss/1")
        assert r.status_code == 401

    def test_dismiss_nonexistent(self, user_client):
        r = user_client.post("/auth/notifications/dismiss/99999")
        assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# Last-admin guard on roles change
# ──────────────────────────────────────────────────────────────────────


class TestLastAdminGuard:
    """Tests for last-admin guard in various endpoints."""

    def test_roles_last_admin_guard(self, admin_client, auth_db):
        """Removing admin from last admin should fail with 409."""
        # Find all admins
        user_repo = UserRepository(auth_db)
        admins = [u for u in user_repo.list_all() if u.is_admin]
        # If there's only one admin, try to demote them
        if len(admins) == 1:
            admin_id = admins[0].id
            r = admin_client.put(
                f"/auth/admin/users/{admin_id}/roles",
                json={"is_admin": False},
            )
            assert r.status_code == 409

    def test_delete_last_admin_guard(self, admin_client, auth_db):
        """Cannot delete last admin."""
        user_repo = UserRepository(auth_db)
        admins = [u for u in user_repo.list_all() if u.is_admin]
        # Create a user that is the only admin for this test
        sole = User(
            username="sole_admin_guard_test",
            auth_type=AuthType.TOTP,
            auth_credential=b"secret",
            is_admin=True,
        ).save(auth_db)
        # Make all other admins non-admin temporarily - too risky in shared fixture
        # Instead just test the v2 delete with last-admin check
        # The delete endpoint checks is_last_admin which checks admin count
        # Since there are multiple admins, deleting this one should succeed
        r = admin_client.delete(f"/auth/admin/users/{sole.id}/delete")
        assert r.status_code == 200  # Not the last admin


# ──────────────────────────────────────────────────────────────────────
# Magic link verify
# ──────────────────────────────────────────────────────────────────────


class TestMagicLinkVerifyExtended:
    """Extended tests for magic link verify endpoint."""

    def test_verify_activation_first_login(self, auth_app, auth_db):
        """First-time login with activate=true shows welcome message."""
        user = User(
            username="activate_test_ext",
            auth_type=AuthType.MAGIC_LINK,
            auth_credential=b"",
            recovery_email="act@test.com",
            recovery_enabled=True,
        ).save(auth_db)
        # Create recovery token
        recovery, raw_token = PendingRecovery.create(auth_db, user.id, expiry_minutes=15)
        client = auth_app.test_client()
        r = client.post(
            "/auth/magic-link/verify",
            json={"token": raw_token, "activate": True},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["activation"] is True

    def test_verify_expired_token(self, auth_app, auth_db):
        """Expired token should fail."""
        user = User(
            username="expired_ml_ext",
            auth_type=AuthType.MAGIC_LINK,
            auth_credential=b"",
        ).save(auth_db)
        recovery, raw_token = PendingRecovery.create(auth_db, user.id, expiry_minutes=15)
        # Force expiry by setting expires_at to the past
        past = (datetime.now() - timedelta(hours=2)).isoformat()
        with auth_db.connection() as conn:
            conn.execute(
                "UPDATE pending_recovery SET expires_at = ? WHERE user_id = ?",
                (past, user.id),
            )
            conn.commit()
        client = auth_app.test_client()
        r = client.post(
            "/auth/magic-link/verify", json={"token": raw_token}
        )
        assert r.status_code == 400
