#!/usr/bin/env python3
"""Standalone Whisper GPU transcription service.

Runs as a system service on the host where the AMD GPU lives. Accepts
audio uploads via HTTP and returns word-level timestamps. The audiobook
API (on VMs or the host) calls this instead of loading Whisper in-process.

Usage:
    python3 whisper_gpu_service.py [--host 0.0.0.0] [--port 8765] [--model large-v3]

Requires: python-pytorch-opt-rocm, python-openai-whisper (system packages)
"""

import argparse
import logging
import tempfile
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("whisper-gpu")

_model = None
_model_name = "large-v3"


def _load_model():
    global _model
    if _model is not None:
        return _model

    import torch
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        logger.info("GPU detected: %s (%.1f GB VRAM)", gpu_name, vram_gb)
    else:
        logger.warning("No GPU detected — running on CPU (will be slow)")

    logger.info("Loading Whisper model '%s' on %s…", _model_name, device)
    start = time.monotonic()
    _model = whisper.load_model(_model_name, device=device)
    elapsed = time.monotonic() - start
    logger.info("Model loaded in %.1f seconds", elapsed)
    return _model


def transcribe_file(audio_path: Path, language: str = "en") -> dict:
    """Transcribe an audio file and return structured results."""
    model = _load_model()

    logger.info("Transcribing %s (language=%s)", audio_path.name, language)
    start = time.monotonic()

    result = model.transcribe(
        str(audio_path), language=language, word_timestamps=True, verbose=False
    )

    elapsed = time.monotonic() - start

    words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            words.append({"word": w["word"].strip(), "start": w["start"], "end": w["end"]})

    duration = result.get("segments", [{}])[-1].get("end", 0) if result.get("segments") else 0

    logger.info(
        "Transcription complete: %d words, %.1fs audio, %.1fs wall time",
        len(words),
        duration,
        elapsed,
    )

    return {
        "words": words,
        "language": result.get("language", language),
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
        import torch

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
    parser = argparse.ArgumentParser(description="Whisper GPU transcription service")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind address"
    )  # nosec B104 — GPU cloud instance
    parser.add_argument("--port", type=int, default=8765, help="Listen port")
    parser.add_argument("--model", default="large-v3", help="Whisper model size")
    parser.add_argument("--preload", action="store_true", help="Load model at startup")
    args = parser.parse_args()

    global _model_name
    _model_name = args.model

    if args.preload:
        _load_model()

    app = create_app()
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
