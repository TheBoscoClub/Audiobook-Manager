FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    XDG_CACHE_HOME=/models

# apt pins intentionally unpinned — the CUDA base image receives rolling
# security updates and hard apt pins would break reproducibility on every
# upstream rebuild. Floating versions track the base image's curated set.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
        faster-whisper==1.0.3 \
        flask==3.0.3 \
        gunicorn==22.0.0 \
        ctranslate2==4.4.0 \
        requests==2.32.3 \
        huggingface-hub==0.24.7 \
        tokenizers==0.20.3

# Pre-download model large-v3 so cold-start is instant, not 5GB of download
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8')" \
    && ls -lh /models

RUN useradd --system --uid 1001 --home-dir /app --create-home whisper \
    && chown -R whisper:whisper /models

WORKDIR /app
COPY --chown=whisper:whisper whisper_server.py /app/whisper_server.py
USER whisper

ENV WHISPER_COMPUTE_TYPE=float16 \
    WHISPER_MODEL=large-v3 \
    WORKERS_PER_GPU=4 \
    IDLE_SHUTDOWN_SEC=1800

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "gunicorn -w 1 -k gthread --threads ${WORKERS_PER_GPU} -b 0.0.0.0:8000 --timeout 600 whisper_server:app"]
