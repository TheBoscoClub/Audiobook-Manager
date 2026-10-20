"""Coverage-focused unit tests for ``library.localization.pipeline`` and
its sibling modules ``chapters`` and ``stt/whisper_stt``.

Intent:

- Exercise the small, pure helpers produced by the Phase 6 F3 complexity
  refactor (``_chapter_stem``, ``_write_translated_chapter_vtt``,
  ``_process_one_chapter``, ``_handle_no_chapters``, ``_stt_by_explicit_name``,
  ``_remote_stt_candidates``) without hitting any GPU provider.
- Cover ``chapters.extract_chapters`` via both ffprobe-success and
  sidecar-fallback paths, plus ``split_chapter`` success and failure.
- Cover ``whisper_stt.WhisperSTT`` construction, supports_language, and the
  RunPod submit → poll → parse flow using mocked HTTP.
- The legacy Vast.ai serverless and dedicated-instance Whisper modules
  were retired (Vast.ai serverless removed in this release; dedicated
  Vast.ai instance retired earlier in v8.3.2). All STT now flows through
  RunPod serverless or the self-hosted whisper-gpu service.

Everything here is network-free — each test patches subprocess / requests
before invoking the module under test.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# chapters.py
# ---------------------------------------------------------------------------


class TestChapter:
    """Cover the ``Chapter`` dataclass properties."""

    def test_duration_ms_is_end_minus_start(self):
        from localization.chapters import Chapter

        ch = Chapter(index=0, title="Intro", start_ms=1_000, end_ms=4_000)
        assert ch.duration_ms == 3_000

    def test_start_and_end_sec_are_millisecond_fractions(self):
        from localization.chapters import Chapter

        ch = Chapter(index=0, title="Intro", start_ms=1_500, end_ms=3_250)
        assert ch.start_sec == pytest.approx(1.5)
        assert ch.end_sec == pytest.approx(3.25)


class TestExtractChapters:
    """Cover the ffprobe → sidecar fallback chain in ``extract_chapters``."""

    def test_returns_chapters_from_ffprobe(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        ffprobe_payload = {
            "chapters": [
                {"id": 0, "start_time": "0.0", "end_time": "60.0", "tags": {"title": "Start"}},
                {"id": 1, "start_time": "60.0", "end_time": "120.0", "tags": {"title": "Mid"}},
            ]
        }
        fake = MagicMock(returncode=0, stdout=json.dumps(ffprobe_payload), stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod.extract_chapters(audio)
        assert len(result) == 2
        assert result[0].title == "Start"
        assert result[0].start_ms == 0
        assert result[1].end_ms == 120_000

    def test_ffprobe_title_missing_uses_synthetic_chapter_number(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        ffprobe_payload = {"chapters": [{"id": 5, "start_time": "0", "end_time": "10"}]}
        fake = MagicMock(returncode=0, stdout=json.dumps(ffprobe_payload), stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod.extract_chapters(audio)
        # id=5 → "Chapter 6" per the synthesis formula (id + 1).
        assert result[0].title == "Chapter 6"

    def test_ffprobe_nonzero_return_code_falls_back_to_empty(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        fake = MagicMock(returncode=1, stdout="", stderr="error")
        with patch.object(mod.subprocess, "run", return_value=fake):
            assert mod._chapters_from_ffprobe(audio) == ([], None)

    def test_ffprobe_timeout_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")

        def boom(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

        with patch.object(mod.subprocess, "run", side_effect=boom):
            assert mod._chapters_from_ffprobe(audio) == ([], None)

    def test_ffprobe_json_decode_error_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        fake = MagicMock(returncode=0, stdout="not-json", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            assert mod._chapters_from_ffprobe(audio) == ([], None)

    def test_sidecar_fallback_when_ffprobe_returns_nothing(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        sidecar = tmp_path / "chapters.json"
        sidecar.write_text(
            json.dumps(
                {
                    "content_metadata": {
                        "chapter_info": {
                            "chapters": [
                                {"title": "Prologue", "start_offset_ms": 0, "length_ms": 10_000}
                            ]
                        }
                    }
                }
            )
        )
        empty = MagicMock(returncode=0, stdout=json.dumps({"chapters": []}), stderr="")
        with patch.object(mod.subprocess, "run", return_value=empty):
            result = mod.extract_chapters(audio)
        assert len(result) == 1
        assert result[0].title == "Prologue"
        assert result[0].end_ms == 10_000

    def test_sidecar_missing_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        assert mod._chapters_from_sidecar(audio) == []

    def test_sidecar_invalid_json_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        (tmp_path / "chapters.json").write_text("{not json")
        assert mod._chapters_from_sidecar(audio) == []

    def test_sidecar_without_chapter_info_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        (tmp_path / "chapters.json").write_text(json.dumps({"content_metadata": {}}))
        assert mod._chapters_from_sidecar(audio) == []

    def test_sidecar_with_empty_chapter_list_returns_empty(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        (tmp_path / "chapters.json").write_text(
            json.dumps({"content_metadata": {"chapter_info": {"chapters": []}}})
        )
        assert mod._chapters_from_sidecar(audio) == []

    def test_sidecar_missing_title_uses_synthetic(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        (tmp_path / "chapters.json").write_text(
            json.dumps(
                {
                    "content_metadata": {
                        "chapter_info": {"chapters": [{"start_offset_ms": 0, "length_ms": 1_000}]}
                    }
                }
            )
        )
        result = mod._chapters_from_sidecar(audio)
        assert result[0].title == "Chapter 1"


class TestSplitChapter:
    """Cover ``split_chapter`` — both explicit output_dir and tmpfile paths."""

    def test_split_chapter_with_output_dir(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        out_dir = tmp_path / "out"
        chapter = mod.Chapter(index=3, title="Three", start_ms=0, end_ms=1_000)

        fake = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod.split_chapter(audio, chapter, output_dir=out_dir)
        assert out_dir.exists()
        assert result.name == "ch003.opus"

    def test_split_chapter_without_output_dir_uses_tmpfile(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapter = mod.Chapter(index=7, title="Seven", start_ms=0, end_ms=1_000)

        fake = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod.split_chapter(audio, chapter)
        # NamedTemporaryFile returns an absolute path outside tmp_path.
        assert result.exists()
        # Clean up — split_chapter contract is caller deletes.
        result.unlink(missing_ok=True)

    def test_split_chapter_without_extension_defaults_to_opus(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book_no_extension"
        audio.write_bytes(b"x")
        out_dir = tmp_path / "out"
        chapter = mod.Chapter(index=0, title="X", start_ms=0, end_ms=1_000)

        fake = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod.split_chapter(audio, chapter, output_dir=out_dir)
        assert result.suffix == ".opus"

    def test_split_chapter_nonzero_return_raises(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        out_dir = tmp_path / "out"
        chapter = mod.Chapter(index=0, title="X", start_ms=0, end_ms=1_000)

        fake = MagicMock(returncode=1, stdout="", stderr="ffmpeg exploded")
        with patch.object(mod.subprocess, "run", return_value=fake):
            with pytest.raises(RuntimeError, match="Failed to extract chapter"):
                mod.split_chapter(audio, chapter, output_dir=out_dir)


# ---------------------------------------------------------------------------
# pipeline.py helpers
# ---------------------------------------------------------------------------


class TestStemAndProviders:
    """Cover ``_chapter_stem`` + STT explicit-name mapping + fallback errors."""

    def test_chapter_stem_sanitises_title(self):
        from localization import pipeline as mod

        ch = MagicMock(index=5, title="Chapter V: The End!")
        stem = mod._chapter_stem(ch)
        assert stem.startswith("ch005_")
        # '!' and ':' should be replaced with underscores.
        assert "!" not in stem
        assert ":" not in stem

    def test_chapter_stem_caps_title_at_50_chars(self):
        from localization import pipeline as mod

        ch = MagicMock(index=0, title="A" * 100)
        stem = mod._chapter_stem(ch)
        # "ch000_" prefix plus the 50-char truncated title.
        assert len(stem) == len("ch000_") + 50

    def test_stt_by_explicit_name_empty_returns_none(self):
        from localization import pipeline as mod

        assert mod._stt_by_explicit_name("") is None

    def test_stt_by_explicit_name_local_raises_migration_error(self):
        from localization import pipeline as mod

        with pytest.raises(ValueError, match="Local CPU Whisper has been removed"):
            mod._stt_by_explicit_name("local")

    def test_stt_by_explicit_name_whisper_missing_credentials_raises(self):
        from localization import pipeline as mod

        with patch.object(mod, "RUNPOD_API_KEY", ""):
            with patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""):
                with pytest.raises(ValueError, match="RunPod Whisper requested"):
                    mod._stt_by_explicit_name("whisper")

    def test_stt_by_explicit_name_local_gpu_returns_provider(self):
        from localization import pipeline as mod

        with patch.object(mod, "WHISPER_GPU_HOST", "gpu.local"):
            with patch.object(mod, "WHISPER_GPU_PORT", 9100):
                provider = mod._stt_by_explicit_name("local-gpu")
        assert provider is not None
        assert provider.__class__.__name__ == "LocalGPUWhisperSTT"

    def test_stt_by_explicit_name_unknown_returns_none(self):
        from localization import pipeline as mod

        assert mod._stt_by_explicit_name("not-a-provider") is None

    def test_get_stt_provider_empty_auto_empty_list_raises(self):
        from localization import pipeline as mod

        with (
            patch.object(mod, "RUNPOD_API_KEY", ""),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
            patch.object(mod, "STT_PROVIDER", ""),
        ):
            with pytest.raises(RuntimeError, match="No STT provider configured"):
                mod.get_stt_provider()

    def test_get_stt_provider_picks_first_remote_candidate(self):
        from localization import pipeline as mod

        fake = MagicMock(name="whisper")
        fake.name = "whisper"
        with (
            patch.object(mod, "STT_PROVIDER", ""),
            patch.object(mod, "_remote_stt_candidates", return_value=[fake]),
        ):
            chosen = mod.get_stt_provider()
        assert chosen is fake

    def test_remote_stt_candidates_runpod_only(self):
        from localization import pipeline as mod

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", "e"),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            result = mod._remote_stt_candidates()
        assert len(result) == 1
        assert result[0].name == "whisper"

    def test_remote_stt_candidates_local_gpu_unreachable_excluded(self):
        from localization import pipeline as mod

        with (
            patch.object(mod, "RUNPOD_API_KEY", ""),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", "gpu.local"),
            patch.object(mod, "WHISPER_GPU_PORT", 9100),
        ):
            with patch.object(mod.LocalGPUWhisperSTT, "is_available", return_value=False):
                result = mod._remote_stt_candidates()
        assert result == []

    def test_remote_stt_candidates_streaming_routes_to_streaming_endpoint(self):
        """STREAMING hint → RunPod streaming endpoint."""
        from localization import pipeline as mod
        from localization.selection import WorkloadHint

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", "stream-ep"),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", "backlog-ep"),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            result = mod._remote_stt_candidates(WorkloadHint.STREAMING)

        assert len(result) == 1
        assert result[0].name == "whisper"
        # _endpoint_id is defined on WhisperSTT (a concrete STTProvider subclass),
        # not on the abstract base; we've already asserted name=="whisper" so the
        # narrowing is safe — pyright just can't see it through the base type.
        assert result[0]._endpoint_id == "stream-ep"  # type: ignore[attr-defined]

    def test_remote_stt_candidates_long_form_routes_to_backlog_endpoint(self):
        """LONG_FORM hint → backlog endpoint (cold pool, cheap)."""
        from localization import pipeline as mod
        from localization.selection import WorkloadHint

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", "stream-ep"),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", "backlog-ep"),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            result = mod._remote_stt_candidates(WorkloadHint.LONG_FORM)

        assert len(result) == 1
        assert result[0].name == "whisper"
        assert result[0]._endpoint_id == "backlog-ep"  # type: ignore[attr-defined]

    def test_remote_stt_candidates_any_defaults_to_backlog(self):
        """Default (ANY) hint behaves like LONG_FORM — backlog endpoint."""
        from localization import pipeline as mod

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", "stream-ep"),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", "backlog-ep"),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            # No workload arg → WorkloadHint.ANY → backlog path.
            result = mod._remote_stt_candidates()

        assert len(result) == 1
        assert result[0]._endpoint_id == "backlog-ep"  # type: ignore[attr-defined]


class TestProcessOneChapter:
    """Cover ``_process_one_chapter`` — happy path, empty speech, and cleanup."""

    def _fake_chapter(self, tmp_path):
        from localization.chapters import Chapter

        return Chapter(index=1, title="Example", start_ms=0, end_ms=1_000)

    def test_process_one_chapter_returns_vtts_on_speech(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapter = self._fake_chapter(tmp_path)

        # Create a fake split chapter file that _process_one_chapter can unlink.
        split_file = tmp_path / "chapter_split.opus"
        split_file.write_bytes(b"y")

        provider = MagicMock()
        transcript = MagicMock()
        transcript.sentence_texts.return_value = ["Hello world.", "Goodbye."]

        with (
            patch.object(mod, "split_chapter", return_value=split_file),
            patch.object(mod, "_transcribe_with_fallback", return_value=transcript),
            patch.object(mod, "align_translations", return_value=([], [])),
            patch.object(mod, "generate_vtt", side_effect=lambda _c, p: p),
            patch.object(mod, "get_translation_provider", return_value=None),
        ):
            result = mod._process_one_chapter(audio, chapter, provider, tmp_path, "zh-Hans", "en")
        assert result is not None
        source_vtt, translated_vtt = result
        assert str(source_vtt).endswith(".en.vtt")
        assert translated_vtt is None
        # Ensure temporary chapter file was cleaned up.
        assert not split_file.exists()

    def test_process_one_chapter_returns_none_when_no_speech(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapter = self._fake_chapter(tmp_path)
        split_file = tmp_path / "empty_split.opus"
        split_file.write_bytes(b"y")

        provider = MagicMock()
        transcript = MagicMock()
        transcript.sentence_texts.return_value = []

        with (
            patch.object(mod, "split_chapter", return_value=split_file),
            patch.object(mod, "_transcribe_with_fallback", return_value=transcript),
        ):
            result = mod._process_one_chapter(audio, chapter, provider, tmp_path, "zh-Hans", "en")
        assert result is None
        # Even on no-speech, the chapter file should still be cleaned up.
        assert not split_file.exists()

    def test_process_one_chapter_cleans_up_after_exception(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapter = self._fake_chapter(tmp_path)
        split_file = tmp_path / "oops_split.opus"
        split_file.write_bytes(b"y")

        provider = MagicMock()

        with (
            patch.object(mod, "split_chapter", return_value=split_file),
            patch.object(mod, "_transcribe_with_fallback", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                mod._process_one_chapter(audio, chapter, provider, tmp_path, "zh-Hans", "en")
        # Cleanup must still have fired.
        assert not split_file.exists()


class TestHandleNoChapters:
    """Cover ``_handle_no_chapters`` — the single-file fallback branch."""

    def test_skipped_when_index_zero_in_skip_set(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        result = mod._handle_no_chapters(audio, tmp_path, "zh-Hans", "en", None, {0})
        assert result == []

    def test_single_file_delegates_to_generate_subtitles(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        with patch.object(
            mod, "generate_subtitles", return_value=(tmp_path / "s.vtt", tmp_path / "t.vtt")
        ):
            result = mod._handle_no_chapters(audio, tmp_path, "zh-Hans", "en", None, None)
        assert len(result) == 1
        assert result[0][0] == 0


class TestOffsetCues:
    """Cover ``_offset_cues`` — simple translation of cue timestamps."""

    def test_offset_cues_shifts_all_timestamps(self):
        from localization.pipeline import _offset_cues
        from localization.subtitles.vtt_generator import VTTCue

        cues = [
            VTTCue(start_ms=100, end_ms=200, text="A"),
            VTTCue(start_ms=300, end_ms=400, text="B"),
        ]
        out = _offset_cues(cues, offset_ms=1_000)
        assert out[0].start_ms == 1_100
        assert out[0].end_ms == 1_200
        assert out[1].start_ms == 1_300
        assert out[1].text == "B"

    def test_offset_cues_with_zero_offset_passthrough(self):
        from localization.pipeline import _offset_cues
        from localization.subtitles.vtt_generator import VTTCue

        cues = [VTTCue(start_ms=5, end_ms=10, text="C")]
        out = _offset_cues(cues, offset_ms=0)
        assert out[0].start_ms == 5
        assert out[0].end_ms == 10


class TestVTTGenerator:
    """Exercise the WebVTT file writer + timestamp formatter — small
    utility, but core to the streaming subtitles pipeline."""

    def test_format_timestamp_zero(self):
        from localization.subtitles.vtt_generator import _format_timestamp

        assert _format_timestamp(0) == "00:00:00.000"

    def test_format_timestamp_sub_second(self):
        from localization.subtitles.vtt_generator import _format_timestamp

        assert _format_timestamp(42) == "00:00:00.042"

    def test_format_timestamp_hours_minutes_seconds_millis(self):
        """Formatter must zero-pad every component to the VTT spec."""
        from localization.subtitles.vtt_generator import _format_timestamp

        ms = (1 * 3_600_000) + (23 * 60_000) + (45 * 1_000) + 678
        assert _format_timestamp(ms) == "01:23:45.678"

    def test_format_timestamp_ten_hour_book(self):
        """The VTT spec allows > 99 hours; our formatter doesn't clamp."""
        from localization.subtitles.vtt_generator import _format_timestamp

        assert _format_timestamp(10 * 3_600_000) == "10:00:00.000"

    def test_generate_vtt_writes_spec_header_and_cue_blocks(self, tmp_path):
        from localization.subtitles.vtt_generator import VTTCue, generate_vtt

        cues = [
            VTTCue(start_ms=0, end_ms=2_500, text="Hello, world."),
            VTTCue(start_ms=3_000, end_ms=5_000, text="Second line."),
        ]
        out = tmp_path / "subs" / "out.vtt"
        result = generate_vtt(cues, out)
        assert result == out
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        # Required VTT header + two numbered cues.
        assert text.startswith("WEBVTT\n")
        assert "00:00:00.000 --> 00:00:02.500" in text
        assert "00:00:03.000 --> 00:00:05.000" in text
        assert "Hello, world." in text
        assert "Second line." in text
        # Both cues are indexed.
        assert "\n1\n" in text
        assert "\n2\n" in text

    def test_generate_vtt_creates_missing_parent_dirs(self, tmp_path):
        """Callers frequently supply a deep path; generator must mkdir -p."""
        from localization.subtitles.vtt_generator import VTTCue, generate_vtt

        out = tmp_path / "a" / "b" / "c" / "deep.vtt"
        generate_vtt([VTTCue(0, 1000, "x")], out)
        assert out.exists()

    def test_generate_vtt_empty_cues_still_writes_header(self, tmp_path):
        from localization.subtitles.vtt_generator import generate_vtt

        out = tmp_path / "empty.vtt"
        generate_vtt([], out)
        assert out.read_text(encoding="utf-8").startswith("WEBVTT")

    def test_generate_dual_vtt_stacks_source_and_translation(self, tmp_path):
        from localization.subtitles.vtt_generator import VTTCue, generate_dual_vtt

        source = [VTTCue(0, 1_000, "Hello"), VTTCue(1_500, 2_500, "World")]
        translated = [VTTCue(0, 1_000, "你好"), VTTCue(1_500, 2_500, "世界")]
        out = tmp_path / "dual.vtt"
        generate_dual_vtt(source, translated, out)
        text = out.read_text(encoding="utf-8")
        # Each cue has both source and translation text on separate lines
        # and uses the SOURCE timing (not translated).
        assert "Hello" in text
        assert "你好" in text
        assert "World" in text
        assert "世界" in text
        assert "00:00:00.000 --> 00:00:01.000" in text

    def test_generate_dual_vtt_mismatched_lengths_raises(self, tmp_path):
        from localization.subtitles.vtt_generator import VTTCue, generate_dual_vtt

        source = [VTTCue(0, 1_000, "A"), VTTCue(1_000, 2_000, "B")]
        translated = [VTTCue(0, 1_000, "X")]  # mismatched length
        with pytest.raises(ValueError, match="mismatch"):
            generate_dual_vtt(source, translated, tmp_path / "bad.vtt")


class TestGenerateBookSubtitlesOrchestration:
    """Cover the top-level ``generate_book_subtitles`` orchestration —
    no-chapters fallback, progress callbacks, and skip-set filtering.
    """

    def test_no_chapters_hits_single_file_fallback(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        with (
            patch.object(mod, "extract_chapters", return_value=[]),
            patch.object(
                mod,
                "_handle_no_chapters",
                return_value=[(0, tmp_path / "s.vtt", tmp_path / "t.vtt")],
            ),
        ):
            result = mod.generate_book_subtitles(audio, tmp_path, "zh-Hans", "en")
        assert len(result) == 1

    def test_progress_and_complete_callbacks_fire_per_chapter(self, tmp_path):
        from localization import pipeline as mod
        from localization.chapters import Chapter

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapters = [
            Chapter(index=0, title="Zero", start_ms=0, end_ms=1_000),
            Chapter(index=1, title="One", start_ms=1_000, end_ms=2_000),
        ]
        progress_events = []
        complete_events = []

        def progress(i, n, title):
            progress_events.append((i, n, title))

        def complete(i, src, tr):
            complete_events.append((i, src, tr))

        provider = MagicMock()
        src_vtt = tmp_path / "src.vtt"
        src_vtt.write_text("src")

        with (
            patch.object(mod, "extract_chapters", return_value=chapters),
            patch.object(mod, "get_stt_provider", return_value=provider),
            patch.object(mod, "_process_one_chapter", return_value=(src_vtt, None)),
        ):
            result = mod.generate_book_subtitles(
                audio, tmp_path, "zh-Hans", "en", on_progress=progress, on_chapter_complete=complete
            )
        assert len(result) == 2
        assert len(progress_events) == 2
        assert len(complete_events) == 2

    def test_skip_set_skips_named_chapter(self, tmp_path):
        from localization import pipeline as mod
        from localization.chapters import Chapter

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapters = [
            Chapter(index=0, title="Zero", start_ms=0, end_ms=1_000),
            Chapter(index=1, title="One", start_ms=1_000, end_ms=2_000),
        ]

        src_vtt = tmp_path / "src.vtt"
        src_vtt.write_text("x")
        with (
            patch.object(mod, "extract_chapters", return_value=chapters),
            patch.object(mod, "get_stt_provider", return_value=MagicMock()),
            patch.object(mod, "_process_one_chapter", return_value=(src_vtt, None)) as proc,
        ):
            result = mod.generate_book_subtitles(
                audio, tmp_path, "zh-Hans", "en", skip_chapters={0}
            )
        # Only chapter 1 should be processed.
        assert len(result) == 1
        assert proc.call_count == 1

    def test_process_returning_none_is_skipped_in_results(self, tmp_path):
        from localization import pipeline as mod
        from localization.chapters import Chapter

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        chapters = [Chapter(index=0, title="Zero", start_ms=0, end_ms=1_000)]
        with (
            patch.object(mod, "extract_chapters", return_value=chapters),
            patch.object(mod, "get_stt_provider", return_value=MagicMock()),
            patch.object(mod, "_process_one_chapter", return_value=None),
        ):
            result = mod.generate_book_subtitles(audio, tmp_path, "zh-Hans", "en")
        assert result == []


# ---------------------------------------------------------------------------
# whisper_stt.py (RunPod client)
# ---------------------------------------------------------------------------


class TestWhisperSTT:
    """Cover the RunPod Whisper STT client's happy path and validation."""

    def test_missing_api_key_raises(self):
        from localization.stt.whisper_stt import WhisperSTT

        with pytest.raises(ValueError, match="RunPod API key is required"):
            WhisperSTT(api_key="", endpoint_id="e")

    def test_missing_endpoint_id_raises(self):
        from localization.stt.whisper_stt import WhisperSTT

        with pytest.raises(ValueError, match="endpoint ID is required"):
            WhisperSTT(api_key="k", endpoint_id="")

    def test_name_property_is_whisper(self):
        from localization.stt.whisper_stt import WhisperSTT

        assert WhisperSTT("k", "e").name == "whisper"

    def test_supports_language_strips_region_subtag(self):
        from localization.stt.whisper_stt import WhisperSTT

        provider = WhisperSTT("k", "e")
        # English without a region tag should be accepted.
        assert provider.supports_language("en") is True
        # en-US → whisper lookup should strip the "-US" part.
        assert provider.supports_language("en-US") is True

    def test_usage_remaining_returns_none(self):
        from localization.stt.whisper_stt import WhisperSTT

        assert WhisperSTT("k", "e").usage_remaining() is None

    def test_transcribe_missing_file_raises_fnf(self, tmp_path):
        from localization.stt.whisper_stt import WhisperSTT

        with pytest.raises(FileNotFoundError):
            WhisperSTT("k", "e").transcribe(tmp_path / "missing.opus")

    def test_transcribe_unsupported_language_raises(self, tmp_path):
        from localization.stt.whisper_stt import WhisperSTT

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        with pytest.raises(ValueError, match="not supported by Whisper"):
            WhisperSTT("k", "e").transcribe(audio, language="klingon")

    def test_transcribe_polls_and_parses_word_timestamps(self, tmp_path):
        from localization.stt import whisper_stt as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"abc")
        submit_resp = MagicMock()
        submit_resp.json.return_value = {"id": "job-123"}
        submit_resp.raise_for_status.return_value = None

        status_resp = MagicMock()
        status_resp.json.return_value = {
            "status": "COMPLETED",
            "output": {
                "word_timestamps": [
                    {"word": "hello", "start": 0.0, "end": 0.5},
                    {"word": "world", "start": 0.5, "end": 1.0},
                ],
                "duration": 1.0,
            },
        }
        status_resp.raise_for_status.return_value = None

        with (
            patch.object(mod.requests, "post", return_value=submit_resp),
            patch.object(mod.requests, "get", return_value=status_resp),
        ):
            transcript = mod.WhisperSTT("k", "e").transcribe(audio, language="en")

        assert transcript.provider == "whisper-large-v3"
        assert transcript.duration_ms == 1_000
        assert [w.word for w in transcript.words] == ["hello", "world"]
        assert transcript.words[0].start_ms == 0
        assert transcript.words[1].end_ms == 1_000

    def test_transcribe_falls_back_to_segments_when_no_top_level_words(self, tmp_path):
        from localization.stt import whisper_stt as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"abc")
        submit = MagicMock()
        submit.json.return_value = {"id": "j"}
        submit.raise_for_status.return_value = None
        status = MagicMock()
        status.json.return_value = {
            "status": "COMPLETED",
            "output": {"segments": [{"words": [{"word": "hi", "start": 0, "end": 0.1}]}]},
        }
        status.raise_for_status.return_value = None

        with (
            patch.object(mod.requests, "post", return_value=submit),
            patch.object(mod.requests, "get", return_value=status),
        ):
            transcript = mod.WhisperSTT("k", "e").transcribe(audio, language="en")
        assert len(transcript.words) == 1
        # Without explicit duration, falls back to last word's end_ms.
        assert transcript.duration_ms == 100

    def test_poll_job_raises_on_failed_status(self, tmp_path):
        from localization.stt import whisper_stt as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"abc")
        submit = MagicMock()
        submit.json.return_value = {"id": "j"}
        submit.raise_for_status.return_value = None
        failed = MagicMock()
        failed.json.return_value = {"status": "FAILED", "error": "GPU melted"}
        failed.raise_for_status.return_value = None
        with (
            patch.object(mod.requests, "post", return_value=submit),
            patch.object(mod.requests, "get", return_value=failed),
        ):
            with pytest.raises(RuntimeError, match="RunPod job FAILED"):
                mod.WhisperSTT("k", "e").transcribe(audio, language="en")

    def test_poll_job_times_out(self, tmp_path):
        from localization.stt import whisper_stt as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"abc")
        submit = MagicMock()
        submit.json.return_value = {"id": "j"}
        submit.raise_for_status.return_value = None
        in_progress = MagicMock()
        in_progress.json.return_value = {"status": "IN_PROGRESS"}
        in_progress.raise_for_status.return_value = None

        # Fake monotonic: first call is start, next is well past max_wait.
        times = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0])

        with (
            patch.object(mod.requests, "post", return_value=submit),
            patch.object(mod.requests, "get", return_value=in_progress),
            patch.object(mod.time, "monotonic", side_effect=lambda: next(times)),
            patch.object(mod.time, "sleep", return_value=None),
        ):
            with pytest.raises(TimeoutError, match="did not complete"):
                mod.WhisperSTT("k", "e").transcribe(audio, language="en")


# ── xtts.py ──


# requests_mock is a dev dependency; a deployed venv does not carry it
needs_requests_mock = pytest.mark.skipif(
    importlib.util.find_spec("requests_mock") is None,
    reason="requests_mock (dev dependency) not installed — e.g. a deployed venv",
)


class TestXTTSProvider:
    """XTTS voice-cloning TTS over RunPod serverless."""

    def test_init_requires_api_key(self):
        from localization.tts.xtts import XTTSProvider

        with pytest.raises(ValueError, match="API key"):
            XTTSProvider(api_key="", endpoint_id="ep")

    def test_init_requires_endpoint_id(self):
        from localization.tts.xtts import XTTSProvider

        with pytest.raises(ValueError, match="endpoint ID"):
            XTTSProvider(api_key="key", endpoint_id="")

    def test_name_property(self):
        from localization.tts.xtts import XTTSProvider

        assert XTTSProvider("key", "ep").name == "xtts"

    def test_requires_gpu_true(self):
        from localization.tts.xtts import XTTSProvider

        assert XTTSProvider("key", "ep").requires_gpu() is True

    def test_available_voices_returns_clone_voice(self):
        from localization.tts.xtts import XTTSProvider

        voices = XTTSProvider("key", "ep").available_voices("zh")
        assert len(voices) == 1
        assert voices[0].id == "clone"
        assert voices[0].language == "zh"

    @needs_requests_mock
    def test_synthesize_happy_path(self, tmp_path, requests_mock):
        import base64

        from localization.tts.xtts import XTTSProvider

        audio_b64 = base64.b64encode(b"fake-audio-bytes").decode()
        requests_mock.post("https://api.runpod.ai/v2/ep/run", json={"id": "job-42"})
        requests_mock.get(
            "https://api.runpod.ai/v2/ep/status/job-42",
            json={"status": "COMPLETED", "output": {"audio_b64": audio_b64}},
        )

        out = tmp_path / "out.wav"
        provider = XTTSProvider("key", "ep")
        result = provider.synthesize("hello", "zh", "default", out)
        assert result == out
        assert out.read_bytes() == b"fake-audio-bytes"

    @needs_requests_mock
    def test_synthesize_includes_reference_audio(self, tmp_path, requests_mock):
        """When voice is a readable file path, its content is sent as reference."""
        import base64

        from localization.tts.xtts import XTTSProvider

        # Create a dummy reference file
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"ref-content")

        audio_b64 = base64.b64encode(b"out").decode()
        requests_mock.post("https://api.runpod.ai/v2/ep/run", json={"id": "job-99"})
        requests_mock.get(
            "https://api.runpod.ai/v2/ep/status/job-99",
            json={"status": "COMPLETED", "output": {"audio_b64": audio_b64}},
        )

        out = tmp_path / "o.wav"
        provider = XTTSProvider("key", "ep")
        provider.synthesize("hi", "zh", str(ref), out)
        # Verify the reference was included in the request body
        req = requests_mock.request_history[0]
        assert "speaker_wav_b64" in req.json()["input"]

    @needs_requests_mock
    def test_synthesize_raises_on_empty_response(self, tmp_path, requests_mock):
        from localization.tts.xtts import XTTSProvider

        requests_mock.post("https://api.runpod.ai/v2/ep/run", json={"id": "job-0"})
        requests_mock.get(
            "https://api.runpod.ai/v2/ep/status/job-0", json={"status": "COMPLETED", "output": {}}
        )
        with pytest.raises(RuntimeError, match="no audio data"):
            XTTSProvider("key", "ep").synthesize("hi", "zh", "default", tmp_path / "o.wav")

    @needs_requests_mock
    def test_poll_job_raises_on_failure(self, tmp_path, requests_mock, monkeypatch):
        from localization.tts.xtts import XTTSProvider

        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
        requests_mock.post("https://api.runpod.ai/v2/ep/run", json={"id": "job-bad"})
        requests_mock.get(
            "https://api.runpod.ai/v2/ep/status/job-bad",
            json={"status": "FAILED", "error": "gpu oom"},
        )
        with pytest.raises(RuntimeError, match="FAILED"):
            XTTSProvider("key", "ep").synthesize("hi", "zh", "default", tmp_path / "o.wav")

    @needs_requests_mock
    def test_poll_job_times_out(self, tmp_path, requests_mock, monkeypatch):
        import time as _time

        from localization.tts.xtts import XTTSProvider

        monkeypatch.setattr("time.sleep", lambda *a, **kw: None)
        # Simulate time passing so we exit the loop immediately
        orig = _time.monotonic
        calls = [0]

        def fast(*args, **kwargs):
            calls[0] += 1
            return orig() + (0 if calls[0] == 1 else 999)

        monkeypatch.setattr("time.monotonic", fast)

        requests_mock.post("https://api.runpod.ai/v2/ep/run", json={"id": "job-slow"})
        requests_mock.get(
            "https://api.runpod.ai/v2/ep/status/job-slow", json={"status": "IN_PROGRESS"}
        )
        with pytest.raises(TimeoutError):
            XTTSProvider("key", "ep").synthesize("hi", "zh", "default", tmp_path / "o.wav")


# ── translation provider pathways (pipeline.py) ──


class _StubProvider:
    """Minimal concrete TranslationProvider for pipeline tests."""

    def __init__(self, outputs=None, raise_strict=False, degrade=0):
        self._outputs = outputs
        self._raise_strict = raise_strict
        self._degrade = degrade
        self.degraded = False
        self.degraded_texts = 0
        self.calls: list[tuple[list[str], str, str, bool]] = []

    @property
    def name(self) -> str:
        return "stub-mt"

    def translate(self, texts, target_locale, source_lang="EN", strict=False):
        from localization.translation.base import TranslationUnavailableError

        self.calls.append((list(texts), target_locale, source_lang, strict))
        if self._degrade:
            self.degraded = True
            self.degraded_texts += self._degrade
        if self._raise_strict and strict:
            raise TranslationUnavailableError("backend down")
        if self._outputs is not None:
            return list(self._outputs)
        return list(texts)


class TestTranslationProviderPathways:
    """Cover generate_subtitles / _write_translated_chapter_vtt against the
    provider abstraction: no provider → source-only VTT; a stub provider →
    dual VTT; a strict failure → TranslationUnavailableError propagates."""

    def _stub_stt(self):
        provider = MagicMock()
        provider.name = "stub-stt"
        provider.is_local = True
        return provider

    def _transcript(self):
        transcript = MagicMock()
        transcript.sentence_texts.return_value = ["Hello world.", "Goodbye."]
        return transcript

    def test_generate_subtitles_source_only_when_no_provider(self, tmp_path, caplog):
        import logging

        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        with (
            patch.object(mod, "_transcribe_with_fallback", return_value=self._transcript()),
            patch.object(mod, "align_translations", return_value=([], [])),
            patch.object(mod, "generate_vtt", side_effect=lambda _c, p: p),
            patch.object(mod, "get_translation_provider", return_value=None),
            caplog.at_level(logging.INFO, logger="localization.pipeline"),
        ):
            source_vtt, translated_vtt = mod.generate_subtitles(
                audio, tmp_path, "zh-Hans", "en", stt_provider=self._stub_stt()
            )
        assert translated_vtt is None
        assert str(source_vtt).endswith(".en.vtt")
        assert any("no translation provider configured" in r.message for r in caplog.records)

    def test_generate_subtitles_dual_vtt_with_stub_provider(self, tmp_path):
        from localization import pipeline as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        stub = _StubProvider(outputs=["你好，世界。", "再见。"])
        with (
            patch.object(mod, "_transcribe_with_fallback", return_value=self._transcript()),
            patch.object(mod, "align_translations", return_value=([], [])),
            patch.object(mod, "generate_vtt", side_effect=lambda _c, p: p),
            patch.object(mod, "get_translation_provider", return_value=stub),
        ):
            source_vtt, translated_vtt = mod.generate_subtitles(
                audio, tmp_path, "zh-Hans", "en", stt_provider=self._stub_stt()
            )
        assert translated_vtt is not None
        assert str(translated_vtt).endswith(".zh-Hans.vtt")
        assert str(source_vtt).endswith(".en.vtt")
        # Persisting caller uses the persistence budget (strict=False at the
        # provider; refusal happens at the budget boundary) with upper-cased
        # source lang.
        assert stub.calls == [(["Hello world.", "Goodbye."], "zh-Hans", "EN", False)]

    def test_generate_subtitles_over_budget_failure_propagates(self, tmp_path):
        from localization import pipeline as mod
        from localization.translation.base import TranslationUnavailableError

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        # Degrade more texts than max(6, 5% of 2 sentences) allows.
        stub = _StubProvider(degrade=7)
        with (
            patch.object(mod, "_transcribe_with_fallback", return_value=self._transcript()),
            patch.object(mod, "align_translations", return_value=([], [])),
            patch.object(mod, "generate_vtt", side_effect=lambda _c, p: p),
            patch.object(mod, "get_translation_provider", return_value=stub),
        ):
            with pytest.raises(TranslationUnavailableError):
                mod.generate_subtitles(
                    audio, tmp_path, "zh-Hans", "en", stt_provider=self._stub_stt()
                )

    def test_write_translated_chapter_vtt_none_without_provider(self, tmp_path):
        from localization import pipeline as mod
        from localization.chapters import Chapter

        chapter = Chapter(index=0, title="Zero", start_ms=0, end_ms=1_000)
        with patch.object(mod, "get_translation_provider", return_value=None):
            result = mod._write_translated_chapter_vtt(
                MagicMock(), ["Hello."], chapter, "ch000", tmp_path, "zh-Hans", "en"
            )
        assert result is None

    def test_write_translated_chapter_vtt_writes_with_provider(self, tmp_path):
        from localization import pipeline as mod
        from localization.chapters import Chapter

        chapter = Chapter(index=0, title="Zero", start_ms=500, end_ms=1_000)
        stub = _StubProvider(outputs=["你好。"])
        with (
            patch.object(mod, "align_translations", return_value=([], [])),
            patch.object(mod, "generate_vtt", side_effect=lambda _c, p: p),
            patch.object(mod, "get_translation_provider", return_value=stub),
        ):
            result = mod._write_translated_chapter_vtt(
                MagicMock(), ["Hello."], chapter, "ch000", tmp_path, "zh-Hans", "en"
            )
        assert result == tmp_path / "ch000.zh-Hans.vtt"
        assert stub.calls[0][3] is False  # budget boundary owns refusal now


class TestChapterClampToAudio:
    """A chapter cannot extend past the end of its own audio file.

    Audiobook-Manager-536 fleet run: a 266-hour Dostoyevsky omnibus carried a
    final chapter claiming 10,000 minutes (166 hours). Splitting it produced a
    ~7 GB temp file that the transcription service refused with HTTP 413,
    failing the whole book.
    """

    def test_overrunning_chapter_is_clamped(self):
        from localization import chapters as ch_mod

        DURATION_MS = 3_600_000  # 60 min
        chs = [
            ch_mod.Chapter(index=0, title="One", start_ms=0, end_ms=1_800_000),
            ch_mod.Chapter(index=1, title="Runaway", start_ms=1_800_000, end_ms=600_024_000),
        ]
        out = ch_mod._clamp_to_audio(chs, DURATION_MS)
        assert len(out) == 2
        assert out[1].end_ms == 3_600_000  # clamped to the real duration
        assert out[0].end_ms == 1_800_000  # untouched

    def test_chapter_starting_past_the_end_is_dropped(self):
        from localization import chapters as ch_mod

        DURATION_MS = 3_600_000
        chs = [
            ch_mod.Chapter(index=0, title="One", start_ms=0, end_ms=3_600_000),
            ch_mod.Chapter(index=1, title="Phantom", start_ms=7_200_000, end_ms=9_000_000),
        ]
        out = ch_mod._clamp_to_audio(chs, DURATION_MS)
        assert [c.title for c in out] == ["One"]

    def test_sane_chapters_pass_through_untouched(self):
        from localization import chapters as ch_mod

        DURATION_MS = 3_600_000
        chs = [
            ch_mod.Chapter(index=0, title="One", start_ms=0, end_ms=1_800_000),
            ch_mod.Chapter(index=1, title="Two", start_ms=1_800_000, end_ms=3_600_000),
        ]
        assert ch_mod._clamp_to_audio(chs, DURATION_MS) == chs

    def test_unprobeable_audio_leaves_chapters_alone(self):
        """No duration means no invariant to enforce — never drop data on a
        failed probe."""
        from localization import chapters as ch_mod

        DURATION_MS = None
        chs = [ch_mod.Chapter(index=0, title="One", start_ms=0, end_ms=600_024_000)]
        assert ch_mod._clamp_to_audio(chs, DURATION_MS) == chs
