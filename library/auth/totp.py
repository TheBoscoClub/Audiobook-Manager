"""
TOTP (Time-based One-Time Password) Authentication Module

Handles TOTP code generation and verification using pyotp (RFC 6238).
Secrets are stored as raw bytes and converted to base32 for authenticator apps.
"""

import base64
import secrets
from typing import Optional, Tuple
from io import BytesIO

import pyotp

# Default issuer name for authenticator apps
DEFAULT_ISSUER = "AudiobookLibrary"

# Number of 30-second windows to allow for clock drift (1 = ±30 seconds)
VALID_WINDOW = 1


def generate_secret() -> bytes:
    """
    Generate a new TOTP secret.

    Returns:
        20 bytes of random data (160 bits, as recommended by RFC 4226)
    """
    return secrets.token_bytes(20)


def secret_to_base32(secret: bytes) -> str:
    """
    Convert raw secret bytes to base32 string for authenticator apps.

    Args:
        secret: Raw secret bytes

    Returns:
        Base32-encoded string (without padding)
    """
    return base64.b32encode(secret).decode('ascii').rstrip('=')


def base32_to_secret(base32_secret: str) -> bytes:
    """
    Convert base32 string back to raw bytes.

    Args:
        base32_secret: Base32-encoded string

    Returns:
        Raw secret bytes
    """
    # Add padding if needed
    padding = 8 - (len(base32_secret) % 8)
    if padding != 8:
        base32_secret += '=' * padding
    return base64.b32decode(base32_secret)


def get_provisioning_uri(
    secret: bytes,
    username: str,
    issuer: str = DEFAULT_ISSUER
) -> str:
    """
    Generate otpauth:// URI for QR code scanning.

    Args:
        secret: Raw secret bytes
        username: User's display name
        issuer: App/service name

    Returns:
        otpauth:// URI string
    """
    totp = pyotp.TOTP(secret_to_base32(secret))
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def generate_qr_code(
    secret: bytes,
    username: str,
    issuer: str = DEFAULT_ISSUER
) -> bytes:
    """
    Generate QR code image as PNG bytes.

    Args:
        secret: Raw secret bytes
        username: User's display name
        issuer: App/service name

    Returns:
        PNG image data as bytes
    """
    try:
        import qrcode
        from qrcode.image.pil import PilImage
    except ImportError:
        raise ImportError("qrcode[pil] package required for QR code generation")

    uri = get_provisioning_uri(secret, username, issuer)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, format='PNG')
    return buffer.getvalue()


def get_current_code(secret: bytes) -> str:
    """
    Get the current TOTP code (for testing/debugging).

    Args:
        secret: Raw secret bytes

    Returns:
        6-digit TOTP code
    """
    totp = pyotp.TOTP(secret_to_base32(secret))
    return totp.now()


def verify_code(secret: bytes, code: str, valid_window: int = VALID_WINDOW) -> bool:
    """
    Verify a TOTP code against the secret.

    Args:
        secret: Raw secret bytes
        code: 6-digit code from user
        valid_window: Number of 30-second windows to allow (default: 1)

    Returns:
        True if code is valid, False otherwise
    """
    # Normalize code (remove spaces, ensure string)
    code = str(code).replace(' ', '').replace('-', '')

    # Must be 6 digits
    if not code.isdigit() or len(code) != 6:
        return False

    totp = pyotp.TOTP(secret_to_base32(secret))
    return totp.verify(code, valid_window=valid_window)


def setup_totp(username: str, issuer: str = DEFAULT_ISSUER) -> Tuple[bytes, str, str]:
    """
    Complete TOTP setup for a new user.

    Args:
        username: User's display name
        issuer: App/service name

    Returns:
        Tuple of (secret_bytes, base32_secret, provisioning_uri)
    """
    secret = generate_secret()
    base32 = secret_to_base32(secret)
    uri = get_provisioning_uri(secret, username, issuer)

    return secret, base32, uri


class TOTPAuthenticator:
    """
    Helper class for TOTP authentication operations.

    Usage:
        auth = TOTPAuthenticator(user.auth_credential)
        if auth.verify(code_from_user):
            # Login successful
    """

    def __init__(self, secret: bytes):
        """
        Initialize with user's secret.

        Args:
            secret: Raw secret bytes from user record
        """
        self.secret = secret
        self._totp = pyotp.TOTP(secret_to_base32(secret))

    def verify(self, code: str) -> bool:
        """
        Verify a TOTP code.

        Args:
            code: 6-digit code from user

        Returns:
            True if valid
        """
        return verify_code(self.secret, code)

    def current_code(self) -> str:
        """Get current code (for testing)."""
        return self._totp.now()

    def provisioning_uri(self, username: str, issuer: str = DEFAULT_ISSUER) -> str:
        """Get provisioning URI for this secret."""
        return self._totp.provisioning_uri(name=username, issuer_name=issuer)
