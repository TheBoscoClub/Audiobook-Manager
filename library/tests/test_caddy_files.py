"""Verify Caddy project files exist and are well-formed."""

from pathlib import Path

import pytest

CADDY_DIR = Path(__file__).resolve().parents[2] / "caddy"

# Skip entire module when running on deployed installation (no caddy/ in app tree)
pytestmark = pytest.mark.skipif(
    not CADDY_DIR.is_dir(), reason="caddy/ directory not present (deployed installation)"
)


def test_audiobooks_conf_exists():
    """Caddy config snippet must exist in project."""
    assert (CADDY_DIR / "audiobooks.conf").is_file()


def test_maintenance_html_exists():
    """Maintenance page must exist in project."""
    assert (CADDY_DIR / "maintenance.html").is_file()


def test_maintenance_html_has_health_polling():
    """Maintenance page must poll /api/system/health."""
    content = (CADDY_DIR / "maintenance.html").read_text()
    assert "/api/system/health" in content, "Must poll health endpoint"
    assert "location.reload()" in content, "Must reload on health success"


def test_maintenance_html_no_innerhtml():
    """Maintenance page must not use innerHTML."""
    content = (CADDY_DIR / "maintenance.html").read_text()
    assert "innerHTML" not in content, "Must not use innerHTML — use textContent or static HTML"


def test_maintenance_html_has_noscript_fallback():
    """Maintenance page must have meta refresh for no-JS browsers."""
    content = (CADDY_DIR / "maintenance.html").read_text()
    assert "meta http-equiv" in content.lower() or "noscript" in content.lower(), (
        "Must have no-JS fallback (meta refresh or noscript)"
    )
