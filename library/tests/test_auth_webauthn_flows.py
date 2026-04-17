"""
Unit tests for WebAuthn/Passkey HTTP endpoint flows in auth.py.

Tests cover the six uncovered WebAuthn code paths:
1. Auth-method switch to WebAuthn (begin + complete)
2. Claim WebAuthn complete (access-request claim flow)
3. Register WebAuthn begin (self-registration)
4. Register WebAuthn complete (self-registration)
5. Login WebAuthn begin
6. Login WebAuthn complete

All WebAuthn library calls are mocked — no real authenticator needed.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import AuthType, UserRepository  # noqa: E402
from auth.passkey import WebAuthnCredential  # noqa: E402
from webauthn.helpers import bytes_to_base64url  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_webauthn_cred():
    """Create a mock WebAuthnCredential returned by verify_registration."""
    cred = MagicMock(spec=WebAuthnCredential)
    cred.credential_id = b"\x01\x02\x03\x04"
    cred.public_key = b"\x05\x06\x07\x08"
    cred.sign_count = 0
    cred.transports = ["internal"]
    cred.created_at = datetime.now()
    cred.to_json.return_value = json.dumps(
        {
            "credential_id": bytes_to_base64url(b"\x01\x02\x03\x04"),
            "public_key": bytes_to_base64url(b"\x05\x06\x07\x08"),
            "sign_count": 0,
            "transports": ["internal"],
            "created_at": datetime.now().isoformat(),
        }
    )
    return cred


def _make_passkey_user(auth_db):
    """Create a user with passkey auth type for login tests."""
    from auth import User

    cred = WebAuthnCredential(
        credential_id=b"\xaa\xbb\xcc\xdd",
        public_key=b"\xee\xff\x00\x11",
        sign_count=5,
        transports=["internal"],
        created_at=datetime.now(),
    )
    user = User(
        username="passkeylogin_fix",
        auth_type=AuthType.PASSKEY,
        auth_credential=cred.to_json().encode("utf-8"),
        is_admin=False,
        can_download=True,
    )
    repo = UserRepository(auth_db)
    existing = repo.get_by_username("passkeylogin_fix")
    if existing:
        return existing
    user.save(auth_db)
    return user


def _make_fido2_user(auth_db):
    """Create a user with FIDO2 auth type for login tests."""
    from auth import User

    cred = WebAuthnCredential(
        credential_id=b"\x11\x22\x33\x44",
        public_key=b"\x55\x66\x77\x88",
        sign_count=3,
        transports=["usb"],
        created_at=datetime.now(),
    )
    user = User(
        username="fido2login_fix",
        auth_type=AuthType.FIDO2,
        auth_credential=cred.to_json().encode("utf-8"),
        is_admin=False,
        can_download=True,
    )
    repo = UserRepository(auth_db)
    existing = repo.get_by_username("fido2login_fix")
    if existing:
        return existing
    user.save(auth_db)
    return user


def _create_approved_access_request(
    auth_db, username, raw_token, claim_expires_at=None, contact_email=None
):
    """Create an approved access request using the repository methods."""
    from auth import hash_token
    from auth.models import AccessRequestRepository

    if claim_expires_at is None:
        claim_expires_at = datetime.now() + timedelta(hours=24)

    req_repo = AccessRequestRepository(auth_db)
    token_hash = hash_token(raw_token.replace("-", ""))
    access_req = req_repo.create(
        username=username,
        claim_token_hash=token_hash,
        contact_email=contact_email,
        claim_expires_at=claim_expires_at,
    )
    req_repo.approve(access_req.id, "adminuser")
    return access_req


# ---------------------------------------------------------------------------
# 1. Auth-method switch: begin + complete (/auth/me/webauthn/*)
# ---------------------------------------------------------------------------


class TestWebAuthnSwitchBegin:
    """Tests for /auth/me/webauthn/begin — start switching auth method."""

    @patch("api_modular.auth.webauthn_registration_options")
    @patch("api_modular.auth.get_webauthn_config")
    def test_begin_passkey_success(self, mock_config, mock_reg_opts, user_client):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_reg_opts.return_value = ('{"rp": {}}', b"\xab\xcd")

        resp = user_client.post(
            "/auth/me/webauthn/begin",
            json={"auth_type": "passkey"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "options" in data
        assert "challenge" in data
        mock_reg_opts.assert_called_once()
        # Verify platform authenticator type was requested
        _, call_kwargs = mock_reg_opts.call_args
        assert call_kwargs.get("authenticator_type") == "platform"

    @patch("api_modular.auth.webauthn_registration_options")
    @patch("api_modular.auth.get_webauthn_config")
    def test_begin_fido2_success(self, mock_config, mock_reg_opts, user_client):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_reg_opts.return_value = ('{"rp": {}}', b"\xab\xcd")

        resp = user_client.post(
            "/auth/me/webauthn/begin",
            json={"auth_type": "fido2"},
        )
        assert resp.status_code == 200

    def test_begin_invalid_auth_type(self, user_client):
        resp = user_client.post(
            "/auth/me/webauthn/begin",
            json={"auth_type": "invalid"},
        )
        assert resp.status_code == 400
        assert "Invalid auth type" in resp.get_json()["error"]

    def test_begin_unauthenticated(self, anon_client):
        resp = anon_client.post(
            "/auth/me/webauthn/begin",
            json={"auth_type": "passkey"},
        )
        assert resp.status_code == 401


class TestWebAuthnSwitchComplete:
    """Tests for /auth/me/webauthn/complete — complete auth method switch."""

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_passkey_success(
        self, mock_config, mock_verify, user_client, auth_db, test_user
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        fake_cred = _make_fake_webauthn_cred()
        mock_verify.return_value = fake_cred

        # Set up pending challenge
        from api_modular.auth import _pending_webauthn_challenges

        challenge_b64 = bytes_to_base64url(b"\xde\xad\xbe\xef")
        _pending_webauthn_challenges[test_user.id] = challenge_b64

        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={
                "credential": {"id": "test", "type": "public-key"},
                "challenge": challenge_b64,
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["auth_type"] == "passkey"

        # Verify user's auth_type updated in DB
        repo = UserRepository(auth_db)
        updated = repo.get_by_id(test_user.id)
        assert updated.auth_type == AuthType.PASSKEY

        # Clean up: reset user back to TOTP for other tests
        with auth_db.connection() as conn:
            conn.execute(
                "UPDATE users SET auth_type = ?, auth_credential = ? WHERE id = ?",
                ("totp", b"testsecret", test_user.id),
            )

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_fido2_success(
        self, mock_config, mock_verify, user_client, auth_db, test_user
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        fake_cred = _make_fake_webauthn_cred()
        mock_verify.return_value = fake_cred

        from api_modular.auth import _pending_webauthn_challenges

        challenge_b64 = bytes_to_base64url(b"\xca\xfe\xba\xbe")
        _pending_webauthn_challenges[test_user.id] = challenge_b64

        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={
                "credential": {"id": "test", "type": "public-key"},
                "challenge": challenge_b64,
                "auth_type": "fido2",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auth_type"] == "fido2"

        # Verify auth_type in DB
        repo = UserRepository(auth_db)
        updated = repo.get_by_id(test_user.id)
        assert updated.auth_type == AuthType.FIDO2

        # Clean up
        with auth_db.connection() as conn:
            conn.execute(
                "UPDATE users SET auth_type = ?, auth_credential = ? WHERE id = ?",
                ("totp", b"testsecret", test_user.id),
            )

    def test_complete_missing_credential(self, user_client):
        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={"challenge": "abc", "auth_type": "passkey"},
        )
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"].lower()

    def test_complete_missing_challenge(self, user_client):
        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={"credential": {"id": "x"}, "auth_type": "passkey"},
        )
        assert resp.status_code == 400

    def test_complete_invalid_auth_type(self, user_client, test_user):
        from api_modular.auth import _pending_webauthn_challenges

        challenge_b64 = bytes_to_base64url(b"\x01\x02")
        _pending_webauthn_challenges[test_user.id] = challenge_b64

        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={
                "credential": {"id": "x"},
                "challenge": challenge_b64,
                "auth_type": "magic_link",
            },
        )
        assert resp.status_code == 400

    def test_complete_wrong_challenge(self, user_client, test_user):
        from api_modular.auth import _pending_webauthn_challenges

        _pending_webauthn_challenges[test_user.id] = bytes_to_base64url(b"\x01")

        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={
                "credential": {"id": "x"},
                "challenge": bytes_to_base64url(b"\x99"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400
        assert "Invalid or expired challenge" in resp.get_json()["error"]

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_verification_fails(
        self, mock_config, mock_verify, user_client, test_user
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = None  # Verification failure

        from api_modular.auth import _pending_webauthn_challenges

        challenge_b64 = bytes_to_base64url(b"\xfe\xed")
        _pending_webauthn_challenges[test_user.id] = challenge_b64

        resp = user_client.post(
            "/auth/me/webauthn/complete",
            json={
                "credential": {"id": "test"},
                "challenge": challenge_b64,
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400
        assert "verification failed" in resp.get_json()["error"].lower()

    def test_complete_unauthenticated(self, anon_client):
        resp = anon_client.post(
            "/auth/me/webauthn/complete",
            json={
                "credential": {"id": "test"},
                "challenge": "abc",
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Claim WebAuthn complete (/auth/register/claim/webauthn/complete)
# ---------------------------------------------------------------------------


class TestClaimWebAuthnComplete:
    """Tests for /auth/register/claim/webauthn/complete — claim flow."""

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_claim_success(self, mock_config, mock_verify, auth_app, auth_db):
        """Successful claim creates user, generates backup codes, sets session."""
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        fake_cred = _make_fake_webauthn_cred()
        mock_verify.return_value = fake_cred

        _create_approved_access_request(
            auth_db,
            "claimuser_wn",
            "AAAA1111BBBB2222",
            contact_email="claim@test.com",
        )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={
                "username": "claimuser_wn",
                "claim_token": "AAAA-1111-BBBB-2222",
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x01\x02\x03\x04"),
                "auth_type": "passkey",
                "recovery_email": "recover@test.com",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["username"] == "claimuser_wn"
        assert "backup_codes" in data
        assert len(data["backup_codes"]) > 0
        assert data["recovery_enabled"] is True
        assert "warning" in data

        # Verify session cookie set (user logged in)
        # Verify session cookie was set in the response
        set_cookie_header = resp.headers.get("Set-Cookie", "")
        assert "audiobooks_session=" in set_cookie_header

        # Verify user created with correct auth_type
        repo = UserRepository(auth_db)
        user = repo.get_by_username("claimuser_wn")
        assert user is not None
        assert user.auth_type == AuthType.PASSKEY

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_claim_fido2_type(self, mock_config, mock_verify, auth_app, auth_db):
        """Claim with fido2 auth_type creates FIDO2 user."""
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        fake_cred = _make_fake_webauthn_cred()
        mock_verify.return_value = fake_cred

        _create_approved_access_request(
            auth_db,
            "claimfido2_wn",
            "CCCC3333DDDD4444",
            contact_email="fido2@test.com",
        )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={
                "username": "claimfido2_wn",
                "claim_token": "CCCC3333DDDD4444",
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x05\x06\x07\x08"),
                "auth_type": "fido2",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # No recovery info — should get the strong warning
        assert data["recovery_enabled"] is False
        assert "IMPORTANT" in data["warning"]

        repo = UserRepository(auth_db)
        user = repo.get_by_username("claimfido2_wn")
        assert user.auth_type == AuthType.FIDO2

    def test_claim_missing_fields(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={"username": "x"},
        )
        assert resp.status_code == 400

    def test_claim_invalid_auth_type(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={
                "username": "x",
                "claim_token": "AAAA",
                "credential": {"id": "x"},
                "challenge": "abc",
                "auth_type": "totp",
            },
        )
        assert resp.status_code == 400

    def test_claim_invalid_token(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={
                "username": "nonexistent",
                "claim_token": "XXXX-YYYY-ZZZZ-1111",
                "credential": {"id": "x"},
                "challenge": bytes_to_base64url(b"\x01"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code in (400, 404)

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_claim_verification_fails(
        self, mock_config, mock_verify, auth_app, auth_db
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = None  # Verification failure

        _create_approved_access_request(
            auth_db,
            "claimfail_wn",
            "FAIL1111FAIL2222",
            contact_email="fail@test.com",
        )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={
                "username": "claimfail_wn",
                "claim_token": "FAIL1111FAIL2222",
                "credential": {"id": "test"},
                "challenge": bytes_to_base64url(b"\x01\x02"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400
        assert "verification failed" in resp.get_json()["error"].lower()

    def test_claim_expired_invitation(self, auth_app, auth_db):
        """Expired claim token returns appropriate error."""
        _create_approved_access_request(
            auth_db,
            "claimexpired_wn",
            "EXPD1111EXPD2222",
            claim_expires_at=datetime.now() - timedelta(hours=1),
            contact_email="expired@test.com",
        )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/claim/webauthn/complete",
            json={
                "username": "claimexpired_wn",
                "claim_token": "EXPD1111EXPD2222",
                "credential": {"id": "test"},
                "challenge": bytes_to_base64url(b"\x01"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400
        assert "expired" in resp.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# 3. Register WebAuthn begin (/auth/register/webauthn/begin)
# ---------------------------------------------------------------------------


class TestRegisterWebAuthnBegin:
    """Tests for /auth/register/webauthn/begin — self-registration start."""

    @patch("api_modular.auth.webauthn_registration_options")
    @patch("api_modular.auth.get_webauthn_config")
    def test_begin_success(self, mock_config, mock_reg_opts, auth_app, auth_db):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_reg_opts.return_value = ('{"rp": {"id": "localhost"}}', b"\xab\xcd\xef")

        # Create a pending registration
        from auth import hash_token

        raw_token = "reg_webauthn_token_1"
        with auth_db.connection() as conn:
            conn.execute(
                """INSERT INTO pending_registrations
                   (username, token_hash, created_at, expires_at)
                   VALUES (?, ?, datetime('now'),
                           datetime('now', '+1 hour'))""",
                ("reguser_wn", hash_token(raw_token)),
            )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/begin",
            json={"token": raw_token, "auth_type": "passkey"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "options" in data
        assert "challenge" in data
        assert "token" in data  # Token returned for completion step

    def test_begin_missing_token(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/begin",
            json={"auth_type": "passkey"},
        )
        assert resp.status_code == 400
        assert "token" in resp.get_json()["error"].lower()

    def test_begin_invalid_token(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/begin",
            json={"token": "nonexistent_token", "auth_type": "passkey"},
        )
        assert resp.status_code == 400

    def test_begin_invalid_auth_type(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/begin",
            json={"token": "x", "auth_type": "magic_link"},
        )
        assert resp.status_code == 400

    def test_begin_expired_token(self, auth_app, auth_db):
        from auth import hash_token

        raw_token = "reg_expired_token_1"
        with auth_db.connection() as conn:
            conn.execute(
                """INSERT INTO pending_registrations
                   (username, token_hash, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    "regexpired_wn",
                    hash_token(raw_token),
                    (datetime.now() - timedelta(hours=2)).isoformat(),
                    (datetime.now() - timedelta(hours=1)).isoformat(),
                ),
            )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/begin",
            json={"token": raw_token, "auth_type": "passkey"},
        )
        assert resp.status_code == 400
        assert "expired" in resp.get_json()["error"].lower()

    def test_begin_no_body(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/begin",
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. Register WebAuthn complete (/auth/register/webauthn/complete)
# ---------------------------------------------------------------------------


class TestRegisterWebAuthnComplete:
    """Tests for /auth/register/webauthn/complete — self-registration complete."""

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_passkey_success(
        self, mock_config, mock_verify, auth_app, auth_db
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        fake_cred = _make_fake_webauthn_cred()
        mock_verify.return_value = fake_cred

        from auth import hash_token

        raw_token = "reg_complete_token_1"
        with auth_db.connection() as conn:
            conn.execute(
                """INSERT INTO pending_registrations
                   (username, token_hash, created_at, expires_at)
                   VALUES (?, ?, datetime('now'),
                           datetime('now', '+1 hour'))""",
                ("regcomplete_wn", hash_token(raw_token)),
            )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": raw_token,
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x01\x02\x03"),
                "auth_type": "passkey",
                "recovery_email": "reg@test.com",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["username"] == "regcomplete_wn"
        assert "backup_codes" in data
        assert len(data["backup_codes"]) > 0
        assert data["recovery_enabled"] is True

        # Verify user created with passkey type
        repo = UserRepository(auth_db)
        user = repo.get_by_username("regcomplete_wn")
        assert user is not None
        assert user.auth_type == AuthType.PASSKEY

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_fido2_success(self, mock_config, mock_verify, auth_app, auth_db):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        fake_cred = _make_fake_webauthn_cred()
        mock_verify.return_value = fake_cred

        from auth import hash_token

        raw_token = "reg_fido2_token_1"
        with auth_db.connection() as conn:
            conn.execute(
                """INSERT INTO pending_registrations
                   (username, token_hash, created_at, expires_at)
                   VALUES (?, ?, datetime('now'),
                           datetime('now', '+1 hour'))""",
                ("regfido2_wn", hash_token(raw_token)),
            )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": raw_token,
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x04\x05\x06"),
                "auth_type": "fido2",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        # No recovery — strong warning expected
        assert data["recovery_enabled"] is False
        assert "IMPORTANT" in data["warning"]

        repo = UserRepository(auth_db)
        user = repo.get_by_username("regfido2_wn")
        assert user.auth_type == AuthType.FIDO2

    def test_complete_missing_fields(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={"token": "x"},
        )
        assert resp.status_code == 400

    def test_complete_invalid_token(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": "nonexistent",
                "credential": {"id": "x"},
                "challenge": bytes_to_base64url(b"\x01"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400

    def test_complete_expired_token(self, auth_app, auth_db):
        from auth import hash_token

        raw_token = "reg_expired_complete_1"
        with auth_db.connection() as conn:
            conn.execute(
                """INSERT INTO pending_registrations
                   (username, token_hash, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    "regexpcomplete_wn",
                    hash_token(raw_token),
                    (datetime.now() - timedelta(hours=2)).isoformat(),
                    (datetime.now() - timedelta(hours=1)).isoformat(),
                ),
            )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": raw_token,
                "credential": {"id": "x"},
                "challenge": bytes_to_base64url(b"\x01"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400
        assert "expired" in resp.get_json()["error"].lower()

    @patch("api_modular.auth.webauthn_verify_registration")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_verification_fails(
        self, mock_config, mock_verify, auth_app, auth_db
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = None

        from auth import hash_token

        raw_token = "reg_verify_fail_1"
        with auth_db.connection() as conn:
            conn.execute(
                """INSERT INTO pending_registrations
                   (username, token_hash, created_at, expires_at)
                   VALUES (?, ?, datetime('now'),
                           datetime('now', '+1 hour'))""",
                ("regverifyfail_wn", hash_token(raw_token)),
            )

        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": raw_token,
                "credential": {"id": "test"},
                "challenge": bytes_to_base64url(b"\x01\x02"),
                "auth_type": "passkey",
            },
        )
        assert resp.status_code == 400
        assert "verification failed" in resp.get_json()["error"].lower()

    def test_complete_invalid_auth_type(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/register/webauthn/complete",
            json={
                "token": "x",
                "credential": {"id": "x"},
                "challenge": bytes_to_base64url(b"\x01"),
                "auth_type": "totp",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. Login WebAuthn begin (/auth/login/webauthn/begin)
# ---------------------------------------------------------------------------


class TestLoginWebAuthnBegin:
    """Tests for /auth/login/webauthn/begin — start WebAuthn login."""

    @patch("api_modular.auth.webauthn_authentication_options")
    @patch("api_modular.auth.get_webauthn_config")
    def test_begin_passkey_success(
        self, mock_config, mock_auth_opts, auth_app, auth_db
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_auth_opts.return_value = ('{"rpId": "localhost"}', b"\xab\xcd")

        _make_passkey_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/begin",
            json={"username": "passkeylogin_fix"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "options" in data
        assert "challenge" in data

    @patch("api_modular.auth.webauthn_authentication_options")
    @patch("api_modular.auth.get_webauthn_config")
    def test_begin_fido2_success(self, mock_config, mock_auth_opts, auth_app, auth_db):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_auth_opts.return_value = ('{"rpId": "localhost"}', b"\xef\x01")

        _make_fido2_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/begin",
            json={"username": "fido2login_fix"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "options" in data
        assert "challenge" in data

    def test_begin_nonexistent_user(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/begin",
            json={"username": "nosuchuser_wn"},
        )
        assert resp.status_code == 401

    def test_begin_totp_user(self, auth_app):
        """TOTP user cannot use WebAuthn login."""
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/begin",
            json={"username": "testuser1"},  # seed TOTP user
        )
        assert resp.status_code == 400
        assert "passkey" in resp.get_json()["error"].lower()

    def test_begin_missing_username(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/begin",
            json={"not_username": "val"},
        )
        assert resp.status_code == 400
        assert "username" in resp.get_json()["error"].lower()

    def test_begin_no_body(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/begin",
            content_type="application/json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 6. Login WebAuthn complete (/auth/login/webauthn/complete)
# ---------------------------------------------------------------------------


class TestLoginWebAuthnComplete:
    """Tests for /auth/login/webauthn/complete — complete WebAuthn login."""

    @patch("api_modular.auth.webauthn_verify_authentication")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_success(self, mock_config, mock_verify, auth_app, auth_db):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = 6  # new sign_count

        _make_passkey_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "passkeylogin_fix",
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x01\x02\x03"),
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["user"]["username"] == "passkeylogin_fix"
        assert "can_download" in data["user"]
        assert "is_admin" in data["user"]

        # Verify session cookie set
        # Verify session cookie was set in the response
        set_cookie_header = resp.headers.get("Set-Cookie", "")
        assert "audiobooks_session=" in set_cookie_header

        # Verify sign_count updated in DB
        repo = UserRepository(auth_db)
        user = repo.get_by_username("passkeylogin_fix")
        stored_cred = WebAuthnCredential.from_json(user.auth_credential.decode("utf-8"))
        assert stored_cred.sign_count == 6

    @patch("api_modular.auth.webauthn_verify_authentication")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_remember_me_false(
        self, mock_config, mock_verify, auth_app, auth_db
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = 7

        _make_passkey_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "passkeylogin_fix",
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x04\x05\x06"),
                "remember_me": False,
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    @patch("api_modular.auth.webauthn_verify_authentication")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_fido2_user(self, mock_config, mock_verify, auth_app, auth_db):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = 4

        _make_fido2_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "fido2login_fix",
                "credential": {"id": "test", "type": "public-key"},
                "challenge": bytes_to_base64url(b"\x07\x08\x09"),
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True
        assert resp.get_json()["user"]["username"] == "fido2login_fix"

    @patch("api_modular.auth.webauthn_verify_authentication")
    @patch("api_modular.auth.get_webauthn_config")
    def test_complete_verification_fails(
        self, mock_config, mock_verify, auth_app, auth_db
    ):
        mock_config.return_value = ("localhost", "TestApp", "http://localhost:5001")
        mock_verify.return_value = None  # Verification failure

        _make_passkey_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "passkeylogin_fix",
                "credential": {"id": "test"},
                "challenge": bytes_to_base64url(b"\x01\x02"),
            },
        )
        assert resp.status_code == 401
        assert "invalid" in resp.get_json()["error"].lower()

    def test_complete_missing_fields(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={"username": "x"},
        )
        assert resp.status_code == 400

    def test_complete_nonexistent_user(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "nosuchuser_wn",
                "credential": {"id": "test"},
                "challenge": bytes_to_base64url(b"\x01"),
            },
        )
        assert resp.status_code == 401

    def test_complete_totp_user(self, auth_app):
        """TOTP user cannot complete WebAuthn login."""
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "testuser1",
                "credential": {"id": "test"},
                "challenge": bytes_to_base64url(b"\x01"),
            },
        )
        assert resp.status_code == 401

    def test_complete_no_body(self, auth_app):
        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_complete_invalid_challenge_format(self, auth_app, auth_db):
        """Invalid base64url challenge format returns error (400 or 401)."""
        _make_passkey_user(auth_db)

        client = auth_app.test_client()
        resp = client.post(
            "/auth/login/webauthn/complete",
            json={
                "username": "passkeylogin_fix",
                "credential": {"id": "test"},
                "challenge": "!!!not-base64!!!",
            },
        )
        # Returns 400 (invalid challenge format) or 401 (credential parse error)
        assert resp.status_code in (400, 401)
