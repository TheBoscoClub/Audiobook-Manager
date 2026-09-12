"""Tests for the vLLM machine-translation provider (Audiobook-Manager-r6h).

The provider talks to a vLLM OpenAI-compatible endpoint (Qwen3-8B on a
rented GPU node). Every network interaction is mocked here — these are
dev-machine unit tests. The guards under test exist because of measured
failure modes:

- JSON-array contract with length validation: the model must return exactly
  one translation per input sentence (the Sept 6 bench's invention defect
  arose from misaligned units).
- zh:en character-ratio band 0.18-0.60 (corpus median 0.333): invented
  content inflates the ratio; truncation deflates it.
- CJK-fraction >= 0.30 for zh targets: the English-as-Chinese pass-through
  class (bd 536: 1,326 corrupt files) becomes impossible to store.
- Identity refusal + strict semantics: inherited from the provider ABC
  contract and the xiy incident.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from localization.translation import factory as factory_mod
from localization.translation.base import TranslationUnavailableError
from localization.translation.vllm_mt import VLLMTranslator, _load_glossary

# ── helpers ──


def _chat_response(translations: list[str]) -> dict:
    """Shape a vLLM /v1/chat/completions response carrying a JSON array."""
    return {"choices": [{"message": {"content": json.dumps(translations, ensure_ascii=False)}}]}


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _mk(db_path=None, **kw) -> VLLMTranslator:
    kw.setdefault("endpoint", "http://localhost:8000")
    kw.setdefault("model", "Qwen/Qwen3-8B")
    return VLLMTranslator(db_path=db_path, **kw)


GOOD_ZH = ["你好，世界。", "船长伸手去拿罗盘。"]
SRC_EN = ["Hello, world.", "The captain reached for the compass."]


# ── happy path ──


class TestTranslateHappyPath:
    def test_batch_translates_and_aligns(self):
        t = _mk()
        with patch.object(t._session, "post", return_value=_FakeResp(_chat_response(GOOD_ZH))):
            out = t.translate(SRC_EN, "zh-Hans")
        assert out == GOOD_ZH
        assert t.degraded is False
        assert t.degraded_texts == 0

    def test_name_carries_model_identity(self):
        assert _mk().name == "vllm:qwen3-8b"

    def test_empty_input_makes_no_request(self):
        t = _mk()
        with patch.object(t._session, "post") as post:
            assert t.translate([], "zh-Hans") == []
        post.assert_not_called()

    def test_translate_one_delegates(self):
        t = _mk()
        with patch.object(
            t._session, "post", return_value=_FakeResp(_chat_response(["你好，世界。"]))
        ):
            assert t.translate_one("Hello, world.", "zh-Hans") == "你好，世界。"


# ── the response contract ──


class TestResponseContract:
    def test_short_array_is_a_failure_not_a_zip_past(self):
        """N inputs must yield N outputs — a shortfall degrades, never
        silently misaligns (the 09z class)."""
        t = _mk()
        with patch.object(
            t._session, "post", return_value=_FakeResp(_chat_response(["你好，世界。"]))
        ):
            out = t.translate(SRC_EN, "zh-Hans")
        assert out == SRC_EN  # pass-through, flagged
        assert t.degraded is True
        assert t.degraded_texts == 2

    def test_non_json_content_degrades(self):
        t = _mk()
        bad = {"choices": [{"message": {"content": "抱歉，我无法翻译。"}}]}
        with patch.object(t._session, "post", return_value=_FakeResp(bad)):
            out = t.translate(SRC_EN, "zh-Hans")
        assert out == SRC_EN
        assert t.degraded is True

    def test_strict_raises_instead_of_passing_through(self):
        t = _mk()
        with patch.object(
            t._session, "post", return_value=_FakeResp(_chat_response(["你好，世界。"]))
        ):
            with pytest.raises(TranslationUnavailableError):
                t.translate(SRC_EN, "zh-Hans", strict=True)

    def test_network_error_degrades_or_raises(self):
        t = _mk()
        with patch.object(t._session, "post", side_effect=OSError("boom")):
            out = t.translate(SRC_EN, "zh-Hans")
            assert out == SRC_EN and t.degraded
            with pytest.raises(TranslationUnavailableError):
                t.translate(SRC_EN, "zh-Hans", strict=True)


# ── per-item output guards ──


class TestOutputGuards:
    def test_ratio_guard_rejects_inflated_output(self):
        """Invented content inflates zh:en length far past the 0.18-0.60 band."""
        t = _mk()
        invented = ["你好，世界。" * 40, GOOD_ZH[1]]
        with patch.object(t._session, "post", return_value=_FakeResp(_chat_response(invented))):
            out = t.translate(SRC_EN, "zh-Hans")
        assert out[0] == SRC_EN[0]  # rejected item passes through flagged
        assert out[1] == GOOD_ZH[1]  # good item kept
        assert t.degraded is True
        assert t.degraded_texts == 1

    def test_cjk_guard_rejects_english_as_chinese(self):
        """The bd 536 class: English text stored as a zh translation."""
        t = _mk()
        pass_through = ["Hello there, world.", GOOD_ZH[1]]
        with patch.object(t._session, "post", return_value=_FakeResp(_chat_response(pass_through))):
            out = t.translate(SRC_EN, "zh-Hans")
        assert out[0] == SRC_EN[0]
        assert t.degraded is True

    def test_identity_result_is_rejected(self):
        t = _mk()
        with patch.object(
            t._session, "post", return_value=_FakeResp(_chat_response([SRC_EN[0], GOOD_ZH[1]]))
        ):
            out = t.translate(SRC_EN, "zh-Hans")
        assert t.degraded is True
        assert out[1] == GOOD_ZH[1]

    def test_guards_do_not_fire_for_non_cjk_targets(self):
        """A pt-BR translation is legitimately latin — CJK guard must scope
        to CJK locales only, and the ratio band is zh-calibrated."""
        t = _mk()
        pt = ["Olá, mundo.", "O capitão pegou a bússola."]
        with patch.object(t._session, "post", return_value=_FakeResp(_chat_response(pt))):
            out = t.translate(SRC_EN, "pt-BR")
        assert out == pt
        assert t.degraded is False


# ── translation memory integration ──


@pytest.fixture
def tm_db(tmp_path: Path) -> Path:
    db = tmp_path / "tm.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE string_translations (
            source_hash TEXT NOT NULL, locale TEXT NOT NULL,
            source TEXT NOT NULL, translation TEXT NOT NULL,
            translator TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_hash, locale))"""
    )
    conn.commit()
    conn.close()
    return db


class TestTranslationMemory:
    def test_hits_skip_the_network_and_misses_are_stored(self, tm_db: Path):
        t = _mk(db_path=tm_db)
        with patch.object(t._session, "post", return_value=_FakeResp(_chat_response(GOOD_ZH))):
            first = t.translate(SRC_EN, "zh-Hans")
        assert first == GOOD_ZH
        # Second call: everything cached — no HTTP at all.
        t2 = _mk(db_path=tm_db)
        with patch.object(t2._session, "post") as post:
            second = t2.translate(SRC_EN, "zh-Hans")
        post.assert_not_called()
        assert second == GOOD_ZH
        conn = sqlite3.connect(str(tm_db))
        provs = {r[0] for r in conn.execute("SELECT translator FROM string_translations")}
        conn.close()
        assert provs == {"vllm:qwen3-8b"}

    def test_rejected_items_are_never_cached(self, tm_db: Path):
        t = _mk(db_path=tm_db)
        with patch.object(
            t._session, "post", return_value=_FakeResp(_chat_response(["English junk", GOOD_ZH[1]]))
        ):
            t.translate(SRC_EN, "zh-Hans")
        conn = sqlite3.connect(str(tm_db))
        rows = conn.execute("SELECT source FROM string_translations").fetchall()
        conn.close()
        assert rows == [(SRC_EN[1],)]


# ── glossary ──


class TestGlossary:
    def test_glossary_loads_and_only_relevant_terms_enter_prompt(self):
        terms = _load_glossary()
        assert terms.get("Audiobook") == "有声书"  # curated en-zh.yaml survives
        t = _mk()
        captured: dict = {}

        def spy(url, **kw):
            captured.update(kw["json"])
            return _FakeResp(_chat_response(["这本有声书很好。"]))

        with patch.object(t._session, "post", side_effect=spy):
            t.translate(["This audiobook is good."], "zh-Hans")
        prompt = json.dumps(captured, ensure_ascii=False)
        assert "有声书" in prompt  # matched term rides along
        assert "朗读者" not in prompt  # unmatched term (Narrator) stays out


# ── factory wiring ──


class TestFactory:
    def test_factory_returns_none_without_endpoint(self, monkeypatch):
        monkeypatch.setattr(factory_mod, "_endpoint", lambda: "")
        assert factory_mod.get_translation_provider() is None
        assert factory_mod.translation_provider_name() is None

    def test_factory_returns_provider_with_endpoint(self, monkeypatch):
        monkeypatch.setattr(factory_mod, "_endpoint", lambda: "http://localhost:8000")
        provider = factory_mod.get_translation_provider()
        assert isinstance(provider, VLLMTranslator)
        assert factory_mod.translation_provider_name() == "vllm:qwen3-8b"
