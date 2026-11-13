"""Maintenance window times are read the same whatever wrote them (bd Audiobook-Manager-bd38.3).

Three forms of next_run_at exist in the wild:

- the browser's toISOString():           2031-01-25T09:00:00.000Z
- the scheduler's / API's cron result:   2031-01-25T09:00:00+00:00Z   (aware isoformat + "Z")
- SQLite's own datetime():               2031-01-25 09:00:00

SQLite's datetime() cannot parse the second, and plain text comparison gets
the first wrong, so a window was run late or never. Times are now parsed in
Python; writers emit the browser's form.
"""

import importlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

LIBRARY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRARY_DIR))
sys.path.insert(0, str(LIBRARY_DIR / "backend"))

from common_utils.maintenance_time import parse_utc, to_storage  # noqa: E402

FORMS = {
    "browser": "%Y-%m-%dT%H:%M:%S.000Z",
    "cron": "%Y-%m-%dT%H:%M:%S+00:00Z",
    "sqlite": "%Y-%m-%d %H:%M:%S",
}


def _stamp(form, **delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime(FORMS[form])


# ------------------------------------------------------------------ parsing


class TestParseUtc:
    @pytest.mark.parametrize(
        "text",
        [
            "2031-01-25T09:00:00.000Z",
            "2031-01-25T09:00:00+00:00Z",
            "2031-01-25 09:00:00",
            "2031-01-25T09:00:00+00:00",
            "2031-01-25T03:00:00-06:00",
            "  2031-01-25T09:00:00Z  ",
        ],
    )
    def test_every_stored_form_is_the_same_moment(self, text):
        assert parse_utc(text) == datetime(2031, 1, 25, 9, 0, 0, tzinfo=timezone.utc)

    def test_fractional_seconds_before_an_offset_and_z_are_kept(self):
        parsed = parse_utc("2031-01-25T09:00:00.123456+00:00Z")
        assert parsed == datetime(2031, 1, 25, 9, 0, 0, 123456, tzinfo=timezone.utc)

    @pytest.mark.parametrize("text", [None, "", "tomorrow", "2031-13-45T99:00:00Z", 12345])
    def test_unreadable_is_none(self, text):
        assert parse_utc(text) is None

    def test_storage_form_round_trips_and_sqlite_can_read_it(self):
        moment = datetime(2031, 1, 25, 3, 0, 0, tzinfo=timezone(timedelta(hours=-6)))
        stored = to_storage(moment)
        assert stored == "2031-01-25T09:00:00.000Z"
        assert parse_utc(stored) == moment
        got = sqlite3.connect(":memory:").execute("select datetime(?)", (stored,)).fetchone()[0]
        assert got == "2031-01-25 09:00:00"


# ---------------------------------------------------------------- scheduler


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIOBOOKS_RUN_DIR", str(tmp_path))
    sys.modules.pop("backend.maintenance_scheduler", None)
    import backend

    if hasattr(backend, "maintenance_scheduler"):
        delattr(backend, "maintenance_scheduler")
    return importlib.import_module("backend.maintenance_scheduler")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript((LIBRARY_DIR / "backend" / "schema.sql").read_text())
    conn.close()
    return path


def _add_window(db_path, name, next_run_at, **cols):
    values = {
        "name": name,
        "task_type": "host_downtime",
        "schedule_type": "once",
        "next_run_at": next_run_at,
        "status": "active",
        "lead_time_hours": 1,
    }
    values.update(cols)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"INSERT INTO maintenance_windows ({', '.join(values)}) "  # noqa: S608  # nosec B608 — column names are this test's own literals
        f"VALUES ({', '.join('?' for _ in values)})",
        list(values.values()),
    )
    conn.commit()
    conn.close()


class TestSchedulerDueWindows:
    @pytest.mark.parametrize("form", sorted(FORMS))
    def test_a_window_just_past_its_time_is_due(self, scheduler, db_path, form):
        _add_window(db_path, form, _stamp(form, seconds=-2))
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            assert [w["name"] for w in scheduler.find_due_windows()] == [form]

    @pytest.mark.parametrize("form", sorted(FORMS))
    def test_a_window_still_ahead_is_not_due(self, scheduler, db_path, form):
        _add_window(db_path, form, _stamp(form, minutes=5))
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            assert scheduler.find_due_windows() == []

    def test_due_windows_come_oldest_first_across_forms(self, scheduler, db_path):
        _add_window(db_path, "second", _stamp("browser", minutes=-5))
        _add_window(db_path, "first", _stamp("cron", minutes=-50))
        _add_window(db_path, "third", _stamp("sqlite", minutes=-1))
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            assert [w["name"] for w in scheduler.find_due_windows()] == ["first", "second", "third"]

    def test_an_unreadable_time_is_never_due_and_harms_nothing_else(self, scheduler, db_path):
        _add_window(db_path, "garbage", "tomorrow-ish")
        _add_window(db_path, "empty", None)
        _add_window(db_path, "fine", _stamp("browser", seconds=-2))
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            assert [w["name"] for w in scheduler.find_due_windows()] == ["fine"]

    def test_inactive_windows_are_not_due(self, scheduler, db_path):
        _add_window(db_path, "done", _stamp("cron", seconds=-2), status="completed")
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            assert scheduler.find_due_windows() == []


class TestSchedulerAnnouncements:
    def _announced(self, db_path):
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT json_extract(payload, '$.name') FROM maintenance_notifications "
            "WHERE notification_type = 'announce'"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    @pytest.mark.parametrize("form", sorted(FORMS))
    def test_a_window_inside_its_lead_time_is_announced(self, scheduler, db_path, form):
        _add_window(db_path, form, _stamp(form, minutes=30), lead_time_hours=1)
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.check_announcements()
        assert self._announced(db_path) == [form]

    @pytest.mark.parametrize("form", sorted(FORMS))
    def test_outside_the_lead_time_or_already_past_is_not(self, scheduler, db_path, form):
        _add_window(db_path, "far", _stamp(form, hours=5), lead_time_hours=1)
        _add_window(db_path, "past", _stamp(form, minutes=-5), lead_time_hours=1)
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            scheduler.check_announcements()
        assert self._announced(db_path) == []

    def test_next_run_of_a_recurring_window_is_written_in_a_readable_form(self, scheduler, db_path):
        _add_window(
            db_path,
            "nightly",
            _stamp("cron", seconds=-2),
            schedule_type="recurring",
            cron_expression="0 3 * * *",
        )
        with patch.object(scheduler, "DATABASE_PATH", db_path):
            window = scheduler.find_due_windows()[0]
            scheduler.update_next_run(window)
        conn = sqlite3.connect(str(db_path))
        stored, readable = conn.execute(
            "SELECT next_run_at, datetime(next_run_at) FROM maintenance_windows"
        ).fetchone()
        conn.close()
        assert readable is not None, stored
        next_run = parse_utc(stored)
        assert next_run is not None and next_run > datetime.now(timezone.utc)
        assert stored.endswith(".000Z")


# ---------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path, db_path):
    from api_modular import create_app

    app = create_app(
        database_path=db_path, project_dir=tmp_path, supplements_dir=tmp_path / "supplements"
    )
    return app.test_client()


class TestApiAnnouncements:
    @pytest.mark.parametrize("form", sorted(FORMS))
    def test_a_window_inside_its_lead_time_is_listed(self, client, db_path, form):
        _add_window(db_path, form, _stamp(form, minutes=30), lead_time_hours=1)
        _add_window(db_path, "far", _stamp(form, hours=5), lead_time_hours=1)
        windows = client.get("/api/maintenance/announcements").get_json()["windows"]
        assert [w["name"] for w in windows] == [form]

    def test_a_recurring_window_made_through_the_api_is_stored_readably(self, client, db_path):
        resp = client.post(
            "/api/admin/maintenance/windows",
            json={
                "name": "nightly vacuum",
                "task_type": "db_vacuum",
                "schedule_type": "recurring",
                "cron_expression": "0 3 * * *",
            },
        )
        assert resp.status_code == 201
        stored = resp.get_json()["next_run_at"]
        assert stored.endswith(".000Z"), stored
        next_run = parse_utc(stored)
        assert next_run is not None and next_run > datetime.now(timezone.utc)
        conn = sqlite3.connect(str(db_path))
        assert conn.execute("select datetime(?)", (stored,)).fetchone()[0] is not None
        conn.close()
