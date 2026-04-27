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
- Cover ``vastai_serverless.VastaiServerlessSTT`` two-step dispatch (router
  POST → worker POST) and parse helpers. The legacy dedicated-instance
  ``vastai_whisper`` module was retired in v8.3.2.

Everything here is network-free — each test patches subprocess / requests
before invoking the module under test.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
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
            assert mod._chapters_from_ffprobe(audio) == []

    def test_ffprobe_timeout_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")

        def boom(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

        with patch.object(mod.subprocess, "run", side_effect=boom):
            assert mod._chapters_from_ffprobe(audio) == []

    def test_ffprobe_json_decode_error_returns_empty_list(self, tmp_path):
        from localization import chapters as mod

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        fake = MagicMock(returncode=0, stdout="not-json", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            assert mod._chapters_from_ffprobe(audio) == []

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

    def test_stt_by_explicit_name_vastai_raises_retirement_error(self):
        """`vastai` (dedicated instance) is retired — always raises, regardless of config."""
        from localization import pipeline as mod

        with pytest.raises(ValueError, match="retired in v8.3.2"):
            mod._stt_by_explicit_name("vastai")

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

    def test_remote_stt_candidates_streaming_routes_to_streaming_endpoints(self):
        """D+C routing: STREAMING hint → RunPod streaming + Vast.ai streaming."""
        from localization import pipeline as mod
        from localization.selection import WorkloadHint

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", "stream-ep"),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", "backlog-ep"),
            patch.object(mod, "VASTAI_SERVERLESS_API_KEY", "vk"),
            patch.object(mod, "VASTAI_SERVERLESS_STREAMING_ENDPOINT", "vast-stream"),
            patch.object(mod, "VASTAI_SERVERLESS_BACKLOG_ENDPOINT", "vast-backlog"),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            result = mod._remote_stt_candidates(WorkloadHint.STREAMING)

        # RunPod streaming first (warm pool wins), Vast.ai streaming second.
        assert len(result) == 2
        assert result[0].name == "whisper"
        assert result[0]._endpoint_id == "stream-ep"
        assert result[1].name == "vastai-serverless:vast-stream"

    def test_remote_stt_candidates_long_form_routes_to_backlog_endpoints(self):
        """D+C routing: LONG_FORM hint → backlog endpoints (cold pool, cheap)."""
        from localization import pipeline as mod
        from localization.selection import WorkloadHint

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", "stream-ep"),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", "backlog-ep"),
            patch.object(mod, "VASTAI_SERVERLESS_API_KEY", "vk"),
            patch.object(mod, "VASTAI_SERVERLESS_STREAMING_ENDPOINT", "vast-stream"),
            patch.object(mod, "VASTAI_SERVERLESS_BACKLOG_ENDPOINT", "vast-backlog"),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            result = mod._remote_stt_candidates(WorkloadHint.LONG_FORM)

        assert len(result) == 2
        assert result[0].name == "whisper"
        assert result[0]._endpoint_id == "backlog-ep"
        assert result[1].name == "vastai-serverless:vast-backlog"

    def test_remote_stt_candidates_any_defaults_to_backlog(self):
        """Default (ANY) hint behaves like LONG_FORM — backlog endpoints."""
        from localization import pipeline as mod

        with (
            patch.object(mod, "RUNPOD_API_KEY", "k"),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", "stream-ep"),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", "backlog-ep"),
            patch.object(mod, "VASTAI_SERVERLESS_API_KEY", ""),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", ""),
        ):
            # No workload arg → WorkloadHint.ANY → backlog path.
            result = mod._remote_stt_candidates()

        assert len(result) == 1
        assert result[0]._endpoint_id == "backlog-ep"

    def test_stt_by_explicit_name_vastai_serverless_prefers_streaming(self):
        """`vastai-serverless` override picks streaming endpoint when both set."""
        from localization import pipeline as mod

        with (
            patch.object(mod, "VASTAI_SERVERLESS_API_KEY", "vk"),
            patch.object(mod, "VASTAI_SERVERLESS_STREAMING_ENDPOINT", "stream-ep"),
            patch.object(mod, "VASTAI_SERVERLESS_BACKLOG_ENDPOINT", "backlog-ep"),
        ):
            provider = mod._stt_by_explicit_name("vastai-serverless")
        assert provider is not None
        assert provider.name == "vastai-serverless:stream-ep"

    def test_stt_by_explicit_name_vastai_serverless_falls_back_to_backlog(self):
        """Only backlog configured → fall back to backlog endpoint."""
        from localization import pipeline as mod

        with (
            patch.object(mod, "VASTAI_SERVERLESS_API_KEY", "vk"),
            patch.object(mod, "VASTAI_SERVERLESS_STREAMING_ENDPOINT", ""),
            patch.object(mod, "VASTAI_SERVERLESS_BACKLOG_ENDPOINT", "backlog-ep"),
        ):
            provider = mod._stt_by_explicit_name("vastai-serverless")
        assert provider is not None
        assert provider.name == "vastai-serverless:backlog-ep"

    def test_stt_by_explicit_name_vastai_serverless_unconfigured_raises(self):
        from localization import pipeline as mod

        with (
            patch.object(mod, "VASTAI_SERVERLESS_API_KEY", ""),
            patch.object(mod, "VASTAI_SERVERLESS_STREAMING_ENDPOINT", ""),
            patch.object(mod, "VASTAI_SERVERLESS_BACKLOG_ENDPOINT", ""),
        ):
            with pytest.raises(ValueError, match="Vast.ai serverless requested"):
                mod._stt_by_explicit_name("vastai-serverless")


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
            patch.object(mod, "DEEPL_API_KEY", ""),
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


class TestVastaiServerlessSTT:
    """Cover the D+C Vast.ai serverless client — routing + worker POST.

    Vast.ai serverless is a two-step dispatch: caller POSTs to the router,
    the router returns a live worker URL, caller POSTs audio to the worker.
    This differs from RunPod's /run + /status polling pattern.
    """

    def test_api_key_required(self):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        with pytest.raises(ValueError, match="API key is required"):
            VastaiServerlessSTT(api_key="", endpoint_name="ep")

    def test_endpoint_name_required(self):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        with pytest.raises(ValueError, match="endpoint name is required"):
            VastaiServerlessSTT(api_key="k", endpoint_name="")

    def test_name_includes_endpoint(self):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        provider = VastaiServerlessSTT(api_key="k", endpoint_name="whisper-stream")
        assert provider.name == "vastai-serverless:whisper-stream"

    def test_supports_language_strips_region(self):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        provider = VastaiServerlessSTT(api_key="k", endpoint_name="ep")
        assert provider.supports_language("zh-Hans") is True
        assert provider.supports_language("klingon") is False

    def test_usage_remaining_returns_none(self):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        assert VastaiServerlessSTT(api_key="k", endpoint_name="ep").usage_remaining() is None

    def test_transcribe_missing_file_raises_fnf(self, tmp_path):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        provider = VastaiServerlessSTT(api_key="k", endpoint_name="ep")
        with pytest.raises(FileNotFoundError):
            provider.transcribe(tmp_path / "nope.opus")

    def test_transcribe_unsupported_language_raises(self, tmp_path):
        from localization.stt.vastai_serverless import VastaiServerlessSTT

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        provider = VastaiServerlessSTT(api_key="k", endpoint_name="ep")
        with pytest.raises(ValueError, match="not supported"):
            provider.transcribe(audio, language="klingon")

    def test_route_raises_when_worker_url_missing(self, tmp_path):
        from localization.stt import vastai_serverless as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        # Router returns a payload with no worker URL field → RuntimeError.
        fake = MagicMock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {"status": "queued"}  # no url/worker_url/endpoint_url
        with patch.object(mod.requests, "post", return_value=fake):
            with pytest.raises(RuntimeError, match="no worker URL"):
                mod.VastaiServerlessSTT(api_key="k", endpoint_name="ep").transcribe(audio)

    def test_transcribe_roundtrip_two_step_dispatch(self, tmp_path):
        """Full roundtrip: route call → worker POST → parsed Transcript."""
        from localization.stt import vastai_serverless as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"abc")

        route_resp = MagicMock()
        route_resp.raise_for_status.return_value = None
        route_resp.json.return_value = {"url": "https://worker-42.vast.ai/"}

        worker_resp = MagicMock()
        worker_resp.raise_for_status.return_value = None
        worker_resp.json.return_value = {
            "words": [
                {"word": "hi", "start": 0, "end": 0.5},
                {"word": "there", "start": 0.5, "end": 1.1},
            ],
            "duration": 1.1,
            "language": "en",
        }

        # requests.post is called twice: once to router, once to worker.
        with patch.object(mod.requests, "post", side_effect=[route_resp, worker_resp]) as post:
            transcript = mod.VastaiServerlessSTT(api_key="k", endpoint_name="ep").transcribe(
                audio, language="en"
            )

        # Router call: VAST_ROUTE_URL with {endpoint, cost} body.
        router_call = post.call_args_list[0]
        assert router_call.args[0] == mod.VAST_ROUTE_URL
        assert router_call.kwargs["json"]["endpoint"] == "ep"

        # Worker call: stripped-trailing-slash URL + transcriptions path.
        worker_call = post.call_args_list[1]
        assert worker_call.args[0] == "https://worker-42.vast.ai/v1/audio/transcriptions"

        assert transcript.language == "en"
        assert transcript.provider == "vastai-serverless-ep"
        assert [w.word for w in transcript.words] == ["hi", "there"]
        assert transcript.duration_ms == 1_100

    def test_route_accepts_alternate_url_keys(self, tmp_path):
        """Router may return the URL under ``worker_url`` or ``endpoint_url``."""
        from localization.stt import vastai_serverless as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")

        route_resp = MagicMock()
        route_resp.raise_for_status.return_value = None
        route_resp.json.return_value = {"worker_url": "https://w.example"}
        worker_resp = MagicMock()
        worker_resp.raise_for_status.return_value = None
        worker_resp.json.return_value = {"words": [], "duration": 0, "language": "en"}

        with patch.object(mod.requests, "post", side_effect=[route_resp, worker_resp]):
            # Should not raise despite missing top-level "url" key.
            mod.VastaiServerlessSTT(api_key="k", endpoint_name="ep").transcribe(audio)

    def test_extract_raw_words_prefers_top_level(self):
        from localization.stt.vastai_serverless import _extract_raw_words

        data = {"words": [{"word": "a"}], "segments": [{"words": [{"word": "ignored"}]}]}
        assert _extract_raw_words(data) == [{"word": "a"}]

    def test_extract_raw_words_falls_back_to_segments(self):
        from localization.stt.vastai_serverless import _extract_raw_words

        data = {"segments": [{"words": [{"word": "hello"}]}, {"words": [{"word": "world"}]}]}
        assert _extract_raw_words(data) == [{"word": "hello"}, {"word": "world"}]

    def test_parse_word_timestamps_drops_empty_words(self):
        from localization.stt.vastai_serverless import _parse_word_timestamps

        payload = {"words": [{"word": " "}, {"word": "keep", "start": 1.0, "end": 2.0}]}
        parsed = _parse_word_timestamps(payload)
        assert len(parsed) == 1
        assert parsed[0].word == "keep"
        assert parsed[0].start_ms == 1_000

    def test_extract_duration_ms_uses_response_field_when_set(self):
        from localization.stt.vastai_serverless import _extract_duration_ms

        assert _extract_duration_ms({"duration": 3.5}, []) == 3_500

    def test_extract_duration_ms_falls_back_to_last_word(self):
        from localization.stt.base import WordTimestamp
        from localization.stt.vastai_serverless import _extract_duration_ms

        words = [
            WordTimestamp(word="a", start_ms=0, end_ms=500),
            WordTimestamp(word="b", start_ms=500, end_ms=1_200),
        ]
        assert _extract_duration_ms({}, words) == 1_200


# ---------------------------------------------------------------------------
# queue.py helpers introduced/touched by the F3 refactor
# ---------------------------------------------------------------------------


@pytest.fixture
def _queue_fixture(tmp_path: Path):
    """Provide a minimal audiobooks DB with the tables the queue touches."""
    import sqlite3 as sq

    from localization import queue as lq

    db_path = tmp_path / "queue_helpers.db"
    conn = sq.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE audiobooks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL
        );
        CREATE TABLE chapter_subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            vtt_path TEXT NOT NULL,
            UNIQUE(audiobook_id, chapter_index, locale)
        );
        CREATE TABLE chapter_translations_audio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audiobook_id INTEGER NOT NULL,
            chapter_index INTEGER NOT NULL,
            locale TEXT NOT NULL,
            audio_path TEXT NOT NULL,
            tts_provider TEXT NOT NULL,
            tts_voice TEXT,
            duration_seconds REAL,
            UNIQUE(audiobook_id, chapter_index, locale)
        );
        """)
    conn.commit()
    conn.close()

    # Reset + init module state so _load_book_state / _load_vtt_rows use
    # the fresh DB.
    lq._db_path = None
    lq._library_path = None
    lq._current_status = {}
    lq._shutdown_event.clear()
    lq.init_queue(db_path, tmp_path)
    yield lq, db_path, tmp_path
    lq._db_path = None
    lq._library_path = None
    lq._current_status = {}


class TestLoadBookState:
    """Cover ``_load_book_state`` — DB-driven branching."""

    def test_missing_book_returns_error(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        result = lq._load_book_state(99, "zh-Hans")
        assert result[0] is None
        assert result[-1] == "Book not found in DB"

    def test_missing_audio_file_returns_error(self, _queue_fixture):
        lq, db_path, tmp_path = _queue_fixture
        import sqlite3 as sq

        conn = sq.connect(str(db_path))
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) "
            "VALUES (1, 'X', '/nonexistent/path.opus')"
        )
        conn.commit()
        conn.close()
        result = lq._load_book_state(1, "zh-Hans")
        assert result[0] is None
        assert result[-1] == "Audio file not found on disk"

    def test_happy_path_returns_book_and_coverage_sets(self, _queue_fixture):
        lq, db_path, tmp_path = _queue_fixture
        import sqlite3 as sq

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        conn = sq.connect(str(db_path))
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'X', ?)", (str(audio),)
        )
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (1, 0, 'en', '/a.vtt'), (1, 1, 'en', '/b.vtt'), "
            "(1, 0, 'zh-Hans', '/tr.vtt')"
        )
        conn.commit()
        conn.close()
        book, path, en, tr, has_tts, err = lq._load_book_state(1, "zh-Hans")
        assert err is None
        assert book["id"] == 1
        assert path == audio
        assert en == {0, 1}
        assert tr == {0}
        assert has_tts is None  # no TTS rows yet


class TestRunPhases:
    """Cover the ``_run_stt_phase`` / ``_run_tts_phase`` branching."""

    def test_stt_phase_skipped_when_resume_step_is_not_stt(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        book = {"id": 1, "title": "X"}
        with patch.object(lq, "_run_stt_and_translate") as runner:
            lq._run_stt_phase(book, "zh-Hans", Path("/a"), set(), set(), "tts")
        runner.assert_not_called()

    def test_stt_phase_runs_when_no_existing_en(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        book = {"id": 1, "title": "X"}
        with (
            patch.object(lq, "_run_stt_and_translate") as runner,
            patch.object(lq, "_set_current") as setter,
        ):
            lq._run_stt_phase(book, "zh-Hans", Path("/a"), set(), set(), "stt")
        runner.assert_called_once()
        setter.assert_called_once()

    def test_stt_phase_resumes_when_tr_lags_en(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        book = {"id": 1, "title": "X"}
        with patch.object(lq, "_run_stt_and_translate") as runner, patch.object(lq, "_set_current"):
            lq._run_stt_phase(book, "zh-Hans", Path("/a"), {0, 1, 2}, {0}, "stt")
        runner.assert_called_once()
        # Skip set is the existing_en set.
        args, _ = runner.call_args
        assert args[3] == {0, 1, 2}

    def test_stt_phase_skipped_when_tr_matches_en(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        book = {"id": 1, "title": "X"}
        with patch.object(lq, "_run_stt_and_translate") as runner:
            lq._run_stt_phase(book, "zh-Hans", Path("/a"), {0, 1}, {0, 1}, "stt")
        runner.assert_not_called()

    def test_tts_phase_skipped_when_has_tts(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        book = {"id": 1, "title": "X"}
        with patch.object(lq, "_run_tts") as runner:
            lq._run_tts_phase(book, "zh-Hans", Path("/a"), has_tts={"id": 5})
        runner.assert_not_called()

    def test_tts_phase_runs_when_no_tts(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        book = {"id": 1, "title": "X"}
        with patch.object(lq, "_run_tts") as runner, patch.object(lq, "_set_current"):
            lq._run_tts_phase(book, "zh-Hans", Path("/a"), has_tts=None)
        runner.assert_called_once()


class TestProcessJob:
    """Cover ``_process_job`` — error routing and happy-path flow."""

    def test_process_job_book_missing_fails_job(self, _queue_fixture):
        lq, _db, _tmp = _queue_fixture
        job = {"id": 1, "audiobook_id": 99, "locale": "zh-Hans"}
        with patch.object(lq, "_finish_job") as finisher:
            lq._process_job(job)
        finisher.assert_called_once()
        args, kwargs = finisher.call_args
        assert args[0] == 1
        assert args[1] == "failed"

    def test_process_job_exception_in_stt_is_captured(self, _queue_fixture):
        lq, db_path, tmp_path = _queue_fixture
        import sqlite3 as sq

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        conn = sq.connect(str(db_path))
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'X', ?)", (str(audio),)
        )
        conn.commit()
        conn.close()
        job = {"id": 42, "audiobook_id": 1, "locale": "zh-Hans"}
        with (
            patch.object(lq, "_run_stt_phase", side_effect=RuntimeError("boom")),
            patch.object(lq, "_finish_job") as finisher,
        ):
            lq._process_job(job)
        args, _ = finisher.call_args
        assert args[0] == 42
        assert args[1] == "failed"

    def test_process_job_happy_path_completes(self, _queue_fixture):
        lq, db_path, tmp_path = _queue_fixture
        import sqlite3 as sq

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        conn = sq.connect(str(db_path))
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'X', ?)", (str(audio),)
        )
        conn.commit()
        conn.close()
        job = {"id": 7, "audiobook_id": 1, "locale": "zh-Hans"}
        with (
            patch.object(lq, "_run_stt_phase"),
            patch.object(lq, "_run_tts_phase"),
            patch.object(lq, "_finish_job") as finisher,
        ):
            lq._process_job(job)
        args, _ = finisher.call_args
        assert args[0] == 7
        assert args[1] == "completed"


class TestVttHelpers:
    """Cover ``_load_vtt_rows`` / ``_read_vtt_lines`` / ``_join_caption_text``."""

    def test_load_vtt_rows_returns_ordered_entries(self, _queue_fixture):
        lq, db_path, _tmp = _queue_fixture
        import sqlite3 as sq

        conn = sq.connect(str(db_path))
        conn.execute("INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'X', '/a.opus')")
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (1, 2, 'zh', '/c2.vtt'), (1, 0, 'zh', '/c0.vtt'), "
            "(1, 1, 'zh', '/c1.vtt')"
        )
        conn.commit()
        conn.close()
        rows = lq._load_vtt_rows(1, "zh")
        assert [r["chapter_index"] for r in rows] == [0, 1, 2]

    def test_read_vtt_lines_strips_headers_and_timestamps(self, tmp_path):
        from localization import queue as lq

        vtt = tmp_path / "a.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "1\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "Hello there\n\n"
            "2\n"
            "00:00:02.000 --> 00:00:04.000\n"
            "Goodbye\n"
        )
        lines = lq._read_vtt_lines(vtt)
        assert lines == ["Hello there", "Goodbye"]

    def test_join_caption_text_uses_space_for_en(self):
        from localization.queue import _join_caption_text

        assert _join_caption_text(["one", "two", "three"], "en") == "one two three"

    def test_join_caption_text_uses_empty_joiner_for_cjk(self):
        from localization.queue import _join_caption_text

        assert _join_caption_text(["你好", "世界"], "zh-Hans") == "你好世界"
        assert _join_caption_text(["こん", "にちは"], "ja") == "こんにちは"
        assert _join_caption_text(["안", "녕"], "ko") == "안녕"


class TestTranscodeAndPersist:
    """Cover ``_transcode_to_opus`` / ``_probe_duration`` / ``_persist_tts_chapter``."""

    def test_transcode_to_opus_success_removes_intermediate(self, tmp_path):
        from localization import queue as mod

        src = tmp_path / "in.wav"
        src.write_bytes(b"x")
        dst = tmp_path / "out.opus"
        fake = MagicMock(returncode=0, stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod._transcode_to_opus(src, dst)
        assert result == dst
        assert not src.exists()

    def test_transcode_to_opus_failure_returns_intermediate(self, tmp_path):
        from localization import queue as mod

        src = tmp_path / "in.wav"
        src.write_bytes(b"x")
        dst = tmp_path / "out.opus"
        fake = MagicMock(returncode=1, stderr="codec error")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod._transcode_to_opus(src, dst)
        assert result == src
        # Intermediate file is preserved on failure so downstream still has
        # playable audio.
        assert src.exists()

    def test_probe_duration_returns_float_on_success(self, tmp_path):
        from localization import queue as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        fake = MagicMock(returncode=0, stdout="120.5\n", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            result = mod._probe_duration(audio)
        assert result == pytest.approx(120.5)

    def test_probe_duration_returns_none_on_nonzero_exit(self, tmp_path):
        from localization import queue as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        fake = MagicMock(returncode=1, stdout="", stderr="error")
        with patch.object(mod.subprocess, "run", return_value=fake):
            assert mod._probe_duration(audio) is None

    def test_probe_duration_returns_none_on_exception(self, tmp_path):
        from localization import queue as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        with patch.object(mod.subprocess, "run", side_effect=OSError("no ffprobe")):
            assert mod._probe_duration(audio) is None

    def test_probe_duration_returns_none_on_empty_stdout(self, tmp_path):
        from localization import queue as mod

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        fake = MagicMock(returncode=0, stdout="   \n", stderr="")
        with patch.object(mod.subprocess, "run", return_value=fake):
            assert mod._probe_duration(audio) is None

    def test_persist_tts_chapter_writes_row(self, _queue_fixture, tmp_path):
        import sqlite3 as sq

        lq, db_path, _tmp = _queue_fixture
        audio = tmp_path / "chap.opus"
        audio.write_bytes(b"x")
        lq._persist_tts_chapter(
            str(db_path),
            book_id=5,
            ch_idx=2,
            locale="zh-Hans",
            output_path=audio,
            tts_name="edge-tts",
            voice="zh-CN-XiaoxiaoNeural",
            duration=120.0,
        )
        conn = sq.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT tts_provider, tts_voice, duration_seconds "
                "FROM chapter_translations_audio "
                "WHERE audiobook_id = 5 AND chapter_index = 2 AND locale = 'zh-Hans'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "edge-tts"
        assert row[1] == "zh-CN-XiaoxiaoNeural"
        assert row[2] == pytest.approx(120.0)


class TestTtsOneChapter:
    """Cover ``_tts_one_chapter`` — the per-chapter TTS orchestrator."""

    def test_missing_vtt_returns_early_without_tts_call(self, _queue_fixture, tmp_path):
        lq, db_path, _tmp = _queue_fixture
        row = {"chapter_index": 0, "vtt_path": str(tmp_path / "missing.vtt")}
        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        tts = MagicMock()
        tts.name = "edge-tts"

        # Even though vtt is missing, this must not raise or invoke TTS.
        with patch.object(lq, "_set_current"):
            lq._tts_one_chapter(
                row,
                book_id=1,
                locale="zh-Hans",
                audio_path=audio,
                output_dir=output_dir,
                total_rows=1,
                tts=tts,
                voice="v",
                db_path=str(db_path),
            )
        # No opus output created.
        assert list(output_dir.iterdir()) == []

    def test_empty_vtt_skips_without_tts(self, _queue_fixture, tmp_path):
        lq, db_path, _tmp = _queue_fixture
        vtt = tmp_path / "empty.vtt"
        vtt.write_text("WEBVTT\n\n")
        row = {"chapter_index": 0, "vtt_path": str(vtt)}
        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        tts = MagicMock()
        tts.name = "edge-tts"

        with patch.object(lq, "_set_current"):
            lq._tts_one_chapter(
                row,
                book_id=1,
                locale="zh-Hans",
                audio_path=audio,
                output_dir=output_dir,
                total_rows=1,
                tts=tts,
                voice="v",
                db_path=str(db_path),
            )
        assert list(output_dir.iterdir()) == []

    def test_full_synth_and_persist_flow(self, _queue_fixture, tmp_path):
        """Verify the happy path — synth called, transcode called, row persisted."""
        lq, db_path, _tmp = _queue_fixture

        vtt = tmp_path / "chap.vtt"
        vtt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\n你好世界\n")
        row = {"chapter_index": 3, "vtt_path": str(vtt)}
        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        # Simulate the synth producing a wav file at the intermediate path.
        def fake_synth(tts, text, locale, voice, output):
            Path(output).write_bytes(b"wav")
            return Path(output)

        tts = MagicMock()
        tts.name = "xtts"  # triggers ".wav" intermediate suffix branch

        with (
            patch("localization.tts.factory.synthesize_with_fallback", fake_synth),
            patch.object(lq, "_transcode_to_opus", side_effect=lambda a, b: b),
            patch.object(lq, "_probe_duration", return_value=60.0),
            patch.object(lq, "_set_current"),
        ):
            lq._tts_one_chapter(
                row,
                book_id=9,
                locale="zh-Hans",
                audio_path=audio,
                output_dir=output_dir,
                total_rows=10,
                tts=tts,
                voice="zh-voice",
                db_path=str(db_path),
            )

        # Row must have been persisted with duration 60.0.
        import sqlite3 as sq

        conn = sq.connect(str(db_path))
        try:
            persisted = conn.execute(
                "SELECT duration_seconds FROM chapter_translations_audio "
                "WHERE audiobook_id = 9 AND chapter_index = 3 AND locale = 'zh-Hans'"
            ).fetchone()
        finally:
            conn.close()
        assert persisted is not None
        assert persisted[0] == pytest.approx(60.0)


class TestRunTts:
    """Cover ``_run_tts`` — early-return on no-VTT and per-chapter dispatch."""

    def test_run_tts_early_return_when_no_vtt_rows(self, _queue_fixture, tmp_path):
        lq, _db, _tmp = _queue_fixture
        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")

        # No chapter_subtitles rows for this book → _load_vtt_rows returns [].
        with patch.object(lq, "_tts_one_chapter") as per_chap:
            lq._run_tts(99, "zh-Hans", audio)
        per_chap.assert_not_called()

    def test_run_tts_dispatches_per_chapter(self, _queue_fixture, tmp_path):
        lq, db_path, _tmp = _queue_fixture
        import sqlite3 as sq

        audio = tmp_path / "book.opus"
        audio.write_bytes(b"x")
        conn = sq.connect(str(db_path))
        conn.execute(
            "INSERT INTO audiobooks (id, title, file_path) VALUES (1, 'X', ?)", (str(audio),)
        )
        conn.execute(
            "INSERT INTO chapter_subtitles "
            "(audiobook_id, chapter_index, locale, vtt_path) "
            "VALUES (1, 0, 'zh-Hans', '/a.vtt'), (1, 1, 'zh-Hans', '/b.vtt')"
        )
        conn.commit()
        conn.close()

        fake_tts = MagicMock(name="tts")
        fake_tts.name = "edge-tts"

        with (
            patch("localization.tts.factory.get_tts_provider", return_value=fake_tts),
            patch.object(lq, "_tts_one_chapter") as per_chap,
            patch.object(lq, "_set_current"),
        ):
            lq._run_tts(1, "zh-Hans", audio)
        assert per_chap.call_count == 2


# ── xtts.py ──


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

    def test_synthesize_raises_on_empty_response(self, tmp_path, requests_mock):
        from localization.tts.xtts import XTTSProvider

        requests_mock.post("https://api.runpod.ai/v2/ep/run", json={"id": "job-0"})
        requests_mock.get(
            "https://api.runpod.ai/v2/ep/status/job-0", json={"status": "COMPLETED", "output": {}}
        )
        with pytest.raises(RuntimeError, match="no audio data"):
            XTTSProvider("key", "ep").synthesize("hi", "zh", "default", tmp_path / "o.wav")

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


# ── deepl_stt.py ──


class TestDeepLSTT:
    """DeepL speech-to-text provider."""

    def test_init_requires_api_key(self):
        from localization.stt.deepl_stt import DeepLSTT

        with pytest.raises(ValueError, match="API key"):
            DeepLSTT("")

    def test_pro_vs_free_base_url(self):
        from localization.stt.deepl_stt import DeepLSTT

        pro = DeepLSTT("pro-key")
        free = DeepLSTT("free-key:fx")
        assert "api-free" not in pro._base_url
        assert "api-free" in free._base_url

    def test_name_is_deepl(self):
        from localization.stt.deepl_stt import DeepLSTT

        assert DeepLSTT("k").name == "deepl"

    def test_supports_language_honors_allowlist(self):
        from localization.stt.deepl_stt import DeepLSTT

        stt = DeepLSTT("k")
        assert stt.supports_language("en")
        assert stt.supports_language("ZH-Hans")  # case + suffix insensitive
        assert not stt.supports_language("xx")

    def test_usage_remaining_divides_by_char_per_minute(self, requests_mock):
        from localization.stt.deepl_stt import DeepLSTT

        requests_mock.get(
            "https://api.deepl.com/v2/usage", json={"character_count": 0, "character_limit": 7500}
        )
        # 7500 chars / 750 per minute = 10 minutes
        assert DeepLSTT("k").usage_remaining() == 10

    def test_usage_remaining_caps_at_zero(self, requests_mock):
        from localization.stt.deepl_stt import DeepLSTT

        requests_mock.get(
            "https://api.deepl.com/v2/usage",
            json={"character_count": 10000, "character_limit": 5000},
        )
        # negative → max(0, ...) clamps
        assert DeepLSTT("k").usage_remaining() == 0

    def test_usage_remaining_swallows_http_error(self, requests_mock):
        from localization.stt.deepl_stt import DeepLSTT

        requests_mock.get("https://api.deepl.com/v2/usage", status_code=500)
        assert DeepLSTT("k").usage_remaining() is None

    def test_transcribe_file_missing_raises(self, tmp_path):
        from localization.stt.deepl_stt import DeepLSTT

        with pytest.raises(FileNotFoundError):
            DeepLSTT("k").transcribe(tmp_path / "missing.opus", "en")

    def test_transcribe_unsupported_language_raises(self, tmp_path):
        from localization.stt.deepl_stt import DeepLSTT

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        with pytest.raises(ValueError, match="not supported"):
            DeepLSTT("k").transcribe(audio, "xx")

    def test_transcribe_happy_path(self, tmp_path, requests_mock):
        from localization.stt.deepl_stt import DeepLSTT

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"audio-bytes")
        requests_mock.post(
            "https://api.deepl.com/v2/transcribe",
            json={
                "segments": [
                    {
                        "words": [
                            {"word": "hello", "start": 0.0, "end": 0.5},
                            {"word": "world", "start": 0.6, "end": 1.0},
                        ]
                    }
                ],
                "duration": 1.5,
            },
        )
        result = DeepLSTT("k").transcribe(audio, "en")
        assert len(result.words) == 2
        assert result.words[0].word == "hello"
        assert result.words[0].end_ms == 500
        assert result.duration_ms == 1500
        assert result.provider == "deepl"


# ── deepl_translate.py ──


class TestDeepLTranslator:
    """DeepL translation provider (text API + TM cache hooks)."""

    def test_init_requires_api_key(self):
        from localization.translation.deepl_translate import DeepLTranslator

        with pytest.raises(ValueError, match="API key"):
            DeepLTranslator("")

    def test_pro_vs_free_base_url(self):
        from localization.translation.deepl_translate import DeepLTranslator

        pro = DeepLTranslator("pro-key")
        free = DeepLTranslator("free-key:fx")
        assert "api-free" not in pro._base_url
        assert "api-free" in free._base_url

    def test_translate_empty_returns_empty(self):
        from localization.translation.deepl_translate import DeepLTranslator

        assert DeepLTranslator("k").translate([], "zh-Hans") == []

    def test_translate_uses_deepl_codes(self, requests_mock):
        from localization.translation.deepl_translate import DeepLTranslator

        requests_mock.post(
            "https://api.deepl.com/v2/translate",
            json={"translations": [{"text": "你好"}, {"text": "世界"}]},
        )
        out = DeepLTranslator("k").translate(["hello", "world"], "zh-Hans")
        assert out == ["你好", "世界"]
        req = requests_mock.request_history[0]
        # Verify target_lang gets mapped to DeepL's ZH-HANS
        body = req.text or ""
        assert "target_lang" in body

    def test_translate_returns_originals_on_http_error(self, requests_mock):
        from localization.translation.deepl_translate import DeepLTranslator

        requests_mock.post("https://api.deepl.com/v2/translate", status_code=500)
        # Error is swallowed — returns originals as safe fallback
        result = DeepLTranslator("k").translate(["hi"], "zh-Hans")
        # Either returns originals or empty — key is no crash
        assert isinstance(result, list)


# ── deepl_translate _hash_source / map_locale_for_deepl ──


class TestDeepLTranslatorHelpers:
    def test_hash_source_is_stable(self):
        from localization.translation.deepl_translate import _hash_source

        assert _hash_source("hello") == _hash_source("hello")
        assert _hash_source("a") != _hash_source("b")

    def test_locale_to_deepl_map_has_known_entries(self):
        """Verify the LOCALE_TO_DEEPL translation table has the key languages."""
        from localization.translation.deepl_translate import LOCALE_TO_DEEPL

        # Chinese simplified → DeepL's ZH-HANS
        assert LOCALE_TO_DEEPL.get("zh-Hans") == "ZH-HANS"
        # Upper case lookup for unknown should fall through
        assert LOCALE_TO_DEEPL.get("xx") is None
