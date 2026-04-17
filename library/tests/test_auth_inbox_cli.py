"""
Tests for auth.inbox_cli (audiobook-inbox CLI tool).

Covers:
- cmd_list() with messages / empty inbox
- cmd_read() mark-as-read / not-found
- cmd_reply() email path / in-app path / error paths
- send_email_reply() SMTP mocking
- cmd_archive() archive + PII cleanup
- main() argument parsing
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from auth import InboxStatus, ReplyMethod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**overrides):
    defaults = {"all": False}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_inbox_message(**overrides):
    defaults = {
        "id": 1,
        "from_user_id": 10,
        "message": "Hello admin, I need help with my account.",
        "reply_via": ReplyMethod.IN_APP,
        "reply_email": None,
        "status": InboxStatus.UNREAD,
        "created_at": datetime(2026, 3, 20, 14, 30, 0),
        "read_at": None,
        "replied_at": None,
    }
    defaults.update(overrides)
    msg = MagicMock()
    for k, v in defaults.items():
        setattr(msg, k, v)
    return msg


def _make_user(**overrides):
    defaults = {"id": 10, "username": "alice"}
    defaults.update(overrides)
    user = MagicMock()
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_list_empty(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()
        inbox_repo = MagicMock()
        inbox_repo.list_all.return_value = []
        inbox_repo.count_unread.return_value = 0
        mock_inbox_cls.return_value = inbox_repo

        result = __import__("auth.inbox_cli", fromlist=["cmd_list"]).cmd_list(_make_args())
        assert result == 0
        assert "No messages" in capsys.readouterr().out

    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_list_with_messages(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message()
        inbox_repo = MagicMock()
        inbox_repo.list_all.return_value = [msg]
        inbox_repo.count_unread.return_value = 1
        mock_inbox_cls.return_value = inbox_repo

        user = _make_user()
        user_repo = MagicMock()
        user_repo.get_by_id.return_value = user
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "alice" in out
        assert "1 unread" in out
        assert "Total: 1" in out

    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_list_deleted_user(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message()
        inbox_repo = MagicMock()
        inbox_repo.list_all.return_value = [msg]
        inbox_repo.count_unread.return_value = 1
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = None
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        assert "[deleted]" in capsys.readouterr().out

    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_list_long_message_truncated(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(message="A" * 60)
        inbox_repo = MagicMock()
        inbox_repo.list_all.return_value = [msg]
        inbox_repo.count_unread.return_value = 0
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "..." in out


# ---------------------------------------------------------------------------
# cmd_read
# ---------------------------------------------------------------------------


class TestCmdRead:
    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_read_message(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message()
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_read

        result = cmd_read(_make_args(id=1))

        assert result == 0
        msg.mark_read.assert_called_once()
        out = capsys.readouterr().out
        assert "alice" in out
        assert "Hello admin" in out

    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_read_already_read(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(status=InboxStatus.READ)
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_read

        result = cmd_read(_make_args(id=1))

        assert result == 0
        msg.mark_read.assert_not_called()

    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_read_not_found(self, mock_get_db, mock_inbox_cls, capsys):
        mock_get_db.return_value = MagicMock()
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = None
        mock_inbox_cls.return_value = inbox_repo

        from auth.inbox_cli import cmd_read

        result = cmd_read(_make_args(id=999))

        assert result == 1
        assert "not found" in capsys.readouterr().out

    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_read_replied_no_reply_hint(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(status=InboxStatus.REPLIED)
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_read

        result = cmd_read(_make_args(id=1))

        assert result == 0
        out = capsys.readouterr().out
        assert "To reply:" not in out

    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_read_shows_email(self, mock_get_db, mock_inbox_cls, mock_user_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(reply_via=ReplyMethod.EMAIL, reply_email="alice@example.com")
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        from auth.inbox_cli import cmd_read

        result = cmd_read(_make_args(id=1))

        assert result == 0
        out = capsys.readouterr().out
        assert "alice@example.com" in out


# ---------------------------------------------------------------------------
# cmd_reply
# ---------------------------------------------------------------------------


class TestCmdReply:
    @patch("auth.inbox_cli.Notification")
    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_reply_in_app(self, mock_get_db, mock_inbox_cls, mock_user_cls, mock_notif_cls, capsys):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(reply_via=ReplyMethod.IN_APP)
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        notif_inst = MagicMock()
        mock_notif_cls.return_value = notif_inst

        from auth.inbox_cli import cmd_reply

        result = cmd_reply(_make_args(id=1, reply="Thanks!"))

        assert result == 0
        notif_inst.save.assert_called_once()
        msg.mark_replied.assert_called_once()
        assert "In-app reply" in capsys.readouterr().out

    @patch("auth.inbox_cli.send_email_reply")
    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_reply_email_success(
        self, mock_get_db, mock_inbox_cls, mock_user_cls, mock_send, capsys
    ):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(reply_via=ReplyMethod.EMAIL, reply_email="alice@example.com")
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        mock_send.return_value = True

        from auth.inbox_cli import cmd_reply

        result = cmd_reply(_make_args(id=1, reply="Got it!"))

        assert result == 0
        mock_send.assert_called_once_with("alice@example.com", "alice", "Got it!")
        msg.mark_replied.assert_called_once()
        assert "Email reply sent" in capsys.readouterr().out

    @patch("auth.inbox_cli.send_email_reply")
    @patch("auth.inbox_cli.UserRepository")
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_reply_email_failure(
        self, mock_get_db, mock_inbox_cls, mock_user_cls, mock_send, capsys
    ):
        mock_get_db.return_value = MagicMock()

        msg = _make_inbox_message(reply_via=ReplyMethod.EMAIL, reply_email="alice@example.com")
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        user_repo = MagicMock()
        user_repo.get_by_id.return_value = _make_user()
        mock_user_cls.return_value = user_repo

        mock_send.return_value = False

        from auth.inbox_cli import cmd_reply

        result = cmd_reply(_make_args(id=1, reply="Got it!"))

        assert result == 1
        msg.mark_replied.assert_not_called()

    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_reply_message_not_found(self, mock_get_db, mock_inbox_cls, capsys):
        mock_get_db.return_value = MagicMock()
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = None
        mock_inbox_cls.return_value = inbox_repo

        from auth.inbox_cli import cmd_reply

        result = cmd_reply(_make_args(id=999, reply="Hello"))

        assert result == 1
        assert "not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# send_email_reply
# ---------------------------------------------------------------------------


class TestSendEmailReply:
    @patch("auth.inbox_cli.smtplib.SMTP")
    def test_send_email_success(self, mock_smtp_cls):
        smtp_inst = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=smtp_inst)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        from auth.inbox_cli import send_email_reply

        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.test.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "user@test.com",
                "SMTP_PASS": "secret",
                "SMTP_FROM": "lib@test.com",
            },
        ):
            result = send_email_reply("alice@example.com", "alice", "Hello!")

        assert result is True

    def test_send_email_no_smtp_user(self, capsys):
        from auth.inbox_cli import send_email_reply

        with patch.dict("os.environ", {"SMTP_USER": ""}, clear=False):
            result = send_email_reply("alice@example.com", "alice", "Hello!")

        assert result is False
        assert "SMTP not configured" in capsys.readouterr().out

    @patch("auth.inbox_cli.smtplib.SMTP")
    def test_send_email_smtp_error(self, mock_smtp_cls, capsys):
        mock_smtp_cls.return_value.__enter__ = MagicMock(
            side_effect=ConnectionRefusedError("refused")
        )
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        from auth.inbox_cli import send_email_reply

        with patch.dict("os.environ", {"SMTP_USER": "user@test.com", "SMTP_PASS": "secret"}):
            result = send_email_reply("alice@example.com", "alice", "Hello!")

        assert result is False


# ---------------------------------------------------------------------------
# cmd_archive
# ---------------------------------------------------------------------------


class TestCmdArchive:
    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_archive_success(self, mock_get_db, mock_inbox_cls, capsys):
        mock_get_db.return_value = MagicMock()
        msg = _make_inbox_message()
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = msg
        mock_inbox_cls.return_value = inbox_repo

        from auth.inbox_cli import cmd_archive

        result = cmd_archive(_make_args(id=1))

        assert result == 0
        assert msg.status == InboxStatus.ARCHIVED
        assert msg.reply_email is None  # PII cleared
        msg.save.assert_called_once()
        assert "archived" in capsys.readouterr().out

    @patch("auth.inbox_cli.InboxRepository")
    @patch("auth.inbox_cli.get_db")
    def test_archive_not_found(self, mock_get_db, mock_inbox_cls, capsys):
        mock_get_db.return_value = MagicMock()
        inbox_repo = MagicMock()
        inbox_repo.get_by_id.return_value = None
        mock_inbox_cls.return_value = inbox_repo

        from auth.inbox_cli import cmd_archive

        result = cmd_archive(_make_args(id=999))

        assert result == 1
        assert "not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main() - argument parsing
# ---------------------------------------------------------------------------


class TestMain:
    @patch("auth.inbox_cli.cmd_list")
    @patch("auth.inbox_cli.get_db")
    def test_dispatch_list(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.inbox_cli import main

        with patch("sys.argv", ["audiobook-inbox", "list"]):
            result = main()
        assert result == 0

    @patch("auth.inbox_cli.cmd_read")
    @patch("auth.inbox_cli.get_db")
    def test_dispatch_read(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.inbox_cli import main

        with patch("sys.argv", ["audiobook-inbox", "read", "5"]):
            result = main()
        assert result == 0

    @patch("auth.inbox_cli.cmd_reply")
    @patch("auth.inbox_cli.get_db")
    def test_dispatch_reply(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.inbox_cli import main

        with patch("sys.argv", ["audiobook-inbox", "reply", "5", "Thanks!"]):
            result = main()
        assert result == 0

    @patch("auth.inbox_cli.cmd_archive")
    @patch("auth.inbox_cli.get_db")
    def test_dispatch_archive(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.inbox_cli import main

        with patch("sys.argv", ["audiobook-inbox", "archive", "5"]):
            result = main()
        assert result == 0

    def test_no_command_returns_1(self):
        from auth.inbox_cli import main

        with patch("sys.argv", ["audiobook-inbox"]):
            result = main()
        assert result == 1
