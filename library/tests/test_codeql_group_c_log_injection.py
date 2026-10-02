"""CodeQL py/log-injection triage, group C (Audiobook-Manager-o9s).

The one real finding in the group: ``VLLMTranslator.translate`` logged
``target_locale`` verbatim. That value comes from the unvalidated ``<locale>``
URL segment of ``/api/translations/by-locale/<locale>`` (only ``== "en"`` is
checked), so a caller could put CR/LF in the path and forge a log line.

These tests pin the sanitiser and prove the sink no longer emits the attacker's
control characters. Network access is mocked — dev-machine unit tests.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from localization.translation.vllm_mt import VLLMTranslator, _safe_log

LOGGER_NAME = "localization.translation.vllm_mt"
FORGED_LOCALE = "zh-Hans\n2026-10-02 ERROR auth: admin login FAILED\r\x1b[31m"


def _mk() -> VLLMTranslator:
    return VLLMTranslator(db_path=None, endpoint="http://localhost:8000", model="Qwen/Qwen3-8B")


class TestSafeLogHelper:
    def test_control_characters_are_neutralised(self):
        out = _safe_log(FORGED_LOCALE)
        assert "\n" not in out and "\r" not in out and "\x1b" not in out
        assert out.startswith("zh-Hans_2026-10-02 ERROR")

    def test_unicode_line_separators_are_neutralised(self):
        # A log aggregator that splits on U+2028/U+0085 is as forgeable as one
        # that splits on LF; str.isprintable() rejects both.
        assert _safe_log("zh Hans\u0085") == "zh_Hans_"

    def test_benign_locale_passes_unchanged(self):
        assert _safe_log("zh-Hans") == "zh-Hans"
        assert _safe_log("pt-BR") == "pt-BR"

    def test_none_and_oversize_are_bounded(self):
        assert _safe_log(None) == ""
        out = _safe_log("x" * 500)
        assert len(out) < 500 and out.endswith("...(truncated)")


class TestTranslateFailureLogIsNotForgeable:
    def _failure_record(self, caplog) -> logging.LogRecord:
        records = [
            r
            for r in caplog.records
            if r.name == LOGGER_NAME and "failed translation to" in r.getMessage()
        ]
        assert len(records) == 1, [r.getMessage() for r in caplog.records]
        return records[0]

    def test_forged_locale_cannot_inject_a_log_line(self, caplog):
        t = _mk()
        caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
        with patch.object(t._session, "post", side_effect=OSError("boom")):
            out = t.translate(["Hello, world."], FORGED_LOCALE)
        assert out == ["Hello, world."] and t.degraded

        msg = self._failure_record(caplog).getMessage()
        # The attack: a second, fake "ERROR auth: ..." line. It must stay
        # glued to the real line, with every control character neutralised.
        assert msg.count("\n") == 0 and "\r" not in msg and "\x1b" not in msg
        assert "zh-Hans_2026-10-02 ERROR auth: admin login FAILED" in msg
        assert msg.startswith("1 of 1 text(s) failed translation to ")

    def test_strict_mode_still_raises_and_still_logs_safely(self, caplog):
        from localization.translation.base import TranslationUnavailableError

        t = _mk()
        caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
        with (
            patch.object(t._session, "post", side_effect=OSError("boom")),
            pytest.raises(TranslationUnavailableError),
        ):
            t.translate(["Hello, world."], FORGED_LOCALE, strict=True)
        assert "\n" not in self._failure_record(caplog).getMessage()
