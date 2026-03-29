"""
Tests for the User Suggestions API blueprint (api_modular/suggestions.py).

Uses the auth-enabled Flask app fixtures from conftest.py.
Suggestion submission requires login_required; admin endpoints use admin_if_enabled.
"""

import sqlite3

import pytest

from backend.api_modular.suggestions import sanitize_message, MAX_MESSAGE_LENGTH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_suggestion(
    auth_app, username="testuser", message="A suggestion", is_read=0
):
    """Insert a suggestion directly into the database and return its id."""
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "INSERT INTO user_suggestions (username, message, is_read, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (username, message, is_read),
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def _clear_suggestions(auth_app):
    """Remove all suggestions."""
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM user_suggestions")
    conn.commit()
    conn.close()


def _count_suggestions(auth_app):
    """Count suggestions in the database."""
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM user_suggestions").fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Fixture: clean suggestion state before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_suggestions(auth_app):
    """Ensure the suggestions table exists and is empty before each test.

    The auth_app fixture uses inline SQL that may not include user_suggestions,
    so we create the table if it doesn't exist.  Also re-points the suggestions
    blueprint's module-level _db_path to auth_app's database — another
    session-scoped create_app() call (flask_app) may have overwritten it.
    """
    db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get("DATABASE")

    # Re-point suggestions blueprint to auth_app's database.
    # The blueprint may be loaded under multiple module paths
    # (api_modular.suggestions AND backend.api_modular.suggestions) — set _db_path on ALL.
    import sys

    for mod_name in list(sys.modules):
        if mod_name.endswith("api_modular.suggestions"):
            mod = sys.modules[mod_name]
            if hasattr(mod, "init_suggestions_routes"):
                mod.init_suggestions_routes(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_suggestions_read ON user_suggestions(is_read);
        CREATE INDEX IF NOT EXISTS idx_suggestions_created ON user_suggestions(created_at);
    """)
    conn.execute("DELETE FROM user_suggestions")
    conn.commit()
    conn.close()
    yield
    _clear_suggestions(auth_app)


# ===========================================================================
# sanitize_message() unit tests
# ===========================================================================


class TestSanitizeMessage:
    """Unit tests for the sanitize_message function."""

    def test_empty_string(self):
        assert sanitize_message("") == ""

    def test_none_input(self):
        assert sanitize_message(None) == ""

    def test_plain_text_unchanged(self):
        assert sanitize_message("Hello world") == "Hello world"

    def test_strips_html_tags(self):
        assert sanitize_message("<b>bold</b> text") == "bold text"
        assert sanitize_message('<script>alert("xss")</script>') == 'alert("xss")'

    def test_strips_html_entities(self):
        # After entity stripping and whitespace collapsing: "foo  bar" -> "foo bar"
        assert sanitize_message("foo &amp; bar") == "foo bar"
        assert sanitize_message("&#123;") == ""

    def test_removes_zero_width_chars(self):
        # Zero-width space (U+200B)
        result = sanitize_message("hello\u200bworld")
        assert "\u200b" not in result
        assert result == "helloworld"

    def test_removes_bidi_overrides(self):
        # Right-to-left override (U+202E)
        result = sanitize_message("test\u202eevil")
        assert "\u202e" not in result

    def test_removes_soft_hyphen(self):
        result = sanitize_message("pass\u00adword")
        assert "\u00ad" not in result
        assert result == "password"

    def test_preserves_newlines(self):
        result = sanitize_message("line1\nline2\nline3")
        assert "line1" in result
        assert "line2" in result

    def test_collapses_excessive_newlines(self):
        result = sanitize_message("a\n\n\n\n\nb")
        assert "\n\n\n" not in result
        # Should collapse to max 2 newlines
        assert result == "a\n\nb"

    def test_collapses_whitespace_on_line(self):
        result = sanitize_message("too   many    spaces")
        assert result == "too many spaces"

    def test_truncates_to_max_length(self):
        long_msg = "x" * (MAX_MESSAGE_LENGTH + 500)
        result = sanitize_message(long_msg)
        assert len(result) <= MAX_MESSAGE_LENGTH

    def test_strips_leading_trailing_whitespace(self):
        result = sanitize_message("   padded   ")
        assert result == "padded"

    def test_normalizes_crlf(self):
        result = sanitize_message("line1\r\nline2\rline3")
        assert "\r" not in result
        assert "line1\nline2\nline3" == result

    def test_preserves_accented_characters(self):
        result = sanitize_message("cafe\u0301 resume\u0301")
        # Accented chars should be preserved (combining marks are category Mn
        # which the code drops, but the base letters survive)
        assert "caf" in result
        assert "resum" in result

    def test_preserves_punctuation_and_symbols(self):
        result = sanitize_message("Hello! @#$%^&*() test?")
        assert "Hello!" in result
        assert "test?" in result

    def test_removes_bom(self):
        result = sanitize_message("\ufeffHello")
        assert "\ufeff" not in result
        assert "Hello" in result

    def test_removes_replacement_char(self):
        result = sanitize_message("data\ufffd here")
        assert "\ufffd" not in result


# ===========================================================================
# POST /api/suggestions (submit)
# ===========================================================================


class TestSubmitSuggestion:
    """Tests for user suggestion submission."""

    def test_submit_valid(self, auth_app, user_client):
        """Authenticated user can submit a suggestion."""
        resp = user_client.post(
            "/api/suggestions",
            json={"message": "Please add dark mode"},
        )
        assert resp.status_code == 201
        assert "thank you" in resp.get_json()["message"].lower()
        assert _count_suggestions(auth_app) == 1

    def test_submit_missing_message(self, user_client):
        """Missing message field returns 400."""
        resp = user_client.post("/api/suggestions", json={})
        assert resp.status_code == 400
        assert "message" in resp.get_json()["error"].lower()

    def test_submit_empty_message(self, user_client):
        """Empty message string returns 400."""
        resp = user_client.post("/api/suggestions", json={"message": ""})
        assert resp.status_code == 400

    def test_submit_non_string_message(self, user_client):
        """Non-string message returns 400."""
        resp = user_client.post("/api/suggestions", json={"message": 12345})
        assert resp.status_code == 400
        assert "string" in resp.get_json()["error"].lower()

    def test_submit_html_sanitized(self, auth_app, user_client):
        """HTML tags are stripped from the message."""
        user_client.post(
            "/api/suggestions",
            json={"message": "<b>bold</b> suggestion"},
        )
        db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get(
            "DATABASE"
        )
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT message FROM user_suggestions").fetchone()
        conn.close()
        assert "<b>" not in row[0]
        assert "bold" in row[0]

    def test_submit_only_html_returns_error(self, user_client):
        """Message that is entirely HTML tags becomes empty after sanitization."""
        resp = user_client.post(
            "/api/suggestions",
            json={"message": "<div><span></span></div>"},
        )
        assert resp.status_code == 400
        assert "empty" in resp.get_json()["error"].lower()

    def test_submit_truncates_long_message(self, auth_app, user_client):
        """Very long messages are truncated to MAX_MESSAGE_LENGTH."""
        long_msg = "a" * (MAX_MESSAGE_LENGTH + 1000)
        resp = user_client.post("/api/suggestions", json={"message": long_msg})
        assert resp.status_code == 201

        db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get(
            "DATABASE"
        )
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT message FROM user_suggestions").fetchone()
        conn.close()
        assert len(row[0]) <= MAX_MESSAGE_LENGTH

    def test_submit_anon_unauthorized(self, anon_client):
        """Unauthenticated request cannot submit suggestions."""
        resp = anon_client.post(
            "/api/suggestions",
            json={"message": "I'm not logged in"},
        )
        assert resp.status_code == 401

    def test_submit_stores_username(self, auth_app, user_client):
        """Submitted suggestion records the authenticated username."""
        user_client.post(
            "/api/suggestions",
            json={"message": "Track my name"},
        )
        db_path = auth_app.config.get("DATABASE_PATH") or auth_app.config.get(
            "DATABASE"
        )
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT username FROM user_suggestions").fetchone()
        conn.close()
        # The user_client fixture uses regularuser_fix
        assert row[0] == "regularuser_fix"

    def test_submit_no_body(self, user_client):
        """Request with no JSON body returns 400."""
        resp = user_client.post(
            "/api/suggestions",
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400


# ===========================================================================
# Admin GET /api/admin/suggestions
# ===========================================================================


class TestAdminGetSuggestions:
    """Tests for admin suggestion listing."""

    def test_empty_list(self, admin_client):
        """Returns empty list when no suggestions exist."""
        resp = admin_client.get("/api/admin/suggestions")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_all_by_default(self, auth_app, admin_client):
        """Default filter returns all suggestions."""
        _insert_suggestion(auth_app, message="Unread one", is_read=0)
        _insert_suggestion(auth_app, message="Read one", is_read=1)

        resp = admin_client.get("/api/admin/suggestions")
        items = resp.get_json()
        assert len(items) == 2

    def test_filter_unread(self, auth_app, admin_client):
        """Filter=unread returns only unread suggestions."""
        _insert_suggestion(auth_app, message="Unread", is_read=0)
        _insert_suggestion(auth_app, message="Read", is_read=1)

        resp = admin_client.get("/api/admin/suggestions?filter=unread")
        items = resp.get_json()
        assert len(items) == 1
        assert items[0]["message"] == "Unread"

    def test_filter_read(self, auth_app, admin_client):
        """Filter=read returns only read suggestions."""
        _insert_suggestion(auth_app, message="Unread", is_read=0)
        _insert_suggestion(auth_app, message="Read", is_read=1)

        resp = admin_client.get("/api/admin/suggestions?filter=read")
        items = resp.get_json()
        assert len(items) == 1
        assert items[0]["message"] == "Read"

    def test_filter_all_explicit(self, auth_app, admin_client):
        """filter=all returns everything."""
        _insert_suggestion(auth_app, message="A", is_read=0)
        _insert_suggestion(auth_app, message="B", is_read=1)

        resp = admin_client.get("/api/admin/suggestions?filter=all")
        assert len(resp.get_json()) == 2

    def test_ordered_by_created_at_asc(self, auth_app, admin_client):
        """Suggestions are returned in chronological order."""
        _insert_suggestion(auth_app, message="First")
        _insert_suggestion(auth_app, message="Second")

        items = admin_client.get("/api/admin/suggestions").get_json()
        assert items[0]["message"] == "First"
        assert items[1]["message"] == "Second"

    def test_non_admin_forbidden(self, user_client):
        """Regular user gets 403 on admin suggestions endpoint."""
        resp = user_client.get("/api/admin/suggestions")
        assert resp.status_code == 403

    def test_anon_unauthorized(self, anon_client):
        """Unauthenticated request gets 401."""
        resp = anon_client.get("/api/admin/suggestions")
        assert resp.status_code == 401


# ===========================================================================
# Admin GET /api/admin/suggestions/unread-count
# ===========================================================================


class TestAdminUnreadCount:
    """Tests for the unread suggestion count endpoint."""

    def test_zero_when_empty(self, admin_client):
        """Count is 0 when no suggestions exist."""
        resp = admin_client.get("/api/admin/suggestions/unread-count")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0

    def test_counts_unread_only(self, auth_app, admin_client):
        """Only unread suggestions are counted."""
        _insert_suggestion(auth_app, message="Unread 1", is_read=0)
        _insert_suggestion(auth_app, message="Unread 2", is_read=0)
        _insert_suggestion(auth_app, message="Read", is_read=1)

        resp = admin_client.get("/api/admin/suggestions/unread-count")
        assert resp.get_json()["count"] == 2

    def test_non_admin_forbidden(self, user_client):
        resp = user_client.get("/api/admin/suggestions/unread-count")
        assert resp.status_code == 403

    def test_anon_unauthorized(self, anon_client):
        resp = anon_client.get("/api/admin/suggestions/unread-count")
        assert resp.status_code == 401


# ===========================================================================
# Admin PATCH /api/admin/suggestions/<id> (mark read/unread)
# ===========================================================================


class TestAdminUpdateSuggestion:
    """Tests for marking suggestions read/unread."""

    def test_mark_as_read(self, auth_app, admin_client):
        """Mark an unread suggestion as read."""
        item_id = _insert_suggestion(auth_app, message="Mark me", is_read=0)
        resp = admin_client.patch(
            f"/api/admin/suggestions/{item_id}",
            json={"is_read": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Updated"

        # Verify via unread-count
        count_resp = admin_client.get("/api/admin/suggestions/unread-count")
        assert count_resp.get_json()["count"] == 0

    def test_mark_as_unread(self, auth_app, admin_client):
        """Mark a read suggestion back to unread."""
        item_id = _insert_suggestion(auth_app, message="Toggle me", is_read=1)
        resp = admin_client.patch(
            f"/api/admin/suggestions/{item_id}",
            json={"is_read": False},
        )
        assert resp.status_code == 200

        count_resp = admin_client.get("/api/admin/suggestions/unread-count")
        assert count_resp.get_json()["count"] == 1

    def test_update_not_found(self, admin_client):
        """Updating a nonexistent suggestion returns 404."""
        resp = admin_client.patch(
            "/api/admin/suggestions/99999",
            json={"is_read": True},
        )
        assert resp.status_code == 404

    def test_update_missing_is_read(self, auth_app, admin_client):
        """Missing is_read field returns 400."""
        item_id = _insert_suggestion(auth_app, message="No field")
        resp = admin_client.patch(
            f"/api/admin/suggestions/{item_id}",
            json={"other": "data"},
        )
        assert resp.status_code == 400
        assert "is_read" in resp.get_json()["error"].lower()

    def test_update_empty_body(self, auth_app, admin_client):
        """Empty body returns 400."""
        item_id = _insert_suggestion(auth_app, message="Empty")
        resp = admin_client.patch(
            f"/api/admin/suggestions/{item_id}",
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_update_non_admin_forbidden(self, auth_app, user_client):
        """Regular user cannot update suggestions."""
        item_id = _insert_suggestion(auth_app, message="Protected")
        resp = user_client.patch(
            f"/api/admin/suggestions/{item_id}",
            json={"is_read": True},
        )
        assert resp.status_code == 403

    def test_update_anon_unauthorized(self, auth_app, anon_client):
        """Unauthenticated request cannot update suggestions."""
        item_id = _insert_suggestion(auth_app, message="Protected")
        resp = anon_client.patch(
            f"/api/admin/suggestions/{item_id}",
            json={"is_read": True},
        )
        assert resp.status_code == 401


# ===========================================================================
# Admin DELETE /api/admin/suggestions/<id>
# ===========================================================================


class TestAdminDeleteSuggestion:
    """Tests for deleting suggestions."""

    def test_delete_item(self, auth_app, admin_client):
        """Delete an existing suggestion."""
        item_id = _insert_suggestion(auth_app, message="Delete me")
        resp = admin_client.delete(f"/api/admin/suggestions/{item_id}")
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Deleted"
        assert _count_suggestions(auth_app) == 0

    def test_delete_not_found(self, admin_client):
        """Deleting a nonexistent suggestion returns 404."""
        resp = admin_client.delete("/api/admin/suggestions/99999")
        assert resp.status_code == 404

    def test_delete_non_admin_forbidden(self, auth_app, user_client):
        """Regular user cannot delete suggestions."""
        item_id = _insert_suggestion(auth_app, message="Protected")
        resp = user_client.delete(f"/api/admin/suggestions/{item_id}")
        assert resp.status_code == 403

    def test_delete_anon_unauthorized(self, auth_app, anon_client):
        """Unauthenticated request cannot delete suggestions."""
        item_id = _insert_suggestion(auth_app, message="Protected")
        resp = anon_client.delete(f"/api/admin/suggestions/{item_id}")
        assert resp.status_code == 401

    def test_delete_only_target(self, auth_app, admin_client):
        """Deleting one suggestion leaves others intact."""
        _insert_suggestion(auth_app, message="Keep")
        id2 = _insert_suggestion(auth_app, message="Remove")
        admin_client.delete(f"/api/admin/suggestions/{id2}")
        assert _count_suggestions(auth_app) == 1
        items = admin_client.get("/api/admin/suggestions").get_json()
        assert items[0]["message"] == "Keep"
