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

    def test_html_height_capped_at_100svh(self):
        """`<html>` height MUST be capped at 100svh.

        Regression guard for the iOS Chrome bottom-nav clip
        (Audiobook-Manager-g9f, Qing's iPhone 17 Pro Chrome 2026-04-25):
        on iOS Chrome both 100dvh and visualViewport.height include the
        area behind the persistent bottom nav, so without the 100svh cap
        the body extends behind the toolbar and the player's wrapped
        rows get clipped. Both prior attempted fixes (v8.3.6 100px,
        v8.3.8.8 200px on --player-height) operated on the wrong
        variable. Replacing this with 100dvh again is forbidden.
        """
        content = SHELL_CSS.read_text()
        # The exact `min(100svh, var(--app-height, 100svh))` formulation
        # is what guarantees the cap. Loosen this only with a comment
        # explaining why and a re-tested iOS Chrome screenshot.
        assert "min(100svh, var(--app-height, 100svh))" in content, (
            "html height must be capped via min(100svh, var(--app-height, 100svh)); "
            "see comment block on `html` in shell.css"
        )
        # Belt-and-braces: forbid the fallback regression to 100dvh.
        assert "var(--app-height, 100dvh)" not in content, (
            "100dvh fallback for --app-height is forbidden — aliases to "
            "100lvh on iOS Chrome and reintroduces the bottom-nav clip"
        )
