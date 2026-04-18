"""Tests for the standalone Whisper GPU Flask service.

Covers ``localization/stt/whisper_gpu_service.py``. The module imports
``torch`` and ``whisper`` lazily inside its helpers, so every test stubs
those modules in ``sys.modules`` before calling in. GPU hardware is never
touched.
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path

import pytest
from localization.stt import whisper_gpu_service as svc

# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _StubModel:
    """Stand-in Whisper model; records the last transcription request."""

    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "language": "en",
            "segments": [
                {
                    "words": [
                        {"word": "hello", "start": 0.0, "end": 0.5},
                        {"word": "world", "start": 0.6, "end": 1.0},
                    ],
                    "end": 1.0,
                }
            ],
        }
        self.last_path: str | None = None
        self.last_kwargs: dict | None = None

    def transcribe(self, audio_path: str, **kwargs):
        self.last_path = audio_path
        self.last_kwargs = kwargs
        return self.result


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cuda_available: bool = True,
    model: _StubModel | None = None,
    device_name: str = "NVIDIA L40S",
    vram_bytes: int = 48 * 1024**3,
) -> _StubModel:
    """Install ``torch`` + ``whisper`` stubs and reset module globals."""
    stub_model = model or _StubModel()

    torch_mod = types.ModuleType("torch")
    cuda_mod = types.ModuleType("torch.cuda")
    cuda_mod.is_available = lambda: cuda_available  # type: ignore[attr-defined]
    cuda_mod.get_device_name = lambda _idx: device_name  # type: ignore[attr-defined]

    class _Props:
        total_memory = vram_bytes

    cuda_mod.get_device_properties = lambda _idx: _Props()  # type: ignore[attr-defined]
    torch_mod.cuda = cuda_mod  # type: ignore[attr-defined]

    whisper_mod = types.ModuleType("whisper")

    def _load_model(name: str, device: str):
        stub_model.loaded_name = name  # type: ignore[attr-defined]
        stub_model.loaded_device = device  # type: ignore[attr-defined]
        return stub_model

    whisper_mod.load_model = _load_model  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "torch.cuda", cuda_mod)
    monkeypatch.setitem(sys.modules, "whisper", whisper_mod)
    # Reset service-level singletons between tests.
    monkeypatch.setattr(svc, "_model", None)
    monkeypatch.setattr(svc, "_model_name", "large-v3")
    return stub_model


# ---------------------------------------------------------------------------
# _load_model
# ---------------------------------------------------------------------------


class TestLoadModel:
    def test_caches_after_first_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_stubs(monkeypatch)
        first = svc._load_model()
        assert first is stub
        assert getattr(stub, "loaded_device", "") == "cuda"
        # A second call returns the cached instance without reloading.
        stub2 = _StubModel()
        # If _load_model called whisper.load_model again it would return stub2;
        # we want the original.
        monkeypatch.setattr(sys.modules["whisper"], "load_model", lambda *a, **kw: stub2)
        second = svc._load_model()
        assert second is stub

    def test_falls_back_to_cpu_without_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_stubs(monkeypatch, cuda_available=False)
        svc._load_model()
        assert getattr(stub, "loaded_device", "") == "cpu"


# ---------------------------------------------------------------------------
# transcribe_file
# ---------------------------------------------------------------------------


class TestTranscribeFile:
    def test_returns_flattened_word_list(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stub = _install_stubs(monkeypatch)
        audio = tmp_path / "sample.opus"
        audio.write_bytes(b"\x00" * 16)
        out = svc.transcribe_file(audio, language="en")
        assert [w["word"] for w in out["words"]] == ["hello", "world"]
        assert out["language"] == "en"
        assert out["duration"] == 1.0
        assert out["model"] == "large-v3"
        assert out["elapsed_seconds"] >= 0
        # Model receives the filesystem path and the word_timestamps flag.
        assert stub.last_path == str(audio)
        assert stub.last_kwargs is not None
        assert stub.last_kwargs["word_timestamps"] is True
        assert stub.last_kwargs["language"] == "en"

    def test_empty_segments_returns_zero_duration(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_stubs(monkeypatch, model=_StubModel(result={"segments": [], "language": "en"}))
        out = svc.transcribe_file(tmp_path / "x.opus", language="en")
        assert out["duration"] == 0
        assert out["words"] == []

    def test_language_fallback_to_request_param(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_stubs(monkeypatch, model=_StubModel(result={"segments": []}))  # no 'language' key
        out = svc.transcribe_file(tmp_path / "x.opus", language="zh")
        # When the model omits 'language' the response carries the requested one.
        assert out["language"] == "zh"


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------


class TestFlaskRoutes:
    def test_health_reports_model_and_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch)
        app = svc.create_app()
        with app.test_client() as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["status"] == "ok"
        assert payload["model"] == "large-v3"
        assert payload["model_loaded"] is False  # not preloaded
        assert payload["gpu_available"] is True
        assert payload["gpu_name"] == "NVIDIA L40S"

    def test_health_no_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, cuda_available=False)
        app = svc.create_app()
        with app.test_client() as client:
            resp = client.get("/health")
        payload = resp.get_json()
        assert payload["gpu_available"] is False
        assert payload["gpu_name"] is None

    def test_transcribe_requires_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch)
        app = svc.create_app()
        with app.test_client() as client:
            resp = client.post("/transcribe", data={})
        assert resp.status_code == 400
        assert "No file uploaded" in resp.get_json()["error"]

    def test_transcribe_success_returns_words(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch)
        app = svc.create_app()
        with app.test_client() as client:
            resp = client.post(
                "/transcribe",
                data={"file": (io.BytesIO(b"\x00" * 32), "clip.opus"), "language": "en"},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["model"] == "large-v3"
        assert len(payload["words"]) == 2

    def test_transcribe_failure_returns_500_and_cleans_tmp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch)

        def _boom(audio_path: Path, language: str = "en") -> dict:
            raise RuntimeError("model dead")

        monkeypatch.setattr(svc, "transcribe_file", _boom)
        app = svc.create_app()
        with app.test_client() as client:
            resp = client.post(
                "/transcribe",
                data={"file": (io.BytesIO(b"\x00" * 32), "clip.opus")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 500
        assert "Transcription failed" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_without_preload_does_not_load_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_stubs(monkeypatch)
        load_calls = {"n": 0}

        def _stub_load_model():
            load_calls["n"] += 1
            return object()

        monkeypatch.setattr(svc, "_load_model", _stub_load_model)

        captured: dict[str, object] = {}

        class _FakeApp:
            def run(self, host: str, port: int, threaded: bool) -> None:
                captured["host"] = host
                captured["port"] = port
                captured["threaded"] = threaded

        monkeypatch.setattr(svc, "create_app", lambda: _FakeApp())
        monkeypatch.setattr(
            sys, "argv", ["whisper_gpu_service.py", "--host", "127.0.0.1", "--port", "9999"]
        )
        svc.main()
        assert captured == {"host": "127.0.0.1", "port": 9999, "threaded": True}
        assert load_calls["n"] == 0  # --preload not passed
        assert svc._model_name == "large-v3"

    def test_main_with_preload_loads_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch)
        load_calls = {"n": 0}

        def _stub_load_model():
            load_calls["n"] += 1
            return object()

        monkeypatch.setattr(svc, "_load_model", _stub_load_model)

        class _FakeApp:
            def run(self, *a, **kw) -> None:
                return None

        monkeypatch.setattr(svc, "create_app", lambda: _FakeApp())
        monkeypatch.setattr(
            sys, "argv", ["whisper_gpu_service.py", "--preload", "--model", "small"]
        )
        svc.main()
        assert load_calls["n"] == 1
        assert svc._model_name == "small"
