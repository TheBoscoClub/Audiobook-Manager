#!/usr/bin/env python3
"""Standalone Whisper GPU transcription service.

Runs as a system service on the host where the GPU lives. Accepts
audio uploads via HTTP and returns word-level timestamps. The audiobook
API (on VMs or the host) calls this instead of loading Whisper in-process.

Backed by faster-whisper (CTranslate2) with BatchedInferencePipeline for
batched decoding — same HTTP contract as the original openai-whisper
implementation.

Usage:
    python3 whisper_gpu_service.py [--host 0.0.0.0] [--port 8765] [--model large-v3]

Environment:
    WHISPER_MODEL       default model name (CLI --model overrides; default large-v3)
    WHISPER_BATCH_SIZE  batch size for BatchedInferencePipeline (default 16)

Requires: python-pytorch-opt-rocm (GPU detection), faster-whisper (pip)
"""

import argparse
import logging
import os
import tempfile
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("whisper-gpu")

_model = None
_model_name = os.environ.get("WHISPER_MODEL", "large-v3")


def _load_model():
    global _model
    if _model is not None:
        return _model

    import torch  # type: ignore[import-not-found]
    from faster_whisper import (  # type: ignore[import-not-found]
        BatchedInferencePipeline,
        WhisperModel,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        logger.info("GPU detected: %s (%.1f GB VRAM)", gpu_name, vram_gb)
    else:
        logger.warning("No GPU detected — running on CPU (will be slow)")

    compute_type = "float16" if device == "cuda" else "int8"
    logger.info("Loading Whisper model '%s' on %s…", _model_name, device)
    start = time.monotonic()
    base_model = WhisperModel(_model_name, device=device, compute_type=compute_type)
    _model = BatchedInferencePipeline(model=base_model)
    elapsed = time.monotonic() - start
    logger.info("Model loaded in %.1f seconds", elapsed)
    return _model


def transcribe_file(audio_path: Path, language: str = "en") -> dict:
    """Transcribe an audio file and return structured results."""
    model = _load_model()
    batch_size = int(os.environ.get("WHISPER_BATCH_SIZE", "16"))

    logger.info("Transcribing %s (language=%s)", audio_path.name, language)
    start = time.monotonic()

    segments, info = model.transcribe(
        str(audio_path), language=language, word_timestamps=True, batch_size=batch_size
    )

    words = []
    for segment in segments:
        for w in segment.words or []:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end})

    elapsed = time.monotonic() - start

    duration = getattr(info, "duration", 0) or 0

    logger.info(
        "Transcription complete: %d words, %.1fs audio, %.1fs wall time",
        len(words),
        duration,
        elapsed,
    )

    return {
        "words": words,
        "language": getattr(info, "language", None) or language,
        "duration": duration,
        "model": _model_name,
        "elapsed_seconds": round(elapsed, 2),
    }


def create_app():
    """Create the Flask application."""
    from flask import Flask, jsonify, request

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB

    @app.route("/health", methods=["GET"])
    def health():
        import torch  # type: ignore[import-not-found]

        return jsonify(
            {
                "status": "ok",
                "model": _model_name,
                "model_loaded": _model is not None,
                "gpu_available": torch.cuda.is_available(),
                "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            }
        )

    @app.route("/transcribe", methods=["POST"])
    def transcribe():
        if "file" not in request.files:
            return (
                jsonify(
                    {"error": "No file uploaded. Send as multipart/form-data with key 'file'."}
                ),
                400,
            )

        audio_file = request.files["file"]
        language = request.form.get("language", "en")

        suffix = Path(audio_file.filename or "audio.opus").suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            audio_file.save(tmp)
            tmp_path = Path(tmp.name)

        try:
            result = transcribe_file(tmp_path, language=language)
            return jsonify(result)
        except Exception:
            logger.exception("Transcription failed")
            return jsonify({"error": "Transcription failed"}), 500
        finally:
            tmp_path.unlink(missing_ok=True)

    return app


def main():
    global _model_name

    parser = argparse.ArgumentParser(description="Whisper GPU transcription service")
    parser.add_argument(
        "--host",
        default="0.0.0.0",  # noqa: S104 — GPU cloud instances require all-interface binding; not a local service  # nosec B104 — bind 0.0.0.0 — intentional; service is fronted by Caddy/TLS reverse proxy, not exposed directly
        help="Bind address",
    )  # nosec B104
    parser.add_argument("--port", type=int, default=8765, help="Listen port")
    parser.add_argument("--model", default=_model_name, help="Whisper model size")
    parser.add_argument("--preload", action="store_true", help="Load model at startup")
    args = parser.parse_args()

    _model_name = args.model

    if args.preload:
        _load_model()

    app = create_app()
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
