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

# Opens https://localhost:8090 in your browser
# HTTP requests to port 8081 are automatically redirected to HTTPS
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

## Installation

Run the interactive installer:
```bash
./install.sh
```

You'll be presented with a menu to choose:
- **System Installation** - Installs to `/usr/local/bin` and `/etc/audiobooks` (requires sudo)
- **User Installation** - Installs to `~/.local/bin` and `~/.config/audiobooks` (no root required)
- **Exit** - Exit without changes

### Command-Line Options
```bash
./install.sh --system              # Skip menu, system install
./install.sh --user                # Skip menu, user install
./install.sh --data-dir /path      # Specify data directory
./install.sh --uninstall           # Remove installation
./install.sh --no-services         # Skip systemd services
```

### Port Conflict Detection
The installer automatically checks if the required ports (5001, 8090, 8081) are available before installation. If a port is in use, you'll see options to:
1. Choose an alternate port
2. Continue anyway (if you plan to stop the conflicting service)
3. Abort installation

Both installation modes:
- Create configuration files
- Generate SSL certificates
- Install systemd services
- Set up Python virtual environment

After installation, use these commands:
```bash
audiobooks-api      # Start API server
audiobooks-web      # Start web server (HTTPS)
audiobooks-scan     # Scan audiobook library
audiobooks-import   # Import to database
audiobooks-config   # Show configuration
```

## Configuration

Configuration is loaded from multiple sources in priority order:
1. System config: `/etc/audiobooks/audiobooks.conf`
2. User config: `~/.config/audiobooks/audiobooks.conf`
3. Environment variables

### Configuration Variables

| Variable | Description |
|----------|-------------|
| `AUDIOBOOKS_DATA` | Root data directory |
| `AUDIOBOOKS_LIBRARY` | Converted audiobook files |
| `AUDIOBOOKS_SOURCES` | Source AAXC files |
| `AUDIOBOOKS_SUPPLEMENTS` | PDF supplements |
| `AUDIOBOOKS_HOME` | Application installation directory |
| `AUDIOBOOKS_DATABASE` | SQLite database path |
| `AUDIOBOOKS_COVERS` | Cover art cache |
| `AUDIOBOOKS_CERTS` | SSL certificate directory |
| `AUDIOBOOKS_LOGS` | Log files directory |
| `AUDIOBOOKS_API_PORT` | API server port (default: 5001) |
| `AUDIOBOOKS_WEB_PORT` | HTTPS web server port (default: 8090) |
| `AUDIOBOOKS_HTTP_REDIRECT_PORT` | HTTP→HTTPS redirect port (default: 8081) |
| `AUDIOBOOKS_HTTP_REDIRECT_ENABLED` | Enable HTTP redirect server (default: true) |

### Override via Environment
```bash
AUDIOBOOKS_LIBRARY=/mnt/nas/audiobooks ./launch.sh
```

### View Current Configuration
```bash
audiobooks-config
```

## Directory Structure

```
Audiobooks/
├── etc/
│   └── audiobooks.conf.example  # Config template
├── lib/
│   └── audiobooks-config.sh     # Config loader (shell)
├── install.sh                   # Unified installer (interactive)
├── install-user.sh              # User installation (standalone)
├── install-system.sh            # System installation (standalone)
├── install-services.sh          # Legacy service installer
├── launch.sh                    # Quick launcher
├── converter/                   # AAXtoMP3 conversion tools
│   ├── AAXtoMP3                 # Main conversion script
│   └── interactiveAAXtoMP3
├── library/                     # Web library interface
│   ├── config.py                # Python configuration module
│   ├── backend/                 # Flask API + SQLite database
│   ├── scanner/                 # Metadata extraction
│   ├── scripts/                 # Hash generation, duplicate detection
│   ├── web-v2/                  # Modern web interface
│   └── web/                     # Legacy interface + cover storage
├── Dockerfile                   # Docker build file
├── docker-compose.yml           # Docker Compose config
└── README.md
```

## Docker (macOS, Windows, Linux)

Run the library in Docker for easy cross-platform deployment. The Docker container automatically initializes the database on first run - just mount your audiobooks and start the container.

### Quick Start (Recommended)

```bash
# Pull and run with a single command
docker run -d \
  --name audiobooks \
  -p 8090:8090 \
  -p 5001:5001 \
  -v /path/to/your/audiobooks:/audiobooks:ro \
  -v audiobooks_data:/app/data \
  -v audiobooks_covers:/app/covers \
  ghcr.io/greogory/audiobook-toolkit:latest

# Access the web interface
open http://localhost:8090
```

On first run, the container automatically:
1. Detects mounted audiobooks
2. Scans and indexes your library
3. Imports metadata into the database
4. Starts the web and API servers

### Using Docker Compose

```bash
# Set your audiobooks directory
export AUDIOBOOK_DIR=/path/to/your/audiobooks

# Optional: Set supplements directory for PDFs
export SUPPLEMENTS_DIR=/path/to/supplements

# Build and run
docker-compose up -d

# Access the web interface
open http://localhost:8090
```

### Build Locally

```bash
# Build the image
docker build -t audiobooks .

# Run with your audiobook directory
docker run -d \
  --name audiobooks \
  -p 8090:8090 \
  -p 5001:5001 \
  -v /path/to/audiobooks:/audiobooks:ro \
  -v audiobooks_data:/app/data \
  -v audiobooks_covers:/app/covers \
  audiobooks
```

### Docker Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIOBOOK_DIR` | `/audiobooks` | Path to audiobooks inside container |
| `DATABASE_PATH` | `/app/data/audiobooks.db` | SQLite database path |
| `COVER_DIR` | `/app/covers` | Cover art cache directory |
| `SUPPLEMENTS_DIR` | `/supplements` | PDF supplements directory |
| `WEB_PORT` | `8090` | Web interface port |
| `API_PORT` | `5001` | REST API port |

### Docker Volumes

| Volume | Purpose |
|--------|---------|
| `audiobooks_data` | Persists SQLite database across container restarts |
| `audiobooks_covers` | Persists cover art cache |

### Manual Library Management

If you need to manually rescan or update your library:

```bash
# Rescan audiobook directory
docker exec -it audiobooks python3 /app/scanner/scan_audiobooks.py

# Re-import to database
docker exec -it audiobooks python3 /app/backend/import_to_db.py

# View README inside container
docker exec -it audiobooks cat /app/README.md
```

### Docker Health Check

The container includes a health check that verifies the API is responding:
```bash
# Check container health
docker inspect --format='{{.State.Health.Status}}' audiobooks
```

### Troubleshooting Docker

```bash
# View container logs
docker logs audiobooks

# Check running processes
docker exec -it audiobooks ps aux

# Access container shell
docker exec -it audiobooks /bin/bash

# Restart container (re-runs initialization)
docker restart audiobooks
```

## Requirements (native install)

- Python 3.8+
- ffmpeg 4.4+ (with ffprobe)
- Flask, flask-cors
- openssl (for SSL certificate generation)

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

## Systemd Services

### User Services
```bash
# Enable services at login
systemctl --user enable audiobooks-api audiobooks-web

# Start services
systemctl --user start audiobooks.target

# Check status
systemctl --user status audiobooks-api audiobooks-web

# View logs
journalctl --user -u audiobooks-api -f

# Enable lingering (services start at boot without login)
loginctl enable-linger $USER
```

### System Services
```bash
# Enable services at boot
sudo systemctl enable audiobooks-api audiobooks-web

# Start services
sudo systemctl start audiobooks.target

# Check status
systemctl status audiobooks-api audiobooks-web
```

## License

See individual component licenses in `converter/LICENSE` and `library/` files.
