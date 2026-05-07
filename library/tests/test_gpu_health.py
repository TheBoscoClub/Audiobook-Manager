"""Tests for the canonical streaming-GPU health probe (v8.3.10.5).

Validates :func:`library.localization.gpu_health.probe_all_streaming_providers`
across the same four configuration scenarios that
``test_provider_agnostic.py`` exercises against ``_probe_stt_warmth``:

  1. No provider configured  → empty providers list, any_healthy=False
  2. RunPod only, unreachable → 1 entry with ready=0, any_healthy=False
  3. Vast.ai only, unreachable → 1 entry with ready=0, any_healthy=False
  4. Both configured, mocked  → 2 entries; any_healthy mirrors the mocks

The HTTP probe is mocked at ``urllib.request.urlopen`` — no real network
traffic and no real GPU provider is contacted (per the project rule
forbidding live RunPod/Vast.ai calls during /test or in dev/QA).
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
from unittest.mock import patch

import pytest

from library.localization.gpu_health import (
    PROBE_TIMEOUT_SEC,
    _probe_one_provider,
    probe_all_streaming_providers,
)

PROVIDER_ENV_KEYS = (
    "AUDIOBOOKS_RUNPOD_API_KEY",
    "AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT",
    "AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY",
    "AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch):
    """Every test starts with all provider env vars unset, then opts in."""
    for key in PROVIDER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _fake_response(payload: dict):
    """Context-manager that mimics urlopen()'s response object."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return _Resp()


# ─── _probe_one_provider — atomic per-provider probe ──────────────────────


def test_probe_one_returns_none_when_unconfigured():
    assert _probe_one_provider("runpod", "", "endpoint", "https://x") is None
    assert _probe_one_provider("runpod", "key", "", "https://x") is None


def test_probe_one_returns_ready_count_on_success():
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"workers": {"ready": 3, "running": 1}}),
    ):
        entry = _probe_one_provider("runpod", "k", "ep", "https://api.runpod.ai")
    assert entry is not None
    assert entry["name"] == "runpod"
    assert entry["ready"] == 3
    assert entry["endpoint_id"] == "ep"


def test_probe_one_returns_zero_ready_on_network_error():
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        entry = _probe_one_provider("runpod", "k", "ep", "https://api.runpod.ai")
    assert entry is not None
    assert entry["ready"] == 0


def test_probe_one_returns_zero_ready_on_malformed_payload():
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"unexpected": "shape"}),
    ):
        entry = _probe_one_provider("runpod", "k", "ep", "https://api.runpod.ai")
    assert entry is not None
    assert entry["ready"] == 0


def test_probe_one_uses_bounded_timeout():
    captured: dict = {}

    def fake_urlopen(req, timeout):
        captured["timeout"] = timeout
        return _fake_response({"workers": {"ready": 0}})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        _probe_one_provider("runpod", "k", "ep", "https://api.runpod.ai")
    assert captured["timeout"] == PROBE_TIMEOUT_SEC


# ─── probe_all_streaming_providers — aggregate ────────────────────────────


def test_aggregate_no_provider_configured():
    result = probe_all_streaming_providers()
    assert result == {"providers": [], "any_healthy": False}


def test_aggregate_runpod_only_unreachable(monkeypatch):
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_API_KEY", "k")
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT", "ep")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("unreachable"),
    ):
        result = probe_all_streaming_providers()
    assert [p["name"] for p in result["providers"]] == ["runpod"]
    assert result["providers"][0]["ready"] == 0
    assert result["any_healthy"] is False


def test_aggregate_vastai_only_unreachable(monkeypatch):
    monkeypatch.setenv("AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY", "k")
    monkeypatch.setenv("AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT", "ep")
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("unreachable"),
    ):
        result = probe_all_streaming_providers()
    assert [p["name"] for p in result["providers"]] == ["vastai"]
    assert result["any_healthy"] is False


def test_aggregate_both_configured_one_healthy(monkeypatch):
    """RunPod returns ready=2, Vast.ai returns ready=0 → any_healthy=True."""
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_API_KEY", "k")
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT", "ep-rp")
    monkeypatch.setenv("AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY", "k")
    monkeypatch.setenv("AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT", "ep-vast")

    def fake_urlopen(req, timeout):  # noqa: ARG001
        if "api.runpod.ai" in req.full_url:
            return _fake_response({"workers": {"ready": 2}})
        return _fake_response({"workers": {"ready": 0}})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = probe_all_streaming_providers()

    names = [p["name"] for p in result["providers"]]
    assert names == ["runpod", "vastai"]  # deterministic order
    by_name = {p["name"]: p for p in result["providers"]}
    assert by_name["runpod"]["ready"] == 2
    assert by_name["vastai"]["ready"] == 0
    assert result["any_healthy"] is True


def test_aggregate_pessimistic_when_all_zero(monkeypatch):
    """Every configured provider has 0 ready → any_healthy=False (the
    bug the v8.3.10.5 stub-replacement is fixing — pre-fix the stub
    returned True even when nothing was checked)."""
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_API_KEY", "k")
    monkeypatch.setenv("AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT", "ep")
    with patch(
        "urllib.request.urlopen",
        return_value=_fake_response({"workers": {"ready": 0}}),
    ):
        result = probe_all_streaming_providers()
    assert result["any_healthy"] is False


# ─── Use of io kept for future stream-shaped responses ────────────────────
_ = io  # prevent unused-import lint if a future test needs BytesIO bodies
_ = os  # available for environ probing in future scenarios
