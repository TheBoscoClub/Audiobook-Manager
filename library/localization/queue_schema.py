"""translation_queue schema — the single definition every writer uses.

Used by the API (``localization.queue``), ``scripts/batch-translate.py``, the
drain's queue helper (``localization.drain_queue``) and data migration
``015_translation_queue_two_stage.sh``. Idempotent: safe to call on every
open.

Two-stage accounting (Audiobook-Manager-4j4)
--------------------------------------------
A row tracks two pieces of work separately:

``subtitles_done_at``
    STT + machine translation finished and the translated subtitles exist.
    This is the GPU stage — done by the scheduled drain on a rented node.
``narration_done_at``
    TTS narration of the translated subtitles finished. This stage needs no
    GPU (edge-tts) and runs on the host after the node is destroyed.

``state`` keeps its meaning for every existing reader: a row is
``'completed'`` only when BOTH timestamps are set. A row whose subtitles are
done but whose narration is not stays ``'pending'`` — still visibly unfinished
— and is picked up by the narration pass, never by the GPU stage.

``drain_attempts`` counts consecutive scheduled-drain runs in which the book
failed for a reason of its own (bad audio, a translation the ratio guard
refuses, a TTS rejection). Run-level failures — tunnel or node loss, bootstrap
timeout, budget stop — never count. At ``AUDIOBOOKS_DRAIN_MAX_ATTEMPTS`` the row
moves to ``'quarantined'``: visible, with the last error, and excluded from the
pending count that decides whether to rent a node. ``drain_queue
requeue-quarantined`` puts it back.

``claimed_by`` names the owner of a ``'processing'`` row (``api:<pid>``,
``drain``, ``batch:<pid>``). It is what lets each owner recover only its own
stale claims: an API restart must not hand back rows a running drain holds.

Mapping of rows that exist when the columns are first added (runs once, in the
same transaction that adds them, decided by evidence on disk rather than by
the old ``state`` alone):

* every transcribed chapter has a translated subtitle row → ``subtitles_done_at``
  set (partial coverage is not "done");
* every translated chapter has a narration row (``chapter_translations_audio``)
  → ``narration_done_at`` set;
* ``'completed'`` with narration → stays ``'completed'`` (both stages are
  evidenced);
* ``'completed'`` with subtitles but no narration → ``'pending'`` with
  subtitles done: only the free host-side narration is outstanding, so no GPU
  node is ever rented for it;
* ``'completed'`` without complete translated subtitles → ``'pending'`` with
  the subtitle stage outstanding: nothing on disk backs the old claim;
* every other state keeps its value; only the evidence timestamps are filled
  (a pending row whose subtitles already exist will not cause a rental).
"""

from __future__ import annotations

import sqlite3

TWO_STAGE_COLUMNS = ("subtitles_done_at", "narration_done_at", "claimed_by")


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(translation_queue)").fetchall()}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def ensure_queue_schema(conn: sqlite3.Connection) -> int:
    """Create/upgrade ``translation_queue``. Returns rows remapped (0 if none).

    Commits on success.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translation_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            priority INTEGER DEFAULT 0,
            state TEXT DEFAULT 'pending',
            step TEXT DEFAULT 'stt',
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            last_progress_at TIMESTAMP,
            total_chapters INTEGER,
            subtitles_done_at TIMESTAMP,
            narration_done_at TIMESTAMP,
            claimed_by TEXT,
            drain_attempts INTEGER NOT NULL DEFAULT 0,
            UNIQUE(audiobook_id, locale),
            FOREIGN KEY (audiobook_id) REFERENCES audiobooks(id) ON DELETE CASCADE
        )
    """)
    # In-place ALTER for upgraded DBs that pre-date these columns. This MUST
    # run before the CREATE INDEX on last_progress_at — otherwise a legacy DB
    # whose translation_queue pre-dates the column errors out on the index
    # creation before the ALTER can fix the schema.
    cols = _columns(conn)
    if "last_progress_at" not in cols:
        conn.execute("ALTER TABLE translation_queue ADD COLUMN last_progress_at TIMESTAMP")
        sources = [c for c in ("started_at", "created_at") if c in cols]
        if sources:
            conn.execute(
                f"UPDATE translation_queue SET last_progress_at = COALESCE({', '.join(sources)}, NULL)"  # nosec B608 — fixed column names
            )
    if "total_chapters" not in cols:
        conn.execute("ALTER TABLE translation_queue ADD COLUMN total_chapters INTEGER")
    remapped = 0
    if not set(TWO_STAGE_COLUMNS) <= cols:
        for col in TWO_STAGE_COLUMNS:
            if col not in cols:
                kind = "TEXT" if col == "claimed_by" else "TIMESTAMP"
                conn.execute(f"ALTER TABLE translation_queue ADD COLUMN {col} {kind}")  # nosec B608 — fixed column names from TWO_STAGE_COLUMNS
        remapped = _map_existing_rows(conn)
    if "drain_attempts" not in _columns(conn):
        # Consecutive scheduled-drain runs in which THIS book failed for a
        # book-specific reason. Reset by any successful stage; at
        # AUDIOBOOKS_DRAIN_MAX_ATTEMPTS the row moves to 'quarantined'.
        conn.execute(
            "ALTER TABLE translation_queue ADD COLUMN drain_attempts INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tq_state ON translation_queue(state, priority DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tq_last_progress ON translation_queue(last_progress_at)"
    )
    conn.commit()
    return remapped


def _map_existing_rows(conn: sqlite3.Connection) -> int:
    """Fill the two-stage columns for rows that pre-date them (see module doc)."""
    has_subs = _table_exists(conn, "chapter_subtitles")
    has_audio = _table_exists(conn, "chapter_translations_audio")
    stamp = "COALESCE(finished_at, CURRENT_TIMESTAMP)"
    tr_chapters = (
        "(SELECT COUNT(DISTINCT cs.chapter_index) FROM chapter_subtitles cs "
        " WHERE cs.audiobook_id = translation_queue.audiobook_id"
        " AND cs.locale = translation_queue.locale)"
    )
    en_chapters = (
        "(SELECT COUNT(DISTINCT cs.chapter_index) FROM chapter_subtitles cs "
        " WHERE cs.audiobook_id = translation_queue.audiobook_id AND cs.locale = 'en')"
    )
    if has_subs:
        # Complete coverage only: every transcribed chapter has its translation.
        conn.execute(
            f"UPDATE translation_queue SET subtitles_done_at = {stamp} "  # nosec B608 — constant SQL fragments
            f"WHERE subtitles_done_at IS NULL AND {tr_chapters} > 0 "
            f"AND {tr_chapters} >= {en_chapters}"
        )
    if has_audio and has_subs:
        audio_chapters = (
            "(SELECT COUNT(DISTINCT ta.chapter_index) FROM chapter_translations_audio ta "
            " WHERE ta.audiobook_id = translation_queue.audiobook_id"
            " AND ta.locale = translation_queue.locale)"
        )
        conn.execute(
            f"UPDATE translation_queue SET narration_done_at = {stamp} "  # nosec B608 — constant SQL fragments
            f"WHERE narration_done_at IS NULL AND {audio_chapters} > 0 "
            f"AND {audio_chapters} >= {tr_chapters}"
        )
    # 'completed' with narration evidence stays completed.
    conn.execute(
        "UPDATE translation_queue SET subtitles_done_at = COALESCE(subtitles_done_at, narration_done_at) "
        "WHERE state = 'completed' AND narration_done_at IS NOT NULL"
    )
    cur = conn.execute(
        "UPDATE translation_queue SET state = 'pending', finished_at = NULL "
        "WHERE state = 'completed' AND narration_done_at IS NULL"
    )
    return cur.rowcount
