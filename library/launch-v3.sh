#!/bin/bash
# Audiobook Library V3 - Production-ready HTTPS server
# Features:
# - Waitress WSGI server for API
# - Reverse proxy for unified HTTPS access
# - HTTP→HTTPS redirect
# - Proper process management with PID tracking
# - Graceful shutdown on SIGTERM/SIGINT

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# PID files (persistent in /var/tmp)
PID_DIR="/var/tmp"
API_PID_FILE="$PID_DIR/audiobook-api.pid"
PROXY_PID_FILE="$PID_DIR/audiobook-proxy.pid"
REDIRECT_PID_FILE="$PID_DIR/audiobook-redirect.pid"

# Ports (from config or defaults)
API_PORT=${AUDIOBOOKS_API_PORT:-5001}
WEB_PORT=${AUDIOBOOKS_WEB_PORT:-8443}
HTTP_PORT=${AUDIOBOOKS_HTTP_REDIRECT_PORT:-8080}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Audiobook Library V3 (Production)${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Cleanup function
cleanup_processes() {
    echo -e "${YELLOW}Shutting down servers...${NC}"

    # Function to gracefully stop a process
    stop_process() {
        local pid_file=$1
        local name=$2

        if [ -f "$pid_file" ]; then
            local pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                echo -e "  Stopping $name (PID: $pid)..."
                kill -TERM "$pid" 2>/dev/null || true

                # Wait up to 10 seconds for graceful shutdown
                for i in {1..10}; do
                    if ! kill -0 "$pid" 2>/dev/null; then
                        break
                    fi
                    sleep 1
                done

                # Force kill if still running
                if kill -0 "$pid" 2>/dev/null; then
                    echo -e "  ${YELLOW}Force killing $name${NC}"
                    kill -KILL "$pid" 2>/dev/null || true
                fi
            fi
            rm -f "$pid_file"
        fi
    }

    stop_process "$REDIRECT_PID_FILE" "HTTP redirect"
    stop_process "$PROXY_PID_FILE" "HTTPS proxy"
    stop_process "$API_PID_FILE" "API server"

    echo -e "${GREEN}Shutdown complete${NC}"
}

# Set up signal handlers
trap cleanup_processes EXIT SIGTERM SIGINT

# Clean stale PID files
echo -e "${BLUE}Checking for stale processes...${NC}"
for pid_file in "$API_PID_FILE" "$PROXY_PID_FILE" "$REDIRECT_PID_FILE"; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if ! kill -0 "$pid" 2>/dev/null; then
            echo -e "  Removing stale PID file: $pid_file"
            rm -f "$pid_file"
        else
            echo -e "${YELLOW}Warning: Process already running (PID: $pid)${NC}"
            echo -e "Kill it with: kill $pid"
            exit 1
        fi
    fi
done

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

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Setting up virtual environment...${NC}"
    python3 -m venv venv
    source venv/bin/activate
    pip install -q -r requirements.txt
else
    source venv/bin/activate
fi

# Check if waitress is installed
if ! python -c "import waitress" 2>/dev/null; then
    echo -e "${YELLOW}Installing waitress...${NC}"
    pip install -q waitress
fi

# Check port availability
check_port() {
    local port=$1
    if ss -tln | grep -q ":$port "; then
        return 1
    fi
    return 0
}

# Check API port
if ! check_port $API_PORT; then
    echo -e "${RED}Error: Port $API_PORT already in use${NC}"
    echo -e "Process using port:"
    ss -tlnp | grep ":$API_PORT" || lsof -i:$API_PORT 2>/dev/null || true
    exit 1
fi

# Check web port
if ! check_port $WEB_PORT; then
    echo -e "${RED}Error: Port $WEB_PORT already in use${NC}"
    echo -e "Process using port:"
    ss -tlnp | grep ":$WEB_PORT" || lsof -i:$WEB_PORT 2>/dev/null || true
    exit 1
fi

# Check HTTP redirect port
if ! check_port $HTTP_PORT; then
    echo -e "${YELLOW}Warning: Port $HTTP_PORT already in use (HTTP redirect will be disabled)${NC}"
    HTTP_REDIRECT_ENABLED=false
else
    HTTP_REDIRECT_ENABLED=true
fi

# Start API server with waitress
echo -e "${GREEN}Starting API server (waitress)...${NC}"
cd backend
AUDIOBOOKS_USE_WAITRESS=true AUDIOBOOKS_BIND_ADDRESS=127.0.0.1 python api.py > /dev/null 2>&1 &
API_PID=$!
echo $API_PID > "$API_PID_FILE"
cd ..

echo -e "  ${GREEN}✓${NC} API server started (PID: $API_PID)"

# Wait for API to be ready
echo -n "Waiting for API to be ready"
for i in {1..15}; do
    if curl -s http://localhost:$API_PORT/api/stats > /dev/null 2>&1; then
        echo -e " ${GREEN}✓${NC}"
        break
    fi
    if [ $i -eq 15 ]; then
        echo -e " ${RED}✗${NC}"
        echo -e "${RED}Error: API failed to start${NC}"
        cleanup_processes
        exit 1
    fi
    echo -n "."
    sleep 1
done

# Start HTTPS reverse proxy
echo -e "${GREEN}Starting HTTPS proxy...${NC}"
cd web-v2
python3 proxy_server.py > /dev/null 2>&1 &
PROXY_PID=$!
echo $PROXY_PID > "$PROXY_PID_FILE"
cd ..

echo -e "  ${GREEN}✓${NC} HTTPS proxy started (PID: $PROXY_PID)"
sleep 2

# Verify proxy is running
if ! kill -0 $PROXY_PID 2>/dev/null; then
    echo -e "${RED}Error: HTTPS proxy failed to start${NC}"
    cleanup_processes
    exit 1
fi

# Start HTTP redirect (if port available)
if [ "$HTTP_REDIRECT_ENABLED" = true ]; then
    echo -e "${GREEN}Starting HTTP redirect...${NC}"
    cd web-v2
    python3 redirect_server.py > /dev/null 2>&1 &
    REDIRECT_PID=$!
    echo $REDIRECT_PID > "$REDIRECT_PID_FILE"
    cd ..

    echo -e "  ${GREEN}✓${NC} HTTP redirect started (PID: $REDIRECT_PID)"
fi

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Library is now running!${NC}"
echo ""
echo -e "Access the library at:"
echo -e "  ${BLUE}https://localhost:$WEB_PORT${NC} (HTTPS - recommended)"
if [ "$HTTP_REDIRECT_ENABLED" = true ]; then
    echo -e "  ${BLUE}http://localhost:$HTTP_PORT${NC} (redirects to HTTPS)"
fi
echo ""
echo -e "API Server:  ${BLUE}http://localhost:$API_PORT${NC} (internal)"
echo ""
echo -e "Process IDs:"
echo -e "  API:      $API_PID"
echo -e "  Proxy:    $PROXY_PID"
if [ "$HTTP_REDIRECT_ENABLED" = true ]; then
    echo -e "  Redirect: $REDIRECT_PID"
fi
echo ""
echo -e "${YELLOW}Opening browser...${NC}"
echo -e "${BLUE}========================================${NC}"

# Wait a moment for server to fully start
sleep 2

# Open in browser
if command -v opera &> /dev/null; then
    opera "https://localhost:$WEB_PORT" &> /dev/null &
elif command -v xdg-open &> /dev/null; then
    xdg-open "https://localhost:$WEB_PORT" &> /dev/null &
fi

echo ""
echo -e "${GREEN}Library is ready!${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop the servers"
echo ""

# Supervision loop - wait for any process to exit
while true; do
    # Check if API is still running
    if ! kill -0 $API_PID 2>/dev/null; then
        echo -e "${RED}API server died unexpectedly${NC}"
        cleanup_processes
        exit 1
    fi

    # Check if proxy is still running
    if ! kill -0 $PROXY_PID 2>/dev/null; then
        echo -e "${RED}HTTPS proxy died unexpectedly${NC}"
        cleanup_processes
        exit 1
    fi

    # Check if redirect is still running (if enabled)
    if [ "$HTTP_REDIRECT_ENABLED" = true ] && [ -n "$REDIRECT_PID" ]; then
        if ! kill -0 $REDIRECT_PID 2>/dev/null; then
            echo -e "${YELLOW}HTTP redirect server died (not critical)${NC}"
            rm -f "$REDIRECT_PID_FILE"
            unset REDIRECT_PID
        fi
    fi

    sleep 5
done
