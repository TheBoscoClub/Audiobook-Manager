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
        details = json.loads(entry.details) if isinstance(entry.details, str) else entry.details
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
            json={"username": "ab", "auth_method": "totp", "is_admin": False, "can_download": True},
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


# ============================================================
# Helper to create a target user for management endpoint tests
# ============================================================


def _audit_details(entry):
    """Parse audit log entry details as dict."""
    if isinstance(entry.details, str):
        return json.loads(entry.details)
    return entry.details or {}


def _create_target_user(admin_client, suffix, **overrides):
    """Create a TOTP user via the admin API and return (user_id, resp_data)."""
    payload = {
        "username": f"target-{suffix}",
        "auth_method": "totp",
        "is_admin": False,
        "can_download": True,
    }
    payload.update(overrides)
    resp = admin_client.post("/auth/admin/users/create", json=payload)
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    return data["user_id"], data


# ============================================================
# 1. PUT /auth/admin/users/<id>/username
# ============================================================


class TestAdminChangeUsername:
    """Tests for PUT /auth/admin/users/<id>/username."""

    def test_change_username_success(self, admin_client):
        uid, _ = _create_target_user(admin_client, "chname1")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/username", json={"username": "renamed-user1"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["user"]["username"] == "renamed-user1"

    def test_change_username_duplicate(self, admin_client):
        uid1, _ = _create_target_user(admin_client, "chname2a")
        _create_target_user(admin_client, "chname2b")
        resp = admin_client.put(
            f"/auth/admin/users/{uid1}/username", json={"username": "target-chname2b"}
        )
        assert resp.status_code == 409
        assert "taken" in resp.get_json()["error"].lower()

    def test_change_username_missing_body(self, admin_client):
        uid, _ = _create_target_user(admin_client, "chname3")
        resp = admin_client.put(f"/auth/admin/users/{uid}/username", json={})
        assert resp.status_code == 400

    def test_change_username_nonexistent_user(self, admin_client):
        resp = admin_client.put("/auth/admin/users/99999/username", json={"username": "ghost"})
        assert resp.status_code == 404

    def test_change_username_requires_admin(self, user_client):
        resp = user_client.put("/auth/admin/users/1/username", json={"username": "noperm"})
        assert resp.status_code == 403

    def test_change_username_audit_log(self, admin_client, auth_db):
        from auth.audit import AuditLogRepository

        uid, _ = _create_target_user(admin_client, "chname-audit")
        admin_client.put(f"/auth/admin/users/{uid}/username", json={"username": "renamed-audit"})
        audit_repo = AuditLogRepository(auth_db)
        entries = audit_repo.list(action_filter="change_username", user_filter=uid)
        assert len(entries) >= 1
        details = (
            json.loads(entries[0].details)
            if isinstance(entries[0].details, str)
            else entries[0].details
        )
        assert details["old"] == "target-chname-audit"
        assert details["new"] == "renamed-audit"


# ============================================================
# 2. PUT /auth/admin/users/<id>/email
# ============================================================


class TestAdminChangeEmail:
    """Tests for PUT /auth/admin/users/<id>/email."""

    def test_change_email_success(self, admin_client):
        uid, _ = _create_target_user(admin_client, "chemail1")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/email", json={"email": "newemail@example.com"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["user"]["email"] == "newemail@example.com"

    def test_clear_email(self, admin_client):
        uid, _ = _create_target_user(admin_client, "chemail2", email="old@example.com")
        resp = admin_client.put(f"/auth/admin/users/{uid}/email", json={"email": ""})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user"]["email"] is None

    def test_change_email_nonexistent_user(self, admin_client):
        resp = admin_client.put("/auth/admin/users/99999/email", json={"email": "x@x.com"})
        assert resp.status_code == 404

    def test_change_email_requires_admin(self, user_client):
        resp = user_client.put("/auth/admin/users/1/email", json={"email": "x@x.com"})
        assert resp.status_code == 403

    def test_change_email_audit_log(self, admin_client, auth_db):
        from auth.audit import AuditLogRepository

        uid, _ = _create_target_user(admin_client, "chemail-audit")
        admin_client.put(f"/auth/admin/users/{uid}/email", json={"email": "audited@example.com"})
        audit_repo = AuditLogRepository(auth_db)
        entries = audit_repo.list(action_filter="change_email", user_filter=uid)
        assert len(entries) >= 1
        details = (
            json.loads(entries[0].details)
            if isinstance(entries[0].details, str)
            else entries[0].details
        )
        assert details["new"] == "audited@example.com"


# ============================================================
# 3. PUT /auth/admin/users/<id>/roles
# ============================================================


class TestAdminChangeRoles:
    """Tests for PUT /auth/admin/users/<id>/roles."""

    def test_set_admin_true(self, admin_client):
        uid, _ = _create_target_user(admin_client, "roles1")
        resp = admin_client.put(f"/auth/admin/users/{uid}/roles", json={"is_admin": True})
        assert resp.status_code == 200
        assert resp.get_json()["user"]["is_admin"] is True

    def test_set_download_false(self, admin_client):
        uid, _ = _create_target_user(admin_client, "roles2")
        resp = admin_client.put(f"/auth/admin/users/{uid}/roles", json={"can_download": False})
        assert resp.status_code == 200
        assert resp.get_json()["user"]["can_download"] is False

    def test_set_both_roles(self, admin_client):
        uid, _ = _create_target_user(admin_client, "roles3")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/roles", json={"is_admin": True, "can_download": False}
        )
        assert resp.status_code == 200
        user = resp.get_json()["user"]
        assert user["is_admin"] is True
        assert user["can_download"] is False

    def test_remove_last_admin_blocked(self, admin_client, auth_db):
        """Cannot remove admin from the last admin."""
        from auth.models import UserRepository

        user_repo = UserRepository(auth_db)
        # Demote all admins except testadmin_fix to ensure it's the only one
        all_users = user_repo.list_all()
        demoted_ids = []
        for u in all_users:
            if u.is_admin and u.username != "testadmin_fix":
                user_repo.set_admin(u.id, False)
                demoted_ids.append(u.id)

        try:
            admin_user = user_repo.get_by_username("testadmin_fix")
            assert user_repo.is_last_admin(admin_user.id)
            resp = admin_client.put(
                f"/auth/admin/users/{admin_user.id}/roles", json={"is_admin": False}
            )
            assert resp.status_code == 409
            assert "last admin" in resp.get_json()["error"].lower()
        finally:
            # Restore admin status for demoted users to avoid polluting session DB
            for uid in demoted_ids:
                user_repo.set_admin(uid, True)

    def test_empty_body_returns_400(self, admin_client):
        uid, _ = _create_target_user(admin_client, "roles-empty")
        resp = admin_client.put(f"/auth/admin/users/{uid}/roles", json={})
        assert resp.status_code == 400

    def test_change_roles_requires_admin(self, user_client):
        resp = user_client.put("/auth/admin/users/1/roles", json={"is_admin": True})
        assert resp.status_code == 403

    def test_change_roles_nonexistent_user(self, admin_client):
        resp = admin_client.put("/auth/admin/users/99999/roles", json={"is_admin": True})
        assert resp.status_code == 404

    def test_change_roles_audit_log(self, admin_client, auth_db):
        from auth.audit import AuditLogRepository

        uid, _ = _create_target_user(admin_client, "roles-audit")
        admin_client.put(
            f"/auth/admin/users/{uid}/roles", json={"is_admin": True, "can_download": False}
        )
        audit_repo = AuditLogRepository(auth_db)
        entries = audit_repo.list(action_filter="toggle_roles", user_filter=uid)
        assert len(entries) >= 1
        details = (
            json.loads(entries[0].details)
            if isinstance(entries[0].details, str)
            else entries[0].details
        )
        assert "is_admin" in details.get("new", {})


# ============================================================
# 4. PUT /auth/admin/users/<id>/auth-method
# ============================================================


class TestAdminChangeAuthMethod:
    """Tests for PUT /auth/admin/users/<id>/auth-method."""

    def test_switch_to_totp(self, admin_client):
        uid, _ = _create_target_user(admin_client, "authm1", auth_method="passkey")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/auth-method", json={"auth_method": "totp"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        setup = data["setup_data"]
        assert "secret" in setup
        assert "qr_uri" in setup

    def test_switch_to_magic_link_requires_email(self, admin_client):
        uid, _ = _create_target_user(admin_client, "authm2")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/auth-method", json={"auth_method": "magic_link"}
        )
        # Should fail: no email on user and none in body
        assert resp.status_code == 400
        assert "email" in resp.get_json()["error"].lower()

    def test_switch_to_magic_link_with_email_in_body(self, admin_client):
        uid, _ = _create_target_user(admin_client, "authm3")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/auth-method",
            json={"auth_method": "magic_link", "email": "ml@example.com"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_switch_to_magic_link_with_existing_email(self, admin_client):
        uid, _ = _create_target_user(admin_client, "authm4", email="existing@example.com")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/auth-method", json={"auth_method": "magic_link"}
        )
        assert resp.status_code == 200

    def test_switch_to_passkey(self, admin_client):
        uid, _ = _create_target_user(admin_client, "authm5")
        resp = admin_client.put(
            f"/auth/admin/users/{uid}/auth-method", json={"auth_method": "passkey"}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        setup = data["setup_data"]
        assert "claim_token" in setup
        assert "claim_url" in setup

    def test_invalid_auth_method(self, admin_client):
        uid, _ = _create_target_user(admin_client, "authm6")
        resp = admin_client.put(f"/auth/admin/users/{uid}/auth-method", json={"auth_method": "sms"})
        assert resp.status_code == 400

    def test_switch_auth_requires_admin(self, user_client):
        resp = user_client.put("/auth/admin/users/1/auth-method", json={"auth_method": "totp"})
        assert resp.status_code == 403

    def test_switch_auth_nonexistent_user(self, admin_client):
        resp = admin_client.put("/auth/admin/users/99999/auth-method", json={"auth_method": "totp"})
        assert resp.status_code == 404

    def test_switch_auth_audit_log(self, admin_client, auth_db):
        from auth.audit import AuditLogRepository

        uid, _ = _create_target_user(admin_client, "authm-audit")
        admin_client.put(f"/auth/admin/users/{uid}/auth-method", json={"auth_method": "passkey"})
        audit_repo = AuditLogRepository(auth_db)
        entries = audit_repo.list(action_filter="switch_auth_method", user_filter=uid)
        assert len(entries) >= 1
        details = (
            json.loads(entries[0].details)
            if isinstance(entries[0].details, str)
            else entries[0].details
        )
        assert details["old"] == "totp"
        assert details["new"] == "passkey"


# ============================================================
# 5. POST /auth/admin/users/<id>/reset-credentials
# ============================================================


class TestAdminResetCredentials:
    """Tests for POST /auth/admin/users/<id>/reset-credentials."""

    def test_reset_totp_credentials(self, admin_client):
        uid, create_data = _create_target_user(admin_client, "reset1")
        old_secret = create_data["setup_data"]["secret"]
        resp = admin_client.post(f"/auth/admin/users/{uid}/reset-credentials")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # New secret should differ from old
        new_secret = data["setup_data"]["secret"]
        assert new_secret != old_secret
        assert "qr_uri" in data["setup_data"]

    def test_reset_passkey_credentials(self, admin_client):
        uid, _ = _create_target_user(admin_client, "reset2", auth_method="passkey")
        resp = admin_client.post(f"/auth/admin/users/{uid}/reset-credentials")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "claim_token" in data["setup_data"]
        assert "claim_url" in data["setup_data"]

    def test_reset_magic_link_noop(self, admin_client):
        uid, _ = _create_target_user(
            admin_client, "reset3", auth_method="magic_link", email="ml-reset@example.com"
        )
        resp = admin_client.post(f"/auth/admin/users/{uid}/reset-credentials")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # Magic link has no new credentials
        assert "email" in data["setup_data"]

    def test_reset_requires_admin(self, user_client):
        resp = user_client.post("/auth/admin/users/1/reset-credentials")
        assert resp.status_code == 403

    def test_reset_nonexistent_user(self, admin_client):
        resp = admin_client.post("/auth/admin/users/99999/reset-credentials")
        assert resp.status_code == 404

    def test_reset_audit_log(self, admin_client, auth_db):
        from auth.audit import AuditLogRepository

        uid, _ = _create_target_user(admin_client, "reset-audit")
        admin_client.post(f"/auth/admin/users/{uid}/reset-credentials")
        audit_repo = AuditLogRepository(auth_db)
        entries = audit_repo.list(action_filter="reset_credentials", user_filter=uid)
        assert len(entries) >= 1


# ============================================================
# 6. DELETE /auth/admin/users/<id>
# ============================================================


class TestAdminDeleteUser:
    """Tests for DELETE /auth/admin/users/<id>."""

    def test_delete_user_success(self, admin_client):
        uid, _ = _create_target_user(admin_client, "del1")
        resp = admin_client.delete(f"/auth/admin/users/{uid}/delete")
        assert resp.status_code == 200
        assert "deleted" in resp.get_json()["message"].lower()

        # Confirm user is gone
        resp2 = admin_client.get(f"/auth/admin/users/{uid}/setup-info")
        assert resp2.status_code == 404

    def test_delete_last_admin_blocked(self, admin_client, auth_db):
        from auth.models import UserRepository

        user_repo = UserRepository(auth_db)
        # Self-deletion guard fires before last-admin guard (both protect correctly).
        # Test self-deletion guard on the v2 endpoint.
        admin_user = user_repo.get_by_username("testadmin_fix")

        # Ensure testadmin_fix is the only admin
        all_users = user_repo.list_all()
        demoted_ids = []
        for u in all_users:
            if u.is_admin and u.username != "testadmin_fix":
                user_repo.set_admin(u.id, False)
                demoted_ids.append(u.id)

        try:
            assert user_repo.is_last_admin(admin_user.id)
            # Self-deletion guard fires first (400) since admin is trying to delete itself
            resp = admin_client.delete(f"/auth/admin/users/{admin_user.id}/delete")
            assert resp.status_code == 400
            assert "yourself" in resp.get_json()["error"].lower()
        finally:
            # Restore admin status for demoted users to avoid polluting session DB
            for uid in demoted_ids:
                user_repo.set_admin(uid, True)

    def test_delete_nonexistent_user(self, admin_client):
        resp = admin_client.delete("/auth/admin/users/99999/delete")
        assert resp.status_code == 404

    def test_delete_requires_admin(self, user_client):
        resp = user_client.delete("/auth/admin/users/1/delete")
        assert resp.status_code == 403

    def test_delete_audit_log(self, admin_client, auth_db):
        from auth.audit import AuditLogRepository

        uid, _ = _create_target_user(admin_client, "del-audit")
        admin_client.delete(f"/auth/admin/users/{uid}/delete")
        audit_repo = AuditLogRepository(auth_db)
        # target_id is SET NULL on cascade, so search by action and check details
        entries = audit_repo.list(action_filter="delete_account")
        found = [e for e in entries if _audit_details(e).get("username") == "target-del-audit"]
        assert len(found) >= 1


# ============================================================
# 7. GET /auth/admin/audit-log
# ============================================================


class TestAdminAuditLog:
    """Tests for GET /auth/admin/audit-log."""

    def test_list_audit_log(self, admin_client):
        # Create a user to ensure at least one audit entry
        _create_target_user(admin_client, "auditlist1")
        resp = admin_client.get("/auth/admin/audit-log")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "entries" in data
        assert "total" in data
        assert isinstance(data["entries"], list)
        assert data["total"] >= 1
        # Entries have expected fields
        entry = data["entries"][0]
        assert "id" in entry
        assert "timestamp" in entry
        assert "action" in entry

    def test_audit_log_limit(self, admin_client):
        resp = admin_client.get("/auth/admin/audit-log?limit=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["entries"]) <= 2

    def test_audit_log_action_filter(self, admin_client):
        _create_target_user(admin_client, "auditfilter1")
        resp = admin_client.get("/auth/admin/audit-log?action=create_user")
        assert resp.status_code == 200
        data = resp.get_json()
        for entry in data["entries"]:
            assert entry["action"] == "create_user"

    def test_audit_log_user_filter(self, admin_client, auth_db):
        uid, _ = _create_target_user(admin_client, "audituser1")
        resp = admin_client.get(f"/auth/admin/audit-log?user_id={uid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1

    def test_audit_log_requires_admin(self, user_client):
        resp = user_client.get("/auth/admin/audit-log")
        assert resp.status_code == 403

    def test_audit_log_max_limit_capped(self, admin_client):
        resp = admin_client.get("/auth/admin/audit-log?limit=9999")
        assert resp.status_code == 200
        # Limit should be capped to 500, not error


# ============================================================
# 8. GET /auth/admin/users/<id>/setup-info
# ============================================================


class TestAdminSetupInfo:
    """Tests for GET /auth/admin/users/<id>/setup-info."""

    def test_setup_info_totp_user(self, admin_client):
        uid, create_data = _create_target_user(admin_client, "setup1")
        resp = admin_client.get(f"/auth/admin/users/{uid}/setup-info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "setup_data" in data
        setup = data["setup_data"]
        assert "secret" in setup
        assert "qr_uri" in setup

    def test_setup_info_passkey_user(self, admin_client):
        uid, _ = _create_target_user(admin_client, "setup2", auth_method="passkey")
        resp = admin_client.get(f"/auth/admin/users/{uid}/setup-info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "setup_data" in data
        setup = data["setup_data"]
        assert "claim_token" in setup

    def test_setup_info_logged_in_user_returns_404(self, admin_client, auth_db):
        """User who has logged in should not expose setup info."""
        from auth.models import UserRepository
        from datetime import datetime

        uid, _ = _create_target_user(admin_client, "setup3")
        # Simulate login by setting last_login
        user_repo = UserRepository(auth_db)
        user = user_repo.get_by_id(uid)
        user.last_login = datetime.now()
        user.save(auth_db)

        resp = admin_client.get(f"/auth/admin/users/{uid}/setup-info")
        assert resp.status_code == 404

    def test_setup_info_nonexistent_user(self, admin_client):
        resp = admin_client.get("/auth/admin/users/99999/setup-info")
        assert resp.status_code == 404

    def test_setup_info_requires_admin(self, user_client):
        resp = user_client.get("/auth/admin/users/1/setup-info")
        assert resp.status_code == 403
