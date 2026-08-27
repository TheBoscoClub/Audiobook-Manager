# library/tests/test_marquee.py
"""Tests for new books marquee."""

import os

import pytest


class TestMarqueeHTML:
    """Verify marquee structure in index.html."""

    @pytest.fixture
    def index_html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "index.html")
        with open(path) as f:
            return f.read()

    def test_marquee_container_exists(self, index_html):
        assert 'id="new-books-marquee"' in index_html

    def test_marquee_js_loaded(self, index_html):
        assert "marquee.js" in index_html

    def test_marquee_css_loaded(self, index_html):
        assert "marquee.css" in index_html


class TestMarqueeCSS:
    """Verify marquee CSS exists and has Art Deco neon styling."""

    @pytest.fixture
    def marquee_css(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "css", "marquee.css")
        with open(path) as f:
            return f.read()

    def test_marquee_file_exists(self, marquee_css):
        assert len(marquee_css) > 0

    def test_has_neon_glow(self, marquee_css):
        """Art Deco neon style should use text-shadow or box-shadow for glow."""
        assert "text-shadow" in marquee_css or "box-shadow" in marquee_css

    def test_has_animation(self, marquee_css):
        """Marquee should have scroll animation."""
        assert "@keyframes" in marquee_css or "animation" in marquee_css


class TestMarqueeJS:
    """Verify marquee.js structure."""

    @pytest.fixture
    def marquee_js(self):
        path = os.path.join(os.path.dirname(__file__), "..", "web-v2", "js", "marquee.js")
        with open(path) as f:
            return f.read()

    def test_marquee_file_exists(self, marquee_js):
        assert len(marquee_js) > 0

    def test_fetches_new_books(self, marquee_js):
        assert "/api/user/new-books" in marquee_js

    def test_dismiss_function(self, marquee_js):
        assert "dismiss" in marquee_js.lower()

    def test_no_innerhtml(self, marquee_js):
        """Security: no innerHTML usage."""
        assert "innerHTML" not in marquee_js
