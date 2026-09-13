"""Permanent translation exclusions — books this library will not translate.

Two sources, unioned:

* **The database** (``audiobooks.translation_excluded``) — the canonical
  one, set by an admin through the UI or ``/api/audiobooks/<id>/translation-exclusion``.
  It travels with the library, is visible in the app, and records who
  excluded the title, when, and why.
* **A config file** (``/etc/audiobooks/translation-exclude.txt``) — an
  operator-side override for cases where the app is not reachable, or for
  seeding an exclusion before a book is imported.

Some books are deliberately out of scope forever. The exclusion has to
survive queue churn to mean anything: a book marked "failed" in
``translation_queue`` comes straight back the next time someone resets
failed rows to pending, which is exactly what happened repeatedly during
the 2026-09-12 repair campaign. So the list is **declarative and external**
to the queue, and every entry point consults it rather than each remembering
on its own.

Format (``/etc/audiobooks/translation-exclude.txt``): one audiobook id or
ASIN per line, ``#`` starts a comment, and an inline comment carries the
reason. A missing file means no exclusions — never an error, because an
unreadable config must not silently start translating things the operator
excluded... and equally must not halt the pipeline.

Consumers (keep this list current — a consumer that forgets to ask is how
an exclusion quietly stops meaning anything):

* ``scripts/batch-translate.py`` — skips excluded books when claiming jobs
* ``scripts/verify-zh-corpus.py`` — neither reports nor purges their files
* ``scripts/sampler-reconcile.py`` — does not enqueue sampler jobs for them
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(
    os.environ.get("AUDIOBOOKS_TRANSLATION_EXCLUDE", "/etc/audiobooks/translation-exclude.txt")
)


def load_exclusions(path: Path | None = None) -> dict[str, str]:
    """Return ``{identifier: reason}`` for every excluded book.

    Identifiers are kept as strings so a numeric id and an ASIN can live in
    the same file; callers compare against both forms.
    """
    target = path or DEFAULT_PATH
    out: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        body, _, comment = line.partition("#")
        token = body.strip()
        if not token:
            continue
        out[token] = comment.strip() or "no reason recorded"
    return out


def is_excluded(
    audiobook_id: int | str,
    asin: str | None = None,
    exclusions: dict[str, str] | None = None,
) -> bool:
    """True when this book is permanently out of scope for translation."""
    table = load_exclusions() if exclusions is None else exclusions
    if not table:
        return False
    return str(audiobook_id) in table or (asin is not None and asin in table)


def excluded_ids(conn, exclusions: dict[str, str] | None = None) -> set[int]:
    """Resolve the exclusion list to audiobook ids using an open DB handle.

    Accepts either form in the file: a numeric id is taken as-is, anything
    else is looked up as an ASIN.
    """
    table = load_exclusions() if exclusions is None else exclusions
    ids: set[int] = set()
    # The database flag is canonical; the file is an operator override.
    try:
        ids.update(
            row[0]
            for row in conn.execute(
                "SELECT id FROM audiobooks WHERE translation_excluded = 1"
            )
        )
    except Exception:  # noqa: BLE001 — pre-migration schemas simply have no flags
        pass
    asins = {token for token in table if not token.isdigit()}
    ids.update(int(token) for token in table if token.isdigit())
    if asins:
        try:
            # Scan rather than build an IN-list: the table is ~2k rows, and a
            # fixed query keeps this free of dynamically-assembled SQL.
            for row_id, row_asin in conn.execute("SELECT id, asin FROM audiobooks"):
                if row_asin and row_asin in asins:
                    ids.add(row_id)
        except Exception:  # noqa: BLE001 — a schema without asin must not break the pipeline
            logger.warning("Could not resolve excluded ASINs against the audiobooks table")
    return ids
