#!/usr/bin/env python3
"""
Corrupted / zero-byte library file report, split by REMEDY.

Every zero-byte file found under the Library tree is reported — the walk
deliberately keeps both canonicality exclusions OFF, because a zero-byte
cover-art sidecar or a zero-byte ``translated/`` chapter artifact is still a
damaged file worth listing. Detection is never reduced.

What this report used to get wrong (Audiobook-Manager-aw9): it framed *every*
row as "needs re-downloading". That is impossible to act on for a derived
artifact — nobody can re-download ``Book.ch001.zh-Hans.opus`` from Audible,
because it was never downloaded in the first place. The defect was the report
conflating two different remedies, not the walk. So each finding now carries a
``remedy``:

- ``re-download`` — source media (the ``.m4b``/``.opus``/``.m4a``/``.mp3``
  book file, or a stray ``.aaxc`` original). The bytes are gone and only the
  vendor has them. Fetch it again from your Audible library.
- ``regenerate`` — a derived artifact that this system produced from source
  media it still has: a ``translated/`` per-chapter translation artifact, or a
  ``<title>.cover.<ext>`` cover-art sidecar. Delete it and re-run the pipeline
  that makes it; re-downloading would fix nothing.

The remedy is decided by ``is_canonical_audiobook_file`` from
``scanner.utils.canonical`` — the same predicate that defines "derived" for
every other walk in the tree. There is deliberately no second copy of that
rule here: if the definition of a derived artifact ever changes, it changes in
one place and this report follows.

Outputs:
- ``missing_audiobooks.csv`` — every finding, with a ``remedy`` column
  appended. Existing columns keep their names and order, so
  ``create_priority_list.py`` (a ``csv.DictReader`` consumer) is unaffected.
- ``missing_audiobooks.txt`` — the same findings, in two sections with the
  instructions that actually apply to each.
"""

import csv
import sys
from pathlib import Path

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AUDIOBOOK_DIR
from scanner.utils.canonical import is_canonical_audiobook_file, iter_library_files

# Extensions this report considers. Deliberately NOT ``SUPPORTED_FORMATS``:
# ``.aaxc`` is included because a zero-byte Audible original that landed in
# the Library tree is exactly the corruption this report exists to surface.
CORRUPTION_SCAN_PATTERNS = ("*.m4b", "*.opus", "*.m4a", "*.mp3", "*.aaxc")

# Remedy labels. These are the two genuinely different actions an operator can
# take; every finding is one or the other.
REMEDY_REDOWNLOAD = "re-download"
REMEDY_REGENERATE = "regenerate"

# Order used everywhere findings are grouped, so CSV, text file and console
# all agree. Source media first — it is the remedy that costs bandwidth.
REMEDY_ORDER = (REMEDY_REDOWNLOAD, REMEDY_REGENERATE)

REMEDY_BLURB = {
    REMEDY_REDOWNLOAD: "source media — the bytes are gone, fetch them from Audible again",
    REMEDY_REGENERATE: "derived artifact — rebuild it locally from source media you still have",
}

OUTPUT_CSV = Path("missing_audiobooks.csv")
OUTPUT_TXT = Path("missing_audiobooks.txt")

CSV_FIELDNAMES = ["title", "filename", "directory", "extension", "path", "remedy"]


def classify_remedy(filepath: Path) -> str:
    """Return the remedy label for a damaged ``filepath``.

    Derived artifacts (``translated/`` chapter files and ``.cover.<ext>``
    sidecars) are regenerable; everything else is source media that has to be
    downloaded again. The "is this derived?" question is answered by
    ``is_canonical_audiobook_file`` rather than re-implemented here, so this
    report cannot drift from the rest of the scanner.
    """
    return REMEDY_REDOWNLOAD if is_canonical_audiobook_file(filepath) else REMEDY_REGENERATE


def find_corrupted_files():
    """Find all empty or corrupted audiobook files, each tagged with a remedy."""
    corrupted = []

    # Find all audiobook files. Walked via the canonical library iterator
    # (Audiobook-Manager-fud). Both canonicality exclusions are switched OFF
    # deliberately: this is a byte-level corruption report, not an ingest
    # path, and a zero-byte cover-art sidecar or a zero-byte
    # ``translated/`` chapter artifact is still a damaged file worth
    # listing. Turning either on would silently shrink the report — the
    # ``remedy`` field, not the walk, is what separates the two audiences
    # (Audiobook-Manager-aw9).
    for filepath in iter_library_files(
        AUDIOBOOK_DIR,
        CORRUPTION_SCAN_PATTERNS,
        exclude_cover_art=False,
        exclude_translated=False,
    ):
        ext = filepath.suffix.lower()
        # Check if file is empty (0 bytes)
        if filepath.stat().st_size == 0:
            # Extract title from filename
            title = filepath.stem

            # Try to clean up the title
            title_clean = title.replace("_", " ")

            # Remove quality indicators
            for quality in ["-AAX 44 128", "-AAX 22 64", "-AAX_44_128", "-AAX_22_64"]:
                title_clean = title_clean.replace(quality, "")

            corrupted.append(
                {
                    "title": title_clean.strip(),
                    "filename": filepath.name,
                    "path": str(filepath.relative_to(AUDIOBOOK_DIR.parent)),
                    "directory": filepath.parent.name,
                    "extension": ext,
                    "remedy": classify_remedy(filepath),
                }
            )

    return corrupted


def _group_by_directory(corrupted: list[dict]) -> dict[str, list[dict]]:
    """Group corrupted file entries by their parent directory name."""
    by_directory: dict[str, list[dict]] = {}
    for item in corrupted:
        by_directory.setdefault(item["directory"], []).append(item)
    return by_directory


def _group_by_remedy(corrupted: list[dict]) -> dict[str, list[dict]]:
    """Group corrupted file entries by remedy, in ``REMEDY_ORDER``.

    Every remedy in ``REMEDY_ORDER`` gets a key even when empty, so callers
    can report "0 files need re-downloading" rather than staying silent about
    a category they did not check.
    """
    by_remedy: dict[str, list[dict]] = {remedy: [] for remedy in REMEDY_ORDER}
    for item in corrupted:
        by_remedy.setdefault(item["remedy"], []).append(item)
    return by_remedy


def _save_csv(corrupted: list[dict]) -> None:
    """Save corrupted files list to CSV."""
    print(f"Saving list to {OUTPUT_CSV}...")
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(corrupted)


def _write_redownload_section(f, items: list[dict]) -> None:
    """Write the re-download (source media) section of the text report."""
    f.write(f"\n{'=' * 80}\n")
    f.write(f"RE-DOWNLOAD — {len(items)} source file(s)\n")
    f.write(f"{'=' * 80}\n\n")
    f.write("These are source media. The bytes are gone and cannot be rebuilt\n")
    f.write("locally — get them from the vendor again.\n\n")
    f.write("INSTRUCTIONS:\n")
    f.write("1. Log in to your Audible account\n")
    f.write("2. Go to your Library\n")
    f.write("3. Search for each title below\n")
    f.write("4. Download the audiobook file\n")
    f.write("5. Run the scanner again to update your library\n\n")
    _write_directory_listing(f, items)


def _write_regenerate_section(f, items: list[dict]) -> None:
    """Write the regenerate (derived artifact) section of the text report."""
    f.write(f"\n{'=' * 80}\n")
    f.write(f"REGENERATE — {len(items)} derived artifact(s)\n")
    f.write(f"{'=' * 80}\n\n")
    f.write("These were produced by this system from source media it still has.\n")
    f.write("Re-downloading would fix nothing — rebuild them instead.\n\n")
    f.write("INSTRUCTIONS:\n")
    f.write("1. Delete the damaged artifact listed below\n")
    f.write("2. Re-run the pipeline that produces it:\n")
    f.write("   - files under translated/ -> the chapter-translation pipeline\n")
    f.write("   - <title>.cover.<ext> sidecars -> cover-art extraction\n")
    f.write("3. Run the scanner again to confirm the artifact came back non-empty\n\n")
    _write_directory_listing(f, items)


def _write_directory_listing(f, items: list[dict]) -> None:
    """Write ``items`` grouped by parent directory to an open text file."""
    for dir_name, dir_items in sorted(_group_by_directory(items).items()):
        f.write(f"{'-' * 80}\n")
        f.write(f"DIRECTORY: {dir_name} ({len(dir_items)} files)\n")
        f.write(f"{'-' * 80}\n\n")

        for idx, item in enumerate(dir_items, 1):
            f.write(f"{idx}. {item['title']}\n")
            f.write(f"   File: {item['filename']}\n")
            f.write(f"   Path: {item['path']}\n")
            f.write("\n")


def _save_txt(corrupted: list[dict], by_remedy: dict[str, list[dict]]) -> None:
    """Save corrupted files list to human-readable text file, split by remedy."""
    print(f"Saving list to {OUTPUT_TXT}...")
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("CORRUPTED / ZERO-BYTE LIBRARY FILES\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total: {len(corrupted)} damaged file(s), split by what actually fixes them\n\n")
        for remedy in REMEDY_ORDER:
            f.write(f"  {remedy:<12} {len(by_remedy[remedy]):>5}  ({REMEDY_BLURB[remedy]})\n")
        f.write("\n")
        f.write("=" * 80 + "\n")

        _write_redownload_section(f, by_remedy[REMEDY_REDOWNLOAD])
        _write_regenerate_section(f, by_remedy[REMEDY_REGENERATE])


def _print_sample(by_remedy: dict[str, list[dict]]) -> None:
    """Print a sample of each remedy bucket and the output file locations."""
    for remedy in REMEDY_ORDER:
        items = by_remedy[remedy]
        print("=" * 80)
        print(f"{remedy.upper()} — {len(items)} file(s): {REMEDY_BLURB[remedy]}")
        print("=" * 80)
        if not items:
            print("  (none)")
            print()
            continue
        for idx, item in enumerate(items[:20], 1):
            print(f"{idx}. {item['title']}")
            print(f"   Directory: {item['directory']}")
        if len(items) > 20:
            print(f"... and {len(items) - 20} more")
        print()

    print("=" * 80)
    print("COMPLETE LIST SAVED TO:")
    print(f"  • {OUTPUT_CSV.absolute()} (CSV format, 'remedy' column)")
    print(f"  • {OUTPUT_TXT.absolute()} (Text format, one section per remedy)")
    print("=" * 80)
    print()
    print("TIP: Filter the CSV on the 'remedy' column — 're-download' rows need")
    print("     the vendor, 'regenerate' rows only need a pipeline re-run.")
    print()


def main():
    print("Scanning for corrupted/empty audiobook files...")
    print()

    corrupted = find_corrupted_files()

    if not corrupted:
        print("✓ No corrupted files found! All audiobooks are valid.")
        return

    corrupted.sort(key=lambda x: (REMEDY_ORDER.index(x["remedy"]), x["title"].lower()))
    print(f"Found {len(corrupted)} corrupted/empty audiobook files")
    print()

    by_remedy = _group_by_remedy(corrupted)

    print("Breakdown by remedy:")
    for remedy in REMEDY_ORDER:
        print(f"  {remedy:<12} {len(by_remedy[remedy]):>5} files")
    print()

    print("Breakdown by directory:")
    for dir_name, items in sorted(_group_by_directory(corrupted).items()):
        print(f"  {dir_name}: {len(items)} files")
    print()

    _save_csv(corrupted)
    _save_txt(corrupted, by_remedy)
    _print_sample(by_remedy)


if __name__ == "__main__":
    main()
