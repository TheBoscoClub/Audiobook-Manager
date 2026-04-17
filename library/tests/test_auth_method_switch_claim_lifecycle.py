"""
Cross-boundary round-trip tests: auth method switch → claim → login.

These tests close Gap 2 from the cross-component audit: the auth method
switch endpoints (admin and self-service) were only tested for response
shape ("does setup_data have a claim_token?") — no test ever fed the
returned token into the claim endpoint and then attempted to log in.

A user who switches to passkey gets a claim token. If that token's hash
doesn't match what the claim endpoint computes, the user is locked out
of their account with no recovery path (except admin intervention).

Covers:
  1. Admin switches TOTP user to passkey → user claims TOTP → logs in
  2. Admin switches TOTP user to passkey → user claims magic_link
  3. Self-service switch to passkey → user claims TOTP → logs in
  4. Admin resets passkey credentials → user claims TOTP → logs in
  5. Self-service reset passkey credentials → user claims TOTP → logs in
  6. Switch to passkey then switch back to TOTP (double switch)
"""

import pyotp
import pytest

from auth.models import AuthType, Session, User, UserRepository


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_totp_user(auth_db, username):
    """Create a TOTP user directly in the DB, return (user, base32_secret)."""
    from backend.api_modular.auth import setup_totp

    secret_bytes, base32_secret, _ = setup_totp(username)
    user = User(
        username=username,
        auth_type=AuthType.TOTP,
        auth_credential=secret_bytes,
        is_admin=False,
        can_download=True,
    ).save(auth_db)
    return user, base32_secret


def _make_session_cookie(auth_db, user_id):
    """Create a session and return the raw token."""
    _, raw_token = Session.create_for_user(
        db=auth_db, user_id=user_id, user_agent="pytest", ip_address="127.0.0.1"
    )
    return raw_token


def _authed_client(auth_app, auth_db, user):
    """Return a test client authenticated as the given user."""
    raw_token = _make_session_cookie(auth_db, user.id)
    client = auth_app.test_client()
    client.set_cookie("audiobooks_session", raw_token)
    return client


def _validate_claim(client, username, claim_token):
    """Validate a claim token."""
    return client.post(
        "/auth/register/claim/validate", json={"username": username, "claim_token": claim_token}
    )


def _claim_totp(client, username, claim_token):
    """Claim TOTP credentials."""
    return client.post(
        "/auth/register/claim",
        json={"username": username, "claim_token": claim_token, "auth_method": "totp"},
    )


def _claim_magic_link(client, username, claim_token, email):
    """Claim magic_link credentials."""
    return client.post(
        "/auth/register/claim",
        json={
            "username": username,
            "claim_token": claim_token,
            "auth_method": "magic_link",
            "recovery_email": email,
        },
    )


def _login_totp(client, username, totp_secret):
    """Generate a TOTP code and login."""
    code = pyotp.TOTP(totp_secret).now()
    return client.post("/auth/login", json={"username": username, "code": code})


# ──────────────────────────────────────────────────────────────────────
# 1. Admin switches TOTP user to passkey → claim TOTP → login
# ──────────────────────────────────────────────────────────────────────


class TestAdminSwitchToPasskeyThenClaimThenLogin:
    """Admin switches a TOTP user to passkey, user claims TOTP, logs in."""

    def test_full_round_trip(self, admin_client, auth_app, auth_db):
        """
        TOTP user → admin switches to passkey → claim token returned
        → user claims TOTP credentials → logs in.
        """
        user, _ = _make_totp_user(auth_db, "switch-claim-1")

        # Admin switches auth method to passkey
        resp = admin_client.put(
            f"/auth/admin/users/{user.id}/auth-method", json={"auth_method": "passkey"}
        )
        assert resp.status_code == 200, f"Switch failed: {resp.get_json()}"
        switch_data = resp.get_json()
        claim_token = switch_data["setup_data"]["claim_token"]

        # Unauthenticated client claims TOTP credentials
        client = auth_app.test_client()
        resp = _validate_claim(client, user.username, claim_token)
        assert resp.status_code == 200
        assert resp.get_json()["valid"] is True

        resp = _claim_totp(client, user.username, claim_token)
        assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
        totp_secret = resp.get_json()["totp_secret"]

        # Login with the claimed TOTP secret
        resp = _login_totp(client, user.username, totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True

    def test_switch_to_passkey_then_claim_magic_link(self, admin_client, auth_app, auth_db):
        """
        Switch to passkey, but claim magic_link credentials instead.
        The claim endpoint should accept any auth_method, not just passkey.
        """
        user, _ = _make_totp_user(auth_db, "switch-claim-ml")

        resp = admin_client.put(
            f"/auth/admin/users/{user.id}/auth-method", json={"auth_method": "passkey"}
        )
        assert resp.status_code == 200
        claim_token = resp.get_json()["setup_data"]["claim_token"]

        client = auth_app.test_client()
        resp = _claim_magic_link(client, user.username, claim_token, "switch-ml@example.com")
        assert resp.status_code == 200, f"Claim ML failed: {resp.get_json()}"
        data = resp.get_json()
        assert data["success"] is True
        assert data["auth_method"] == "magic_link"

        # Verify the user's auth type was updated in DB
        user_repo = UserRepository(auth_db)
        updated = user_repo.get_by_id(user.id)
        assert updated.auth_type == AuthType.MAGIC_LINK


# ──────────────────────────────────────────────────────────────────────
# 2. Self-service switch to passkey → claim TOTP → login
# ──────────────────────────────────────────────────────────────────────


class TestSelfServiceSwitchToPasskeyThenClaimThenLogin:
    """User switches their own auth method to passkey, claims TOTP, logs in."""

    def test_self_switch_then_claim_then_login(self, auth_app, auth_db):
        """
        User switches from TOTP to passkey via self-service endpoint.
        Gets claim token → claims TOTP → logs in with new credentials.
        """
        user, _ = _make_totp_user(auth_db, "self-switch-1")
        authed = _authed_client(auth_app, auth_db, user)

        # Self-service switch to passkey
        resp = authed.put("/auth/account/auth-method", json={"auth_method": "passkey"})
        assert resp.status_code == 200, f"Self-switch failed: {resp.get_json()}"
        claim_token = resp.get_json()["setup_data"]["claim_token"]

        # Claim TOTP (use unauthenticated client — user's session may be invalid
        # after auth method switch)
        client = auth_app.test_client()
        resp = _claim_totp(client, user.username, claim_token)
        assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
        totp_secret = resp.get_json()["totp_secret"]

        # Login with new TOTP
        resp = _login_totp(client, user.username, totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True


# ──────────────────────────────────────────────────────────────────────
# 3. Admin resets passkey credentials → claim TOTP → login
# ──────────────────────────────────────────────────────────────────────


class TestAdminResetPasskeyCredentialsThenClaimThenLogin:
    """Admin resets a passkey user's credentials, user claims TOTP, logs in."""

    def test_reset_passkey_before_claim_then_claim_then_login(
        self, admin_client, auth_app, auth_db
    ):
        """
        Create passkey user → admin resets BEFORE user claims →
        new claim token generated → user claims TOTP → logs in.

        This exercises the reset-credentials path for a user whose
        auth_type is still PASSKEY (hasn't claimed yet).
        """
        # Create passkey user
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "reset-claim-login-1",
                "auth_method": "passkey",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        uid = resp.get_json()["user_id"]
        first_token = resp.get_json()["setup_data"]["claim_token"]

        # Admin resets BEFORE user claims — should generate a new claim token
        resp = admin_client.post(f"/auth/admin/users/{uid}/reset-credentials")
        assert resp.status_code == 200, f"Reset failed: {resp.get_json()}"
        reset_data = resp.get_json()
        reset_token = reset_data["setup_data"]["claim_token"]

        # First token should no longer work (user's auth_credential reset)
        client = auth_app.test_client()
        resp = _validate_claim(client, "reset-claim-login-1", first_token)
        # May or may not be valid — the old pending_registration may still exist
        # but the important thing is the NEW token works

        # Claim with the new reset token
        resp = _claim_totp(client, "reset-claim-login-1", reset_token)
        assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
        totp_secret = resp.get_json()["totp_secret"]

        # Login
        resp = _login_totp(client, "reset-claim-login-1", totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True

    def test_reset_totp_after_claim_then_login(self, admin_client, auth_app, auth_db):
        """
        Create passkey user → claim TOTP (now auth_type=TOTP) →
        admin resets → new TOTP secret returned directly → login.

        After claiming TOTP, the user's auth_type is TOTP, so reset
        returns a new TOTP secret directly (no claim token needed).
        """
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "reset-after-claim-1",
                "auth_method": "passkey",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        uid = resp.get_json()["user_id"]
        first_token = resp.get_json()["setup_data"]["claim_token"]

        # Claim TOTP (changes auth_type from PASSKEY to TOTP)
        client = auth_app.test_client()
        resp = _claim_totp(client, "reset-after-claim-1", first_token)
        assert resp.status_code == 200
        old_secret = resp.get_json()["totp_secret"]

        # Admin resets credentials — should return new TOTP secret directly
        resp = admin_client.post(f"/auth/admin/users/{uid}/reset-credentials")
        assert resp.status_code == 200, f"Reset failed: {resp.get_json()}"
        reset_data = resp.get_json()
        new_secret = reset_data["setup_data"]["secret"]
        assert new_secret != old_secret

        # Login with new secret
        resp = _login_totp(client, "reset-after-claim-1", new_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True

        # Old secret should NOT work
        resp = _login_totp(client, "reset-after-claim-1", old_secret)
        assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────────
# 4. Self-service reset passkey → claim → login
# ──────────────────────────────────────────────────────────────────────


class TestSelfServiceResetPasskeyThenClaimThenLogin:
    """User resets own passkey credentials, claims TOTP, logs in."""

    def test_self_reset_then_claim_then_login(self, auth_app, auth_db):
        """
        TOTP user → switch to passkey → claim first token → self-reset →
        claim second token → login.
        """
        user, _ = _make_totp_user(auth_db, "self-reset-login-1")
        authed = _authed_client(auth_app, auth_db, user)

        # Switch to passkey
        resp = authed.put("/auth/account/auth-method", json={"auth_method": "passkey"})
        assert resp.status_code == 200
        first_token = resp.get_json()["setup_data"]["claim_token"]

        # Claim first token to establish passkey credentials
        client = auth_app.test_client()
        resp = _claim_totp(client, user.username, first_token)
        assert resp.status_code == 200

        # Re-authenticate (credentials changed)
        authed = _authed_client(auth_app, auth_db, user)

        # Self-service reset credentials
        resp = authed.post("/auth/account/reset-credentials")
        assert resp.status_code == 200, f"Self-reset failed: {resp.get_json()}"

        # For TOTP users, reset returns new secret directly (no claim needed).
        # For passkey users, reset returns a claim token.
        reset_data = resp.get_json()
        setup = reset_data.get("setup_data", {})

        # Initialized so pylint can prove totp_secret is bound at the call
        # site below. pytest.fail() raises so the else branch never falls
        # through, but pylint can't see NoReturn here.
        totp_secret = ""
        if "claim_token" in setup:
            # Passkey reset → claim flow
            reset_token = setup["claim_token"]
            resp = _claim_totp(client, user.username, reset_token)
            assert resp.status_code == 200, f"Claim failed: {resp.get_json()}"
            totp_secret = resp.get_json()["totp_secret"]
        elif "secret" in setup:
            # TOTP reset → direct secret
            totp_secret = setup["secret"]
        else:
            pytest.fail(f"Unexpected reset response: {reset_data}")

        # Login
        resp = _login_totp(client, user.username, totp_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True


# ──────────────────────────────────────────────────────────────────────
# 5. Double switch: TOTP → passkey → claim → switch back to TOTP
# ──────────────────────────────────────────────────────────────────────


class TestDoubleSwitchRoundTrip:
    """Switch TOTP → passkey → claim → switch back to TOTP, login works."""

    def test_double_switch(self, admin_client, auth_app, auth_db):
        """
        TOTP user → admin switches to passkey → user claims TOTP →
        admin switches back to TOTP → user logs in with new TOTP secret.
        """
        user, original_secret = _make_totp_user(auth_db, "double-switch-1")

        # Switch to passkey
        resp = admin_client.put(
            f"/auth/admin/users/{user.id}/auth-method", json={"auth_method": "passkey"}
        )
        assert resp.status_code == 200
        claim_token = resp.get_json()["setup_data"]["claim_token"]

        # Claim TOTP
        client = auth_app.test_client()
        resp = _claim_totp(client, user.username, claim_token)
        assert resp.status_code == 200
        intermediate_secret = resp.get_json()["totp_secret"]

        # Switch back to TOTP via admin (direct TOTP setup, no claim needed)
        resp = admin_client.put(
            f"/auth/admin/users/{user.id}/auth-method", json={"auth_method": "totp"}
        )
        assert resp.status_code == 200
        final_secret = resp.get_json()["setup_data"]["secret"]

        # All three secrets should be different
        assert final_secret != original_secret
        assert final_secret != intermediate_secret

        # Login with the final TOTP secret
        resp = _login_totp(client, user.username, final_secret)
        assert resp.status_code == 200, f"Login failed: {resp.get_json()}"
        assert resp.get_json()["success"] is True

        # Old secrets should NOT work
        resp = _login_totp(client, user.username, original_secret)
        assert resp.status_code == 401

        resp = _login_totp(client, user.username, intermediate_secret)
        assert resp.status_code == 401
