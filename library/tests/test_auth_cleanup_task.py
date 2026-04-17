"""
Tests for AuthCleanupTask — auth database cleanup maintenance task.

Covers all code paths: _get_auth_db(), validate(), execute() with all
cleanup types, progress callbacks, error handling, and DB close guarantees.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

from backend.api_modular.maintenance_tasks.auth_cleanup import (
    AuthCleanupTask,
    _ACCESS_REQUEST_RETENTION_DAYS,
    _get_auth_db,
)


# ============================================================
# Helpers
# ============================================================


def _make_mock_db():
    """Create a mock AuthDatabase with a working connection() context manager."""
    db = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0
    mock_conn.execute.return_value = mock_cursor

    @contextmanager
    def _connection():
        yield mock_conn

    db.connection = _connection
    return db, mock_conn, mock_cursor


def _make_mock_repos(stale=0, expired_regs=0, expired_rec=0):
    """Create mock repository classes that return specified cleanup counts."""
    mock_session_repo = MagicMock()
    mock_session_repo.return_value.cleanup_stale.return_value = stale

    mock_reg_repo = MagicMock()
    mock_reg_repo.return_value.cleanup_expired.return_value = expired_regs

    mock_rec_repo = MagicMock()
    mock_rec_repo.return_value.cleanup_expired.return_value = expired_rec

    return mock_session_repo, mock_reg_repo, mock_rec_repo


# ============================================================
# _get_auth_db()
# ============================================================


class TestGetAuthDb:
    def test_successful_import(self):
        """_get_auth_db imports AuthDatabase and returns an instance."""
        mock_auth_db_class = MagicMock()
        mock_instance = MagicMock()
        mock_auth_db_class.return_value = mock_instance

        with patch.dict("sys.modules", {"database": MagicMock(AuthDatabase=mock_auth_db_class)}):
            result = _get_auth_db()
            mock_auth_db_class.assert_called_once()
            assert result is mock_instance

    def test_import_failure(self):
        """_get_auth_db raises when auth module cannot be imported."""
        # Remove 'database' from sys.modules so the import fails
        with patch.dict("sys.modules", {"database": None}):
            with pytest.raises(ImportError):
                _get_auth_db()


# ============================================================
# AuthCleanupTask class attributes
# ============================================================


class TestAuthCleanupTaskAttributes:
    def test_name(self):
        task = AuthCleanupTask()
        assert task.name == "auth_cleanup"

    def test_display_name(self):
        task = AuthCleanupTask()
        assert task.display_name == "Auth Data Cleanup"

    def test_description(self):
        task = AuthCleanupTask()
        assert "stale sessions" in task.description.lower()


# ============================================================
# validate()
# ============================================================


class TestValidate:
    def test_db_available(self):
        """validate returns ok=True when auth DB can be opened."""
        mock_db = MagicMock()
        task = AuthCleanupTask()
        with patch(
            "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db", return_value=mock_db
        ):
            result = task.validate({})

        assert result.ok is True
        mock_db.close.assert_called_once()

    def test_db_unavailable(self):
        """validate returns ok=False with message when DB open fails."""
        task = AuthCleanupTask()
        with patch(
            "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
            side_effect=RuntimeError("no sqlcipher"),
        ):
            result = task.validate({})

        assert result.ok is False
        assert "Auth DB unavailable" in result.message
        assert "no sqlcipher" in result.message


# ============================================================
# execute()
# ============================================================


class TestExecuteDbOpenFailure:
    def test_db_open_failure(self):
        """execute returns failure when _get_auth_db raises."""
        task = AuthCleanupTask()
        with patch(
            "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
            side_effect=RuntimeError("cannot connect"),
        ):
            result = task.execute({})

        assert result.success is False
        assert "Cannot open auth DB" in result.message
        assert "cannot connect" in result.message


class TestExecuteWithCleanup:
    """Tests for successful execute() with various cleanup counts."""

    def _run_execute(
        self, stale=0, expired_regs=0, expired_rec=0, old_requests=0, progress_callback=None
    ):
        """Helper to run execute with mocked repos and DB."""
        mock_db, mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.rowcount = old_requests
        mock_session_repo, mock_reg_repo, mock_rec_repo = _make_mock_repos(
            stale=stale, expired_regs=expired_regs, expired_rec=expired_rec
        )

        task = AuthCleanupTask()
        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup.SessionRepository",
                mock_session_repo,
                create=True,
            ),
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup.PendingRegistrationRepository",
                mock_reg_repo,
                create=True,
            ),
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup.PendingRecoveryRepository",
                mock_rec_repo,
                create=True,
            ),
        ):
            # Patch the deferred imports inside execute()
            import types

            mock_models = types.ModuleType("models")
            mock_models.SessionRepository = mock_session_repo
            mock_models.PendingRegistrationRepository = mock_reg_repo
            mock_models.PendingRecoveryRepository = mock_rec_repo

            with patch.dict("sys.modules", {"models": mock_models}):
                result = task.execute({}, progress_callback=progress_callback)

        return (result, mock_db, mock_conn, mock_session_repo, mock_reg_repo, mock_rec_repo)

    def test_all_cleanup_types_with_items(self):
        """All four cleanup types find and remove items."""
        result, mock_db, mock_conn, *_ = self._run_execute(
            stale=5, expired_regs=3, expired_rec=2, old_requests=7
        )

        assert result.success is True
        assert result.data["stale_sessions"] == 5
        assert result.data["expired_registrations"] == 3
        assert result.data["expired_recoveries"] == 2
        assert result.data["old_access_requests"] == 7
        assert "Cleaned 17 records" in result.message
        assert "5 stale sessions" in result.message
        assert "3 expired registrations" in result.message
        assert "2 expired recoveries" in result.message
        assert "7 old access requests" in result.message
        mock_db.close.assert_called_once()

    def test_no_stale_data_found(self):
        """All cleanup types return 0 items."""
        result, mock_db, *_ = self._run_execute(
            stale=0, expired_regs=0, expired_rec=0, old_requests=0
        )

        assert result.success is True
        assert result.message == "No stale auth data found"
        assert result.data["stale_sessions"] == 0
        assert result.data["expired_registrations"] == 0
        assert result.data["expired_recoveries"] == 0
        assert result.data["old_access_requests"] == 0
        mock_db.close.assert_called_once()

    def test_only_stale_sessions(self):
        """Only stale sessions found."""
        result, *_ = self._run_execute(stale=10)
        assert result.success is True
        assert "10 stale sessions" in result.message
        assert "expired registrations" not in result.message

    def test_only_expired_registrations(self):
        """Only expired registrations found."""
        result, *_ = self._run_execute(expired_regs=4)
        assert result.success is True
        assert "4 expired registrations" in result.message
        assert "stale sessions" not in result.message

    def test_only_expired_recoveries(self):
        """Only expired recoveries found."""
        result, *_ = self._run_execute(expired_rec=6)
        assert result.success is True
        assert "6 expired recoveries" in result.message

    def test_only_old_access_requests(self):
        """Only old access requests found."""
        result, *_ = self._run_execute(old_requests=8)
        assert result.success is True
        assert "8 old access requests" in result.message

    def test_session_repo_called_with_grace_minutes(self):
        """SessionRepository.cleanup_stale is called with grace_minutes=30."""
        _, _, _, mock_session_repo, *_ = self._run_execute(stale=1)
        mock_session_repo.return_value.cleanup_stale.assert_called_once_with(grace_minutes=30)

    def test_registration_repo_cleanup_called(self):
        """PendingRegistrationRepository.cleanup_expired is called."""
        _, _, _, _, mock_reg_repo, _ = self._run_execute(expired_regs=1)
        mock_reg_repo.return_value.cleanup_expired.assert_called_once()

    def test_recovery_repo_cleanup_called(self):
        """PendingRecoveryRepository.cleanup_expired is called."""
        _, _, _, _, _, mock_rec_repo = self._run_execute(expired_rec=1)
        mock_rec_repo.return_value.cleanup_expired.assert_called_once()

    def test_access_requests_sql_query(self):
        """Access request cleanup uses correct SQL with status filter and date cutoff."""
        _, _, mock_conn, *_ = self._run_execute(old_requests=3)
        mock_conn.execute.assert_called_once()
        sql_call = mock_conn.execute.call_args
        sql = sql_call[0][0]
        assert "DELETE FROM access_requests" in sql
        assert "status IN ('approved', 'denied')" in sql
        assert "requested_at < ?" in sql
        # The cutoff param should be an ISO format string
        cutoff_param = sql_call[0][1][0]
        assert isinstance(cutoff_param, str)
        assert "T" in cutoff_param  # ISO format has T separator


class TestExecuteProgressCallbacks:
    def test_progress_callbacks_called_in_order(self):
        """Progress callback is invoked at expected points."""
        mock_db, mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.rowcount = 0
        mock_session_repo, mock_reg_repo, mock_rec_repo = _make_mock_repos()

        import types

        mock_models = types.ModuleType("models")
        mock_models.SessionRepository = mock_session_repo
        mock_models.PendingRegistrationRepository = mock_reg_repo
        mock_models.PendingRecoveryRepository = mock_rec_repo

        progress = MagicMock()
        task = AuthCleanupTask()

        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch.dict("sys.modules", {"models": mock_models}),
        ):
            task.execute({}, progress_callback=progress)

        assert progress.call_count == 5
        calls = progress.call_args_list
        assert calls[0] == call(0.1, "Cleaning stale sessions...")
        assert calls[1] == call(0.3, "Cleaning expired registrations...")
        assert calls[2] == call(0.5, "Cleaning expired recovery tokens...")
        assert calls[3] == call(0.7, "Cleaning old access requests...")
        assert calls[4] == call(1.0, "Complete")

    def test_no_progress_callback(self):
        """execute works fine when progress_callback is None."""
        mock_db, mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.rowcount = 0
        mock_session_repo, mock_reg_repo, mock_rec_repo = _make_mock_repos()

        import types

        mock_models = types.ModuleType("models")
        mock_models.SessionRepository = mock_session_repo
        mock_models.PendingRegistrationRepository = mock_reg_repo
        mock_models.PendingRecoveryRepository = mock_rec_repo

        task = AuthCleanupTask()

        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch.dict("sys.modules", {"models": mock_models}),
        ):
            result = task.execute({}, progress_callback=None)

        assert result.success is True


class TestExecuteErrorHandling:
    def test_exception_during_cleanup_closes_db(self):
        """DB is closed even when an exception occurs during cleanup."""
        mock_db, mock_conn, mock_cursor = _make_mock_db()

        mock_session_repo = MagicMock()
        mock_session_repo.return_value.cleanup_stale.side_effect = RuntimeError("db locked")

        import types

        mock_models = types.ModuleType("models")
        mock_models.SessionRepository = mock_session_repo
        mock_models.PendingRegistrationRepository = MagicMock()
        mock_models.PendingRecoveryRepository = MagicMock()

        task = AuthCleanupTask()

        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch.dict("sys.modules", {"models": mock_models}),
        ):
            result = task.execute({})

        assert result.success is False
        assert "db locked" in result.message
        mock_db.close.assert_called_once()

    def test_exception_in_access_request_cleanup(self):
        """Error during access request SQL still closes DB."""
        mock_db = MagicMock()

        @contextmanager
        def _connection():
            raise RuntimeError("connection failed")

        mock_db.connection = _connection
        mock_session_repo, mock_reg_repo, mock_rec_repo = _make_mock_repos()

        import types

        mock_models = types.ModuleType("models")
        mock_models.SessionRepository = mock_session_repo
        mock_models.PendingRegistrationRepository = mock_reg_repo
        mock_models.PendingRecoveryRepository = mock_rec_repo

        task = AuthCleanupTask()

        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch.dict("sys.modules", {"models": mock_models}),
        ):
            result = task.execute({})

        assert result.success is False
        assert "connection failed" in result.message
        mock_db.close.assert_called_once()


class TestExecuteDbCloseGuarantee:
    def test_db_close_on_success(self):
        """DB.close() called on successful execution."""
        mock_db, mock_conn, mock_cursor = _make_mock_db()
        mock_cursor.rowcount = 0
        mock_session_repo, mock_reg_repo, mock_rec_repo = _make_mock_repos()

        import types

        mock_models = types.ModuleType("models")
        mock_models.SessionRepository = mock_session_repo
        mock_models.PendingRegistrationRepository = mock_reg_repo
        mock_models.PendingRecoveryRepository = mock_rec_repo

        task = AuthCleanupTask()

        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch.dict("sys.modules", {"models": mock_models}),
        ):
            result = task.execute({})

        assert result.success is True
        mock_db.close.assert_called_once()

    def test_db_close_on_error(self):
        """DB.close() called when execution raises."""
        mock_db = MagicMock()

        import types

        mock_models = types.ModuleType("models")
        mock_models.SessionRepository = MagicMock(side_effect=RuntimeError("boom"))
        mock_models.PendingRegistrationRepository = MagicMock()
        mock_models.PendingRecoveryRepository = MagicMock()

        task = AuthCleanupTask()

        with (
            patch(
                "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
                return_value=mock_db,
            ),
            patch.dict("sys.modules", {"models": mock_models}),
        ):
            result = task.execute({})

        assert result.success is False
        mock_db.close.assert_called_once()

    def test_db_not_closed_when_open_fails(self):
        """DB.close() is NOT called when _get_auth_db itself fails."""
        task = AuthCleanupTask()
        with patch(
            "backend.api_modular.maintenance_tasks.auth_cleanup._get_auth_db",
            side_effect=RuntimeError("no db"),
        ):
            result = task.execute({})

        assert result.success is False
        # No db object was created, so close should never be called


# ============================================================
# estimate_duration()
# ============================================================


class TestEstimateDuration:
    def test_returns_5(self):
        task = AuthCleanupTask()
        assert task.estimate_duration() == 5


# ============================================================
# Access request retention constant
# ============================================================


class TestAccessRequestRetention:
    def test_retention_days_is_90(self):
        assert _ACCESS_REQUEST_RETENTION_DAYS == 90
