#!/bin/bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

# Get version from VERSION file
APP_VERSION=$(cat /app/VERSION 2>/dev/null | tr -d '[:space:]' || echo "unknown")

echo -e "${CYAN}=========================================="
echo "  Audiobook Library - Docker Container"
echo "  Version: ${APP_VERSION}"
echo -e "==========================================${NC}"
echo ""

# Ensure data directories exist
mkdir -p /app/data /app/covers

# Export environment variables for Python scripts
export DATABASE_PATH="${DATABASE_PATH:-/app/data/audiobooks.db}"
export AUDIOBOOK_DIR="${AUDIOBOOK_DIR:-/audiobooks}"
export COVER_DIR="${COVER_DIR:-/app/covers}"
export DATA_DIR="${DATA_DIR:-/app/data}"
export PROJECT_DIR="${PROJECT_DIR:-/app}"
export SUPPLEMENTS_DIR="${SUPPLEMENTS_DIR:-/supplements}"
export WEB_PORT="${WEB_PORT:-8443}"
export API_PORT="${API_PORT:-5001}"
export HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT:-8080}"
export AUDIOBOOKS_USE_WAITRESS="${AUDIOBOOKS_USE_WAITRESS:-true}"
export AUDIOBOOKS_BIND_ADDRESS="${AUDIOBOOKS_BIND_ADDRESS:-127.0.0.1}"

# Function to check if audiobooks are mounted
check_audiobooks_mounted() {
    if [ -d /audiobooks ] && [ "$(ls -A /audiobooks 2>/dev/null)" ]; then
        return 0
    fi
    return 1
}

# Function to count audiobook files
count_audiobooks() {
    find /audiobooks -type f \( -name "*.opus" -o -name "*.m4b" -o -name "*.mp3" -o -name "*.m4a" -o -name "*.flac" \) 2>/dev/null | wc -l
}

# Function to scan audiobooks
scan_audiobooks() {
    echo -e "${CYAN}Scanning audiobook directory...${NC}"
    cd /app/scanner
    if python3 scan_audiobooks.py; then
        echo -e "${GREEN}Scan complete${NC}"
        return 0
    else
        echo -e "${RED}Scan failed${NC}"
        return 1
    fi
}

# Function to import to database
import_to_database() {
    echo -e "${CYAN}Importing to database...${NC}"
    cd /app/backend
    if python3 import_to_db.py; then
        echo -e "${GREEN}Import complete${NC}"
        return 0
    else
        echo -e "${RED}Import failed${NC}"
        return 1
    fi
}

# Function to verify database has entries
database_has_entries() {
    if [ ! -f "$DATABASE_PATH" ]; then
        return 1
    fi
    count=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('$DATABASE_PATH')
    cursor = conn.execute('SELECT COUNT(*) FROM audiobooks')
    count = cursor.fetchone()[0]
    conn.close()
    print(count)
except:
    print(0)
" 2>/dev/null || echo "0")
    [ "$count" -gt 0 ]
}

# ============================================================================
# Step 1: Check for mounted audiobooks
# ============================================================================
echo -e "${CYAN}Checking mounted volumes...${NC}"

if check_audiobooks_mounted; then
    AUDIOBOOK_COUNT=$(count_audiobooks)
    echo -e "  Audiobooks: ${GREEN}$AUDIOBOOK_COUNT files found${NC}"
else
    echo -e "  Audiobooks: ${YELLOW}None mounted${NC}"
    echo ""
    echo -e "${YELLOW}Warning: No audiobooks directory mounted.${NC}"
    echo "Mount your audiobook directory to use this container:"
    echo "  docker run -v /path/to/audiobooks:/audiobooks:ro ..."
    echo ""
fi

# Check supplements
if [ -d "$SUPPLEMENTS_DIR" ] && [ "$(ls -A $SUPPLEMENTS_DIR 2>/dev/null)" ]; then
    SUPPLEMENT_COUNT=$(find "$SUPPLEMENTS_DIR" -type f \( -name "*.pdf" -o -name "*.epub" \) 2>/dev/null | wc -l)
    echo -e "  Supplements: ${GREEN}$SUPPLEMENT_COUNT files found${NC}"
else
    echo -e "  Supplements: ${YELLOW}None mounted (optional)${NC}"
fi

echo ""

# ============================================================================
# Step 2: Auto-initialize database if needed
# ============================================================================
echo -e "${CYAN}Checking database...${NC}"

if [ ! -f "$DATABASE_PATH" ]; then
    echo -e "  Database: ${YELLOW}Not found${NC}"
    NEEDS_INIT=true
elif ! database_has_entries; then
    echo -e "  Database: ${YELLOW}Empty (no audiobooks)${NC}"
    NEEDS_INIT=true
else
    DB_COUNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DATABASE_PATH')
cursor = conn.execute('SELECT COUNT(*) FROM audiobooks')
count = cursor.fetchone()[0]
conn.close()
print(count)
" 2>/dev/null || echo "0")
    echo -e "  Database: ${GREEN}$DB_COUNT audiobooks indexed${NC}"
    NEEDS_INIT=false
fi

echo ""

# ============================================================================
# Step 3: Auto-scan and import if database is missing/empty
# ============================================================================
if [ "$NEEDS_INIT" = true ] && check_audiobooks_mounted; then
    echo -e "${CYAN}=========================================="
    echo "  First-time setup: Scanning library"
    echo -e "==========================================${NC}"
    echo ""

    # Scan audiobooks
    if scan_audiobooks; then
        echo ""
        # Import to database
        if import_to_database; then
            echo ""
            echo -e "${GREEN}Library initialized successfully!${NC}"

            # Show final count
            DB_COUNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DATABASE_PATH')
cursor = conn.execute('SELECT COUNT(*) FROM audiobooks')
count = cursor.fetchone()[0]
conn.close()
print(count)
" 2>/dev/null || echo "0")
            echo -e "  Indexed: ${GREEN}$DB_COUNT audiobooks${NC}"
        fi
    fi
    echo ""
elif [ "$NEEDS_INIT" = true ]; then
    echo -e "${YELLOW}Skipping auto-initialization: No audiobooks mounted${NC}"
    echo "Mount your audiobooks and restart the container, or run manually:"
    echo "  docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py"
    echo "  docker exec -it audiobooks python3 /app/backend/import_to_db.py"
    echo ""
fi

# ============================================================================
# Step 4: Scan supplements if available
# ============================================================================
if [ -d "$SUPPLEMENTS_DIR" ] && [ "$(ls -A $SUPPLEMENTS_DIR 2>/dev/null)" ]; then
    echo -e "${CYAN}Scanning supplements...${NC}"
    cd /app/scripts && python3 scan_supplements.py --supplements-dir "$SUPPLEMENTS_DIR" --quiet 2>/dev/null || true
    echo -e "  ${GREEN}Supplements scanned${NC}"
    echo ""
fi

# ============================================================================
# Step 5: Start servers
# ============================================================================
echo -e "${CYAN}=========================================="
echo "  Starting services"
echo -e "==========================================${NC}"
echo ""

# Start API server with waitress (production WSGI)
echo -e "Starting API server (waitress) on port ${API_PORT}..."
cd /app/backend
AUDIOBOOKS_USE_WAITRESS=true AUDIOBOOKS_BIND_ADDRESS=127.0.0.1 python3 api_server.py &
API_PID=$!

# Wait for API to start
sleep 2

# Check if API started successfully
if ! kill -0 $API_PID 2>/dev/null; then
    echo -e "${RED}Error: API server failed to start${NC}"
    echo "Check logs for details"
else
    echo -e "  API: ${GREEN}Running on port ${API_PORT}${NC} (waitress)"
fi

# Wait for API health check
echo -n "Waiting for API to be ready"
for i in {1..10}; do
    if curl -s http://localhost:${API_PORT}/api/stats > /dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e " ${RED}✗${NC}"
        echo -e "${RED}Warning: API may not be fully ready${NC}"
    fi
    echo -n "."
    sleep 1
done

# Generate self-signed certs if missing
export AUDIOBOOKS_CERTS="${AUDIOBOOKS_CERTS:-/app/certs}"
mkdir -p "$AUDIOBOOKS_CERTS"
if [ ! -f "$AUDIOBOOKS_CERTS/server.crt" ] || [ ! -f "$AUDIOBOOKS_CERTS/server.key" ]; then
    echo -e "Generating self-signed TLS certificate..."
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$AUDIOBOOKS_CERTS/server.key" \
        -out "$AUDIOBOOKS_CERTS/server.crt" \
        -days 365 -subj '/CN=localhost' 2>/dev/null
    echo -e "  ${GREEN}Certificate generated${NC}"
fi

# Start HTTPS reverse proxy
echo -e "Starting HTTPS proxy on port ${WEB_PORT}..."
cd /app/web
python3 proxy_server.py &
PROXY_PID=$!

sleep 2
if ! kill -0 $PROXY_PID 2>/dev/null; then
    echo -e "${RED}Error: HTTPS proxy failed to start${NC}"
else
    echo -e "  Proxy: ${GREEN}Running on port ${WEB_PORT}${NC} (HTTPS)"
fi

# Start HTTP redirect (optional)
if [ "${HTTP_REDIRECT_ENABLED:-true}" = "true" ]; then
    echo -e "Starting HTTP redirect on port ${HTTP_REDIRECT_PORT}..."
    cd /app/web
    python3 redirect_server.py &
    REDIRECT_PID=$!

    sleep 1
    if ! kill -0 $REDIRECT_PID 2>/dev/null; then
        echo -e "${YELLOW}Warning: HTTP redirect server failed to start${NC}"
        REDIRECT_PID=""
    else
        echo -e "  Redirect: ${GREEN}Running on port ${HTTP_REDIRECT_PORT}${NC}"
    fi
fi

echo ""
echo -e "${GREEN}=========================================="
echo "  Audiobook Library is running!"
echo "=========================================="
echo -e "  Web UI:  https://localhost:${WEB_PORT}"
if [ -n "$REDIRECT_PID" ]; then
    echo -e "           http://localhost:${HTTP_REDIRECT_PORT} (redirects to HTTPS)"
fi
echo -e "  API:     http://localhost:${API_PORT} (internal)"
echo -e "==========================================${NC}"
echo ""
echo "API Endpoints (via HTTPS proxy):"
echo "  GET /api/audiobooks       - List all audiobooks"
echo "  GET /api/audiobooks/:id   - Get audiobook details"
echo "  GET /api/search?q=query   - Search audiobooks"
echo "  GET /api/stats            - Library statistics"
echo "  GET /api/narrator-counts  - Narrator statistics"
echo ""
echo "Management Commands:"
echo "  docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py"
echo "  docker exec -it audiobooks python3 /app/backend/import_to_db.py"
echo ""

# Handle shutdown gracefully
PIDS="$API_PID $PROXY_PID"
[ -n "$REDIRECT_PID" ] && PIDS="$PIDS $REDIRECT_PID"
# shellcheck disable=SC2064  # We want $PIDS expanded at trap definition time
trap "echo 'Shutting down...'; kill $PIDS 2>/dev/null; exit 0" SIGTERM SIGINT

# Keep container running and wait for processes
wait
