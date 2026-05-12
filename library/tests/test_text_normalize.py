"""Tests for ``scanner.utils.text_normalize``.

Driven by the 2026-05-12 prod incident: HTML-entity-encoded text in the
``description`` column of audiobook 116208 (``&quot;An all-encompassing
treatise…``) collided with the library card's onclick attribute encoder and
silently broke playback for the affected row. The fix normalizes entities at
ingestion. These tests pin that contract.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scanner.utils.text_normalize import normalize_freetext


class TestNormalizeFreetext:
    def test_decodes_quot_entity(self):
        assert normalize_freetext("&quot;Hello&quot;") == '"Hello"'

    def test_decodes_nbsp_entity(self):
        # &nbsp; decodes to U+00A0 (non-breaking space)
        assert normalize_freetext("Foo&nbsp;Bar") == "Foo\xa0Bar"

    def test_decodes_amp_entity(self):
        assert normalize_freetext("Rock &amp; Roll") == "Rock & Roll"

    def test_decodes_numeric_entity(self):
        # &#34; is the numeric form of &quot;
        assert normalize_freetext("&#34;quoted&#34;") == '"quoted"'

    def test_decodes_hex_entity(self):
        assert normalize_freetext("&#x22;quoted&#x22;") == '"quoted"'

    def test_decodes_mixed_entities(self):
        raw = "&quot;An all-encompassing treatise&quot;&nbsp;&mdash; book"
        decoded = normalize_freetext(raw)
        assert "&quot;" not in decoded
        assert "&nbsp;" not in decoded
        assert "&mdash;" not in decoded
        assert decoded.startswith('"An all-encompassing')

    def test_idempotent(self):
        # Running twice on already-decoded text must not corrupt it.
        once = normalize_freetext("&quot;Hello&quot;")
        twice = normalize_freetext(once)
        assert once == twice == '"Hello"'

    def test_plain_text_passthrough(self):
        # Strings with no entities round-trip unchanged.
        assert normalize_freetext("Just plain text.") == "Just plain text."

    def test_none_passthrough(self):
        assert normalize_freetext(None) is None

    def test_non_string_passthrough(self):
        # Ints, dicts, lists are returned as-is — callers should not have to
        # type-guard before passing optional dict values.
        assert normalize_freetext(42) == 42
        assert normalize_freetext({"a": 1}) == {"a": 1}
        assert normalize_freetext(["x"]) == ["x"]

    def test_empty_string(self):
        assert normalize_freetext("") == ""

    def test_dawn_of_everything_regression(self):
        """Prod row 116208 — exact prefix that broke the onclick handler."""
        raw = (
            "&quot;An all-encompassing treatise on modern civilization, offering "
            "bold revisions to canonical understandings in sociology"
        )
        decoded = normalize_freetext(raw)
        # The browser-side bug fired because '&quot;' double-decoded to '"',
        # producing '""An' (empty string + identifier) in the onclick JS.
        # After normalization the literal entity is gone, so JSON.stringify
        # produces a properly escaped string and the attribute round-trips.
        assert decoded.startswith('"An all-encompassing')
        assert "&quot;" not in decoded
