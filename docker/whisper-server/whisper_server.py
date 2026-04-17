import tempfile
import os
import signal
import time
import logging
import threading
import traceback
from flask import Flask, request, jsonify
from faster_whisper import WhisperModel

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("whisper-server")

COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3")
log.info("Loading model %s compute_type=%s on cuda", MODEL_SIZE, COMPUTE_TYPE)
model = WhisperModel(MODEL_SIZE, device="cuda", compute_type=COMPUTE_TYPE)
log.info("Model loaded")

# faster-whisper / CTranslate2 GPU inference is NOT safe under concurrent calls
# against a single model instance. Serialize transcribe() across worker threads.
_model_lock = threading.Lock()

# Dead-man TTL: cost protection for orphaned/idle pods.
# Default 1800s (30min). The old version called os._exit(0) which only killed
# the gunicorn worker thread — container stayed RUNNING (still billing) and
# the Cloudflare proxy kept routing to a dead port. Fix: SIGTERM PID 1
# (gunicorn master) so the whole container exits cleanly, RunPod sees it
# STOPPED, and billing halts.
IDLE_SHUTDOWN_SEC = int(os.environ.get("IDLE_SHUTDOWN_SEC", "1800"))
LAST_REQUEST_TIME = time.time()
_last_lock = threading.Lock()


@app.before_request
def _bump_idle_timer():
    global LAST_REQUEST_TIME
    with _last_lock:
        LAST_REQUEST_TIME = time.time()


def _deadman_loop():
    while True:
        time.sleep(60)
        with _last_lock:
            idle = time.time() - LAST_REQUEST_TIME
        if idle > IDLE_SHUTDOWN_SEC:
            logging.warning(
                "Dead-man TTL: idle %ds > %ds — terminating container",
                int(idle),
                IDLE_SHUTDOWN_SEC,
            )
            # SIGTERM PID 1 (gunicorn master). Gunicorn gracefully shuts down
            # all workers; container exits; RunPod stops billing.
            try:
                os.kill(1, signal.SIGTERM)
            except Exception as exc:
                logging.error(
                    "Failed to SIGTERM PID 1: %s — falling back to os._exit", exc
                )
                os._exit(0)
            # If PID 1 doesn't exit within 30s, escalate to SIGKILL.
            time.sleep(30)
            try:
                os.kill(1, signal.SIGKILL)
            except Exception:
                pass
            os._exit(0)


if IDLE_SHUTDOWN_SEC > 0:
    threading.Thread(target=_deadman_loop, daemon=True).start()
    log.info(
        "Dead-man TTL enabled: %ds (terminates container via SIGTERM PID 1)",
        IDLE_SHUTDOWN_SEC,
    )
else:
    log.info("Dead-man TTL disabled (IDLE_SHUTDOWN_SEC=0)")


@app.route("/v1/audio/transcriptions", methods=["POST"])
def transcribe():
    audio_file = request.files.get("file")
    if not audio_file:
        return jsonify({"error": "No file"}), 400
    language = request.form.get("language", "en")
    t0 = time.time()
    with tempfile.NamedTemporaryFile(suffix=".opus", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name
    size = os.path.getsize(tmp_path)
    log.info("received file=%s bytes lang=%s", size, language)
    try:
        with _model_lock:
            segments, info = model.transcribe(
                tmp_path, language=language, word_timestamps=True
            )
            words, text_parts = [], []
            for seg in segments:
                text_parts.append(seg.text)
                for w in seg.words or []:
                    words.append({"word": w.word, "start": w.start, "end": w.end})
        log.info(
            "transcribed in %.1fs duration=%.1fs words=%d",
            time.time() - t0,
            info.duration,
            len(words),
        )
        return jsonify(
            {
                "text": " ".join(text_parts),
                "language": info.language,
                "duration": info.duration,
                "words": words,
            }
        )
    except Exception as e:
        log.error("transcribe failed: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": str(e), "type": type(e).__name__}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/health")
def health():
    return jsonify({"status": "ok", "model": MODEL_SIZE})


@app.route("/selftest")
def selftest():
    """Generate silence internally and transcribe — bypasses HTTP multipart."""
    t0 = time.time()
    import numpy as np  # noqa: PLC0415

    try:
        audio = np.zeros(16000, dtype=np.float32)  # 1 second silence at 16kHz
        with _model_lock:
            segments, info = model.transcribe(
                audio, language="en", word_timestamps=False
            )
            parts = [s.text for s in segments]
        return jsonify(
            {
                "ok": True,
                "duration": info.duration,
                "elapsed": time.time() - t0,
                "text": " ".join(parts),
            }
        )
    except Exception as e:
        log.error("selftest failed: %s\n%s", e, traceback.format_exc())
        return jsonify({"ok": False, "error": str(e), "type": type(e).__name__}), 500
