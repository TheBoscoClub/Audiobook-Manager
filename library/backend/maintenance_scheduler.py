#!/usr/bin/env python3
"""
Maintenance Scheduler Daemon

Standalone process that:
1. Polls maintenance_windows for due tasks every 60 seconds
2. Executes tasks via the registry
3. Records results in maintenance_history
4. Writes to maintenance_notifications for WebSocket delivery
5. Computes next_run_at for recurring windows

Runs as audiobook-scheduler.service under audiobook.target.
"""
import fcntl
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATABASE_PATH

logging.basicConfig(
    level=logging.INFO,
    format="[SCHEDULER] %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("maintenance_scheduler")

POLL_INTERVAL = 60  # seconds
# AUDIOBOOKS_RUN_DIR is set by EnvironmentFile=/etc/audiobooks/audiobooks.conf,
# which systemd loads before ExecStart. Must always be set via that file.
_run_dir = os.environ.get("AUDIOBOOKS_RUN_DIR")
if not _run_dir:
    # Fallback for dev/testing: read from audiobooks.conf if sourced manually
    raise RuntimeError(
        "AUDIOBOOKS_RUN_DIR is not set. "
        "Run under systemd (EnvironmentFile loads audiobooks.conf) "
        "or export AUDIOBOOKS_RUN_DIR before running manually."
    )
LOCK_PATH = os.environ.get("MAINTENANCE_LOCK", str(Path(_run_dir) / "maintenance.lock"))

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("SIGTERM received, finishing current task then exiting...")
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def get_db():
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def find_due_windows():
    """Find windows that are due for execution."""
    conn = get_db()
    try:
        return [
            dict(r) for r in conn.execute(
                """SELECT * FROM maintenance_windows
                   WHERE next_run_at <= datetime('now')
                     AND status = 'active'
                   ORDER BY next_run_at ASC"""
            ).fetchall()
        ]
    finally:
        conn.close()


def record_history(window_id, started_at, status, message, data=None):
    """Write execution result to history table."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO maintenance_history
               (window_id, started_at, completed_at, status, result_message, result_data)
               VALUES (?, ?, datetime('now'), ?, ?, ?)""",
            (window_id, started_at, status, message, json.dumps(data or {})),
        )
        conn.commit()
    finally:
        conn.close()


def write_notification(ntype, payload):
    """Write to notification queue for WebSocket delivery."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO maintenance_notifications (notification_type, payload) VALUES (?, ?)",
            (ntype, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def update_next_run(window):
    """Compute and update next_run_at for recurring windows."""
    if window["schedule_type"] != "recurring" or not window.get("cron_expression"):
        # One-time window: mark completed
        conn = get_db()
        try:
            conn.execute(
                "UPDATE maintenance_windows SET status = 'completed' WHERE id = ?",
                (window["id"],),
            )
            conn.commit()
        finally:
            conn.close()
        return

    try:
        from croniter import croniter
        cron = croniter(window["cron_expression"], datetime.now(timezone.utc))
        next_at = cron.get_next(datetime).isoformat() + "Z"
        conn = get_db()
        try:
            conn.execute(
                "UPDATE maintenance_windows SET next_run_at = ? WHERE id = ?",
                (next_at, window["id"]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.error("Failed to compute next_run_at for window %d: %s", window["id"], e)


def execute_window(window):
    """Execute a single maintenance window's task."""
    logger.info("Executing window %d: %s (%s)", window["id"], window["name"], window["task_type"])

    # Import registry inside function to use Flask app context if available
    try:
        from api_modular.maintenance_tasks import registry
    except ImportError:
        # Try relative import path
        sys.path.insert(0, str(Path(__file__).parent))
        from api_modular.maintenance_tasks import registry

    task = registry.get(window["task_type"])
    if task is None:
        msg = f"Unknown task type: {window['task_type']}"
        logger.error(msg)
        started_at = datetime.now(timezone.utc).isoformat() + "Z"
        record_history(window["id"], started_at, "failure", msg)
        write_notification("update", {
            "window_id": window["id"], "status": "failure", "message": msg,
        })
        return

    params = json.loads(window.get("task_params", "{}"))
    # Inject db_path so handlers work outside Flask app context
    params.setdefault("db_path", str(DATABASE_PATH))

    # Validate
    validation = task.validate(params)
    if not validation.ok:
        msg = f"Validation failed: {validation.message}"
        logger.warning(msg)
        started_at = datetime.now(timezone.utc).isoformat() + "Z"
        record_history(window["id"], started_at, "failure", msg)
        write_notification("update", {
            "window_id": window["id"], "status": "failure", "message": msg,
        })
        return

    # Execute
    started_at = datetime.now(timezone.utc).isoformat() + "Z"
    write_notification("update", {
        "window_id": window["id"], "status": "running",
        "message": f"Executing: {window['name']}",
    })

    result = task.execute(params, progress_callback=lambda p, m: None)

    status = "success" if result.success else "failure"
    record_history(window["id"], started_at, status, result.message, result.data)
    write_notification("update", {
        "window_id": window["id"], "status": status, "message": result.message,
    })

    # Update next_run_at or mark completed
    update_next_run(window)

    logger.info("Window %d completed: %s - %s", window["id"], status, result.message)


def check_announcements():
    """Write announcement notifications for windows within lead time."""
    conn = get_db()
    try:
        upcoming = conn.execute(
            """SELECT id, name, description, next_run_at, lead_time_hours
               FROM maintenance_windows
               WHERE status = 'active'
                 AND next_run_at IS NOT NULL
                 AND datetime(next_run_at, '-' || lead_time_hours || ' hours')
                     <= datetime('now')
                 AND next_run_at > datetime('now')"""
        ).fetchall()

        for window in upcoming:
            # Only announce if not already announced recently (within last poll interval)
            existing = conn.execute(
                """SELECT COUNT(*) FROM maintenance_notifications
                   WHERE notification_type = 'announce'
                     AND json_extract(payload, '$.window_id') = ?
                     AND created_at >= datetime('now', '-2 minutes')""",
                (window["id"],),
            ).fetchone()[0]

            if existing == 0:
                write_notification("announce", {
                    "window_id": window["id"],
                    "name": window["name"],
                    "description": window["description"],
                    "next_run_at": window["next_run_at"],
                })
    finally:
        conn.close()


def main():
    """Main scheduler loop."""
    logger.info("Maintenance scheduler starting (poll every %ds)", POLL_INTERVAL)
    logger.info("Lock file: %s", LOCK_PATH)
    logger.info("Database: %s", DATABASE_PATH)

    # Ensure lock directory exists
    Path(LOCK_PATH).parent.mkdir(parents=True, exist_ok=True)

    while not _shutdown:
        try:
            # Check for announcements (windows within lead time)
            check_announcements()

            # Find and execute due windows
            due_windows = find_due_windows()
            for window in due_windows:
                if _shutdown:
                    break

                # Acquire file lock for execution
                lock_fd = open(LOCK_PATH, "w")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    execute_window(window)
                except BlockingIOError:
                    logger.info("Lock held by another process, skipping window %d", window["id"])
                finally:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        lock_fd.close()
                    except Exception:
                        pass

        except Exception as e:
            logger.error("Scheduler loop error: %s", e, exc_info=True)

        # Sleep in 1-second increments to respond to SIGTERM quickly
        for _ in range(POLL_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Scheduler shutting down")


if __name__ == "__main__":
    main()
