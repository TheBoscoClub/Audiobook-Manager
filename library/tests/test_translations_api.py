"""
Tests for the audiobook translations API blueprint.

Covers CRUD endpoints for per-locale metadata translations, the
on-demand / batch / by-locale caching endpoints, and the pure helper
functions used internally (payload normalization, hashing, and the
small mapping helpers used by the batch translator).

DeepL-backed paths are exercised via the DEEPL_API_KEY=None branch,
which short-circuits cleanly without making real API calls.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from backend.api_modular import translations as tr
from backend.api_modular.translations import (
    _fetch_cached_string_translations,
    _hash_source,
    _normalize_strings_payload,
    _parse_on_demand_ids,
    _sanitize_log,
    _translate_batch_field_with_map,
    _validate_batch_request,
    _validate_on_demand_request,
    _validate_translate_strings_request,
)


# ── Pure helpers ──


@pytest.fixture
def _app_context(flask_app):
    """Push a Flask app context so helpers that call jsonify() work."""
    with flask_app.app_context():
        yield


class TestSanitizeLog:
    def test_plain(self):
        assert _sanitize_log("hello") == "hello"

    def test_strips_newlines(self):
        assert _sanitize_log("a\nb") == "a\\nb"

    def test_strips_carriage_returns(self):
        assert _sanitize_log("a\rb") == "a\\rb"

    def test_coerces_non_string(self):
        assert _sanitize_log(42) == "42"


class TestHashSource:
    def test_stable(self):
        a = _hash_source("hello")
        b = _hash_source("hello")
        assert a == b

    def test_differs_for_different_input(self):
        assert _hash_source("hello") != _hash_source("world")

    def test_length_is_16_hex(self):
        h = _hash_source("any string")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestNormalizeStringsPayload:
    def test_strips_and_dedupes(self):
        result = _normalize_strings_payload(["hello", "  hello  ", "world"])
        # After strip "hello" appears twice but dedupes by hash
        assert len(result) == 2

    def test_skips_non_strings(self):
        assert _normalize_strings_payload([None, 42, {}, "ok"]) == {
            _hash_source("ok"): "ok"
        }

    def test_skips_overlong_strings(self):
        long = "A" * 1001
        assert _normalize_strings_payload([long, "ok"]) == {_hash_source("ok"): "ok"}

    def test_caps_at_200(self):
        inputs = [f"s{i}" for i in range(250)]
        result = _normalize_strings_payload(inputs)
        assert len(result) == 200

    def test_empty_input(self):
        assert _normalize_strings_payload([]) == {}


class TestTranslateBatchFieldWithMap:
    def test_passthrough_when_all_empty(self):
        translator = MagicMock()
        translator.translate.return_value = []
        result = _translate_batch_field_with_map(translator, ["", "", ""], "zh-Hans")
        assert result == ["", "", ""]
        translator.translate.assert_not_called()

    def test_translates_non_empty(self):
        translator = MagicMock()
        translator.translate.return_value = ["你好", "世界"]
        result = _translate_batch_field_with_map(
            translator, ["hello", "", "world"], "zh-Hans"
        )
        assert result == ["你好", "", "世界"]

    def test_falls_back_when_translator_returns_less(self):
        translator = MagicMock()
        translator.translate.return_value = []  # DeepL returned nothing
        result = _translate_batch_field_with_map(translator, ["hello"], "zh-Hans")
        assert result == ["hello"]


class TestValidateTranslateStringsRequest:
    def test_missing_body(self, _app_context):
        locale, strings, err = _validate_translate_strings_request(None)
        assert locale is None
        assert err is not None

    def test_missing_locale(self, _app_context):
        locale, strings, err = _validate_translate_strings_request({})
        assert err is not None

    def test_english_short_circuits(self, _app_context):
        locale, strings, err = _validate_translate_strings_request({"locale": "en"})
        # err is a response, locale/strings are None
        assert err is not None
        assert locale is None

    def test_strings_not_list(self, _app_context):
        locale, strings, err = _validate_translate_strings_request(
            {"locale": "zh-Hans", "strings": "not a list"}
        )
        assert err is not None

    def test_valid_request(self, _app_context):
        locale, strings, err = _validate_translate_strings_request(
            {"locale": "zh-Hans", "strings": ["a", "b"]}
        )
        assert err is None
        assert locale == "zh-Hans"
        assert strings == ["a", "b"]


class TestParseOnDemandIds:
    def test_valid_ints(self):
        ids, err = _parse_on_demand_ids({"audiobook_ids": [1, 2, 3]})
        assert err is None
        assert ids == [1, 2, 3]

    def test_string_ints_coerced(self):
        ids, err = _parse_on_demand_ids({"audiobook_ids": ["1", "2"]})
        assert err is None
        assert ids == [1, 2]

    def test_invalid_ids(self, _app_context):
        ids, err = _parse_on_demand_ids({"audiobook_ids": ["abc"]})
        assert err is not None


class TestValidateOnDemandRequest:
    def test_missing_body(self, _app_context):
        locale, ids, err = _validate_on_demand_request(None)
        assert err is not None

    def test_missing_locale(self, _app_context):
        locale, ids, err = _validate_on_demand_request({"audiobook_ids": [1]})
        assert err is not None

    def test_missing_ids(self, _app_context):
        locale, ids, err = _validate_on_demand_request({"locale": "zh-Hans"})
        assert err is not None

    def test_english_short_circuits(self, _app_context):
        locale, ids, err = _validate_on_demand_request(
            {"locale": "en", "audiobook_ids": [1]}
        )
        assert err is not None
        assert locale is None

    def test_valid(self, _app_context):
        locale, ids, err = _validate_on_demand_request(
            {"locale": "zh-Hans", "audiobook_ids": [1, 2]}
        )
        assert err is None
        assert locale == "zh-Hans"
        assert ids == [1, 2]


class TestValidateBatchRequest:
    def test_missing_body(self, _app_context):
        locale, ids, err = _validate_batch_request(None)
        assert err is not None

    def test_missing_locale(self, _app_context):
        locale, ids, err = _validate_batch_request({})
        assert err is not None

    def test_wrong_provider(self, _app_context):
        locale, ids, err = _validate_batch_request(
            {"locale": "zh-Hans", "provider": "google"}
        )
        assert err is not None

    def test_empty_ids_list(self, _app_context):
        locale, ids, err = _validate_batch_request(
            {"locale": "zh-Hans", "audiobook_ids": []}
        )
        assert err is not None

    def test_ids_all_string_accepted(self, _app_context):
        locale, ids, err = _validate_batch_request(
            {"locale": "zh-Hans", "audiobook_ids": "all"}
        )
        assert err is None
        assert ids is None  # None = "all"
        assert locale == "zh-Hans"

    def test_ids_bad_string(self, _app_context):
        locale, ids, err = _validate_batch_request(
            {"locale": "zh-Hans", "audiobook_ids": "some-other-string"}
        )
        assert err is not None

    def test_ids_non_integer(self, _app_context):
        locale, ids, err = _validate_batch_request(
            {"locale": "zh-Hans", "audiobook_ids": ["abc"]}
        )
        assert err is not None

    def test_valid(self):
        locale, ids, err = _validate_batch_request(
            {"locale": "zh-Hans", "audiobook_ids": [1, 2]}
        )
        assert err is None
        assert ids == {1, 2}


# ── HTTP endpoint tests ──


@pytest.fixture
def translations_db(flask_app, session_temp_dir):
    """Ensure translations DB is clean and has seed audiobooks before each test.

    Re-binds ``tr._db_path`` to the session DB because other test modules
    (e.g. ``test_enriched_api.py``) spin up their own Flask app with a
    different DB and overwrite the module-level global.
    """
    db_path = session_temp_dir / "test_audiobooks.db"
    assert db_path.exists(), f"session DB missing: {db_path}"
    tr._db_path = db_path

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM audiobook_translations")
    conn.execute("DELETE FROM collection_translations")
    conn.execute("DELETE FROM string_translations")
    # Seed a pair of test audiobooks for upsert/CRUD tests (idempotent)
    conn.execute("DELETE FROM audiobooks WHERE id IN (1, 2)")
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type) "
        "VALUES (1, 'Test Book 1', 'Test Author', '/tmp/test1.opus', 'opus', 10.0, 'Product')"
    )
    conn.execute(
        "INSERT INTO audiobooks (id, title, author, file_path, format, duration_hours, content_type) "
        "VALUES (2, 'Test Book 2', 'Test Author', '/tmp/test2.opus', 'opus', 12.0, 'Product')"
    )
    conn.commit()
    conn.close()

    yield db_path

    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM audiobook_translations")
    conn.execute("DELETE FROM collection_translations")
    conn.execute("DELETE FROM string_translations")
    conn.execute("DELETE FROM audiobooks WHERE id IN (1, 2)")
    conn.commit()
    conn.close()


class TestGetBookTranslations:
    def test_empty_returns_empty_list(self, app_client, translations_db):
        resp = app_client.get("/api/audiobooks/1/translations")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_translations_for_book(self, app_client, translations_db):
        conn = sqlite3.connect(str(translations_db))
        conn.execute(
            "INSERT INTO audiobook_translations "
            "(audiobook_id, locale, title, author_display) "
            "VALUES (1, 'zh-Hans', '魔戒', '托尔金')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translations")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 1
        assert body[0]["title"] == "魔戒"


class TestGetSingleTranslation:
    def test_not_found(self, app_client, translations_db):
        resp = app_client.get("/api/audiobooks/1/translations/zh-Hans")
        assert resp.status_code == 404

    def test_returns_single_locale(self, app_client, translations_db):
        conn = sqlite3.connect(str(translations_db))
        conn.execute(
            "INSERT INTO audiobook_translations "
            "(audiobook_id, locale, title) VALUES (1, 'ja', '指輪物語')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/audiobooks/1/translations/ja")
        assert resp.status_code == 200
        assert resp.get_json()["title"] == "指輪物語"


class TestUpsertTranslation:
    def test_missing_locale_400(self, app_client, translations_db):
        resp = app_client.post("/api/audiobooks/1/translations", json={})
        assert resp.status_code == 400

    def test_book_not_found_404(self, app_client, translations_db):
        resp = app_client.post(
            "/api/audiobooks/99999/translations",
            json={"locale": "zh-Hans", "title": "foo"},
        )
        assert resp.status_code == 404

    def test_create_translation(self, app_client, translations_db):
        resp = app_client.post(
            "/api/audiobooks/1/translations",
            json={
                "locale": "zh-Hans",
                "title": "戒指",
                "author_display": "托尔金",
                "description": "描述",
            },
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["title"] == "戒指"
        assert body["pinyin_sort"] is not None  # zh locale triggers pinyin

    def test_update_translation_upsert(self, app_client, translations_db):
        # Insert first
        app_client.post(
            "/api/audiobooks/1/translations",
            json={"locale": "zh-Hans", "title": "old"},
        )
        # Then overwrite
        resp = app_client.post(
            "/api/audiobooks/1/translations",
            json={"locale": "zh-Hans", "title": "new"},
        )
        assert resp.status_code == 201
        assert resp.get_json()["title"] == "new"

    def test_non_zh_locale_no_pinyin(self, app_client, translations_db):
        resp = app_client.post(
            "/api/audiobooks/1/translations",
            json={"locale": "ja", "title": "日本語"},
        )
        body = resp.get_json()
        assert body["pinyin_sort"] is None


class TestDeleteTranslation:
    def test_not_found(self, app_client, translations_db):
        resp = app_client.delete("/api/audiobooks/1/translations/zh-Hans")
        assert resp.status_code == 404

    def test_delete_succeeds(self, app_client, translations_db):
        conn = sqlite3.connect(str(translations_db))
        conn.execute(
            "INSERT INTO audiobook_translations "
            "(audiobook_id, locale, title) VALUES (1, 'zh-Hans', 't')"
        )
        conn.commit()
        conn.close()

        resp = app_client.delete("/api/audiobooks/1/translations/zh-Hans")
        assert resp.status_code == 200
        assert "deleted" in resp.get_json()["message"].lower()

        # Verify actually gone
        resp2 = app_client.get("/api/audiobooks/1/translations/zh-Hans")
        assert resp2.status_code == 404


class TestGetTranslationsByLocale:
    def test_english_short_circuits(self, app_client, translations_db):
        resp = app_client.get("/api/translations/by-locale/en")
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_returns_cached(self, app_client, translations_db):
        conn = sqlite3.connect(str(translations_db))
        conn.execute(
            "INSERT INTO audiobook_translations "
            "(audiobook_id, locale, title, author_display, description) "
            "VALUES (1, 'zh-Hans', '戒指', '托尔金', '描述')"
        )
        conn.commit()
        conn.close()

        resp = app_client.get("/api/translations/by-locale/zh-Hans")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "1" in body
        assert body["1"]["title"] == "戒指"

    def test_ids_param_with_no_deepl_key_returns_cached_only(
        self, app_client, translations_db
    ):
        """When DEEPL_API_KEY is missing, missing ids should gracefully skip."""
        with patch("localization.config.DEEPL_API_KEY", None):
            resp = app_client.get("/api/translations/by-locale/zh-Hans?ids=1,2,3")
        assert resp.status_code == 200

    def test_ids_param_invalid_format_ignored(self, app_client, translations_db):
        resp = app_client.get("/api/translations/by-locale/zh-Hans?ids=notints,abc")
        assert resp.status_code == 200
        # Body may be empty since the invalid IDs couldn't be parsed
        assert resp.get_json() == {}


class TestTranslateStringsEndpoint:
    def test_missing_locale_400(self, app_client, translations_db):
        resp = app_client.post("/api/translations/strings", json={})
        assert resp.status_code == 400

    def test_english_returns_empty(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/strings", json={"locale": "en", "strings": ["hi"]}
        )
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_strings_not_list_400(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/strings",
            json={"locale": "zh-Hans", "strings": "not-a-list"},
        )
        assert resp.status_code == 400

    def test_empty_strings_returns_empty(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/strings",
            json={"locale": "zh-Hans", "strings": []},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_returns_cached_hits(self, app_client, translations_db):
        conn = sqlite3.connect(str(translations_db))
        hash_key = _hash_source("hello")
        conn.execute(
            "INSERT INTO string_translations "
            "(source_hash, locale, source, translation, translator) "
            "VALUES (?, 'zh-Hans', 'hello', '你好', 'deepl')",
            (hash_key,),
        )
        conn.commit()
        conn.close()

        with patch("localization.config.DEEPL_API_KEY", None):
            resp = app_client.post(
                "/api/translations/strings",
                json={"locale": "zh-Hans", "strings": ["hello", "world"]},
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get(hash_key) == "你好"


class TestOnDemandTranslate:
    def test_missing_locale_400(self, app_client, translations_db):
        resp = app_client.post("/api/translations/on-demand", json={})
        assert resp.status_code == 400

    def test_missing_ids_400(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/on-demand", json={"locale": "zh-Hans"}
        )
        assert resp.status_code == 400

    def test_invalid_ids_400(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/on-demand",
            json={"locale": "zh-Hans", "audiobook_ids": ["abc"]},
        )
        assert resp.status_code == 400

    def test_english_returns_empty(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/on-demand",
            json={"locale": "en", "audiobook_ids": [1]},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_returns_cached_when_no_api_key(self, app_client, translations_db):
        conn = sqlite3.connect(str(translations_db))
        conn.execute(
            "INSERT INTO audiobook_translations "
            "(audiobook_id, locale, title, author_display) "
            "VALUES (1, 'zh-Hans', '戒指', '托尔金')"
        )
        conn.commit()
        conn.close()

        with patch("localization.config.DEEPL_API_KEY", None):
            resp = app_client.post(
                "/api/translations/on-demand",
                json={"locale": "zh-Hans", "audiobook_ids": [1, 2]},
            )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "1" in body


class TestBatchTranslate:
    def test_missing_locale_400(self, app_client, translations_db):
        resp = app_client.post("/api/translations/batch", json={})
        assert resp.status_code == 400

    def test_wrong_provider_400(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/batch",
            json={"locale": "zh-Hans", "provider": "google", "audiobook_ids": "all"},
        )
        assert resp.status_code == 400

    def test_empty_ids_400(self, app_client, translations_db):
        resp = app_client.post(
            "/api/translations/batch",
            json={"locale": "zh-Hans", "audiobook_ids": []},
        )
        assert resp.status_code == 400

    def test_no_api_key_returns_503(self, app_client, translations_db):
        with patch("localization.config.DEEPL_API_KEY", None):
            resp = app_client.post(
                "/api/translations/batch",
                json={"locale": "zh-Hans", "audiobook_ids": [1]},
            )
        assert resp.status_code == 503


class TestCollectionTranslations:
    def test_english_empty(self, app_client, translations_db):
        resp = app_client.get("/api/translations/collections/en")
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_non_english_returns_cached(self, app_client, translations_db):
        # Works against whatever dynamic collections exist. Without DeepL,
        # missing names just won't be in the response.
        with patch("localization.config.DEEPL_API_KEY", None):
            resp = app_client.get("/api/translations/collections/zh-Hans")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), dict)


class TestFetchCachedStringTranslations:
    """Directly exercise the internal helper used by /strings."""

    def test_fetches_cached_by_hash(self, flask_app, translations_db):
        conn = sqlite3.connect(str(translations_db))
        h = _hash_source("hello")
        conn.execute(
            "INSERT INTO string_translations "
            "(source_hash, locale, source, translation, translator) "
            "VALUES (?, 'zh-Hans', 'hello', '你好', 'deepl')",
            (h,),
        )
        conn.row_factory = sqlite3.Row
        result = _fetch_cached_string_translations(conn, "zh-Hans", {h: "hello"})
        conn.close()
        assert result == {h: "你好"}
