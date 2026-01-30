#!/bin/bash
# =============================================================================
# Audiobook-Manager Development Server
# =============================================================================
# Starts Caddy + Flask API in development mode.
# Uses ports 9443 (HTTPS) and 6001 (API) to avoid conflict with production.
#
# Usage:
#   ./dev/start-dev.sh         # Normal start
#   ./dev/start-dev.sh --fg    # Foreground mode (see all logs)
#
# =============================================================================

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Get project root (parent of dev/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Source config
if [ -f "$PROJECT_DIR/config.env" ]; then
    source "$PROJECT_DIR/config.env"
else
    echo -e "${RED}Error: config.env not found${NC}"
    exit 1
fi

# Export PROJECT_DIR for Caddyfile
export PROJECT_DIR

# PID files
PID_DIR="/tmp/audiobooks-dev"
mkdir -p "$PID_DIR"
API_PID_FILE="$PID_DIR/api.pid"
CADDY_PID_FILE="$PID_DIR/caddy.pid"

# Ports from config
API_PORT=${AUDIOBOOKS_API_PORT:-6001}
WEB_PORT=${AUDIOBOOKS_WEB_PORT:-9443}
HTTP_PORT=${AUDIOBOOKS_HTTP_REDIRECT_PORT:-9081}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Audiobook-Manager Development Mode${NC}"
echo -e "${BLUE}   Branch: $(git branch --show-current 2>/dev/null || echo 'unknown')${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if already running
check_running() {
    local pid_file=$1
    local name=$2

    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "${YELLOW}$name already running (PID: $pid)${NC}"
            echo -e "Run ${BLUE}./dev/stop-dev.sh${NC} first, or:"
            echo -e "  kill $pid"
            return 1
        else
            rm -f "$pid_file"
        fi
    fi
    return 0
}

if ! check_running "$API_PID_FILE" "API server"; then
    exit 1
fi

if ! check_running "$CADDY_PID_FILE" "Caddy"; then
    exit 1
fi

# Check port availability
check_port() {
    local port=$1
    local name=$2

    if ss -tln 2>/dev/null | grep -q ":$port "; then
        echo -e "${RED}Error: Port $port ($name) already in use${NC}"
        ss -tlnp 2>/dev/null | grep ":$port " || true
        return 1
    fi
    return 0
}

if ! check_port $API_PORT "API"; then
    exit 1
fi

if ! check_port $WEB_PORT "HTTPS"; then
    exit 1
fi

# Check dependencies
echo -e "${BLUE}Checking dependencies...${NC}"

# Check Caddy
if ! command -v caddy &> /dev/null; then
    echo -e "${RED}Error: Caddy not installed${NC}"
    echo "Install with: sudo pacman -S caddy"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Caddy $(caddy version | head -1)"

# Check Python venv
cd "$PROJECT_DIR/library"
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi
source venv/bin/activate

# Check Flask
if ! python -c "import flask" 2>/dev/null; then
    echo -e "${YELLOW}Installing Flask...${NC}"
    pip install -q flask
fi
echo -e "  ${GREEN}✓${NC} Flask installed"

# Check dev database
if [ ! -f "$PROJECT_DIR/library/backend/audiobooks-dev.db" ]; then
    echo -e "${YELLOW}Warning: Dev database not found${NC}"
    echo -e "  Expected: $PROJECT_DIR/library/backend/audiobooks-dev.db"
    echo -e "  The API may fail to start."
fi

# Initialize auth database (encrypted with SQLCipher)
AUTH_DB="$PROJECT_DIR/library/backend/auth-dev.db"
AUTH_KEY="$PROJECT_DIR/dev/auth-dev.key"
export AUTH_DATABASE="$AUTH_DB"
export AUTH_KEY_FILE="$AUTH_KEY"

echo -e "${BLUE}Initializing auth database...${NC}"
if python auth/cli.py --dev -d "$AUTH_DB" -k "$AUTH_KEY" init 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Auth database ready"
else
    echo -e "  ${YELLOW}!${NC} Auth database init failed (may need sqlcipher3)"
fi

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down dev servers...${NC}"

    if [ -f "$CADDY_PID_FILE" ]; then
        local pid=$(cat "$CADDY_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  Stopping Caddy (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$CADDY_PID_FILE"
    fi

    if [ -f "$API_PID_FILE" ]; then
        local pid=$(cat "$API_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  Stopping API (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$API_PID_FILE"
    fi

    echo -e "${GREEN}Dev servers stopped${NC}"
}

trap cleanup EXIT SIGTERM SIGINT

# Start API server
echo ""
echo -e "${GREEN}Starting API server on port $API_PORT...${NC}"
cd "$PROJECT_DIR/library/backend"

# Set environment for dev mode
export AUDIOBOOKS_DEV_MODE="true"
export AUDIOBOOKS_DATABASE="$PROJECT_DIR/library/backend/audiobooks-dev.db"
export AUDIOBOOKS_API_PORT="$API_PORT"
export AUDIOBOOKS_BIND_ADDRESS="127.0.0.1"

python api_server.py > /tmp/audiobooks-dev-api.log 2>&1 &
API_PID=$!
echo $API_PID > "$API_PID_FILE"

# Wait for API to be ready
echo -n "  Waiting for API"
for i in {1..15}; do
    if curl -s "http://localhost:$API_PORT/health" > /dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    if [ $i -eq 15 ]; then
        echo -e " ${RED}✗${NC}"
        echo -e "${RED}Error: API failed to start. Check /tmp/audiobooks-dev-api.log${NC}"
        tail -20 /tmp/audiobooks-dev-api.log
        exit 1
    fi
    echo -n "."
    sleep 1
done

echo -e "  ${GREEN}✓${NC} API server started (PID: $API_PID)"

# Start Caddy
echo ""
echo -e "${GREEN}Starting Caddy on port $WEB_PORT...${NC}"
cd "$PROJECT_DIR"

caddy run --config "$PROJECT_DIR/dev/Caddyfile" --adapter caddyfile > /tmp/audiobooks-dev-caddy.log 2>&1 &
CADDY_PID=$!
echo $CADDY_PID > "$CADDY_PID_FILE"

# Wait for Caddy to be ready
sleep 2
if ! kill -0 $CADDY_PID 2>/dev/null; then
    echo -e "${RED}Error: Caddy failed to start. Check /tmp/audiobooks-dev-caddy.log${NC}"
    tail -20 /tmp/audiobooks-dev-caddy.log
    exit 1
fi

echo -e "  ${GREEN}✓${NC} Caddy started (PID: $CADDY_PID)"

# Summary
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Development servers running!${NC}"
echo ""
echo -e "Access the library at:"
echo -e "  ${BLUE}https://localhost:$WEB_PORT${NC}"
echo ""
echo -e "API endpoint:"
echo -e "  ${BLUE}http://localhost:$API_PORT/api/${NC}"
echo ""
echo -e "Logs:"
echo -e "  API:   /tmp/audiobooks-dev-api.log"
echo -e "  Caddy: /tmp/audiobooks-dev-caddy.log"
echo -e "  Caddy: /tmp/caddy-audiobooks-dev.log (access log)"
echo ""
echo -e "PIDs:"
echo -e "  API:   $API_PID"
echo -e "  Caddy: $CADDY_PID"
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop"
echo ""

# Open browser (optional)
if [ "$1" != "--no-browser" ]; then
    sleep 1
    if command -v xdg-open &> /dev/null; then
        xdg-open "https://localhost:$WEB_PORT" &> /dev/null &
    fi
fi

# Supervision loop
while true; do
    if ! kill -0 $API_PID 2>/dev/null; then
        echo -e "${RED}API server died unexpectedly${NC}"
        exit 1
    fi

    if ! kill -0 $CADDY_PID 2>/dev/null; then
        echo -e "${RED}Caddy died unexpectedly${NC}"
        exit 1
    fi

    sleep 5
done
