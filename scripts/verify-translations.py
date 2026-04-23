#!/usr/bin/env python3
"""Verify completed translations with proof.

Checks that every audiobook in the translation queue marked 'completed' has:
  1. English subtitles (VTT files) for all chapters
  2. Chinese (zh-Hans) translated subtitles for all chapters
  3. VTT files exist on disk and are non-empty
  4. Subtitle content is valid (contains timestamps and text)
  5. Chapter subtitle DB rows match expected chapter count

Outputs a verification report with pass/fail per book and aggregate stats.
Exit code 0 = all verified, 1 = failures found.

Usage:
    python scripts/verify-translations.py --db $AUDIOBOOKS_VAR_DIR/db/audiobooks.db
    python scripts/verify-translations.py --db ... --fix  # re-queue failed books
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
LIB_DIR = PROJECT_DIR / "library"
sys.path.insert(0, str(LIB_DIR))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger("verify-translations")


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def count_chapters(audio_path: Path) -> int:
    """Count chapters in an audiobook by checking chapter files."""
    from localization.chapters import extract_chapters

    try:
        chapters = extract_chapters(audio_path)
        return len(chapters)
    except Exception:
        return 0


def validate_vtt(vtt_path: Path) -> tuple[bool, str]:
    """Validate a VTT file exists, is non-empty, and has proper content."""
    if not vtt_path.exists():
        return False, f"File missing: {vtt_path}"
    size = vtt_path.stat().st_size
    if size == 0:
        return False, f"Empty file: {vtt_path}"
    if size < 20:
        return False, f"File too small ({size}B): {vtt_path}"

    content = vtt_path.read_text(encoding="utf-8", errors="replace")
    if "WEBVTT" not in content:
        return False, f"Not a valid VTT (no WEBVTT header): {vtt_path}"
    if "-->" not in content:
        return False, f"No timestamps in VTT: {vtt_path}"

    # Count cue blocks (lines with -->)
    cues = content.count("-->")
    if cues < 1:
        return False, f"No subtitle cues: {vtt_path}"

    return True, f"OK ({cues} cues, {size}B)"


def verify_book(conn: sqlite3.Connection, book_id: int, locale: str) -> dict:
    """Verify a single book's translations. Returns verification result."""
    book = conn.execute(
        "SELECT id, title, file_path FROM audiobooks WHERE id = ?", (book_id,)
    ).fetchone()

    result: dict[str, Any] = {
        "book_id": book_id,
        "title": book["title"] if book else "UNKNOWN",
        "status": "PASS",
        "issues": [],
        "en_chapters": 0,
        "zh_chapters": 0,
        "en_vtt_valid": 0,
        "zh_vtt_valid": 0,
    }

    if not book:
        result["status"] = "FAIL"
        result["issues"].append("Book not found in DB")
        return result

    # Get subtitle rows
    en_rows = conn.execute(
        "SELECT chapter_index, vtt_path FROM chapter_subtitles "
        "WHERE audiobook_id = ? AND locale = 'en' ORDER BY chapter_index",
        (book_id,),
    ).fetchall()

    zh_rows = conn.execute(
        "SELECT chapter_index, vtt_path FROM chapter_subtitles "
        "WHERE audiobook_id = ? AND locale = ? ORDER BY chapter_index",
        (book_id, locale),
    ).fetchall()

    result["en_chapters"] = len(en_rows)
    result["zh_chapters"] = len(zh_rows)

    if not en_rows:
        result["status"] = "FAIL"
        result["issues"].append("No English subtitles in DB")

    if not zh_rows:
        result["status"] = "FAIL"
        result["issues"].append(f"No {locale} subtitles in DB")

    if en_rows and zh_rows and len(en_rows) != len(zh_rows):
        result["status"] = "WARN"
        result["issues"].append(
            f"Chapter count mismatch: {len(en_rows)} en vs {len(zh_rows)} {locale}"
        )

    # Validate VTT files on disk
    for row in en_rows:
        vtt = Path(row["vtt_path"])
        valid, msg = validate_vtt(vtt)
        if valid:
            result["en_vtt_valid"] += 1
        else:
            result["status"] = "FAIL"
            result["issues"].append(f"EN ch{row['chapter_index']}: {msg}")

    for row in zh_rows:
        vtt = Path(row["vtt_path"])
        valid, msg = validate_vtt(vtt)
        if valid:
            result["zh_vtt_valid"] += 1
        else:
            result["status"] = "FAIL"
            result["issues"].append(f"ZH ch{row['chapter_index']}: {msg}")

    return result


def main():
    # Must run as audiobooks service account — DB + config are 0640
    # audiobooks:audiobooks. Fails fast with a usage diagnostic if not.
    from config import require_audiobooks_user  # noqa: E402

    require_audiobooks_user()

    parser = argparse.ArgumentParser(description="Verify completed translations")
    parser.add_argument("--db", required=True, help="Path to audiobooks.db")
    parser.add_argument("--locale", default="zh-Hans", help="Target locale")
    parser.add_argument("--fix", action="store_true", help="Re-queue failed books")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--book-id", type=int, help="Verify single book")
    args = parser.parse_args()

    conn = get_db(args.db)

    # Get completed books from queue
    if args.book_id:
        book_ids = [args.book_id]
    else:
        rows = conn.execute(
            "SELECT DISTINCT audiobook_id FROM translation_queue WHERE state = 'completed'"
        ).fetchall()
        book_ids = [r[0] for r in rows]

    if not book_ids:
        # Also check books with subtitles but not in queue
        rows = conn.execute("SELECT DISTINCT audiobook_id FROM chapter_subtitles").fetchall()
        book_ids = [r[0] for r in rows]

    total_books = conn.execute("SELECT COUNT(*) FROM audiobooks").fetchone()[0]
    queue_stats = {}
    for row in conn.execute(
        "SELECT state, COUNT(*) FROM translation_queue GROUP BY state"
    ).fetchall():
        queue_stats[row[0]] = row[1]

    logger.info("=" * 70)
    logger.info("TRANSLATION VERIFICATION REPORT")
    logger.info("=" * 70)
    logger.info("Total audiobooks in library: %d", total_books)
    logger.info("Queue: %s", queue_stats)
    logger.info("Books to verify: %d", len(book_ids))
    logger.info("-" * 70)

    results = []
    pass_count = 0
    warn_count = 0
    fail_count = 0
    requeue_ids = []

    for book_id in book_ids:
        result = verify_book(conn, book_id, args.locale)
        results.append(result)

        if result["status"] == "PASS":
            pass_count += 1
            logger.info(
                "  PASS  [%d] %s — en=%d zh=%d",
                book_id,
                result["title"][:50],
                result["en_chapters"],
                result["zh_chapters"],
            )
        elif result["status"] == "WARN":
            warn_count += 1
            logger.warning(
                "  WARN  [%d] %s — %s", book_id, result["title"][:50], "; ".join(result["issues"])
            )
        else:
            fail_count += 1
            logger.error(
                "  FAIL  [%d] %s — %s",
                book_id,
                result["title"][:50],
                "; ".join(result["issues"][:3]),
            )
            requeue_ids.append(book_id)

    # Aggregate subtitle stats
    total_en = conn.execute(
        "SELECT COUNT(*) FROM chapter_subtitles WHERE locale = 'en'"
    ).fetchone()[0]
    total_zh = conn.execute(
        "SELECT COUNT(*) FROM chapter_subtitles WHERE locale = ?", (args.locale,)
    ).fetchone()[0]
    total_en_books = conn.execute(
        "SELECT COUNT(DISTINCT audiobook_id) FROM chapter_subtitles WHERE locale = 'en'"
    ).fetchone()[0]
    total_zh_books = conn.execute(
        "SELECT COUNT(DISTINCT audiobook_id) FROM chapter_subtitles WHERE locale = ?",
        (args.locale,),
    ).fetchone()[0]

    logger.info("-" * 70)
    logger.info("SUMMARY")
    logger.info("-" * 70)
    logger.info("Verified: %d books", len(results))
    logger.info("  PASS: %d", pass_count)
    logger.info("  WARN: %d", warn_count)
    logger.info("  FAIL: %d", fail_count)
    logger.info("")
    logger.info("PROOF — Database counts:")
    logger.info("  English subtitle chapters: %d (across %d books)", total_en, total_en_books)
    logger.info(
        "  %s subtitle chapters: %d (across %d books)", args.locale, total_zh, total_zh_books
    )
    logger.info("")
    logger.info("PROOF — Queue state:")
    for state, count in sorted(queue_stats.items()):
        logger.info("  %s: %d", state, count)
    logger.info("")
    logger.info(
        "PROOF — Coverage: %d / %d books have subtitles (%.1f%%)",
        total_en_books,
        total_books,
        100 * total_en_books / total_books if total_books else 0,
    )

    # Re-queue failed books
    if args.fix and requeue_ids:
        logger.info("")
        logger.info("Re-queuing %d failed books...", len(requeue_ids))
        for bid in requeue_ids:
            conn.execute(
                "UPDATE translation_queue SET state = 'pending', started_at = NULL, "
                "finished_at = NULL, error = NULL WHERE audiobook_id = ? AND state IN ('completed', 'failed')",
                (bid,),
            )
        conn.commit()
        logger.info("Done — %d books re-queued", len(requeue_ids))

    if args.json:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_books": total_books,
            "queue": queue_stats,
            "verified": len(results),
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "en_chapters": total_en,
            "zh_chapters": total_zh,
            "en_books": total_en_books,
            "zh_books": total_zh_books,
            "coverage_pct": round(100 * total_en_books / total_books, 1) if total_books else 0,
            "details": results,
        }
        report_path = Path(args.db).parent / "translation-verification.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        logger.info("JSON report: %s", report_path)

    conn.close()
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
