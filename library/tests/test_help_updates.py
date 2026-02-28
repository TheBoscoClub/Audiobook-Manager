"""Tests for updated help content covering new per-user features."""

import os

import pytest


class TestHelpNewSections:
    """Verify help.html covers new per-user features."""

    @pytest.fixture
    def help_html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "help.html")
        with open(path) as f:
            return f.read()

    def test_my_grotto_section(self, help_html):
        assert "My Grotto" in help_html

    def test_progress_tracking_section(self, help_html):
        assert "progress" in help_html.lower()

    def test_download_history_section(self, help_html):
        assert "download" in help_html.lower()

    def test_new_books_section(self, help_html):
        assert "new books" in help_html.lower() or "New Books" in help_html


class TestTutorialNewSteps:
    """Verify tutorial covers new features."""

    @pytest.fixture
    def tutorial_js(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "web-v2", "js", "tutorial.js"
        )
        with open(path) as f:
            return f.read()

    def test_my_grotto_step(self, tutorial_js):
        assert "my-library" in tutorial_js.lower() or "My Grotto" in tutorial_js
