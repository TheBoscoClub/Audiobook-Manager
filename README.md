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

## Quick Start

### Convert Audiobooks
```bash
# Basic conversion to MP3
./converter/AAXtoMP3 -A <AUTHCODE> input.aax

# Convert to M4B audiobook format
./converter/AAXtoMP3 -e:m4b -A <AUTHCODE> input.aax

# Interactive mode
./converter/interactiveAAXtoMP3
```

### Browse Library
```bash
# Launch the web interface
./launch.sh

# Or manually:
cd library
source venv/bin/activate
python3 backend/api.py &
python3 -m http.server 8090 --directory web-v2
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

## Directory Structure

```
Audiobooks/
├── converter/           # AAXtoMP3 conversion tools
│   ├── AAXtoMP3        # Main conversion script
│   └── interactiveAAXtoMP3
├── library/            # Web library interface
│   ├── backend/        # Flask API + SQLite database
│   ├── scanner/        # Metadata extraction
│   ├── scripts/        # Hash generation, duplicate detection
│   ├── web-v2/         # Modern web interface
│   └── web/            # Legacy interface + cover storage
├── launch.sh           # Quick launcher
└── README.md
```

## Docker (macOS, Windows, Linux)

Run the library in Docker for easy cross-platform deployment:

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

## Data Source

Default audiobook directory: `/raid0/Audiobooks`

Configure in `library/scanner/scan_audiobooks.py`:
```python
AUDIOBOOK_DIR = Path("/your/path/to/audiobooks")
```

## License

See individual component licenses in `converter/LICENSE` and `library/` files.
