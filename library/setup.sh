#!/bin/bash
# Audiobook Library Setup Script

echo "========================================="
echo "  Audiobook Library Setup"
echo "========================================="
echo ""

# Check for required tools
echo "Checking dependencies..."

if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed"
    exit 1
fi
echo "✓ Python 3 found"

if ! command -v ffprobe &> /dev/null; then
    echo "❌ Error: ffprobe is not installed"
    echo "   Install with: sudo pacman -S ffmpeg"
    exit 1
fi
echo "✓ ffprobe found"

if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  Warning: ffmpeg not found - cover art extraction will fail"
    echo "   Install with: sudo pacman -S ffmpeg"
else
    echo "✓ ffmpeg found"
fi

echo ""
echo "Dependencies OK!"
echo ""

# Check if audiobooks directory exists
if [ ! -d "/raid0/Audiobooks" ]; then
    echo "❌ Error: /raid0/Audiobooks directory not found"
    exit 1
fi
echo "✓ Audiobooks directory found"

# Count audiobooks
AUDIOBOOK_COUNT=$(find /raid0/Audiobooks -type f -name "*.m4b" | wc -l)
echo "✓ Found $AUDIOBOOK_COUNT audiobook files"
echo ""

# Ask user if they want to scan now
echo "========================================="
echo ""
echo "Ready to scan your audiobook collection!"
echo ""
echo "This will:"
echo "  - Extract metadata from $AUDIOBOOK_COUNT audiobooks"
echo "  - Generate cover art images"
echo "  - Create searchable database"
echo ""
echo "Estimated time: 30-60 minutes"
echo ""
read -p "Start scanning now? (y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Starting scan..."
    echo "========================================="
    cd scanner
    python3 scan_audiobooks.py

    if [ $? -eq 0 ]; then
        echo ""
        echo "========================================="
        echo "✓ Scan completed successfully!"
        echo ""
        echo "To launch the library:"
        echo "  cd web"
        echo "  python3 -m http.server 8080"
        echo ""
        echo "Then open: http://localhost:8080"
        echo "========================================="
    else
        echo ""
        echo "❌ Scan failed. Please check errors above."
        exit 1
    fi
else
    echo ""
    echo "Scan cancelled. You can run it manually later:"
    echo "  cd scanner"
    echo "  python3 scan_audiobooks.py"
    echo ""
fi
