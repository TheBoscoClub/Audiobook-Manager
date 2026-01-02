# Audiobook-Manager

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
- Built-in audio player with playback position saving
- Resume from last position
- Full-text search across titles, authors, and narrators
- **Author/Narrator autocomplete** with letter group filters (A-E, F-J, K-O, P-T, U-Z)
- **Collections sidebar** for browsing by category (Fiction, Nonfiction, Mystery, Sci-Fi, etc.)
- **Comprehensive sorting**: title, author/narrator first/last name, duration, publish date, acquired date, series with sequence, edition
- **Smart duplicate detection** by title/author/narrator or SHA-256 hash
- Cover art display with automatic extraction
- PDF supplement support (course materials, maps, etc.)
- **Genre sync** from Audible library export with 250+ genre categories
- **Narrator metadata sync** from Audible library export
- Production-ready HTTPS server with reverse proxy

## Quick Start

### Browse Library
```bash
# Launch the web interface (production mode)
cd library
./launch-v3.sh

# Opens https://localhost:8443 in your browser
# HTTP requests to port 8081 are automatically redirected to HTTPS
# Uses Waitress WSGI server for production-ready performance

# Or use legacy launcher (development mode)
./launch-v2.sh  # Opens http://localhost:8090
```

**Note**: Your browser will show a security warning (self-signed certificate). Click "Advanced" → "Proceed to localhost" to continue.

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

# Generate file hashes (sequential)
python3 scripts/generate_hashes.py

# Generate hashes in parallel (uses all CPU cores)
python3 scripts/generate_hashes.py --parallel

# Generate with specific worker count
python3 scripts/generate_hashes.py --parallel 8

# View hash statistics
python3 scripts/generate_hashes.py --stats

# Verify random sample of hashes
python3 scripts/generate_hashes.py --verify 20

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

### Update Narrator Metadata
Narrator information is often missing from converted audio files. Sync from your Audible library:
```bash
# Export your Audible library metadata (requires audible-cli authentication)
audible library export -f json -o /path/to/Audiobooks/library_metadata.json

# Update database with narrator information (dry run first)
cd library/scripts
python3 update_narrators_from_audible.py

# Apply changes
python3 update_narrators_from_audible.py --execute
```

### Populate Genres
Genre information enables the Collections sidebar for browsing by category. Sync genres from your Audible library export:
```bash
# Export your Audible library metadata (if not already done)
audible library export -f json -o /path/to/Audiobooks/library_metadata.json

# Preview genre matches (dry run)
cd library/scripts
python3 populate_genres.py

# Apply changes
python3 populate_genres.py --execute
```
The script matches books by ASIN, exact title, or fuzzy title matching (85% threshold). This populates the genres table and enables collection-based filtering in the web UI.

### Multi-Source Audiobooks (Experimental - Disabled by Default)

> **⚠️ EXPERIMENTAL / NOT FULLY TESTED - USE AT YOUR OWN RISK**
>
> Multi-source audiobook support (Google Play, Chirp, Librivox, etc.) is **disabled by default**. The only fully tested and verified format is **Audible's AAXC**.
>
> **Known Issues with non-AAXC formats:**
> - Metadata extraction may be incomplete or incorrect
> - Chapter detection/ordering may fail for some sources
> - Cover art extraction is unreliable for many formats
> - Multi-reader audiobooks (e.g., Librivox) may not be handled correctly
>
> The `audiobooks-multiformat` service and related scripts are disabled. To enable at your own risk, uncomment the watch directories in `watch-multiformat-sources.sh`.
>
> PRs welcome if you want to improve multi-source support.
> See: [Roadmap Discussion](https://github.com/greogory/Audiobook-Manager/discussions/2)

<details>
<summary>Multi-source scripts (click to expand)</summary>

Import audiobooks from sources beyond Audible (Google Play, Librivox, Chirp, etc.):

```bash
# Process Google Play audiobook (ZIP or M4A files)
cd library/scripts
python3 google_play_processor.py /path/to/audiobook.zip --import-db --execute

# Process directory of MP3/M4A chapter files
python3 google_play_processor.py /path/to/chapters/ --import-db --execute

# Enrich metadata from OpenLibrary API
python3 populate_from_openlibrary.py --execute

# Download free audiobooks from Librivox
python3 librivox_downloader.py --search "pride and prejudice"
python3 librivox_downloader.py --id 12345  # Download by Librivox ID
```

The Google Play processor:
- Accepts ZIP files, directories of chapters, or single audio files (MP3/M4A/M4B)
- Merges chapters into a single OPUS file at 64kbps (optimal for speech)
- Extracts and embeds cover art
- Enriches metadata from OpenLibrary (title, author, subjects)
- Calculates SHA-256 hash automatically
- Imports directly to database with `--import-db`

</details>

### Populate Sort Fields
Extract author/narrator names and series info for enhanced sorting:
```bash
cd library/scripts

# Preview changes
python3 populate_sort_fields.py

# Apply changes
python3 populate_sort_fields.py --execute
```
This extracts:
- Author first/last name from full name (handles "J.R.R. Tolkien", "John le Carré", etc.)
- Narrator first/last name
- Series sequence numbers from titles ("Book 1", "#2", "Part 3", Roman numerals)
- Edition information ("20th Anniversary Edition", "Unabridged", etc.)
- Acquired date from file modification time

## Installation

### Quick Install (From GitHub Releases)

Install the latest release without cloning the repository:

```bash
# One-line installer
curl -sSL https://github.com/greogory/Audiobook-Manager/raw/main/bootstrap-install.sh | bash

# Or download and install manually
wget https://github.com/greogory/Audiobook-Manager/releases/latest/download/audiobooks-*.tar.gz
tar -xzf audiobooks-*.tar.gz
cd audiobooks-*
./install.sh
```

### From Source

Clone the repository and run the interactive installer:

```bash
git clone https://github.com/greogory/Audiobook-Manager.git
cd Audiobook-Manager
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
The installer automatically checks if the required ports (5001, 8443, 8080) are available before installation. If a port is in use, you'll see options to:
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

## Upgrading

### Docker

Docker installations upgrade by pulling a new image:

```bash
# Pull latest image and recreate container
docker-compose pull
docker-compose up -d

# Or with docker directly
docker pull greogory/Audiobook-Manager:latest
docker stop audiobooks && docker rm audiobooks
docker run -d --name audiobooks ... greogory/Audiobook-Manager:latest

# Check running version
docker exec audiobooks cat /app/VERSION
```

Your data persists in mounted volumes (`/audiobooks`, `/app/data`).

### Standalone Installation (From GitHub)

Upgrade your installation directly from GitHub releases:

```bash
# Upgrade to latest version
audiobooks-upgrade

# Upgrade to specific version
audiobooks-upgrade --version 3.2.0

# Check for updates without installing
audiobooks-upgrade --check
```

### From Local Project

If you have the repository cloned locally:

```bash
# From within the project directory
./upgrade.sh --target /opt/audiobooks

# Or specify both source and target
./upgrade.sh --from-project /path/to/repo --target /opt/audiobooks
```

### API Architecture Migration

Switch between monolithic and modular Flask architectures:

```bash
# Check current architecture
audiobooks-migrate --check

# Switch to modular (Flask Blueprints)
audiobooks-migrate --to modular

# Switch to monolithic (single file)
audiobooks-migrate --to monolithic
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
| `AUDIOBOOKS_WEB_PORT` | HTTPS web server port (default: 8443) |
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
│   ├── backend/
│   │   ├── api_server.py        # Flask server launcher
│   │   ├── api_modular/         # Modular Flask Blueprints
│   │   │   ├── __init__.py
│   │   │   ├── audiobooks.py    # Audiobook endpoints
│   │   │   ├── metadata.py      # Metadata endpoints
│   │   │   ├── search.py        # Search endpoints
│   │   │   ├── stats.py         # Statistics endpoints
│   │   │   ├── operations.py    # Background operations
│   │   │   └── utilities.py     # Utility endpoints
│   │   ├── import_to_db.py      # Database importer
│   │   ├── schema.sql           # Database schema
│   │   └── operation_status.py  # Operation tracking
│   ├── scanner/
│   │   └── scan_audiobooks.py   # Metadata extraction from audio files
│   ├── scripts/
│   │   ├── generate_hashes.py           # SHA-256 hash generation (parallel)
│   │   ├── find_duplicates.py           # Duplicate detection & removal
│   │   ├── scan_supplements.py          # PDF supplement scanner
│   │   ├── populate_sort_fields.py      # Extract name/series/edition info
│   │   ├── populate_genres.py           # Sync genres from Audible export
│   │   ├── populate_from_openlibrary.py # Enrich from OpenLibrary API
│   │   ├── update_narrators_from_audible.py  # Sync narrator metadata
│   │   ├── google_play_processor.py     # Process multi-source audiobooks
│   │   ├── librivox_downloader.py       # Download free Librivox audiobooks
│   │   ├── cleanup_audiobook_duplicates.py   # Database cleanup
│   │   ├── fix_audiobook_authors.py     # Author metadata repair
│   │   └── utils/
│   │       └── openlibrary_client.py    # OpenLibrary API client
│   └── web-v2/
│       ├── index.html           # Main web interface
│       ├── js/library.js        # Frontend JavaScript
│       ├── css/library.css      # Vintage library styling
│       ├── proxy_server.py      # HTTPS reverse proxy
│       └── redirect_server.py   # HTTP→HTTPS redirect
├── Dockerfile                   # Docker build file
├── docker-compose.yml           # Docker Compose config
└── README.md
```

## Web Interface Features

### Collections Sidebar
Browse your library by curated categories:
- **Toggle button**: Click "Collections" in the results bar to open the sidebar
- **Categories**: Special (The Great Courses), Main Genres (Fiction, Nonfiction), Nonfiction (History, Science, Biography, Memoir), Subgenres (Mystery & Thriller, Science Fiction, Fantasy, Romance)
- **Active filter badge**: Shows current collection on toggle button
- **Close options**: × button, click overlay, or press Escape

### Search & Filtering
- **Full-text search**: Search across titles, authors, and narrators
- **Author filter**: Autocomplete dropdown with A-E, F-J, K-O, P-T, U-Z letter groups
- **Narrator filter**: Autocomplete dropdown with book counts and letter groups
- **Collection filter**: Browse by category via Collections sidebar
- **Clear button**: Reset all filters with one click

### Sorting Options
| Sort By | Description |
|---------|-------------|
| Title (A-Z/Z-A) | Alphabetical by title |
| Author Last Name | Sort by author's last name (Smith, King, etc.) |
| Author First Name | Sort by author's first name |
| Author Full Name | Sort by full author name as displayed |
| Narrator Last Name | Sort by narrator's last name |
| Narrator First Name | Sort by narrator's first name |
| Duration | Longest or shortest first |
| Recently Acquired | By file modification date |
| Newest/Oldest Published | By publication year |
| Series (A-Z with sequence) | Groups series together, ordered by book number |
| Edition | Sort by edition type |

### Duplicate Detection
Two modes available via "Find Duplicates" dropdown:
1. **Same Title/Author/Narrator**: Finds books with matching metadata (different files)
2. **Exact Match (SHA-256)**: Finds byte-identical files using cryptographic hashes

### Audio Player
- Play/pause with progress bar
- Skip forward/back 30 seconds
- Adjustable playback speed (0.5x - 2.5x)
- Volume control
- **Position saving**: Automatically saves playback position per book
- **Resume playback**: Click any book to resume from last position

## REST API

The library exposes a REST API on port 5001:

### Core Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audiobooks` | GET | List audiobooks with pagination, search, filtering, sorting |
| `/api/audiobooks/<id>` | GET | Get single audiobook details |
| `/api/audiobooks/<id>` | PUT | Update audiobook metadata |
| `/api/audiobooks/<id>` | DELETE | Delete audiobook from library |
| `/api/collections` | GET | List available collections with book counts |
| `/api/stats` | GET | Library statistics (counts, total hours) |
| `/api/filters` | GET | Available filter options (authors, narrators, genres) |
| `/api/narrator-counts` | GET | Narrator names with book counts |
| `/api/stream/<id>` | GET | Stream audio file (supports range requests) |
| `/covers/<filename>` | GET | Get cover art image |
| `/health` | GET | API health check |

### Duplicate Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/duplicates` | GET | List all duplicates |
| `/api/duplicates/by-title` | GET | Find duplicates by title/author/narrator |
| `/api/duplicates/delete` | POST | Delete duplicate files |
| `/api/duplicates/verify` | POST | Verify duplicate detection |
| `/api/hash-stats` | GET | Hash generation statistics |

### Supplements

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/supplements` | GET | List all supplements |
| `/api/supplements/stats` | GET | Supplement statistics |
| `/api/supplements/<id>/download` | GET | Download PDF supplement |
| `/api/supplements/scan` | POST | Scan for new supplements |

### Bulk Operations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audiobooks/bulk-update` | POST | Update multiple audiobooks |
| `/api/audiobooks/bulk-delete` | POST | Delete multiple audiobooks |
| `/api/audiobooks/missing-narrator` | GET | List books without narrator |
| `/api/audiobooks/missing-hash` | GET | List books without hash |

### Utilities (Back Office)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/utilities/add-new` | POST | Add new audiobooks (incremental scan) |
| `/api/utilities/rescan` | POST | Full library rescan |
| `/api/utilities/rescan-async` | POST | Async full library rescan |
| `/api/utilities/reimport` | POST | Reimport metadata to database |
| `/api/utilities/reimport-async` | POST | Async reimport metadata |
| `/api/utilities/generate-hashes` | POST | Generate SHA-256 hashes |
| `/api/utilities/generate-hashes-async` | POST | Async hash generation |
| `/api/utilities/vacuum` | POST | Vacuum database |
| `/api/utilities/export-db` | GET | Export SQLite database |
| `/api/utilities/export-json` | GET | Export as JSON |
| `/api/utilities/export-csv` | GET | Export as CSV |

### Operation Status (Long-running tasks)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/operations/status/<id>` | GET | Get operation status |
| `/api/operations/active` | GET | List active operations |
| `/api/operations/all` | GET | List all operations |
| `/api/operations/cancel/<id>` | POST | Cancel running operation |

### Query Parameters for `/api/audiobooks`
- `page` - Page number (default: 1)
- `per_page` - Items per page (default: 50, max: 200)
- `search` - Full-text search query
- `author` - Filter by author name
- `narrator` - Filter by narrator name
- `collection` - Filter by collection slug (e.g., `fiction`, `mystery-thriller`, `great-courses`)
- `sort` - Sort field (title, author, author_last, narrator_last, duration_hours, acquired_date, published_year, series, edition)
- `order` - Sort order (asc, desc)

## Database Schema

The SQLite database stores audiobook metadata with the following key fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | INTEGER | Primary key |
| `title` | TEXT | Audiobook title |
| `author` | TEXT | Full author name |
| `author_last_name` | TEXT | Extracted last name for sorting |
| `author_first_name` | TEXT | Extracted first name for sorting |
| `narrator` | TEXT | Full narrator name(s) |
| `narrator_last_name` | TEXT | Extracted last name for sorting |
| `narrator_first_name` | TEXT | Extracted first name for sorting |
| `series` | TEXT | Series name (if part of series) |
| `series_sequence` | REAL | Book number in series (e.g., 1.0, 2.5) |
| `edition` | TEXT | Edition info (e.g., "20th Anniversary Edition") |
| `duration_hours` | REAL | Duration in hours |
| `published_year` | INTEGER | Year of publication |
| `acquired_date` | TEXT | Date added to library (YYYY-MM-DD) |
| `file_path` | TEXT | Full path to audio file |
| `file_size_mb` | REAL | File size in megabytes |
| `sha256_hash` | TEXT | SHA-256 hash for duplicate detection |
| `cover_path` | TEXT | Path to extracted cover art |
| `asin` | TEXT | Amazon Standard Identification Number |
| `isbn` | TEXT | International Standard Book Number |
| `source` | TEXT | Audiobook source (audible, google_play, librivox, chirp, etc.) |

Additional tables: `supplements` (PDF attachments), `audiobook_genres`, `audiobook_topics`, `audiobook_eras`

## Docker (macOS, Windows, Linux)

Run the library in Docker for easy cross-platform deployment. The Docker container automatically initializes the database on first run - just mount your audiobooks and start the container.

### Quick Start (Recommended)

```bash
# Pull and run with a single command
docker run -d \
  --name audiobooks \
  -p 8443:8443 \
  -p 8080:8080 \
  -v /path/to/your/audiobooks:/audiobooks:ro \
  -v audiobooks_data:/app/data \
  -v audiobooks_covers:/app/covers \
  ghcr.io/greogory/Audiobook-Manager:latest

# Access the web interface
open https://localhost:8443
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
open https://localhost:8443
```

### Build Locally

```bash
# Build the image
docker build -t audiobooks .

# Run with your audiobook directory
docker run -d \
  --name audiobooks \
  -p 8443:8443 \
  -p 8080:8080 \
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
| `WEB_PORT` | `8443` | HTTPS web interface port |
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
- Flask (CORS handled natively since v3.2.0)
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

All services use the `audiobooks-*` naming convention for easy management.

### Core Services

| Service | Description | Type |
|---------|-------------|------|
| `audiobooks-api` | Flask REST API (Waitress) on localhost:5001 | always running |
| `audiobooks-proxy` | HTTPS reverse proxy on 0.0.0.0:8443 | always running |
| `audiobooks-converter` | AAXC → OPUS conversion | always running |
| `audiobooks-mover` | Move converted files from tmpfs to storage | always running |
| `audiobooks-downloader.timer` | Download new Audible audiobooks (every 4h) | timer |
| `audiobooks-library-update.timer` | Update database with new audiobooks | timer |
| `audiobooks-shutdown-saver` | Save staging files before shutdown | on shutdown |
| `audiobooks-conversion-trigger.path` | Watch for new downloads | path watcher |
| `audiobooks-database-trigger.path` | Watch for completed conversions | path watcher |

### Experimental Services (Disabled by Default)

| Service | Description |
|---------|-------------|
| `audiobooks-multiformat` | Non-AAXC format conversion (Google Play, Chirp, Librivox) |
| `audiobooks-librivox.timer` | Download from Librivox wishlist |

### System Services (Recommended)

System services run at boot without requiring login:

```bash
# Check all audiobooks services
sudo systemctl status 'audiobooks-*'

# Enable core services at boot
sudo systemctl enable audiobooks-api audiobooks-proxy audiobooks-redirect \
  audiobooks-converter audiobooks-mover audiobooks-downloader.timer

# Start services
sudo systemctl start audiobooks.target

# Check status
sudo systemctl status audiobooks-api audiobooks-proxy

# View logs
journalctl -u audiobooks-api -f
```

### Conversion Priority

The converter service runs with low CPU and I/O priority to avoid impacting interactive use:
- **CPU**: `nice -n 19` (lowest priority)
- **I/O**: `ionice -c 2 -n 7` (best-effort, lowest priority within class)

This ensures audiobook conversion happens in the background without affecting system responsiveness.

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

## Changelog

### v3.2.1 (Current)
- **Docker Build**: Added Docker build job to release workflow for automated container builds
- **Performance**: Increased default parallel conversion jobs from 8 to 12
- **Cleanup**: Removed redundant config fallbacks from scripts (single source of truth)

### v3.2.0
- **GitHub Releases**: Standalone installation via `bootstrap-install.sh`
- **Upgrade System**: GitHub-based upgrades with `audiobooks-upgrade --from-github`
- **Release Automation**: CI/CD workflow and release tarball builder
- **Repository Renamed**: `audiobook-toolkit` → `Audiobook-Manager`
- **Removed Flask-CORS**: CORS now handled natively by the application
- **Cleanup**: Removed legacy `api.py` (2,244 lines) and `web.legacy/` directory
- **Security**: Fixed SQL injection in `generate_hashes.py`, Flask blueprint registration

### v3.1.1
- **Fix**: RuntimeDirectoryMode changed from 0755 to 0775 for group write access

### v3.1.0
- **Install Manifest**: `install-manifest.json` for production validation
- **API Migration**: Tools for switching between monolithic and modular architectures
- **Modular API**: Flask Blueprint architecture (`api_modular/`)
- **Testing**: Fixed 7 hanging tests, resolved mock path issues
- **Quality**: Fixed 13 shellcheck warnings, 18 mypy type errors

### v3.0.5
- **Security**: SQL injection fix in genre queries, non-root Docker user
- **Docker**: Pinned base image to `python:3.11.11-slim`
- **Ports**: Standardized to 8443 (HTTPS), 8080 (HTTP redirect)
- **Documentation**: Added LICENSE, CONTRIBUTING.md, CHANGELOG.md

### v3.0.0
- **The Back Office**: New utilities page with vintage library back-office aesthetic
  - Database management: stats, vacuum, rescan, reimport, export (JSON/CSV/SQLite)
  - Metadata editing: search, view, and edit audiobook metadata
  - Duplicate management: find and remove duplicates by title/author or SHA-256 hash
  - Bulk operations: select multiple audiobooks, bulk update fields, bulk delete
- **API Enhancements**: PUT/DELETE endpoints for editing, storage size and database size in stats
- **Smart Author/Narrator Sorting**: Sort by last name, first name
  - Single author: "Stephen King" → sorts as "King, Stephen"
  - Co-authored: "Stephen King, Peter Straub" → appears in both K and S letter groups
  - Anthologies: "Gaiman (contributor), Martin (editor)" → sorts by editor (Martin)
  - Role suffixes stripped: "(editor)", "(translator)", "- editor" handled correctly
- **Proxy Server**: Added PUT/DELETE method support for utilities operations
- **Removed**: Find Duplicates dropdown from main Library page (moved to Back Office)

### v2.9
- **Metadata Preservation**: Import now preserves manually-populated narrator and genre data from Audible exports, preventing data loss on reimport
- **Improved Deduplication**: Scanner now intelligently deduplicates between main library and `/Library/Audiobook/` folder, preferring main library files while keeping unique entries
- **Security**: Updated flask-cors from 4.0.0 to 6.0.0 (fixes CVE-2024-6839, CVE-2024-6844, CVE-2024-6866)

### v2.8
- Multi-source audiobook support (Google Play, Librivox, OpenLibrary)
- Parallel SHA-256 hash generation (24x speedup on multi-core systems)
- Automatic hashing during import
- New `isbn` and `source` database fields

### v2.7
- Collections sidebar for browsing by category
- Genre sync from Audible library export

### v2.6
- Author/narrator autocomplete with letter group filters
- Enhanced sorting options (first/last name, series sequence, edition)
- Narrator metadata sync from Audible

### v2.5
- Docker auto-initialization
- Portable configuration system
- Production-ready HTTPS server with Waitress

See [GitHub Releases](https://github.com/greogory/Audiobook-Manager/releases) for full version history.

## Known Issues

| Issue | Workaround | Status |
|-------|------------|--------|
| Browser security warning for self-signed SSL cert | Click "Advanced" → "Proceed to localhost" | By design |
| Narrator/genre data must be re-synced after adding new books | Run `update_narrators_from_audible.py` and `populate_genres.py` after importing | Planned: Auto-sync on import |
| ~No UI for duplicate management~ | ~~Use CLI scripts~~ | ✅ Fixed in v3.0 (Back Office) |
| ~Limited metadata editing in webapp~ | ~~Edit database directly~~ | ✅ Fixed in v3.0 (Back Office) |

## Roadmap

### Next Phase: Secure by Design

The next major focus is hardening the application with security as a first-class design principle:

- **Certificate Authority Integration**: Support for Let's Encrypt and other trusted CAs (currently uses self-signed certificates)
- **Authentication & Authorization**: Optional user authentication for multi-user deployments
- **Audit Logging**: Track all operations for security compliance
- **Input Validation**: Comprehensive input sanitization across all endpoints
- **Secrets Management**: Secure credential storage for Audible API keys and service accounts
- **Container Hardening**: Read-only filesystems, non-root execution, minimal base images
- **Network Security**: Rate limiting, CORS policies, CSP headers

### Planned Features

#### Utilities Section (Web UI)
A new "Utilities" or "Library Management" section in the webapp for:

**Database Management**
- View database statistics (total books, storage, duplicates)
- Trigger full library rescan from web UI
- Rebuild search index
- Export/import database backups

**Duplicate Management**
- Visual duplicate finder with side-by-side comparison
- One-click duplicate removal (keep highest quality)
- Merge duplicate entries (combine metadata from multiple sources)

**Audiobook Management**
- Delete audiobooks from library (with file deletion option)
- Edit metadata directly in webapp (title, author, narrator, series)
- Bulk operations (delete selected, update metadata)
- Move/reorganize files within library structure

**Audible Integration**
- Sync library with Audible account (via audible-cli)
- Download missing audiobooks directly
- Remove audiobooks from Audible library (with confirmation)
- Auto-import new Audible purchases

**Import Tools**
- Drag-and-drop audiobook import
- Bulk conversion from AAX/AAXC
- Multi-source import wizard (Google Play, Librivox, manual)
- Metadata lookup and enrichment

#### Enhanced Player
- Chapter navigation
- Bookmarks and notes
- Sleep timer
- Queue/playlist management

#### Mobile Support
- Responsive design improvements
- Progressive Web App (PWA) support
- Offline playback caching

### Contributing

Feature requests and pull requests welcome! See the [GitHub Issues](https://github.com/greogory/Audiobook-Manager/issues) page.

## License

See individual component licenses in `converter/LICENSE` and `library/` files.
