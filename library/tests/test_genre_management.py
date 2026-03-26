"""Tests for genre management API endpoints and Back Office UI."""

import os

import pytest

# ---------------------------------------------------------------------------
# HTML / JS / CSS static tests
# ---------------------------------------------------------------------------


class TestGenreManagementHTML:
    """Verify bulk-ops genre management UI elements in utilities.html."""

    @pytest.fixture
    def html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "utilities.html")
        with open(path) as f:
            return f.read()

    def test_genre_action_section_exists(self, html):
        """Genre management action should be in Step 3."""
        assert "bulk-genre-action" in html or "genre-action" in html

    def test_genre_picker_container(self, html):
        """There should be a container for genre checkboxes."""
        assert "genre-picker" in html

    def test_genre_mode_selector(self, html):
        """There should be add/remove mode controls."""
        assert "genre-mode" in html or "genre-action-mode" in html

    def test_genre_apply_button(self, html):
        """There should be an apply button for genre changes."""
        assert "bulk-genre-apply" in html or "genre-apply" in html


class TestGenreManagementJS:
    """Verify JS functions exist for genre management."""

    @pytest.fixture
    def js(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "js", "utilities.js"
        )
        with open(path) as f:
            return f.read()

    def test_load_genres_function(self, js):
        assert "loadGenresForPicker" in js or "loadGenreList" in js

    def test_apply_bulk_genres_function(self, js):
        assert "applyBulkGenres" in js or "bulkGenreApply" in js

    def test_no_innerhtml_with_genre_data(self, js):
        """Genre names come from user data; must use textContent, not innerHTML."""
        # Find lines with innerHTML that also reference genre
        lines = js.split("\n")
        for line in lines:
            if "innerHTML" in line and "genre" in line.lower():
                # Allow innerHTML with static HTML only (no variable interpolation)
                assert "${" not in line and "+" not in line, (
                    f"innerHTML with dynamic genre data: {line.strip()}"
                )


class TestGenreManagementCSS:
    """Verify genre management styles exist."""

    @pytest.fixture
    def css(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "css", "utilities.css"
        )
        with open(path) as f:
            return f.read()

    def test_genre_picker_styles(self, css):
        assert "genre-picker" in css

    def test_genre_tag_styles(self, css):
        assert "genre-tag" in css or "genre-chip" in css or "genre-checkbox" in css


# ---------------------------------------------------------------------------
# API endpoint tests (static — check route definitions exist)
# ---------------------------------------------------------------------------


class TestGenreAPIRoutes:
    """Verify genre management API routes are defined in utilities_crud.py."""

    @pytest.fixture
    def crud_py(self):
        path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "backend",
            "api_modular",
            "utilities_crud.py",
        )
        with open(path) as f:
            return f.read()

    def test_get_genres_route(self, crud_py):
        """GET /api/genres should be defined."""
        assert "/api/genres" in crud_py

    def test_set_audiobook_genres_route(self, crud_py):
        """PUT /api/audiobooks/<id>/genres should be defined."""
        assert "genres" in crud_py
        assert "PUT" in crud_py

    def test_bulk_genres_route(self, crud_py):
        """POST /api/audiobooks/bulk-genres should be defined."""
        assert "bulk-genres" in crud_py

    def test_bulk_genres_add_mode(self, crud_py):
        """Bulk genres should support add mode."""
        assert '"add"' in crud_py or "'add'" in crud_py

    def test_bulk_genres_remove_mode(self, crud_py):
        """Bulk genres should support remove mode."""
        assert '"remove"' in crud_py or "'remove'" in crud_py
