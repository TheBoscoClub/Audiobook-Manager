# Audiobooks - Web-based audiobook library browser
# Supports: Linux, macOS, Windows (via Docker Desktop)
#
# Build: docker build -t audiobooks .
# Run:   docker-compose up -d

FROM python:3.11-slim

LABEL maintainer="Audiobooks Project"
LABEL description="Web-based audiobook library with search, playback, cover art, and PDF supplements"
LABEL version="2.4"

# OCI labels for GitHub Container Registry
LABEL org.opencontainers.image.source="https://github.com/greogory/audiobook-toolkit"
LABEL org.opencontainers.image.description="Web-based audiobook library browser with search, playback, and PDF supplements"
LABEL org.opencontainers.image.licenses="MIT"

# Install system dependencies
# - ffmpeg: Audio/video processing for conversion and metadata
# - mediainfo: Audio file metadata extraction
# - jq: JSON processing for AAXtoMP3 converter
# - curl: Health checks and API testing
# Note: mp4v2-utils (mp4art, mp4chaps) not available in Debian trixie
# Chapter/cover tools use ffmpeg fallback instead
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    jq \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better Docker layer caching
COPY library/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy configuration module (shared by all Python scripts)
COPY library/config.py /app/config.py

# Copy application components
COPY library/backend /app/backend
COPY library/scanner /app/scanner
COPY library/scripts /app/scripts
COPY library/web-v2 /app/web

# Copy converter tools (AAXtoMP3 fork v2.2 for optional in-container conversion)
# Converter uses: ffmpeg, jq, mp4v2-utils (system), mutagen (pip)
# mutagen is required for Opus cover art embedding via METADATA_BLOCK_PICTURE
COPY converter /app/converter

# Create directories for data persistence
# Covers and supplements will be populated at runtime or mounted as volumes
RUN mkdir -p /app/data /app/covers /app/supplements

# Set environment variables
ENV FLASK_APP=backend/api.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Docker-specific paths (overrides config.py defaults)
ENV PROJECT_DIR=/app
ENV AUDIOBOOK_DIR=/audiobooks
ENV DATABASE_PATH=/app/data/audiobooks.db
ENV COVER_DIR=/app/covers
ENV DATA_DIR=/app/data
ENV SUPPLEMENTS_DIR=/supplements
ENV WEB_PORT=8090
ENV API_PORT=5001

# Expose ports
# 5001: Flask REST API
# 8090: Web interface (static files)
EXPOSE 5001 8090

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5001/api/audiobooks?limit=1 || exit 1

# Copy and set entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
