#!/bin/bash
set -e

# Initialize database if it doesn't exist
if [ ! -f /app/data/audiobooks.db ]; then
    echo "Database not found. Please run the scanner first:"
    echo "  docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py"
    echo "  docker exec -it audiobooks python3 /app/backend/import_to_db.py"
    echo ""
    echo "Or mount an existing database to /app/data/audiobooks.db"
fi

# Update database path in API if needed
export DATABASE_PATH="${DATABASE_PATH:-/app/data/audiobooks.db}"

# Start Flask API in background
echo "Starting API server on port 5001..."
cd /app/backend
python3 api.py &
API_PID=$!

# Wait for API to start
sleep 2

# Start web server
echo "Starting web server on port 8090..."
cd /app/web
python3 -m http.server 8090 &
WEB_PID=$!

echo ""
echo "=========================================="
echo "  Audiobook Library is running!"
echo "=========================================="
echo "  Web UI:  http://localhost:8090"
echo "  API:     http://localhost:5001"
echo "=========================================="

# Handle shutdown
trap "echo 'Shutting down...'; kill $API_PID $WEB_PID 2>/dev/null; exit 0" SIGTERM SIGINT

# Wait for processes
wait
