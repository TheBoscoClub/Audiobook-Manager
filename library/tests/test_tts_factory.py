"""Verify the TTS provider factory selects the right backend by config."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from library.localization.tts.factory import get_tts_provider
from library.localization.tts.edge_tts_provider import EdgeTTSProvider
from library.localization.tts.vastai_xtts import VastaiXTTSProvider


def test_default_returns_edge_tts():
    with patch("library.localization.config.TTS_PROVIDER", "edge-tts"):
        provider = get_tts_provider()
    assert isinstance(provider, EdgeTTSProvider)
    assert provider.name == "edge-tts"


def test_empty_provider_falls_back_to_edge_tts():
    with patch("library.localization.config.TTS_PROVIDER", ""):
        provider = get_tts_provider()
    assert isinstance(provider, EdgeTTSProvider)


def test_explicit_edge_alias_returns_edge_tts():
    provider = get_tts_provider("edge")
    assert isinstance(provider, EdgeTTSProvider)


def test_vastai_requires_host():
    with patch("library.localization.config.VASTAI_XTTS_HOST", ""):
        with pytest.raises(ValueError, match="VASTAI_XTTS_HOST"):
            get_tts_provider("xtts-vastai")


def test_vastai_factory_builds_provider_with_configured_host():
    with (
        patch("library.localization.config.VASTAI_XTTS_HOST", "10.0.0.1"),
        patch("library.localization.config.VASTAI_XTTS_PORT", 9000),
    ):
        provider = get_tts_provider("vastai")
    assert isinstance(provider, VastaiXTTSProvider)
    assert provider.name == "xtts-vastai"
    assert provider.requires_gpu() is True
    assert provider._base_url == "http://10.0.0.1:9000"


def test_runpod_requires_api_key():
    with (
        patch("library.localization.config.RUNPOD_API_KEY", ""),
        patch("library.localization.config.RUNPOD_XTTS_ENDPOINT", "ep_123"),
    ):
        with pytest.raises(ValueError, match="RUNPOD_API_KEY"):
            get_tts_provider("xtts-runpod")


def test_runpod_requires_endpoint():
    with (
        patch("library.localization.config.RUNPOD_API_KEY", "rpa_key"),
        patch("library.localization.config.RUNPOD_XTTS_ENDPOINT", ""),
    ):
        with pytest.raises(ValueError, match="RUNPOD_XTTS_ENDPOINT"):
            get_tts_provider("xtts-runpod")


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown TTS provider"):
        get_tts_provider("not-a-real-provider")


def test_vastai_xtts_synthesize_writes_response_body(tmp_path: Path):
    provider = VastaiXTTSProvider(host="10.0.0.1", port=8020)
    fake_resp = MagicMock()
    fake_resp.content = b"RIFF\x00\x00\x00\x00WAVEfake-audio-bytes"
    fake_resp.raise_for_status.return_value = None

    out = tmp_path / "out" / "clip.wav"
    with patch(
        "library.localization.tts.vastai_xtts.requests.post", return_value=fake_resp
    ) as mocked:
        result = provider.synthesize("你好世界", language="zh-Hans", voice="clone", output_path=out)

    assert result == out
    assert out.exists()
    assert out.read_bytes() == fake_resp.content
    # Confirm we sent the right payload to the right path with the language stripped
    args, kwargs = mocked.call_args
    assert args[0] == "http://10.0.0.1:8020/synthesize"
    assert kwargs["json"] == {"text": "你好世界", "language": "zh", "voice": "clone"}
    assert kwargs["timeout"] == 600


def test_vastai_xtts_strips_region_suffix_from_language(tmp_path: Path):
    """Vast.ai XTTS expects bare 'zh', not 'zh-Hans' or 'zh-CN'."""
    provider = VastaiXTTSProvider(host="10.0.0.1")
    fake_resp = MagicMock(content=b"x", raise_for_status=lambda: None)
    with patch(
        "library.localization.tts.vastai_xtts.requests.post", return_value=fake_resp
    ) as mocked:
        provider.synthesize("hi", language="zh-Hans", voice="", output_path=tmp_path / "a.wav")
    assert mocked.call_args.kwargs["json"]["language"] == "zh"
    assert mocked.call_args.kwargs["json"]["voice"] == "clone"  # blank → default


def test_vastai_xtts_rejects_empty_host():
    with pytest.raises(ValueError, match="host is required"):
        VastaiXTTSProvider(host="")


def test_vastai_xtts_voices_reflect_requested_language():
    provider = VastaiXTTSProvider(host="10.0.0.1")
    voices = provider.available_voices("zh-Hans")
    assert len(voices) == 1
    assert voices[0].id == "clone"
    assert voices[0].language == "zh-Hans"


# ── EdgeTTSProvider synthesize flow ──────────────────────────────────


class TestEdgeTTSProviderSynthesize:
    """Exercise the CLI-subprocess synthesis path. The provider writes
    text to a temp file, invokes ``python -m edge_tts``, and validates
    the output. All three require branches are covered here."""

    def test_available_voices_returns_language_family(self):
        provider = EdgeTTSProvider()
        voices = provider.available_voices("zh-Hans")
        # zh prefix matches the EDGE_VOICES key 'zh'.
        assert len(voices) >= 1
        assert all(v.language.startswith("zh") for v in voices)

    def test_available_voices_returns_empty_for_unsupported(self):
        provider = EdgeTTSProvider()
        assert provider.available_voices("ja") == []

    def test_requires_gpu_is_false(self):
        assert EdgeTTSProvider().requires_gpu() is False

    def test_name_is_edge_tts(self):
        assert EdgeTTSProvider().name == "edge-tts"

    def test_synthesize_invokes_cli_with_voice_and_writes_output(self, tmp_path, monkeypatch):
        """Happy path: subprocess.run succeeds, output file exists and
        has non-zero bytes — the provider returns the output path."""
        from library.localization.tts import edge_tts_provider as mod

        calls: dict = {}

        def _fake_run(cmd, capture_output, text, timeout):
            calls["cmd"] = cmd
            calls["timeout"] = timeout
            out_path = Path(cmd[cmd.index("--write-media") + 1])
            out_path.write_bytes(b"FAKEMP3DATA")
            return MagicMock(returncode=0, stderr="")

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        provider = EdgeTTSProvider()
        output = tmp_path / "out" / "speech.mp3"
        result = provider.synthesize("Hello world", "en", "en-US-JennyNeural", output)
        assert result == output
        assert output.exists()
        assert "--voice" in calls["cmd"]
        assert calls["cmd"][calls["cmd"].index("--voice") + 1] == "en-US-JennyNeural"
        assert "--file" in calls["cmd"]
        assert "--write-media" in calls["cmd"]

    def test_synthesize_raises_when_subprocess_fails(self, tmp_path, monkeypatch):
        """Non-zero exit from edge-tts must surface as RuntimeError with
        a truncated stderr preview."""
        from library.localization.tts import edge_tts_provider as mod

        def _fake_run(cmd, capture_output, text, timeout):
            return MagicMock(returncode=1, stderr="auth failed")

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        provider = EdgeTTSProvider()
        with pytest.raises(RuntimeError, match="edge-tts failed"):
            provider.synthesize("Hi", "en", "en-US-JennyNeural", tmp_path / "out.mp3")

    def test_synthesize_raises_on_empty_output(self, tmp_path, monkeypatch):
        """Even with exit 0, a zero-byte output must be flagged."""
        from library.localization.tts import edge_tts_provider as mod

        def _fake_run(cmd, capture_output, text, timeout):
            # DO NOT write to output — simulate edge-tts quietly producing nothing.
            return MagicMock(returncode=0, stderr="")

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        provider = EdgeTTSProvider()
        with pytest.raises(RuntimeError, match="empty output"):
            provider.synthesize("Hi", "en", "en-US-JennyNeural", tmp_path / "out.mp3")

    def test_synthesize_cleans_up_temp_file(self, tmp_path, monkeypatch):
        """The text temp file must be removed even if the subprocess
        succeeds (the finally block must always run)."""
        from library.localization.tts import edge_tts_provider as mod

        seen_temp: dict = {}

        def _fake_run(cmd, capture_output, text, timeout):
            # Capture the temp text file path so we can verify cleanup.
            file_idx = cmd.index("--file") + 1
            seen_temp["path"] = Path(cmd[file_idx])
            assert seen_temp["path"].exists()  # exists during the call
            out_path = Path(cmd[cmd.index("--write-media") + 1])
            out_path.write_bytes(b"ok")
            return MagicMock(returncode=0, stderr="")

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        provider = EdgeTTSProvider()
        provider.synthesize("Hello", "en", "en-US-JennyNeural", tmp_path / "out.mp3")
        # Temp file removed after synthesis.
        assert not seen_temp["path"].exists()
