#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=========================================="
echo "  Audiobook Library - Starting"
echo -e "==========================================${NC}"

# Ensure data directories exist
mkdir -p /app/data /app/covers

# Check for database
if [ ! -f /app/data/audiobooks.db ]; then
    echo -e "${YELLOW}Database not found at /app/data/audiobooks.db${NC}"
    echo ""
    echo "To scan your audiobooks for the first time, run:"
    echo "  docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py"
    echo "  docker exec -it audiobooks python3 /app/backend/import_to_db.py"
    echo ""
    echo "Or mount an existing database to /app/data/audiobooks.db"
    echo ""
fi

# Check if audiobooks are mounted
if [ -d /audiobooks ] && [ "$(ls -A /audiobooks 2>/dev/null)" ]; then
    AUDIOBOOK_COUNT=$(find /audiobooks -name "*.opus" -o -name "*.m4b" -o -name "*.mp3" 2>/dev/null | wc -l)
    echo -e "Audiobooks mounted: ${GREEN}$AUDIOBOOK_COUNT files found${NC}"
else
    echo -e "${YELLOW}Warning: No audiobooks found in /audiobooks${NC}"
    echo "Make sure to mount your audiobook directory:"
    echo "  -v /path/to/audiobooks:/audiobooks:ro"
fi

# Export environment variables for Python scripts
export DATABASE_PATH="${DATABASE_PATH:-/app/data/audiobooks.db}"
export AUDIOBOOK_DIR="${AUDIOBOOK_DIR:-/audiobooks}"
export COVER_DIR="${COVER_DIR:-/app/covers}"
export DATA_DIR="${DATA_DIR:-/app/data}"
export PROJECT_DIR="${PROJECT_DIR:-/app}"
export SUPPLEMENTS_DIR="${SUPPLEMENTS_DIR:-/supplements}"

# Scan supplements if directory exists and has files
if [ -d "$SUPPLEMENTS_DIR" ] && [ "$(ls -A $SUPPLEMENTS_DIR 2>/dev/null)" ]; then
    SUPPLEMENT_COUNT=$(find "$SUPPLEMENTS_DIR" -type f \( -name "*.pdf" -o -name "*.epub" -o -name "*.jpg" -o -name "*.png" \) 2>/dev/null | wc -l)
    echo -e "Supplements mounted: ${GREEN}$SUPPLEMENT_COUNT files found${NC}"
    echo "Scanning supplements into database..."
    cd /app/scripts && python3 scan_supplements.py --supplements-dir "$SUPPLEMENTS_DIR" --quiet || true
    echo -e "${GREEN}Supplements scanned${NC}"
fi

echo ""

# Start Flask API in background
echo -e "Starting API server on port ${API_PORT:-5001}..."
cd /app/backend
python3 api.py &
API_PID=$!

# Wait for API to start
sleep 2

# Check if API started successfully
if ! kill -0 $API_PID 2>/dev/null; then
    echo -e "${YELLOW}Warning: API server may have failed to start${NC}"
    echo "Check logs for details"
fi

# Start web server
echo -e "Starting web server on port ${WEB_PORT:-8090}..."
cd /app/web
python3 -m http.server ${WEB_PORT:-8090} &
WEB_PID=$!

echo ""
echo -e "${GREEN}=========================================="
echo "  Audiobook Library is running!"
echo "=========================================="
echo -e "  Web UI:  http://localhost:${WEB_PORT:-8090}"
echo -e "  API:     http://localhost:${API_PORT:-5001}"
echo -e "==========================================${NC}"
echo ""
echo "API Endpoints:"
echo "  GET /api/audiobooks       - List all audiobooks"
echo "  GET /api/audiobooks/:id   - Get audiobook details"
echo "  GET /api/search?q=query   - Search audiobooks"
echo "  GET /api/narrator-counts  - Narrator statistics"
echo ""

# Handle shutdown gracefully
trap "echo 'Shutting down...'; kill $API_PID $WEB_PID 2>/dev/null; exit 0" SIGTERM SIGINT

# Keep container running and wait for processes
wait
