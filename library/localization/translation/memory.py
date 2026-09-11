"""Provider-neutral translation memory (TM).

The TM is the ``string_translations`` table in the audiobooks DB: rows are
keyed by a SHA-256 prefix of the source text plus the target locale, and
record which provider produced them in the ``translator`` column. It is
consulted before any paid/expensive translation call and written back
afterwards, so a string is never translated twice — across sessions,
providers, or provider changes.

Extracted from the removed hosted-API translator module
(Audiobook-Manager-4uj) because both the data and the mechanism are
provider-agnostic; a future :class:`~.base.TranslationProvider` backend
should route its lookups and stores through here.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def hash_source(text: str) -> str:
    """Return the 16-char SHA-256 prefix used as TM key.

    Must match the hashing convention in
    ``backend.api_modular.translations._hash_source`` so TM rows written
    by either code path are mutually readable.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def tm_lookup(
    db_path: Path | str, texts: list[str], locale: str
) -> tuple[dict[int, str], list[tuple[int, str]]]:
    """Return (index -> cached translation, list of (index, text) misses)."""
    if not texts:
        return {}, []

    hash_by_index = {i: hash_source(t) for i, t in enumerate(texts)}
    hashes = list(hash_by_index.values())
    placeholders = ",".join("?" * len(hashes))

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT source_hash, translation FROM string_translations "  # nosec B608  # noqa: S608
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


def tm_store(
    db_path: Path | str,
    pairs: list[tuple[str, str]],
    locale: str,
    translator_name: str,
) -> None:
    """Write (source, translation) pairs to the TM, tagged with provenance.

    NEVER caches a "translation" identical to its source. A real identity
    result (a bare number, a proper noun) is indistinguishable from a
    degraded pass-through, and once cached the TM serves English as though
    it were the target language — permanently, because a cache hit is never
    retried. 70 such rows accumulated during the April 2026 outages and
    survived every later fix, including a full delete of the
    audiobook_translations rows, because the TM re-served them
    (Audiobook-Manager-xiy). The cost of not caching these is one
    re-translation of a short string; the cost of caching one wrongly is
    permanent.
    """
    storable = [(s, t) for s, t in pairs if s != t]
    identity_results = len(pairs) - len(storable)
    if identity_results:
        logger.info(
            "Not caching %d identity result(s) — a translation equal to its "
            "source is indistinguishable from a degraded pass-through",
            identity_results,
        )
    if not storable:
        return
    conn = sqlite3.connect(str(db_path))
    try:
        for source, translation in storable:
            conn.execute(
                """INSERT INTO string_translations
                   (source_hash, locale, source, translation, translator)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source_hash, locale) DO UPDATE SET
                       translation = excluded.translation,
                       translator = excluded.translator,
                       updated_at = CURRENT_TIMESTAMP
                """,
                (hash_source(source), locale, source, translation, translator_name),
            )
        conn.commit()
    except sqlite3.Error:
        logger.exception("TM store failed")
    finally:
        conn.close()


def prune_translation_memory(db_path: Path | str, older_than_days: int) -> int:
    """Delete TM rows older than ``older_than_days``. Returns rows removed."""
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
