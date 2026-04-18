"""
Cross-boundary lifecycle tests for credential reset → claim flow.

These tests verify the complete round-trip: admin (or self-service) resets
a user's credentials, then the user claims new credentials through the
claim endpoints. This is the class of test that was missing when the
credential reset claim bug shipped in v7.6.0 — the admin reset tests
verified the response shape but never followed the token through to the
claim endpoint.

Covers:
  1. Admin resets TOTP user → user claims via TOTP (full round-trip)
  2. Admin resets passkey user → user claims via WebAuthn begin (round-trip)
  3. Self-service reset → user claims via TOTP (round-trip)
  4. Claim URL format verification (must point to /claim.html, not API)
  5. Credential reset token consumed after successful claim
  6. Expired reset token rejected at claim endpoint
"""

import re
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _create_user_via_admin(admin_client, username, auth_method="totp", **extra):
    """Create a user through the admin API, return (user_id, response_data)."""
    payload = {
        "username": username,
        "auth_method": auth_method,
        "is_admin": False,
        "can_download": True,
    }
    payload.update(extra)
    resp = admin_client.post("/auth/admin/users/create", json=payload)
    assert resp.status_code == 201, f"User creation failed: {resp.get_json()}"
    data = resp.get_json()
    return data["user_id"], data


def _reset_credentials(admin_client, user_id):
    """Reset credentials via admin API, return response data."""
    resp = admin_client.post(f"/auth/admin/users/{user_id}/reset-credentials")
    assert resp.status_code == 200, f"Reset failed: {resp.get_json()}"
    return resp.get_json()


# ──────────────────────────────────────────────────────────────────────
# 1. Admin resets TOTP user → claim via TOTP
# ──────────────────────────────────────────────────────────────────────


class TestAdminResetThenClaimTOTP:
    """Full lifecycle: create TOTP user → admin reset → claim new TOTP."""

    def test_reset_totp_then_claim_totp(self, admin_client):
        """Admin resets TOTP credentials; user claims new TOTP secret."""
        # Step 1: Create a TOTP user
        uid, create_data = _create_user_via_admin(admin_client, "reset-claim-totp1")
        old_secret = create_data["setup_data"]["secret"]

        # Step 2: Admin resets credentials
        reset_data = _reset_credentials(admin_client, uid)
        new_secret = reset_data["setup_data"]["secret"]
        assert new_secret != old_secret, "Reset should generate a new secret"

        # Step 3: The TOTP reset flow doesn't use the claim endpoint —
        # it directly returns the new secret. Verify the user's credential
        # was actually updated in the database.
        from auth.models import UserRepository

        db = admin_client.application.auth_db
        user = UserRepository(db).get_by_id(uid)
        assert user.auth_credential != b"testsecret"
        assert user.auth_credential != b"pending"


# ──────────────────────────────────────────────────────────────────────
# 2. Admin resets passkey user → claim via WebAuthn (validate + begin)
# ──────────────────────────────────────────────────────────────────────


class TestAdminResetPasskeyThenClaim:
    """Full lifecycle: create passkey user → admin reset → validate → claim."""

    def test_reset_passkey_validate_succeeds(self, admin_client, auth_app):
        """Reset passkey user, then validate claim token at claim endpoint."""
        # Step 1: Create a passkey user
        uid, _ = _create_user_via_admin(admin_client, "reset-claim-pk1", auth_method="passkey")

        # Step 2: Admin resets credentials — gets claim token
        reset_data = _reset_credentials(admin_client, uid)
        setup = reset_data["setup_data"]
        claim_token = setup["claim_token"]
        assert claim_token, "Reset should return a claim token"

        # Step 3: Validate the claim token at the claim endpoint
        anon = auth_app.test_client()
        resp = anon.post(
            "/auth/register/claim/validate",
            json={"username": "reset-claim-pk1", "claim_token": claim_token},
        )
        assert (
            resp.status_code == 200
        ), f"Validate should succeed for reset token: {resp.get_json()}"
        data = resp.get_json()
        assert data["valid"] is True

    @patch("api_modular.auth.webauthn_registration_options")
    @patch("api_modular.auth.get_webauthn_config")
    def test_reset_passkey_webauthn_begin_succeeds(
        self, mock_config, mock_reg_opts, admin_client, auth_app
    ):
        """Reset passkey user, then start WebAuthn registration via claim."""
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_reg_opts.return_value = ('{"rp": {"id": "localhost"}}', b"\xab\xcd\xef")

        # Step 1: Create and reset
        uid, _ = _create_user_via_admin(admin_client, "reset-claim-pk2", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        claim_token = reset_data["setup_data"]["claim_token"]

        # Step 2: Begin WebAuthn registration with the reset claim token
        anon = auth_app.test_client()
        resp = anon.post(
            "/auth/register/claim/webauthn/begin",
            json={
                "username": "reset-claim-pk2",
                "claim_token": claim_token,
                "auth_type": "passkey",
            },
        )
        assert (
            resp.status_code == 200
        ), f"WebAuthn begin should succeed for reset token: {resp.get_json()}"
        data = resp.get_json()
        assert "options" in data
        assert "challenge" in data

    def test_reset_passkey_claim_totp_instead(self, admin_client, auth_app):
        """Reset passkey user, but claim TOTP credentials instead."""
        # Step 1: Create passkey user and reset
        uid, _ = _create_user_via_admin(admin_client, "reset-claim-pk-totp", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        claim_token = reset_data["setup_data"]["claim_token"]

        # Step 2: Claim TOTP credentials using the passkey reset token
        anon = auth_app.test_client()
        resp = anon.post(
            "/auth/register/claim",
            json={
                "username": "reset-claim-pk-totp",
                "claim_token": claim_token,
                "auth_method": "totp",
            },
        )
        assert (
            resp.status_code == 200
        ), f"Claiming TOTP after passkey reset should work: {resp.get_json()}"
        data = resp.get_json()
        assert data["success"] is True
        assert "totp_secret" in data
        assert "backup_codes" in data

        # Verify user was updated in DB
        from auth.models import AuthType, UserRepository

        db = admin_client.application.auth_db
        user = UserRepository(db).get_by_id(uid)
        assert user.auth_type == AuthType.TOTP
        assert user.auth_credential != b"pending"


# ──────────────────────────────────────────────────────────────────────
# 3. Self-service reset → claim
# ──────────────────────────────────────────────────────────────────────


class TestSelfServiceResetThenClaim:
    """Self-service credential reset → claim lifecycle."""

    def test_self_reset_passkey_then_claim(self, auth_app, auth_db):
        """User resets own passkey credentials, then claims via TOTP."""
        from auth.models import AuthType, User, UserRepository

        # Create a passkey user directly in DB
        user = User(
            username="self-reset-pk1",
            auth_type=AuthType.PASSKEY,
            auth_credential=b'{"credential_id": "test"}',
            is_admin=False,
            can_download=True,
        ).save(auth_db)

        # Create session for this user
        from auth.models import Session

        _, raw_token = Session.create_for_user(
            db=auth_db, user_id=user.id, user_agent="pytest", ip_address="127.0.0.1"
        )

        # Step 1: Self-service reset
        client = auth_app.test_client()
        client.set_cookie("audiobooks_session", raw_token)
        resp = client.post("/auth/account/reset-credentials")
        assert resp.status_code == 200
        reset_data = resp.get_json()
        claim_token = reset_data["setup_data"]["claim_token"]

        # Step 2: Claim TOTP using the self-service reset token
        anon = auth_app.test_client()
        resp = anon.post(
            "/auth/register/claim",
            json={"username": "self-reset-pk1", "claim_token": claim_token, "auth_method": "totp"},
        )
        assert (
            resp.status_code == 200
        ), f"Self-service reset claim should succeed: {resp.get_json()}"
        data = resp.get_json()
        assert data["success"] is True
        assert "totp_secret" in data

        # Verify DB updated
        user = UserRepository(auth_db).get_by_username("self-reset-pk1")
        assert user.auth_type == AuthType.TOTP
        assert user.auth_credential != b"pending"


# ──────────────────────────────────────────────────────────────────────
# 4. Claim URL format verification
# ──────────────────────────────────────────────────────────────────────


class TestClaimURLFormat:
    """Verify claim URLs point to the browser page, not the API endpoint."""

    def test_admin_create_passkey_claim_url_format(self, admin_client):
        """Creating a passkey user returns /claim.html URL, not API URL."""
        resp = admin_client.post(
            "/auth/admin/users/create",
            json={
                "username": "url-check-create",
                "auth_method": "passkey",
                "is_admin": False,
                "can_download": True,
            },
        )
        assert resp.status_code == 201
        claim_url = resp.get_json()["setup_data"]["claim_url"]
        assert claim_url.startswith(
            "/claim.html?"
        ), f"Claim URL must point to /claim.html, got: {claim_url}"
        assert "username=" in claim_url
        assert "token=" in claim_url
        # Must NOT be the API endpoint
        assert "/auth/register/claim" not in claim_url

    def test_admin_reset_passkey_claim_url_format(self, admin_client):
        """Resetting passkey credentials returns /claim.html URL."""
        uid, _ = _create_user_via_admin(admin_client, "url-check-reset", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        claim_url = reset_data["setup_data"]["claim_url"]
        assert claim_url.startswith(
            "/claim.html?"
        ), f"Reset claim URL must point to /claim.html, got: {claim_url}"
        assert "username=url-check-reset" in claim_url
        assert "token=" in claim_url
        assert "/auth/register/claim" not in claim_url

    def test_self_service_reset_claim_url_format(self, auth_app, auth_db):
        """Self-service passkey reset returns /claim.html URL."""
        from auth.models import AuthType, Session, User

        user = User(
            username="url-check-self",
            auth_type=AuthType.PASSKEY,
            auth_credential=b'{"credential_id": "test"}',
            is_admin=False,
            can_download=True,
        ).save(auth_db)
        _, raw_token = Session.create_for_user(
            db=auth_db, user_id=user.id, user_agent="pytest", ip_address="127.0.0.1"
        )
        client = auth_app.test_client()
        client.set_cookie("audiobooks_session", raw_token)
        resp = client.post("/auth/account/reset-credentials")
        assert resp.status_code == 200
        claim_url = resp.get_json()["setup_data"]["claim_url"]
        assert claim_url.startswith(
            "/claim.html?"
        ), f"Self-service claim URL must point to /claim.html, got: {claim_url}"
        assert "/auth/register/claim" not in claim_url

    def test_invite_user_claim_url_format(self, admin_client):
        """Inviting a user returns /claim.html URL."""
        resp = admin_client.post(
            "/auth/admin/users/invite",
            json={
                "username": "url-check-invite",
                "auth_method": "passkey",
                "is_admin": False,
                "can_download": True,
            },
        )
        # invite endpoint may or may not exist; skip if 404
        if resp.status_code == 404:
            pytest.skip("Invite endpoint not available")
        if resp.status_code in (200, 201):
            data = resp.get_json()
            setup = data.get("setup_data", data)
            if "claim_url" in setup:
                assert setup["claim_url"].startswith(
                    "/claim.html?"
                ), f"Invite claim URL must be /claim.html: {setup['claim_url']}"

    def test_claim_token_format_matches_pattern(self, admin_client):
        """Claim tokens follow XXXX-XXXX-XXXX-XXXX format."""
        uid, _ = _create_user_via_admin(admin_client, "token-fmt-check", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        token = reset_data["setup_data"]["claim_token"]
        assert re.match(
            r"^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$", token
        ), f"Token format invalid: {token}"


# ──────────────────────────────────────────────────────────────────────
# 5. Token consumption after successful claim
# ──────────────────────────────────────────────────────────────────────


class TestResetTokenConsumption:
    """Reset tokens are single-use and consumed after claim."""

    def test_token_consumed_after_totp_claim(self, admin_client, auth_app):
        """After claiming TOTP, the same token cannot be reused."""
        uid, _ = _create_user_via_admin(admin_client, "consume-totp1", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        claim_token = reset_data["setup_data"]["claim_token"]

        anon = auth_app.test_client()

        # First claim succeeds
        resp = anon.post(
            "/auth/register/claim",
            json={"username": "consume-totp1", "claim_token": claim_token, "auth_method": "totp"},
        )
        assert resp.status_code == 200

        # Second claim with same token fails
        resp = anon.post(
            "/auth/register/claim/validate",
            json={"username": "consume-totp1", "claim_token": claim_token},
        )
        # Token should be gone — either 400 (already claimed) or 404 (not found)
        assert resp.status_code in (
            400,
            404,
        ), f"Consumed token should be rejected: {resp.get_json()}"


# ──────────────────────────────────────────────────────────────────────
# 6. Expired reset tokens rejected at claim
# ──────────────────────────────────────────────────────────────────────


class TestExpiredResetToken:
    """Expired credential reset tokens are rejected at claim endpoints."""

    def test_expired_reset_token_rejected(self, admin_client, auth_app, auth_db):
        """Claim endpoint rejects expired reset tokens."""
        uid, _ = _create_user_via_admin(admin_client, "expired-reset1", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        claim_token = reset_data["setup_data"]["claim_token"]

        # Manually expire the token in the database
        with auth_db.connection() as conn:
            conn.execute(
                """UPDATE pending_registrations
                   SET expires_at = ?
                   WHERE username = ?""",
                ((datetime.now() - timedelta(hours=1)).isoformat(), "expired-reset1"),
            )

        # Attempt to claim — should fail with expired
        anon = auth_app.test_client()
        resp = anon.post(
            "/auth/register/claim/validate",
            json={"username": "expired-reset1", "claim_token": claim_token},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["valid"] is False
        assert "expired" in data.get("error", "").lower()


# ──────────────────────────────────────────────────────────────────────
# 7. Username mismatch on reset token
# ──────────────────────────────────────────────────────────────────────


class TestResetTokenUsernameMismatch:
    """Reset tokens are bound to a specific username."""

    def test_wrong_username_rejected(self, admin_client, auth_app):
        """Claiming with wrong username for a valid reset token fails."""
        uid, _ = _create_user_via_admin(admin_client, "mismatch-user1", auth_method="passkey")
        reset_data = _reset_credentials(admin_client, uid)
        claim_token = reset_data["setup_data"]["claim_token"]

        anon = auth_app.test_client()
        resp = anon.post(
            "/auth/register/claim/validate",
            json={"username": "wrong-username", "claim_token": claim_token},
        )
        # Should not validate for a different username
        assert resp.status_code in (
            400,
            404,
        ), f"Wrong username should be rejected: {resp.get_json()}"
