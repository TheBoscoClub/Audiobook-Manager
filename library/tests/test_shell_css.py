"""Verify shell.css exists and contains required layout rules."""

from pathlib import Path

SHELL_CSS = Path(__file__).parent.parent / "web-v2" / "css" / "shell.css"


class TestShellCSS:
    def test_shell_css_exists(self):
        assert SHELL_CSS.exists(), "shell.css must exist in web-v2/css/"

    def test_iframe_fills_viewport(self):
        content = SHELL_CSS.read_text()
        assert "#content-frame" in content

    def test_player_bar_flex_layout(self):
        """Player bar uses flexbox layout (not fixed positioning) to avoid mobile clipping."""
        content = SHELL_CSS.read_text()
        assert "#shell-player" in content
        assert "flex-shrink: 0" in content

    def test_responsive_mobile(self):
        """Shell CSS should handle mobile viewports."""
        content = SHELL_CSS.read_text()
        assert "@media" in content

    def test_uses_theme_colors(self):
        """Shell CSS must use the same theme as the rest of the app."""
        content = SHELL_CSS.read_text()
        assert "--deep-burgundy" in content
        assert "--gold" in content
        assert "--parchment" in content
