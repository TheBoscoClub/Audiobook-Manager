"""
Regression tests for the chrome header styling fixes shipped under
Audiobook-Manager-bc0 (v8.3.10.3):

1. Locale `<select>` had lost its Art Deco theming because `appearance: none`
   was missing the Firefox vendor prefix and no custom dropdown arrow had
   been provided to replace the native one. The popped-open <option> menu
   also rendered with OS default white-on-black, clashing with the dark gold
   theme.
2. The "globe" icon was a featureless wireframe (a circle with a meridian +
   two longitude curves) that did not visually read as Earth. Replaced with
   a stylized continents SVG.

These are static-analysis tests over `library/web-v2/shell.html` and
`library/web-v2/css/i18n.css` — they lock the wiring so the fix cannot
silently regress.
"""

from pathlib import Path

LIBRARY_DIR = Path(__file__).parent.parent
WEB_DIR = LIBRARY_DIR / "web-v2"
CSS_DIR = WEB_DIR / "css"


def _locale_select_rule() -> str:
    """Return just the `.locale-switcher select { ... }` block from i18n.css.

    The CSS file has multiple .locale-switcher rules; we want the bare
    `select` declaration block (not :hover / :focus / option).
    """
    css = (CSS_DIR / "i18n.css").read_text()
    marker = ".locale-switcher select {"
    start = css.index(marker)
    end = css.index("}", start)
    return css[start : end + 1]


class TestLocaleSelectStyling:
    """Lock down the cross-browser Art Deco styling of the locale dropdown."""

    def test_locale_select_has_appearance_none_all_vendors(self):
        """All three appearance vendor prefixes must be present so Firefox,
        Safari/Chromium, and the spec-compliant property all strip the
        native chrome together."""
        rule = _locale_select_rule()
        assert "appearance: none" in rule, (
            "i18n.css `.locale-switcher select` must set `appearance: none` (spec-compliant)"
        )
        assert "-webkit-appearance: none" in rule, (
            "i18n.css `.locale-switcher select` must set "
            "`-webkit-appearance: none` (Safari/Chromium)"
        )
        assert "-moz-appearance: none" in rule, (
            "i18n.css `.locale-switcher select` must set `-moz-appearance: none` (Firefox)"
        )

    def test_locale_select_has_custom_arrow_background_image(self):
        """`appearance: none` strips the native dropdown arrow — a custom
        SVG arrow must be supplied via background-image so the control
        still reads as a dropdown."""
        rule = _locale_select_rule()
        assert "background-image:" in rule, (
            "i18n.css `.locale-switcher select` must declare a "
            "`background-image` for the custom dropdown arrow"
        )
        assert "data:image/svg+xml" in rule, (
            "The custom arrow must be an inline SVG data URI (single "
            "canonical asset, no external file)"
        )

    def test_locale_select_options_have_dark_bg(self):
        """The popped-open <option> menu is styled by the OS by default.
        A dedicated rule must set background + color on
        `.locale-switcher select option` so the dropdown menu doesn't
        clash with the dark gold Art Deco theme."""
        css = (CSS_DIR / "i18n.css").read_text()
        # The rule selector itself must exist
        assert ".locale-switcher select option" in css, (
            "i18n.css must have a `.locale-switcher select option` rule "
            "to style the popped-open dropdown menu"
        )
        # Locate the option-rule body and check it sets both properties
        marker = ".locale-switcher select option {"
        start = css.index(marker)
        end = css.index("}", start)
        option_rule = css[start : end + 1]
        assert "background" in option_rule, (
            "`.locale-switcher select option` must set a `background` so "
            "the OS dropdown popup matches the theme"
        )
        assert "color" in option_rule, (
            "`.locale-switcher select option` must set a `color` so "
            "option text is visible on the dark popup"
        )


class TestGlobeIconReplaced:
    """Lock down that the abstract wireframe globe was replaced with an
    Earth-with-continents icon. Detected by absence of the old wireframe
    pattern — circle + 3 specific path commands."""

    def test_globe_icon_replaced_in_shell_html(self):
        """The original wireframe globe (circle + meridian + 2 longitude
        curves) must no longer be the entire `.locale-icon` content. We
        assert the three identifying path commands from the old SVG are
        no longer all present together."""
        content = (WEB_DIR / "shell.html").read_text()
        # Old wireframe patterns — these were the three distinctive paths.
        old_meridian = 'd="M2 12h20"'
        old_long_right = 'd="M12 2c3 3.6 3 16.4 0 20"'
        old_long_left = 'd="M12 2c-3 3.6-3 16.4 0 20"'

        # The old icon was identified by ALL THREE of these path commands
        # appearing together. The replacement icon must drop at least one
        # (in practice, all three) so this combined fingerprint disappears.
        old_pattern_intact = (
            old_meridian in content and old_long_right in content and old_long_left in content
        )
        assert not old_pattern_intact, (
            "The old wireframe globe SVG (circle + meridian + 2 longitude "
            "curves) must be replaced with an Earth-with-continents icon. "
            "Found all three old path commands still present in shell.html."
        )

    def test_globe_icon_still_uses_currentcolor(self):
        """The replacement icon must still inherit the gold theme color
        via `currentColor` so the existing `.locale-icon` CSS color rule
        applies (no regression to a hard-coded fill)."""
        content = (WEB_DIR / "shell.html").read_text()
        # Find the .locale-icon SVG block
        marker = 'class="locale-icon"'
        start = content.index(marker)
        end = content.index("</svg>", start)
        svg_block = content[start:end]
        assert "currentColor" in svg_block, (
            "The replacement `.locale-icon` SVG must use `currentColor` so "
            "the gold theme colour from CSS still applies"
        )
