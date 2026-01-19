#!/usr/bin/env python3
"""
audiobook-user CLI - User Management Tool

Admin tool for managing authenticated users of the audiobook library.
This tool requires direct access to the auth database and encryption key.

Usage:
    audiobook-user list                     # List all users
    audiobook-user add <username> --totp    # Add user with TOTP auth
    audiobook-user delete <username>        # Delete user
    audiobook-user grant <username>         # Grant download permission
    audiobook-user revoke <username>        # Revoke download permission
    audiobook-user kick <username>          # Invalidate user session
    audiobook-user info <username>          # Show user details
    audiobook-user totp-reset <username>    # Reset TOTP and show new QR
"""

import argparse
import sys
import os
import base64
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add library to path if running directly
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from library.auth import (
    AuthDatabase,
    AuthType,
    User,
    UserRepository,
    SessionRepository,
)


def get_db(args) -> AuthDatabase:
    """Get database instance from args."""
    return AuthDatabase(
        db_path=args.database,
        key_path=args.key_file,
        is_dev=args.dev
    )


def validate_username(username: str) -> tuple[bool, str]:
    """Validate username requirements."""
    if len(username) < 5:
        return False, "Username must be at least 5 characters"
    if len(username) > 16:
        return False, "Username must be at most 16 characters"
    if not all(32 <= ord(c) <= 126 for c in username):
        return False, "Username must contain only printable ASCII characters"
    return True, ""


def generate_totp_secret() -> bytes:
    """Generate a random TOTP secret (160 bits as recommended by RFC 4226)."""
    return secrets.token_bytes(20)


def secret_to_base32(secret: bytes) -> str:
    """Convert secret to base32 for authenticator apps."""
    return base64.b32encode(secret).decode('ascii').rstrip('=')


def generate_totp_uri(username: str, secret: bytes, issuer: str = "AudiobookLibrary") -> str:
    """Generate otpauth:// URI for QR codes."""
    secret_b32 = secret_to_base32(secret)
    return f"otpauth://totp/{issuer}:{username}?secret={secret_b32}&issuer={issuer}"


def cmd_list(args) -> int:
    """List all users."""
    db = get_db(args)

    try:
        db.initialize()
        repo = UserRepository(db)
        users = repo.list_all()

        if not users:
            print("No users found.")
            return 0

        print(f"{'ID':<5} {'Username':<18} {'Auth':<8} {'Download':<10} {'Admin':<7} {'Last Login'}")
        print("-" * 80)

        for user in users:
            last_login = user.last_login.strftime("%Y-%m-%d %H:%M") if user.last_login else "Never"
            print(
                f"{user.id:<5} {user.username:<18} {user.auth_type.value:<8} "
                f"{'Yes' if user.can_download else 'No':<10} "
                f"{'Yes' if user.is_admin else 'No':<7} {last_login}"
            )

        print(f"\nTotal: {len(users)} user(s)")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_add(args) -> int:
    """Add a new user."""
    db = get_db(args)

    # Validate username
    valid, error = validate_username(args.username)
    if not valid:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    try:
        db.initialize()
        repo = UserRepository(db)

        # Check if username exists
        if repo.username_exists(args.username):
            print(f"Error: Username '{args.username}' already exists", file=sys.stderr)
            return 1

        # Determine auth type
        if args.passkey:
            auth_type = AuthType.PASSKEY
            credential = b""  # Will be set during WebAuthn registration
            print("Note: Passkey credential will be set during browser registration.")
        elif args.fido2:
            auth_type = AuthType.FIDO2
            credential = b""  # Will be set during WebAuthn registration
            print("Note: FIDO2 credential will be set during browser registration.")
        else:
            auth_type = AuthType.TOTP
            credential = generate_totp_secret()

        # Create user
        user = User(
            username=args.username,
            auth_type=auth_type,
            auth_credential=credential,
            can_download=args.download,
            is_admin=args.admin
        )
        user.save(db)

        print(f"User '{args.username}' created (ID: {user.id})")
        print(f"  Auth type: {auth_type.value}")
        print(f"  Download: {'Yes' if args.download else 'No'}")
        print(f"  Admin: {'Yes' if args.admin else 'No'}")

        # Show TOTP setup info
        if auth_type == AuthType.TOTP:
            print()
            print("=== TOTP Setup ===")
            uri = generate_totp_uri(args.username, credential)
            print(f"Secret (base32): {secret_to_base32(credential)}")
            print(f"OTPAuth URI: {uri}")
            print()
            print("Scan this QR code or enter the secret manually in your authenticator app.")
            print("(Use 'qrencode' or an online QR generator with the URI above)")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_delete(args) -> int:
    """Delete a user."""
    db = get_db(args)

    try:
        db.initialize()
        repo = UserRepository(db)

        user = repo.get_by_username(args.username)
        if user is None:
            print(f"Error: User '{args.username}' not found", file=sys.stderr)
            return 1

        if user.is_admin and not args.force:
            print(f"Error: Cannot delete admin user without --force", file=sys.stderr)
            return 1

        if not args.yes:
            confirm = input(f"Delete user '{args.username}'? [y/N]: ")
            if confirm.lower() != 'y':
                print("Aborted.")
                return 0

        # Invalidate sessions first
        session_repo = SessionRepository(db)
        sessions_deleted = session_repo.invalidate_user_sessions(user.id)

        # Delete user (cascades to positions, notifications, etc.)
        user.delete(db)

        print(f"User '{args.username}' deleted.")
        if sessions_deleted:
            print(f"  Invalidated {sessions_deleted} session(s)")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_grant(args) -> int:
    """Grant download permission."""
    db = get_db(args)

    try:
        db.initialize()
        repo = UserRepository(db)

        user = repo.get_by_username(args.username)
        if user is None:
            print(f"Error: User '{args.username}' not found", file=sys.stderr)
            return 1

        if user.can_download:
            print(f"User '{args.username}' already has download permission.")
            return 0

        user.can_download = True
        user.save(db)

        print(f"Download permission granted to '{args.username}'.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_revoke(args) -> int:
    """Revoke download permission."""
    db = get_db(args)

    try:
        db.initialize()
        repo = UserRepository(db)

        user = repo.get_by_username(args.username)
        if user is None:
            print(f"Error: User '{args.username}' not found", file=sys.stderr)
            return 1

        if not user.can_download:
            print(f"User '{args.username}' already has no download permission.")
            return 0

        user.can_download = False
        user.save(db)

        print(f"Download permission revoked from '{args.username}'.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_kick(args) -> int:
    """Invalidate user session (force logout)."""
    db = get_db(args)

    try:
        db.initialize()
        user_repo = UserRepository(db)
        session_repo = SessionRepository(db)

        user = user_repo.get_by_username(args.username)
        if user is None:
            print(f"Error: User '{args.username}' not found", file=sys.stderr)
            return 1

        count = session_repo.invalidate_user_sessions(user.id)

        if count:
            print(f"User '{args.username}' kicked ({count} session(s) invalidated).")
        else:
            print(f"User '{args.username}' has no active sessions.")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_info(args) -> int:
    """Show user details."""
    db = get_db(args)

    try:
        db.initialize()
        user_repo = UserRepository(db)
        session_repo = SessionRepository(db)

        user = user_repo.get_by_username(args.username)
        if user is None:
            print(f"Error: User '{args.username}' not found", file=sys.stderr)
            return 1

        session = session_repo.get_by_user_id(user.id)

        print(f"User: {user.username}")
        print(f"  ID: {user.id}")
        print(f"  Auth type: {user.auth_type.value}")
        print(f"  Can download: {'Yes' if user.can_download else 'No'}")
        print(f"  Is admin: {'Yes' if user.is_admin else 'No'}")
        print(f"  Created: {user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else 'Unknown'}")
        print(f"  Last login: {user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else 'Never'}")

        print()
        if session:
            print("Active session:")
            print(f"  Created: {session.created_at.strftime('%Y-%m-%d %H:%M:%S') if session.created_at else 'Unknown'}")
            print(f"  Last seen: {session.last_seen.strftime('%Y-%m-%d %H:%M:%S') if session.last_seen else 'Unknown'}")
            print(f"  User agent: {session.user_agent or 'Unknown'}")
            print(f"  IP address: {session.ip_address or 'Unknown'}")
        else:
            print("No active session.")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_totp_reset(args) -> int:
    """Reset TOTP secret and show new QR code."""
    db = get_db(args)

    try:
        db.initialize()
        repo = UserRepository(db)

        user = repo.get_by_username(args.username)
        if user is None:
            print(f"Error: User '{args.username}' not found", file=sys.stderr)
            return 1

        if user.auth_type != AuthType.TOTP:
            print(f"Error: User '{args.username}' does not use TOTP authentication", file=sys.stderr)
            return 1

        if not args.yes:
            confirm = input(f"Reset TOTP for '{args.username}'? This will invalidate the current authenticator. [y/N]: ")
            if confirm.lower() != 'y':
                print("Aborted.")
                return 0

        # Generate new secret
        new_secret = generate_totp_secret()
        user.auth_credential = new_secret
        user.save(db)

        # Invalidate sessions
        session_repo = SessionRepository(db)
        session_repo.invalidate_user_sessions(user.id)

        print(f"TOTP reset for '{args.username}'.")
        print()
        print("=== New TOTP Setup ===")
        uri = generate_totp_uri(args.username, new_secret)
        print(f"Secret (base32): {secret_to_base32(new_secret)}")
        print(f"OTPAuth URI: {uri}")
        print()
        print("The user's previous authenticator will no longer work.")
        print("Share this new secret with the user to set up their authenticator again.")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_init(args) -> int:
    """Initialize the auth database."""
    db = get_db(args)

    try:
        created = db.initialize()
        status = db.verify()

        if created:
            print(f"Auth database created: {args.database}")
        else:
            print(f"Auth database already exists: {args.database}")

        print(f"  Schema version: {status['schema_version']}")
        print(f"  Tables: {status['table_count']}")
        print(f"  Users: {status['user_count']}")
        print(f"  Key file: {args.key_file}")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="audiobook-user",
        description="Manage authenticated users for the audiobook library"
    )

    # Global options
    parser.add_argument(
        "--database", "-d",
        default=os.environ.get("AUTH_DATABASE", "/var/lib/audiobooks/auth.db"),
        help="Path to auth database (default: $AUTH_DATABASE or /var/lib/audiobooks/auth.db)"
    )
    parser.add_argument(
        "--key-file", "-k",
        default=os.environ.get("AUTH_KEY_FILE", "/etc/audiobooks/auth.key"),
        help="Path to encryption key (default: $AUTH_KEY_FILE or /etc/audiobooks/auth.key)"
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Development mode (relaxed key permissions)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize auth database")

    # list command
    list_parser = subparsers.add_parser("list", help="List all users")

    # add command
    add_parser = subparsers.add_parser("add", help="Add a new user")
    add_parser.add_argument("username", help="Username (5-16 printable chars)")
    auth_group = add_parser.add_mutually_exclusive_group()
    auth_group.add_argument("--totp", action="store_true", default=True, help="Use TOTP auth (default)")
    auth_group.add_argument("--passkey", action="store_true", help="Use Passkey auth")
    auth_group.add_argument("--fido2", action="store_true", help="Use FIDO2 hardware key auth")
    add_parser.add_argument("--download", action="store_true", help="Grant download permission")
    add_parser.add_argument("--admin", action="store_true", help="Make user an admin")

    # delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a user")
    delete_parser.add_argument("username", help="Username to delete")
    delete_parser.add_argument("--force", action="store_true", help="Force delete (even for admin users)")
    delete_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    # grant command
    grant_parser = subparsers.add_parser("grant", help="Grant download permission")
    grant_parser.add_argument("username", help="Username")

    # revoke command
    revoke_parser = subparsers.add_parser("revoke", help="Revoke download permission")
    revoke_parser.add_argument("username", help="Username")

    # kick command
    kick_parser = subparsers.add_parser("kick", help="Force logout user")
    kick_parser.add_argument("username", help="Username to kick")

    # info command
    info_parser = subparsers.add_parser("info", help="Show user details")
    info_parser.add_argument("username", help="Username")

    # totp-reset command
    totp_parser = subparsers.add_parser("totp-reset", help="Reset TOTP secret")
    totp_parser.add_argument("username", help="Username")
    totp_parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "init": cmd_init,
        "list": cmd_list,
        "add": cmd_add,
        "delete": cmd_delete,
        "grant": cmd_grant,
        "revoke": cmd_revoke,
        "kick": cmd_kick,
        "info": cmd_info,
        "totp-reset": cmd_totp_reset,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
