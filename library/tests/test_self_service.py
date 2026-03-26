"""Tests for user self-service account endpoints (/auth/account/*)."""


class TestGetAccount:
    def test_get_own_profile(self, user_client, test_user):
        resp = user_client.get("/auth/account")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["username"] == test_user.username
        assert "auth_type" in data

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.get("/auth/account")
        assert resp.status_code == 401


class TestChangeOwnUsername:
    def test_change_own_username(self, user_client):
        resp = user_client.put("/auth/account/username", json={"username": "mynewname"})
        assert resp.status_code == 200
        assert resp.get_json()["username"] == "mynewname"

    def test_change_username_triggers_audit(self, user_client, auth_db):
        user_client.put("/auth/account/username", json={"username": "audited"})
        from auth.audit import AuditLogRepository

        entries = AuditLogRepository(auth_db).list(action_filter="change_username")
        assert len(entries) >= 1

    def test_change_username_empty_rejected(self, user_client):
        resp = user_client.put("/auth/account/username", json={"username": ""})
        assert resp.status_code == 400

    def test_change_username_duplicate_rejected(self, user_client, admin_client):
        # admin user is "testadmin_fix"
        resp = user_client.put(
            "/auth/account/username", json={"username": "testadmin_fix"}
        )
        assert resp.status_code == 409

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.put("/auth/account/username", json={"username": "x"})
        assert resp.status_code == 401


class TestChangeOwnEmail:
    def test_change_own_email(self, user_client):
        resp = user_client.put("/auth/account/email", json={"email": "me@new.com"})
        assert resp.status_code == 200

    def test_clear_email(self, user_client):
        resp = user_client.put("/auth/account/email", json={"email": ""})
        assert resp.status_code == 200

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.put("/auth/account/email", json={"email": "x@x.com"})
        assert resp.status_code == 401


class TestSwitchOwnAuth:
    def test_initiate_switch_to_totp(self, magic_link_user_client):
        resp = magic_link_user_client.put(
            "/auth/account/auth-method", json={"auth_method": "totp"}
        )
        assert resp.status_code == 200
        assert "setup_data" in resp.get_json()

    def test_invalid_method_rejected(self, user_client):
        resp = user_client.put(
            "/auth/account/auth-method", json={"auth_method": "invalid"}
        )
        assert resp.status_code == 400

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.put(
            "/auth/account/auth-method", json={"auth_method": "totp"}
        )
        assert resp.status_code == 401


class TestResetOwnCredentials:
    def test_reset_totp_credentials(self, user_client):
        resp = user_client.post("/auth/account/reset-credentials")
        assert resp.status_code == 200
        assert "setup_data" in resp.get_json()

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.post("/auth/account/reset-credentials")
        assert resp.status_code == 401


class TestDeleteOwnAccount:
    def test_delete_own_account(self, user_client, test_user, auth_db):
        resp = user_client.delete("/auth/account")
        assert resp.status_code == 200
        from auth.models import UserRepository

        assert UserRepository(auth_db).get_by_id(test_user.id) is None

    def test_delete_clears_session_cookie(self, user_client):
        resp = user_client.delete("/auth/account")
        assert resp.status_code == 200
        # The response should instruct the browser to clear the session cookie
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "audiobooks_session" in set_cookie

    def test_unauthenticated_gets_401(self, anon_client):
        resp = anon_client.delete("/auth/account")
        assert resp.status_code == 401
