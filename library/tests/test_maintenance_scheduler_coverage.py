"""
Comprehensive tests for maintenance_scheduler.py.

Covers:
- The main scheduler loop (mocked sleep/time)
- run_auth_cleanup() — stale sessions, expired registrations/recoveries
- Error handling when auth DB is unavailable
- Scheduler configuration (poll interval, lock path)
- Integration with maintenance task registry
- Graceful exception handling during cleanup
- Signal handlers and shutdown logic
- find_due_windows(), record_history(), write_notification()
- update_next_run() for recurring and one-time windows
- check_announcements()
- execute_window() with known/unknown tasks, validation failures
- File lock contention (BlockingIOError)
"""

import importlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Ensure library is importable
LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))


# ---------------------------------------------------------------------------
# We must set AUDIOBOOKS_RUN_DIR before importing maintenance_scheduler
# because the module checks it at import time and raises RuntimeError.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _set_run_dir_env(tmp_path, monkeypatch):
    """Set AUDIOBOOKS_RUN_DIR so the module can be imported."""
    monkeypatch.setenv("AUDIOBOOKS_RUN_DIR", str(tmp_path))


# ---------------------------------------------------------------------------
# Helper to force-reimport the module with current env
# ---------------------------------------------------------------------------
def _import_scheduler():
    """Force-reimport maintenance_scheduler with current env vars."""
    mod_name = "backend.maintenance_scheduler"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import backend

    if hasattr(backend, "maintenance_scheduler"):
        delattr(backend, "maintenance_scheduler")
    from backend import maintenance_scheduler

    return maintenance_scheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scheduler():
    """Provide a freshly-imported scheduler module."""
    return _import_scheduler()


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary SQLite DB with the required tables."""
    p = tmp_path / "test.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        """
        CREATE TABLE maintenance_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            task_type TEXT NOT NULL,
            task_params TEXT DEFAULT '{}',
            schedule_type TEXT DEFAULT 'one_time',
            cron_expression TEXT,
            next_run_at TEXT,
            status TEXT DEFAULT 'active',
            lead_time_hours INTEGER DEFAULT 1
        );
        CREATE TABLE maintenance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_id INTEGER,
            started_at TEXT,
            completed_at TEXT,
            status TEXT,
            result_message TEXT,
            result_data TEXT
        );
        CREATE TABLE maintenance_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_type TEXT,
            payload TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """
    )
    conn.close()
    return p


# ===================================================================
# Module-level configuration tests
# ===================================================================


class TestModuleConfiguration:
    """Tests for module-level constants and env var handling."""

    def test_poll_interval_default(self, scheduler):
        assert scheduler.POLL_INTERVAL == 60

    def test_lock_path_derived_from_run_dir(self, tmp_path):
        sched = _import_scheduler()
        expected = str(tmp_path / "maintenance.lock")
        assert sched.LOCK_PATH == expected

    def test_lock_path_override_via_env(self, monkeypatch):
        monkeypatch.setenv("MAINTENANCE_LOCK", "/custom/lock")
        sched = _import_scheduler()
        assert sched.LOCK_PATH == "/custom/lock"

    def test_missing_run_dir_raises(self, monkeypatch):
        """Without AUDIOBOOKS_RUN_DIR the module must raise RuntimeError."""
        monkeypatch.delenv("AUDIOBOOKS_RUN_DIR", raising=False)
        monkeypatch.delenv("MAINTENANCE_LOCK", raising=False)
        mod_name = "backend.maintenance_scheduler"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        with pytest.raises(RuntimeError, match="AUDIOBOOKS_RUN_DIR is not set"):
            importlib.import_module(mod_name)


# ===================================================================
# Signal handler tests
# ===================================================================


class TestSignalHandlers:
    """Tests for SIGTERM / SIGINT handlers."""

    def test_sigterm_handler_sets_shutdown(self, scheduler):
        scheduler._shutdown = False
        scheduler._handle_sigterm(None, None)
        assert scheduler._shutdown is True

    def test_sigint_handler_sets_shutdown(self, scheduler):
        scheduler._shutdown = False
        scheduler._handle_sigterm(None, None)
        assert scheduler._shutdown is True


# ===================================================================
# Database helper tests
# ===================================================================


class TestGetDb:
    """Tests for get_db()."""

    def test_get_db_returns_connection(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            conn = scheduler.get_db()
            assert conn is not None
            assert conn.row_factory == sqlite3.Row
            conn.close()

    def test_get_db_uses_wal_mode(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            conn = scheduler.get_db()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
            conn.close()


# ===================================================================
# find_due_windows tests
# ===================================================================


class TestFindDueWindows:
    """Tests for find_due_windows()."""

    def test_returns_empty_when_no_windows(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            result = scheduler.find_due_windows()
            assert result == []

    def test_returns_due_active_windows(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows (name, task_type, next_run_at, status) "
            "VALUES (?, ?, datetime('now', '-1 minute'), 'active')",
            ("Test Task", "test_type"),
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "DATABASE_PATH", db_path):
            result = scheduler.find_due_windows()
            assert len(result) == 1
            assert result[0]["name"] == "Test Task"

    def test_ignores_inactive_windows(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows (name, task_type, next_run_at, status) "
            "VALUES (?, ?, datetime('now', '-1 minute'), 'completed')",
            ("Done Task", "test_type"),
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "DATABASE_PATH", db_path):
            result = scheduler.find_due_windows()
            assert result == []

    def test_ignores_future_windows(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows (name, task_type, next_run_at, status) "
            "VALUES (?, ?, datetime('now', '+1 hour'), 'active')",
            ("Future Task", "test_type"),
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "DATABASE_PATH", db_path):
            result = scheduler.find_due_windows()
            assert result == []


# ===================================================================
# record_history tests
# ===================================================================


class TestRecordHistory:
    """Tests for record_history()."""

    def test_inserts_history_record(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.record_history(
                window_id=1,
                started_at="2026-01-01T00:00:00Z",
                status="success",
                message="All good",
                data={"items": 5},
            )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT * FROM maintenance_history").fetchone()
        conn.close()

        assert row is not None
        assert row[1] == 1  # window_id
        assert row[4] == "success"  # status
        assert row[5] == "All good"  # result_message
        assert json.loads(row[6]) == {"items": 5}

    def test_defaults_data_to_empty_dict(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.record_history(1, "2026-01-01T00:00:00Z", "success", "ok")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT result_data FROM maintenance_history").fetchone()
        conn.close()
        assert json.loads(row[0]) == {}


# ===================================================================
# write_notification tests
# ===================================================================


class TestWriteNotification:
    """Tests for write_notification()."""

    def test_inserts_notification(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.write_notification(
                "update",
                {"window_id": 1, "status": "running", "message": "Starting"},
            )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT * FROM maintenance_notifications").fetchone()
        conn.close()

        assert row is not None
        assert row[1] == "update"
        payload = json.loads(row[2])
        assert payload["window_id"] == 1
        assert payload["status"] == "running"


# ===================================================================
# update_next_run tests
# ===================================================================


class TestUpdateNextRun:
    """Tests for update_next_run()."""

    def test_one_time_window_marks_completed(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows "
            "(id, name, task_type, schedule_type, status) "
            "VALUES (1, 'OneShot', 'test', 'one_time', 'active')"
        )
        conn.commit()
        conn.close()

        window = {"id": 1, "schedule_type": "one_time", "cron_expression": None}

        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.update_next_run(window)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status FROM maintenance_windows WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] == "completed"

    def test_recurring_window_updates_next_run(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows (id, name, task_type, "
            "schedule_type, cron_expression, status) "
            "VALUES (1, 'Recurring', 'test', 'recurring', '0 * * * *', 'active')"
        )
        conn.commit()
        conn.close()

        window = {"id": 1, "schedule_type": "recurring", "cron_expression": "0 * * * *"}

        mock_cron_instance = MagicMock()
        mock_cron_instance.get_next.return_value = datetime(
            2026, 6, 1, 12, 0, tzinfo=timezone.utc
        )
        mock_croniter_cls = MagicMock(return_value=mock_cron_instance)

        # croniter is imported locally inside update_next_run via
        # 'from croniter import croniter', so we mock the croniter module
        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules", {"croniter": MagicMock(croniter=mock_croniter_cls)}
            ),
        ):
            scheduler.update_next_run(window)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT next_run_at FROM maintenance_windows WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert "2026-06-01" in row[0]

    def test_recurring_window_croniter_error_logged(self, scheduler):
        window = {"id": 99, "schedule_type": "recurring", "cron_expression": "invalid"}

        mock_mod = MagicMock()
        mock_mod.croniter.side_effect = ValueError("bad cron")

        with patch.dict("sys.modules", {"croniter": mock_mod}):
            # Should not raise — errors are logged
            scheduler.update_next_run(window)


# ===================================================================
# execute_window tests
# ===================================================================


class TestExecuteWindow:
    """Tests for execute_window()."""

    def _make_window(self, **overrides):
        base = {
            "id": 1,
            "name": "Test Window",
            "task_type": "db_vacuum",
            "task_params": "{}",
            "schedule_type": "one_time",
            "cron_expression": None,
        }
        base.update(overrides)
        return base

    def test_unknown_task_type_records_failure(self, scheduler, db_path):
        window = self._make_window(task_type="nonexistent_task")
        mock_registry = MagicMock()
        mock_registry.get.return_value = None

        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules",
                {
                    "api_modular.maintenance_tasks": MagicMock(registry=mock_registry),
                },
            ),
        ):
            scheduler.execute_window(window)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, result_message FROM maintenance_history"
        ).fetchone()
        conn.close()
        assert row[0] == "failure"
        assert "Unknown task type" in row[1]

    def test_validation_failure_records_failure(self, scheduler, db_path):
        from backend.api_modular.maintenance_tasks.base import ValidationResult

        window = self._make_window()
        mock_task = MagicMock()
        mock_task.validate.return_value = ValidationResult(
            ok=False, message="bad params"
        )
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_task

        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules",
                {
                    "api_modular.maintenance_tasks": MagicMock(registry=mock_registry),
                },
            ),
        ):
            scheduler.execute_window(window)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, result_message FROM maintenance_history"
        ).fetchone()
        conn.close()
        assert row[0] == "failure"
        assert "Validation failed" in row[1]

    def test_successful_execution(self, scheduler, db_path):
        from backend.api_modular.maintenance_tasks.base import (
            ExecutionResult,
            ValidationResult,
        )

        window = self._make_window()
        mock_task = MagicMock()
        mock_task.validate.return_value = ValidationResult(ok=True)
        mock_task.execute.return_value = ExecutionResult(
            success=True, message="Vacuumed OK", data={"freed_mb": 10}
        )
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_task

        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules",
                {
                    "api_modular.maintenance_tasks": MagicMock(registry=mock_registry),
                },
            ),
            patch.object(scheduler, "update_next_run") as mock_update,
        ):
            scheduler.execute_window(window)
            mock_update.assert_called_once_with(window)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status, result_message FROM maintenance_history"
        ).fetchone()
        conn.close()
        assert row[0] == "success"
        assert "Vacuumed OK" in row[1]

    def test_failed_execution(self, scheduler, db_path):
        from backend.api_modular.maintenance_tasks.base import (
            ExecutionResult,
            ValidationResult,
        )

        window = self._make_window()
        mock_task = MagicMock()
        mock_task.validate.return_value = ValidationResult(ok=True)
        mock_task.execute.return_value = ExecutionResult(
            success=False, message="Disk full"
        )
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_task

        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules",
                {
                    "api_modular.maintenance_tasks": MagicMock(registry=mock_registry),
                },
            ),
            patch.object(scheduler, "update_next_run"),
        ):
            scheduler.execute_window(window)

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT status FROM maintenance_history").fetchone()
        conn.close()
        assert row[0] == "failure"

    def test_notifications_sent_during_execution(self, scheduler, db_path):
        from backend.api_modular.maintenance_tasks.base import (
            ExecutionResult,
            ValidationResult,
        )

        window = self._make_window()
        mock_task = MagicMock()
        mock_task.validate.return_value = ValidationResult(ok=True)
        mock_task.execute.return_value = ExecutionResult(success=True, message="Done")
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_task

        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules",
                {
                    "api_modular.maintenance_tasks": MagicMock(registry=mock_registry),
                },
            ),
            patch.object(scheduler, "update_next_run"),
        ):
            scheduler.execute_window(window)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT notification_type, payload FROM maintenance_notifications"
        ).fetchall()
        conn.close()

        types = [r[0] for r in rows]
        assert "update" in types
        payloads = [json.loads(r[1]) for r in rows]
        statuses = [p["status"] for p in payloads]
        assert "running" in statuses
        assert "success" in statuses

    def test_task_params_deserialized_with_db_path(self, scheduler, db_path):
        from backend.api_modular.maintenance_tasks.base import (
            ExecutionResult,
            ValidationResult,
        )

        window = self._make_window(task_params='{"deep": true}')
        mock_task = MagicMock()
        mock_task.validate.return_value = ValidationResult(ok=True)
        mock_task.execute.return_value = ExecutionResult(success=True, message="ok")
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_task

        with (
            patch.object(scheduler, "DATABASE_PATH", db_path),
            patch.dict(
                "sys.modules",
                {
                    "api_modular.maintenance_tasks": MagicMock(registry=mock_registry),
                },
            ),
            patch.object(scheduler, "update_next_run"),
        ):
            scheduler.execute_window(window)

        call_params = mock_task.validate.call_args[0][0]
        assert call_params["deep"] is True
        assert "db_path" in call_params


# ===================================================================
# check_announcements tests
# ===================================================================


class TestCheckAnnouncements:
    """Tests for check_announcements()."""

    def test_no_announcements_when_nothing_upcoming(self, scheduler, db_path):
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.check_announcements()

        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM maintenance_notifications"
        ).fetchone()[0]
        conn.close()
        assert count == 0

    def test_announcement_created_for_upcoming_window(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows "
            "(id, name, description, task_type, next_run_at, status, lead_time_hours) "
            "VALUES (1, 'Upcoming', 'desc', 'test', "
            "datetime('now', '+30 minutes'), 'active', 1)"
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.check_announcements()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT notification_type, payload FROM maintenance_notifications"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == "announce"
        payload = json.loads(rows[0][1])
        assert payload["window_id"] == 1
        assert payload["name"] == "Upcoming"

    def test_no_duplicate_announcements(self, scheduler, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO maintenance_windows "
            "(id, name, description, task_type, next_run_at, status, lead_time_hours) "
            "VALUES (1, 'Upcoming', 'desc', 'test', "
            "datetime('now', '+30 minutes'), 'active', 1)"
        )
        conn.commit()
        conn.close()

        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.check_announcements()
            scheduler.check_announcements()

        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM maintenance_notifications "
            "WHERE notification_type = 'announce'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


# ===================================================================
# run_auth_cleanup tests
# ===================================================================


class TestRunAuthCleanup:
    """Tests for run_auth_cleanup()."""

    def test_cleanup_calls_all_repositories(self, scheduler):
        mock_db = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.cleanup_stale.return_value = 2
        mock_reg_repo = MagicMock()
        mock_reg_repo.cleanup_expired.return_value = 1
        mock_rec_repo = MagicMock()
        mock_rec_repo.cleanup_expired.return_value = 0

        with patch.dict(
            "sys.modules",
            {
                "database": MagicMock(AuthDatabase=MagicMock(return_value=mock_db)),
                "models": MagicMock(
                    SessionRepository=MagicMock(return_value=mock_session_repo),
                    PendingRegistrationRepository=MagicMock(return_value=mock_reg_repo),
                    PendingRecoveryRepository=MagicMock(return_value=mock_rec_repo),
                ),
            },
        ):
            scheduler.run_auth_cleanup()

        mock_session_repo.cleanup_stale.assert_called_once_with(grace_minutes=30)
        mock_reg_repo.cleanup_expired.assert_called_once()
        mock_rec_repo.cleanup_expired.assert_called_once()
        mock_db.close.assert_called_once()

    def test_cleanup_logs_when_records_removed(self, scheduler):
        mock_db = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.cleanup_stale.return_value = 5
        mock_reg_repo = MagicMock()
        mock_reg_repo.cleanup_expired.return_value = 3
        mock_rec_repo = MagicMock()
        mock_rec_repo.cleanup_expired.return_value = 1

        with (
            patch.dict(
                "sys.modules",
                {
                    "database": MagicMock(AuthDatabase=MagicMock(return_value=mock_db)),
                    "models": MagicMock(
                        SessionRepository=MagicMock(return_value=mock_session_repo),
                        PendingRegistrationRepository=MagicMock(
                            return_value=mock_reg_repo
                        ),
                        PendingRecoveryRepository=MagicMock(return_value=mock_rec_repo),
                    ),
                },
            ),
            patch.object(scheduler.logger, "info") as mock_log,
        ):
            scheduler.run_auth_cleanup()

        mock_log.assert_called_once()
        assert "9" in str(mock_log.call_args)

    def test_cleanup_silent_when_no_records_removed(self, scheduler):
        mock_db = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.cleanup_stale.return_value = 0
        mock_reg_repo = MagicMock()
        mock_reg_repo.cleanup_expired.return_value = 0
        mock_rec_repo = MagicMock()
        mock_rec_repo.cleanup_expired.return_value = 0

        with (
            patch.dict(
                "sys.modules",
                {
                    "database": MagicMock(AuthDatabase=MagicMock(return_value=mock_db)),
                    "models": MagicMock(
                        SessionRepository=MagicMock(return_value=mock_session_repo),
                        PendingRegistrationRepository=MagicMock(
                            return_value=mock_reg_repo
                        ),
                        PendingRecoveryRepository=MagicMock(return_value=mock_rec_repo),
                    ),
                },
            ),
            patch.object(scheduler.logger, "info") as mock_log,
        ):
            scheduler.run_auth_cleanup()

        mock_log.assert_not_called()

    def test_cleanup_handles_import_error(self, scheduler):
        """If auth modules are unavailable, cleanup logs debug."""
        with (
            patch.dict("sys.modules", {"database": None}),
            patch.object(scheduler.logger, "debug") as mock_debug,
        ):
            scheduler.run_auth_cleanup()

        mock_debug.assert_called_once()

    def test_cleanup_handles_db_connection_error(self, scheduler):
        """If auth DB connection fails, cleanup catches and logs."""
        mock_auth_db_cls = MagicMock(side_effect=Exception("DB connection refused"))

        with (
            patch.dict(
                "sys.modules",
                {
                    "database": MagicMock(AuthDatabase=mock_auth_db_cls),
                    "models": MagicMock(
                        SessionRepository=MagicMock(),
                        PendingRegistrationRepository=MagicMock(),
                        PendingRecoveryRepository=MagicMock(),
                    ),
                },
            ),
            patch.object(scheduler.logger, "debug") as mock_debug,
        ):
            scheduler.run_auth_cleanup()

        mock_debug.assert_called_once()
        assert "DB connection refused" in str(mock_debug.call_args)

    def test_cleanup_handles_repository_exception(self, scheduler):
        """If a repository method raises, the whole cleanup is caught."""
        mock_db = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.cleanup_stale.side_effect = sqlite3.OperationalError(
            "database is locked"
        )

        with (
            patch.dict(
                "sys.modules",
                {
                    "database": MagicMock(AuthDatabase=MagicMock(return_value=mock_db)),
                    "models": MagicMock(
                        SessionRepository=MagicMock(return_value=mock_session_repo),
                        PendingRegistrationRepository=MagicMock(),
                        PendingRecoveryRepository=MagicMock(),
                    ),
                },
            ),
            patch.object(scheduler.logger, "debug") as mock_debug,
        ):
            scheduler.run_auth_cleanup()

        mock_debug.assert_called_once()
        assert "locked" in str(mock_debug.call_args)


# ===================================================================
# Main loop tests
# ===================================================================


class TestMainLoop:
    """Tests for the main() scheduler loop."""

    def test_main_exits_on_shutdown_flag(self, scheduler):
        """main() exits immediately when _shutdown is already set."""
        scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=[]),
            patch.object(scheduler, "time") as mock_time,
        ):
            scheduler.main()

        mock_time.sleep.assert_not_called()
        scheduler._shutdown = False

    def test_main_runs_auth_cleanup_each_cycle(self, scheduler):
        """run_auth_cleanup is called each poll cycle."""
        call_count = 0

        def stop_after_one(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup", side_effect=stop_after_one),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=[]),
            patch.object(scheduler, "time"),
        ):
            scheduler.main()

        assert call_count == 1
        scheduler._shutdown = False

    def test_main_runs_check_announcements(self, scheduler):
        called = False

        def mark_called():
            nonlocal called
            called = True
            scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements", side_effect=mark_called),
            patch.object(scheduler, "find_due_windows", return_value=[]),
            patch.object(scheduler, "time"),
        ):
            scheduler.main()

        assert called
        scheduler._shutdown = False

    def test_main_executes_due_windows(self, scheduler):
        """Windows returned by find_due_windows are executed."""
        windows = [{"id": 1, "name": "W1"}, {"id": 2, "name": "W2"}]
        executed = []

        def track_execution(w):
            executed.append(w["id"])

        find_call_count = 0

        def find_then_empty(*args, **kwargs):
            nonlocal find_call_count
            find_call_count += 1
            return windows if find_call_count == 1 else []

        def stop_on_sleep(n):
            scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", side_effect=find_then_empty),
            patch.object(scheduler, "execute_window", side_effect=track_execution),
            patch.object(scheduler, "time", **{"sleep.side_effect": stop_on_sleep}),
            patch("builtins.open", mock_open()),
            patch.object(scheduler, "fcntl"),
        ):
            scheduler.main()

        assert executed == [1, 2]
        scheduler._shutdown = False

    def test_main_skips_window_when_lock_held(self, scheduler):
        """When flock raises BlockingIOError, the window is skipped."""
        windows = [{"id": 1, "name": "W1"}]

        def stop_on_sleep(n):
            scheduler._shutdown = True

        mock_fcntl = MagicMock()
        mock_fcntl.flock.side_effect = BlockingIOError("locked")
        mock_fcntl.LOCK_EX = 2
        mock_fcntl.LOCK_NB = 4
        mock_fcntl.LOCK_UN = 8

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=windows),
            patch.object(scheduler, "execute_window") as mock_exec,
            patch.object(scheduler, "time", **{"sleep.side_effect": stop_on_sleep}),
            patch("builtins.open", mock_open()),
            patch.object(scheduler, "fcntl", mock_fcntl),
        ):
            scheduler.main()

        mock_exec.assert_not_called()
        scheduler._shutdown = False

    def test_main_handles_loop_exception(self, scheduler):
        """Exception in the loop body is caught and logged."""
        call_count = 0

        def fail_then_stop():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DB gone")
            scheduler._shutdown = True

        with (
            patch.object(
                scheduler, "run_auth_cleanup", side_effect=lambda: fail_then_stop()
            ),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=[]),
            patch.object(scheduler, "time"),
            patch.object(scheduler.logger, "error") as mock_log,
        ):
            scheduler.main()

        assert call_count == 2
        mock_log.assert_called()
        scheduler._shutdown = False

    def test_main_sleep_loop_responds_to_shutdown(self, scheduler):
        """The 1-second sleep loop breaks early on _shutdown."""
        sleep_count = 0

        def counting_sleep(n):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                scheduler._shutdown = True

        first_call = True

        def one_cycle():
            nonlocal first_call
            if not first_call:
                scheduler._shutdown = True
            first_call = False

        with (
            patch.object(scheduler, "run_auth_cleanup", side_effect=one_cycle),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=[]),
            patch.object(scheduler, "time", **{"sleep.side_effect": counting_sleep}),
        ):
            scheduler.main()

        assert sleep_count < scheduler.POLL_INTERVAL
        scheduler._shutdown = False

    def test_main_creates_lock_directory(self, scheduler, tmp_path):
        lock_dir = tmp_path / "subdir" / "nested"
        scheduler.LOCK_PATH = str(lock_dir / "maintenance.lock")
        scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=[]),
            patch.object(scheduler, "time"),
        ):
            scheduler.main()

        assert lock_dir.exists()
        scheduler._shutdown = False

    def test_main_breaks_window_loop_on_shutdown(self, scheduler):
        """If _shutdown is set mid-window-loop, remaining windows skip."""
        windows = [{"id": 1, "name": "W1"}, {"id": 2, "name": "W2"}]
        executed = []

        def execute_and_shutdown(w):
            executed.append(w["id"])
            scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=windows),
            patch.object(scheduler, "execute_window", side_effect=execute_and_shutdown),
            patch.object(scheduler, "time"),
            patch("builtins.open", mock_open()),
            patch.object(scheduler, "fcntl"),
        ):
            scheduler.main()

        assert executed == [1]
        scheduler._shutdown = False

    def test_lock_fd_closed_even_on_execute_error(self, scheduler):
        """Lock file descriptor is always cleaned up via finally."""
        windows = [{"id": 1, "name": "W1"}]
        mock_fd = MagicMock()

        def stop_on_sleep(n):
            scheduler._shutdown = True

        with (
            patch.object(scheduler, "run_auth_cleanup"),
            patch.object(scheduler, "check_announcements"),
            patch.object(scheduler, "find_due_windows", return_value=windows),
            patch.object(scheduler, "execute_window", side_effect=RuntimeError("boom")),
            patch.object(scheduler, "time", **{"sleep.side_effect": stop_on_sleep}),
            patch("builtins.open", return_value=mock_fd),
            patch.object(scheduler, "fcntl"),
        ):
            scheduler.main()

        mock_fd.close.assert_called()
        scheduler._shutdown = False
