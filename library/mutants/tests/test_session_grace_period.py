"""
Tests for session staleness grace period.

The Session.DEFAULT_GRACE_MINUTES constant was bumped from 30 to 120 in
v8.3.10.5 to avoid sweeping out sessions of users mid-audio-listen — audio
streams (/streaming-audio/* and /audio/*) bypass /api/* so they never
refresh the session's last_seen, and a 30-min listening session would 401
the position-save PUT mid-chapter.

These tests pin the new default in place so a careless change does not
silently revert the fix.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

LIBRARY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(LIBRARY_DIR))

from auth.database import AuthDatabase  # noqa: E402
from auth.models import Session  # noqa: E402


@pytest.fixture
def temp_db():
    """Create a temporary encrypted database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test-auth.db")
        key_path = os.path.join(tmpdir, "test.key")
        db = AuthDatabase(db_path=db_path, key_path=key_path, is_dev=True)
        db.initialize()
        yield db


class TestDefaultGraceMinutes:
    """Pin the canonical Session.DEFAULT_GRACE_MINUTES value."""

    def test_default_grace_minutes_is_120(self):
        """The grace period MUST be 120 minutes — bumped from 30 in v8.3.10.5.

        Anything below 120 risks 401-ing audio listeners mid-chapter because
        audio streams bypass /api/* and never refresh last_seen.
        """
        assert Session.DEFAULT_GRACE_MINUTES == 120, (
            f"DEFAULT_GRACE_MINUTES should be 120 to avoid 401-ing audio "
            f"listeners mid-chapter; got {Session.DEFAULT_GRACE_MINUTES}"
        )

    def test_is_stale_default_uses_120_minutes(self):
        """Session.is_stale() with no args uses the 120-minute default."""
        # last_seen was 119 minutes ago — within 120-min grace, so NOT stale.
        s = Session(
            id="x",  # type: ignore[arg-type]
            user_id=1,
            token_hash=b"x" * 32,  # type: ignore[arg-type]
            created_at=datetime.now() - timedelta(hours=2),
            last_seen=datetime.now() - timedelta(minutes=119),
            user_agent="pytest",
            ip_address="127.0.0.1",
            is_persistent=False,
            expires_at=None,
        )
        assert s.is_stale() is False, "119 min old session must not be stale under 120-min grace"

    def test_is_stale_default_marks_session_after_121_minutes(self):
        """Session.is_stale() with no args marks 121-min-old session stale."""
        s = Session(
            id="x",  # type: ignore[arg-type]
            user_id=1,
            token_hash=b"x" * 32,  # type: ignore[arg-type]
            created_at=datetime.now() - timedelta(hours=3),
            last_seen=datetime.now() - timedelta(minutes=121),
            user_agent="pytest",
            ip_address="127.0.0.1",
            is_persistent=False,
            expires_at=None,
        )
        assert s.is_stale() is True, "121 min old session must be stale under 120-min grace"

    def test_is_stale_explicit_grace_minutes_still_works(self):
        """Explicit grace_minutes= override still works (e.g., for tests)."""
        s = Session(
            id="x",  # type: ignore[arg-type]
            user_id=1,
            token_hash=b"x" * 32,  # type: ignore[arg-type]
            created_at=datetime.now() - timedelta(hours=2),
            last_seen=datetime.now() - timedelta(minutes=45),
            user_agent="pytest",
            ip_address="127.0.0.1",
            is_persistent=False,
            expires_at=None,
        )
        # 45 min old — stale at 30, fresh at 60 and at default 120.
        assert s.is_stale(grace_minutes=30) is True
        assert s.is_stale(grace_minutes=60) is False
        assert s.is_stale() is False

    def test_persistent_session_never_stale_under_default(self):
        """Persistent sessions ignore grace period (signed-out only)."""
        s = Session(
            id="x",  # type: ignore[arg-type]
            user_id=1,
            token_hash=b"x" * 32,  # type: ignore[arg-type]
            created_at=datetime.now() - timedelta(days=30),
            last_seen=datetime.now() - timedelta(days=15),
            user_agent="pytest",
            ip_address="127.0.0.1",
            is_persistent=True,
            expires_at=None,
        )
        assert s.is_stale() is False
