"""Tests for maintenance banner file structure."""

from pathlib import Path

# Resolve project root from test file location (library/tests/ -> project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_banner_css_exists():
    """Verify maintenance-banner.css was created."""
    with open(_PROJECT_ROOT / "library/web-v2/css/maintenance-banner.css") as f:
        content = f.read()
    assert ".maintenance-indicator" in content
    assert ".maintenance-panel" in content
    assert ".knife-switch" in content
    assert "@keyframes maintenance-pulse" in content


def test_banner_js_exists():
    """Verify maintenance-banner.js was created with safe DOM methods."""
    with open(_PROJECT_ROOT / "library/web-v2/js/maintenance-banner.js") as f:
        content = f.read()
    assert "maintenance-announce" in content
    assert "createElement" in content
    assert "textContent" in content
    assert "innerHTML" not in content


def test_shell_html_includes_banner():
    """Verify shell.html includes banner CSS and JS."""
    with open(_PROJECT_ROOT / "library/web-v2/shell.html") as f:
        content = f.read()
    assert "maintenance-banner.css" in content
    assert "maintenance-banner.js" in content


def test_banner_js_no_innerhtml():
    """Verify banner JS uses safe DOM methods exclusively."""
    with open(_PROJECT_ROOT / "library/web-v2/js/maintenance-banner.js") as f:
        content = f.read()
    assert "innerHTML" not in content, (
        "Must use createElement/textContent, not innerHTML"
    )
