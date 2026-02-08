#!/usr/bin/env zsh
# Quick launcher for Audiobook Library

set -e  # Exit on error

# Debug mode (set to 1 to enable debugging)
DEBUG=${DEBUG:-0}

if [ "$DEBUG" -eq 1 ]; then
    set -x  # Print commands as they execute
fi

echo "========================================="
echo "  Audiobook Library"
echo "========================================="
echo ""

# Get the directory where this script is located
SCRIPT_DIR="${0:A:h}"
echo "[DEBUG] Script directory: $SCRIPT_DIR"

# Change to project directory
cd "$SCRIPT_DIR" || {
    echo "❌ Error: Cannot change to script directory: $SCRIPT_DIR"
    exit 1
}

echo "[DEBUG] Current directory: $(pwd)"
echo "[DEBUG] Checking for data file: $SCRIPT_DIR/data/audiobooks.json"

# Check if data exists
if [ ! -f "$SCRIPT_DIR/data/audiobooks.json" ]; then
    echo "⚠️  No audiobook data found!"
    echo ""
    echo "Expected location: $SCRIPT_DIR/data/audiobooks.json"
    echo ""
    echo "You need to scan your audiobook collection first:"
    echo "  cd $SCRIPT_DIR/scanner"
    echo "  python3 scan_audiobooks.py"
    echo ""
    echo "Or run the setup script:"
    echo "  cd $SCRIPT_DIR"
    echo "  ./setup.sh"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "[DEBUG] Data file found!"

# Count audiobooks
BOOK_COUNT=$(python3 -c "import json; data=json.load(open('$SCRIPT_DIR/data/audiobooks.json')); print(data['total_audiobooks'])" 2>&1) || {
    echo "❌ Error reading audiobook data"
    echo "Error: $BOOK_COUNT"
    read -p "Press Enter to exit..."
    exit 1
}

echo "✓ Library contains $BOOK_COUNT audiobooks"
echo ""

# Find available port (try 8090 first, then 8091-8099)
PORT=8090
MAX_PORT=8099

echo "[DEBUG] Looking for available port..."
while [ $PORT -le $MAX_PORT ]; do
    if ! ss -tln | grep -q ":$PORT "; then
        echo "[DEBUG] Port $PORT is available"
        break
    fi
    echo "[DEBUG] Port $PORT is in use, trying next..."
    PORT=$((PORT + 1))
done

if [ $PORT -gt $MAX_PORT ]; then
    echo "❌ Error: No available ports found between 8090-8099"
    echo ""
    echo "Please close some applications and try again."
    read -p "Press Enter to exit..."
    exit 1
fi

URL="http://localhost:$PORT"
echo "Starting web server on port $PORT..."
echo "[DEBUG] Serving from project directory (so web/ and data/ are accessible)"

# Stay in project root directory to serve both web/ and data/
# This allows the browser to access:
#   - http://localhost:PORT/web/index.html
#   - http://localhost:PORT/data/audiobooks.json

# Start server in background
echo "[DEBUG] Starting Python HTTP server on port $PORT..."
python3 -m http.server $PORT > /tmp/audiobook-library-server.log 2>&1 &
SERVER_PID=$!

echo "[DEBUG] Server PID: $SERVER_PID"

# Check if server started successfully
sleep 1
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "❌ Error: Server failed to start"
    echo "Log output:"
    cat /tmp/audiobook-library-server.log
    read -p "Press Enter to exit..."
    exit 1
fi

echo "[DEBUG] Server started successfully"

# Wait a bit more for server to be ready
sleep 1

# Update URL to point to the web interface
URL="http://localhost:$PORT/web/"

# Verify server is responding
if curl -s "$URL" >/dev/null 2>&1; then
    echo "[DEBUG] Server is responding to requests"
else
    echo "⚠️  Warning: Server may not be ready yet"
fi

# Open Opera browser
echo "Opening browser to $URL..."
BROWSER_OPENED=0

if command -v opera &> /dev/null; then
    echo "[DEBUG] Found opera, launching..."
    opera "$URL" &> /dev/null &
    BROWSER_OPENED=1
elif command -v opera-developer &> /dev/null; then
    echo "[DEBUG] Found opera-developer, launching..."
    opera-developer "$URL" &> /dev/null &
    BROWSER_OPENED=1
elif command -v opera-beta &> /dev/null; then
    echo "[DEBUG] Found opera-beta, launching..."
    opera-beta "$URL" &> /dev/null &
    BROWSER_OPENED=1
else
    echo "[DEBUG] Opera not found, using default browser..."
    xdg-open "$URL" &> /dev/null &
    BROWSER_OPENED=1
fi

if [ "$BROWSER_OPENED" -eq 1 ]; then
    echo "✓ Browser opened"
fi

echo ""
echo "========================================="
echo "✓ Server running at $URL"
echo ""
echo "Server log: /tmp/audiobook-library-server.log"
echo ""
echo "Press Ctrl+C to stop the server"
echo "========================================="
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "Shutting down server..."
    kill $SERVER_PID 2>/dev/null
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for server process
wait $SERVER_PID
