#!/usr/bin/env python3
"""
Inbox Management CLI

Commands:
    list                    List inbox messages
    read <id>               Read a message (marks as read)
    reply <id> <message>    Reply to a message
    archive <id>            Archive a message
"""

import argparse
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import (
    AuthDatabase,
    InboxMessage,
    InboxStatus,
    InboxRepository,
    Notification,
    NotificationType,
    UserRepository,
    ReplyMethod,
)


def get_db():
    """Get auth database connection."""
    from auth.database import get_auth_db
    return get_auth_db()


def cmd_list(args):
    """List inbox messages."""
    db = get_db()
    inbox_repo = InboxRepository(db)
    user_repo = UserRepository(db)

    messages = inbox_repo.list_all(include_archived=args.all)
    unread = inbox_repo.count_unread()

    if not messages:
        print("No messages in inbox.")
        return 0

    print(f"\n{'ID':<6} {'Status':<10} {'From':<15} {'Reply Via':<10} {'Date':<20} {'Preview'}")
    print("-" * 100)

    for m in messages:
        user = user_repo.get_by_id(m.from_user_id)
        username = user.username if user else "[deleted]"
        status = m.status.value.upper()
        reply_via = m.reply_via.value
        date = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "-"
        preview = m.message[:40] + "..." if len(m.message) > 40 else m.message

        # Color coding for status
        if m.status == InboxStatus.UNREAD:
            status = f"*{status}*"

        print(f"{m.id:<6} {status:<10} {username:<15} {reply_via:<10} {date:<20} {preview}")

    print(f"\nTotal: {len(messages)} message(s), {unread} unread")
    return 0


def cmd_read(args):
    """Read a single message."""
    db = get_db()
    inbox_repo = InboxRepository(db)
    user_repo = UserRepository(db)

    message = inbox_repo.get_by_id(args.id)
    if not message:
        print(f"Error: Message {args.id} not found")
        return 1

    # Mark as read
    if message.status == InboxStatus.UNREAD:
        message.mark_read(db)

    user = user_repo.get_by_id(message.from_user_id)
    username = user.username if user else "[deleted]"

    print("\n" + "=" * 60)
    print(f"From: {username}")
    print(f"Date: {message.created_at.strftime('%Y-%m-%d %H:%M:%S') if message.created_at else '-'}")
    print(f"Reply via: {message.reply_via.value}")
    if message.reply_email:
        print(f"Reply email: {message.reply_email}")
    print(f"Status: {message.status.value}")
    print("=" * 60)
    print(f"\n{message.message}\n")
    print("=" * 60)

    if message.status != InboxStatus.REPLIED:
        print(f"\nTo reply: audiobook-inbox reply {args.id} \"Your reply here\"")

    return 0


def cmd_reply(args):
    """Reply to a message."""
    db = get_db()
    inbox_repo = InboxRepository(db)
    user_repo = UserRepository(db)

    message = inbox_repo.get_by_id(args.id)
    if not message:
        print(f"Error: Message {args.id} not found")
        return 1

    user = user_repo.get_by_id(message.from_user_id)
    username = user.username if user else "User"

    if message.reply_via == ReplyMethod.EMAIL and message.reply_email:
        # Send email reply
        success = send_email_reply(message.reply_email, username, args.reply)
        if not success:
            print("Error: Failed to send email reply")
            return 1
        print(f"Email reply sent to {message.reply_email}")
    else:
        # Create in-app notification
        notification = Notification(
            message=f"Reply from admin: {args.reply}",
            type=NotificationType.PERSONAL,
            target_user_id=message.from_user_id,
            dismissable=True,
            created_by="cli",
        )
        notification.save(db)
        print(f"In-app reply sent to {username}")

    # Mark message as replied (clears email for privacy)
    message.mark_replied(db)

    return 0


def send_email_reply(to_email: str, username: str, reply_text: str) -> bool:
    """Send email reply to user."""
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    smtp_from = os.environ.get("SMTP_FROM", "library@thebosco.club")

    if not smtp_user:
        print("Warning: SMTP not configured. Set SMTP_USER and SMTP_PASS environment variables.")
        return False

    subject = "Reply from The Library"
    body = f"""Hi {username},

{reply_text}

---
This is a reply to your message to The Library.
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to_email

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, to_email, msg.as_string())

        return True
    except Exception as e:
        print(f"SMTP Error: {e}")
        return False


def cmd_archive(args):
    """Archive a message."""
    db = get_db()
    inbox_repo = InboxRepository(db)

    message = inbox_repo.get_by_id(args.id)
    if not message:
        print(f"Error: Message {args.id} not found")
        return 1

    message.status = InboxStatus.ARCHIVED
    message.reply_email = None  # Clear PII
    message.save(db)

    print(f"Message {args.id} archived")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Inbox Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s list
    %(prog)s list --all              # Include archived
    %(prog)s read 5
    %(prog)s reply 5 "Thanks for the feedback!"
    %(prog)s archive 5
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    list_parser = subparsers.add_parser("list", help="List inbox messages")
    list_parser.add_argument("--all", "-a", action="store_true",
                            help="Include archived messages")
    list_parser.set_defaults(func=cmd_list)

    # read command
    read_parser = subparsers.add_parser("read", help="Read a message")
    read_parser.add_argument("id", type=int, help="Message ID to read")
    read_parser.set_defaults(func=cmd_read)

    # reply command
    reply_parser = subparsers.add_parser("reply", help="Reply to a message")
    reply_parser.add_argument("id", type=int, help="Message ID to reply to")
    reply_parser.add_argument("reply", help="Reply message text")
    reply_parser.set_defaults(func=cmd_reply)

    # archive command
    archive_parser = subparsers.add_parser("archive", help="Archive a message")
    archive_parser.add_argument("id", type=int, help="Message ID to archive")
    archive_parser.set_defaults(func=cmd_archive)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
