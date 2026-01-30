"""
WebAuthn/Passkey authentication support.

Implements FIDO2/WebAuthn registration and authentication ceremonies
using the py_webauthn library.

Terminology:
- Passkey: Platform authenticator (Face ID, Touch ID, Windows Hello)
- FIDO2: Roaming authenticator (YubiKey, Titan Security Key)
- Both use the same WebAuthn protocol
"""

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
    base64url_to_bytes,
)
from webauthn.helpers import (
    bytes_to_base64url,
    parse_registration_credential_json,
    parse_authentication_credential_json,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
    RegistrationCredential,
    AuthenticationCredential,
)

from .database import AuthDatabase

# Default WebAuthn configuration (localhost development only)
# Production values are auto-derived from deployment config by
# get_webauthn_config() in api_modular/auth.py
DEFAULT_RP_ID = "localhost"
DEFAULT_RP_NAME = "The Library"
DEFAULT_ORIGIN = "http://localhost:5001"

# Challenge expiry
CHALLENGE_TIMEOUT_SECONDS = 300  # 5 minutes


@dataclass
class WebAuthnCredential:
    """Stored WebAuthn credential data."""
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]
    created_at: datetime

    def to_json(self) -> str:
        """Serialize to JSON for storage."""
        return json.dumps({
            "credential_id": bytes_to_base64url(self.credential_id),
            "public_key": bytes_to_base64url(self.public_key),
            "sign_count": self.sign_count,
            "transports": self.transports,
            "created_at": self.created_at.isoformat(),
        })

    @classmethod
    def from_json(cls, data: str) -> "WebAuthnCredential":
        """Deserialize from JSON storage."""
        obj = json.loads(data)
        return cls(
            credential_id=base64url_to_bytes(obj["credential_id"]),
            public_key=base64url_to_bytes(obj["public_key"]),
            sign_count=obj["sign_count"],
            transports=obj.get("transports", []),
            created_at=datetime.fromisoformat(obj["created_at"]),
        )


@dataclass
class WebAuthnChallenge:
    """Pending WebAuthn challenge."""
    challenge: bytes
    user_id: Optional[int]  # None for registration (user doesn't exist yet)
    username: str
    expires_at: datetime
    is_registration: bool

    def is_expired(self) -> bool:
        return datetime.now() > self.expires_at


# In-memory challenge storage (cleared on restart)
# Key: challenge bytes as base64url string
_pending_challenges: dict[str, WebAuthnChallenge] = {}


def cleanup_expired_challenges() -> int:
    """Remove expired challenges. Returns count of removed challenges."""
    expired = [k for k, v in _pending_challenges.items() if v.is_expired()]
    for k in expired:
        del _pending_challenges[k]
    return len(expired)


def create_registration_options(
    username: str,
    rp_id: str = DEFAULT_RP_ID,
    rp_name: str = DEFAULT_RP_NAME,
    authenticator_type: str = "platform",  # "platform" for passkey, "cross-platform" for security key
    existing_credentials: Optional[list[bytes]] = None,
) -> tuple[str, bytes]:
    """
    Generate WebAuthn registration options.

    Args:
        username: The username being registered
        rp_id: Relying party ID (domain)
        rp_name: Relying party display name
        authenticator_type: "platform" for passkey, "cross-platform" for FIDO2 key
        existing_credentials: List of existing credential IDs to exclude

    Returns:
        Tuple of (options_json, challenge_bytes)
    """
    cleanup_expired_challenges()

    # Generate a random WebAuthn user handle (not the database user ID)
    webauthn_user_id = secrets.token_bytes(32)

    # Set authenticator attachment based on type
    # "platform" (passkey): no restriction — let browser offer all options
    #   (phone, password manager, built-in biometrics, etc.)
    # "cross-platform" (FIDO2): restrict to hardware security keys
    if authenticator_type == "cross-platform":
        authenticator_selection = AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.CROSS_PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
    else:
        authenticator_selection = AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        )

    # Build exclude list from existing credentials
    exclude_list = []
    if existing_credentials:
        for cred_id in existing_credentials:
            exclude_list.append(PublicKeyCredentialDescriptor(id=cred_id))

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=webauthn_user_id,
        user_name=username,
        user_display_name=username,
        attestation=AttestationConveyancePreference.NONE,  # Don't need attestation for our use case
        authenticator_selection=authenticator_selection,
        exclude_credentials=exclude_list if exclude_list else None,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
        timeout=CHALLENGE_TIMEOUT_SECONDS * 1000,  # milliseconds
    )

    # Store challenge for later verification
    challenge_key = bytes_to_base64url(options.challenge)
    _pending_challenges[challenge_key] = WebAuthnChallenge(
        challenge=options.challenge,
        user_id=None,  # User doesn't exist yet during registration
        username=username,
        expires_at=datetime.now() + timedelta(seconds=CHALLENGE_TIMEOUT_SECONDS),
        is_registration=True,
    )

    return options_to_json(options), options.challenge


def verify_registration(
    credential_json: str,
    expected_challenge: bytes,
    expected_origin: str = DEFAULT_ORIGIN,
    expected_rp_id: str = DEFAULT_RP_ID,
) -> Optional[WebAuthnCredential]:
    """
    Verify a WebAuthn registration response.

    Args:
        credential_json: JSON string from navigator.credentials.create()
        expected_challenge: The challenge that was sent
        expected_origin: Expected origin URL
        expected_rp_id: Expected relying party ID

    Returns:
        WebAuthnCredential if successful, None if verification fails
    """
    challenge_key = bytes_to_base64url(expected_challenge)

    # Check challenge exists and is valid
    pending = _pending_challenges.get(challenge_key)
    if not pending:
        return None
    if pending.is_expired():
        del _pending_challenges[challenge_key]
        return None
    if not pending.is_registration:
        return None

    try:
        # Parse the credential JSON
        credential = parse_registration_credential_json(credential_json)

        verification = verify_registration_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
            require_user_verification=True,
        )

        # Clean up the challenge
        del _pending_challenges[challenge_key]

        # Extract transports if available
        transports = []
        if credential.response.transports:
            transports = list(credential.response.transports)

        return WebAuthnCredential(
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=transports,
            created_at=datetime.now(),
        )

    except Exception as e:
        # Log full error for debugging
        import traceback
        print(f"WebAuthn registration verification failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


def create_authentication_options(
    user_id: int,
    credential_id: bytes,
    rp_id: str = DEFAULT_RP_ID,
    username: str = "",
) -> tuple[str, bytes]:
    """
    Generate WebAuthn authentication options.

    Args:
        user_id: Database user ID
        credential_id: The user's registered credential ID
        rp_id: Relying party ID (domain)
        username: Username for challenge storage

    Returns:
        Tuple of (options_json, challenge_bytes)
    """
    cleanup_expired_challenges()

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=credential_id),
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
        timeout=CHALLENGE_TIMEOUT_SECONDS * 1000,
    )

    # Store challenge for later verification
    challenge_key = bytes_to_base64url(options.challenge)
    _pending_challenges[challenge_key] = WebAuthnChallenge(
        challenge=options.challenge,
        user_id=user_id,
        username=username,
        expires_at=datetime.now() + timedelta(seconds=CHALLENGE_TIMEOUT_SECONDS),
        is_registration=False,
    )

    return options_to_json(options), options.challenge


def verify_authentication(
    credential_json: str,
    expected_challenge: bytes,
    credential_public_key: bytes,
    credential_current_sign_count: int,
    expected_origin: str = DEFAULT_ORIGIN,
    expected_rp_id: str = DEFAULT_RP_ID,
) -> Optional[int]:
    """
    Verify a WebAuthn authentication response.

    Args:
        credential_json: JSON string from navigator.credentials.get()
        expected_challenge: The challenge that was sent
        credential_public_key: The stored public key for this credential
        credential_current_sign_count: The stored sign count
        expected_origin: Expected origin URL
        expected_rp_id: Expected relying party ID

    Returns:
        New sign count if successful, None if verification fails
    """
    challenge_key = bytes_to_base64url(expected_challenge)

    # Check challenge exists and is valid
    pending = _pending_challenges.get(challenge_key)
    if not pending:
        return None
    if pending.is_expired():
        del _pending_challenges[challenge_key]
        return None
    if pending.is_registration:
        return None

    try:
        # Parse the credential JSON
        credential = parse_authentication_credential_json(credential_json)

        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
            credential_public_key=credential_public_key,
            credential_current_sign_count=credential_current_sign_count,
            require_user_verification=True,
        )

        # Clean up the challenge
        del _pending_challenges[challenge_key]

        return verification.new_sign_count

    except Exception as e:
        # Log but don't expose error details
        print(f"WebAuthn authentication verification failed: {type(e).__name__}")
        return None


def get_pending_challenge(challenge: bytes) -> Optional[WebAuthnChallenge]:
    """Get a pending challenge by its bytes value."""
    challenge_key = bytes_to_base64url(challenge)
    pending = _pending_challenges.get(challenge_key)
    if pending and not pending.is_expired():
        return pending
    return None


def clear_challenge(challenge: bytes) -> None:
    """Remove a challenge from pending storage."""
    challenge_key = bytes_to_base64url(challenge)
    if challenge_key in _pending_challenges:
        del _pending_challenges[challenge_key]
