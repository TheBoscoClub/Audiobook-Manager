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


# ── Export query registry (no f-string SQL — literal queries only) ──
_EXPORT_QUERIES: dict[str, tuple[str, str]] = {
    # key: (query_all, query_locale)
    "subs_en": ("SELECT * FROM chapter_subtitles WHERE locale = 'en'", ""),
    "subs_other": (
        "SELECT * FROM chapter_subtitles WHERE locale != 'en'",
        "SELECT * FROM chapter_subtitles WHERE locale = ?",
    ),
    "audio": (
        "SELECT * FROM chapter_translations_audio",
        "SELECT * FROM chapter_translations_audio WHERE locale = ?",
    ),
    "meta": (
        "SELECT * FROM audiobook_translations",
        "SELECT * FROM audiobook_translations WHERE locale = ?",
    ),
    "collections": (
        "SELECT * FROM collection_translations",
        "SELECT * FROM collection_translations WHERE locale = ?",
    ),
    "strings": (
        "SELECT * FROM string_translations",
        "SELECT * FROM string_translations WHERE locale = ?",
    ),
    "queue": (
        "SELECT * FROM translation_queue WHERE state = 'completed'",
        "SELECT * FROM translation_queue WHERE state = 'completed' AND locale = ?",
    ),
}


def _fetch_locale(
    conn: sqlite3.Connection, key: str, locale: str | None
) -> list[sqlite3.Row]:
    """Run an export query, with/without a locale filter."""
    q_all, q_locale = _EXPORT_QUERIES[key]
    if locale and q_locale:
        return conn.execute(q_locale, (locale,)).fetchall()
    return conn.execute(q_all).fetchall()


def _fetch_optional(
    conn: sqlite3.Connection, table: str, key: str, locale: str | None
) -> list[sqlite3.Row]:
    """Like _fetch_locale but returns [] when the table doesn't exist."""
    if not _table_exists(conn, table):
        return []
    return _fetch_locale(conn, key, locale)


def _load_export_data(
    conn: sqlite3.Connection, locale: str | None
) -> dict:
    """Pull every export-relevant row + book directory map from the DB."""
    en_subs = _fetch_locale(conn, "subs_en", None)
    subs = _fetch_locale(conn, "subs_other", locale)
    audio = _fetch_locale(conn, "audio", locale)
    meta = _fetch_locale(conn, "meta", locale)
    collections = _fetch_optional(conn, "collection_translations", "collections", locale)
    strings = _fetch_optional(conn, "string_translations", "strings", locale)
    queue = _fetch_locale(conn, "queue", locale)

    books: dict = {}
    for row in conn.execute("SELECT id, title, file_path FROM audiobooks").fetchall():
        books[row["id"]] = {"title": row["title"], "file_path": row["file_path"]}

    return {
        "en_subs": en_subs,
        "subs": subs,
        "audio": audio,
        "meta": meta,
        "collections": collections,
        "strings": strings,
        "queue": queue,
        "books": books,
    }


def _build_manifest(data: dict) -> dict:
    """Build the manifest dict written into the tarball."""
    return {
        "version": 2,
        "subtitles": [dict(r) for r in data["en_subs"]]
        + [dict(r) for r in data["subs"]],
        "translated_audio": [dict(r) for r in data["audio"]],
        "audiobook_translations": [dict(r) for r in data["meta"]],
        "collection_translations": [dict(r) for r in data["collections"]],
        "string_translations": [dict(r) for r in data["strings"]],
        "queue_completed": [dict(r) for r in data["queue"]],
        "books": data["books"],
    }


def _write_tarball(output: str, manifest: dict, data: dict) -> None:
    """Write manifest.json + referenced VTT/audio files into the output tarball."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

        with tarfile.open(output, "w:gz") as tar:
            tar.add(str(manifest_path), arcname="manifest.json")

            seen_files: set[str] = set()
            for row in list(data["en_subs"]) + list(data["subs"]):
                vtt = row["vtt_path"]
                if vtt and Path(vtt).exists() and vtt not in seen_files:
                    tar.add(vtt, arcname=f"vtt/{Path(vtt).name}")
                    seen_files.add(vtt)

            for row in data["audio"]:
                ap = row["audio_path"]
                if ap and Path(ap).exists() and ap not in seen_files:
                    tar.add(ap, arcname=f"audio/{Path(ap).name}")
                    seen_files.add(ap)


def _print_export_summary(summary: dict, data: dict, output: str) -> None:
    """Emit the two human-readable lines + archive path."""
    total = (
        summary["en_subtitles"]
        + summary["translated_subtitles"]
        + summary["audio_files"]
    )
    print(
        f"Exported {total} chapter assets + {len(data['meta'])} metadata + "
        f"{len(data['collections'])} collection + {len(data['strings'])} string translations"
    )
    print(
        f"  {len(data['en_subs'])} EN subtitles, {len(data['subs'])} translated subtitles, "
        f"{len(data['audio'])} audio files from {len(data['books'])} books"
    )
    print(f"  Archive: {output}")


def export_translations(db_path: str, output: str, locale: str | None = None) -> dict:
    """Export translation assets to a portable tarball.

    Returns a summary dict with counts for logging/display.
    """
    conn = _connect(db_path)
    data = _load_export_data(conn, locale)
    conn.close()

    manifest = _build_manifest(data)
    _write_tarball(output, manifest, data)

    summary = {
        "en_subtitles": len(data["en_subs"]),
        "translated_subtitles": len(data["subs"]),
        "audio_files": len(data["audio"]),
        "metadata": len(data["meta"]),
        "collections": len(data["collections"]),
        "strings": len(data["strings"]),
        "books": len(data["books"]),
        "output": output,
    }
    _print_export_summary(summary, data, output)
    return summary


_INSERT_SUBTITLE = (
    "INSERT OR REPLACE INTO chapter_subtitles "
    "(audiobook_id, chapter_index, chapter_title, locale, "
    " vtt_path, stt_provider, translation_provider) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_AUDIO = (
    "INSERT OR REPLACE INTO chapter_translations_audio "
    "(audiobook_id, chapter_index, locale, audio_path, "
    " tts_provider, tts_voice, duration_seconds) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_META = (
    "INSERT OR REPLACE INTO audiobook_translations "
    "(audiobook_id, locale, title, author_display, "
    " series_display, description, translator, pinyin_sort) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_COLLECTION = (
    "INSERT OR REPLACE INTO collection_translations "
    "(collection_id, locale, name, translator) "
    "VALUES (?, ?, ?, ?)"
)
_INSERT_STRING = (
    "INSERT OR REPLACE INTO string_translations "
    "(source_hash, locale, source, translation, translator) "
    "VALUES (?, ?, ?, ?, ?)"
)
_INSERT_QUEUE = (
    "INSERT OR IGNORE INTO translation_queue "
    "(audiobook_id, locale, priority, state, step, finished_at) "
    "VALUES (?, ?, 0, 'completed', 'tts', CURRENT_TIMESTAMP)"
)


def _read_manifest(archive: str) -> dict:
    """Open the archive and read the embedded manifest.json."""
    with tarfile.open(archive, "r:gz") as tar:
        mf = tar.extractfile("manifest.json")
        if not mf:
            print("ERROR: No manifest.json in archive", file=sys.stderr)
            sys.exit(1)
        return json.loads(mf.read())


def _build_id_map(
    manifest: dict, books_by_title: dict[str, int]
) -> dict[int, int]:
    """Map source-env book IDs → target-env book IDs by title."""
    id_map: dict[int, int] = {}
    for old_id_str, info in manifest.get("books", {}).items():
        old_id = int(old_id_str)
        title = info["title"]
        if title in books_by_title:
            id_map[old_id] = books_by_title[title]
    return id_map


def _extract_file_to_dest(
    tar: tarfile.TarFile,
    members: dict,
    arc_key: str | None,
    new_id: int,
    target_book_paths: dict[int, Path],
    subdir: str,
    filename: str | None,
    default_path: str,
) -> str:
    """Extract a file from the tar to its destination dir; return the final path."""
    if not (arc_key and arc_key in members and new_id in target_book_paths):
        return default_path
    dest_dir = target_book_paths[new_id] / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    tar_member = members[arc_key]
    tar_member.name = filename
    tar.extract(tar_member, path=str(dest_dir))
    return str(dest_path)


def _import_subtitles(
    conn: sqlite3.Connection,
    tar: tarfile.TarFile,
    members: dict,
    manifest: dict,
    id_map: dict[int, int],
    target_book_paths: dict[int, Path],
) -> int:
    count = 0
    for sub in manifest.get("subtitles", []):
        new_id = id_map.get(sub["audiobook_id"])
        if not new_id:
            continue
        old_vtt = sub["vtt_path"]
        vtt_name = Path(old_vtt).name if old_vtt else None
        arc_key = f"vtt/{vtt_name}" if vtt_name else None
        new_vtt_path = _extract_file_to_dest(
            tar, members, arc_key, new_id, target_book_paths,
            "subtitles", vtt_name, old_vtt,
        )
        conn.execute(
            _INSERT_SUBTITLE,
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
        count += 1
    return count


def _import_audio(
    conn: sqlite3.Connection,
    tar: tarfile.TarFile,
    members: dict,
    manifest: dict,
    id_map: dict[int, int],
    target_book_paths: dict[int, Path],
) -> int:
    count = 0
    for aud in manifest.get("translated_audio", []):
        new_id = id_map.get(aud["audiobook_id"])
        if not new_id:
            continue
        old_path = aud["audio_path"]
        audio_name = Path(old_path).name if old_path else None
        arc_key = f"audio/{audio_name}" if audio_name else None
        new_audio_path = _extract_file_to_dest(
            tar, members, arc_key, new_id, target_book_paths,
            "translated", audio_name, old_path,
        )
        conn.execute(
            _INSERT_AUDIO,
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
        count += 1
    return count


def _import_metadata(
    conn: sqlite3.Connection, manifest: dict, id_map: dict[int, int]
) -> int:
    count = 0
    for meta in manifest.get("audiobook_translations", []):
        new_id = id_map.get(meta["audiobook_id"])
        if not new_id:
            continue
        conn.execute(
            _INSERT_META,
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
        count += 1
    return count


def _import_collections(conn: sqlite3.Connection, manifest: dict) -> int:
    if not _table_exists(conn, "collection_translations"):
        return 0
    count = 0
    for col in manifest.get("collection_translations", []):
        conn.execute(
            _INSERT_COLLECTION,
            (
                col["collection_id"],
                col["locale"],
                col["name"],
                col.get("translator"),
            ),
        )
        count += 1
    return count


def _import_strings(conn: sqlite3.Connection, manifest: dict) -> int:
    if not _table_exists(conn, "string_translations"):
        return 0
    count = 0
    for st in manifest.get("string_translations", []):
        conn.execute(
            _INSERT_STRING,
            (
                st["source_hash"],
                st["locale"],
                st["source"],
                st["translation"],
                st.get("translator"),
            ),
        )
        count += 1
    return count


def _mark_queue_completed(
    conn: sqlite3.Connection, manifest: dict, id_map: dict[int, int]
) -> None:
    for qe in manifest.get("queue_completed", []):
        new_id = id_map.get(qe["audiobook_id"])
        if not new_id:
            continue
        conn.execute(_INSERT_QUEUE, (new_id, qe["locale"]))


def _print_import_summary(summary: dict) -> None:
    print(
        f"Imported: {summary['subtitles']} subtitles, {summary['audio']} audio files, "
        f"{summary['metadata']} metadata, {summary['collections']} collections, "
        f"{summary['strings']} strings"
    )
    print(f"Matched {summary['matched']}/{summary['total_books']} books by title")
    if summary["matched"] < summary["total_books"]:
        unmatched = summary["total_books"] - summary["matched"]
        print(f"  {unmatched} books had no title match in target DB (skipped)")


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

    manifest = _read_manifest(archive)
    id_map = _build_id_map(manifest, books_by_title)

    if not id_map:
        print(
            "WARNING: No matching books found by title. "
            "Ensure the target database has the same audiobooks.",
            file=sys.stderr,
        )
        return {"matched": 0, "total_books": len(manifest.get("books", {}))}

    target_book_paths: dict[int, Path] = {}
    for row in conn.execute("SELECT id, file_path FROM audiobooks").fetchall():
        target_book_paths[row["id"]] = Path(row["file_path"]).parent

    with tarfile.open(archive, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        imported_subs = _import_subtitles(
            conn, tar, members, manifest, id_map, target_book_paths
        )
        imported_audio = _import_audio(
            conn, tar, members, manifest, id_map, target_book_paths
        )
        imported_meta = _import_metadata(conn, manifest, id_map)
        imported_collections = _import_collections(conn, manifest)
        imported_strings = _import_strings(conn, manifest)
        _mark_queue_completed(conn, manifest, id_map)

    conn.commit()
    conn.close()

    summary = {
        "matched": len(id_map),
        "total_books": len(manifest.get("books", {})),
        "subtitles": imported_subs,
        "audio": imported_audio,
        "metadata": imported_meta,
        "collections": imported_collections,
        "strings": imported_strings,
    }
    _print_import_summary(summary)
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
