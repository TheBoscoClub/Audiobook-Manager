# library/tests/test_per_user_models.py
"""Tests for per-user state data models."""

import os
import sqlite3
from datetime import datetime
from unittest.mock import MagicMock

import pytest


class TestListeningHistoryModel:
    """Tests for UserListeningHistory dataclass and repository."""

    @pytest.fixture
    def db(self):
        """In-memory auth DB with schema."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        schema_path = os.path.join(os.path.dirname(__file__), "..", "auth", "schema.sql")
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential)"
            " VALUES (1, 'testuser1', 'totp', X'00')"
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mock_auth_db(self, db):
        """Mock AuthDatabase that returns our test connection."""
        mock = MagicMock()
        mock.connection.return_value.__enter__ = lambda s: db
        mock.connection.return_value.__exit__ = lambda s, *a: None
        return mock

    def test_create_listening_session(self, mock_auth_db):
        from auth.models import ListeningHistoryRepository, UserListeningHistory

        ListeningHistoryRepository(mock_auth_db)  # verify importable
        session = UserListeningHistory(user_id=1, audiobook_id="100", position_start_ms=5000)
        saved = session.save(mock_auth_db)
        assert saved.id is not None
        assert saved.started_at is not None

    def test_close_listening_session(self, mock_auth_db):
        from auth.models import ListeningHistoryRepository, UserListeningHistory

        repo = ListeningHistoryRepository(mock_auth_db)
        session = UserListeningHistory(user_id=1, audiobook_id="100", position_start_ms=5000)
        saved = session.save(mock_auth_db)

        saved.position_end_ms = 120000
        saved.ended_at = datetime.now()
        saved.duration_listened_ms = 115000
        saved.save(mock_auth_db)

        fetched = repo.get_for_user(1, limit=1)
        assert len(fetched) == 1
        assert fetched[0].position_end_ms == 120000
        assert fetched[0].duration_listened_ms == 115000

    def test_get_for_user_paginated(self, mock_auth_db):
        from auth.models import ListeningHistoryRepository, UserListeningHistory

        repo = ListeningHistoryRepository(mock_auth_db)
        for i in range(5):
            session = UserListeningHistory(
                user_id=1, audiobook_id=str(100 + i), position_start_ms=0
            )
            session.save(mock_auth_db)

        page1 = repo.get_for_user(1, limit=3, offset=0)
        page2 = repo.get_for_user(1, limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2

    def test_get_user_books(self, mock_auth_db):
        """Get distinct audiobook IDs user has interacted with."""
        from auth.models import ListeningHistoryRepository, UserListeningHistory

        repo = ListeningHistoryRepository(mock_auth_db)
        for book_id in ["100", "100", "200"]:
            session = UserListeningHistory(user_id=1, audiobook_id=book_id, position_start_ms=0)
            session.save(mock_auth_db)

        books = repo.get_user_book_ids(1)
        assert set(books) == {"100", "200"}

    def test_brief_session_filter(self, mock_auth_db):
        """Sessions < 5 seconds should be filterable."""
        from auth.models import ListeningHistoryRepository, UserListeningHistory

        repo = ListeningHistoryRepository(mock_auth_db)
        brief = UserListeningHistory(
            user_id=1, audiobook_id="100", position_start_ms=0, duration_listened_ms=3000
        )
        brief.ended_at = datetime.now()
        brief.save(mock_auth_db)

        real = UserListeningHistory(
            user_id=1, audiobook_id="200", position_start_ms=0, duration_listened_ms=120000
        )
        real.ended_at = datetime.now()
        real.save(mock_auth_db)

        sessions = repo.get_for_user(1, min_duration_ms=5000)
        assert len(sessions) == 1
        assert sessions[0].audiobook_id == "200"

    def test_get_open_session(self, mock_auth_db):
        from auth.models import ListeningHistoryRepository, UserListeningHistory

        repo = ListeningHistoryRepository(mock_auth_db)
        # Create an open session
        session = UserListeningHistory(user_id=1, audiobook_id="100", position_start_ms=0)
        session.save(mock_auth_db)

        # Should find it
        open_session = repo.get_open_session(1, "100")
        assert open_session is not None
        assert open_session.ended_at is None

        # Close it
        open_session.ended_at = datetime.now()
        open_session.duration_listened_ms = 60000
        open_session.save(mock_auth_db)

        # Should not find open session anymore
        assert repo.get_open_session(1, "100") is None


class TestDownloadModel:
    """Tests for UserDownload dataclass and repository."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        schema_path = os.path.join(os.path.dirname(__file__), "..", "auth", "schema.sql")
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential)"
            " VALUES (1, 'testuser1', 'totp', X'00')"
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mock_auth_db(self, db):
        mock = MagicMock()
        mock.connection.return_value.__enter__ = lambda s: db
        mock.connection.return_value.__exit__ = lambda s, *a: None
        return mock

    def test_record_download(self, mock_auth_db):
        from auth.models import DownloadRepository, UserDownload

        DownloadRepository(mock_auth_db)  # verify importable
        dl = UserDownload(user_id=1, audiobook_id="100", file_format="opus")
        saved = dl.save(mock_auth_db)
        assert saved.id is not None
        assert saved.downloaded_at is not None

    def test_get_user_downloads(self, mock_auth_db):
        from auth.models import DownloadRepository, UserDownload

        repo = DownloadRepository(mock_auth_db)
        UserDownload(user_id=1, audiobook_id="100", file_format="opus").save(mock_auth_db)
        UserDownload(user_id=1, audiobook_id="200", file_format="opus").save(mock_auth_db)

        downloads = repo.get_for_user(1)
        assert len(downloads) == 2

    def test_get_download_count_for_book(self, mock_auth_db):
        from auth.models import DownloadRepository, UserDownload

        repo = DownloadRepository(mock_auth_db)
        UserDownload(user_id=1, audiobook_id="100", file_format="opus").save(mock_auth_db)

        assert repo.has_downloaded(1, "100") is True
        assert repo.has_downloaded(1, "999") is False


class TestUserPreferencesModel:
    """Tests for UserPreferences dataclass and repository."""

    @pytest.fixture
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        schema_path = os.path.join(os.path.dirname(__file__), "..", "auth", "schema.sql")
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.execute(
            "INSERT INTO users (id, username, auth_type, auth_credential)"
            " VALUES (1, 'testuser1', 'totp', X'00')"
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def mock_auth_db(self, db):
        mock = MagicMock()
        mock.connection.return_value.__enter__ = lambda s: db
        mock.connection.return_value.__exit__ = lambda s, *a: None
        return mock

    def test_create_preferences(self, mock_auth_db):
        from auth.models import PreferencesRepository

        repo = PreferencesRepository(mock_auth_db)
        prefs = repo.get_or_create(1)
        assert prefs.user_id == 1
        assert prefs.new_books_seen_at is None

    def test_update_new_books_seen(self, mock_auth_db):
        from auth.models import PreferencesRepository

        repo = PreferencesRepository(mock_auth_db)
        prefs = repo.get_or_create(1)
        now = datetime.now()
        prefs.new_books_seen_at = now
        prefs.save(mock_auth_db)

        fetched = repo.get_or_create(1)
        assert fetched.new_books_seen_at is not None

    def test_idempotent_get_or_create(self, mock_auth_db):
        from auth.models import PreferencesRepository

        repo = PreferencesRepository(mock_auth_db)
        prefs1 = repo.get_or_create(1)
        prefs2 = repo.get_or_create(1)
        assert prefs1.user_id == prefs2.user_id
