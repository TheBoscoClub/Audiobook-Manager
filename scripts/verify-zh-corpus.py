#!/usr/bin/env python3
"""verify-zh-corpus.py — audit (and optionally purge) corrupt zh-Hans artifacts.

STAND-ALONE OPERATOR TOOL — deliberately not wired into the service graph
(no systemd unit, no dispatch): it is invoked by hand or by the repair
runbook around a GPU translation session. This exception comment satisfies
the new-script wiring rule in .claude/rules/upgrade-consistency.md.

Detects the English-as-Chinese pass-through class (bd Audiobook-Manager-536:
1,326 files across 77 books written during the April 2026 MT outages, before
strict= existed). A real zh-Hans subtitle file is CJK-dominant; a
pass-through is Latin text wearing a .zh-Hans.vtt name. The same
CJK-fraction and zh:en ratio gates now guard every new write at the
provider level (localization/translation/vllm_mt.py); this tool is the
retroactive half — it finds what predates the gates.

Modes:
    audit (default)      read-only report; exit 1 if corrupt files exist,
                         0 when the corpus is clean — usable as a CI-style
                         check that can actually fail
    --delete-corrupt     delete the corrupt chapter_subtitles rows and their
                         VTT files so the resumable batch driver re-translates
                         exactly those chapters. Refuses without --yes.
                         Suspect (mixed-content) files are REPORTED, never
                         auto-deleted.

Run as the audiobooks user against the installed database:
    sudo -u audiobooks python3 scripts/verify-zh-corpus.py --db "$AUDIOBOOKS_DATABASE"
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_TIMESTAMP_RE = re.compile(r"^\d\d:\d\d")

# A file below CORRUPT_MAX is untranslated English (delete + redo); between
# the two thresholds it is mixed content needing a human eye (report only).
CORRUPT_MAX_CJK_FRACTION = 0.02
SUSPECT_MAX_CJK_FRACTION = 0.30

# ...but only once there is enough text for "no Chinese" to MEAN anything.
# The bd-536 corruption was whole chapters of English prose — hundreds to
# thousands of characters. A chapter whose entire cue text is "18.", "1986."
# or "BASH!" is a chapter marker or a sound effect: it has no translatable
# content, so its lack of Chinese is the correct result, not a defect. Six
# such micro-chapters were re-translated three times in a row on 2026-09-13,
# each time "failing" an audit that could not tell a 5-character marker from
# a failed translation. Below this length the file is reported as trivial
# and never purged.
TRIVIAL_MAX_CHARS = 50


def cjk_fraction(text: str) -> float:
    """Fraction of characters in the CJK unified blocks."""
    return len(CJK_RE.findall(text)) / len(text) if text else 0.0


def vtt_cue_text(path: Path) -> str:
    """Concatenated cue text of a WEBVTT file — headers/timestamps stripped."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "".join(
        s.strip()
        for s in lines
        if s.strip()
        and s.strip() != "WEBVTT"
        and "-->" not in s
        and not _TIMESTAMP_RE.match(s.strip())
        and not s.strip().isdigit()
    )


def classify(path: Path) -> tuple[str, float]:
    """Classify one zh VTT file.

    Returns ('corrupt'|'suspect'|'trivial'|'empty'|'ok', cjk_fraction).
    Only 'corrupt' is ever purged.
    """
    text = vtt_cue_text(path)
    if not text:
        return "empty", 0.0
    frac = cjk_fraction(text)
    if len(text) < TRIVIAL_MAX_CHARS and frac < SUSPECT_MAX_CJK_FRACTION:
        # Too little text for the absence of Chinese to be evidence.
        return "trivial", frac
    if frac < CORRUPT_MAX_CJK_FRACTION:
        return "corrupt", frac
    if frac < SUSPECT_MAX_CJK_FRACTION:
        return "suspect", frac
    return "ok", frac


def scan(conn: sqlite3.Connection) -> dict[str, list[tuple[int, int, str, float]]]:
    """Scan every zh-Hans chapter_subtitles row. Returns class -> rows."""
    out: dict[str, list[tuple[int, int, str, float]]] = {
        "corrupt": [],
        "suspect": [],
        "trivial": [],
        "missing": [],
    }
    rows = conn.execute(
        "SELECT audiobook_id, chapter_index, vtt_path FROM chapter_subtitles "
        "WHERE locale = 'zh-Hans'"
    ).fetchall()
    # A permanently excluded book will never be re-translated, so reporting
    # its files as "corrupt" is noise and PURGING them would destroy the only
    # subtitles it has.
    try:
        sys.path.insert(0, "/opt/audiobooks/library")
        from localization.exclusions import excluded_ids as _excluded_ids

        skip = _excluded_ids(conn)
    except Exception:  # noqa: BLE001 — the audit must run even without the library
        skip = set()
    for book_id, chapter, vtt_path in rows:
        if book_id in skip:
            continue
        p = Path(vtt_path)
        if not p.exists():
            out["missing"].append((book_id, chapter, vtt_path, 0.0))
            continue
        verdict, frac = classify(p)
        if verdict == "corrupt":
            out["corrupt"].append((book_id, chapter, vtt_path, frac))
        elif verdict in ("trivial", "empty"):
            # Reported, never purged: a chapter marker, a sound cue or a
            # silent chapter has nothing to re-translate, and deleting it
            # would loop forever (purge -> re-translate -> flag -> purge).
            out["trivial"].append((book_id, chapter, vtt_path, frac))
        elif verdict == "suspect" and chapter > 0:
            # chapter 0 is canned intro boilerplate on many books — mixed
            # Latin content there is expected, not evidence of corruption.
            out["suspect"].append((book_id, chapter, vtt_path, frac))
    return out


def purge(conn: sqlite3.Connection, corrupt: list[tuple[int, int, str, float]]) -> int:
    """Delete corrupt zh rows + files AND the same chapters' en rows.

    The en row must go too: the batch driver's resume logic
    (``skip_chapters=existing_en`` in scripts/batch-translate.py) skips any
    chapter that still has an English row, so a zh-only purge would leave
    the chapter permanently untranslated. Deleting both forces a full
    re-process (STT + MT) of exactly these chapters; the en VTT file on
    disk is left in place and simply overwritten by the redo.

    Returns zh rows deleted. Caller commits.
    """
    deleted = 0
    for book_id, chapter, vtt_path, _frac in corrupt:
        conn.execute(
            "DELETE FROM chapter_subtitles WHERE audiobook_id = ? AND "
            "chapter_index = ? AND locale IN ('zh-Hans', 'en')",
            (book_id, chapter),
        )
        deleted += 1
        try:
            Path(vtt_path).unlink(missing_ok=True)
        except OSError as exc:
            print(f"  WARNING: row deleted but file remains ({exc}): {vtt_path}")
    return deleted


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument(
        "--db",
        default=os.environ.get("AUDIOBOOKS_DATABASE", ""),
        help="audiobooks database path (default: $AUDIOBOOKS_DATABASE)",
    )
    ap.add_argument("--delete-corrupt", action="store_true")
    ap.add_argument("--yes", action="store_true", help="confirm deletion")
    ap.add_argument(
        "--book-id",
        type=int,
        default=None,
        help="scope the audit/purge to one audiobook id (pilot runs)",
    )
    args = ap.parse_args()
    if not args.db:
        ap.error("--db required (or set AUDIOBOOKS_DATABASE)")

    mode = "rw" if args.delete_corrupt else "ro"
    conn = sqlite3.connect(f"file:{args.db}?mode={mode}", uri=True)
    try:
        result = scan(conn)
        if args.book_id is not None:
            result = {
                bucket: [r for r in rows if r[0] == args.book_id] for bucket, rows in result.items()
            }
        corrupt, suspect, missing = result["corrupt"], result["suspect"], result["missing"]
        trivial = result.get("trivial", [])
        by_book = Counter(b for b, _c, _p, _f in corrupt)
        print(
            f"corrupt (CJK < {CORRUPT_MAX_CJK_FRACTION:.0%}): {len(corrupt)} files "
            f"across {len(by_book)} books"
        )
        for book, n in by_book.most_common(10):
            print(f"  book {book}: {n} corrupt file(s)")
        print(
            f"suspect mixed-content (non-ch0, CJK < {SUSPECT_MAX_CJK_FRACTION:.0%}): "
            f"{len(suspect)} — report only, review by hand"
        )
        for book, chapter, _p, frac in suspect[:10]:
            print(f"  book {book} ch {chapter}: cjk={frac:.2f}")
        if trivial:
            print(
                f"trivial (<{TRIVIAL_MAX_CHARS} chars: markers, sound cues, silence): "
                f"{len(trivial)} — expected, never purged"
            )
        if missing:
            print(f"rows whose VTT file is missing on disk: {len(missing)}")

        if args.delete_corrupt and corrupt:
            if not args.yes:
                print("\nRefusing to delete without --yes")
                return 2
            deleted = purge(conn, corrupt)
            conn.commit()
            print(
                f"\ndeleted {deleted} corrupt rows + files — the resumable batch "
                "driver will re-translate exactly these chapters"
            )
            return 0

        return 1 if corrupt else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
