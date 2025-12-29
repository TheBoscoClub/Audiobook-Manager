# Audiobook Library

A beautiful, old-fashioned library-themed web interface for browsing, searching, and playing your personal audiobook collection.

> **Note:** This library is designed and tested exclusively with OGG/OPUS audiobook files. See the main [README](../README.md) for details.

## Features

### Core Features
- **Vintage Library Aesthetic**: Relaxing, classic library appearance with rich wood tones and leather textures
- **Built-in Audio Player**: Play audiobooks directly from the web interface
  - Playback speed control (0.75x - 2.0x)
  - Skip forward/backward (30 seconds)
  - Volume control
  - Progress tracking
- **Advanced Search**: Full-text search powered by SQLite FTS5
- **Smart Pagination**: Efficient browsing with customizable results per page (25/50/100/200)
- **Multiple Filters**: Browse by:
  - Authors (524+)
  - Narrators (620+)
  - Sort by title, author, narrator, or duration
- **Cover Art Display**: Visual browsing with audiobook covers extracted from files
- **Metadata Rich**: Displays duration, narrator, series, topics, and more
- **Fully Local**: Runs entirely on your machine, no internet required

### Backend
- **SQLite Database**: Fast, indexed database with 2,700+ audiobooks
- **Flask REST API**: RESTful API with CORS support
- **Streaming Support**: Direct audiobook streaming with seek support

## Quick Start

For detailed installation instructions, see [INSTALL.md](INSTALL.md).

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/audiobook-library.git
cd audiobook-library

# 2. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure audiobook directory (via environment or config file)
# Default: /srv/audiobooks (system) or ~/Audiobooks (user)

# 4. Scan your collection
cd scanner && python3 scan_audiobooks.py

# 5. Import to database
cd ../backend && python3 import_to_db.py

# 6. Launch the application
cd .. && ./launch-v2.sh
```

The launcher script will:
- Start the Flask API server (port 5001)
- Start the web server (ports 8090-8099)
- Open your default browser

## Structure

```
audiobook-library/
├── backend/              # Flask API and database
│   ├── api_modular/     # Modular REST API (replaces monolithic api.py)
│   ├── api_server.py    # API server launcher
│   ├── schema.sql       # Database schema
│   ├── import_to_db.py  # JSON to SQLite importer
│   └── audiobooks.db    # SQLite database (generated)
├── scanner/              # Metadata extraction
│   └── scan_audiobooks.py
├── web-v2/              # Modern web interface
│   ├── index.html       # Single-page application
│   ├── css/
│   │   └── library.css
│   └── js/
│       └── library.js   # Frontend + audio player
├── scripts/             # Maintenance scripts
│   ├── fix_opus_metadata.sh
│   └── fix_all_opus_metadata.sh
├── data/                # Generated metadata
│   └── audiobooks.json  # Intermediate format
├── launch-v2.sh         # Quick launcher script
├── requirements.txt     # Python dependencies
├── INSTALL.md          # Detailed installation guide
└── README.md
```

## Requirements

- **Python**: 3.8 or higher
- **ffmpeg**: 4.0 or higher (with ffprobe)
- **Flask**: 3.0.0+
- **Web Browser**: Modern browser with HTML5 audio support

## Audio Format

All audiobooks are standardized to **OPUS** format for optimal compression and quality.

- OPUS provides ~50% smaller file sizes compared to M4B with equivalent audio quality
- Full metadata support including cover art, chapter markers, and narrator information
- Previous M4B files have been converted to OPUS

## Data Source

Default audiobook directory: `/srv/audiobooks` (system install) or `~/Audiobooks` (user install)

Configure via environment variable or config file:
```bash
# Environment variable
export AUDIOBOOKS_LIBRARY=/your/path/to/audiobooks

# Or edit config file
# System: /etc/audiobooks/audiobooks.conf
# User: ~/.config/audiobooks/audiobooks.conf
```

## Documentation

- **[INSTALL.md](INSTALL.md)** - Complete installation guide
- **[QUICKSTART.md](QUICKSTART.md)** - Quick start guide
- **[UPGRADE_GUIDE.md](UPGRADE_GUIDE.md)** - Upgrading from V1 to V2
- **[OPUS_METADATA_FIX.md](OPUS_METADATA_FIX.md)** - Fixing OPUS metadata issues

## API Endpoints

The Flask API provides the following endpoints:

- `GET /api/stats` - Library statistics
- `GET /api/audiobooks` - Paginated audiobook list
  - Query params: `page`, `per_page`, `search`, `author`, `narrator`, `sort`, `order`
- `GET /api/audiobooks/<id>` - Single audiobook details
- `GET /api/filters` - Available filter options (authors, narrators)
- `GET /api/stream/<id>` - Stream audiobook file
- `GET /covers/<filename>` - Serve cover images

Example queries:
```
/api/audiobooks?search=tolkien
/api/audiobooks?author=Brandon%20Sanderson&sort=duration_hours&order=desc
/api/audiobooks?narrator=Ray%20Porter&per_page=100
```

## Screenshots

### Library View
Browse your collection with cover art, search, and filters.

### Audio Player
Built-in player with speed control, skip buttons, and progress tracking.

## Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is open source. See LICENSE file for details.

## Acknowledgments

This library interface was built with the help of many excellent open-source projects and tools:

### Core Technologies
- **[Flask](https://flask.palletsprojects.com/)** - Python web framework powering the REST API
- **[SQLite](https://sqlite.org/)** with FTS5 - Fast, embedded database with full-text search
- **[FFmpeg](https://ffmpeg.org/)** / ffprobe - Metadata extraction from audio files
- **Vanilla JavaScript** - No framework bloat, just clean ES6+

### Development
- **[Claude Code](https://claude.ai/code)** (Anthropic) - AI coding assistant that helped with implementation details, debugging, and documentation throughout development.

### Related Projects
- **[AAXtoMP3](https://github.com/KrumpetPirate/AAXtoMP3)** - The converter component (included as a fork in `converter/`)
- **[audible-cli](https://github.com/mkb79/audible-cli)** - CLI tool for Audible integration
- **[mutagen](https://mutagen.readthedocs.io/)** - Audio metadata library for Opus cover embedding

*Designed for personal audiobook library management.*
