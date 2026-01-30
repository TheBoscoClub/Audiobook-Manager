#!/usr/bin/env python3
"""
Notification Management CLI

Commands:
    list                    List all notifications
    create <message>        Create a notification
    delete <id>             Delete a notification
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import AuthDatabase, Notification, NotificationType, NotificationRepository


def get_db():
    """Get auth database connection."""
    from auth.database import get_auth_db
    return get_auth_db()


def cmd_list(args):
    """List all notifications."""
    db = get_db()
    repo = NotificationRepository(db)
    notifications = repo.list_all()

    if not notifications:
        print("No notifications found.")
        return 0

    print(f"\n{'ID':<6} {'Type':<12} {'Target':<10} {'Dismissable':<12} {'Created':<20} {'Message'}")
    print("-" * 100)

    for n in notifications:
        target = f"User {n.target_user_id}" if n.target_user_id else "Global"
        dismissable = "Yes" if n.dismissable else "No"
        created = n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else "-"
        message = n.message[:50] + "..." if len(n.message) > 50 else n.message

        print(f"{n.id:<6} {n.type.value:<12} {target:<10} {dismissable:<12} {created:<20} {message}")

    print(f"\nTotal: {len(notifications)} notification(s)")
    return 0


def cmd_create(args):
    """Create a notification."""
    db = get_db()

    # Validate type
    notif_type = args.type.lower()
    if notif_type not in ("info", "maintenance", "outage", "personal"):
        print(f"Error: Invalid type '{args.type}'. Must be: info, maintenance, outage, personal")
        return 1

    # Personal notifications require a target user
    if notif_type == "personal" and not args.user:
        print("Error: Personal notifications require --user <user_id>")
        return 1

    # Parse expiry
    expires_at = None
    if args.expires:
        try:
            expires_at = datetime.fromisoformat(args.expires)
        except ValueError:
            print(f"Error: Invalid expiry format. Use ISO format: YYYY-MM-DDTHH:MM:SS")
            return 1

    notification = Notification(
        message=args.message,
        type=NotificationType(notif_type),
        target_user_id=args.user,
        expires_at=expires_at,
        dismissable=not args.no_dismiss,
        priority=args.priority or 0,
        created_by="cli",
    )
    notification.save(db)

    print(f"Notification created (ID: {notification.id})")
    return 0


def cmd_delete(args):
    """Delete a notification."""
    db = get_db()
    repo = NotificationRepository(db)

    notifications = repo.list_all()
    notif = next((n for n in notifications if n.id == args.id), None)

    if not notif:
        print(f"Error: Notification {args.id} not found")
        return 1

    notif.delete(db)
    print(f"Notification {args.id} deleted")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Notification Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s list
    %(prog)s create "Library updated with new books!" --type info
    %(prog)s create "Scheduled maintenance Saturday 2am" --type maintenance --expires 2026-01-25T04:00:00
    %(prog)s create "Hey Bob, added the series you wanted!" --type personal --user 5
    %(prog)s delete 3
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    list_parser = subparsers.add_parser("list", help="List all notifications")
    list_parser.set_defaults(func=cmd_list)

    # create command
    create_parser = subparsers.add_parser("create", help="Create a notification")
    create_parser.add_argument("message", help="Notification message")
    create_parser.add_argument("--type", "-t", default="info",
                              help="Type: info, maintenance, outage, personal (default: info)")
    create_parser.add_argument("--user", "-u", type=int,
                              help="Target user ID (for personal notifications)")
    create_parser.add_argument("--expires", "-e",
                              help="Expiry datetime (ISO format: YYYY-MM-DDTHH:MM:SS)")
    create_parser.add_argument("--no-dismiss", action="store_true",
                              help="Make notification non-dismissable")
    create_parser.add_argument("--priority", "-p", type=int, default=0,
                              help="Priority (higher = shown first)")
    create_parser.set_defaults(func=cmd_create)

    # delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a notification")
    delete_parser.add_argument("id", type=int, help="Notification ID to delete")
    delete_parser.set_defaults(func=cmd_delete)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
