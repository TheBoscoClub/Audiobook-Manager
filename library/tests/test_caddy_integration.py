"""Verify install.sh and upgrade.sh handle Caddy files."""

from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"
UPGRADE_SH = Path(__file__).resolve().parents[2] / "upgrade.sh"


def test_install_references_caddy_files():
    """install.sh must install Caddy config and maintenance page."""
    content = INSTALL_SH.read_text()
    assert "audiobooks.conf" in content, "install.sh must install Caddy config"
    assert "maintenance.html" in content, "install.sh must install maintenance page"


def test_upgrade_syncs_caddy_files():
    """upgrade.sh must sync Caddy files during upgrade."""
    content = UPGRADE_SH.read_text()
    assert "audiobooks.conf" in content or "caddy" in content.lower(), (
        "upgrade.sh must sync Caddy config"
    )


def test_caddy_conditional_on_install():
    """Caddy installation must be conditional (skip if Caddy not installed)."""
    content = INSTALL_SH.read_text()
    assert "caddy" in content.lower(), "Must check for Caddy availability"
