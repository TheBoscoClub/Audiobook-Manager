"""End-to-end test for the subtitle generation pipeline.

Exercises :func:`library.localization.pipeline.generate_subtitles` across the
full STT → sentence segmentation → alignment → VTT chain. STT and translation
are stubbed so the test runs in milliseconds without loading Whisper or hitting
DeepL — this is the "test on dev VM" replacement: the pipeline shape is what
we care about, the model weights are unit-tested elsewhere.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from library.localization.pipeline import generate_subtitles
from library.localization.stt.base import STTProvider, Transcript, WordTimestamp


class StubSTT(STTProvider):
    """Returns a canned transcript regardless of the audio file."""

    is_local = True

    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript

    def transcribe(self, audio_path: Path, language: str = "en") -> Transcript:
        return self._transcript

    def supports_language(self, language: str) -> bool:
        return True

    def usage_remaining(self) -> int | None:
        return None

    @property
    def name(self) -> str:
        return "stub-stt"


def _canned_transcript() -> Transcript:
    words = [
        WordTimestamp("Hello", 0, 500),
        WordTimestamp("world.", 600, 1200),
        WordTimestamp("The", 2100, 2400),
        WordTimestamp("library", 2500, 3200),
        WordTimestamp("awaits.", 3300, 4100),
    ]
    return Transcript(words=words, language="en", provider="stub", duration_ms=4100)


def _read_cues(vtt_path: Path) -> list[str]:
    """Return only the text lines from a VTT file, skipping headers/timestamps."""
    lines = []
    for block in vtt_path.read_text(encoding="utf-8").split("\n\n"):
        for line in block.strip().splitlines():
            if line and line != "WEBVTT" and "-->" not in line and not line.isdigit():
                lines.append(line)
    return lines


def test_pipeline_source_only_without_deepl_key(tmp_path: Path):
    """When no DeepL key is configured, only the source-language VTT is written."""
    stt = StubSTT(_canned_transcript())

    with patch("library.localization.pipeline.DEEPL_API_KEY", ""):
        source_vtt, translated_vtt = generate_subtitles(
            audio_path=tmp_path / "ch01.opus",
            output_dir=tmp_path,
            target_locale="zh-Hans",
            source_lang="en",
            chapter_name="ch01",
            stt_provider=stt,
        )

    assert translated_vtt is None
    assert source_vtt == tmp_path / "ch01.en.vtt"
    assert source_vtt.exists()

    cues = _read_cues(source_vtt)
    assert cues == ["Hello world.", "The library awaits."]


def test_pipeline_dual_language_with_deepl_stubbed(tmp_path: Path):
    """With DeepL available, both source and translated VTTs are written
    and cue count matches sentence count."""
    stt = StubSTT(_canned_transcript())

    class StubTranslator:
        def __init__(self, *_a, **_kw):
            pass

        def translate(self, sentences, target_locale, source_lang):
            assert target_locale == "zh-Hans"
            assert source_lang == "EN"
            return ["你好，世界。", "图书馆在等你。"]

    with (
        patch("library.localization.pipeline.DEEPL_API_KEY", "fake-key"),
        patch("library.localization.translation.deepl_translate.DeepLTranslator", StubTranslator),
    ):
        source_vtt, translated_vtt = generate_subtitles(
            audio_path=tmp_path / "ch01.opus",
            output_dir=tmp_path,
            target_locale="zh-Hans",
            source_lang="en",
            chapter_name="ch01",
            stt_provider=stt,
        )

    assert source_vtt.exists()
    assert translated_vtt is not None and translated_vtt.exists()
    assert translated_vtt == tmp_path / "ch01.zh-Hans.vtt"

    src_cues = _read_cues(source_vtt)
    tr_cues = _read_cues(translated_vtt)
    assert src_cues == ["Hello world.", "The library awaits."]
    assert tr_cues == ["你好，世界。", "图书馆在等你。"]


def test_pipeline_skips_translation_when_target_equals_source(tmp_path: Path):
    """target_locale == source_lang → only source VTT, no DeepL round-trip."""
    stt = StubSTT(_canned_transcript())

    with patch("library.localization.pipeline.DEEPL_API_KEY", "fake-key") as _:
        source_vtt, translated_vtt = generate_subtitles(
            audio_path=tmp_path / "ch01.opus",
            output_dir=tmp_path,
            target_locale="en",
            source_lang="en",
            chapter_name="ch01",
            stt_provider=stt,
        )

    assert translated_vtt is None
    assert source_vtt.exists()


def test_pipeline_raises_on_silent_audio(tmp_path: Path):
    """An empty transcript is a hard error, not a silent empty-VTT."""
    empty = Transcript(words=[], language="en", provider="stub", duration_ms=0)
    stt = StubSTT(empty)

    with patch("library.localization.pipeline.DEEPL_API_KEY", ""):
        with pytest.raises(ValueError, match="No speech detected"):
            generate_subtitles(
                audio_path=tmp_path / "silent.opus",
                output_dir=tmp_path,
                target_locale="zh-Hans",
                source_lang="en",
                chapter_name="silent",
                stt_provider=stt,
            )


def test_pipeline_vtt_format_is_parseable(tmp_path: Path):
    """The VTT must start with the WEBVTT header and contain timestamp arrows."""
    stt = StubSTT(_canned_transcript())
    with patch("library.localization.pipeline.DEEPL_API_KEY", ""):
        source_vtt, _ = generate_subtitles(
            audio_path=tmp_path / "ch01.opus",
            output_dir=tmp_path,
            target_locale="en",
            source_lang="en",
            chapter_name="ch01",
            stt_provider=stt,
        )

    content = source_vtt.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT\n")
    assert "-->" in content
    assert "00:00:00.000" in content  # first cue starts at t=0
