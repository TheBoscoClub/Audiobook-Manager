#!/bin/bash
# Audiobook Library Launcher
# Starts the Flask API and web server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration
source "$SCRIPT_DIR/lib/audiobooks-config.sh"

# Legacy variable mapping for this script
PROJECT_DIR="$AUDIOBOOKS_HOME"
LIBRARY_DIR="$AUDIOBOOKS_HOME/library"
WEB_PORT="$AUDIOBOOKS_WEB_PORT"
API_PORT="$AUDIOBOOKS_API_PORT"

echo "=========================================="
echo "  Audiobook Library"
echo "=========================================="

# Check for database
if [ ! -f "$LIBRARY_DIR/backend/audiobooks.db" ]; then
    echo "Database not found. Please run the scanner first:"
    echo "  cd $LIBRARY_DIR/scanner && python3 scan_audiobooks.py"
    echo "  cd $LIBRARY_DIR/backend && python3 import_to_db.py"
    exit 1
fi

# Activate virtual environment
if [ -d "$LIBRARY_DIR/venv" ]; then
    source "$LIBRARY_DIR/venv/bin/activate"
fi

# Install requirements if needed
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install -r "$LIBRARY_DIR/requirements.txt"
fi

# Kill any existing processes
pkill -f "python3.*api.py" 2>/dev/null
pkill -f "python3.*http.server" 2>/dev/null

# Find available port for web server
while lsof -i:$WEB_PORT >/dev/null 2>&1; do
    WEB_PORT=$((WEB_PORT + 1))
    if [ $WEB_PORT -gt 8099 ]; then
        echo "No available ports in range 8090-8099"
        exit 1
    fi
done

# Find available port for API
while lsof -i:$API_PORT >/dev/null 2>&1; do
    API_PORT=$((API_PORT + 1))
    if [ $API_PORT -gt 5010 ]; then
        echo "No available ports in range 5001-5010"
        exit 1
    fi
done

# Export ports for Python scripts
export API_PORT
export WEB_PORT

echo "Starting API server on port $API_PORT..."
cd "$LIBRARY_DIR/backend"
python3 api.py &
API_PID=$!

sleep 2

echo "Starting web server on port $WEB_PORT..."
cd "$LIBRARY_DIR/web-v2"
python3 -m http.server $WEB_PORT &
WEB_PID=$!

echo ""
echo "=========================================="
echo "  Library is running!"
echo "=========================================="
echo "  Web UI:  http://localhost:$WEB_PORT"
echo "  API:     http://localhost:$API_PORT"
echo ""
echo "  Press Ctrl+C to stop"
echo "=========================================="

# Open browser
if command -v xdg-open &> /dev/null; then
    sleep 1
    xdg-open "http://localhost:$WEB_PORT" 2>/dev/null &
fi

# Wait for Ctrl+C
trap "echo 'Shutting down...'; kill $API_PID $WEB_PID 2>/dev/null; exit 0" INT TERM

wait
