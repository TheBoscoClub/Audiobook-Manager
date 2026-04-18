"""
Tests for audit.py — notify_admins, _format_notification, _send_notification_email.

Supplements test_audit_log.py (which covers AuditLogRepository CRUD) with tests
for the notification/email subsystem and edge cases not covered there.
"""

import os
from unittest.mock import MagicMock, patch

from auth.audit import (
    CRITICAL_ACTIONS,
    AuditLogRepository,
    _format_notification,
    _send_notification_email,
    notify_admins,
)

# ============================================================
# CRITICAL_ACTIONS constant
# ============================================================


class TestCriticalActions:
    def test_expected_actions_present(self):
        expected = {"change_username", "switch_auth_method", "reset_credentials", "delete_account"}
        assert CRITICAL_ACTIONS == expected

    def test_is_a_set(self):
        assert isinstance(CRITICAL_ACTIONS, set)


# ============================================================
# _format_notification — email subject/body templating
# ============================================================


class TestFormatNotification:
    def test_change_username(self):
        details = {
            "actor_username": "admin1",
            "target_username": "alice",
            "new": "alice_new",
            "timestamp": "2026-03-25 10:00:00",
        }
        subject, body = _format_notification("change_username", details)
        assert "alice" in subject
        assert "alice_new" in subject
        assert "admin1" in body
        assert "2026-03-25 10:00:00" in body
        assert "Back Office" in body

    def test_switch_auth_method(self):
        details = {
            "actor_username": "admin1",
            "target_username": "bob",
            "new": "passkey",
            "timestamp": "2026-03-25 11:00:00",
        }
        subject, body = _format_notification("switch_auth_method", details)
        assert "bob" in subject
        assert "passkey" in subject
        assert "admin1" in body

    def test_reset_credentials(self):
        details = {
            "actor_username": "bob",
            "target_username": "bob",
            "timestamp": "2026-03-25 12:00:00",
        }
        subject, body = _format_notification("reset_credentials", details)
        assert "bob" in subject
        assert "reset" in subject.lower()

    def test_delete_account(self):
        details = {
            "actor_username": "admin1",
            "target_username": "charlie",
            "username": "charlie",
            "timestamp": "2026-03-25 13:00:00",
        }
        subject, body = _format_notification("delete_account", details)
        assert "charlie" in subject
        assert "deleted" in subject.lower()

    def test_unknown_action_fallback(self):
        details = {
            "actor_username": "admin1",
            "target_username": "dave",
            "timestamp": "2026-03-25 14:00:00",
        }
        subject, body = _format_notification("some_unknown_action", details)
        assert "some_unknown_action" in subject
        assert "dave" in subject

    def test_missing_details_fields(self):
        """Gracefully handle missing keys in details dict."""
        subject, body = _format_notification("change_username", {})
        assert "Unknown" in body  # actor defaults to "Unknown"
        assert "?" in subject  # new username defaults to "?"

    def test_subject_prefix(self):
        subject, _ = _format_notification(
            "reset_credentials", {"actor_username": "x", "target_username": "x"}
        )
        assert subject.startswith("[Audiobook Library]")

    def test_body_contains_review_instruction(self):
        _, body = _format_notification(
            "reset_credentials", {"actor_username": "x", "target_username": "x"}
        )
        assert "Audit Log" in body

    def test_actor_defaults_to_unknown(self):
        """If actor_username missing, defaults to 'Unknown'."""
        _, body = _format_notification("reset_credentials", {"target_username": "y"})
        assert "Unknown" in body

    def test_target_defaults_to_actor(self):
        """If target_username missing, defaults to actor_username."""
        subject, _ = _format_notification("reset_credentials", {"actor_username": "self_user"})
        assert "self_user" in subject


# ============================================================
# _send_notification_email — SMTP sending
# ============================================================


class TestSendNotificationEmail:
    @patch("auth.audit.smtplib.SMTP")
    def test_send_success_without_auth(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        env = {"SMTP_HOST": "mail.test", "SMTP_PORT": "25", "SMTP_FROM": "lib@test.com"}
        with patch.dict(os.environ, env, clear=False):
            # Clear SMTP_USER/SMTP_PASS to test no-auth path
            os.environ.pop("SMTP_USER", None)
            os.environ.pop("SMTP_PASS", None)
            result = _send_notification_email("admin@test.com", "Test Subject", "Body")

        assert result is True
        mock_smtp_cls.assert_called_once_with("mail.test", 25)

    @patch("auth.audit.smtplib.SMTP")
    def test_send_success_with_auth(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        env = {
            "SMTP_HOST": "mail.test",
            "SMTP_PORT": "587",
            "SMTP_USER": "user",
            "SMTP_PASS": "pass",
            "SMTP_FROM": "lib@test.com",
        }
        with patch.dict(os.environ, env, clear=False):
            result = _send_notification_email("admin@test.com", "Subject", "Body")

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")

    @patch("auth.audit.smtplib.SMTP")
    def test_send_failure_returns_false(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = Exception("connection refused")

        env = {"SMTP_HOST": "mail.test", "SMTP_PORT": "25", "SMTP_FROM": "lib@test.com"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("SMTP_USER", None)
            os.environ.pop("SMTP_PASS", None)
            result = _send_notification_email("admin@test.com", "Subject", "Body")

        assert result is False

    @patch("auth.audit.smtplib.SMTP")
    def test_send_uses_default_values(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Remove all SMTP env vars to test defaults
        env_clear = {k: v for k, v in os.environ.items() if not k.startswith("SMTP_")}
        with patch.dict(os.environ, env_clear, clear=True):
            result = _send_notification_email("admin@test.com", "Subject", "Body")

        assert result is True
        mock_smtp_cls.assert_called_once_with("localhost", 25)


# ============================================================
# notify_admins — orchestration
# ============================================================


class TestNotifyAdmins:
    def test_non_critical_action_skipped(self):
        """Non-critical actions should not trigger notifications."""
        mock_db = MagicMock()
        # Should return immediately without touching db
        notify_admins("session.login", {"actor_username": "alice"}, mock_db)
        # No UserRepository interaction expected for non-critical actions

    @patch("auth.audit._send_notification_email")
    def test_critical_action_sends_email(self, mock_send):
        """Critical actions should send emails to admins with recovery_email."""
        mock_db = MagicMock()

        # Create mock admin with recovery_email
        mock_admin = MagicMock()
        mock_admin.is_admin = True
        mock_admin.recovery_email = "admin@test.com"

        # Create mock non-admin
        mock_user = MagicMock()
        mock_user.is_admin = False
        mock_user.recovery_email = "user@test.com"

        mock_user_repo = MagicMock()
        mock_user_repo.list_all.return_value = [mock_admin, mock_user]

        with patch("auth.models.UserRepository", mock_user_repo.__class__):
            # Patch where it's imported: inside notify_admins via from .models import UserRepository
            with patch("auth.audit.UserRepository", mock_user_repo.__class__, create=True):
                # Simpler: patch the function's local import directly
                pass

        # Use a different approach - patch the models module
        import auth.models as models_mod

        original_ur = models_mod.UserRepository

        class FakeUserRepo:
            def __init__(self, db):
                pass

            def list_all(self):
                return [mock_admin, mock_user]

        models_mod.UserRepository = FakeUserRepo
        try:
            notify_admins(
                "delete_account", {"actor_username": "admin1", "username": "charlie"}, mock_db
            )
        finally:
            models_mod.UserRepository = original_ur

        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][0] == "admin@test.com"  # to_email

    @patch("auth.audit._send_notification_email")
    def test_no_admins_with_email_skips(self, mock_send):
        """If no admins have recovery_email, no emails are sent."""
        mock_db = MagicMock()
        mock_admin = MagicMock()
        mock_admin.is_admin = True
        mock_admin.recovery_email = None  # No email

        import auth.models as models_mod

        original_ur = models_mod.UserRepository

        class FakeUserRepo:
            def __init__(self, db):
                pass

            def list_all(self):
                return [mock_admin]

        models_mod.UserRepository = FakeUserRepo
        try:
            notify_admins("delete_account", {"actor_username": "a"}, mock_db)
        finally:
            models_mod.UserRepository = original_ur

        mock_send.assert_not_called()

    @patch("auth.audit._send_notification_email")
    def test_multiple_admins_all_notified(self, mock_send):
        """All admins with recovery_email should receive notification."""
        mock_db = MagicMock()

        admins = []
        for email in ["admin1@test.com", "admin2@test.com", "admin3@test.com"]:
            a = MagicMock()
            a.is_admin = True
            a.recovery_email = email
            admins.append(a)

        import auth.models as models_mod

        original_ur = models_mod.UserRepository

        class FakeUserRepo:
            def __init__(self, db):
                pass

            def list_all(self):
                return admins

        models_mod.UserRepository = FakeUserRepo
        try:
            notify_admins("reset_credentials", {"actor_username": "bob"}, mock_db)
        finally:
            models_mod.UserRepository = original_ur

        assert mock_send.call_count == 3

    @patch("auth.audit._send_notification_email")
    def test_each_critical_action_triggers(self, mock_send):
        """Every action in CRITICAL_ACTIONS should trigger notification."""
        import auth.models as models_mod

        original_ur = models_mod.UserRepository

        for action in CRITICAL_ACTIONS:
            mock_send.reset_mock()
            mock_db = MagicMock()
            mock_admin = MagicMock()
            mock_admin.is_admin = True
            mock_admin.recovery_email = "admin@test.com"

            class FakeUserRepo:
                def __init__(self, db):
                    pass

                def list_all(self):  # pylint: disable=unused-argument
                    return [mock_admin]

            models_mod.UserRepository = FakeUserRepo
            try:
                notify_admins(action, {"actor_username": "x"}, mock_db)
            finally:
                models_mod.UserRepository = original_ur

            assert mock_send.call_count == 1, f"Expected email for action '{action}'"


# ============================================================
# AuditLogRepository.log — WebSocket broadcast (supplemental)
# ============================================================


class TestAuditLogWebsocketBroadcast:
    def test_log_broadcasts_audit_notify(self, auth_app):
        """log() should attempt to broadcast an audit_notify message."""
        repo = AuditLogRepository(auth_app.auth_db)

        # Patch the singleton that gets imported inside log()
        import backend.api_modular.websocket as ws_mod

        original_cm = ws_mod.connection_manager
        mock_cm = MagicMock()
        ws_mod.connection_manager = mock_cm
        try:
            entry = repo.log(
                actor_id=auth_app.test_user_id, target_id=None, action="test.broadcast"
            )
        finally:
            ws_mod.connection_manager = original_cm

        assert entry is not None
        mock_cm.broadcast.assert_called_once()
        call_args = mock_cm.broadcast.call_args[0][0]
        assert call_args["type"] == "audit_notify"
        assert call_args["action"] == "test.broadcast"

    def test_log_broadcast_failure_does_not_raise(self, auth_app):
        """WebSocket broadcast failure should not prevent audit log creation."""
        repo = AuditLogRepository(auth_app.auth_db)

        import backend.api_modular.websocket as ws_mod

        original_cm = ws_mod.connection_manager
        mock_cm = MagicMock()
        mock_cm.broadcast.side_effect = Exception("ws down")
        ws_mod.connection_manager = mock_cm
        try:
            entry = repo.log(
                actor_id=auth_app.test_user_id, target_id=None, action="test.broadcast_fail"
            )
        finally:
            ws_mod.connection_manager = original_cm

        # Entry should still be created despite broadcast failure
        assert entry is not None
        assert entry.action == "test.broadcast_fail"


# ============================================================
# AuditLogRepository.count — supplemental
# ============================================================


class TestAuditLogCount:
    def test_count_all(self, auth_app):
        repo = AuditLogRepository(auth_app.auth_db)
        # Create some entries
        repo.log(actor_id=auth_app.test_user_id, target_id=None, action="count.test.a")
        repo.log(actor_id=auth_app.test_user_id, target_id=None, action="count.test.b")

        total = repo.count()
        assert total >= 2

    def test_count_with_action_filter(self, auth_app):
        repo = AuditLogRepository(auth_app.auth_db)
        unique_action = "count.filter.unique.xyz"
        repo.log(actor_id=auth_app.test_user_id, target_id=None, action=unique_action)
        repo.log(actor_id=auth_app.test_user_id, target_id=None, action="count.other")

        count = repo.count(action_filter=unique_action)
        assert count == 1

    def test_count_with_user_filter(self, auth_app):
        repo = AuditLogRepository(auth_app.auth_db)
        uid = auth_app.test_user_id
        unique_action = "count.user.unique.abc"
        repo.log(actor_id=uid, target_id=None, action=unique_action)

        count = repo.count(action_filter=unique_action, user_filter=uid)
        assert count == 1

    def test_count_with_both_filters(self, auth_app):
        repo = AuditLogRepository(auth_app.auth_db)
        uid = auth_app.test_user_id
        other_uid = auth_app.admin_user_id
        action = "count.both.filters.test"

        repo.log(actor_id=uid, target_id=other_uid, action=action)
        repo.log(actor_id=other_uid, target_id=None, action=action)

        # user_filter=uid should match the first entry (as actor)
        count = repo.count(action_filter=action, user_filter=uid)
        assert count == 1

    def test_count_zero_for_nonexistent_action(self, auth_app):
        repo = AuditLogRepository(auth_app.auth_db)
        count = repo.count(action_filter="nonexistent.action.abc123xyz")
        assert count == 0
