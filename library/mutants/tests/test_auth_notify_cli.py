"""
Tests for auth.notify_cli (audiobook-notify CLI tool).

Covers:
- cmd_list() with notifications / empty
- cmd_create() valid/invalid types, expiry parsing, personal notifications
- cmd_delete() exists / not found
- main() argument parsing
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from auth import NotificationType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**overrides):
    defaults = {}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_notification(**overrides):
    defaults = {
        "id": 1,
        "message": "Library updated with new books!",
        "type": NotificationType.INFO,
        "target_user_id": None,
        "starts_at": None,
        "expires_at": None,
        "dismissable": True,
        "priority": 0,
        "created_at": datetime(2026, 3, 20, 10, 0, 0),
        "created_by": "admin",
    }
    defaults.update(overrides)
    notif = MagicMock()
    for k, v in defaults.items():
        setattr(notif, k, v)
    return notif


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_list_empty(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        repo = MagicMock()
        repo.list_all.return_value = []
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        assert "No notifications" in capsys.readouterr().out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_list_global_notification(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        notif = _make_notification()
        repo = MagicMock()
        repo.list_all.return_value = [notif]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "Global" in out
        assert "Library updated" in out
        assert "Total: 1" in out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_list_personal_notification(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        notif = _make_notification(type=NotificationType.PERSONAL, target_user_id=5)
        repo = MagicMock()
        repo.list_all.return_value = [notif]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "User 5" in out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_list_long_message_truncated(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        notif = _make_notification(message="X" * 60)
        repo = MagicMock()
        repo.list_all.return_value = [notif]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        assert "..." in capsys.readouterr().out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_list_no_created_at(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        notif = _make_notification(created_at=None)
        repo = MagicMock()
        repo.list_all.return_value = [notif]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        # Should show "-" for missing date
        assert "-" in capsys.readouterr().out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_list_multiple(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        n1 = _make_notification(id=1, type=NotificationType.INFO)
        n2 = _make_notification(id=2, type=NotificationType.OUTAGE, message="System outage")
        repo = MagicMock()
        repo.list_all.return_value = [n1, n2]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_list

        result = cmd_list(_make_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "Total: 2" in out


# ---------------------------------------------------------------------------
# cmd_create
# ---------------------------------------------------------------------------


class TestCmdCreate:
    @patch("auth.notify_cli.Notification")
    @patch("auth.notify_cli.get_db")
    def test_create_info(self, mock_get_db, mock_notif_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif_inst = MagicMock()
        notif_inst.id = 42
        mock_notif_cls.return_value = notif_inst

        from auth.notify_cli import cmd_create

        args = _make_args(
            message="New books added!",
            type="info",
            user=None,
            expires=None,
            no_dismiss=False,
            priority=0,
        )
        result = cmd_create(args)

        assert result == 0
        notif_inst.save.assert_called_once_with(db)
        assert "42" in capsys.readouterr().out

    @patch("auth.notify_cli.Notification")
    @patch("auth.notify_cli.get_db")
    def test_create_maintenance(self, mock_get_db, mock_notif_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif_inst = MagicMock()
        notif_inst.id = 10
        mock_notif_cls.return_value = notif_inst

        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Maintenance Saturday",
            type="maintenance",
            user=None,
            expires=None,
            no_dismiss=False,
            priority=0,
        )
        result = cmd_create(args)

        assert result == 0
        mock_notif_cls.assert_called_once()
        call_kwargs = mock_notif_cls.call_args
        assert call_kwargs[1]["type"] == NotificationType.MAINTENANCE

    def test_create_invalid_type(self, capsys):
        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Test", type="bogus", user=None, expires=None, no_dismiss=False, priority=0
        )
        # get_db is never called since validation fails first
        with patch("auth.notify_cli.get_db", return_value=MagicMock()):
            result = cmd_create(args)

        assert result == 1
        assert "Invalid type" in capsys.readouterr().out

    def test_create_personal_without_user(self, capsys):
        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Hey user!",
            type="personal",
            user=None,
            expires=None,
            no_dismiss=False,
            priority=0,
        )
        with patch("auth.notify_cli.get_db", return_value=MagicMock()):
            result = cmd_create(args)

        assert result == 1
        assert "--user" in capsys.readouterr().out

    @patch("auth.notify_cli.Notification")
    @patch("auth.notify_cli.get_db")
    def test_create_personal_with_user(self, mock_get_db, mock_notif_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif_inst = MagicMock()
        notif_inst.id = 20
        mock_notif_cls.return_value = notif_inst

        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Just for you!",
            type="personal",
            user=5,
            expires=None,
            no_dismiss=False,
            priority=0,
        )
        result = cmd_create(args)

        assert result == 0
        call_kwargs = mock_notif_cls.call_args[1]
        assert call_kwargs["target_user_id"] == 5
        assert call_kwargs["type"] == NotificationType.PERSONAL

    @patch("auth.notify_cli.Notification")
    @patch("auth.notify_cli.get_db")
    def test_create_with_valid_expiry(self, mock_get_db, mock_notif_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif_inst = MagicMock()
        notif_inst.id = 30
        mock_notif_cls.return_value = notif_inst

        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Expires soon",
            type="info",
            user=None,
            expires="2026-04-01T12:00:00",
            no_dismiss=False,
            priority=0,
        )
        result = cmd_create(args)

        assert result == 0
        call_kwargs = mock_notif_cls.call_args[1]
        assert call_kwargs["expires_at"] == datetime(2026, 4, 1, 12, 0, 0)

    def test_create_with_invalid_expiry(self, capsys):
        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Bad date",
            type="info",
            user=None,
            expires="not-a-date",
            no_dismiss=False,
            priority=0,
        )
        with patch("auth.notify_cli.get_db", return_value=MagicMock()):
            result = cmd_create(args)

        assert result == 1
        assert "Invalid expiry" in capsys.readouterr().out

    @patch("auth.notify_cli.Notification")
    @patch("auth.notify_cli.get_db")
    def test_create_non_dismissable(self, mock_get_db, mock_notif_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif_inst = MagicMock()
        notif_inst.id = 40
        mock_notif_cls.return_value = notif_inst

        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Cannot dismiss",
            type="outage",
            user=None,
            expires=None,
            no_dismiss=True,
            priority=5,
        )
        result = cmd_create(args)

        assert result == 0
        call_kwargs = mock_notif_cls.call_args[1]
        assert call_kwargs["dismissable"] is False
        assert call_kwargs["priority"] == 5

    @patch("auth.notify_cli.Notification")
    @patch("auth.notify_cli.get_db")
    def test_create_type_case_insensitive(self, mock_get_db, mock_notif_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif_inst = MagicMock()
        notif_inst.id = 50
        mock_notif_cls.return_value = notif_inst

        from auth.notify_cli import cmd_create

        args = _make_args(
            message="Uppercase type",
            type="INFO",
            user=None,
            expires=None,
            no_dismiss=False,
            priority=0,
        )
        result = cmd_create(args)

        assert result == 0


# ---------------------------------------------------------------------------
# cmd_delete
# ---------------------------------------------------------------------------


class TestCmdDelete:
    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_delete_success(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        notif = _make_notification(id=3)
        repo = MagicMock()
        repo.list_all.return_value = [notif]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_delete

        result = cmd_delete(_make_args(id=3))

        assert result == 0
        notif.delete.assert_called_once_with(db)
        assert "deleted" in capsys.readouterr().out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_delete_not_found(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        repo = MagicMock()
        repo.list_all.return_value = []
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_delete

        result = cmd_delete(_make_args(id=999))

        assert result == 1
        assert "not found" in capsys.readouterr().out

    @patch("auth.notify_cli.NotificationRepository")
    @patch("auth.notify_cli.get_db")
    def test_delete_wrong_id(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()
        notif = _make_notification(id=1)
        repo = MagicMock()
        repo.list_all.return_value = [notif]
        mock_repo_cls.return_value = repo

        from auth.notify_cli import cmd_delete

        result = cmd_delete(_make_args(id=2))

        assert result == 1
        assert "not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main() - argument parsing
# ---------------------------------------------------------------------------


class TestMain:
    @patch("auth.notify_cli.cmd_list")
    @patch("auth.notify_cli.get_db")
    def test_dispatch_list(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.notify_cli import main

        with patch("sys.argv", ["audiobook-notify", "list"]):
            result = main()
        assert result == 0

    @patch("auth.notify_cli.cmd_create")
    @patch("auth.notify_cli.get_db")
    def test_dispatch_create(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.notify_cli import main

        with patch("sys.argv", ["audiobook-notify", "create", "Hello!"]):
            result = main()
        assert result == 0

    @patch("auth.notify_cli.cmd_delete")
    @patch("auth.notify_cli.get_db")
    def test_dispatch_delete(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.notify_cli import main

        with patch("sys.argv", ["audiobook-notify", "delete", "3"]):
            result = main()
        assert result == 0

    def test_no_command_returns_1(self):
        from auth.notify_cli import main

        with patch("sys.argv", ["audiobook-notify"]):
            result = main()
        assert result == 1

    @patch("auth.notify_cli.cmd_create")
    @patch("auth.notify_cli.get_db")
    def test_create_with_all_options(self, mock_get_db, mock_cmd):
        mock_cmd.return_value = 0
        from auth.notify_cli import main

        with patch(
            "sys.argv",
            [
                "audiobook-notify",
                "create",
                "Outage!",
                "--type",
                "outage",
                "--user",
                "5",
                "--expires",
                "2026-04-01T00:00:00",
                "--no-dismiss",
                "--priority",
                "10",
            ],
        ):
            result = main()
        assert result == 0
        # Verify args were parsed correctly
        call_args = mock_cmd.call_args[0][0]
        assert call_args.message == "Outage!"
        assert call_args.type == "outage"
        assert call_args.user == 5
        assert call_args.no_dismiss is True
        assert call_args.priority == 10
