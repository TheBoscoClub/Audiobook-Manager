"""Tests for admin user management endpoints."""

import json
import re


class TestAdminCreateUser:
    """Tests for POST /auth/admin/users/create."""

    def test_create_totp_user(self, admin_client):
        """Creating a TOTP user returns 201 with setup data."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "newtotp",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        assert "user_id" in data
        assert isinstance(data["user_id"], int)

        setup = data["setup_data"]
        assert "secret" in setup
        assert "qr_uri" in setup
        assert "manual_key" in setup
        # base32 secret should be non-empty alphanumeric
        assert len(setup["secret"]) > 0
        # provisioning URI should be otpauth://
        assert setup["qr_uri"].startswith("otpauth://totp/")
        # manual_key is same as secret
        assert setup["manual_key"] == setup["secret"]

    def test_create_magic_link_user_requires_email(self, admin_client):
        """Creating a magic_link user without email returns 400."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "newml",
                "auth_method": "magic_link",
                "is_admin": False,
                "can_download": False,
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "email" in data["error"].lower()

    def test_create_magic_link_user_with_email(self, admin_client):
        """Creating a magic_link user with email succeeds."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "newml2",
                "auth_method": "magic_link",
                "email": "newml2@example.com",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        assert "user_id" in data
        # magic_link setup_data is empty
        assert data["setup_data"] == {}

    def test_create_passkey_user_gets_claim_url(self, admin_client):
        """Creating a passkey user returns claim token and URL."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "newpasskey",
                "auth_method": "passkey",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True
        assert "user_id" in data

        setup = data["setup_data"]
        assert "claim_token" in setup
        assert "claim_url" in setup
        assert "expires_at" in setup
        # claim_token is formatted XXXX-XXXX-XXXX-XXXX
        assert re.match(r"^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$", setup["claim_token"])

    def test_create_duplicate_username_fails(self, admin_client):
        """Creating a user with an existing username returns 409."""
        # First create
        admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "dupuser",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        # Second create with same username
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "dupuser",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 409
        assert "already" in resp.get_json()["error"].lower()

    def test_create_user_logs_audit_entry(self, admin_client, auth_db):
        """Creating a user creates an audit log entry."""
        from auth.audit import AuditLogRepository

        audit_repo = AuditLogRepository(auth_db)

        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "auditeduser",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        user_id = resp.get_json()["user_id"]

        # Check audit log has create_user entry for this user
        entries = audit_repo.list(action_filter="create_user", user_filter=user_id)
        assert len(entries) >= 1
        entry = entries[0]
        assert entry.action == "create_user"
        assert entry.target_id == user_id
        details = (
            json.loads(entry.details)
            if isinstance(entry.details, str)
            else entry.details
        )
        assert details["auth_method"] == "totp"
        assert details["target_username"] == "auditeduser"

    def test_create_user_requires_admin(self, user_client):
        """Non-admin user gets 403 when trying to create a user."""
        resp = user_client.post(
            "/auth/admin/users/create",
            json={
                "username": "noperm",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 403

    def test_username_too_short(self, admin_client):
        """Username under 3 characters returns 400."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "ab",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 400
        assert "3" in resp.get_json()["error"]

    def test_username_too_long(self, admin_client):
        """Username over 24 characters returns 400."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "a" * 25,
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 400
        assert "24" in resp.get_json()["error"]

    def test_username_invalid_chars(self, admin_client):
        """Username with special characters returns 400."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "bad user!",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 400

    def test_invalid_auth_method(self, admin_client):
        """Invalid auth_method returns 400."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "badmethod",
                "auth_method": "sms",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 400
        assert "auth_method" in resp.get_json()["error"].lower()

    def test_create_admin_user(self, admin_client):
        """Creating a user with is_admin=True succeeds."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "newadmin",
                "auth_method": "totp",
                "is_admin": True,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["success"] is True

    def test_unauthenticated_returns_401(self, anon_client):
        """Unauthenticated request returns 401."""
        resp = anon_client.post(
            "/auth/admin/users/create",
            json={
                "username": "anontest",
                "auth_method": "totp",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 401
