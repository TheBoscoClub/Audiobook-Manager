# Audiobooks

A comprehensive audiobook management toolkit for converting Audible files and browsing your audiobook collection.

## Important: OGG/OPUS Format Only

**This project uses OGG/OPUS as the exclusive audio format.** While the included AAXtoMP3 converter supports other formats (MP3, M4A, M4B, FLAC), the library browser, web UI, Docker container, and all tooling are designed and tested **only with OGG/OPUS files**.

OPUS offers superior audio quality at lower bitrates compared to MP3, making it ideal for audiobooks. I chose this format for my personal library and have no plans to support other formats.

<details>
<summary>What would need to change for other formats?</summary>

- Scanner: Update file extension detection (`.opus` → `.mp3`, etc.)
- Database schema: Potentially add format-specific metadata fields
- Web UI: Update MIME types in audio player, file extension filters
- Cover art handling: Different embedding methods per format
- Docker entrypoint: Update file discovery patterns
- API: Modify file serving and content-type headers

Pull requests welcome if you need this functionality.
</details>

## Components

### 1. Converter (`converter/`)

This project includes a **personal fork of [AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3)** (v2.2) for converting Audible AAX/AAXC files to OGG/OPUS format. The original project by KrumpetPirate has been archived, and this fork includes essential fixes for modern AAXC file handling.

> **Note**: While AAXtoMP3 supports multiple output formats (MP3, M4A, M4B, FLAC, OPUS), this toolkit is configured exclusively for OPUS output. See the converter's [FORK_README.md](converter/FORK_README.md) for full documentation.

<details>
<summary>Fork modifications from original AAXtoMP3</summary>

**Bug Fixes:**
- Fixed `tmp_chapter_file: unbound variable` crash when chapter files are missing
- Fixed cover extraction for AAXC files (was using hardcoded `-activation_bytes` instead of `${decrypt_param}`)
- Made audible-cli chapter/cover files optional instead of required

**New Features:**
- **Opus cover art embedding** via Python mutagen library (FFmpeg cannot embed covers in OGG/Opus)
- Enhanced fallback handling - extracts metadata directly from AAXC when audible-cli files are missing
- Improved logging and user feedback during conversion

**Dependencies Added:**
- `mutagen` (optional) - Required for Opus cover art embedding

See [converter/CHANGELOG.md](converter/CHANGELOG.md) for version history.
</details>

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
# Convert to OPUS (recommended, default for this project)
./converter/AAXtoMP3 --opus --single --use-audible-cli-data input.aaxc

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

## Acknowledgments

This project would not be possible without the incredible work of many developers and open-source communities. I am deeply grateful to:

### Core Dependencies

- **[KrumpetPirate](https://github.com/KrumpetPirate)** and the **55+ contributors** to [AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3) - The foundation of the converter component. Years of community effort went into building this essential tool for the audiobook community.

- **[mkb79](https://github.com/mkb79)** for [audible-cli](https://github.com/mkb79/audible-cli) - An indispensable CLI tool for interacting with Audible's API, downloading books, and extracting metadata. This project relies heavily on audible-cli for AAXC decryption and metadata.

- **[FFmpeg](https://ffmpeg.org/)** - The Swiss Army knife of multimedia processing. FFmpeg handles all audio conversion, metadata extraction, and stream processing in this project.

- **[Flask](https://flask.palletsprojects.com/)** by the Pallets Projects team - The lightweight Python web framework powering the REST API.

- **[SQLite](https://sqlite.org/)** - The embedded database engine that stores and indexes the audiobook library with remarkable efficiency.

- **[mutagen](https://mutagen.readthedocs.io/)** - Python library for handling audio metadata, essential for embedding cover art in Opus files.

### Development Tools

- **[Claude Code](https://claude.ai/code)** (Anthropic) - AI coding assistant that helped with implementation details, debugging, and documentation throughout development.

- **[CachyOS](https://cachyos.org/)** - The Arch-based Linux distribution where this project was developed and tested. CachyOS provides an excellent development environment with up-to-date packages and performance optimizations.

### The Audiobook Community

Special thanks to the broader audiobook and self-hosting communities on Reddit ([r/audiobooks](https://www.reddit.com/r/audiobooks/), [r/selfhosted](https://www.reddit.com/r/selfhosted/)) and various forums for sharing knowledge, workarounds, and inspiration for managing personal audiobook libraries.

---

*This project is a personal tool shared in the hope that others might find it useful. All credit for the underlying technologies belongs to their respective creators and communities.*

## License

See individual component licenses in `converter/LICENSE` and `library/` files.
