"""Verify button spinners respect the hidden attribute.

Bug: .button-loading has display: inline-flex which overrides the HTML
hidden attribute (UA stylesheet specificity). Without an explicit
.button-loading[hidden] { display: none } rule, spinners are always
visible, confusing users who see them spinning before any action.
"""

from pathlib import Path

AUTH_CSS = Path(__file__).parent.parent / "web-v2" / "css" / "auth.css"


class TestSpinnerHidden:

    def test_button_loading_hidden_override_exists(self):
        """CSS must explicitly hide .button-loading[hidden]."""
        content = AUTH_CSS.read_text()
        assert ".button-loading[hidden]" in content, (
            ".button-loading[hidden] { display: none } is required because "
            ".button-loading { display: inline-flex } overrides the hidden attribute"
        )

    def test_button_loading_hidden_sets_display_none(self):
        content = AUTH_CSS.read_text()
        # Find the [hidden] rule and verify it sets display: none
        idx = content.find(".button-loading[hidden]")
        assert idx != -1
        block = content[idx:content.find("}", idx) + 1]
        assert "display: none" in block or "display:none" in block
