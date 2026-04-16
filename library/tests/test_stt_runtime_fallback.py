"""Verify STT network errors raise after retries — no local CPU fallback."""

from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from library.localization.pipeline import _transcribe_with_fallback
from library.localization.stt.base import Transcript, WordTimestamp
from library.localization.stt.vastai_whisper import VastaiWhisperSTT


def _fake_transcript(provider_name: str = "vastai-whisper") -> Transcript:
    return Transcript(
        words=[WordTimestamp(word="hello", start_ms=0, end_ms=500)],
        language="en",
        provider=provider_name,
        duration_ms=500,
    )


def test_connection_error_raises_after_retries(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(
        VastaiWhisperSTT,
        "transcribe",
        side_effect=requests.exceptions.ConnectionError("refused"),
    ):
        with pytest.raises(requests.exceptions.ConnectionError, match="refused"):
            _transcribe_with_fallback(remote, audio, "en")


def test_timeout_raises_after_retries(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(
        VastaiWhisperSTT,
        "transcribe",
        side_effect=requests.exceptions.Timeout("slow"),
    ):
        with pytest.raises(requests.exceptions.Timeout, match="slow"):
            _transcribe_with_fallback(remote, audio, "en")


def test_oserror_raises_after_retries(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(
        VastaiWhisperSTT,
        "transcribe",
        side_effect=OSError("network unreachable"),
    ):
        with pytest.raises(OSError, match="network unreachable"):
            _transcribe_with_fallback(remote, audio, "en")


def test_remote_success_returns_transcript(tmp_path: Path):
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(
        VastaiWhisperSTT,
        "transcribe",
        return_value=_fake_transcript("vastai-whisper"),
    ) as remote_mock:
        result = _transcribe_with_fallback(remote, audio, "en")

    assert result.provider == "vastai-whisper"
    remote_mock.assert_called_once()


def test_non_network_error_propagates_immediately(tmp_path: Path):
    """ValueError, KeyError, etc. propagate without retries."""
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with patch.object(
        VastaiWhisperSTT,
        "transcribe",
        side_effect=ValueError("bad audio format"),
    ):
        with pytest.raises(ValueError, match="bad audio format"):
            _transcribe_with_fallback(remote, audio, "en")


def test_retries_before_raising(tmp_path: Path):
    """Verify all 4 retry attempts are made before raising."""
    audio = tmp_path / "ch01.opus"
    audio.write_bytes(b"\x00")

    remote = VastaiWhisperSTT(host="10.0.0.1")
    with (
        patch.object(
            VastaiWhisperSTT,
            "transcribe",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ) as remote_mock,
        patch("library.localization.fallback.time.sleep"),
    ):
        with pytest.raises(requests.exceptions.ConnectionError):
            _transcribe_with_fallback(remote, audio, "en")

    assert remote_mock.call_count == 4
