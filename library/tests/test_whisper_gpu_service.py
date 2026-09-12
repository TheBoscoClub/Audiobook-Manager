"""Tests for the standalone Whisper GPU Flask service.

Covers ``localization/stt/whisper_gpu_service.py``. The module imports
``torch`` and ``faster_whisper`` lazily inside its helpers, so every
test stubs those modules in ``sys.modules`` before calling in. GPU
hardware is never touched.
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


class _StubWord:
    """faster-whisper word: attributes, not dict keys."""

    def __init__(self, word: str, start: float, end: float) -> None:
        self.word = word
        self.start = start
        self.end = end


class _StubSegment:
    def __init__(self, words: list[_StubWord] | None) -> None:
        self.words = words


class _StubInfo:
    def __init__(self, language: str | None = "en", duration: float = 1.0) -> None:
        self.language = language
        self.duration = duration


class _StubPipeline:
    """Stand-in BatchedInferencePipeline; records the last request."""

    def __init__(
        self,
        segments: list[_StubSegment] | None = None,
        info: _StubInfo | None = None,
    ) -> None:
        self.segments = (
            segments
            if segments is not None
            else [
                _StubSegment(
                    [
                        _StubWord("hello", 0.0, 0.5),
                        _StubWord("world", 0.6, 1.0),
                    ]
                )
            ]
        )
        self.info = info or _StubInfo(language="en", duration=1.0)
        self.last_path: str | None = None
        self.last_kwargs: dict | None = None

    def transcribe(self, audio_path: str, **kwargs):
        self.last_path = audio_path
        self.last_kwargs = kwargs
        # faster-whisper returns a lazy generator plus an info object.
        return iter(self.segments), self.info


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cuda_available: bool = True,
    pipeline: _StubPipeline | None = None,
    device_name: str = "NVIDIA L40S",
    vram_bytes: int = 48 * 1024**3,
) -> _StubPipeline:
    """Install ``torch`` + ``faster_whisper`` stubs and reset module globals."""
    stub_pipeline = pipeline or _StubPipeline()

    torch_mod = types.ModuleType("torch")
    cuda_mod = types.ModuleType("torch.cuda")
    cuda_mod.is_available = lambda: cuda_available  # type: ignore[attr-defined]
    cuda_mod.get_device_name = lambda _idx: device_name  # type: ignore[attr-defined]

    class _Props:
        total_memory = vram_bytes

    cuda_mod.get_device_properties = lambda _idx: _Props()  # type: ignore[attr-defined]
    torch_mod.cuda = cuda_mod  # type: ignore[attr-defined]

    fw_mod = types.ModuleType("faster_whisper")

    class _StubWhisperModel:
        def __init__(self, model_name: str, device: str, compute_type: str) -> None:
            stub_pipeline.loaded_name = model_name  # type: ignore[attr-defined]
            stub_pipeline.loaded_device = device  # type: ignore[attr-defined]
            stub_pipeline.loaded_compute_type = compute_type  # type: ignore[attr-defined]

    def _batched_pipeline(model: _StubWhisperModel) -> _StubPipeline:
        stub_pipeline.wrapped_model = model  # type: ignore[attr-defined]
        return stub_pipeline

    fw_mod.WhisperModel = _StubWhisperModel  # type: ignore[attr-defined]
    fw_mod.BatchedInferencePipeline = _batched_pipeline  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "torch.cuda", cuda_mod)
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_mod)
    # Reset service-level singletons between tests.
    monkeypatch.setattr(svc, "_model", None)
    monkeypatch.setattr(svc, "_model_name", "large-v3")
    return stub_pipeline


# ---------------------------------------------------------------------------
# _load_model
# ---------------------------------------------------------------------------


class TestLoadModel:
    def test_caches_after_first_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_stubs(monkeypatch)
        first = svc._load_model()
        assert first is stub
        assert getattr(stub, "loaded_device", "") == "cuda"
        assert getattr(stub, "loaded_compute_type", "") == "float16"
        assert getattr(stub, "loaded_name", "") == "large-v3"
        # A second call returns the cached instance without reloading.
        stub2 = _StubPipeline()
        # If _load_model rebuilt the pipeline it would return stub2; we
        # want the original.
        monkeypatch.setattr(
            sys.modules["faster_whisper"],
            "BatchedInferencePipeline",
            lambda model: stub2,
        )
        second = svc._load_model()
        assert second is stub

    def test_falls_back_to_cpu_without_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_stubs(monkeypatch, cuda_available=False)
        svc._load_model()
        assert getattr(stub, "loaded_device", "") == "cpu"
        # float16 is CUDA-only — CPU fallback uses int8.
        assert getattr(stub, "loaded_compute_type", "") == "int8"

    def test_pipeline_wraps_whisper_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _install_stubs(monkeypatch)
        svc._load_model()
        fw_mod = sys.modules["faster_whisper"]
        assert isinstance(getattr(stub, "wrapped_model", None), fw_mod.WhisperModel)  # type: ignore[attr-defined]


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
        # Model receives the filesystem path, the word_timestamps flag,
        # and the default batch size.
        assert stub.last_path == str(audio)
        assert stub.last_kwargs is not None
        assert stub.last_kwargs["word_timestamps"] is True
        assert stub.last_kwargs["language"] == "en"
        assert stub.last_kwargs["batch_size"] == 16

    def test_response_shape_matches_http_contract(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The exact JSON shape LocalGPUWhisperSTT consumes — do not change."""
        _install_stubs(monkeypatch)
        audio = tmp_path / "sample.opus"
        audio.write_bytes(b"\x00" * 16)
        out = svc.transcribe_file(audio, language="en")
        assert set(out) == {"words", "language", "duration", "model", "elapsed_seconds"}
        for w in out["words"]:
            assert set(w) == {"word", "start", "end"}

    def test_batch_size_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        stub = _install_stubs(monkeypatch)
        monkeypatch.setenv("WHISPER_BATCH_SIZE", "32")
        audio = tmp_path / "sample.opus"
        audio.write_bytes(b"\x00" * 16)
        svc.transcribe_file(audio, language="en")
        assert stub.last_kwargs is not None
        assert stub.last_kwargs["batch_size"] == 32

    def test_word_text_is_stripped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _install_stubs(
            monkeypatch,
            pipeline=_StubPipeline(
                segments=[_StubSegment([_StubWord(" padded ", 0.0, 0.4)])],
                info=_StubInfo(language="en", duration=0.4),
            ),
        )
        audio = tmp_path / "x.opus"
        audio.write_bytes(b"\x00")
        out = svc.transcribe_file(audio, language="en")
        assert out["words"][0]["word"] == "padded"

    def test_empty_segments_returns_zero_duration(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_stubs(
            monkeypatch,
            pipeline=_StubPipeline(segments=[], info=_StubInfo(language="en", duration=0)),
        )
        out = svc.transcribe_file(tmp_path / "x.opus", language="en")
        assert out["duration"] == 0
        assert out["words"] == []

    def test_segment_with_no_words_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # word_timestamps can still yield segments whose .words is None.
        _install_stubs(
            monkeypatch,
            pipeline=_StubPipeline(
                segments=[_StubSegment(None)], info=_StubInfo(language="en", duration=2.0)
            ),
        )
        out = svc.transcribe_file(tmp_path / "x.opus", language="en")
        assert out["words"] == []
        assert out["duration"] == 2.0

    def test_language_fallback_to_request_param(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _install_stubs(
            monkeypatch,
            pipeline=_StubPipeline(segments=[], info=_StubInfo(language=None, duration=0)),
        )
        out = svc.transcribe_file(tmp_path / "x.opus", language="zh")
        # When the model omits the language the response carries the requested one.
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
