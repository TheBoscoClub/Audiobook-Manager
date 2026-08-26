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
