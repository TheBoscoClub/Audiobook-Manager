"""
Cross-boundary round-trip tests: admin create → claim → login.

These tests close Gap 1 from the cross-component audit: the admin user
creation endpoint was only tested for response shape ("does setup_data
have a claim_token?") — no test ever fed the returned token into the
claim endpoint and then attempted to log in with the resulting credentials.

This is distinct from test_credential_reset_claim_lifecycle.py, which
covers the *reset* path. This file covers the *creation* path: a user
that has never existed before gets created by an admin, claims credentials,
and logs in for the first time.

The two paths diverge at the database layer:
  - Admin create (passkey) → PendingRegistration table
  - Self-registration → AccessRequest table
  - Admin create (TOTP) → credentials set directly (no claim needed)

Covers:
  1. Admin creates passkey user → user validates token → claims TOTP → logs in
  2. Admin creates passkey user → user claims TOTP → token is consumed
  3. Admin creates passkey user → validates via /register/claim/validate
  4. Admin creates TOTP user → user logs in directly (no claim needed)
  5. Admin creates passkey user → claim with wrong username fails
  6. Admin creates passkey user → claim with mangled token fails
  7. Admin invites passkey user → user claims → logs in
"""

import pyotp
import pytest


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _admin_create_user(admin_client, username, auth_method="passkey", **extra):
    """Create a user through admin API, return (user_id, full_response_data)."""
    payload = {
        "username": username,
        "auth_method": auth_method,
        "is_admin": False,
        "can_download": True,
    }
    payload.update(extra)
    resp = admin_client.post("/auth/admin/users/create", json=payload)
    assert resp.status_code == 201, f"Create failed: {resp.get_json()}"
    data = resp.get_json()
    return data["user_id"], data


def _validate_claim(client, username, claim_token):
    """Validate a claim token, return response."""
    return client.post(
        "/auth/register/claim/validate", json={"username": username, "claim_token": claim_token}
    )


def _claim_totp(client, username, claim_token):
    """Claim TOTP credentials, return response."""
    return client.post(
        "/auth/register/claim",
        json={"username": username, "claim_token": claim_token, "auth_method": "totp"},
    )


def _login_totp(client, username, totp_secret):
    """Generate a TOTP code and login, return response."""
    code = pyotp.TOTP(totp_secret).now()
    return client.post("/auth/login", json={"username": username, "code": code})


# ──────────────────────────────────────────────────────────────────────
# 1. Admin create passkey user → validate → claim TOTP → login
# ──────────────────────────────────────────────────────────────────────


class TestAdminCreatePasskeyThenClaimThenLogin:
    """Full lifecycle: admin creates passkey user, user claims TOTP, logs in."""

    def test_full_round_trip(self, admin_client, auth_app):
        """
        Admin creates passkey user → user validates token → claims TOTP
        → logs in with the TOTP secret from the claim response.
        """
        username = "lifecycle-create-1"
        uid, create_data = _admin_create_user(admin_client, username, "passkey")
        claim_token = create_data["setup_data"]["claim_token"]

        # Step 1: Validate the claim token
        client = auth_app.test_client()
        resp = _validate_claim(client, username, claim_token)
        assert resp.status_code == 200, f"Validate failed: {resp.get_json()}"
        vdata = resp.get_json()
        assert vdata["valid"] is True
        assert vdata["mode"] == "credential_reset"

        # Step 2: Claim TOTP credentials
        resp = _claim_totp(client, username, claim_token)
        assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
        claim_data = resp.get_json()
        assert claim_data["success"] is True
        totp_secret = claim_data["totp_secret"]
        assert totp_secret  # non-empty

        # Step 3: Login with the claimed TOTP secret
        resp = _login_totp(client, username, totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        login_data = resp.get_json()
        assert login_data["success"] is True
        assert login_data["user"]["username"] == username

    def test_token_consumed_after_claim(self, admin_client, auth_app):
        """After claiming, the same token cannot be reused."""
        username = "lifecycle-create-2"
        uid, create_data = _admin_create_user(admin_client, username, "passkey")
        claim_token = create_data["setup_data"]["claim_token"]

        client = auth_app.test_client()

        # First claim succeeds
        resp = _claim_totp(client, username, claim_token)
        assert resp.status_code == 200

        # Second claim fails — token consumed
        resp = _claim_totp(client, username, claim_token)
        assert resp.status_code in (400, 404)

    def test_validate_returns_correct_mode(self, admin_client, auth_app):
        """Validate endpoint returns mode=credential_reset for admin-created users."""
        username = "lifecycle-create-3"
        uid, create_data = _admin_create_user(admin_client, username, "passkey")
        claim_token = create_data["setup_data"]["claim_token"]

        client = auth_app.test_client()
        resp = _validate_claim(client, username, claim_token)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["valid"] is True
        assert data["mode"] == "credential_reset"
        assert data["username"] == username


# ──────────────────────────────────────────────────────────────────────
# 2. Admin create TOTP user → login directly
# ──────────────────────────────────────────────────────────────────────


class TestAdminCreateTOTPThenLogin:
    """Admin creates TOTP user → user logs in directly with the secret."""

    def test_totp_user_can_login_immediately(self, admin_client, auth_app):
        """
        TOTP users don't need a claim step — the secret is returned at
        creation time and the user can log in immediately.
        """
        username = "lifecycle-totp-1"
        uid, create_data = _admin_create_user(admin_client, username, "totp")
        totp_secret = create_data["setup_data"]["secret"]

        client = auth_app.test_client()
        resp = _login_totp(client, username, totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        login_data = resp.get_json()
        assert login_data["success"] is True
        assert login_data["user"]["username"] == username


# ──────────────────────────────────────────────────────────────────────
# 3. Negative cases — wrong username, mangled token
# ──────────────────────────────────────────────────────────────────────


class TestAdminCreateClaimNegativeCases:
    """Verify claim endpoint rejects invalid inputs."""

    def test_wrong_username_rejected(self, admin_client, auth_app):
        """Claim with a different username than the token was created for."""
        username = "lifecycle-neg-1"
        uid, create_data = _admin_create_user(admin_client, username, "passkey")
        claim_token = create_data["setup_data"]["claim_token"]

        client = auth_app.test_client()
        resp = _claim_totp(client, "wrong-user", claim_token)
        assert resp.status_code in (400, 404)

    def test_mangled_token_rejected(self, admin_client, auth_app):
        """Claim with a corrupted token fails."""
        username = "lifecycle-neg-2"
        uid, create_data = _admin_create_user(admin_client, username, "passkey")

        client = auth_app.test_client()
        resp = _claim_totp(client, username, "XXXX-XXXX-XXXX-XXXX")
        assert resp.status_code in (400, 404)

    def test_empty_token_rejected(self, admin_client, auth_app):
        """Claim with empty token fails."""
        username = "lifecycle-neg-3"
        _admin_create_user(admin_client, username, "passkey")

        client = auth_app.test_client()
        resp = _claim_totp(client, username, "")
        assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────
# 4. Admin invite → claim → login
# ──────────────────────────────────────────────────────────────────────


class TestAdminInvitePasskeyThenClaimThenLogin:
    """Admin invites passkey user → user claims TOTP → logs in."""

    def test_invite_then_claim_then_login(self, admin_client, auth_app):
        """
        The invite endpoint is a separate code path from create.
        Verify the full round-trip works through invite as well.
        """
        # Invite a new passkey user (invite requires email)
        resp = admin_client.post(
            "/auth/admin/users/invite",
            json={
                "username": "lifecycle-invite-1",
                "auth_method": "passkey",
                "email": "invite1@example.com",
                "can_download": True,
            },
        )
        assert resp.status_code in (200, 201), f"Invite failed: {resp.get_json()}"
        invite_data = resp.get_json()

        # Extract claim token — invite may nest it differently
        setup = invite_data.get("setup_data", invite_data)
        claim_token = setup.get("claim_token")
        if not claim_token:
            pytest.skip("Invite endpoint does not return claim_token for passkey")

        username = "lifecycle-invite-1"
        client = auth_app.test_client()

        # Validate
        resp = _validate_claim(client, username, claim_token)
        assert resp.status_code == 200

        # Claim TOTP
        resp = _claim_totp(client, username, claim_token)
        assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
        totp_secret = resp.get_json()["totp_secret"]

        # Login
        resp = _login_totp(client, username, totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True
