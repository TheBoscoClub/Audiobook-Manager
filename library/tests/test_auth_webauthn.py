"""
Unit tests for the WebAuthn/Passkey authentication module.

Tests cover:
- Registration options generation
- Authentication options generation
- Challenge management (creation, expiry, cleanup)
- Credential serialization/deserialization
- Integration with auth database (via AuthType enum)
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add library directory to path
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth import (
    AuthDatabase,
    AuthType,
    User,
    UserRepository,
    hash_token,
    generate_session_token,
)

from auth.webauthn import (
    WebAuthnCredential,
    WebAuthnChallenge,
    create_registration_options,
    verify_registration,
    create_authentication_options,
    verify_authentication,
    get_pending_challenge,
    clear_challenge,
    cleanup_expired_challenges,
    _pending_challenges,
    CHALLENGE_TIMEOUT_SECONDS,
    DEFAULT_RP_ID,
    DEFAULT_RP_NAME,
    DEFAULT_ORIGIN,
)

from webauthn.helpers import bytes_to_base64url, base64url_to_bytes


@pytest.fixture
def temp_db():
    """Create a temporary encrypted database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test-auth.db')
        key_path = os.path.join(tmpdir, 'test.key')
        db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
        db.initialize()
        yield db


@pytest.fixture(autouse=True)
def clear_challenges():
    """Clear pending challenges before and after each test."""
    _pending_challenges.clear()
    yield
    _pending_challenges.clear()


class TestWebAuthnCredential:
    """Tests for WebAuthnCredential dataclass."""

    def test_credential_creation(self):
        """Test creating a WebAuthn credential."""
        cred = WebAuthnCredential(
            credential_id=b'\x01\x02\x03\x04',
            public_key=b'\x05\x06\x07\x08',
            sign_count=0,
            transports=['internal', 'hybrid'],
            created_at=datetime.now()
        )
        assert cred.credential_id == b'\x01\x02\x03\x04'
        assert cred.public_key == b'\x05\x06\x07\x08'
        assert cred.sign_count == 0
        assert 'internal' in cred.transports

    def test_credential_json_serialization(self):
        """Test serializing credential to JSON."""
        now = datetime.now()
        cred = WebAuthnCredential(
            credential_id=b'\x01\x02\x03\x04',
            public_key=b'\x05\x06\x07\x08',
            sign_count=5,
            transports=['usb'],
            created_at=now
        )
        json_str = cred.to_json()
        data = json.loads(json_str)

        assert data['credential_id'] == bytes_to_base64url(b'\x01\x02\x03\x04')
        assert data['public_key'] == bytes_to_base64url(b'\x05\x06\x07\x08')
        assert data['sign_count'] == 5
        assert data['transports'] == ['usb']
        assert data['created_at'] == now.isoformat()

    def test_credential_json_deserialization(self):
        """Test deserializing credential from JSON."""
        now = datetime.now()
        original = WebAuthnCredential(
            credential_id=b'\xaa\xbb\xcc\xdd',
            public_key=b'\xee\xff\x00\x11',
            sign_count=10,
            transports=['nfc', 'ble'],
            created_at=now
        )
        json_str = original.to_json()
        restored = WebAuthnCredential.from_json(json_str)

        assert restored.credential_id == original.credential_id
        assert restored.public_key == original.public_key
        assert restored.sign_count == original.sign_count
        assert restored.transports == original.transports
        # Compare ISO format strings to avoid microsecond issues
        assert restored.created_at.isoformat() == now.isoformat()

    def test_credential_empty_transports(self):
        """Test credential with no transports."""
        cred = WebAuthnCredential(
            credential_id=b'\x01\x02',
            public_key=b'\x03\x04',
            sign_count=0,
            transports=[],
            created_at=datetime.now()
        )
        json_str = cred.to_json()
        restored = WebAuthnCredential.from_json(json_str)
        assert restored.transports == []


class TestWebAuthnChallenge:
    """Tests for WebAuthnChallenge dataclass."""

    def test_challenge_creation(self):
        """Test creating a challenge."""
        challenge = WebAuthnChallenge(
            challenge=b'\x01\x02\x03',
            user_id=None,
            username='testuser',
            expires_at=datetime.now() + timedelta(minutes=5),
            is_registration=True
        )
        assert challenge.challenge == b'\x01\x02\x03'
        assert challenge.user_id is None
        assert challenge.username == 'testuser'
        assert challenge.is_registration is True

    def test_challenge_not_expired(self):
        """Test challenge is not expired when within timeout."""
        challenge = WebAuthnChallenge(
            challenge=b'\x01\x02\x03',
            user_id=1,
            username='user',
            expires_at=datetime.now() + timedelta(minutes=5),
            is_registration=False
        )
        assert challenge.is_expired() is False

    def test_challenge_expired(self):
        """Test challenge is expired after timeout."""
        challenge = WebAuthnChallenge(
            challenge=b'\x01\x02\x03',
            user_id=1,
            username='user',
            expires_at=datetime.now() - timedelta(seconds=1),
            is_registration=False
        )
        assert challenge.is_expired() is True


class TestRegistrationOptions:
    """Tests for registration options generation."""

    def test_create_registration_options_platform(self):
        """Test generating registration options for platform authenticator (passkey)."""
        options_json, challenge = create_registration_options(
            username='testuser',
            authenticator_type='platform'
        )

        # Parse the options
        options = json.loads(options_json)

        assert options['rp']['id'] == DEFAULT_RP_ID
        assert options['rp']['name'] == DEFAULT_RP_NAME
        assert options['user']['name'] == 'testuser'
        assert options['user']['displayName'] == 'testuser'
        assert 'challenge' in options
        assert options['timeout'] == CHALLENGE_TIMEOUT_SECONDS * 1000

        # Check authenticator selection
        auth_sel = options.get('authenticatorSelection', {})
        assert auth_sel.get('authenticatorAttachment') == 'platform'
        assert auth_sel.get('userVerification') == 'required'

    def test_create_registration_options_cross_platform(self):
        """Test generating registration options for cross-platform authenticator (FIDO2 key)."""
        options_json, challenge = create_registration_options(
            username='fido2user',
            authenticator_type='cross-platform'
        )

        options = json.loads(options_json)
        auth_sel = options.get('authenticatorSelection', {})
        assert auth_sel.get('authenticatorAttachment') == 'cross-platform'

    def test_create_registration_options_stores_challenge(self):
        """Test that registration options stores challenge for later verification."""
        options_json, challenge = create_registration_options(username='testuser')

        # Challenge should be stored
        pending = get_pending_challenge(challenge)
        assert pending is not None
        assert pending.username == 'testuser'
        assert pending.is_registration is True
        assert pending.user_id is None

    def test_create_registration_options_with_exclude_list(self):
        """Test generating options with existing credentials to exclude."""
        existing = [b'\x01\x02\x03\x04', b'\x05\x06\x07\x08']
        options_json, challenge = create_registration_options(
            username='testuser',
            existing_credentials=existing
        )

        options = json.loads(options_json)
        exclude = options.get('excludeCredentials', [])
        assert len(exclude) == 2

    def test_create_registration_options_custom_rp(self):
        """Test generating options with custom relying party."""
        options_json, challenge = create_registration_options(
            username='testuser',
            rp_id='example.com',
            rp_name='Example App'
        )

        options = json.loads(options_json)
        assert options['rp']['id'] == 'example.com'
        assert options['rp']['name'] == 'Example App'


class TestAuthenticationOptions:
    """Tests for authentication options generation."""

    def test_create_authentication_options(self):
        """Test generating authentication options."""
        credential_id = b'\xaa\xbb\xcc\xdd\xee\xff'
        options_json, challenge = create_authentication_options(
            user_id=123,
            credential_id=credential_id,
            username='authuser'
        )

        options = json.loads(options_json)

        assert options['rpId'] == DEFAULT_RP_ID
        assert 'challenge' in options
        assert options['timeout'] == CHALLENGE_TIMEOUT_SECONDS * 1000
        assert options['userVerification'] == 'required'

        # Check allow credentials
        allow = options.get('allowCredentials', [])
        assert len(allow) == 1
        assert allow[0]['id'] == bytes_to_base64url(credential_id)

    def test_create_authentication_options_stores_challenge(self):
        """Test that authentication options stores challenge."""
        credential_id = b'\x01\x02\x03'
        options_json, challenge = create_authentication_options(
            user_id=456,
            credential_id=credential_id,
            username='authuser'
        )

        pending = get_pending_challenge(challenge)
        assert pending is not None
        assert pending.user_id == 456
        assert pending.username == 'authuser'
        assert pending.is_registration is False


class TestChallengeManagement:
    """Tests for challenge storage and cleanup."""

    def test_get_pending_challenge(self):
        """Test retrieving a pending challenge."""
        options_json, challenge = create_registration_options(username='user1')

        pending = get_pending_challenge(challenge)
        assert pending is not None
        assert pending.username == 'user1'

    def test_get_pending_challenge_not_found(self):
        """Test retrieving a non-existent challenge."""
        pending = get_pending_challenge(b'\x00\x00\x00\x00')
        assert pending is None

    def test_get_pending_challenge_expired(self):
        """Test retrieving an expired challenge returns None."""
        options_json, challenge = create_registration_options(username='user1')

        # Manually expire the challenge
        challenge_key = bytes_to_base64url(challenge)
        _pending_challenges[challenge_key] = WebAuthnChallenge(
            challenge=challenge,
            user_id=None,
            username='user1',
            expires_at=datetime.now() - timedelta(seconds=1),
            is_registration=True
        )

        pending = get_pending_challenge(challenge)
        assert pending is None

    def test_clear_challenge(self):
        """Test clearing a specific challenge."""
        options_json, challenge = create_registration_options(username='user1')

        # Should exist before clear
        assert get_pending_challenge(challenge) is not None

        clear_challenge(challenge)

        # Should not exist after clear
        assert get_pending_challenge(challenge) is None

    def test_clear_nonexistent_challenge(self):
        """Test clearing a non-existent challenge doesn't error."""
        # Should not raise
        clear_challenge(b'\xff\xff\xff\xff')

    def test_cleanup_expired_challenges(self):
        """Test cleanup removes expired challenges."""
        # Create two challenges
        _, challenge1 = create_registration_options(username='user1')
        _, challenge2 = create_registration_options(username='user2')

        # Expire challenge1
        challenge1_key = bytes_to_base64url(challenge1)
        _pending_challenges[challenge1_key] = WebAuthnChallenge(
            challenge=challenge1,
            user_id=None,
            username='user1',
            expires_at=datetime.now() - timedelta(minutes=10),
            is_registration=True
        )

        # Run cleanup
        removed = cleanup_expired_challenges()

        assert removed == 1
        assert get_pending_challenge(challenge1) is None
        assert get_pending_challenge(challenge2) is not None

    def test_cleanup_no_expired(self):
        """Test cleanup with no expired challenges."""
        _, challenge1 = create_registration_options(username='user1')

        removed = cleanup_expired_challenges()
        assert removed == 0
        assert get_pending_challenge(challenge1) is not None


class TestRegistrationVerification:
    """Tests for registration response verification."""

    def test_verify_registration_invalid_challenge(self):
        """Test verification fails with unknown challenge."""
        result = verify_registration(
            credential_json='{}',
            expected_challenge=b'\x00\x00\x00\x00'
        )
        assert result is None

    def test_verify_registration_expired_challenge(self):
        """Test verification fails with expired challenge."""
        _, challenge = create_registration_options(username='user1')

        # Expire the challenge
        challenge_key = bytes_to_base64url(challenge)
        _pending_challenges[challenge_key] = WebAuthnChallenge(
            challenge=challenge,
            user_id=None,
            username='user1',
            expires_at=datetime.now() - timedelta(seconds=1),
            is_registration=True
        )

        result = verify_registration(
            credential_json='{}',
            expected_challenge=challenge
        )
        assert result is None

    def test_verify_registration_wrong_type(self):
        """Test verification fails if challenge was for authentication."""
        _, challenge = create_authentication_options(
            user_id=1,
            credential_id=b'\x01\x02',
            username='user1'
        )

        result = verify_registration(
            credential_json='{}',
            expected_challenge=challenge
        )
        assert result is None


class TestAuthenticationVerification:
    """Tests for authentication response verification."""

    def test_verify_authentication_invalid_challenge(self):
        """Test verification fails with unknown challenge."""
        result = verify_authentication(
            credential_json='{}',
            expected_challenge=b'\x00\x00\x00\x00',
            credential_public_key=b'\x01\x02',
            credential_current_sign_count=0
        )
        assert result is None

    def test_verify_authentication_expired_challenge(self):
        """Test verification fails with expired challenge."""
        _, challenge = create_authentication_options(
            user_id=1,
            credential_id=b'\x01\x02',
            username='user1'
        )

        # Expire the challenge
        challenge_key = bytes_to_base64url(challenge)
        _pending_challenges[challenge_key] = WebAuthnChallenge(
            challenge=challenge,
            user_id=1,
            username='user1',
            expires_at=datetime.now() - timedelta(seconds=1),
            is_registration=False
        )

        result = verify_authentication(
            credential_json='{}',
            expected_challenge=challenge,
            credential_public_key=b'\x01\x02',
            credential_current_sign_count=0
        )
        assert result is None

    def test_verify_authentication_wrong_type(self):
        """Test verification fails if challenge was for registration."""
        _, challenge = create_registration_options(username='user1')

        result = verify_authentication(
            credential_json='{}',
            expected_challenge=challenge,
            credential_public_key=b'\x01\x02',
            credential_current_sign_count=0
        )
        assert result is None


class TestAuthTypeIntegration:
    """Tests for WebAuthn integration with auth types."""

    def test_auth_type_passkey(self, temp_db):
        """Test user can be created with passkey auth type."""
        # Create a WebAuthn credential
        cred = WebAuthnCredential(
            credential_id=b'\x01\x02\x03\x04',
            public_key=b'\x05\x06\x07\x08',
            sign_count=0,
            transports=['internal'],
            created_at=datetime.now()
        )

        # Create user with passkey auth
        user = User(
            id=None,
            username='passkeyuser',
            auth_type=AuthType.PASSKEY,
            auth_credential=cred.to_json().encode('utf-8')
        )
        user.save(temp_db)
        assert user.id is not None

        # Retrieve and verify using repository
        repo = UserRepository(temp_db)
        retrieved = repo.get_by_username('passkeyuser')
        assert retrieved is not None
        assert retrieved.auth_type == AuthType.PASSKEY

        # Deserialize credential
        stored_cred = WebAuthnCredential.from_json(
            retrieved.auth_credential.decode('utf-8')
        )
        assert stored_cred.credential_id == b'\x01\x02\x03\x04'

    def test_auth_type_fido2(self, temp_db):
        """Test user can be created with fido2 auth type."""
        cred = WebAuthnCredential(
            credential_id=b'\xaa\xbb\xcc\xdd',
            public_key=b'\xee\xff\x00\x11',
            sign_count=0,
            transports=['usb'],
            created_at=datetime.now()
        )

        user = User(
            id=None,
            username='fido2user',
            auth_type=AuthType.FIDO2,
            auth_credential=cred.to_json().encode('utf-8')
        )
        user.save(temp_db)
        assert user.id is not None

        repo = UserRepository(temp_db)
        retrieved = repo.get_by_username('fido2user')
        assert retrieved.auth_type == AuthType.FIDO2

    def test_update_sign_count(self, temp_db):
        """Test updating sign count after authentication."""
        # Create initial credential
        cred = WebAuthnCredential(
            credential_id=b'\x01\x02\x03\x04',
            public_key=b'\x05\x06\x07\x08',
            sign_count=5,
            transports=['internal'],
            created_at=datetime.now()
        )

        user = User(
            id=None,
            username='signcount',
            auth_type=AuthType.PASSKEY,
            auth_credential=cred.to_json().encode('utf-8')
        )
        user.save(temp_db)
        user_id = user.id

        # Simulate authentication by updating sign count
        repo = UserRepository(temp_db)
        retrieved = repo.get_by_id(user_id)
        stored_cred = WebAuthnCredential.from_json(
            retrieved.auth_credential.decode('utf-8')
        )

        # Create updated credential with new sign count
        updated_cred = WebAuthnCredential(
            credential_id=stored_cred.credential_id,
            public_key=stored_cred.public_key,
            sign_count=10,  # Simulated new sign count
            transports=stored_cred.transports,
            created_at=stored_cred.created_at
        )

        # Update in database
        with temp_db.connection() as conn:
            conn.execute(
                "UPDATE users SET auth_credential = ? WHERE id = ?",
                (updated_cred.to_json().encode('utf-8'), user_id)
            )

        # Verify update
        final = repo.get_by_id(user_id)
        final_cred = WebAuthnCredential.from_json(
            final.auth_credential.decode('utf-8')
        )
        assert final_cred.sign_count == 10


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_multiple_users_concurrent_challenges(self):
        """Test multiple users can have concurrent challenges."""
        _, c1 = create_registration_options(username='user1')
        _, c2 = create_registration_options(username='user2')
        _, c3 = create_authentication_options(
            user_id=1, credential_id=b'\x01', username='user3'
        )

        assert len(_pending_challenges) == 3
        assert get_pending_challenge(c1).username == 'user1'
        assert get_pending_challenge(c2).username == 'user2'
        assert get_pending_challenge(c3).username == 'user3'

    def test_challenge_uniqueness(self):
        """Test challenges are unique."""
        challenges = set()
        for i in range(10):
            _, challenge = create_registration_options(username=f'user{i}')
            challenges.add(challenge)

        # All challenges should be unique
        assert len(challenges) == 10

    def test_large_credential_ids(self):
        """Test handling of large credential IDs."""
        large_id = bytes(range(256)) * 4  # 1024 bytes
        cred = WebAuthnCredential(
            credential_id=large_id,
            public_key=b'\x01\x02',
            sign_count=0,
            transports=[],
            created_at=datetime.now()
        )

        json_str = cred.to_json()
        restored = WebAuthnCredential.from_json(json_str)
        assert restored.credential_id == large_id

    def test_unicode_username(self):
        """Test registration with unicode username."""
        options_json, challenge = create_registration_options(
            username='用户名'
        )

        options = json.loads(options_json)
        assert options['user']['name'] == '用户名'

        pending = get_pending_challenge(challenge)
        assert pending.username == '用户名'

    def test_special_characters_username(self):
        """Test registration with special characters in username."""
        username = "user@example.com"
        options_json, challenge = create_registration_options(username=username)

        options = json.loads(options_json)
        assert options['user']['name'] == username
