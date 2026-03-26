"""
Tests for the tutorial engine (tutorial.js) and tutorial CSS.

Static analysis of JavaScript class structure, security requirements,
step definitions, and CSS overlay/tooltip styling.
"""

from pathlib import Path

LIBRARY_DIR = Path(__file__).parent.parent
WEB_DIR = LIBRARY_DIR / "web-v2"
CSS_DIR = WEB_DIR / "css"
JS_DIR = WEB_DIR / "js"


class TestTutorialJS:
    """Test tutorial.js structure and security."""

    def test_tutorial_js_exists(self):
        """tutorial.js must exist in web-v2/js/."""
        assert (JS_DIR / "tutorial.js").exists(), "tutorial.js should exist"

    def test_no_unsafe_html_assignment(self):
        """tutorial.js must NOT use .inner + HTML property (XSS risk)."""
        content = (JS_DIR / "tutorial.js").read_text()
        # Check for property access pattern, not just the word in comments
        unsafe_prop = ".inner" + "HTML"
        assert unsafe_prop not in content, (
            "tutorial.js must not use unsafe HTML property — use safe DOM methods"
        )

    def test_class_defined(self):
        """tutorial.js must define a LibraryTutorial class."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "class LibraryTutorial" in content, (
            "tutorial.js should define class LibraryTutorial"
        )

    def test_has_start_method(self):
        """LibraryTutorial must have a start() method."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "start()" in content, "LibraryTutorial should have start() method"

    def test_has_end_method(self):
        """LibraryTutorial must have an end() method."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "end()" in content, "LibraryTutorial should have end() method"

    def test_has_goto_method(self):
        """LibraryTutorial must have a goTo() method."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "goTo(" in content, "LibraryTutorial should have goTo() method"

    def test_step_count(self):
        """Tutorial must define at least 10 steps."""
        content = (JS_DIR / "tutorial.js").read_text()
        target_count = content.count("target:")
        assert target_count >= 10, (
            f"Tutorial should have at least 10 steps, found {target_count}"
        )

    def test_auto_start_on_url_param(self):
        """Tutorial must check for ?tutorial=1 URL parameter."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "tutorial" in content, "Should check for tutorial URL parameter"
        assert "URLSearchParams" in content, "Should use URLSearchParams"

    def test_safe_dom_methods(self):
        """Tutorial must use createElement and textContent (safe DOM)."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "createElement" in content, "Should use document.createElement"
        assert "textContent" in content, "Should use textContent (not innerHTML)"

    def test_optional_steps_supported(self):
        """Tutorial must support optional steps (skipped if target not found)."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "optional:" in content or "optional :" in content, (
            "Should support optional: true pattern for skippable steps"
        )

    def test_fallback_text_supported(self):
        """Tutorial must support fallback text for hidden elements."""
        content = (JS_DIR / "tutorial.js").read_text()
        assert "fallback:" in content or "fallback :" in content, (
            "Should support fallback: text for elements not visible"
        )


class TestTutorialCSS:
    """Test tutorial.css structure and classes."""

    def test_tutorial_css_exists(self):
        """tutorial.css must exist in web-v2/css/."""
        assert (CSS_DIR / "tutorial.css").exists(), "tutorial.css should exist"

    def test_overlay_class(self):
        """.tutorial-overlay must be defined."""
        content = (CSS_DIR / "tutorial.css").read_text()
        assert ".tutorial-overlay" in content, (
            "tutorial.css should define .tutorial-overlay"
        )

    def test_tooltip_class(self):
        """.tutorial-tooltip must be defined."""
        content = (CSS_DIR / "tutorial.css").read_text()
        assert ".tutorial-tooltip" in content, (
            "tutorial.css should define .tutorial-tooltip"
        )

    def test_highlight_class(self):
        """.tutorial-highlight must be defined."""
        content = (CSS_DIR / "tutorial.css").read_text()
        assert ".tutorial-highlight" in content, (
            "tutorial.css should define .tutorial-highlight"
        )

    def test_z_index_ordering(self):
        """Overlay z-index 9998, tooltip z-index 9999 (tooltip above overlay)."""
        content = (CSS_DIR / "tutorial.css").read_text()
        assert "z-index: 9998" in content, "Overlay should have z-index: 9998"
        assert "z-index: 9999" in content, "Tooltip should have z-index: 9999"

    def test_responsive_breakpoints(self):
        """tutorial.css must have 768px and 480px responsive breakpoints."""
        content = (CSS_DIR / "tutorial.css").read_text()
        assert "768px" in content, "tutorial.css should have 768px breakpoint"
        assert "480px" in content, "tutorial.css should have 480px breakpoint"
