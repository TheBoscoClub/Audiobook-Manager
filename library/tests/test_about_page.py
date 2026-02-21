"""Tests for About The Library page."""

import os

import pytest


class TestAboutPage:
    """Verify about.html exists and has required content."""

    @pytest.fixture
    def about_html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "about.html")
        with open(path) as f:
            return f.read()

    def test_file_exists(self, about_html):
        assert len(about_html) > 0

    def test_has_concept_credit(self, about_html):
        assert "Bosco" in about_html

    def test_has_joint_authorship(self, about_html):
        assert "Claude" in about_html

    def test_has_attributions(self, about_html):
        """Third-party tools should be credited."""
        assert "ffmpeg" in about_html.lower() or "FFmpeg" in about_html
        assert "SQLCipher" in about_html or "sqlcipher" in about_html
        assert "Flask" in about_html

    def test_has_version(self, about_html):
        """Version number should be displayed."""
        assert "version" in about_html.lower()

    def test_has_github_link(self, about_html):
        assert "github.com" in about_html

    def test_no_innerhtml_in_js(self, about_html):
        """Security: no innerHTML in inline scripts."""
        assert "innerHTML" not in about_html


class TestAboutLinkInHelp:
    """Verify Help page links to About."""

    @pytest.fixture
    def help_html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "help.html")
        with open(path) as f:
            return f.read()

    def test_help_links_to_about(self, help_html):
        assert "about.html" in help_html
