"""Verify pipeline.generate_subtitles falls back to local Whisper on network errors."""

from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from library.localization.pipeline import _transcribe_with_fallback
from library.localization.stt.base import Transcript, WordTimestamp
from library.localization.stt.local_whisper import LocalWhisperSTT
from library.localization.stt.vastai_whisper import VastaiWhisperSTT


def _fake_transcript(provider_name: str = "local-whisper-base") -> Transcript:
    return Transcript(
        words=[WordTimestamp(word="hello", start_ms=0, end_ms=500)],
        language="en",
        provider=provider_name,
        duration_ms=500,
    )


def test_fallback_on_connection_error_uses_local_whisper(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(VastaiWhisperSTT, "transcribe",
                      side_effect=requests.exceptions.ConnectionError("refused")), \
         patch.object(LocalWhisperSTT, "transcribe",
                      return_value=_fake_transcript()) as local_mock:
        result = _transcribe_with_fallback(remote, audio, "en")

    assert result.provider == "local-whisper-base"
    local_mock.assert_called_once_with(audio, language="en")


def test_fallback_on_timeout_uses_local_whisper(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(VastaiWhisperSTT, "transcribe",
                      side_effect=requests.exceptions.Timeout("slow")), \
         patch.object(LocalWhisperSTT, "transcribe",
                      return_value=_fake_transcript()):
        result = _transcribe_with_fallback(remote, audio, "en")

    assert result.provider == "local-whisper-base"


def test_fallback_on_oserror_uses_local_whisper(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(VastaiWhisperSTT, "transcribe",
                      side_effect=OSError("network unreachable")), \
         patch.object(LocalWhisperSTT, "transcribe",
                      return_value=_fake_transcript()):
        result = _transcribe_with_fallback(remote, audio, "en")

    assert result.provider == "local-whisper-base"


def test_local_provider_failure_is_not_retried(tmp_path: Path):
    """If LocalWhisperSTT itself fails, the error must propagate."""
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    local = LocalWhisperSTT()
    with patch.object(LocalWhisperSTT, "transcribe",
                      side_effect=OSError("model file missing")):
        with pytest.raises(OSError, match="model file missing"):
            _transcribe_with_fallback(local, audio, "en")


def test_remote_success_does_not_invoke_local(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(VastaiWhisperSTT, "transcribe",
                      return_value=_fake_transcript("vastai-whisper")) as remote_mock, \
         patch.object(LocalWhisperSTT, "transcribe") as local_mock:
        result = _transcribe_with_fallback(remote, audio, "en")

    assert result.provider == "vastai-whisper"
    remote_mock.assert_called_once()
    local_mock.assert_not_called()


def test_non_network_error_is_not_caught(tmp_path: Path):
    """ValueError, KeyError, etc. must propagate — only network errors fall back."""
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(VastaiWhisperSTT, "transcribe",
                      side_effect=ValueError("bad audio format")), \
         patch.object(LocalWhisperSTT, "transcribe") as local_mock:
        with pytest.raises(ValueError, match="bad audio format"):
            _transcribe_with_fallback(remote, audio, "en")
    local_mock.assert_not_called()
