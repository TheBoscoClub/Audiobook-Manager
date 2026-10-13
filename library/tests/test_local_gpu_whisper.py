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
from localization.stt.local_gpu_whisper import WHISPER_LANGUAGES, LocalGPUWhisperSTT


@pytest.fixture(autouse=True)
def _reset_cleartext_warning_state():
    from localization.stt import local_gpu_whisper as mod

    mod._warned_cleartext.clear()
    yield
    mod._warned_cleartext.clear()


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
            "localization.stt.local_gpu_whisper.requests.get", side_effect=requests.Timeout("slow")
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
        resp = _make_response([{"word": "Done", "start": 0.0, "end": 3.25}], duration=0)
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
        resp = _make_response([{"word": "Hola", "start": 0, "end": 1}], language="es")
        with patch("localization.stt.local_gpu_whisper.requests.post", return_value=resp):
            result = provider.transcribe(audio, language="en")
        assert result.language == "es"


# ── HTTPS base URL and bearer auth (Audiobook-Manager-h2v) ────────────


class TestBaseUrlAndAuth:
    @staticmethod
    def _ok_health():
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"status": "ok"}
        return resp

    def test_default_is_http_host_port_without_auth(self, monkeypatch):
        monkeypatch.delenv("AUDIOBOOKS_WHISPER_GPU_TOKEN", raising=False)
        monkeypatch.delenv("AUDIOBOOKS_WHISPER_GPU_TOKEN_FILE", raising=False)
        p = LocalGPUWhisperSTT(host="127.0.0.1", port=8765)
        assert p._base_url == "http://127.0.0.1:8765"
        with patch("requests.get", return_value=self._ok_health()) as get:
            assert p.is_available() is True
        assert get.call_args.args[0] == "http://127.0.0.1:8765/health"
        assert "headers" not in get.call_args.kwargs

    def test_https_url_overrides_host_port(self):
        p = LocalGPUWhisperSTT(host="10.0.0.1", port=9, base_url="https://gpu.example.com/")
        with patch("requests.get", return_value=self._ok_health()) as get:
            p.is_available()
        assert get.call_args.args[0] == "https://gpu.example.com/health"

    def test_url_only_no_host_is_accepted(self):
        p = LocalGPUWhisperSTT(base_url="https://gpu.example.com")
        assert p._base_url == "https://gpu.example.com"

    def test_token_sent_as_bearer_on_health_and_transcribe(self, tmp_path):
        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        p = LocalGPUWhisperSTT(base_url="https://gpu.example.com", token="tok-123")
        with patch("requests.get", return_value=self._ok_health()) as get:
            p.is_available()
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok-123"
        post_resp = MagicMock()
        post_resp.json.return_value = {"words": [], "duration": 1.0}
        with patch("requests.post", return_value=post_resp) as post:
            p.transcribe(audio)
        assert post.call_args.args[0] == "https://gpu.example.com/transcribe"
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok-123"

    def test_token_from_file_pointer(self, tmp_path, monkeypatch):
        tf = tmp_path / "tok"
        tf.write_text("file-tok-456\n")
        monkeypatch.delenv("AUDIOBOOKS_WHISPER_GPU_TOKEN", raising=False)
        monkeypatch.setenv("AUDIOBOOKS_WHISPER_GPU_TOKEN_FILE", str(tf))
        p = LocalGPUWhisperSTT(base_url="https://gpu.example.com")
        with patch("requests.get", return_value=self._ok_health()) as get:
            p.is_available()
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer file-tok-456"

    @pytest.mark.parametrize(
        "bad", ["ftp://gpu.example.com", "javascript:alert(1)", "gpu.example.com"]
    )
    def test_bad_scheme_rejected(self, bad):
        with pytest.raises(ValueError, match="http:// or https://"):
            LocalGPUWhisperSTT(base_url=bad)

    def test_token_never_logged(self, tmp_path, caplog):
        import logging

        audio = tmp_path / "a.opus"
        audio.write_bytes(b"x")
        p = LocalGPUWhisperSTT(base_url="https://gpu.example.com", token="SECRET-xyz-789")
        post_resp = MagicMock()
        post_resp.json.return_value = {"words": [], "duration": 1.0}
        with caplog.at_level(logging.DEBUG), patch("requests.post", return_value=post_resp):
            p.transcribe(audio)
        assert "SECRET-xyz-789" not in caplog.text
        assert "SECRET-xyz-789" not in repr(p._base_url)


class TestEndpointRedaction:
    def test_userinfo_rejected_without_echo(self):
        with pytest.raises(ValueError) as ei:
            LocalGPUWhisperSTT(base_url="https://bob:hunter2@gpu.example.com")
        msg = str(ei.value)
        assert "AUDIOBOOKS_WHISPER_GPU_TOKEN" in msg
        assert "hunter2" not in msg and "bob" not in msg and "gpu.example.com" not in msg

    def test_username_only_rejected(self):
        with pytest.raises(ValueError, match="AUDIOBOOKS_WHISPER_GPU_TOKEN"):
            LocalGPUWhisperSTT(base_url="https://s3cretuser@gpu.example.com")

    def test_malformed_port_error_does_not_echo(self):
        with pytest.raises(ValueError) as ei:
            LocalGPUWhisperSTT(base_url="https://gpu.example.com:notaport/?key=s3cret")
        assert "s3cret" not in str(ei.value)

    def test_redact_endpoint_drops_everything_but_origin(self):
        from localization.stt.local_gpu_whisper import redact_endpoint

        assert (
            redact_endpoint("https://u:p@Gpu.Example.com:8443/a/b?key=s3cret#f")
            == "https://gpu.example.com:8443"
        )
        assert redact_endpoint("http://[::1]:8765/x") == "http://[::1]:8765"
        assert redact_endpoint("garbage") == "<invalid-endpoint>"

    @staticmethod
    def _gate_patches(mod, host, url):
        return (
            patch.object(mod, "RUNPOD_API_KEY", ""),
            patch.object(mod, "RUNPOD_WHISPER_ENDPOINT", ""),
            patch.object(mod, "RUNPOD_STREAMING_WHISPER_ENDPOINT", ""),
            patch.object(mod, "RUNPOD_BACKLOG_WHISPER_ENDPOINT", ""),
            patch.object(mod, "WHISPER_GPU_HOST", host),
            patch.object(mod, "WHISPER_GPU_URL", url),
        )

    def test_not_reachable_log_has_origin_only(self, caplog):
        import contextlib
        import logging

        from localization import pipeline as mod

        with contextlib.ExitStack() as st:
            for p in self._gate_patches(mod, "", "https://gpu.example.com/priv/path"):
                st.enter_context(p)
            st.enter_context(patch("requests.get", side_effect=requests.ConnectionError("down")))
            st.enter_context(caplog.at_level(logging.DEBUG))
            assert mod._remote_stt_candidates() == []
        assert "https://gpu.example.com" in caplog.text
        assert "priv/path" not in caplog.text

    def test_query_url_rejected_without_echo(self):
        with pytest.raises(ValueError) as ei:
            LocalGPUWhisperSTT(base_url="https://gpu.example.com/?key=s3cret")
        assert "s3cret" not in str(ei.value)
        with pytest.raises(ValueError):
            LocalGPUWhisperSTT(base_url="https://gpu.example.com/#frag")

    def test_empty_hostname_rejected(self):
        with pytest.raises(ValueError, match="URL with a host"):
            LocalGPUWhisperSTT(base_url="https://:443")

    def test_gate_url_only_enables_provider(self):
        import contextlib

        from localization import pipeline as mod

        with contextlib.ExitStack() as st:
            for p in self._gate_patches(mod, "", "https://gpu.example.com"):
                st.enter_context(p)
            st.enter_context(patch("requests.get", return_value=self._ok()))
            got = mod._remote_stt_candidates()
        assert [type(x).__name__ for x in got] == ["LocalGPUWhisperSTT"]

    def test_gate_neither_set_no_provider(self):
        import contextlib

        from localization import pipeline as mod

        with contextlib.ExitStack() as st:
            for p in self._gate_patches(mod, "", ""):
                st.enter_context(p)
            assert mod._remote_stt_candidates() == []

    @staticmethod
    def _ok():
        r = MagicMock()
        r.ok = True
        r.json.return_value = {"status": "ok"}
        return r

    def test_token_over_http_warns_once_without_token(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            LocalGPUWhisperSTT(host="10.0.0.1", token="TOPSECRET-1")
        recs = [r for r in caplog.records if "unencrypted" in r.getMessage()]
        assert len(recs) == 1
        assert "TOPSECRET-1" not in caplog.text

    def test_cleartext_warning_once_per_endpoint(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                LocalGPUWhisperSTT(host="10.0.0.1", token="t1")
        assert caplog.text.count("unencrypted") == 1
        with caplog.at_level(logging.WARNING):
            LocalGPUWhisperSTT(host="10.0.0.2", token="t1")
        assert caplog.text.count("unencrypted") == 2

    def test_token_over_https_no_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            LocalGPUWhisperSTT(base_url="https://gpu.example.com", token="t")
        assert "unencrypted" not in caplog.text

    def test_no_token_http_no_warning(self, caplog, monkeypatch):
        import logging

        monkeypatch.delenv("AUDIOBOOKS_WHISPER_GPU_TOKEN", raising=False)
        monkeypatch.delenv("AUDIOBOOKS_WHISPER_GPU_TOKEN_FILE", raising=False)
        with caplog.at_level(logging.WARNING):
            LocalGPUWhisperSTT(host="10.0.0.1")
        assert caplog.text == ""

    def test_uppercase_scheme_accepted(self):
        p = LocalGPUWhisperSTT(base_url="HTTPS://gpu.example.com")
        assert p.endpoint == "https://gpu.example.com"

    def test_whitespace_padded_url_is_stripped(self):
        p = LocalGPUWhisperSTT(base_url="  https://gpu.example.com/  \n")
        assert p._base_url == "https://gpu.example.com"

    def test_whitespace_only_url_falls_back_to_host(self):
        p = LocalGPUWhisperSTT(host="10.0.0.1", base_url="   ")
        assert p._base_url == "http://10.0.0.1:8765"
