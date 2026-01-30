"""
Software WebAuthn authenticator for integration testing.

Implements a minimal software-based FIDO2/WebAuthn authenticator that can
perform registration (make_credential) and authentication (get_assertion)
ceremonies without requiring hardware. Uses ECDSA P-256 key pairs and
constructs valid WebAuthn responses that pass py_webauthn server verification.

This authenticator uses "none" attestation format (no attestation statement),
which matches the server's AttestationConveyancePreference.NONE setting.
"""

import hashlib
import json
import os
import struct
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256

import cbor2


def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding."""
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """Base64url decode with padding restoration."""
    s += "=" * (4 - len(s) % 4)
    return urlsafe_b64decode(s)


def _encode_cose_public_key(public_key: ec.EllipticCurvePublicKey) -> bytes:
    """Encode an EC public key in COSE_Key format (CBOR map).

    COSE Key for EC2 P-256:
        1 (kty): 2 (EC2)
        3 (alg): -7 (ES256)
       -1 (crv): 1 (P-256)
       -2 (x): 32-byte x-coordinate
       -3 (y): 32-byte y-coordinate
    """
    numbers = public_key.public_numbers()
    x = numbers.x.to_bytes(32, "big")
    y = numbers.y.to_bytes(32, "big")
    cose_key = {
        1: 2,     # kty: EC2
        3: -7,    # alg: ES256
        -1: 1,    # crv: P-256
        -2: x,    # x-coordinate
        -3: y,    # y-coordinate
    }
    return cbor2.dumps(cose_key)


@dataclass
class StoredCredential:
    """A credential stored by the software authenticator."""
    credential_id: bytes
    private_key: ec.EllipticCurvePrivateKey
    rp_id: str
    sign_count: int = 0


@dataclass
class SoftwareAuthenticator:
    """Software-based WebAuthn authenticator for testing.

    Maintains a registry of credentials keyed by credential_id.
    Supports both registration and authentication ceremonies.
    """
    credentials: dict[bytes, StoredCredential] = field(default_factory=dict)
    # AAGUID for our software authenticator (all zeros = no attestation)
    aaguid: bytes = field(default=b"\x00" * 16)

    def make_credential(
        self,
        options: dict,
        origin: str,
    ) -> dict:
        """Perform a WebAuthn registration ceremony (navigator.credentials.create).

        Args:
            options: The PublicKeyCredentialCreationOptions from the server's
                     /begin endpoint (parsed JSON).
            origin: The origin to use in clientDataJSON (must match server config).

        Returns:
            A dict matching the PublicKeyCredential interface for the /complete endpoint.
        """
        # Extract challenge
        challenge_b64 = options["challenge"]

        # Extract RP ID
        rp_id = options["rp"]["id"]

        # Generate credential
        private_key = ec.generate_private_key(ec.SECP256R1())
        credential_id = os.urandom(32)

        # Store credential
        self.credentials[credential_id] = StoredCredential(
            credential_id=credential_id,
            private_key=private_key,
            rp_id=rp_id,
            sign_count=0,
        )

        # Build clientDataJSON
        client_data = json.dumps({
            "type": "webauthn.create",
            "challenge": challenge_b64,
            "origin": origin,
            "crossOrigin": False,
        }, separators=(",", ":")).encode("utf-8")

        # Build authenticator data
        auth_data = self._build_auth_data_registration(
            rp_id, credential_id, private_key.public_key()
        )

        # Build attestation object (none format)
        attestation_object = cbor2.dumps({
            "fmt": "none",
            "attStmt": {},
            "authData": auth_data,
        })

        return {
            "id": _b64url_encode(credential_id),
            "rawId": _b64url_encode(credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64url_encode(client_data),
                "attestationObject": _b64url_encode(attestation_object),
            },
            "authenticatorAttachment": "platform",
        }

    def get_assertion(
        self,
        options: dict,
        origin: str,
        credential_id: bytes | None = None,
    ) -> dict:
        """Perform a WebAuthn authentication ceremony (navigator.credentials.get).

        Args:
            options: The PublicKeyCredentialRequestOptions from the server's
                     /begin endpoint (parsed JSON).
            origin: The origin to use in clientDataJSON (must match server config).
            credential_id: Specific credential to use. If None, uses the first
                          matching credential from allowCredentials.

        Returns:
            A dict matching the PublicKeyCredential interface for the /complete endpoint.
        """
        challenge_b64 = options["challenge"]

        # Find the credential to use
        if credential_id is None:
            allow_creds = options.get("allowCredentials", [])
            for ac in allow_creds:
                cid = _b64url_decode(ac["id"])
                if cid in self.credentials:
                    credential_id = cid
                    break

        if credential_id is None or credential_id not in self.credentials:
            raise ValueError("No matching credential found for assertion")

        stored = self.credentials[credential_id]
        stored.sign_count += 1

        # Build clientDataJSON
        client_data = json.dumps({
            "type": "webauthn.get",
            "challenge": challenge_b64,
            "origin": origin,
            "crossOrigin": False,
        }, separators=(",", ":")).encode("utf-8")

        # Build authenticator data (no attested credential data for assertion)
        rp_id_hash = hashlib.sha256(stored.rp_id.encode("utf-8")).digest()
        flags = 0x05  # UP (0x01) + UV (0x04)
        auth_data = rp_id_hash + struct.pack(">BI", flags, stored.sign_count)

        # Sign: authData || SHA-256(clientDataJSON)
        client_data_hash = hashlib.sha256(client_data).digest()
        signature = stored.private_key.sign(
            auth_data + client_data_hash,
            ec.ECDSA(SHA256()),
        )

        return {
            "id": _b64url_encode(credential_id),
            "rawId": _b64url_encode(credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64url_encode(client_data),
                "authenticatorData": _b64url_encode(auth_data),
                "signature": _b64url_encode(signature),
            },
            "authenticatorAttachment": "platform",
        }

    def _build_auth_data_registration(
        self,
        rp_id: str,
        credential_id: bytes,
        public_key: ec.EllipticCurvePublicKey,
    ) -> bytes:
        """Build authenticator data for registration with attested credential data.

        Layout: rpIdHash (32) | flags (1) | signCount (4) | attestedCredData (variable)
        attestedCredData: aaguid (16) | credIdLen (2) | credId (N) | coseKey (variable)
        """
        rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
        # Flags: UP (0x01) | UV (0x04) | AT (0x40) = 0x45
        flags = 0x45
        sign_count = 0

        # Attested credential data
        cred_id_len = struct.pack(">H", len(credential_id))
        cose_key = _encode_cose_public_key(public_key)

        return (
            rp_id_hash
            + struct.pack(">BI", flags, sign_count)
            + self.aaguid
            + cred_id_len
            + credential_id
            + cose_key
        )
