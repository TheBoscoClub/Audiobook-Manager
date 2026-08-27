"""
Behaviour tests for ``scanner.utils.canonical`` — the single place that knows
how to walk the Library and Sources trees (Audiobook-Manager-6cx, extended
by Audiobook-Manager-fud).

The exclusions these assert are the ones that were previously re-derived at
every call site, and forgotten at one of them: ``translated/`` chapter
artifacts leaking into the conversion counter (Audiobook-Manager-94p) and the
grouped-library API (Audiobook-Manager-2sw). The defaults are what a new
caller gets for free, so they are what most needs pinning down.
"""

from scanner.utils.canonical import (
    is_canonical_audiobook_file,
    iter_canonical_audiobook_files,
    iter_library_files,
    iter_source_files,
)


def _build_library(root):
    """A library tree containing one of every shape that matters."""
    book = root / "Author" / "Book"
    book.mkdir(parents=True)
    (book / "Book.opus").write_bytes(b"x")
    (book / "Book.cover.opus").write_bytes(b"x")  # cover-art sidecar
    (book / "chapters.json").write_text("{}")
    (book / "Book.m4b").write_bytes(b"x")
    translated = book / "translated"
    translated.mkdir()
    (translated / "Book.ch001.zh-Hans.opus").write_bytes(b"x")
    (translated / "chapters.json").write_text("{}")
    # A book whose TITLE contains the word, which must NOT be excluded.
    other = root / "Author" / "The Translated Soldier"
    other.mkdir(parents=True)
    (other / "The Translated Soldier.opus").write_bytes(b"x")
    return root


class TestIsCanonicalAudiobookFile:
    def test_plain_audio_file_is_canonical(self, temp_dir):
        assert is_canonical_audiobook_file(temp_dir / "Book.opus")

    def test_cover_art_sidecar_is_not(self, temp_dir):
        assert not is_canonical_audiobook_file(temp_dir / "Book.cover.opus")

    def test_translated_subdirectory_is_not(self, temp_dir):
        assert not is_canonical_audiobook_file(temp_dir / "translated" / "Book.ch001.opus")

    def test_title_containing_the_word_still_is(self, temp_dir):
        assert is_canonical_audiobook_file(temp_dir / "The Translated Soldier" / "Book.opus")


class TestIterLibraryFilesDefaults:
    def test_defaults_exclude_cover_art_and_translated(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        names = sorted(p.name for p in iter_library_files(root, ("*.opus",)))
        assert names == ["Book.opus", "The Translated Soldier.opus"]

    def test_non_audio_pattern_uses_the_same_exclusions(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        found = list(iter_library_files(root, ("chapters.json",)))
        assert len(found) == 1, found
        assert "translated" not in found[0].parts

    def test_missing_root_yields_nothing_without_raising(self, temp_dir):
        assert list(iter_library_files(temp_dir / "nope", ("*.opus",))) == []

    def test_patterns_are_applied_in_order(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        suffixes = [p.suffix for p in iter_library_files(root, ("*.m4b", "*.opus"))]
        assert suffixes == [".m4b", ".opus", ".opus"]


class TestIterLibraryFilesOptOuts:
    def test_cover_art_opt_out_restores_the_sidecar(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        names = sorted(
            p.name for p in iter_library_files(root, ("*.opus",), exclude_cover_art=False)
        )
        assert "Book.cover.opus" in names

    def test_translated_opt_out_restores_the_chapter_artifact(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        names = sorted(
            p.name for p in iter_library_files(root, ("*.opus",), exclude_translated=False)
        )
        assert "Book.ch001.zh-Hans.opus" in names

    def test_both_opt_outs_match_a_bare_rglob(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        canonical = sorted(
            iter_library_files(root, ("*.opus",), exclude_cover_art=False, exclude_translated=False)
        )
        assert canonical == sorted(root.rglob("*.opus"))


class TestIterCanonicalAudiobookFiles:
    def test_defaults_to_all_supported_formats(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        suffixes = {p.suffix for p in iter_canonical_audiobook_files(root)}
        assert suffixes == {".m4b", ".opus"}

    def test_format_filter_narrows_the_walk(self, temp_dir):
        root = _build_library(temp_dir / "Library")
        names = sorted(p.name for p in iter_canonical_audiobook_files(root, formats=[".opus"]))
        assert names == ["Book.opus", "The Translated Soldier.opus"]

    def test_missing_root_yields_nothing(self, temp_dir):
        assert list(iter_canonical_audiobook_files(temp_dir / "nope")) == []


class TestIterSourceFiles:
    def test_finds_aaxc_recursively(self, temp_dir):
        sources = temp_dir / "Sources"
        (sources / "nested").mkdir(parents=True)
        (sources / "A.aaxc").write_bytes(b"x")
        (sources / "nested" / "B.aaxc").write_bytes(b"x")
        (sources / "A.voucher").write_text("{}")
        assert sorted(p.name for p in iter_source_files(sources)) == ["A.aaxc", "B.aaxc"]

    def test_missing_sources_dir_yields_nothing_without_raising(self, temp_dir):
        assert list(iter_source_files(temp_dir / "nope")) == []

    def test_no_library_exclusions_are_applied(self, temp_dir):
        """Sources has no cover-art or translated/ concept; nothing is filtered."""
        sources = temp_dir / "Sources"
        (sources / "translated").mkdir(parents=True)
        (sources / "translated" / "X.aaxc").write_bytes(b"x")
        (sources / "Y.cover.aaxc").write_bytes(b"x")
        assert sorted(p.name for p in iter_source_files(sources)) == ["X.aaxc", "Y.cover.aaxc"]

    def test_custom_patterns(self, temp_dir):
        sources = temp_dir / "Sources"
        sources.mkdir()
        (sources / "A.voucher").write_text("{}")
        (sources / "A.aaxc").write_bytes(b"x")
        assert [p.name for p in iter_source_files(sources, ("*.voucher",))] == ["A.voucher"]
