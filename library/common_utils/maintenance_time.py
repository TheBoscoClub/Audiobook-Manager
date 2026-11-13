"""Reading and writing maintenance-window times.

next_run_at / scheduled_at are TEXT columns and three forms have been
written to them over time:

    2031-01-25T09:00:00.000Z       the browser's toISOString()
    2031-01-25T09:00:00+00:00Z     an aware isoformat() with a "Z" appended
    2031-01-25 09:00:00            SQLite's datetime()

SQLite's datetime() cannot parse the second form, and comparing the first
with SQLite's "now" as text is wrong on the window's own day. So times are
compared in Python, and everything written from here on uses one form: the
browser's, which SQLite also reads (bd Audiobook-Manager-bd38.3).
"""

from datetime import datetime, timezone
from typing import Optional

STORAGE_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"


def parse_utc(text) -> Optional[datetime]:
    """Any stored form → an aware UTC datetime; None when it cannot be read.

    A time with no zone is UTC (that is what SQLite's datetime('now') means).
    """
    if not isinstance(text, str):
        return None
    value = text.strip()
    if not value:
        return None
    if value.endswith(("Z", "z")):
        value = value[:-1]
        # "…+00:00Z": the offset is already there; a bare "…Z" means UTC.
        has_offset = "+" in value[10:] or "-" in value[10:]
        if not has_offset:
            value += "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_storage(moment: datetime) -> str:
    """An aware datetime → the one form written to the database."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(STORAGE_FORMAT)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
