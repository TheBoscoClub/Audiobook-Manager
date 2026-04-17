"""Behavioral tests for genre management API endpoints.

These tests call the actual API endpoints (GET /api/genres,
POST /api/audiobooks/bulk-genres, PUT /api/audiobooks/<id>/genres)
and verify that genre data is correctly persisted and returned.

Replaces the static string-grep tests in test_genre_management.py.

Uses the session-scoped flask_app fixture from conftest.py to avoid
blueprint double-registration issues (Flask blueprints are singletons
that can only be initialized once per process).
"""

import sqlite3

import pytest


def _get_db_path(flask_app):
    """Get the database path from the Flask app config."""
    return flask_app.config["DATABASE_PATH"]


def _seed_audiobooks(db_path, count=4):
    """Insert test audiobooks and return their IDs."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ids = []
    for i in range(1, count + 1):
        cursor = conn.execute(
            "INSERT OR IGNORE INTO audiobooks (title, author, file_path, format) "
            "VALUES (?, ?, ?, ?)",
            (f"GenreTest Book {i}", f"Author {i}", f"/test/genrebook{i}.opus", "opus"),
        )
        if cursor.lastrowid:
            ids.append(cursor.lastrowid)
        else:
            # Row already existed, find it
            row = conn.execute(
                "SELECT id FROM audiobooks WHERE file_path = ?", (f"/test/genrebook{i}.opus",)
            ).fetchone()
            ids.append(row[0])
    conn.commit()
    conn.close()
    return ids


def _cleanup_genre_test_data(db_path, book_ids):
    """Remove test audiobooks and their genre associations."""
    conn = sqlite3.connect(str(db_path))
    for bid in book_ids:
        conn.execute("DELETE FROM audiobook_genres WHERE audiobook_id = ?", (bid,))
        conn.execute("DELETE FROM audiobooks WHERE id = ?", (bid,))
    # Clean up any genres that have no remaining associations
    conn.execute(
        "DELETE FROM genres WHERE id NOT IN (SELECT DISTINCT genre_id FROM audiobook_genres)"
    )
    conn.commit()
    conn.close()


def _get_audiobook_genres(db_path, audiobook_id):
    """Query genre names for an audiobook directly from the DB."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT g.name FROM genres g "
        "JOIN audiobook_genres ag ON g.id = ag.genre_id "
        "WHERE ag.audiobook_id = ? ORDER BY g.name",
        (audiobook_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


@pytest.fixture
def genre_client(flask_app):
    """Test client with seeded audiobooks for genre tests.

    Creates test audiobooks, yields the client and book IDs,
    then cleans up all test data to avoid polluting the session DB.
    """
    db_path = _get_db_path(flask_app)
    book_ids = _seed_audiobooks(db_path, count=4)

    with flask_app.test_client() as client:
        yield client, book_ids, db_path

    _cleanup_genre_test_data(db_path, book_ids)


# ================================================================
# GET /api/genres
# ================================================================


class TestListGenres:
    """Test the GET /api/genres endpoint."""

    def test_genres_endpoint_returns_list(self, app_client):
        """GET /api/genres should return a JSON list."""
        resp = app_client.get("/api/genres")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_genres_returned_with_book_counts(self, genre_client):
        """Genres with associated books should report correct counts."""
        client, book_ids, db_path = genre_client

        # Set genres on two books via the API
        client.put(
            f"/api/audiobooks/{book_ids[0]}/genres",
            json={"genres": ["BehavSci-Fi", "BehavFantasy"]},
        )
        client.put(f"/api/audiobooks/{book_ids[1]}/genres", json={"genres": ["BehavFantasy"]})

        resp = client.get("/api/genres")
        assert resp.status_code == 200
        genres = resp.get_json()

        by_name = {g["name"]: g for g in genres}
        assert "BehavFantasy" in by_name
        assert by_name["BehavFantasy"]["book_count"] == 2
        assert by_name["BehavSci-Fi"]["book_count"] == 1

    def test_genre_objects_have_required_fields(self, genre_client):
        """Each genre object should include id, name, and book_count."""
        client, book_ids, _ = genre_client

        # Create a genre via the API
        client.put(f"/api/audiobooks/{book_ids[0]}/genres", json={"genres": ["FieldCheckGenre"]})

        resp = client.get("/api/genres")
        genres = resp.get_json()
        target = [g for g in genres if g["name"] == "FieldCheckGenre"]
        assert len(target) == 1
        genre = target[0]
        assert "id" in genre
        assert isinstance(genre["id"], int)
        assert "name" in genre
        assert "book_count" in genre


# ================================================================
# PUT /api/audiobooks/<id>/genres -- single audiobook genre set
# ================================================================


class TestSetAudiobookGenres:
    """Test PUT /api/audiobooks/<id>/genres endpoint."""

    def test_set_genres_creates_associations(self, genre_client):
        """Setting genres on a book should persist them in the DB."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        resp = client.put(
            f"/api/audiobooks/{bid}/genres", json={"genres": ["SetHorror", "SetThriller"]}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        actual = _get_audiobook_genres(db_path, bid)
        assert sorted(actual) == ["SetHorror", "SetThriller"]

    def test_set_genres_replaces_existing(self, genre_client):
        """Setting genres should replace all previous associations."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        # First set
        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": ["ReplA", "ReplB"]})
        assert sorted(_get_audiobook_genres(db_path, bid)) == ["ReplA", "ReplB"]

        # Replace with different genre
        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": ["ReplC"]})
        assert _get_audiobook_genres(db_path, bid) == ["ReplC"]

    def test_set_genres_creates_new_genre_records(self, genre_client):
        """Genres that do not exist yet should be created automatically."""
        client, book_ids, _ = genre_client

        client.put(f"/api/audiobooks/{book_ids[0]}/genres", json={"genres": ["BrandNewTestGenre"]})

        resp = client.get("/api/genres")
        names = [g["name"] for g in resp.get_json()]
        assert "BrandNewTestGenre" in names

    def test_set_genres_nonexistent_audiobook(self, genre_client):
        """Setting genres on a nonexistent audiobook should return 404."""
        client, _, _ = genre_client
        resp = client.put("/api/audiobooks/999999/genres", json={"genres": ["Horror"]})
        assert resp.status_code == 404

    def test_set_genres_missing_body(self, genre_client):
        """Missing 'genres' key should return 400."""
        client, book_ids, _ = genre_client
        resp = client.put(f"/api/audiobooks/{book_ids[0]}/genres", json={"wrong_key": []})
        assert resp.status_code == 400

    def test_set_genres_empty_list_clears_all(self, genre_client):
        """Setting an empty genre list should remove all genre associations."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": ["ClearA", "ClearB"]})
        assert len(_get_audiobook_genres(db_path, bid)) == 2

        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": []})
        assert _get_audiobook_genres(db_path, bid) == []

    def test_set_genres_whitespace_names_stripped(self, genre_client):
        """Genre names with leading/trailing whitespace should be stripped."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": ["  WsSci-Fi  ", " WsFantasy"]})
        actual = _get_audiobook_genres(db_path, bid)
        assert "WsSci-Fi" in actual
        assert "WsFantasy" in actual

    def test_set_genres_skips_blank_names(self, genre_client):
        """Blank genre names (empty or whitespace-only) should be skipped."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": ["BlankValid", "", "  "]})
        assert _get_audiobook_genres(db_path, bid) == ["BlankValid"]


# ================================================================
# POST /api/audiobooks/bulk-genres -- add/remove genres in bulk
# ================================================================


class TestBulkGenres:
    """Test POST /api/audiobooks/bulk-genres endpoint."""

    def test_bulk_add_genres(self, genre_client):
        """Bulk add should associate genres with multiple books."""
        client, book_ids, db_path = genre_client

        resp = client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": book_ids[:3], "genres": ["BulkRomance"], "mode": "add"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["mode"] == "add"

        for bid in book_ids[:3]:
            assert "BulkRomance" in _get_audiobook_genres(db_path, bid)

    def test_bulk_add_idempotent(self, genre_client):
        """Adding the same genre twice should not create duplicates."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        payload = {"ids": [bid], "genres": ["IdempMystery"], "mode": "add"}
        client.post("/api/audiobooks/bulk-genres", json=payload)
        client.post("/api/audiobooks/bulk-genres", json=payload)

        genres = _get_audiobook_genres(db_path, bid)
        assert genres.count("IdempMystery") == 1

    def test_bulk_remove_genres(self, genre_client):
        """Bulk remove should dissociate genres from books."""
        client, book_ids, db_path = genre_client
        ids = book_ids[:2]

        # First add two genres
        client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": ids, "genres": ["RmDrama", "RmComedy"], "mode": "add"},
        )
        assert "RmDrama" in _get_audiobook_genres(db_path, ids[0])

        # Remove Drama from both
        resp = client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": ids, "genres": ["RmDrama"], "mode": "remove"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["mode"] == "remove"

        # Drama gone, Comedy remains
        assert "RmDrama" not in _get_audiobook_genres(db_path, ids[0])
        assert "RmComedy" in _get_audiobook_genres(db_path, ids[0])

    def test_bulk_remove_nonexistent_genre_no_error(self, genre_client):
        """Removing a genre that does not exist should not error."""
        client, book_ids, _ = genre_client
        resp = client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": [book_ids[0]], "genres": ["NonExistGenre99"], "mode": "remove"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_bulk_genres_invalid_mode(self, genre_client):
        """Invalid mode should return 400."""
        client, book_ids, _ = genre_client
        resp = client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": [book_ids[0]], "genres": ["X"], "mode": "replace"},
        )
        assert resp.status_code == 400

    def test_bulk_genres_no_ids(self, genre_client):
        """Empty IDs list should return 400."""
        client, _, _ = genre_client
        resp = client.post(
            "/api/audiobooks/bulk-genres", json={"ids": [], "genres": ["X"], "mode": "add"}
        )
        assert resp.status_code == 400

    def test_bulk_genres_no_genres(self, genre_client):
        """Empty genres list should return 400."""
        client, book_ids, _ = genre_client
        resp = client.post(
            "/api/audiobooks/bulk-genres", json={"ids": [book_ids[0]], "genres": [], "mode": "add"}
        )
        assert resp.status_code == 400

    def test_bulk_genres_no_body(self, genre_client):
        """Missing request body should return 400."""
        client, _, _ = genre_client
        resp = client.post(
            "/api/audiobooks/bulk-genres", content_type="application/json", data="null"
        )
        assert resp.status_code == 400

    def test_bulk_add_multiple_genres_multiple_books(self, genre_client):
        """Adding multiple genres to multiple books at once."""
        client, book_ids, db_path = genre_client

        resp = client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": book_ids, "genres": ["MultiAction", "MultiAdventure"], "mode": "add"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["genre_count"] == 2
        assert data["book_count"] == 4

        for bid in book_ids:
            genres = _get_audiobook_genres(db_path, bid)
            assert "MultiAction" in genres
            assert "MultiAdventure" in genres

    def test_bulk_add_response_reports_affected_count(self, genre_client):
        """Response 'affected' field should reflect actual new associations."""
        client, book_ids, _ = genre_client
        resp = client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": book_ids[:2], "genres": ["AffectedGenre"], "mode": "add"},
        )
        data = resp.get_json()
        assert data["affected"] == 2

    def test_default_mode_is_add(self, genre_client):
        """If mode is omitted, default should be 'add'."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        resp = client.post(
            "/api/audiobooks/bulk-genres", json={"ids": [bid], "genres": ["DefaultModeGenre"]}
        )
        assert resp.status_code == 200
        assert "DefaultModeGenre" in _get_audiobook_genres(db_path, bid)


# ================================================================
# Cross-endpoint integration
# ================================================================


class TestGenreEndToEnd:
    """Verify genre lifecycle across multiple endpoints."""

    def test_create_via_set_then_list(self, genre_client):
        """Genres created via PUT show up in GET /api/genres with counts."""
        client, book_ids, _ = genre_client

        client.put(
            f"/api/audiobooks/{book_ids[0]}/genres",
            json={"genres": ["E2EDystopian", "E2EYoungAdult"]},
        )
        client.put(f"/api/audiobooks/{book_ids[1]}/genres", json={"genres": ["E2EDystopian"]})

        resp = client.get("/api/genres")
        by_name = {g["name"]: g["book_count"] for g in resp.get_json()}
        assert by_name["E2EDystopian"] == 2
        assert by_name["E2EYoungAdult"] == 1

    def test_bulk_add_then_bulk_remove_then_list(self, genre_client):
        """Full cycle: bulk add genres, bulk remove some, verify via list."""
        client, book_ids, _ = genre_client
        ids = book_ids[:3]

        # Add
        client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": ids, "genres": ["E2EEpic", "E2EWar"], "mode": "add"},
        )

        # Remove War from books 2 and 3
        client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": ids[1:], "genres": ["E2EWar"], "mode": "remove"},
        )

        resp = client.get("/api/genres")
        by_name = {g["name"]: g["book_count"] for g in resp.get_json()}
        assert by_name["E2EEpic"] == 3
        assert by_name["E2EWar"] == 1  # only first book still has War

    def test_set_overwrites_bulk_add(self, genre_client):
        """PUT (set) should completely replace genres added via bulk POST."""
        client, book_ids, db_path = genre_client
        bid = book_ids[0]

        # Bulk add two genres
        client.post(
            "/api/audiobooks/bulk-genres",
            json={"ids": [bid], "genres": ["OverG1", "OverG2"], "mode": "add"},
        )
        assert sorted(_get_audiobook_genres(db_path, bid)) == ["OverG1", "OverG2"]

        # Set replaces with just one
        client.put(f"/api/audiobooks/{bid}/genres", json={"genres": ["OverG3"]})
        assert _get_audiobook_genres(db_path, bid) == ["OverG3"]
