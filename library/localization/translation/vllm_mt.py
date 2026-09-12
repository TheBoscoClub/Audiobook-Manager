"""Machine-translation provider backed by a vLLM OpenAI-compatible endpoint.

The intended deployment is Qwen3-8B served by ``vllm serve`` on a rented GPU
node, reached through an SSH tunnel (see ``scripts/gpu-node.sh``). The
provider is model-agnostic in mechanics but its guards are calibrated for
English → Chinese, the deployment this project runs.

Design constraints, each paid for by a measured incident:

* **Sentence-aligned units, JSON-array contract.** An LLM is a completion
  engine: fed a unit that ends mid-sentence, it will translate the fragment
  AND finish the thought — fluent target-language text for words never in
  the source (observed on the 2026-09-06 bench; zero overruns in 240 units
  once inputs were whole sentences). Inputs here are the pipeline's
  sentence texts, the model must answer with a JSON array of exactly one
  translation per input, and a length mismatch fails the batch rather than
  zipping past the shortfall (the 09z class).
* **Per-item output gates** (CJK targets): character-ratio band
  0.14–0.90 zh:en (corpus median 0.333 at chapter scale; sentence-level
  variance runs wider — legit sentences measured at 0.63 on the pilot —
  while observed invention inflates to 2.5–83×, so the ceiling keeps a
  wide margin; truncation deflates below the floor), CJK-fraction ≥ 0.30 (the bd-536 class:
  English stored as Chinese), and identity rejection. A gated item is
  returned as source text and counted in ``degraded_texts``; it is never
  cached.
* **Strict refuses, never degrades silently**: any failed item under
  ``strict=True`` raises :class:`TranslationUnavailableError` — mandatory
  for callers that persist results.
* **Translation memory first**: cache hits cost nothing and never expire;
  only gate-passing translations are stored (``memory.tm_store`` refuses
  identity pairs a second time, by design — defence in depth).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import requests

from .base import TranslationProvider, TranslationUnavailableError
from .memory import tm_lookup, tm_store

logger = logging.getLogger(__name__)

# Output gates, calibrated on the existing corpus (median zh:en ratio 0.333
# over 6,629 chapter pairs). Short sources are ratio-noisy, so the band
# widens below _RATIO_STRICT_MIN_LEN source characters.
_RATIO_BAND = (0.14, 0.90)
_RATIO_BAND_SHORT = (0.10, 1.50)
_RATIO_STRICT_MIN_LEN = 20
_CJK_MIN_FRACTION = 0.30

_MAX_SENTENCES_PER_REQUEST = 16
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_ALPHA_RE = re.compile(r"[A-Za-z]")
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_GLOSSARY_PATH = Path(__file__).resolve().parents[1] / "glossary" / "en-zh.yaml"

_LOCALE_NAMES = {
    "zh-Hans": "Simplified Chinese (简体中文)",
    "zh-Hant": "Traditional Chinese (繁體中文)",
    "pt": "European Portuguese",
    "pt-BR": "Brazilian Portuguese",
}


def _load_glossary(path: Path = _GLOSSARY_PATH) -> dict[str, str]:
    """Parse the curated ``english: chinese`` terminology file.

    The file is deliberately simple YAML (flat ``key: value`` lines plus
    comments) — parsed by hand so the runtime needs no YAML dependency.
    """
    terms: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Glossary not readable at %s — continuing without", path)
        return terms
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s == "---" or ": " not in s:
            continue
        en, _, zh = s.partition(": ")
        if en and zh:
            terms[en.strip()] = zh.strip()
    return terms


def _is_cjk_locale(locale: str) -> bool:
    return locale.startswith("zh")


def _cjk_fraction(text: str) -> float:
    return len(_CJK_RE.findall(text)) / len(text) if text else 0.0


class VLLMTranslator(TranslationProvider):
    """Translate via a vLLM OpenAI-compatible ``/v1/chat/completions``."""

    def __init__(
        self,
        endpoint: str,
        model: str = "Qwen/Qwen3-8B",
        db_path: Path | str | None = None,
        timeout: int = 180,
        glossary_path: Path | None = None,
    ):
        if not endpoint:
            raise ValueError("vLLM endpoint is required")
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._db_path = db_path
        self._timeout = timeout
        self._session = requests.Session()
        self._glossary = _load_glossary(glossary_path or _GLOSSARY_PATH)
        self.degraded = False
        self.degraded_texts = 0
        self._last_error = ""

    @property
    def name(self) -> str:
        return f"vllm:{self._model.split('/')[-1].lower()}"

    # ── prompt construction ──

    def _system_prompt(self, target_locale: str, source_lang: str, batch_text: str) -> str:
        target = _LOCALE_NAMES.get(target_locale, target_locale)
        lines = [
            f"You are a professional literary translator. Translate each string in the "
            f"JSON array from {source_lang} to {target}.",
            "Rules:",
            "- Translate ONLY the text given. Never continue, complete, or extend a "
            "sentence beyond its source. Never add explanations or commentary.",
            "- Return ONLY a JSON array of strings, same length and order as the input.",
            "- Preserve proper nouns unless the glossary below says otherwise.",
        ]
        matched = {en: zh for en, zh in self._glossary.items() if en.lower() in batch_text.lower()}
        if matched:
            lines.append("Glossary (must be used exactly):")
            lines.extend(f"- {en} → {zh}" for en, zh in sorted(matched.items()))
        return "\n".join(lines)

    def _request_chunk(
        self, chunk: list[str], target_locale: str, source_lang: str
    ) -> list[str] | None:
        """One /v1/chat/completions round trip. None on any failure."""
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 4096,
            # Qwen3 thinking mode off — MT wants the answer, not the chain.
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(target_locale, source_lang, " ".join(chunk)),
                },
                {"role": "user", "content": json.dumps(chunk, ensure_ascii=False)},
            ],
        }
        resp = None
        for attempt in (1, 2):
            try:
                resp = self._session.post(
                    f"{self._endpoint}/v1/chat/completions",
                    json=payload,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                break
            except Exception as exc:  # noqa: BLE001 — any transport failure degrades
                resp = None
                self._last_error = exc.__class__.__name__
                if attempt == 2:
                    logger.error("vLLM request failed twice (%s)", self._last_error)
                    return None
        if resp is None:  # pragma: no cover — loop invariant, here for the type checker
            return None
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(_JSON_FENCE_RE.sub("", content.strip()))
        except (KeyError, IndexError, ValueError, TypeError):
            self._last_error = "response was not a JSON array"
            logger.error("vLLM response violated the JSON-array contract")
            return None
        if not isinstance(parsed, list) or len(parsed) != len(chunk):
            self._last_error = (
                f"array length {len(parsed) if isinstance(parsed, list) else 'n/a'} "
                f"for {len(chunk)} input(s)"
            )
            logger.error("vLLM answered %s — refusing misaligned batch", self._last_error)
            return None
        return [str(item) for item in parsed]

    def _request_single_plain(
        self, source: str, target_locale: str, source_lang: str
    ) -> str | None:
        """Bare-text translation of ONE sentence — the last fallback tier.

        Only ever called with a single sentence, where alignment is trivial
        and the output gates carry the correctness burden.
        """
        target = _LOCALE_NAMES.get(target_locale, target_locale)
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 2048,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Translate the user's text from {source_lang} to {target}. "
                        "Reply with ONLY the translation — no quotes, no commentary."
                    ),
                },
                {"role": "user", "content": source},
            ],
        }
        try:
            resp = self._session.post(
                f"{self._endpoint}/v1/chat/completions", json=payload, timeout=self._timeout
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001 — any failure degrades this item
            self._last_error = exc.__class__.__name__
            return None
        text = _JSON_FENCE_RE.sub("", text).strip().strip('"').strip()
        return text or None

    def _resolve_item(
        self, src: str, candidate: str, target_locale: str, source_lang: str
    ) -> tuple[str | None, str]:
        """Gate a candidate translation for one sentence.

        Returns ``(text, "cache")`` for a gate-passing translation,
        ``(text, "no-cache")`` for a CONFIRMED identity, or
        ``(None, reason)`` for a rejection.

        Identity needs the extra step: names, interjections and
        sound-effect cues legitimately survive translation unchanged
        (observed live: 16 such sentences in one Alice chapter), but
        wholesale identity is the xiy/64p poison class. A candidate equal
        to its source is therefore accepted only when an INDEPENDENT
        plain-tier request agrees the text stays unchanged — and even
        then it is never cached, so the TM cannot be poisoned. A dead
        endpoint cannot fake this: it fails at transport, not by echoing.
        """
        reason = self._gate(src, candidate, target_locale)
        if reason is None:
            return candidate, "cache"
        if reason.startswith("identity"):
            confirm = self._request_single_plain(src, target_locale, source_lang)
            if confirm is not None:
                if confirm.strip() == src.strip():
                    return src, "no-cache"
                second = self._gate(src, confirm, target_locale)
                if second is None:
                    return confirm, "cache"
                reason = second
        return None, reason

    # ── output gates ──

    def _gate(self, source: str, translated: str, target_locale: str) -> str | None:
        """Return the failure reason, or None if the translation is acceptable."""
        if not _is_cjk_locale(target_locale):
            return None
        translatable = bool(_ALPHA_RE.search(source))
        if translatable and translated.strip() == source.strip():
            return "identity (source returned unchanged)"
        if translatable and _cjk_fraction(translated) < _CJK_MIN_FRACTION:
            return f"cjk fraction {_cjk_fraction(translated):.2f} < {_CJK_MIN_FRACTION}"
        if source:
            lo, hi = _RATIO_BAND if len(source) >= _RATIO_STRICT_MIN_LEN else _RATIO_BAND_SHORT
            ratio = len(translated) / len(source)
            if not (lo <= ratio <= hi):
                return f"length ratio {ratio:.2f} outside [{lo}, {hi}]"
        return None

    # ── public API ──

    def translate(
        self,
        texts: list[str],
        target_locale: str,
        source_lang: str = "EN",
        strict: bool = False,
    ) -> list[str]:
        if not texts:
            return []

        if self._db_path is not None:
            hits, misses = tm_lookup(self._db_path, texts, target_locale)
        else:
            hits, misses = {}, list(enumerate(texts))

        output: list[str | None] = [None] * len(texts)
        for idx, cached in hits.items():
            output[idx] = cached

        failed: list[tuple[int, str, str]] = []  # (index, source, reason)
        accepted_pairs: list[tuple[str, str]] = []

        for start in range(0, len(misses), _MAX_SENTENCES_PER_REQUEST):
            chunk = misses[start : start + _MAX_SENTENCES_PER_REQUEST]
            chunk_texts = [t for _, t in chunk]
            translations = self._request_chunk(chunk_texts, target_locale, source_lang)
            if translations is None:
                # Batch contract failed (usually the model dropping one array
                # element from a long batch). A length-1 array cannot
                # misalign, so recover the chunk sentence-by-sentence instead
                # of failing 24 texts for one dropped element.
                logger.warning(
                    "Batch of %d failed (%s) — retrying sentence-by-sentence",
                    len(chunk),
                    self._last_error,
                )
                for idx, src in chunk:
                    single = self._request_chunk([src], target_locale, source_lang)
                    if single is None:
                        # Last tier: some inputs push the model into prose
                        # even for a single-element array. With exactly one
                        # sentence there is nothing to misalign, so ask for
                        # the bare translation and let the gates judge it.
                        plain = self._request_single_plain(src, target_locale, source_lang)
                        if plain is None:
                            failed.append((idx, src, self._last_error))
                            continue
                        single = [plain]
                    text, verdict = self._resolve_item(src, single[0], target_locale, source_lang)
                    if text is None:
                        failed.append((idx, src, verdict))
                    else:
                        output[idx] = text
                        if verdict == "cache":
                            accepted_pairs.append((src, text))
                continue
            for (idx, src), tgt in zip(chunk, translations):
                text, verdict = self._resolve_item(src, tgt, target_locale, source_lang)
                if text is None:
                    failed.append((idx, src, verdict))
                else:
                    output[idx] = text
                    if verdict == "cache":
                        accepted_pairs.append((src, text))

        if accepted_pairs and self._db_path is not None:
            tm_store(self._db_path, accepted_pairs, target_locale, self.name)

        if failed:
            self.degraded = True
            self.degraded_texts += len(failed)
            for _idx, src, reason in failed[:5]:
                logger.error("Rejected translation (%s): %.60s…", reason, src)
            logger.error(
                "%d of %d text(s) failed translation to %s — returning SOURCE text for those",
                len(failed),
                len(texts),
                target_locale,
            )
            if strict:
                raise TranslationUnavailableError(
                    f"{len(failed)} of {len(texts)} text(s) failed translation to "
                    f"{target_locale} ({failed[0][2]}); refusing to hand untranslated "
                    "text to a caller that persists results"
                )
            for idx, src, _reason in failed:
                output[idx] = src

        return [o if o is not None else "" for o in output]
