# Audiobooks

A comprehensive audiobook management toolkit for converting Audible files and browsing your audiobook collection.

## Components

### 1. Converter (`converter/`)
AAXtoMP3 - Convert Audible AAX/AAXC files to common audio formats (MP3, M4A, M4B, FLAC, OPUS).

### 2. Library (`library/`)
Web-based audiobook library browser with:
- Vintage library-themed interface
- Built-in audio player
- Full-text search
- SHA-256 hash-based duplicate detection
- Cover art display
- PDF supplement support (course materials, maps, etc.)

## Quick Start

### Browse Library
```bash
# Launch the web interface
./launch.sh

# Opens http://localhost:8090 in your browser
```

### Convert Audiobooks
```bash
# Basic conversion to MP3
./converter/AAXtoMP3 -A <AUTHCODE> input.aax

# Convert to M4B audiobook format
./converter/AAXtoMP3 -e:m4b -A <AUTHCODE> input.aax

# Interactive mode
./converter/interactiveAAXtoMP3
```

### Scan New Audiobooks
```bash
cd library/scanner
python3 scan_audiobooks.py

cd ../backend
python3 import_to_db.py
```

### Manage Duplicates
```bash
cd library

# Generate file hashes
python3 scripts/generate_hashes.py

# Find duplicates
python3 scripts/find_duplicates.py

# Remove duplicates (dry run)
python3 scripts/find_duplicates.py --remove

# Remove duplicates (execute)
python3 scripts/find_duplicates.py --execute
```

### Manage Supplements
Some Audible audiobooks include supplemental PDFs (course materials, maps, reference guides).
```bash
# Scan supplements directory and link to audiobooks
cd library/scripts
python3 scan_supplements.py --supplements-dir /path/to/supplements

# In Docker, supplements are scanned automatically on startup
```
Books with supplements show a red "PDF" badge in the UI. Click to download.

## Configuration

All paths are configured in `config.env`. Edit this file to customize your installation:

```bash
# config.env - Main configuration file

# Path to your audiobook collection
AUDIOBOOK_DIR="/path/to/your/audiobooks"

# Path where this project is installed (usually auto-detected)
PROJECT_DIR="/path/to/Audiobooks"

# Server ports
WEB_PORT=8090
API_PORT=5001
```

### Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOK_DIR` | `/raid0/Audiobooks` | Where your audiobook files are stored |
| `PROJECT_DIR` | Auto-detected | Installation directory of this project |
| `DATABASE_PATH` | `${PROJECT_DIR}/library/backend/audiobooks.db` | SQLite database location |
| `COVER_DIR` | `${PROJECT_DIR}/library/web/covers` | Cover art cache |
| `DATA_DIR` | `${PROJECT_DIR}/library/data` | JSON data directory |
| `SUPPLEMENTS_DIR` | `/raid0/Audiobooks/Supplements` | PDF supplements directory |
| `WEB_PORT` | `8090` | Web interface port |
| `API_PORT` | `5001` | REST API port |

### Environment Variables

You can also override any setting via environment variables:
```bash
AUDIOBOOK_DIR=/mnt/nas/audiobooks ./launch.sh
```

## Directory Structure

```
Audiobooks/
├── config.env           # Main configuration file
├── launch.sh            # Quick launcher
├── converter/           # AAXtoMP3 conversion tools
│   ├── AAXtoMP3         # Main conversion script
│   └── interactiveAAXtoMP3
├── library/             # Web library interface
│   ├── config.py        # Python configuration module
│   ├── backend/         # Flask API + SQLite database
│   ├── scanner/         # Metadata extraction
│   ├── scripts/         # Hash generation, duplicate detection
│   ├── web-v2/          # Modern web interface
│   └── web/             # Legacy interface + cover storage
├── Dockerfile           # Docker build file
├── docker-compose.yml   # Docker Compose config
└── README.md
```

## Docker (macOS, Windows, Linux)

Run the library in Docker for easy cross-platform deployment.

### Option 1: Pull from GitHub Container Registry (Recommended)

```bash
# Pull the latest image
docker pull ghcr.io/greogory/audiobook-toolkit:latest

# Or pull a specific version
docker pull ghcr.io/greogory/audiobook-toolkit:2.3

# Run with your audiobook directory
docker run -d \
  --name audiobooks \
  -p 8090:8090 \
  -p 5001:5001 \
  -v /path/to/your/audiobooks:/audiobooks:ro \
  -v /path/to/supplements:/supplements:ro \
  -v audiobooks_data:/app/data \
  -v audiobooks_covers:/app/covers \
  ghcr.io/greogory/audiobook-toolkit:latest

# Access the web interface
open http://localhost:8090
```

### Option 2: Build Locally with Docker Compose

```bash
# Set your audiobooks directory
export AUDIOBOOK_DIR=/path/to/your/audiobooks

# Build and run
docker-compose up -d

# Access the web interface
open http://localhost:8090
```

### First-time setup (scan audiobooks)
```bash
# Scan your audiobook directory
docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py

# Import to database
docker exec -it audiobooks python3 /app/backend/import_to_db.py
```

### Docker volumes
- `audiobooks_data`: Persists the SQLite database
- `audiobooks_covers`: Persists cover art cache

## Requirements (native install)

- Python 3.8+
- ffmpeg 4.4+ (with ffprobe)
- Flask, flask-cors

### First-time setup
```bash
# Create virtual environment and install dependencies
cd library
python3 -m venv venv
source venv/bin/activate
pip install flask flask-cors

# Scan your audiobooks
cd scanner
python3 scan_audiobooks.py

# Import to database
cd ../backend
python3 import_to_db.py
```

## Local Audiobook Directory Structure

The audiobook files on this system are organized as follows:

```
/raid0/Audiobooks/
├── Sources/           # Original AAXC files from Audible
├── Library/           # Converted OPUS files (organized by Author/Title)
├── scripts/           # Conversion scripts (master copies)
├── systemd/           # Systemd service files
├── logs/              # Conversion logs
└── README_CONVERSION.md  # Detailed conversion documentation
```

For more information about the automated conversion system, see `/raid0/Audiobooks/README_CONVERSION.md`.

### Conversion Commands (available in PATH)
```bash
audiobook-status    # Check conversion status
audiobook-help      # Full command reference
audiobook-start     # Start conversion services
audiobook-stop      # Stop conversion services
```

## License

See individual component licenses in `converter/LICENSE` and `library/` files.
