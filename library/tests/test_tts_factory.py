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
        result = provider.synthesize(
            "你好世界", language="zh-Hans", voice="clone", output_path=out
        )

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
        provider.synthesize(
            "hi", language="zh-Hans", voice="", output_path=tmp_path / "a.wav"
        )
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
