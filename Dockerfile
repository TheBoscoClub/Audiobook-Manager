# Audiobooks - Web-based audiobook library and converter
# Supports: Linux, macOS, Windows (via Docker Desktop)

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mediainfo \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY library/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY library/backend /app/backend
COPY library/web-v2 /app/web
COPY library/scanner /app/scanner
COPY library/scripts /app/scripts
COPY converter /app/converter

# Create directories for data persistence
RUN mkdir -p /app/data /app/covers

# Set environment variables
ENV FLASK_APP=backend/api.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Expose ports
# 5001: Flask API
# 8090: Web interface
EXPOSE 5001 8090

# Copy and set entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
