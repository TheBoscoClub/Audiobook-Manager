#!/usr/bin/env zsh
# Audiobook Library V2 - Database-backed version
# Launches Flask API server and opens web interface

set -e

SCRIPT_DIR="${0:A:h}"
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

# Check if database exists (use config path, default to /var/lib/audiobooks/db)
DB_PATH="${AUDIOBOOKS_DATABASE:-/var/lib/audiobooks/db/audiobooks.db}"
if [ ! -f "$DB_PATH" ]; then
    echo -e "${YELLOW}Database not found at $DB_PATH. Creating database...${NC}"
    if [ ! -f "data/audiobooks.json" ]; then
        echo -e "${RED}Error: audiobooks.json not found. Please run the scanner first:${NC}"
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
if ss -tln | grep -q ":5001 "; then
    echo -e "${YELLOW}API server already running on port 5001${NC}"
else
    echo -e "${GREEN}Starting Flask API server...${NC}"
    python backend/api_server.py &
    API_PID=$!
    echo -e "${GREEN}✓ API server started (PID: $API_PID)${NC}"

    # Wait for API to be ready
    echo -n "Waiting for API to be ready"
    for i in {1..10}; do
        if curl -s http://localhost:5001/health > /dev/null 2>&1; then
            echo -e " ${GREEN}✓${NC}"
            break
        fi
        echo -n "."
        sleep 1
    done
fi

# Find available port for frontend (8090-8099)
PORT=8090
MAX_PORT=8099

while [ $PORT -le $MAX_PORT ]; do
    if ! ss -tln | grep -q ":$PORT "; then
        break
    fi
    PORT=$((PORT + 1))
done

if [ $PORT -gt $MAX_PORT ]; then
    echo -e "${RED}Error: No available ports in range 8090-8099${NC}"
    exit 1
fi

echo -e "${GREEN}Starting web server on port $PORT...${NC}"
cd web-v2

# Start HTTP server in background
python -m http.server $PORT > /dev/null 2>&1 &
SERVER_PID=$!

echo -e "${GREEN}✓ Web server started (PID: $SERVER_PID)${NC}"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Library is now running!${NC}"
echo ""
echo -e "API Server:  ${BLUE}http://localhost:5001${NC}"
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
if command -v opera &> /dev/null; then
    opera "http://localhost:$PORT" &
elif command -v xdg-open &> /dev/null; then
    xdg-open "http://localhost:$PORT" &
fi

echo ""
echo -e "${GREEN}Library is ready!${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop the servers"

# Keep script running
wait
