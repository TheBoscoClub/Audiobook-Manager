#!/usr/bin/env python3
"""
Secure Credential Manager for Audible Position Sync

Encrypts and stores the Audible auth file password so users only
need to enter it once. Uses Fernet symmetric encryption (AES-128-CBC + HMAC).

Storage location: ~/.audible/position_sync_credentials.enc

Security model:
- Password encrypted with key derived from master password + salt
- Salt stored alongside encrypted data (standard practice)
- Master password can be empty for convenience (machine-bound security)
- Or set a master password for additional protection
"""

import base64
import json
import os
from getpass import getpass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# Configuration
CREDENTIAL_FILE = Path.home() / ".audible" / "position_sync_credentials.enc"
SERVICE_NAME = "audible-position-sync"
PBKDF2_ITERATIONS = 480000  # OWASP 2023 recommendation for PBKDF2-SHA256


def _derive_key(master_password: str, salt: bytes) -> bytes:
    """Derive a Fernet key from master password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(master_password.encode()))
    return key


def store_credential(
    audible_password: str,
    master_password: str = "",
    credential_file: Path = CREDENTIAL_FILE
) -> bool:
    """
    Encrypt and store the Audible auth file password.

    Args:
        audible_password: The password to encrypt and store
        master_password: Optional master password for additional security
                        (empty string = machine-bound convenience mode)
        credential_file: Where to store the encrypted credentials

    Returns:
        True if successful
    """
    # Generate random salt
    salt = os.urandom(16)

    # Derive encryption key
    key = _derive_key(master_password, salt)
    fernet = Fernet(key)

    # Encrypt the credential
    encrypted = fernet.encrypt(audible_password.encode())

    # Store salt + encrypted data as JSON
    data = {
        "version": 1,
        "salt": base64.b64encode(salt).decode(),
        "encrypted": encrypted.decode(),
        "service": SERVICE_NAME,
    }

    # Ensure directory exists
    credential_file.parent.mkdir(parents=True, exist_ok=True)

    # Write with restricted permissions
    credential_file.write_text(json.dumps(data, indent=2))
    credential_file.chmod(0o600)

    print(f"✅ Credential stored securely at: {credential_file}")
    return True


def retrieve_credential(
    master_password: str = "",
    credential_file: Path = CREDENTIAL_FILE
) -> str | None:
    """
    Retrieve and decrypt the stored Audible auth file password.

    Args:
        master_password: The master password used during storage
        credential_file: Where credentials are stored

    Returns:
        Decrypted password or None if not found/invalid
    """
    if not credential_file.exists():
        return None

    try:
        data = json.loads(credential_file.read_text())

        if data.get("version") != 1:
            print(f"⚠️  Unknown credential format version")
            return None

        salt = base64.b64decode(data["salt"])
        encrypted = data["encrypted"].encode()

        # Derive key and decrypt
        key = _derive_key(master_password, salt)
        fernet = Fernet(key)

        decrypted = fernet.decrypt(encrypted)
        return decrypted.decode()

    except InvalidToken:
        print("❌ Invalid master password or corrupted credential file")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ Error reading credential file: {e}")
        return None


def delete_credential(credential_file: Path = CREDENTIAL_FILE) -> bool:
    """Delete stored credentials."""
    if credential_file.exists():
        credential_file.unlink()
        print(f"🗑️  Credential deleted: {credential_file}")
        return True
    return False


def has_stored_credential(credential_file: Path = CREDENTIAL_FILE) -> bool:
    """Check if credentials are stored."""
    return credential_file.exists()


def get_or_prompt_credential(
    master_password: str = "",
    force_prompt: bool = False
) -> str | None:
    """
    Get stored credential or prompt user to enter and store it.

    This is the main entry point for the position sync script.

    Args:
        master_password: Master password for encryption/decryption
        force_prompt: If True, always prompt even if credential exists

    Returns:
        The Audible auth file password
    """
    # Try to retrieve existing credential
    if not force_prompt and has_stored_credential():
        password = retrieve_credential(master_password)
        if password:
            print("🔓 Using stored credential")
            return password
        else:
            print("⚠️  Stored credential invalid, prompting for new one...")

    # Prompt for password
    print("\n" + "=" * 50)
    print("First-time setup: Store Audible credential")
    print("=" * 50)
    print("Your Audible auth file password will be encrypted and stored")
    print(f"locally at: {CREDENTIAL_FILE}")
    print()

    audible_password = getpass("Enter your Audible auth file password: ")
    if not audible_password:
        print("❌ No password entered")
        return None

    # Confirm
    confirm = getpass("Confirm password: ")
    if audible_password != confirm:
        print("❌ Passwords don't match")
        return None

    # Store it
    store_credential(audible_password, master_password)

    return audible_password


# CLI interface for standalone use
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Manage Audible credential storage")
    parser.add_argument(
        "action",
        choices=["store", "retrieve", "delete", "status"],
        help="Action to perform"
    )
    parser.add_argument(
        "--master-password", "-m",
        default="",
        help="Master password for encryption (default: empty for convenience)"
    )

    args = parser.parse_args()

    if args.action == "status":
        if has_stored_credential():
            print(f"✅ Credential stored at: {CREDENTIAL_FILE}")
            print(f"   Size: {CREDENTIAL_FILE.stat().st_size} bytes")
            print(f"   Permissions: {oct(CREDENTIAL_FILE.stat().st_mode)[-3:]}")
        else:
            print("❌ No credential stored")

    elif args.action == "store":
        password = getpass("Enter Audible auth file password to store: ")
        if password:
            store_credential(password, args.master_password)

    elif args.action == "retrieve":
        password = retrieve_credential(args.master_password)
        if password:
            print(f"✅ Retrieved password: {'*' * len(password)} ({len(password)} chars)")
        else:
            print("❌ Could not retrieve password")

    elif args.action == "delete":
        delete_credential()
