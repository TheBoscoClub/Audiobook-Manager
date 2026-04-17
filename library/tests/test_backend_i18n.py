"""Coverage tests for ``library.backend.i18n``.

Covers the locale detection priority chain (query param → X-Locale header
→ Accept-Language parsing with language-only fallback → default), catalog
loading (path-traversal guard, missing file, default fallback merging),
the ``t()`` helper, and the cache-reset hook.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from backend import i18n as mod


@pytest.fixture
def clear_catalog_cache():
    """Each test starts with a fresh LRU cache so stubbed fixtures take effect."""
    mod.reload_catalogs()
    yield
    mod.reload_catalogs()


@pytest.fixture
def tmp_locales(tmp_path: Path, monkeypatch, clear_catalog_cache):
    """Redirect the i18n module to a throwaway locales directory so we
    control the catalog contents precisely. Also lock the default/supported
    lists so tests don't depend on real JSON on disk."""
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "en.json").write_text(
        json.dumps({"greeting": "Hello", "shared": "English text"})
    )
    (locales_dir / "zh-Hans.json").write_text(
        json.dumps({"greeting": "你好"})  # shared key intentionally missing
    )
    monkeypatch.setattr(mod, "_LOCALES_DIR", locales_dir)
    monkeypatch.setattr(mod, "DEFAULT_LOCALE", "en")
    monkeypatch.setattr(mod, "SUPPORTED_LOCALES", {"en", "zh-Hans"})
    mod.reload_catalogs()  # drop anything cached from the real dir
    return locales_dir


# ── _load_catalog ────────────────────────────────────────────────────


class TestLoadCatalog:
    def test_rejects_path_traversal_input(self, tmp_locales):
        # Slashes, dots, etc. are rejected by the regex guard.
        assert mod._load_catalog("../../etc/passwd") == {}

    def test_missing_file_returns_empty_dict(self, tmp_locales):
        # Locale name is valid but the JSON doesn't exist.
        assert mod._load_catalog("fr") == {}

    def test_valid_locale_returns_parsed_json(self, tmp_locales):
        catalog = mod._load_catalog("en")
        assert catalog["greeting"] == "Hello"

    def test_results_are_cached(self, tmp_locales):
        info_before = mod._load_catalog.cache_info()
        mod._load_catalog("en")
        mod._load_catalog("en")
        info_after = mod._load_catalog.cache_info()
        # Second call must be a cache hit.
        assert info_after.hits > info_before.hits


# ── reload_catalogs ──────────────────────────────────────────────────


class TestReloadCatalogs:
    def test_cache_clear_drops_entries(self, tmp_locales):
        mod._load_catalog("en")
        assert mod._load_catalog.cache_info().currsize >= 1
        mod.reload_catalogs()
        assert mod._load_catalog.cache_info().currsize == 0


# ── get_catalog ──────────────────────────────────────────────────────


class TestGetCatalog:
    def test_unsupported_falls_back_to_default(self, tmp_locales):
        # 'fr' is not in SUPPORTED_LOCALES → served as English.
        catalog = mod.get_catalog("fr")
        assert catalog["greeting"] == "Hello"

    def test_default_catalog_returned_directly(self, tmp_locales):
        catalog = mod.get_catalog("en")
        # Default returns the raw catalog — no merge branch taken.
        assert catalog == {"greeting": "Hello", "shared": "English text"}

    def test_non_default_merges_missing_keys_from_default(self, tmp_locales):
        catalog = mod.get_catalog("zh-Hans")
        # zh-Hans has its own greeting — translation wins.
        assert catalog["greeting"] == "你好"
        # zh-Hans has no 'shared' — falls through to English.
        assert catalog["shared"] == "English text"


# ── t() helper ───────────────────────────────────────────────────────


class TestTHelper:
    def test_t_returns_translation_when_key_exists(self, tmp_locales):
        assert mod.t("greeting", locale="zh-Hans") == "你好"

    def test_t_falls_back_to_default_when_key_missing(self, tmp_locales):
        # 'shared' is not in zh-Hans → merged from English default.
        assert mod.t("shared", locale="zh-Hans") == "English text"

    def test_t_returns_key_itself_when_no_translation(self, tmp_locales):
        # Unknown key — catalog.get returns the key by default.
        assert mod.t("missing.key.xyz", locale="en") == "missing.key.xyz"

    def test_t_uses_get_locale_when_locale_is_none(self, tmp_locales):
        app = Flask(__name__)
        # Without a request context, get_locale() would blow up — but we
        # test the locale=None branch via a test request context.
        with app.test_request_context("/?locale=zh-Hans"):
            assert mod.t("greeting") == "你好"


# ── get_locale ───────────────────────────────────────────────────────


class TestGetLocale:
    @pytest.fixture
    def app(self):
        return Flask(__name__)

    def test_query_param_wins(self, tmp_locales, app):
        with app.test_request_context("/?locale=zh-Hans"):
            assert mod.get_locale() == "zh-Hans"

    def test_query_param_unsupported_falls_through_to_default(self, tmp_locales, app):
        # 'xx' is not in SUPPORTED_LOCALES — must fall through past the
        # query-param branch. With no other headers, the Accept-Language
        # loop skips empty fragments and returns DEFAULT_LOCALE.
        with app.test_request_context("/?locale=xx"):
            assert mod.get_locale() == "en"

    def test_x_locale_header_wins_when_no_query(self, tmp_locales, app):
        with app.test_request_context("/", headers={"X-Locale": "zh-Hans"}):
            assert mod.get_locale() == "zh-Hans"

    def test_x_locale_header_unsupported_falls_through_to_default(self, tmp_locales, app):
        with app.test_request_context("/", headers={"X-Locale": "xx"}):
            assert mod.get_locale() == "en"

    def test_accept_language_exact_match(self, tmp_locales, app):
        with app.test_request_context("/", headers={"Accept-Language": "zh-Hans,en;q=0.9"}):
            assert mod.get_locale() == "zh-Hans"

    def test_accept_language_language_only_fallback(self, tmp_locales, app):
        # 'zh-CN' is not supported, but 'zh' prefix matches 'zh-Hans'.
        with app.test_request_context("/", headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}):
            assert mod.get_locale() == "zh-Hans"

    def test_accept_language_none_match_uses_default(self, tmp_locales, app):
        with app.test_request_context("/", headers={"Accept-Language": "fr-FR,es;q=0.5"}):
            # No prefix match for fr or es → default.
            assert mod.get_locale() == "en"

    def test_empty_request_returns_default(self, tmp_locales, app):
        # Empty Accept-Language → empty tag → continue → fall through to
        # DEFAULT_LOCALE. This confirms the `if not tag` guard works.
        with app.test_request_context("/"):
            assert mod.get_locale() == "en"

    def test_accept_language_with_only_empty_tags_returns_default(self, tmp_locales, app):
        # Pathological header: just commas and whitespace. Every tag is
        # empty, so the loop short-circuits every iteration.
        with app.test_request_context("/", headers={"Accept-Language": ", ,,"}):
            assert mod.get_locale() == "en"

    def test_accept_language_degenerate_tag_skipped(self, tmp_locales, app):
        # "-CN" has empty lang portion — inner guard skips it. Then the
        # next tag ("en") is an exact match.
        with app.test_request_context("/", headers={"Accept-Language": "-CN,en"}):
            assert mod.get_locale() == "en"

    def test_header_priority_over_accept_language(self, tmp_locales, app):
        # X-Locale wins over Accept-Language.
        with app.test_request_context(
            "/",
            headers={
                "X-Locale": "zh-Hans",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ):
            assert mod.get_locale() == "zh-Hans"
