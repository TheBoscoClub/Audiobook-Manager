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
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def export_translations(db_path: str, output: str, locale: str | None = None) -> None:
    conn = _connect(db_path)

    en_subs = conn.execute(
        "SELECT * FROM chapter_subtitles WHERE locale = 'en'",
    ).fetchall()

    if locale:
        subs = conn.execute(
            "SELECT * FROM chapter_subtitles WHERE locale = ?",
            (locale,),
        ).fetchall()
        audio = conn.execute(
            "SELECT * FROM chapter_translations_audio WHERE locale = ?",
            (locale,),
        ).fetchall()
        meta = conn.execute(
            "SELECT * FROM audiobook_translations WHERE locale = ?",
            (locale,),
        ).fetchall()
        queue = conn.execute(
            "SELECT * FROM translation_queue WHERE state = 'completed' AND locale = ?",
            (locale,),
        ).fetchall()
    else:
        subs = conn.execute(
            "SELECT * FROM chapter_subtitles WHERE locale != 'en'",
        ).fetchall()
        audio = conn.execute(
            "SELECT * FROM chapter_translations_audio",
        ).fetchall()
        meta = conn.execute(
            "SELECT * FROM audiobook_translations",
        ).fetchall()
        queue = conn.execute(
            "SELECT * FROM translation_queue WHERE state = 'completed'",
        ).fetchall()

    books = {}
    for row in conn.execute("SELECT id, title, file_path FROM audiobooks").fetchall():
        books[row["id"]] = {"title": row["title"], "file_path": row["file_path"]}
    conn.close()

    manifest = {
        "version": 1,
        "subtitles": [dict(r) for r in en_subs + list(subs)],
        "translated_audio": [dict(r) for r in audio],
        "audiobook_translations": [dict(r) for r in meta],
        "queue_completed": [dict(r) for r in queue],
        "books": books,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

        with tarfile.open(output, "w:gz") as tar:
            tar.add(str(manifest_path), arcname="manifest.json")

            seen_files: set[str] = set()
            for row in en_subs + list(subs):
                vtt = row["vtt_path"]
                if vtt and Path(vtt).exists() and vtt not in seen_files:
                    tar.add(vtt, arcname=f"vtt/{Path(vtt).name}")
                    seen_files.add(vtt)

            for row in audio:
                ap = row["audio_path"]
                if ap and Path(ap).exists() and ap not in seen_files:
                    tar.add(ap, arcname=f"audio/{Path(ap).name}")
                    seen_files.add(ap)

    total = len(en_subs) + len(subs) + len(audio)
    print(f"Exported {total} assets ({len(en_subs)} en subs, {len(subs)} translated subs, "
          f"{len(audio)} audio files) to {output}")


def import_translations(db_path: str, archive: str) -> None:
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

    id_map: dict[int, int] = {}
    for old_id_str, info in manifest.get("books", {}).items():
        old_id = int(old_id_str)
        title = info["title"]
        if title in books_by_title:
            id_map[old_id] = books_by_title[title]

    if not id_map:
        print("WARNING: No matching books found by title. "
              "Ensure the target database has the same audiobooks.", file=sys.stderr)
        return

    with tarfile.open(archive, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}

        target_book_paths: dict[int, Path] = {}
        for row in conn.execute("SELECT id, file_path FROM audiobooks").fetchall():
            target_book_paths[row["id"]] = Path(row["file_path"]).parent

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
                "(audiobook_id, chapter_index, locale, vtt_path, stt_provider, translation_provider) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_id, sub["chapter_index"], sub["locale"],
                 new_vtt_path, sub.get("stt_provider"), sub.get("translation_provider")),
            )
            imported_subs += 1

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
                (new_id, aud["chapter_index"], aud["locale"],
                 new_audio_path, aud.get("tts_provider"),
                 aud.get("tts_voice"), aud.get("duration_seconds")),
            )
            imported_audio += 1

        imported_meta = 0
        for meta in manifest.get("audiobook_translations", []):
            new_id = id_map.get(meta["audiobook_id"])
            if not new_id:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO audiobook_translations "
                "(audiobook_id, locale, title, author_display, "
                " series_display, description, translated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id, meta["locale"], meta.get("title"),
                 meta.get("author_display"), meta.get("series_display"),
                 meta.get("description"), meta.get("translated_at")),
            )
            imported_meta += 1

    conn.commit()
    conn.close()

    matched = len(id_map)
    total_books = len(manifest.get("books", {}))
    print(f"Imported: {imported_subs} subtitles, {imported_audio} audio files, "
          f"{imported_meta} metadata entries")
    print(f"Matched {matched}/{total_books} books by title")


def main():
    parser = argparse.ArgumentParser(description="Transfer translation assets between environments")
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="Export translation assets to tarball")
    exp.add_argument("--db", required=True, help="Path to audiobooks.db")
    exp.add_argument("--out", "-o", required=True, help="Output tarball path")
    exp.add_argument("--locale", help="Export only this locale (default: all)")

    imp = sub.add_parser("import", help="Import translation assets from tarball")
    imp.add_argument("--db", required=True, help="Path to audiobooks.db")
    imp.add_argument("--archive", "-a", required=True, help="Input tarball path")

    args = parser.parse_args()
    if args.command == "export":
        export_translations(args.db, args.out, locale=getattr(args, "locale", None))
    elif args.command == "import":
        import_translations(args.db, args.archive)


if __name__ == "__main__":
    main()
