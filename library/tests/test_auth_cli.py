"""
Tests for auth.cli (audiobook-user CLI tool).

Covers:
- validate_username() edge cases
- generate_totp_secret() / secret_to_base32() / generate_totp_uri()
- All cmd_* functions with mocked DB/repos
- main() argument parsing dispatch
"""

import base64
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from auth.cli import (
    validate_username,
    generate_totp_secret,
    secret_to_base32,
    generate_totp_uri,
    get_db,
    cmd_init,
    cmd_list,
    cmd_add,
    cmd_delete,
    cmd_grant,
    cmd_revoke,
    cmd_kick,
    cmd_info,
    cmd_totp_reset,
    main,
)

# Import AuthType from the same module that cli.py resolved it to,
# so enum identity comparisons work correctly in the code under test.
import auth.cli as _cli_mod

AuthType = _cli_mod.AuthType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**overrides):
    """Build a minimal args namespace for cmd_* functions."""
    defaults = {
        "database": ":memory:",
        "key_file": "/dev/null",
        "dev": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_user(**overrides):
    """Build a mock User object with spec-free attributes.

    Uses SimpleNamespace so enum comparisons (e.g. auth_type != AuthType.TOTP)
    work correctly -- MagicMock attribute comparison can silently break with enums.
    """
    defaults = {
        "id": 1,
        "username": "testuser",
        "auth_type": AuthType.TOTP,
        "auth_credential": b"\x00" * 20,
        "can_download": True,
        "is_admin": False,
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "last_login": datetime(2026, 3, 20, 8, 0, 0),
        "save": MagicMock(),
        "delete": MagicMock(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_session(**overrides):
    """Build a mock Session object."""
    defaults = {
        "id": 1,
        "user_id": 1,
        "created_at": datetime(2026, 3, 20, 8, 0, 0),
        "last_seen": datetime(2026, 3, 20, 9, 0, 0),
        "user_agent": "Mozilla/5.0",
        "ip_address": "192.168.1.100",
    }
    defaults.update(overrides)
    session = MagicMock()
    for k, v in defaults.items():
        setattr(session, k, v)
    return session


# ---------------------------------------------------------------------------
# validate_username
# ---------------------------------------------------------------------------


class TestValidateUsername:
    def test_valid_username(self):
        ok, msg = validate_username("alice")
        assert ok is True
        assert msg == ""

    def test_too_short(self):
        ok, msg = validate_username("ab")
        assert ok is False
        assert "at least 3" in msg

    def test_exact_min_length(self):
        ok, _ = validate_username("abc")
        assert ok is True

    def test_too_long(self):
        ok, msg = validate_username("a" * 25)
        assert ok is False
        assert "at most 24" in msg

    def test_exact_max_length(self):
        ok, _ = validate_username("a" * 24)
        assert ok is True

    def test_non_printable_ascii(self):
        ok, msg = validate_username("user\x00name")
        assert ok is False
        assert "printable ASCII" in msg

    def test_non_ascii_unicode(self):
        ok, msg = validate_username("us\u00e9r")
        assert ok is False
        assert "printable ASCII" in msg

    def test_spaces_allowed(self):
        # Space is printable ASCII (ord 32)
        ok, _ = validate_username("a b")
        assert ok is True


# ---------------------------------------------------------------------------
# TOTP utility functions
# ---------------------------------------------------------------------------


class TestTotpUtilities:
    def test_generate_totp_secret_length(self):
        secret = generate_totp_secret()
        assert isinstance(secret, bytes)
        assert len(secret) == 20

    def test_generate_totp_secret_random(self):
        s1 = generate_totp_secret()
        s2 = generate_totp_secret()
        assert s1 != s2

    def test_secret_to_base32(self):
        secret = b"\x00" * 20
        result = secret_to_base32(secret)
        # Should be valid base32 (no padding)
        assert "=" not in result
        assert isinstance(result, str)
        # Round-trip: decode back
        padded = result + "=" * ((8 - len(result) % 8) % 8)
        decoded = base64.b32decode(padded)
        assert decoded == secret

    def test_generate_totp_uri_format(self):
        secret = b"\x00" * 20
        uri = generate_totp_uri("alice", secret)
        assert uri.startswith("otpauth://totp/")
        assert "alice" in uri
        assert "AudiobookLibrary" in uri
        assert "secret=" in uri

    def test_generate_totp_uri_custom_issuer(self):
        secret = b"\x00" * 20
        uri = generate_totp_uri("bob", secret, issuer="MyApp")
        assert "MyApp" in uri
        assert "bob" in uri


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


class TestGetDb:
    @patch("auth.cli.AuthDatabase")
    def test_passes_args(self, mock_db_cls):
        args = _make_args()
        get_db(args)
        mock_db_cls.assert_called_once_with(
            db_path=":memory:", key_path="/dev/null", is_dev=True
        )


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


class TestCmdInit:
    @patch("auth.cli.get_db")
    def test_init_new_db(self, mock_get_db, capsys):
        db = MagicMock()
        db.initialize.return_value = True
        db.verify.return_value = {
            "schema_version": 1,
            "table_count": 8,
            "user_count": 0,
        }
        mock_get_db.return_value = db

        args = _make_args(database="/tmp/test.db", key_file="/tmp/key")
        result = cmd_init(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "created" in out
        assert "Schema version: 1" in out

    @patch("auth.cli.get_db")
    def test_init_existing_db(self, mock_get_db, capsys):
        db = MagicMock()
        db.initialize.return_value = False
        db.verify.return_value = {
            "schema_version": 1,
            "table_count": 8,
            "user_count": 3,
        }
        mock_get_db.return_value = db

        args = _make_args(database="/tmp/test.db", key_file="/tmp/key")
        result = cmd_init(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "already exists" in out

    @patch("auth.cli.get_db")
    def test_init_error(self, mock_get_db, capsys):
        db = MagicMock()
        db.initialize.side_effect = RuntimeError("disk full")
        mock_get_db.return_value = db

        args = _make_args(database="/tmp/test.db", key_file="/tmp/key")
        result = cmd_init(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "disk full" in err


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


class TestCmdList:
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_list_empty(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.list_all.return_value = []
        mock_repo_cls.return_value = repo

        result = cmd_list(_make_args())
        assert result == 0
        assert "No users found" in capsys.readouterr().out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_list_with_users(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user()
        repo = MagicMock()
        repo.list_all.return_value = [user]
        mock_repo_cls.return_value = repo

        result = cmd_list(_make_args())
        assert result == 0
        out = capsys.readouterr().out
        assert "testuser" in out
        assert "Total: 1" in out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_list_user_never_logged_in(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(last_login=None)
        repo = MagicMock()
        repo.list_all.return_value = [user]
        mock_repo_cls.return_value = repo

        result = cmd_list(_make_args())
        assert result == 0
        assert "Never" in capsys.readouterr().out

    @patch("auth.cli.get_db")
    def test_list_error(self, mock_get_db, capsys):
        db = MagicMock()
        db.initialize.side_effect = RuntimeError("db locked")
        mock_get_db.return_value = db

        result = cmd_list(_make_args())
        assert result == 1
        assert "db locked" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_add
# ---------------------------------------------------------------------------


class TestCmdAdd:
    @patch("auth.cli.User")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_add_totp_user(self, mock_get_db, mock_repo_cls, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.username_exists.return_value = False
        mock_repo_cls.return_value = repo

        user_inst = MagicMock()
        user_inst.id = 5
        mock_user_cls.return_value = user_inst

        args = _make_args(
            username="newuser",
            passkey=False,
            fido2=False,
            download=True,
            admin=False,
        )
        result = cmd_add(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "newuser" in out
        assert "TOTP Setup" in out
        assert "Secret (base32)" in out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_add_invalid_username(self, mock_get_db, mock_repo_cls, capsys):
        mock_get_db.return_value = MagicMock()

        args = _make_args(
            username="ab", passkey=False, fido2=False, download=True, admin=False
        )
        result = cmd_add(args)

        assert result == 1
        assert "at least 3" in capsys.readouterr().err

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_add_duplicate_username(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.username_exists.return_value = True
        mock_repo_cls.return_value = repo

        args = _make_args(
            username="existing", passkey=False, fido2=False, download=True, admin=False
        )
        result = cmd_add(args)

        assert result == 1
        assert "already exists" in capsys.readouterr().err

    @patch("auth.cli.User")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_add_passkey_user(self, mock_get_db, mock_repo_cls, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.username_exists.return_value = False
        mock_repo_cls.return_value = repo

        user_inst = MagicMock()
        user_inst.id = 6
        mock_user_cls.return_value = user_inst

        args = _make_args(
            username="passuser", passkey=True, fido2=False, download=True, admin=False
        )
        result = cmd_add(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "Passkey credential" in out
        assert "TOTP Setup" not in out

    @patch("auth.cli.User")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_add_fido2_user(self, mock_get_db, mock_repo_cls, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.username_exists.return_value = False
        mock_repo_cls.return_value = repo

        user_inst = MagicMock()
        user_inst.id = 7
        mock_user_cls.return_value = user_inst

        args = _make_args(
            username="fidouser", passkey=False, fido2=True, download=True, admin=False
        )
        result = cmd_add(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "FIDO2 credential" in out

    @patch("auth.cli.get_db")
    def test_add_db_error(self, mock_get_db, capsys):
        db = MagicMock()
        db.initialize.side_effect = RuntimeError("permission denied")
        mock_get_db.return_value = db

        args = _make_args(
            username="newuser", passkey=False, fido2=False, download=True, admin=False
        )
        result = cmd_add(args)

        assert result == 1
        assert "permission denied" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_delete
# ---------------------------------------------------------------------------


class TestCmdDelete:
    @patch("auth.cli.SessionRepository")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_delete_user_confirmed(
        self, mock_get_db, mock_user_cls, mock_sess_cls, capsys
    ):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(is_admin=False)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_user_cls.return_value = repo
        sess_repo = MagicMock()
        sess_repo.invalidate_user_sessions.return_value = 2
        mock_sess_cls.return_value = sess_repo

        args = _make_args(username="testuser", force=False, yes=True)
        result = cmd_delete(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "deleted" in out
        assert "2 session(s)" in out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_delete_user_not_found(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.get_by_username.return_value = None
        mock_user_cls.return_value = repo

        args = _make_args(username="ghost", force=False, yes=True)
        result = cmd_delete(args)

        assert result == 1
        assert "not found" in capsys.readouterr().err

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_delete_admin_without_force(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(is_admin=True)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_user_cls.return_value = repo

        args = _make_args(username="admin", force=False, yes=True)
        result = cmd_delete(args)

        assert result == 1
        assert "--force" in capsys.readouterr().err

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_delete_aborted_by_user(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(is_admin=False)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_user_cls.return_value = repo

        args = _make_args(username="testuser", force=False, yes=False)

        with patch("builtins.input", return_value="n"):
            result = cmd_delete(args)

        assert result == 0
        assert "Aborted" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_grant / cmd_revoke
# ---------------------------------------------------------------------------


class TestCmdGrant:
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_grant_success(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(can_download=False)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_repo_cls.return_value = repo

        result = cmd_grant(_make_args(username="testuser"))
        assert result == 0
        assert "granted" in capsys.readouterr().out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_grant_already_has_permission(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(can_download=True)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_repo_cls.return_value = repo

        result = cmd_grant(_make_args(username="testuser"))
        assert result == 0
        assert "already has" in capsys.readouterr().out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_grant_user_not_found(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        repo = MagicMock()
        repo.get_by_username.return_value = None
        mock_repo_cls.return_value = repo

        result = cmd_grant(_make_args(username="ghost"))
        assert result == 1
        assert "not found" in capsys.readouterr().err


class TestCmdRevoke:
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_revoke_success(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(can_download=True)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_repo_cls.return_value = repo

        result = cmd_revoke(_make_args(username="testuser"))
        assert result == 0
        assert "revoked" in capsys.readouterr().out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_revoke_already_revoked(self, mock_get_db, mock_repo_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(can_download=False)
        repo = MagicMock()
        repo.get_by_username.return_value = user
        mock_repo_cls.return_value = repo

        result = cmd_revoke(_make_args(username="testuser"))
        assert result == 0
        assert "already has no" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_kick
# ---------------------------------------------------------------------------


class TestCmdKick:
    @patch("auth.cli.SessionRepository")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_kick_with_sessions(
        self, mock_get_db, mock_user_cls, mock_sess_cls, capsys
    ):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user()
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo
        sess_repo = MagicMock()
        sess_repo.invalidate_user_sessions.return_value = 3
        mock_sess_cls.return_value = sess_repo

        result = cmd_kick(_make_args(username="testuser"))
        assert result == 0
        assert "3 session(s)" in capsys.readouterr().out

    @patch("auth.cli.SessionRepository")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_kick_no_sessions(self, mock_get_db, mock_user_cls, mock_sess_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user()
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo
        sess_repo = MagicMock()
        sess_repo.invalidate_user_sessions.return_value = 0
        mock_sess_cls.return_value = sess_repo

        result = cmd_kick(_make_args(username="testuser"))
        assert result == 0
        assert "no active sessions" in capsys.readouterr().out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_kick_user_not_found(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = None
        mock_user_cls.return_value = user_repo

        result = cmd_kick(_make_args(username="ghost"))
        assert result == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_info
# ---------------------------------------------------------------------------


class TestCmdInfo:
    @patch("auth.cli.SessionRepository")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_info_with_session(self, mock_get_db, mock_user_cls, mock_sess_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user()
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo

        session = _make_session()
        sess_repo = MagicMock()
        sess_repo.get_by_user_id.return_value = session
        mock_sess_cls.return_value = sess_repo

        result = cmd_info(_make_args(username="testuser"))
        assert result == 0
        out = capsys.readouterr().out
        assert "testuser" in out
        assert "Active session:" in out
        assert "192.168.1.100" in out

    @patch("auth.cli.SessionRepository")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_info_no_session(self, mock_get_db, mock_user_cls, mock_sess_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(last_login=None, created_at=None)
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo

        sess_repo = MagicMock()
        sess_repo.get_by_user_id.return_value = None
        mock_sess_cls.return_value = sess_repo

        result = cmd_info(_make_args(username="testuser"))
        assert result == 0
        out = capsys.readouterr().out
        assert "No active session" in out
        assert "Never" in out
        assert "Unknown" in out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_info_user_not_found(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = None
        mock_user_cls.return_value = user_repo

        result = cmd_info(_make_args(username="ghost"))
        assert result == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_totp_reset
# ---------------------------------------------------------------------------


class TestCmdTotpReset:
    @patch("auth.cli.SessionRepository")
    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_totp_reset_success(
        self, mock_get_db, mock_user_cls, mock_sess_cls, capsys
    ):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(auth_type=AuthType.TOTP)
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo

        sess_repo = MagicMock()
        mock_sess_cls.return_value = sess_repo

        result = cmd_totp_reset(_make_args(username="testuser", yes=True))
        assert result == 0
        out = capsys.readouterr().out
        assert "TOTP reset" in out
        assert "New TOTP Setup" in out
        assert "Secret (base32)" in out

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_totp_reset_user_not_found(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = None
        mock_user_cls.return_value = user_repo

        result = cmd_totp_reset(_make_args(username="ghost", yes=True))
        assert result == 1
        assert "not found" in capsys.readouterr().err

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_totp_reset_non_totp_user(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(auth_type=AuthType.PASSKEY)
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo

        result = cmd_totp_reset(_make_args(username="passuser", yes=True))
        assert result == 1
        assert "does not use TOTP" in capsys.readouterr().err

    @patch("auth.cli.UserRepository")
    @patch("auth.cli.get_db")
    def test_totp_reset_aborted(self, mock_get_db, mock_user_cls, capsys):
        db = MagicMock()
        mock_get_db.return_value = db
        user = _make_user(auth_type=AuthType.TOTP)
        user_repo = MagicMock()
        user_repo.get_by_username.return_value = user
        mock_user_cls.return_value = user_repo

        with patch("builtins.input", return_value="n"):
            result = cmd_totp_reset(_make_args(username="testuser", yes=False))

        assert result == 0
        assert "Aborted" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main() - argument parsing
# ---------------------------------------------------------------------------


class TestMain:
    @patch("auth.cli.cmd_list")
    def test_dispatch_list(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "list"]):
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    @patch("auth.cli.cmd_add")
    def test_dispatch_add(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "add", "alice"]):
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    @patch("auth.cli.cmd_delete")
    def test_dispatch_delete(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "delete", "alice", "-y"]):
            result = main()
        assert result == 0
        mock_cmd.assert_called_once()

    def test_no_command_returns_1(self, capsys):
        with patch("sys.argv", ["audiobook-user"]):
            result = main()
        assert result == 1

    @patch("auth.cli.cmd_init")
    def test_dispatch_init(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "init"]):
            result = main()
        assert result == 0

    @patch("auth.cli.cmd_totp_reset")
    def test_dispatch_totp_reset(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "totp-reset", "bob", "-y"]):
            result = main()
        assert result == 0

    @patch("auth.cli.cmd_kick")
    def test_dispatch_kick(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "kick", "alice"]):
            result = main()
        assert result == 0

    @patch("auth.cli.cmd_info")
    def test_dispatch_info(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "info", "alice"]):
            result = main()
        assert result == 0

    @patch("auth.cli.cmd_grant")
    def test_dispatch_grant(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "grant", "alice"]):
            result = main()
        assert result == 0

    @patch("auth.cli.cmd_revoke")
    def test_dispatch_revoke(self, mock_cmd):
        mock_cmd.return_value = 0
        with patch("sys.argv", ["audiobook-user", "--dev", "revoke", "alice"]):
            result = main()
        assert result == 0
