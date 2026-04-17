"""Coverage tests for ``library.localization.stt.local_gpu_whisper``.

The provider wraps an HTTP service that runs on the host GPU. These
tests stub ``requests.get``/``requests.post`` so no network or GPU is
touched — we verify URL construction, request payloads, response
parsing, error handling, and the language-support gate.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from localization.stt.local_gpu_whisper import LocalGPUWhisperSTT, WHISPER_LANGUAGES


# ── Construction & static helpers ────────────────────────────────────


class TestConstruction:
    def test_empty_host_raises(self):
        with pytest.raises(ValueError, match="AUDIOBOOKS_WHISPER_GPU_HOST"):
            LocalGPUWhisperSTT(host="")

    def test_default_port_is_8765(self):
        p = LocalGPUWhisperSTT(host="10.0.0.1")
        assert p._base_url == "http://10.0.0.1:8765"

    def test_custom_port_is_honored(self):
        p = LocalGPUWhisperSTT(host="10.0.0.1", port=9000)
        assert p._base_url == "http://10.0.0.1:9000"

    def test_name_is_local_gpu_whisper(self):
        assert LocalGPUWhisperSTT(host="h").name == "local-gpu-whisper"

    def test_usage_remaining_is_unlimited(self):
        # Local GPU has no billing — returns None as the "unlimited" sentinel.
        assert LocalGPUWhisperSTT(host="h").usage_remaining() is None


class TestSupportsLanguage:
    @pytest.mark.parametrize("lang", ["en", "zh", "fr", "ja", "ko", "es"])
    def test_common_langs_supported(self, lang):
        assert LocalGPUWhisperSTT(host="h").supports_language(lang) is True

    def test_regional_variant_is_normalized(self):
        # 'zh-Hans' is not a Whisper tag — provider strips the region.
        assert LocalGPUWhisperSTT(host="h").supports_language("zh-Hans") is True
        assert LocalGPUWhisperSTT(host="h").supports_language("en-US") is True

    def test_unsupported_language(self):
        # Made-up tag that isn't in the WHISPER_LANGUAGES set.
        assert LocalGPUWhisperSTT(host="h").supports_language("xx") is False

    def test_whisper_languages_is_non_empty(self):
        # Guard-rail against accidental deletion of the language allowlist.
        assert len(WHISPER_LANGUAGES) >= 40
        assert "en" in WHISPER_LANGUAGES


# ── is_available ─────────────────────────────────────────────────────


class TestIsAvailable:
    def test_healthy_service_returns_true(self):
        provider = LocalGPUWhisperSTT(host="10.0.0.1")
        fake_resp = MagicMock(ok=True)
        fake_resp.json.return_value = {"status": "ok"}
        with patch(
            "localization.stt.local_gpu_whisper.requests.get", return_value=fake_resp
        ) as mock_get:
            assert provider.is_available() is True
        mock_get.assert_called_once_with("http://10.0.0.1:8765/health", timeout=3)

    def test_unhealthy_status_returns_false(self):
        provider = LocalGPUWhisperSTT(host="10.0.0.1")
        fake_resp = MagicMock(ok=True)
        fake_resp.json.return_value = {"status": "degraded"}
        with patch("localization.stt.local_gpu_whisper.requests.get", return_value=fake_resp):
            assert provider.is_available() is False

    def test_http_error_returns_false(self):
        provider = LocalGPUWhisperSTT(host="10.0.0.1")
        fake_resp = MagicMock(ok=False)
        fake_resp.json.return_value = {"status": "ok"}
        with patch("localization.stt.local_gpu_whisper.requests.get", return_value=fake_resp):
            assert provider.is_available() is False

    def test_connection_error_returns_false(self):
        provider = LocalGPUWhisperSTT(host="10.0.0.1")
        with patch(
            "localization.stt.local_gpu_whisper.requests.get",
            side_effect=requests.ConnectionError("down"),
        ):
            assert provider.is_available() is False

    def test_timeout_returns_false(self):
        provider = LocalGPUWhisperSTT(host="10.0.0.1")
        with patch(
            "localization.stt.local_gpu_whisper.requests.get",
            side_effect=requests.Timeout("slow"),
        ):
            assert provider.is_available() is False


# ── transcribe ───────────────────────────────────────────────────────


def _make_response(words, *, language="en", duration=12.5, elapsed=2.1, error=None):
    """Helper to build a fake whisper-gpu service response."""
    payload = {
        "words": words,
        "language": language,
        "duration": duration,
        "elapsed_seconds": elapsed,
    }
    if error is not None:
        payload["error"] = error
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


class TestTranscribe:
    def test_missing_audio_raises_file_not_found(self, tmp_path):
        provider = LocalGPUWhisperSTT(host="10.0.0.1")
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            provider.transcribe(tmp_path / "missing.mp3", language="en")

    def test_sends_multipart_post_with_language(self, tmp_path):
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")
        provider = LocalGPUWhisperSTT(host="10.0.0.1", port=8765)

        resp = _make_response([{"word": "Hello", "start": 0.1, "end": 0.4}])
        with patch(
            "localization.stt.local_gpu_whisper.requests.post", return_value=resp
        ) as mock_post:
            result = provider.transcribe(audio, language="en")

        args, kwargs = mock_post.call_args
        assert args[0] == "http://10.0.0.1:8765/transcribe"
        assert "files" in kwargs
        assert kwargs["data"] == {"language": "en"}
        assert kwargs["timeout"] == 1800
        assert result.language == "en"
        assert result.provider == "local-gpu-whisper-large-v3"

    def test_word_timestamps_are_parsed_and_converted_to_ms(self, tmp_path):
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"x")
        provider = LocalGPUWhisperSTT(host="h")
        resp = _make_response(
            [
                {"word": " The ", "start": 0.5, "end": 0.75},
                {"word": " quick ", "start": 0.76, "end": 1.0},
            ],
            duration=2.0,
        )
        with patch("localization.stt.local_gpu_whisper.requests.post", return_value=resp):
            result = provider.transcribe(audio)

        assert len(result.words) == 2
        # Seconds → milliseconds + .strip() on the word text.
        assert result.words[0].word == "The"
        assert result.words[0].start_ms == 500
        assert result.words[0].end_ms == 750
        assert result.words[1].word == "quick"
        assert result.duration_ms == 2000

    def test_duration_falls_back_to_last_word_end(self, tmp_path):
        # Server omitted 'duration' — provider uses the last word's end_ms.
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"x")
        provider = LocalGPUWhisperSTT(host="h")
        resp = _make_response(
            [{"word": "Done", "start": 0.0, "end": 3.25}],
            duration=0,
        )
        with patch("localization.stt.local_gpu_whisper.requests.post", return_value=resp):
            result = provider.transcribe(audio)
        assert result.duration_ms == 3250

    def test_error_field_raises_runtime_error(self, tmp_path):
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"x")
        provider = LocalGPUWhisperSTT(host="h")
        resp = _make_response([], error="CUDA OOM")
        with patch("localization.stt.local_gpu_whisper.requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="Whisper GPU service error.*CUDA OOM"):
                provider.transcribe(audio)

    def test_http_error_propagates_via_raise_for_status(self, tmp_path):
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"x")
        provider = LocalGPUWhisperSTT(host="h")
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("500")
        with patch("localization.stt.local_gpu_whisper.requests.post", return_value=resp):
            with pytest.raises(requests.HTTPError):
                provider.transcribe(audio)

    def test_language_from_server_wins_over_requested(self, tmp_path):
        # Whisper may auto-detect a different language and override.
        audio = tmp_path / "clip.wav"
        audio.write_bytes(b"x")
        provider = LocalGPUWhisperSTT(host="h")
        resp = _make_response(
            [{"word": "Hola", "start": 0, "end": 1}],
            language="es",
        )
        with patch("localization.stt.local_gpu_whisper.requests.post", return_value=resp):
            result = provider.transcribe(audio, language="en")
        assert result.language == "es"
