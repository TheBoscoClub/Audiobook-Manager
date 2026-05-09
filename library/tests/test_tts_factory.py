"""Verify the TTS provider factory selects the right backend by config."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from library.localization.tts.edge_tts_provider import EdgeTTSProvider
from library.localization.tts.factory import get_tts_provider


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

        def _fake_run(cmd, *args, **kwargs):
            calls["cmd"] = cmd
            calls["timeout"] = kwargs.get("timeout")
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

        def _fake_run(cmd, *args, **kwargs):
            return MagicMock(returncode=1, stderr="auth failed")

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        provider = EdgeTTSProvider()
        with pytest.raises(RuntimeError, match="edge-tts failed"):
            provider.synthesize("Hi", "en", "en-US-JennyNeural", tmp_path / "out.mp3")

    def test_synthesize_raises_on_empty_output(self, tmp_path, monkeypatch):
        """Even with exit 0, a zero-byte output must be flagged."""
        from library.localization.tts import edge_tts_provider as mod

        def _fake_run(cmd, *args, **kwargs):
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

        def _fake_run(cmd, *args, **kwargs):
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
