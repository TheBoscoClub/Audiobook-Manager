#!/bin/bash
# Audiobook Library V2 - Database-backed version
# Launches Flask API server and opens web interface

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Audiobook Library V2 (Database)${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Development config: this launcher is for the project working tree only.
# It refuses to start without a dev config rather than fall back to the
# production defaults (ports 5001/8443, /var/lib/audiobooks).
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$PROJECT_ROOT/config.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_ROOT/config.env"
    set +a
fi

if [ -z "${AUDIOBOOKS_DATABASE:-}" ] || [ -z "${AUDIOBOOKS_API_PORT:-}" ] || [ -z "${AUDIOBOOKS_WEB_PORT:-}" ]; then
    echo -e "${RED}Error: no development configuration found.${NC}"
    echo "  Expected $PROJECT_ROOT/config.env (dev paths and ports), or set"
    echo "  AUDIOBOOKS_DATABASE, AUDIOBOOKS_API_PORT and AUDIOBOOKS_WEB_PORT in the environment."
    echo "  Refusing to start with production defaults."
    exit 1
fi

case "$AUDIOBOOKS_DATABASE" in
    /var/lib/audiobooks/* | /srv/audiobooks/* | /opt/audiobooks/*)
        echo -e "${RED}Error: AUDIOBOOKS_DATABASE ($AUDIOBOOKS_DATABASE) is an installed-application path.${NC}"
        echo "  This launcher is for the project working tree; use a dev database."
        exit 1
        ;;
esac

API_PORT="$AUDIOBOOKS_API_PORT"
PORT="$AUDIOBOOKS_WEB_PORT"
DB_PATH="$AUDIOBOOKS_DATABASE"
JSON_PATH="${DATA_DIR:-data}/audiobooks.json"

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    echo -e "${YELLOW}Database not found at $DB_PATH. Creating database...${NC}"
    if [ ! -f "$JSON_PATH" ]; then
        echo -e "${RED}Error: $JSON_PATH not found. Please run the scanner first:${NC}"
        echo -e "  cd scanner && python3 scan_audiobooks.py"
        exit 1
    fi

    source venv/bin/activate
    python backend/import_to_db.py
fi

# Check if Flask is installed
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Setting up virtual environment...${NC}"
    python -m venv venv
    source venv/bin/activate
    pip install -q Flask
else
    source venv/bin/activate
fi

# Check if API server is already running
if ss -tln | grep -q ":$API_PORT "; then
    echo -e "${YELLOW}API server already running on port $API_PORT${NC}"
else
    echo -e "${GREEN}Starting Flask API server...${NC}"
    python backend/api_server.py &
    API_PID=$!
    echo -e "${GREEN}✓ API server started (PID: $API_PID)${NC}"

    # Wait for API to be ready
    echo -n "Waiting for API to be ready"
    for i in {1..10}; do
        if curl -s http://localhost:"$API_PORT"/health >/dev/null 2>&1; then
            echo -e " ${GREEN}✓${NC}"
            break
        fi
        echo -n "."
        sleep 1
    done
fi

# Web port comes from the dev config; refuse if it is already taken
if ss -tln | grep -q ":$PORT "; then
    echo -e "${RED}Error: web port $PORT is already in use${NC}"
    exit 1
fi

echo -e "${GREEN}Starting web server on port $PORT...${NC}"
cd web-v2

# Start HTTP server in background
python -m http.server "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!

echo -e "${GREEN}✓ Web server started (PID: $SERVER_PID)${NC}"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Library is now running!${NC}"
echo ""
echo -e "API Server:  ${BLUE}http://localhost:$API_PORT${NC}"
echo -e "Web Interface: ${BLUE}http://localhost:$PORT${NC}"
echo ""
echo -e "API Documentation:"
echo -e "  ${BLUE}/api/stats${NC} - Library statistics"
echo -e "  ${BLUE}/api/audiobooks${NC} - Paginated audiobooks"
echo -e "  ${BLUE}/api/audiobooks?search=tolkien${NC} - Search"
echo -e "  ${BLUE}/api/audiobooks?author=sanderson${NC} - Filter"
echo -e "  ${BLUE}/api/filters${NC} - Available filters"
echo ""
echo -e "${YELLOW}Opening browser...${NC}"
echo -e "${BLUE}========================================${NC}"

# Wait a moment for server to fully start
sleep 2

# Open in Opera browser
if command -v opera &>/dev/null; then
    opera "http://localhost:$PORT" &
elif command -v xdg-open &>/dev/null; then
    xdg-open "http://localhost:$PORT" &
fi

echo ""
echo -e "${GREEN}Library is ready!${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop the servers"

# Keep script running
wait
