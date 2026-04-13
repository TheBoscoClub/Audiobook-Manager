"""DeepL text translation provider.

The translator is wrapped in two pieces of production infrastructure:

* **Translation memory (TM)** — the ``string_translations`` table in
  the audiobooks DB is consulted before every API call. Cache hits are
  returned verbatim and do NOT bill against the DeepL quota. Newly
  translated strings are written back to the same table.
* **Quota tracking** — every API call flows through :class:`QuotaTracker`
  so we never silently blow the monthly character budget. Hard-limit
  breaches raise :class:`QuotaExceededError`; callers in the translation
  layer catch that and fall back to pass-through English.
* **Glossary** — on first use the translator loads (or builds) a DeepL
  glossary from the YAML source and passes ``glossary_id`` on every
  request for consistent terminology.

The DB path and glossary manager are injected lazily so unit tests can
bypass them with a plain ``DeepLTranslator(api_key, db_path=None)``.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

import requests

from .quota import QuotaExceededError, QuotaTracker

logger = logging.getLogger(__name__)

DEEPL_API_URL = "https://api.deepl.com/v2"
DEEPL_FREE_API_URL = "https://api-free.deepl.com/v2"

# Map locale codes to DeepL target language codes
LOCALE_TO_DEEPL = {
    "zh-Hans": "ZH-HANS",
    "zh-Hant": "ZH-HANT",
    "en": "EN-US",
    "pt": "PT-PT",
    "pt-BR": "PT-BR",
}


def _hash_source(text: str) -> str:
    """Return the 16-char SHA-256 prefix used as TM key.

    Must match the hashing convention in
    ``backend.api_modular.translations._hash_source`` so TM rows written
    by either code path are mutually readable.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class DeepLTranslator:
    """Translate text using the DeepL API with TM + quota + glossary."""

    def __init__(
        self,
        api_key: str,
        db_path: Path | str | None = None,
        tracker: QuotaTracker | None = None,
        glossary_id: str | None = None,
        enable_glossary: bool = True,
    ):
        if not api_key:
            raise ValueError("DeepL API key is required")
        self._api_key = api_key
        self._base_url = (
            DEEPL_FREE_API_URL if api_key.endswith(":fx") else DEEPL_API_URL
        )
        self._db_path = Path(db_path) if db_path else None
        self._tracker = tracker
        self._glossary_id = glossary_id
        self._enable_glossary = enable_glossary
        self._glossary_resolved = glossary_id is not None

        if self._tracker is None and self._db_path is not None:
            self._tracker = QuotaTracker(
                db_path=self._db_path,
                api_key=api_key,
                base_url=self._base_url,
            )

    # -- TM helpers ------------------------------------------------------

    def _tm_lookup(
        self, texts: list[str], locale: str
    ) -> tuple[dict[int, str], list[tuple[int, str]]]:
        """Return (index -> cached translation, list of (index, text) misses)."""
        if self._db_path is None or not texts:
            return {}, [(i, t) for i, t in enumerate(texts)]

        hash_by_index = {i: _hash_source(t) for i, t in enumerate(texts)}
        hashes = list(hash_by_index.values())
        placeholders = ",".join("?" * len(hashes))

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT source_hash, translation FROM string_translations "
                f"WHERE locale = ? AND source_hash IN ({placeholders})",
                (locale, *hashes),
            ).fetchall()
        except sqlite3.Error:
            logger.exception("TM lookup failed")
            return {}, [(i, t) for i, t in enumerate(texts)]
        finally:
            conn.close()

        hit_by_hash = {r["source_hash"]: r["translation"] for r in rows}
        hits: dict[int, str] = {}
        misses: list[tuple[int, str]] = []
        for idx, text in enumerate(texts):
            cached = hit_by_hash.get(hash_by_index[idx])
            if cached is not None:
                hits[idx] = cached
            else:
                misses.append((idx, text))
        return hits, misses

    def _tm_store(self, pairs: list[tuple[str, str]], locale: str) -> None:
        if self._db_path is None or not pairs:
            return
        conn = sqlite3.connect(str(self._db_path))
        try:
            for source, translation in pairs:
                conn.execute(
                    """INSERT INTO string_translations
                       (source_hash, locale, source, translation, translator)
                       VALUES (?, ?, ?, ?, 'deepl')
                       ON CONFLICT(source_hash, locale) DO UPDATE SET
                           translation = excluded.translation,
                           translator = excluded.translator,
                           updated_at = CURRENT_TIMESTAMP
                    """,
                    (_hash_source(source), locale, source, translation),
                )
            conn.commit()
        except sqlite3.Error:
            logger.exception("TM store failed")
        finally:
            conn.close()

    # -- glossary -------------------------------------------------------

    def _resolve_glossary(self) -> str | None:
        if not self._enable_glossary:
            return None
        if self._glossary_resolved:
            return self._glossary_id
        self._glossary_resolved = True
        if self._tracker is None:
            return None
        try:
            from .glossary import GlossaryError, GlossaryManager

            mgr = GlossaryManager(
                api_key=self._api_key,
                base_url=self._base_url,
                tracker=self._tracker,
            )
            self._glossary_id = mgr.ensure()
        except (GlossaryError, Exception) as exc:  # noqa: BLE001
            logger.warning("Glossary unavailable, continuing without: %s", exc)
            self._glossary_id = None
        return self._glossary_id

    # -- public API ------------------------------------------------------

    def translate(
        self,
        texts: list[str],
        target_locale: str,
        source_lang: str = "EN",
    ) -> list[str]:
        """Translate a batch of texts to the target locale."""
        if not texts:
            return []

        hits, misses = self._tm_lookup(texts, target_locale)

        # Build the output array with cache hits in place.
        output: list[str | None] = [None] * len(texts)
        for idx, val in hits.items():
            output[idx] = val

        if not misses:
            return [o or "" for o in output]

        miss_texts = [t for _, t in misses]
        char_count = sum(len(t) for t in miss_texts)

        if self._tracker is not None:
            self._tracker.check_before_translate(char_count)

        target_lang = LOCALE_TO_DEEPL.get(target_locale, target_locale.upper())
        payload: dict[str, Any] = {
            "text": miss_texts,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }
        glossary_id = self._resolve_glossary()
        if glossary_id:
            payload["glossary_id"] = glossary_id

        try:
            resp = requests.post(
                f"{self._base_url}/translate",
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
        except QuotaExceededError:
            raise
        except requests.RequestException:
            logger.exception("DeepL translate call failed")
            # Fall back: fill misses with source text (pass-through English).
            for idx, text in misses:
                output[idx] = text
            return [o or "" for o in output]

        result = resp.json()
        translations = [t["text"] for t in result.get("translations", [])]

        pairs_to_store: list[tuple[str, str]] = []
        for (idx, src), translated in zip(misses, translations):
            output[idx] = translated
            pairs_to_store.append((src, translated))

        self._tm_store(pairs_to_store, target_locale)
        if self._tracker is not None:
            self._tracker.record_usage(char_count)

        # Any leftover Nones (e.g., DeepL returned fewer entries than sent)
        # fall back to the original source text so callers never see None.
        for idx, text in misses:
            if output[idx] is None:
                output[idx] = text
        return [o or "" for o in output]

    def translate_one(
        self,
        text: str,
        target_locale: str,
        source_lang: str = "EN",
    ) -> str:
        """Translate a single string."""
        results = self.translate([text], target_locale, source_lang)
        return results[0] if results else text


def prune_translation_memory(db_path: Path | str, older_than_days: int) -> int:
    """Delete TM rows older than ``older_than_days``.

    Returns the number of rows removed. The translation memory lives in
    the audiobooks DB's ``string_translations`` table.
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "DELETE FROM string_translations WHERE updated_at < datetime('now', ?)",
            (f"-{int(older_than_days)} days",),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()
