# Installation Guide

Complete installation instructions for the Audiobook Library system.

## Table of Contents

- [System Requirements](#system-requirements)
- [Dependencies](#dependencies)
- [Installation Steps](#installation-steps)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Troubleshooting](#troubleshooting)

---

## System Requirements

### Operating System
- **Linux** (tested on CachyOS/Arch, Ubuntu, Debian)
- **macOS** (10.15+)
- **Windows** (via WSL2 recommended)

### Hardware
- **CPU**: Any modern processor (2+ cores recommended)
- **RAM**: 2GB minimum, 4GB+ recommended
- **Storage**: Varies based on audiobook collection size
  - Application: ~10-20 MB
  - Database: ~5-10 MB per 1,000 audiobooks
  - Cover art cache: ~50-100 MB per 1,000 books

### Software
- **Python**: 3.8 or higher
- **ffmpeg**: 4.0 or higher (with ffprobe)
- **Web Browser**: Modern browser with HTML5 audio support
  - Chrome/Chromium 90+
  - Firefox 88+
  - Safari 14+
  - Edge 90+

---

## Dependencies

### System Packages

#### Arch Linux / CachyOS
```bash
sudo pacman -S python python-pip ffmpeg
```

#### Ubuntu / Debian
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv ffmpeg
```

#### macOS (Homebrew)
```bash
brew install python@3 ffmpeg
```

#### Windows (WSL2)
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv ffmpeg
```

### Python Packages

All Python dependencies are listed in `requirements.txt`:

- **Flask** (>=3.0.0) - Web framework for API server
- **flask-cors** (>=4.0.0) - CORS support for API

---

## Installation Steps

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/audiobook-library.git
cd audiobook-library
```

### 2. Create Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Verify FFmpeg Installation

```bash
ffmpeg -version
ffprobe -version
```

Both commands should display version information. If not, install ffmpeg using your package manager.

### 5. Configure Audiobook Directory

Edit the scanner configuration to point to your audiobook directory:

```bash
# Edit scanner/scan_audiobooks.py
# Change AUDIOBOOKS_DIR to your audiobook collection path
```

Default path: `/raid0/Audiobooks`

Supported formats:
- M4B (Apple Audiobook)
- OPUS (Opus audio)
- M4A (AAC audio)
- MP3 (MP3 audio)

### 6. Scan Your Audiobook Collection

```bash
cd scanner
python3 scan_audiobooks.py
```

This will:
- Scan your audiobook directory
- Extract metadata (title, author, narrator, duration, etc.)
- Extract cover art images
- Save metadata to `../data/audiobooks.json`
- Save cover images to `../web/covers/`

**Estimated time**: 1-2 hours for ~4,000 audiobooks

### 7. Import to Database

```bash
cd ../backend
python3 import_to_db.py
```

This creates an optimized SQLite database with:
- Full-text search index
- Author/narrator/publisher indices
- Efficient pagination support

### 8. Start the API Server

```bash
python3 api.py
```

The API will be available at `http://localhost:5001`

### 9. Start the Web Server

In a new terminal:

```bash
cd web-v2
python3 -m http.server 8090
```

The web interface will be available at `http://localhost:8090`

### 10. Access the Library

Open your web browser and navigate to:
```
http://localhost:8090
```

---

## Configuration

### Directory Structure

```
audiobook-library/
├── backend/
│   ├── api.py              # Flask API server
│   ├── audiobooks.db       # SQLite database
│   └── import_to_db.py     # Database import script
├── scanner/
│   └── scan_audiobooks.py  # Audiobook scanner
├── data/
│   └── audiobooks.json     # Metadata JSON export
├── web/
│   └── covers/             # Cover art images
├── web-v2/
│   ├── index.html          # Web interface
│   ├── css/
│   │   └── library.css     # Styling
│   └── js/
│       └── library.js      # Frontend JavaScript
├── scripts/
│   └── fix_all_opus_metadata.sh  # OPUS metadata fixer
└── requirements.txt        # Python dependencies
```

### API Configuration

Edit `backend/api.py` to change:
- **Port**: Default `5001` (line 355)
- **Host**: Default `0.0.0.0` (listens on all interfaces)
- **Debug mode**: Default `True` (disable for production)

### Web Server Configuration

The frontend runs on port `8090` by default. Change with:

```bash
python3 -m http.server PORT_NUMBER
```

### Audiobook Paths

If your audiobooks are in a different location, update:

1. `scanner/scan_audiobooks.py`:
   ```python
   AUDIOBOOKS_DIR = Path("/your/path/to/audiobooks")
   ```

2. `scripts/fix_all_opus_metadata.sh`:
   ```bash
   AUDIOBOOKS_DIR="/your/path/to/audiobooks"
   ```

---

## Running the Application

### Quick Start (Development)

```bash
# Terminal 1: API Server
cd audiobook-library
source venv/bin/activate
python3 backend/api.py

# Terminal 2: Web Server
cd audiobook-library/web-v2
python3 -m http.server 8090
```

### Production Deployment

For production use, consider:

1. **WSGI Server** (for API):
   ```bash
   pip install gunicorn
   gunicorn -w 4 -b 0.0.0.0:5001 backend.api:app
   ```

2. **Web Server** (for frontend):
   Use nginx or Apache to serve the `web-v2` directory

3. **Systemd Service** (Linux):
   Create service files for automatic startup

4. **Reverse Proxy**:
   Use nginx to proxy both API and web interface

---

## Troubleshooting

### Scanner Issues

**Problem**: Scanner fails with "ffprobe not found"
```bash
# Solution: Install ffmpeg
sudo apt install ffmpeg  # Ubuntu/Debian
sudo pacman -S ffmpeg    # Arch Linux
brew install ffmpeg      # macOS
```

**Problem**: No metadata extracted from files
```bash
# Check if ffprobe can read the file
ffprobe /path/to/audiobook.m4b
```

### API Issues

**Problem**: API won't start - "Address already in use"
```bash
# Check what's using port 5001
sudo lsof -i :5001

# Kill the process or change the port in backend/api.py
```

**Problem**: CORS errors in browser console
```bash
# Ensure flask-cors is installed
pip install flask-cors

# Verify API is running on the correct host/port
```

### Web Interface Issues

**Problem**: Cover images not displaying
```bash
# Check symlink exists
ls -la web-v2/covers

# Create if missing
cd web-v2
ln -s ../web/covers covers
```

**Problem**: Audio player shows "Failed to load audio file"
```bash
# Verify API is running
curl http://localhost:5001/health

# Check browser console for specific error
# Ensure file paths in database are correct
```

### Database Issues

**Problem**: No audiobooks showing in web interface
```bash
# Verify database exists and has data
sqlite3 backend/audiobooks.db "SELECT COUNT(*) FROM audiobooks"

# Re-import if needed
python3 backend/import_to_db.py
```

**Problem**: Search not working
```bash
# Verify FTS5 index exists
sqlite3 backend/audiobooks.db "SELECT name FROM sqlite_master WHERE type='table'"

# Re-import database to rebuild indexes
```

### OPUS Metadata Issues

**Problem**: OPUS files showing "Unknown Author/Narrator"
```bash
# Run metadata fix script
cd audiobook-library
./scripts/fix_all_opus_metadata.sh

# Re-scan and re-import
cd scanner && python3 scan_audiobooks.py
cd ../backend && python3 import_to_db.py
```

---

## Getting Help

If you encounter issues not covered here:

1. **Check Logs**: Look for error messages in:
   - Scanner output
   - API server output (`python3 backend/api.py`)
   - Browser console (F12)

2. **GitHub Issues**: Report bugs or ask questions at:
   https://github.com/YOUR_USERNAME/audiobook-library/issues

3. **Verify Setup**:
   ```bash
   # Check Python version
   python3 --version

   # Check FFmpeg
   ffmpeg -version

   # Check Python packages
   pip list | grep -E "Flask|flask-cors"
   ```

---

## Next Steps

After installation:

1. **Customize Theme**: Edit `web-v2/css/library.css` for styling
2. **Add Desktop Launcher**: Use provided launcher script for easy access
3. **Set Up Backups**: Regular backups of `backend/audiobooks.db` and `web/covers/`
4. **Explore Features**:
   - Full-text search
   - Filter by author, narrator, format
   - Playback speed control (0.75x - 2.0x)
   - Skip ±30 seconds
   - Volume control

---

## License

This project is open source. See LICENSE file for details.

## Contributing

Contributions welcome! Please open an issue or pull request on GitHub.
