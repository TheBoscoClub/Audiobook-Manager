#!/usr/bin/env zsh
# =============================================================================
# Stop Audiobook-Manager Development Servers
# =============================================================================

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# PID files
PID_DIR="/tmp/audiobooks-dev"
API_PID_FILE="$PID_DIR/api.pid"
CADDY_PID_FILE="$PID_DIR/caddy.pid"

echo -e "${YELLOW}Stopping development servers...${NC}"

# Function to stop a process
stop_process() {
    local pid_file=$1
    local name=$2

    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  Stopping $name (PID: $pid)..."
            kill -TERM "$pid" 2>/dev/null || true

            # Wait up to 5 seconds
            for i in {1..5}; do
                if ! kill -0 "$pid" 2>/dev/null; then
                    echo -e "  ${GREEN}✓${NC} $name stopped"
                    break
                fi
                sleep 1
            done

            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                echo -e "  ${YELLOW}Force killing $name${NC}"
                kill -KILL "$pid" 2>/dev/null || true
            fi
        else
            echo -e "  $name not running (stale PID file)"
        fi
        rm -f "$pid_file"
    else
        echo -e "  $name not running (no PID file)"
    fi
}

stop_process "$CADDY_PID_FILE" "Caddy"
stop_process "$API_PID_FILE" "API server"

# Clean up PID directory if empty
rmdir "$PID_DIR" 2>/dev/null || true

echo -e "${GREEN}Development servers stopped${NC}"
