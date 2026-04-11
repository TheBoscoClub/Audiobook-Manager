"""Verify VastaiWhisperSTT parses both faster-whisper and whisper.cpp shapes."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from library.localization.stt.vastai_whisper import VastaiWhisperSTT


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    f = tmp_path / "clip.wav"
    f.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    return f


def _mock_post(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_faster_whisper_top_level_words_shape(audio_file: Path):
    payload = {
        "language": "en",
        "duration": 1.5,
        "words": [
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.6, "end": 1.2},
        ],
    }
    with patch("library.localization.stt.vastai_whisper.requests.post",
               return_value=_mock_post(payload)):
        provider = VastaiWhisperSTT(host="127.0.0.1", port=8000)
        transcript = provider.transcribe(audio_file, language="en")

    assert [w.word for w in transcript.words] == ["hello", "world"]
    assert transcript.words[0].start_ms == 0
    assert transcript.words[0].end_ms == 500
    assert transcript.words[1].start_ms == 600
    assert transcript.duration_ms == 1500
    assert transcript.language == "en"


def test_whisper_cpp_nested_segments_words_shape(audio_file: Path):
    payload = {
        "language": "zh",
        "duration": 2.0,
        "segments": [
            {
                "id": 0,
                "text": "你好",
                "words": [
                    {"word": "你好", "start": 0.0, "end": 0.8},
                ],
            },
            {
                "id": 1,
                "text": "世界",
                "words": [
                    {"word": "世界", "start": 1.0, "end": 1.7},
                ],
            },
        ],
    }
    with patch("library.localization.stt.vastai_whisper.requests.post",
               return_value=_mock_post(payload)):
        provider = VastaiWhisperSTT(host="127.0.0.1")
        transcript = provider.transcribe(audio_file, language="zh")

    assert [w.word for w in transcript.words] == ["你好", "世界"]
    assert transcript.words[0].start_ms == 0
    assert transcript.words[0].end_ms == 800
    assert transcript.words[1].start_ms == 1000
    assert transcript.words[1].end_ms == 1700
    assert transcript.duration_ms == 2000
    assert transcript.language == "zh"


def test_string_typed_timestamps_dont_crash(audio_file: Path):
    """Some whisper.cpp builds emit timestamps as JSON strings, not numbers."""
    payload = {
        "duration": "1.0",
        "words": [
            {"word": "test", "start": "0.1", "end": "0.4"},
        ],
    }
    with patch("library.localization.stt.vastai_whisper.requests.post",
               return_value=_mock_post(payload)):
        provider = VastaiWhisperSTT(host="127.0.0.1")
        transcript = provider.transcribe(audio_file)

    assert transcript.words[0].start_ms == 100
    assert transcript.words[0].end_ms == 400
    assert transcript.duration_ms == 1000


def test_empty_words_skipped(audio_file: Path):
    """Whisper occasionally emits empty/whitespace word entries — drop them."""
    payload = {
        "duration": 0.5,
        "words": [
            {"word": "   ", "start": 0.0, "end": 0.1},
            {"word": "real", "start": 0.2, "end": 0.4},
            {"word": "", "start": 0.4, "end": 0.5},
        ],
    }
    with patch("library.localization.stt.vastai_whisper.requests.post",
               return_value=_mock_post(payload)):
        provider = VastaiWhisperSTT(host="127.0.0.1")
        transcript = provider.transcribe(audio_file)

    assert [w.word for w in transcript.words] == ["real"]


def test_text_field_alias_used_when_word_missing(audio_file: Path):
    """whisper.cpp segments[].words[] sometimes use 'text' instead of 'word'."""
    payload = {
        "duration": 0.5,
        "segments": [
            {"words": [{"text": "alt", "start": 0.0, "end": 0.4}]},
        ],
    }
    with patch("library.localization.stt.vastai_whisper.requests.post",
               return_value=_mock_post(payload)):
        provider = VastaiWhisperSTT(host="127.0.0.1")
        transcript = provider.transcribe(audio_file)

    assert [w.word for w in transcript.words] == ["alt"]


def test_duration_falls_back_to_last_word_end(audio_file: Path):
    payload = {
        "words": [
            {"word": "a", "start": 0.0, "end": 0.5},
            {"word": "b", "start": 0.6, "end": 1.4},
        ],
    }
    with patch("library.localization.stt.vastai_whisper.requests.post",
               return_value=_mock_post(payload)):
        provider = VastaiWhisperSTT(host="127.0.0.1")
        transcript = provider.transcribe(audio_file)

    assert transcript.duration_ms == 1400
