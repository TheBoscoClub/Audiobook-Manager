"""Scan-time trigger for the 6-minute pretranslation sampler.

When a new book is ingested, enqueue a sampler job for each enabled
non-EN locale in ``AUDIOBOOKS_SUPPORTED_LOCALES``. Idempotent — already-
complete (book, locale) pairs are no-ops.

Failures here MUST NOT block the scanner. A sampler enqueue error is
a degraded state (book won't have a preview in that locale), not a
scan failure. We log warnings and keep going.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def enqueue_sampler_for_new_book(
    conn: sqlite3.Connection,
    audiobook_id: int,
    file_path: str | Path,
) -> None:
    """Best-effort sampler enqueue for a freshly-imported audiobook.

    Resolves chapter durations from the audio file via
    ``localization.chapters.extract_chapters`` (ffprobe under the hood, same
    source the live streaming path uses). For each enabled non-EN locale,
    calls ``localization.sampler.enqueue_sampler`` to create the job + segments.

    Wrapped in broad try/except per locale so one bad locale can't break the
    others, and so a sampler failure never breaks the scan.
    """
    try:
        from localization.chapters import extract_chapters
        from localization.config import SUPPORTED_LOCALES
        from localization.sampler import enqueue_sampler
    except ImportError as e:
        logger.warning("sampler hook: imports failed, skipping (%s)", e)
        return

    # Filter to actionable locales. en* is the source; skip.
    target_locales = [
        loc.strip()
        for loc in SUPPORTED_LOCALES
        if loc.strip() and not loc.strip().lower().startswith("en")
    ]
    if not target_locales:
        logger.debug(
            "sampler hook: no non-EN locales configured — skipping (AUDIOBOOKS_SUPPORTED_LOCALES=%s)",
            ",".join(SUPPORTED_LOCALES),
        )
        return

    # Resolve chapter durations once per book.
    try:
        chapters = extract_chapters(Path(file_path))
        chapter_durations = [c.duration_ms / 1000.0 for c in chapters]
    except Exception as e:  # noqa: BLE001 — don't want ingest to fail here
        logger.warning(
            "sampler hook: extract_chapters failed for book=%d path=%s err=%s",
            audiobook_id,
            str(file_path),
            e,
        )
        return

    if not chapter_durations:
        logger.info(
            "sampler hook: book=%d has no chapter metadata — skipping sampler",
            audiobook_id,
        )
        return

    for locale in target_locales:
        try:
            result = enqueue_sampler(conn, audiobook_id, locale, chapter_durations)
            logger.info(
                "sampler hook: book=%d locale=%s status=%s",
                audiobook_id,
                locale,
                result.get("status"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "sampler hook: enqueue failed book=%d locale=%s err=%s",
                audiobook_id,
                locale,
                e,
            )
