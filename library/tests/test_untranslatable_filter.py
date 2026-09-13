"""The reader-facing side of a title marked untranslatable.

Two separate audiences, deliberately:

* **Any reader** may filter untranslatable titles out of their view
  (``?hide_untranslatable=1``) and sees the explanatory note on the card.
  The audio plays perfectly well, so hiding is the reader's choice — which
  is why the filter is opt-in and not gated behind admin.
* **Only an admin** may MARK a title untranslatable (covered in
  ``test_translation_exclusions.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web-v2"
LOCALES = Path(__file__).resolve().parents[1] / "locales"


class TestListingFilter:
    """The API half — a plain query param on the public listing endpoint."""

    def test_filter_param_is_parsed_truthily(self):
        from backend.api_modular.audiobooks import _parse_query_params

        def parse(raw):
            args = type(
                "A", (), {"get": lambda _s, k, d="": {"hide_untranslatable": raw}.get(k, d)}
            )()
            return _parse_query_params(type("R", (), {"args": args})())["hide_untranslatable"]

        for raw, expected in [
            ("1", True),
            ("true", True),
            ("TRUE", True),
            ("yes", True),
            ("0", False),
            ("", False),
            ("no", False),
        ]:
            assert parse(raw) is expected, raw

    def test_filter_adds_a_where_clause_only_when_asked(self):
        from backend.api_modular.audiobooks import _build_filter_clauses

        off, _ = _build_filter_clauses({"hide_untranslatable": False})
        on, _ = _build_filter_clauses({"hide_untranslatable": True})
        assert not any("translation_excluded" in c for c in off)
        assert any("translation_excluded" in c for c in on)

    def test_clause_treats_null_as_not_excluded(self):
        """Rows predating migration 022 have NULL, which must not vanish."""
        from backend.api_modular.audiobooks import _build_filter_clauses

        on, _ = _build_filter_clauses({"hide_untranslatable": True})
        clause = next(c for c in on if "translation_excluded" in c)
        assert "COALESCE" in clause


class TestReaderFacingUi:
    """The UI half — present, localized, and not admin-gated."""

    @pytest.fixture(scope="class")
    def library_js(self):
        return (WEB / "js" / "library.js").read_text(encoding="utf-8")

    def test_filter_toggle_exists_in_the_library_markup(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        assert 'id="hide-untranslatable"' in html

    def test_filter_is_not_gated_behind_admin(self, library_js):
        """Regression guard: only MARKING is admin-only. If the toggle ever
        ends up inside an is_admin branch, this fails."""
        setup = library_js[
            library_js.index("setupUntranslatableFilter()") : library_js.index(
                "setupEventListeners()"
            )
        ]
        assert "is_admin" not in setup

    def test_card_note_is_scoped_to_non_english_views(self, library_js):
        assert "showUntranslatableNote" in library_js
        assert re.search(r"viewLocale\s*!==\s*[\"']en[\"']", library_js)

    def test_choice_persists_across_reloads(self, library_js):
        assert 'localStorage.setItem("hideUntranslatable"' in library_js
        assert 'localStorage.getItem("hideUntranslatable")' in library_js

    @pytest.mark.parametrize("locale", ["en", "zh-Hans"])
    def test_strings_exist_in_both_locales(self, locale):
        import json

        data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        for key in (
            "book.untranslatable",
            "book.untranslatableTip",
            "library.hideUntranslatable",
        ):
            assert data.get(key), f"{key} missing from {locale}"

    def test_chinese_note_is_actually_chinese(self):
        """The note's whole purpose is to speak to a reader who is not
        reading English — an untranslated string here would be the very
        failure it describes."""
        import json

        data = json.loads((LOCALES / "zh-Hans.json").read_text(encoding="utf-8"))
        assert re.search(r"[一-鿿]", data["book.untranslatable"])
        assert re.search(r"[一-鿿]", data["library.hideUntranslatable"])
