# Audiobooks - Standalone audiobook library browser
# A fully self-contained, portable product for cross-platform deployment.
# Includes all databases, dependencies, and runtime — no external services needed.
# Supports: Linux, macOS, Windows (via Docker Desktop)
# Platforms: linux/amd64, linux/arm64
#
# Build: docker build -t audiobooks .
# Run:   docker-compose up -d

FROM python:3.14-slim

# Read version from VERSION file during build
ARG APP_VERSION=7.5.1.3

LABEL maintainer="Audiobooks Project"
LABEL description="Standalone audiobook library — fully self-contained with all databases and dependencies"
LABEL version="${APP_VERSION}"

# OCI labels for GitHub Container Registry
LABEL org.opencontainers.image.source="https://github.com/TheBoscoClub/Audiobook-Manager"
LABEL org.opencontainers.image.description="Standalone audiobook library browser — portable, cross-platform, self-contained"
LABEL org.opencontainers.image.licenses="MIT"

# Install system dependencies (Debian Trixie packages)
# - ffmpeg: Audio/video processing for conversion and metadata
# - mediainfo: Audio file metadata extraction
# - jq: JSON processing for AAXtoMP3 converter
# - curl: Health checks and API testing
# - libsqlcipher-dev: Encrypted SQLite for auth database
# - openssl: TLS certificate generation
# hadolint ignore=DL3008
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    jq \
    curl \
    libsqlcipher-dev \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Upgrade pip to latest before installing dependencies
# hadolint ignore=DL3013
RUN pip install --no-cache-dir --upgrade pip

# Copy Docker-specific requirements (excludes audible CLI — not needed in standalone container)
COPY library/requirements-docker.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy configuration and utility modules (shared by all Python scripts)
COPY library/config.py /app/config.py
COPY library/common.py /app/common.py

# Copy application components
COPY library/auth /app/auth
COPY library/backend /app/backend
COPY library/scanner /app/scanner
COPY library/scripts /app/scripts
COPY library/web-v2 /app/web

# Copy converter tools (AAXtoMP3 fork v2.2 for optional in-container conversion)
# Converter uses: ffmpeg, jq, mp4v2-utils (system), mutagen (pip)
# mutagen is required for Opus cover art embedding via METADATA_BLOCK_PICTURE
COPY converter /app/converter

# Copy documentation for reference inside container
COPY README.md /app/README.md

# Copy version and release information
COPY VERSION /app/VERSION

# Create .release-info for version identification
# Note: Docker upgrades via image pulls, not upgrade.sh
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN printf '{\n  "github_repo": "TheBoscoClub/Audiobook-Manager",\n  "github_api": "https://api.github.com/repos/TheBoscoClub/Audiobook-Manager",\n  "version": "%s",\n  "install_type": "docker",\n  "install_date": "%s"\n}\n' \
  "$(tr -d '[:space:]' < /app/VERSION)" "$(date -Iseconds)" > /app/.release-info
SHELL ["/bin/sh", "-c"]

# Create directories for data persistence
# Covers and supplements will be populated at runtime or mounted as volumes
RUN mkdir -p /app/data /app/covers /app/supplements

# Set environment variables
ENV FLASK_APP=backend.api_modular:create_app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Docker-specific paths (overrides config.py defaults)
ENV AUDIOBOOKS_HOME=/app
ENV PROJECT_DIR=/app
ENV AUDIOBOOK_DIR=/audiobooks
ENV DATABASE_PATH=/app/data/audiobooks.db
ENV COVER_DIR=/app/covers
ENV DATA_DIR=/app/data
ENV SUPPLEMENTS_DIR=/supplements
ENV WEB_PORT=8443
ENV API_PORT=5001

# Expose ports
# 5001: Flask REST API
# 8443: HTTPS Web interface
# 8080: HTTP redirect to HTTPS
EXPOSE 5001 8443 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5001/api/system/health || exit 1

# Create non-root user for security
RUN groupadd --gid 1000 audiobooks && \
    useradd --uid 1000 --gid audiobooks --shell /bin/bash --create-home audiobooks && \
    chown -R audiobooks:audiobooks /app

# Copy and set entrypoint (755 = readable and executable by all)
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod 755 /docker-entrypoint.sh

# Switch to non-root user
USER audiobooks

ENTRYPOINT ["/docker-entrypoint.sh"]
