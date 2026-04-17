"""
Extended unit tests for auth.passkey module — targeting uncovered lines.

Covers: verify_registration exception path (lines 223-257),
verify_authentication exception path (lines 336-358).
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth.passkey import (  # noqa: E402
    WebAuthnCredential,
    create_registration_options,
    verify_registration,
    create_authentication_options,
    verify_authentication,
    get_pending_challenge,
    _pending_challenges,
)


@pytest.fixture(autouse=True)
def clear_challenges():
    """Clear pending challenges before and after each test."""
    _pending_challenges.clear()
    yield
    _pending_challenges.clear()


class TestVerifyRegistrationExceptionPath:
    """Test lines 223-257: verify_registration try/except block."""

    def test_invalid_credential_json_returns_none(self):
        """Lines 251-257: Invalid credential JSON triggers exception, returns None."""
        _, challenge = create_registration_options(username="testuser")

        # Pass garbage JSON that will fail parse_registration_credential_json
        result = verify_registration(
            credential_json='{"invalid": "not a valid webauthn response"}',
            expected_challenge=challenge,
        )
        assert result is None

    def test_empty_json_object_returns_none(self):
        """Lines 251-257: Empty JSON object fails parsing."""
        _, challenge = create_registration_options(username="testuser")

        result = verify_registration(credential_json="{}", expected_challenge=challenge)
        assert result is None

    def test_malformed_credential_returns_none(self):
        """Lines 223-257: Malformed but structurally valid JSON fails verification."""
        _, challenge = create_registration_options(username="testuser")

        # Structurally looks like a credential but has invalid data
        fake_credential = json.dumps(
            {
                "id": "AQID",
                "rawId": "AQID",
                "type": "public-key",
                "response": {
                    "attestationObject": "AAAA",
                    "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uY3JlYXRlIiwiY2hhbGxlbmdlIjoiQUFBQSIsIm9yaWdpbiI6Imh0dHA6Ly9sb2NhbGhvc3Q6NTAwMSJ9",
                },
            }
        )

        result = verify_registration(credential_json=fake_credential, expected_challenge=challenge)
        assert result is None

    def test_challenge_cleaned_up_on_success_path_entry(self):
        """Lines 223-236: The try block is entered (challenge valid + not expired + is_registration)."""
        _, challenge = create_registration_options(username="testuser")

        # Confirm the challenge exists before verification attempt
        assert get_pending_challenge(challenge) is not None

        # The verification itself will fail (invalid credential), but the try
        # block is entered, confirming lines 223+ are exercised
        result = verify_registration(credential_json="{}", expected_challenge=challenge)
        assert result is None

    def test_verify_registration_prints_error(self, capsys):
        """Lines 255-256: Exception is printed for debugging."""
        _, challenge = create_registration_options(username="testuser")

        verify_registration(credential_json="not even json", expected_challenge=challenge)
        # The exception handler prints the error
        captured = capsys.readouterr()
        assert "WebAuthn registration verification failed" in captured.out

    def test_successful_registration_with_mocked_webauthn(self):
        """Lines 223-249: Full success path with mocked webauthn library."""
        _, challenge = create_registration_options(username="testuser")

        mock_verification = MagicMock()
        mock_verification.credential_id = b"\x01\x02\x03"
        mock_verification.credential_public_key = b"\x04\x05\x06"
        mock_verification.sign_count = 0

        mock_credential = MagicMock()
        mock_credential.response.transports = ["internal", "hybrid"]

        with (
            patch("auth.passkey.parse_registration_credential_json", return_value=mock_credential),
            patch("auth.passkey.verify_registration_response", return_value=mock_verification),
        ):
            result = verify_registration(
                credential_json="{}",
                expected_challenge=challenge,
                expected_origin="http://localhost:5001",
                expected_rp_id="localhost",
            )
            assert result is not None
            assert isinstance(result, WebAuthnCredential)
            assert result.credential_id == b"\x01\x02\x03"
            assert result.sign_count == 0
            assert result.transports == ["internal", "hybrid"]

    def test_successful_registration_no_transports(self):
        """Lines 239-241: Success path when transports is None/empty."""
        _, challenge = create_registration_options(username="testuser")

        mock_verification = MagicMock()
        mock_verification.credential_id = b"\x01\x02\x03"
        mock_verification.credential_public_key = b"\x04\x05\x06"
        mock_verification.sign_count = 0

        mock_credential = MagicMock()
        mock_credential.response.transports = None

        with (
            patch("auth.passkey.parse_registration_credential_json", return_value=mock_credential),
            patch("auth.passkey.verify_registration_response", return_value=mock_verification),
        ):
            result = verify_registration(credential_json="{}", expected_challenge=challenge)
            assert result is not None
            assert result.transports == []

    def test_challenge_deleted_after_success(self):
        """Line 236: Challenge is deleted after successful verification."""
        _, challenge = create_registration_options(username="testuser")

        mock_verification = MagicMock()
        mock_verification.credential_id = b"\x01"
        mock_verification.credential_public_key = b"\x02"
        mock_verification.sign_count = 0
        mock_credential = MagicMock()
        mock_credential.response.transports = []

        with (
            patch("auth.passkey.parse_registration_credential_json", return_value=mock_credential),
            patch("auth.passkey.verify_registration_response", return_value=mock_verification),
        ):
            verify_registration(credential_json="{}", expected_challenge=challenge)

        # Challenge should be consumed
        assert get_pending_challenge(challenge) is None


class TestVerifyAuthenticationExceptionPath:
    """Test lines 336-358: verify_authentication try/except block."""

    def test_invalid_credential_json_returns_none(self):
        """Lines 355-358: Invalid credential JSON returns None."""
        _, challenge = create_authentication_options(
            user_id=1, credential_id=b"\x01\x02", username="user1"
        )

        result = verify_authentication(
            credential_json='{"invalid": true}',
            expected_challenge=challenge,
            credential_public_key=b"\x01\x02",
            credential_current_sign_count=0,
        )
        assert result is None

    def test_empty_json_returns_none(self):
        """Lines 336-358: Empty JSON fails parsing."""
        _, challenge = create_authentication_options(
            user_id=1, credential_id=b"\x01\x02", username="user1"
        )

        result = verify_authentication(
            credential_json="{}",
            expected_challenge=challenge,
            credential_public_key=b"\x01\x02",
            credential_current_sign_count=0,
        )
        assert result is None

    def test_verify_authentication_prints_error(self, capsys):
        """Line 357: Exception type is printed."""
        _, challenge = create_authentication_options(
            user_id=1, credential_id=b"\x01\x02", username="user1"
        )

        verify_authentication(
            credential_json="not json",
            expected_challenge=challenge,
            credential_public_key=b"\x01\x02",
            credential_current_sign_count=0,
        )
        captured = capsys.readouterr()
        assert "WebAuthn authentication verification failed" in captured.out

    def test_successful_authentication_with_mocked_webauthn(self):
        """Lines 336-353: Full success path with mocked webauthn."""
        _, challenge = create_authentication_options(
            user_id=1, credential_id=b"\x01\x02", username="user1"
        )

        mock_verification = MagicMock()
        mock_verification.new_sign_count = 5

        mock_credential = MagicMock()

        with (
            patch(
                "auth.passkey.parse_authentication_credential_json", return_value=mock_credential
            ),
            patch("auth.passkey.verify_authentication_response", return_value=mock_verification),
        ):
            result = verify_authentication(
                credential_json="{}",
                expected_challenge=challenge,
                credential_public_key=b"\x01\x02",
                credential_current_sign_count=0,
            )
            assert result == 5

    def test_challenge_deleted_after_successful_auth(self):
        """Line 351: Challenge removed after successful auth."""
        _, challenge = create_authentication_options(
            user_id=1, credential_id=b"\x01\x02", username="user1"
        )

        mock_verification = MagicMock()
        mock_verification.new_sign_count = 1
        mock_credential = MagicMock()

        with (
            patch(
                "auth.passkey.parse_authentication_credential_json", return_value=mock_credential
            ),
            patch("auth.passkey.verify_authentication_response", return_value=mock_verification),
        ):
            verify_authentication(
                credential_json="{}",
                expected_challenge=challenge,
                credential_public_key=b"\x01\x02",
                credential_current_sign_count=0,
            )

        assert get_pending_challenge(challenge) is None

    def test_malformed_credential_json_returns_none(self):
        """Lines 336-358: Structurally wrong JSON fails."""
        _, challenge = create_authentication_options(
            user_id=1, credential_id=b"\x01\x02", username="user1"
        )

        fake = json.dumps(
            {
                "id": "AQID",
                "rawId": "AQID",
                "type": "public-key",
                "response": {
                    "authenticatorData": "AAAA",
                    "clientDataJSON": "eyJ0eXBlIjoid2ViYXV0aG4uZ2V0IiwiY2hhbGxlbmdlIjoiQUFBQSIsIm9yaWdpbiI6Imh0dHA6Ly9sb2NhbGhvc3Q6NTAwMSJ9",
                    "signature": "AAAA",
                },
            }
        )

        result = verify_authentication(
            credential_json=fake,
            expected_challenge=challenge,
            credential_public_key=b"\x01\x02",
            credential_current_sign_count=0,
        )
        assert result is None
