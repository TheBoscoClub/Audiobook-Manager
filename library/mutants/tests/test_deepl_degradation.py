"""Regression tests for Audiobook-Manager-64p.

A dead DeepL key used to make ``translate()`` return the English source text
with no exception, no flag and no error log, so a caller could not tell a real
translation from a silent give-up. These tests pin the fix:

  * degradation is ANNOUNCED (``degraded`` flag + counted + logged at ERROR)
  * callers that PERSIST results can demand ``strict=True`` and get a raise
  * the success path sets none of that

The 403 case is the real one: it is what a revoked key, or a Free key sent to
the Pro endpoint, actually returns.
"""

from __future__ import annotations

import logging

import pytest
import requests
from localization.translation.deepl_translate import (
    DeepLTranslator,
    TranslationUnavailableError,
)


def _fail(status: int):
    """Build a side effect that raises HTTPError carrying a response status."""
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} Client Error", response=resp)


class TestDegradationIsAnnounced:
    def test_dead_key_still_returns_source_but_flags_it(self, monkeypatch):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        out = t.translate(["The Hobbit"], "zh-Hans")

        # Behaviour preserved: live pages still degrade rather than break.
        assert out == ["The Hobbit"]
        # ...but it is no longer invisible.
        assert t.degraded is True
        assert t.degraded_texts == 1
        assert t._last_error == "HTTP 403"

    def test_failure_is_logged_at_error_level(self, monkeypatch, caplog):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        with caplog.at_level(logging.ERROR):
            t.translate(["The Hobbit"], "zh-Hans")

        blob = caplog.text
        assert "UNTRANSLATED" in blob, "operator must be told the text is not translated"
        assert "REJECTED the API key" in blob, "401/403 must name the cause"

    def test_counter_accumulates_across_calls(self, monkeypatch):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        t.translate(["a", "b"], "zh-Hans")
        t.translate(["c"], "zh-Hans")
        assert t.degraded_texts == 3


class TestStrictMode:
    def test_strict_raises_instead_of_passing_through(self, monkeypatch):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        with pytest.raises(TranslationUnavailableError, match="refusing to return"):
            t.translate(["The Hobbit"], "zh-Hans", strict=True)

    def test_translate_one_forwards_strict(self, monkeypatch):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        with pytest.raises(TranslationUnavailableError):
            t.translate_one("The Hobbit", "zh-Hans", strict=True)

    def test_non_strict_translate_one_still_degrades(self, monkeypatch):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        assert t.translate_one("The Hobbit", "zh-Hans") == "The Hobbit"
        assert t.degraded is True


class TestHealthyPathUnaffected:
    def test_success_sets_no_degradation(self, monkeypatch):
        t = DeepLTranslator(api_key="good-key:fx")

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"translations": [{"text": "《霍比特人》"}]}

        monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
        out = t.translate(["The Hobbit"], "zh-Hans")
        assert out == ["《霍比特人》"]
        assert t.degraded is False
        assert t.degraded_texts == 0

    def test_empty_input_is_not_degradation(self):
        t = DeepLTranslator(api_key="good-key:fx")
        assert t.translate([], "zh-Hans") == []
        assert t.degraded is False


class TestPersistingCallersAreStrict:
    """The whole point of 64p: things that write to disk must not pass English.

    Uses inspect.getsource on the IMPORTED module rather than a repo-relative
    path, so the assertion follows the code actually loaded and does not depend
    on pytest's working directory.
    """

    def test_pipeline_uses_strict(self):
        import inspect

        from localization import pipeline

        src = inspect.getsource(pipeline)
        assert src.count("strict=True") >= 2, (
            "both VTT-writing call sites in pipeline.py must pass strict=True"
        )

    def test_metadata_lookup_uses_strict(self):
        import inspect

        from localization.metadata import lookup

        src = inspect.getsource(lookup)
        assert "strict=True" in src, (
            "metadata lookup labels results source='deepl'; it must not pass English through"
        )


class TestLogInjection:
    """CodeQL py/log-injection #537/#538 — target_locale reaches logger.error
    from the API request, so a caller could embed CR/LF and forge log lines."""

    def test_newlines_stripped_from_locale_in_log(self, monkeypatch, caplog):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        with caplog.at_level(logging.ERROR):
            t.translate(["x"], "zh\nERROR root: injected line")

        for rec in caplog.records:
            assert "\n" not in rec.getMessage(), "a log record must not span lines"
        assert "injectedline" in caplog.text or "ERRORrootinjectedline" in caplog.text

    def test_legitimate_locale_survives_intact(self, monkeypatch, caplog):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        with caplog.at_level(logging.ERROR):
            t.translate(["x"], "zh-Hans")
        assert "zh-Hans" in caplog.text, "sanitising must not mangle a real locale"

    def test_strict_exception_message_is_sanitised(self, monkeypatch):
        t = DeepLTranslator(api_key="dead-key:fx")
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(403)))
        with pytest.raises(TranslationUnavailableError) as ei:
            t.translate(["x"], "zh\nforged", strict=True)
        assert "\n" not in str(ei.value)


class TestShortOrEmptyResponseIsDegradation:
    """A 200 with fewer translations than inputs is a failure, not a success.

    Audiobook-Manager-09z. `_call_deepl_api` returns None only on
    RequestException; on a 200 it returns `[t["text"] for t in
    result.get("translations", [])]`, so a missing/empty/truncated array
    yields [] or a short list. Before the fix that slipped past the
    `is None` gate, zip() stopped early, and `_fill_misses_with_source`
    wrote the ENGLISH SOURCE into the gaps with degraded=False and no
    raise — silently corrupting whatever the caller persisted.
    """

    def test_empty_array_degrades_instead_of_passing_english_through(self, monkeypatch):
        t = DeepLTranslator(api_key="k:fx")
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: [])
        out = t.translate(["The Hobbit"], "zh-Hans")

        assert out == ["The Hobbit"]  # non-strict still degrades gracefully
        assert t.degraded is True  # ...but it is announced
        assert t.degraded_texts == 1

    def test_empty_array_raises_under_strict(self, monkeypatch):
        t = DeepLTranslator(api_key="k:fx")
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: [])
        with pytest.raises(TranslationUnavailableError):
            t.translate(["The Hobbit"], "zh-Hans", strict=True)

    def test_short_array_raises_under_strict(self, monkeypatch):
        t = DeepLTranslator(api_key="k:fx")
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: ["\u9738\u7ea7"])
        with pytest.raises(TranslationUnavailableError):
            t.translate(["The Hobbit", "The Silmarillion"], "zh-Hans", strict=True)

    def test_short_response_is_named_in_the_error(self, monkeypatch):
        t = DeepLTranslator(api_key="k:fx")
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: [])
        t.translate(["The Hobbit"], "zh-Hans")
        assert "short response" in (t._last_error or "")

    def test_exact_length_match_is_still_a_success(self, monkeypatch):
        t = DeepLTranslator(api_key="k:fx")
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: ["\u9738\u7ea7"])
        out = t.translate(["The Hobbit"], "zh-Hans", strict=True)
        assert out == ["\u9738\u7ea7"]
        assert t.degraded is False


class TestStaleGlossaryIsSelfHealing:
    """A glossary DeepL will not accept must not degrade every translate forever.

    Audiobook-Manager-2s6 follow-up. `GlossaryManager.ensure()` trusts its
    cached id whenever the source hash matches, so an id minted under a
    different DeepL account is resent indefinitely. Production hit exactly
    this: every translate 404'd and wrote ENGLISH rows tagged translator=
    'deepl' while the cached id survived.
    """

    def _translator_with_glossary(self, monkeypatch, tmp_path):
        from localization.translation.quota import QuotaTracker

        t = DeepLTranslator(api_key="pro-key", db_path=tmp_path / "q.db")
        assert isinstance(t._tracker, QuotaTracker)
        t._tracker.set_glossary("stale-id-from-a-dead-account", "hash")
        t._glossary_id = "stale-id-from-a-dead-account"
        t._glossary_resolved = True
        return t

    def test_404_with_glossary_retries_without_it(self, monkeypatch, tmp_path):
        t = self._translator_with_glossary(monkeypatch, tmp_path)
        seen: list[dict] = []

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.append(json)
            if "glossary_id" in json:
                raise _fail(404)
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"translations":[{"text":"\\u4f60\\u597d"}]}'
            return resp

        monkeypatch.setattr("requests.post", fake_post)
        out = t.translate(["Hello"], "zh-Hans")

        assert len(seen) == 2, "no retry was attempted"
        assert "glossary_id" in seen[0] and "glossary_id" not in seen[1]
        assert out == ["你好"], "the retry's translation was not used"
        assert t.degraded is False

    def test_stale_glossary_id_is_cleared_from_the_db(self, monkeypatch, tmp_path):
        t = self._translator_with_glossary(monkeypatch, tmp_path)

        def fake_post(url, headers=None, json=None, timeout=None):
            if "glossary_id" in json:
                raise _fail(404)
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"translations":[{"text":"\\u4f60\\u597d"}]}'
            return resp

        monkeypatch.setattr("requests.post", fake_post)
        t.translate(["Hello"], "zh-Hans")

        cached_id, _ = t._tracker.get_glossary()
        assert not cached_id, f"stale glossary id survived: {cached_id!r}"

    def test_404_without_a_glossary_still_degrades(self, monkeypatch, tmp_path):
        """The retry path must not swallow a genuine 404."""
        t = DeepLTranslator(api_key="pro-key", db_path=tmp_path / "q.db")
        t._glossary_resolved = True  # no glossary
        monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(_fail(404)))
        out = t.translate(["Hello"], "zh-Hans")
        assert out == ["Hello"]
        assert t.degraded is True


class TestIdentityResultsAreNeverCached:
    """A 'translation' equal to its source must never enter the TM.

    Audiobook-Manager-xiy. 70 such rows accumulated during the April 2026
    outages. They survived deleting every audiobook_translations row, because
    the translation memory re-served them on the next request — a cache hit is
    never retried, so the poison is permanent and silent.
    """

    def test_identity_translation_is_not_stored(self, monkeypatch, tmp_path):
        t = DeepLTranslator(api_key="pro-key", db_path=tmp_path / "q.db")
        t._glossary_resolved = True
        stored: list = []
        monkeypatch.setattr(t, "_tm_store", lambda pairs, locale: stored.append(pairs))
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: ["Borne"])

        out = t.translate(["Borne"], "zh-Hans")

        assert out == ["Borne"], "the caller still receives what DeepL returned"
        assert stored == [[]], f"an identity result was cached: {stored}"

    def test_real_translations_are_still_stored(self, monkeypatch, tmp_path):
        t = DeepLTranslator(api_key="pro-key", db_path=tmp_path / "q.db")
        t._glossary_resolved = True
        stored: list = []
        monkeypatch.setattr(t, "_tm_store", lambda pairs, locale: stored.append(pairs))
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: ["霸级"])

        t.translate(["Overlord"], "zh-Hans")
        assert stored == [[("Overlord", "霸级")]]

    def test_mixed_batch_stores_only_the_real_one(self, monkeypatch, tmp_path):
        t = DeepLTranslator(api_key="pro-key", db_path=tmp_path / "q.db")
        t._glossary_resolved = True
        stored: list = []
        monkeypatch.setattr(t, "_tm_store", lambda pairs, locale: stored.append(pairs))
        monkeypatch.setattr(t, "_call_deepl_api", lambda payload: ["14", "霸级"])

        out = t.translate(["14", "Overlord"], "zh-Hans")
        assert out == ["14", "霸级"]
        assert stored == [[("Overlord", "霸级")]]
