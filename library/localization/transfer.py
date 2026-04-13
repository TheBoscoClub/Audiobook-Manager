#!/usr/bin/env python3
"""Export and import translation assets between environments.

Bundles VTT subtitle files, TTS audio files, and database rows into a
portable tarball that can be transferred between dev/test/qa/prod without
re-translating (GPU costs real money).

Usage:
    python3 -m localization.transfer export --db /path/to/audiobooks.db --out translations.tar.gz
    python3 -m localization.transfer import --db /path/to/audiobooks.db --archive translations.tar.gz
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def export_translations(db_path: str, output: str, locale: str | None = None) -> dict:
    """Export translation assets to a portable tarball.

    Returns a summary dict with counts for logging/display.
    """
    conn = _connect(db_path)

    # ── Subtitle VTTs (always include English source + translated) ──
    en_subs = conn.execute(
        "SELECT * FROM chapter_subtitles WHERE locale = 'en'",
    ).fetchall()

    if locale:
        subs = conn.execute(
            "SELECT * FROM chapter_subtitles WHERE locale = ?",
            (locale,),
        ).fetchall()
    else:
        subs = conn.execute(
            "SELECT * FROM chapter_subtitles WHERE locale != 'en'",
        ).fetchall()

    # ── TTS audio ──
    if locale:
        audio = conn.execute(
            "SELECT * FROM chapter_translations_audio WHERE locale = ?",
            (locale,),
        ).fetchall()
    else:
        audio = conn.execute(
            "SELECT * FROM chapter_translations_audio",
        ).fetchall()

    # ── Metadata translations (titles, authors, descriptions) ──
    if locale:
        meta = conn.execute(
            "SELECT * FROM audiobook_translations WHERE locale = ?",
            (locale,),
        ).fetchall()
    else:
        meta = conn.execute(
            "SELECT * FROM audiobook_translations",
        ).fetchall()

    # ── Collection translations (genre/series names) ──
    collections = []
    if _table_exists(conn, "collection_translations"):
        if locale:
            collections = conn.execute(
                "SELECT * FROM collection_translations WHERE locale = ?",
                (locale,),
            ).fetchall()
        else:
            collections = conn.execute(
                "SELECT * FROM collection_translations",
            ).fetchall()

    # ── String translations (UI strings from admin content) ──
    strings = []
    if _table_exists(conn, "string_translations"):
        if locale:
            strings = conn.execute(
                "SELECT * FROM string_translations WHERE locale = ?",
                (locale,),
            ).fetchall()
        else:
            strings = conn.execute(
                "SELECT * FROM string_translations",
            ).fetchall()

    # ── Completed queue entries (for skip-on-import logic) ──
    if locale:
        queue = conn.execute(
            "SELECT * FROM translation_queue WHERE state = 'completed' AND locale = ?",
            (locale,),
        ).fetchall()
    else:
        queue = conn.execute(
            "SELECT * FROM translation_queue WHERE state = 'completed'",
        ).fetchall()

    # ── Book metadata for title-based matching on import ──
    books = {}
    for row in conn.execute("SELECT id, title, file_path FROM audiobooks").fetchall():
        books[row["id"]] = {"title": row["title"], "file_path": row["file_path"]}
    conn.close()

    manifest = {
        "version": 2,
        "subtitles": [dict(r) for r in en_subs] + [dict(r) for r in subs],
        "translated_audio": [dict(r) for r in audio],
        "audiobook_translations": [dict(r) for r in meta],
        "collection_translations": [dict(r) for r in collections],
        "string_translations": [dict(r) for r in strings],
        "queue_completed": [dict(r) for r in queue],
        "books": books,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

        with tarfile.open(output, "w:gz") as tar:
            tar.add(str(manifest_path), arcname="manifest.json")

            seen_files: set[str] = set()
            for row in list(en_subs) + list(subs):
                vtt = row["vtt_path"]
                if vtt and Path(vtt).exists() and vtt not in seen_files:
                    tar.add(vtt, arcname=f"vtt/{Path(vtt).name}")
                    seen_files.add(vtt)

            for row in audio:
                ap = row["audio_path"]
                if ap and Path(ap).exists() and ap not in seen_files:
                    tar.add(ap, arcname=f"audio/{Path(ap).name}")
                    seen_files.add(ap)

    summary = {
        "en_subtitles": len(en_subs),
        "translated_subtitles": len(subs),
        "audio_files": len(audio),
        "metadata": len(meta),
        "collections": len(collections),
        "strings": len(strings),
        "books": len(books),
        "output": output,
    }
    total = (
        summary["en_subtitles"]
        + summary["translated_subtitles"]
        + summary["audio_files"]
    )
    print(
        f"Exported {total} chapter assets + {len(meta)} metadata + "
        f"{len(collections)} collection + {len(strings)} string translations"
    )
    print(
        f"  {len(en_subs)} EN subtitles, {len(subs)} translated subtitles, "
        f"{len(audio)} audio files from {len(books)} books"
    )
    print(f"  Archive: {output}")
    return summary


def import_translations(db_path: str, archive: str) -> dict:
    """Import translation assets from a portable tarball.

    Books are matched by title (IDs differ between environments).
    File paths are rewritten to the target environment's directory structure.
    Returns a summary dict with counts.
    """
    conn = _connect(db_path)

    books_by_title: dict[str, int] = {}
    for row in conn.execute("SELECT id, title FROM audiobooks").fetchall():
        books_by_title[row["title"]] = row["id"]

    with tarfile.open(archive, "r:gz") as tar:
        mf = tar.extractfile("manifest.json")
        if not mf:
            print("ERROR: No manifest.json in archive", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(mf.read())

    # Build ID mapping: source environment book ID → target environment book ID
    id_map: dict[int, int] = {}
    for old_id_str, info in manifest.get("books", {}).items():
        old_id = int(old_id_str)
        title = info["title"]
        if title in books_by_title:
            id_map[old_id] = books_by_title[title]

    if not id_map:
        print(
            "WARNING: No matching books found by title. "
            "Ensure the target database has the same audiobooks.",
            file=sys.stderr,
        )
        return {"matched": 0, "total_books": len(manifest.get("books", {}))}

    # Look up target book file paths for directory placement
    target_book_paths: dict[int, Path] = {}
    for row in conn.execute("SELECT id, file_path FROM audiobooks").fetchall():
        target_book_paths[row["id"]] = Path(row["file_path"]).parent

    with tarfile.open(archive, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}

        # ── Import subtitles ──
        imported_subs = 0
        for sub in manifest.get("subtitles", []):
            new_id = id_map.get(sub["audiobook_id"])
            if not new_id:
                continue
            old_vtt = sub["vtt_path"]
            vtt_name = Path(old_vtt).name if old_vtt else None
            arc_key = f"vtt/{vtt_name}" if vtt_name else None

            new_vtt_path = old_vtt
            if arc_key and arc_key in members and new_id in target_book_paths:
                dest_dir = target_book_paths[new_id] / "subtitles"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / vtt_name

                tar_member = members[arc_key]
                tar_member.name = vtt_name
                tar.extract(tar_member, path=str(dest_dir))
                new_vtt_path = str(dest_path)

            conn.execute(
                "INSERT OR REPLACE INTO chapter_subtitles "
                "(audiobook_id, chapter_index, chapter_title, locale, "
                " vtt_path, stt_provider, translation_provider) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id,
                    sub["chapter_index"],
                    sub.get("chapter_title"),
                    sub["locale"],
                    new_vtt_path,
                    sub.get("stt_provider"),
                    sub.get("translation_provider"),
                ),
            )
            imported_subs += 1

        # ── Import TTS audio ──
        imported_audio = 0
        for aud in manifest.get("translated_audio", []):
            new_id = id_map.get(aud["audiobook_id"])
            if not new_id:
                continue
            old_path = aud["audio_path"]
            audio_name = Path(old_path).name if old_path else None
            arc_key = f"audio/{audio_name}" if audio_name else None

            new_audio_path = old_path
            if arc_key and arc_key in members and new_id in target_book_paths:
                dest_dir = target_book_paths[new_id] / "translated"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / audio_name

                tar_member = members[arc_key]
                tar_member.name = audio_name
                tar.extract(tar_member, path=str(dest_dir))
                new_audio_path = str(dest_path)

            conn.execute(
                "INSERT OR REPLACE INTO chapter_translations_audio "
                "(audiobook_id, chapter_index, locale, audio_path, "
                " tts_provider, tts_voice, duration_seconds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id,
                    aud["chapter_index"],
                    aud["locale"],
                    new_audio_path,
                    aud.get("tts_provider"),
                    aud.get("tts_voice"),
                    aud.get("duration_seconds"),
                ),
            )
            imported_audio += 1

        # ── Import metadata translations ──
        imported_meta = 0
        for meta in manifest.get("audiobook_translations", []):
            new_id = id_map.get(meta["audiobook_id"])
            if not new_id:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO audiobook_translations "
                "(audiobook_id, locale, title, author_display, "
                " series_display, description, translator, pinyin_sort) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id,
                    meta["locale"],
                    meta.get("title"),
                    meta.get("author_display"),
                    meta.get("series_display"),
                    meta.get("description"),
                    meta.get("translator"),
                    meta.get("pinyin_sort"),
                ),
            )
            imported_meta += 1

        # ── Import collection translations ──
        imported_collections = 0
        if _table_exists(conn, "collection_translations"):
            for col in manifest.get("collection_translations", []):
                conn.execute(
                    "INSERT OR REPLACE INTO collection_translations "
                    "(collection_id, locale, name, translator) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        col["collection_id"],
                        col["locale"],
                        col["name"],
                        col.get("translator"),
                    ),
                )
                imported_collections += 1

        # ── Import string translations ──
        imported_strings = 0
        if _table_exists(conn, "string_translations"):
            for st in manifest.get("string_translations", []):
                conn.execute(
                    "INSERT OR REPLACE INTO string_translations "
                    "(source_hash, locale, source, translation, translator) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        st["source_hash"],
                        st["locale"],
                        st["source"],
                        st["translation"],
                        st.get("translator"),
                    ),
                )
                imported_strings += 1

        # ── Mark imported books as completed in translation queue ──
        for qe in manifest.get("queue_completed", []):
            new_id = id_map.get(qe["audiobook_id"])
            if not new_id:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO translation_queue "
                "(audiobook_id, locale, priority, state, step, finished_at) "
                "VALUES (?, ?, 0, 'completed', 'tts', CURRENT_TIMESTAMP)",
                (new_id, qe["locale"]),
            )

    conn.commit()
    conn.close()

    matched = len(id_map)
    total_books = len(manifest.get("books", {}))
    summary = {
        "matched": matched,
        "total_books": total_books,
        "subtitles": imported_subs,
        "audio": imported_audio,
        "metadata": imported_meta,
        "collections": imported_collections,
        "strings": imported_strings,
    }
    print(
        f"Imported: {imported_subs} subtitles, {imported_audio} audio files, "
        f"{imported_meta} metadata, {imported_collections} collections, "
        f"{imported_strings} strings"
    )
    print(f"Matched {matched}/{total_books} books by title")
    if matched < total_books:
        unmatched = total_books - matched
        print(f"  {unmatched} books had no title match in target DB (skipped)")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Transfer translation assets between environments",
        epilog="Examples:\n"
        "  Export all:     %(prog)s export -o translations.tar.gz\n"
        "  Export zh only: %(prog)s export -o zh.tar.gz --locale zh-Hans\n"
        "  Import:         %(prog)s import -a translations.tar.gz\n"
        "\n"
        "The --db flag defaults to $AUDIOBOOKS_DATABASE from your config.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    db_default = os.environ.get("AUDIOBOOKS_DATABASE", "audiobooks.db")
    parser.add_argument(
        "--db",
        default=db_default,
        help="Path to audiobooks.db (default: $AUDIOBOOKS_DATABASE)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="Export translation assets to tarball")
    exp.add_argument("--out", "-o", required=True, help="Output tarball path")
    exp.add_argument("--locale", help="Export only this locale (default: all)")

    imp = sub.add_parser("import", help="Import translation assets from tarball")
    imp.add_argument("--archive", "-a", required=True, help="Input tarball path")

    args = parser.parse_args()
    if args.command == "export":
        export_translations(args.db, args.out, locale=getattr(args, "locale", None))
    elif args.command == "import":
        import_translations(args.db, args.archive)


if __name__ == "__main__":
    main()
