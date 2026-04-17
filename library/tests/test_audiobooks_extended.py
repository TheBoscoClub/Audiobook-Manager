"""
Extended tests for audiobooks.py targeting uncovered lines.

Uncovered lines and what they correspond to:
  56: _get_audiobooks_db() when DATABASE_PATH is None → RuntimeError
  124-125: get_stats() OSError when checking database file size
  225: get_audiobooks() edition sort pass (no-op)
  338: genres_map.setdefault inside batch genre fetch
  351: eras_map.setdefault inside batch era fetch
  364: topics_map.setdefault inside batch topic fetch
  399-401: authors_map exception handler (table not exist)
  425-427: narrators_map exception handler (table not exist)
  462: edition_count > 1 branch
  470-471: edition sort filter (books with edition_count > 1)
  606-641: get_audiobook() single book detail (genres, eras, topics)
  708-716: stream_audiobook() WebM remux failure (returncode != 0)
  718-726: stream_audiobook() WebM remux exception (timeout/OS error)
  771: download_audiobook() not found
  778-803: download_audiobook() successful download with filename sanitization
  819: health() reading VERSION file
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _init_test_database(db_path):
    """Initialize a test database with the full schema."""
    schema_path = Path(__file__).parent.parent / "backend" / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    with open(schema_path) as f:
        conn.executescript(f.read())
    conn.close()


def _insert_test_data(db_path):
    """Insert comprehensive test data for audiobooks tests.

    Inserts books with genres, eras, topics, supplements, and
    edition variants to exercise all batch-fetch paths.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        -- Book 1: full metadata
        INSERT INTO audiobooks
            (id, title, author, narrator, publisher, series, series_sequence,
             duration_hours, duration_formatted, file_size_mb,
             file_path, cover_path, format, quality, description,
             content_type, author_last_name, author_first_name,
             narrator_last_name, narrator_first_name, edition, asin,
             published_year, published_date, acquired_date)
        VALUES (100, 'Dune', 'Frank Herbert', 'Scott Brick', 'Macmillan Audio',
                'Dune Saga', 1, 21.0, '21:00:00', 500.0,
                '/test/dune.opus', '/covers/dune.jpg', 'opus', '128k',
                'A desert planet epic', 'Product',
                'Herbert', 'Frank', 'Brick', 'Scott',
                NULL, 'B00B7KZMO6', 1965, '1965-08-01', '2025-01-15');

        -- Book 2: same author, edition variant of book 1
        INSERT INTO audiobooks
            (id, title, author, narrator, publisher, series, series_sequence,
             duration_hours, file_size_mb, file_path, format,
             content_type, edition)
        VALUES (101, 'Dune (50th Anniversary Edition)', 'Frank Herbert',
                'Scott Brick', 'Macmillan Audio', 'Dune Saga', 1,
                21.5, 510.0, '/test/dune_anniversary.opus', 'opus',
                'Product', '50th Anniversary');

        -- Book 3: m4b format for download test
        INSERT INTO audiobooks
            (id, title, author, narrator, file_path, format,
             content_type, file_size_mb, duration_hours)
        VALUES (102, 'Test: Book/With "Special" Chars', 'Author One',
                'Narrator One', '/test/special.m4b', 'm4b',
                'Product', 100.0, 5.0);

        -- Book 4: no author (for download without author)
        INSERT INTO audiobooks
            (id, title, author, narrator, file_path, format,
             content_type, file_size_mb, duration_hours)
        VALUES (103, 'Orphan Book', NULL, NULL,
                '/test/orphan.mp3', 'mp3', 'Product', 50.0, 3.0);

        -- Book 5: podcast (non-audiobook content)
        INSERT INTO audiobooks
            (id, title, author, file_path, format, content_type,
             file_size_mb, duration_hours)
        VALUES (104, 'My Podcast Episode', 'Podcast Host',
                '/test/podcast.opus', 'opus', 'Podcast', 30.0, 1.0);

        -- Book 6: series book for series sort test
        INSERT INTO audiobooks
            (id, title, author, series, series_sequence, file_path, format,
             content_type, file_size_mb, duration_hours)
        VALUES (105, 'Dune Messiah', 'Frank Herbert', 'Dune Saga', 2,
                '/test/dune_messiah.opus', 'opus', 'Product', 400.0, 12.0);

        -- Genres
        INSERT OR IGNORE INTO genres (id, name) VALUES (100, 'Science Fiction');
        INSERT OR IGNORE INTO genres (id, name) VALUES (101, 'Epic');
        INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (100, 100);
        INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (100, 101);
        INSERT INTO audiobook_genres (audiobook_id, genre_id) VALUES (101, 100);

        -- Eras
        INSERT OR IGNORE INTO eras (id, name) VALUES (100, 'Cold War');
        INSERT INTO audiobook_eras (audiobook_id, era_id) VALUES (100, 100);

        -- Topics
        INSERT OR IGNORE INTO topics (id, name) VALUES (100, 'Desert Planet');
        INSERT INTO audiobook_topics (audiobook_id, topic_id) VALUES (100, 100);

        -- Supplements
        INSERT INTO supplements (audiobook_id, type, filename, file_path)
        VALUES (100, 'pdf', 'map.pdf', '/test/supplements/map.pdf');

        -- Authors (normalized)
        INSERT OR IGNORE INTO authors (id, name, sort_name)
        VALUES (100, 'Frank Herbert', 'Herbert, Frank');
        INSERT OR IGNORE INTO authors (id, name, sort_name)
        VALUES (101, 'Author One', 'One, Author');
        INSERT OR IGNORE INTO book_authors (book_id, author_id, position)
        VALUES (100, 100, 0);
        INSERT OR IGNORE INTO book_authors (book_id, author_id, position)
        VALUES (101, 100, 0);
        INSERT OR IGNORE INTO book_authors (book_id, author_id, position)
        VALUES (102, 101, 0);
        INSERT OR IGNORE INTO book_authors (book_id, author_id, position)
        VALUES (105, 100, 0);

        -- Narrators (normalized)
        INSERT OR IGNORE INTO narrators (id, name, sort_name)
        VALUES (100, 'Scott Brick', 'Brick, Scott');
        INSERT OR IGNORE INTO narrators (id, name, sort_name)
        VALUES (101, 'Narrator One', 'One, Narrator');
        INSERT OR IGNORE INTO book_narrators (book_id, narrator_id, position)
        VALUES (100, 100, 0);
        INSERT OR IGNORE INTO book_narrators (book_id, narrator_id, position)
        VALUES (101, 100, 0);
        INSERT OR IGNORE INTO book_narrators (book_id, narrator_id, position)
        VALUES (102, 101, 0);
    """)
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def audiobooks_app(tmp_path_factory):
    """Create a Flask app with rich test data for audiobooks endpoint tests."""
    from backend.api_modular import create_app

    tmpdir = tmp_path_factory.mktemp("audiobooks_ext")
    test_db = tmpdir / "test_audiobooks.db"
    supplements_dir = tmpdir / "supplements"
    supplements_dir.mkdir(exist_ok=True)

    # Create VERSION file
    version_file = tmpdir / "VERSION"
    version_file.write_text("9.9.9-test\n")

    # Initialize DB with full schema
    _init_test_database(test_db)

    # Insert rich test data
    _insert_test_data(test_db)

    app = create_app(
        database_path=test_db, project_dir=tmpdir, supplements_dir=supplements_dir, api_port=5098
    )
    app.config["TESTING"] = True

    return app


@pytest.fixture
def client(audiobooks_app):
    """Test client for audiobooks endpoint tests."""
    with audiobooks_app.test_client() as c:
        yield c


# ─── _get_audiobooks_db: line 56 ──────────────────────────────────────────


class TestGetAudiobooksDbError:
    def test_database_path_not_configured_raises(self, audiobooks_app):
        """Line 56: RuntimeError when DATABASE_PATH is None."""
        with audiobooks_app.test_request_context():
            # Temporarily remove DATABASE_PATH
            original = audiobooks_app.config.pop("DATABASE_PATH")
            try:
                from backend.api_modular.audiobooks import _get_audiobooks_db

                with pytest.raises(RuntimeError, match="DATABASE_PATH not configured"):
                    _get_audiobooks_db()
            finally:
                audiobooks_app.config["DATABASE_PATH"] = original


# ─── get_stats: lines 124-125 ─────────────────────────────────────────────


class TestGetStatsEdgeCases:
    def test_stats_returns_expected_fields(self, client):
        """Basic stats endpoint smoke test."""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_audiobooks" in data
        assert "total_hours" in data
        assert "total_size_gb" in data
        assert "database_size_mb" in data
        assert "unique_authors" in data
        assert "unique_narrators" in data
        assert "unique_publishers" in data
        assert "unique_genres" in data

    def test_stats_database_size_oserror(self, audiobooks_app):
        """Lines 124-125: OSError when reading database file size."""
        with audiobooks_app.test_client() as c:
            with patch("os.path.getsize", side_effect=OSError("permission denied")):
                resp = c.get("/api/stats")
                assert resp.status_code == 200
                data = resp.get_json()
                # database_size_mb should be 0.0 on error
                assert data["database_size_mb"] == 0.0


# ─── get_audiobooks: lines 225, 338, 351, 364, 399-401, 425-427, 462, 470-471 ──


class TestGetAudiobooksFilters:
    def test_search_filter(self, client):
        """Full-text search filter."""
        resp = client.get("/api/audiobooks?search=Dune")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pagination"]["total_count"] >= 1

    def test_author_filter(self, client):
        """Author filter via normalized book_authors table."""
        resp = client.get("/api/audiobooks?author=Frank Herbert")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pagination"]["total_count"] >= 1

    def test_narrator_filter(self, client):
        """Narrator filter via normalized book_narrators table."""
        resp = client.get("/api/audiobooks?narrator=Scott Brick")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pagination"]["total_count"] >= 1

    def test_publisher_filter(self, client):
        """Publisher LIKE filter."""
        resp = client.get("/api/audiobooks?publisher=Macmillan")
        assert resp.status_code == 200

    def test_format_filter(self, client):
        """Format exact match filter."""
        resp = client.get("/api/audiobooks?format=opus")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pagination"]["total_count"] >= 1

    def test_genre_filter(self, client):
        """Genre filter via audiobook_genres join."""
        resp = client.get("/api/audiobooks?genre=Science Fiction")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pagination"]["total_count"] >= 1

    def test_invalid_sort_field_defaults_to_title(self, client):
        """Unknown sort field falls back to 'title'."""
        resp = client.get("/api/audiobooks?sort=nonexistent")
        assert resp.status_code == 200

    def test_invalid_sort_order_defaults_to_asc(self, client):
        """Unknown sort order falls back to 'asc'."""
        resp = client.get("/api/audiobooks?sort=title&order=invalid")
        assert resp.status_code == 200

    def test_series_sort(self, client):
        """Line 215-222: series sort filters to books with series only."""
        resp = client.get("/api/audiobooks?sort=series")
        assert resp.status_code == 200
        data = resp.get_json()
        for book in data["audiobooks"]:
            assert book["series"] is not None
            assert book["series"] != ""

    def test_edition_sort_pass(self, client):
        """Lines 223-225, 469-471: edition sort filters to multi-edition books."""
        resp = client.get("/api/audiobooks?sort=edition")
        assert resp.status_code == 200
        data = resp.get_json()
        # Only books with edition_count > 1 should appear
        for book in data["audiobooks"]:
            assert book.get("edition_count", 1) > 1

    def test_nullable_sort_fields(self, client):
        """Lines 205-211: nullable column sort pushes NULLs to end."""
        for field in [
            "author_last",
            "author_first",
            "narrator_last",
            "narrator_first",
            "acquired_date",
            "published_date",
        ]:
            resp = client.get(f"/api/audiobooks?sort={field}")
            assert resp.status_code == 200

    def test_pagination_params(self, client):
        """Pagination with page and per_page."""
        resp = client.get("/api/audiobooks?page=1&per_page=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["audiobooks"]) <= 2
        assert data["pagination"]["per_page"] == 2


class TestGetAudiobooksBatchFetch:
    """Tests for batch genre/era/topic/supplement/author/narrator fetch."""

    def test_genres_map_populated(self, client):
        """Line 338: genres_map.setdefault populates book genres."""
        resp = client.get("/api/audiobooks?search=Dune")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert "Science Fiction" in dune["genres"]

    def test_eras_map_populated(self, client):
        """Line 351: eras_map.setdefault populates book eras."""
        resp = client.get("/api/audiobooks?search=Dune")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert "Cold War" in dune["eras"]

    def test_topics_map_populated(self, client):
        """Line 364: topics_map.setdefault populates book topics."""
        resp = client.get("/api/audiobooks?search=Dune")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert "Desert Planet" in dune["topics"]

    def test_supplement_count_populated(self, client):
        """Supplement count batch fetch."""
        resp = client.get("/api/audiobooks?search=Dune")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert dune["supplement_count"] >= 1

    def test_authors_map_populated(self, client):
        """Lines 378-401: normalized authors batch fetch."""
        resp = client.get("/api/audiobooks?search=Dune")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert len(dune["authors"]) >= 1
        assert dune["authors"][0]["name"] == "Frank Herbert"

    def test_narrators_map_populated(self, client):
        """Lines 403-427: normalized narrators batch fetch."""
        resp = client.get("/api/audiobooks?search=Dune")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert len(dune["narrators"]) >= 1
        assert dune["narrators"][0]["name"] == "Scott Brick"

    def test_edition_count_detected_for_multi_edition(self, client):
        """Lines 454-464: edition_count > 1 for books with edition markers."""
        # Dune and Dune (50th Anniversary Edition) share a base title
        resp = client.get("/api/audiobooks?author=Frank Herbert")
        data = resp.get_json()
        dune = next((b for b in data["audiobooks"] if b["title"] == "Dune"), None)
        assert dune is not None
        assert dune["edition_count"] > 1

    def test_edition_count_is_1_for_single_edition(self, client):
        """Lines 463-464: edition_count == 1 when no edition markers."""
        resp = client.get("/api/audiobooks")
        data = resp.get_json()
        orphan = next((b for b in data["audiobooks"] if b["title"] == "Orphan Book"), None)
        if orphan:
            assert orphan["edition_count"] == 1


class TestGetAudiobooksCollections:
    """Test collection-based filtering in get_audiobooks."""

    def test_collection_filter_podcasts(self, client):
        """Collection with bypasses_filter=True shows non-audiobook content."""
        resp = client.get("/api/audiobooks?collection=podcasts")
        assert resp.status_code == 200
        data = resp.get_json()
        # Our podcast entry should appear
        titles = [b["title"] for b in data["audiobooks"]]
        assert "My Podcast Episode" in titles

    def test_collection_filter_nonexistent(self, client):
        """Unknown collection ID is silently ignored."""
        resp = client.get("/api/audiobooks?collection=does-not-exist")
        assert resp.status_code == 200


class TestGetAudiobooksAuthorsException:
    """Test the exception handlers for missing book_authors/book_narrators tables."""

    def test_authors_table_missing_handled(self, tmp_path):
        """Lines 399-401: exception when book_authors table doesn't exist."""
        from backend.api_modular import create_app

        db_path = tmp_path / "no_authors.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE audiobooks (
                id INTEGER PRIMARY KEY, title TEXT, author TEXT, narrator TEXT,
                publisher TEXT, series TEXT, series_sequence REAL, edition TEXT,
                asin TEXT, acquired_date TEXT, published_year INTEGER,
                published_date TEXT, author_last_name TEXT, author_first_name TEXT,
                narrator_last_name TEXT, narrator_first_name TEXT,
                duration_hours REAL, duration_formatted TEXT, file_size_mb REAL,
                file_path TEXT, cover_path TEXT, format TEXT, quality TEXT,
                description TEXT, content_type TEXT DEFAULT 'Product'
            );
            CREATE TABLE genres (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE audiobook_genres (audiobook_id INTEGER, genre_id INTEGER,
                PRIMARY KEY (audiobook_id, genre_id));
            CREATE TABLE eras (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE audiobook_eras (audiobook_id INTEGER, era_id INTEGER,
                PRIMARY KEY (audiobook_id, era_id));
            CREATE TABLE topics (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE TABLE audiobook_topics (audiobook_id INTEGER, topic_id INTEGER,
                PRIMARY KEY (audiobook_id, topic_id));
            CREATE TABLE supplements (id INTEGER PRIMARY KEY, audiobook_id INTEGER,
                type TEXT, filename TEXT, file_path TEXT, file_size_mb REAL);
            -- Deliberately omit book_authors and book_narrators tables

            INSERT INTO audiobooks (id, title, author, file_path, format, content_type,
                duration_hours, file_size_mb)
            VALUES (1, 'Test Book', 'Test Author', '/test/book.opus', 'opus',
                'Product', 5.0, 100.0);
        """)
        conn.commit()
        conn.close()

        supplements_dir = tmp_path / "supplements"
        supplements_dir.mkdir(exist_ok=True)

        app = create_app(
            database_path=db_path,
            project_dir=tmp_path,
            supplements_dir=supplements_dir,
            api_port=5097,
        )
        app.config["TESTING"] = True

        with app.test_client() as c:
            resp = c.get("/api/audiobooks")
            assert resp.status_code == 200
            data = resp.get_json()
            # Should still return results despite missing tables
            assert data["pagination"]["total_count"] >= 1
            book = data["audiobooks"][0]
            # authors and narrators should be empty lists (exception caught)
            assert book["authors"] == []
            assert book["narrators"] == []


# ─── get_audiobook (single): lines 606-641 ────────────────────────────────


class TestGetSingleAudiobook:
    def test_get_audiobook_found_with_related_data(self, client):
        """Lines 606-641: single audiobook with genres, eras, topics."""
        resp = client.get("/api/audiobooks/100")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["title"] == "Dune"
        assert "Science Fiction" in data["genres"]
        assert "Cold War" in data["eras"]
        assert "Desert Planet" in data["topics"]

    def test_get_audiobook_not_found(self, client):
        """Line 602-604: audiobook not found returns 404."""
        resp = client.get("/api/audiobooks/99999")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_get_audiobook_no_related_data(self, client):
        """Audiobook with no genres/eras/topics returns empty lists."""
        resp = client.get("/api/audiobooks/103")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["genres"] == []
        assert data["eras"] == []
        assert data["topics"] == []


# ─── stream_audiobook: lines 708-726 ──────────────────────────────────────


class TestStreamAudiobook:
    def test_stream_not_found(self, client):
        """Line 670-671: audiobook not found."""
        resp = client.get("/api/stream/99999")
        assert resp.status_code == 404

    def test_stream_file_not_on_disk(self, client):
        """Line 674-675: file_path doesn't exist on disk."""
        resp = client.get("/api/stream/100")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "File not found" in data["error"]

    def test_stream_webm_remux_failure_returncode(self, client, tmp_path):
        """Lines 706-716: ffmpeg returns non-zero exit code."""
        # Create a temporary file to serve as the opus file
        opus_file = tmp_path / "dune.opus"
        opus_file.write_bytes(b"fake opus data")

        # Patch the DB to return our temp file path
        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {"file_path": str(opus_file), "format": "opus"}
            mock_db.return_value = mock_conn

            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Error: invalid input"

            with patch("subprocess.run", return_value=mock_result):
                with patch(
                    "backend.api_modular.audiobooks.AUDIOBOOKS_WEBM_CACHE", tmp_path / "webm-cache"
                ):
                    resp = client.get("/api/stream/100?format=webm")
                    assert resp.status_code == 500
                    data = resp.get_json()
                    assert "Format conversion failed" in data["error"]

    def test_stream_webm_remux_timeout_exception(self, client, tmp_path):
        """Lines 718-726: subprocess.TimeoutExpired during ffmpeg."""
        import subprocess

        opus_file = tmp_path / "dune.opus"
        opus_file.write_bytes(b"fake opus data")

        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {"file_path": str(opus_file), "format": "opus"}
            mock_db.return_value = mock_conn

            with patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=300)
            ):
                with patch(
                    "backend.api_modular.audiobooks.AUDIOBOOKS_WEBM_CACHE", tmp_path / "webm-cache"
                ):
                    resp = client.get("/api/stream/100?format=webm")
                    assert resp.status_code == 500
                    data = resp.get_json()
                    assert "Format conversion failed" in data["error"]

    def test_stream_webm_remux_oserror(self, client, tmp_path):
        """Lines 718-726: OSError during ffmpeg (disk full, etc.)."""
        opus_file = tmp_path / "dune.opus"
        opus_file.write_bytes(b"fake opus data")

        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {"file_path": str(opus_file), "format": "opus"}
            mock_db.return_value = mock_conn

            with patch("subprocess.run", side_effect=OSError("No space left on device")):
                with patch(
                    "backend.api_modular.audiobooks.AUDIOBOOKS_WEBM_CACHE", tmp_path / "webm-cache"
                ):
                    resp = client.get("/api/stream/100?format=webm")
                    assert resp.status_code == 500
                    data = resp.get_json()
                    assert "Format conversion failed" in data["error"]

    def test_stream_original_format_m4b(self, client, tmp_path):
        """Default stream serves original file with correct MIME type."""
        m4b_file = tmp_path / "special.m4b"
        m4b_file.write_bytes(b"fake m4b data")

        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {"file_path": str(m4b_file), "format": "m4b"}
            mock_db.return_value = mock_conn

            resp = client.get("/api/stream/102")
            assert resp.status_code == 200
            assert resp.content_type in ("audio/mp4", "audio/mp4; charset=utf-8")


# ─── download_audiobook: lines 771, 778-803 ───────────────────────────────


class TestDownloadAudiobook:
    def test_download_not_found(self, client):
        """Line 771: audiobook not found returns 404."""
        resp = client.get("/api/download/99999")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_download_file_not_on_disk(self, client):
        """Line 774-775: file doesn't exist on disk."""
        resp = client.get("/api/download/100")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "File not found" in data["error"]

    def test_download_with_author(self, client, tmp_path):
        """Lines 778-803: successful download with author in filename."""
        m4b_file = tmp_path / "special.m4b"
        m4b_file.write_bytes(b"fake m4b audio content")

        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "title": 'Test: Book/With "Special" Chars',
                "author": "Author One",
                "file_path": str(m4b_file),
                "format": "m4b",
            }
            mock_db.return_value = mock_conn

            resp = client.get("/api/download/102")
            assert resp.status_code == 200
            # Check Content-Disposition header for sanitized filename
            cd = resp.headers.get("Content-Disposition", "")
            assert "attachment" in cd
            # Problematic chars should be replaced with '-'
            assert "/" not in cd.split("filename=")[-1].replace("UTF-8''", "")

    def test_download_without_author(self, client, tmp_path):
        """Lines 791-792: download when author is None."""
        mp3_file = tmp_path / "orphan.mp3"
        mp3_file.write_bytes(b"fake mp3 audio")

        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "title": "Orphan Book",
                "author": None,
                "file_path": str(mp3_file),
                "format": "mp3",
            }
            mock_db.return_value = mock_conn

            resp = client.get("/api/download/103")
            assert resp.status_code == 200
            cd = resp.headers.get("Content-Disposition", "")
            assert "attachment" in cd
            # Should not contain " - " author separator
            assert "Orphan Book" in cd or "orphan" in cd.lower()

    def test_download_unknown_format_mimetype(self, client, tmp_path):
        """Lines 795-801: unknown format falls back to application/octet-stream."""
        weird_file = tmp_path / "weird.flac"
        weird_file.write_bytes(b"fake flac data")

        with patch("backend.api_modular.audiobooks._get_audiobooks_db") as mock_db:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_cursor.fetchone.return_value = {
                "title": "Weird Format Book",
                "author": "Someone",
                "file_path": str(weird_file),
                "format": "flac",
            }
            mock_db.return_value = mock_conn

            resp = client.get("/api/download/999")
            assert resp.status_code == 200
            assert resp.content_type in (
                "application/octet-stream",
                "application/octet-stream; charset=utf-8",
            )


# ─── health: line 819 ─────────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_health_with_version_file(self, client):
        """Line 819: health reads VERSION from project dir."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["version"] == "9.9.9-test"

    def test_health_without_version_file(self, tmp_path):
        """Line 818-819: VERSION file doesn't exist → version = 'unknown'."""
        from backend.api_modular import create_app

        db_path = tmp_path / "health_test.db"
        _init_test_database(db_path)

        # project_dir with no VERSION file
        empty_dir = tmp_path / "no_version"
        empty_dir.mkdir()

        app = create_app(
            database_path=db_path, project_dir=empty_dir, supplements_dir=tmp_path, api_port=5096
        )
        app.config["TESTING"] = True

        with app.test_client() as c:
            resp = c.get("/health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["version"] == "unknown"

    def test_health_db_exists_field(self, client):
        """Health endpoint reports database exists."""
        resp = client.get("/health")
        data = resp.get_json()
        assert data["database"] == "True"


# ─── get_filters endpoint ─────────────────────────────────────────────────


class TestGetFilters:
    def test_filters_returns_all_categories(self, client):
        """Smoke test for /api/filters."""
        resp = client.get("/api/filters")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "authors" in data
        assert "narrators" in data
        assert "publishers" in data
        assert "genres" in data
        assert "eras" in data
        assert "topics" in data
        assert "formats" in data


# ─── get_narrator_counts endpoint ─────────────────────────────────────────


class TestGetNarratorCounts:
    def test_narrator_counts(self, client):
        """Smoke test for /api/narrator-counts."""
        resp = client.get("/api/narrator-counts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)
        # Scott Brick narrates multiple books
        assert "Scott Brick" in data
