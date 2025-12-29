# Installation Guide

Complete installation instructions for the Audiobook Library system.

## Table of Contents

- [System Requirements](#system-requirements)
- [Dependencies](#dependencies)
- [Installation Methods](#installation-methods)
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
- **openssl**: For SSL certificate generation
- **Web Browser**: Modern browser with HTML5 audio support
  - Chrome/Chromium 90+
  - Firefox 88+
  - Safari 14+
  - Edge 90+

---

## Dependencies

### System Packages

Install the following using your system's package manager:

| Package | Description |
|---------|-------------|
| `python3` | Python 3.8+ interpreter |
| `python3-pip` | Python package manager |
| `python3-venv` | Virtual environment support (some distros) |
| `ffmpeg` | Audio/video processing (includes ffprobe) |
| `openssl` | SSL certificate generation |

**Package Names by OS:**

| OS | Package Manager | Example Command |
|----|-----------------|-----------------|
| Arch/CachyOS/Manjaro | pacman | `sudo pacman -S python python-pip ffmpeg openssl` |
| Ubuntu/Debian | apt | `sudo apt install python3 python3-pip python3-venv ffmpeg openssl` |
| Fedora/RHEL | dnf | `sudo dnf install python3 python3-pip ffmpeg openssl` |
| openSUSE | zypper | `sudo zypper install python3 python3-pip ffmpeg openssl` |
| macOS | Homebrew | `brew install python@3 ffmpeg openssl` |
| Windows | WSL2/Chocolatey | Use WSL2 with your preferred Linux distro |
| NixOS | nix | `nix-env -iA nixpkgs.python3 nixpkgs.ffmpeg nixpkgs.openssl` |
| Alpine | apk | `apk add python3 py3-pip ffmpeg openssl` |

**Note:** Package names may vary slightly between distributions. Search your package manager if the exact name doesn't work.

### Python Packages

All Python dependencies are listed in `requirements.txt`:

- **Flask** (>=3.0.0) - Web framework for API server

---

## Installation Methods

### Method 1: User Installation (Recommended)

Installs to `~/.local/bin` and `~/.config/audiobooks` (no root required):

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/Audiobook-Manager.git
cd Audiobook-Manager

# Run user installer
./install-user.sh --data-dir ~/Audiobooks
```

The installer will:
- Create directories for audiobook data
- Install commands to `~/.local/bin`
- Create configuration at `~/.config/audiobooks/audiobooks.conf`
- Generate SSL certificate at `~/.config/audiobooks/certs/`
- Install systemd user services
- Set up Python virtual environment

### Method 2: System Installation

Installs to `/usr/local/bin` and `/etc/audiobooks` (requires root):

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/Audiobook-Manager.git
cd Audiobook-Manager

# Run system installer
sudo ./install-system.sh --data-dir /srv/audiobooks
```

### Method 3: Manual Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/Audiobook-Manager.git
cd Audiobook-Manager

# Create Python virtual environment
cd library
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Copy example configuration
mkdir -p ~/.config/audiobooks
cp ../etc/audiobooks.conf.example ~/.config/audiobooks/audiobooks.conf

# Edit configuration
nano ~/.config/audiobooks/audiobooks.conf
```

### Verify Installation

After installation:

```bash
# Check FFmpeg
ffmpeg -version
ffprobe -version

# Check configuration (if installed via install scripts)
audiobooks-config

# Or source config manually
source lib/audiobooks-config.sh
audiobooks_print_config
```

---

## Configuration

### Configuration Files

Configuration is loaded from multiple sources in priority order:

1. **System config**: `/etc/audiobooks/audiobooks.conf`
2. **User config**: `~/.config/audiobooks/audiobooks.conf`
3. **Environment variables**

### Configuration Variables

| Variable | Description |
|----------|-------------|
| `AUDIOBOOKS_DATA` | Root data directory for all audiobook files |
| `AUDIOBOOKS_LIBRARY` | Converted audiobook files (default: `${AUDIOBOOKS_DATA}/Library`) |
| `AUDIOBOOKS_SOURCES` | Source AAXC files (default: `${AUDIOBOOKS_DATA}/Sources`) |
| `AUDIOBOOKS_SUPPLEMENTS` | PDF supplements (default: `${AUDIOBOOKS_DATA}/Supplements`) |
| `AUDIOBOOKS_HOME` | Application installation directory |
| `AUDIOBOOKS_DATABASE` | SQLite database path |
| `AUDIOBOOKS_COVERS` | Cover art cache directory |
| `AUDIOBOOKS_CERTS` | SSL certificate directory |
| `AUDIOBOOKS_LOGS` | Log files directory |
| `AUDIOBOOKS_VENV` | Python virtual environment path |
| `AUDIOBOOKS_API_PORT` | API server port (default: 5001) |
| `AUDIOBOOKS_WEB_PORT` | Web server port (default: 8090) |

### Example Configuration

```bash
# ~/.config/audiobooks/audiobooks.conf

# Data directories
AUDIOBOOKS_DATA="$HOME/Audiobooks"
AUDIOBOOKS_LIBRARY="${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="${AUDIOBOOKS_DATA}/Supplements"

# Application directories
AUDIOBOOKS_HOME="$HOME/.local/lib/audiobooks"
AUDIOBOOKS_DATABASE="$HOME/.local/var/lib/audiobooks/audiobooks.db"
AUDIOBOOKS_COVERS="${AUDIOBOOKS_HOME}/library/web-v2/covers"
AUDIOBOOKS_CERTS="$HOME/.config/audiobooks/certs"
AUDIOBOOKS_LOGS="$HOME/.local/var/log/audiobooks"

# Server settings
AUDIOBOOKS_API_PORT="5001"
AUDIOBOOKS_WEB_PORT="8090"
```

### Override via Environment

You can override any configuration variable:

```bash
AUDIOBOOKS_LIBRARY=/mnt/nas/audiobooks audiobooks-scan
```

---

## Running the Application

### First-Time Setup

After installation, scan your audiobook collection:

```bash
# Scan audiobooks
audiobooks-scan

# Import to database
audiobooks-import

# Or with manual installation:
cd library/scanner
python3 scan_audiobooks.py
cd ../backend
python3 import_to_db.py
```

**Estimated time**: 1-2 hours for ~4,000 audiobooks

### Starting Services

#### Using Systemd (Recommended)

```bash
# Enable core services at boot
sudo systemctl enable audiobooks-api audiobooks-proxy audiobooks-redirect \
  audiobooks-converter audiobooks-mover audiobooks-downloader.timer

# Start services
sudo systemctl start audiobooks.target
```

#### Using Launch Script

```bash
./launch.sh
```

#### Manual Start

```bash
# Terminal 1: API Server
audiobooks-api

# Terminal 2: Web Server
audiobooks-web
```

### Access the Library

Open your browser and navigate to:
```
https://localhost:8443
```

**Note**: You'll see a browser warning about the self-signed certificate. Click "Advanced" → "Proceed to localhost" to continue.

### Service Management

```bash
# Check all audiobooks services
sudo systemctl status 'audiobooks-*'

# Check specific services
sudo systemctl status audiobooks-api audiobooks-proxy

# Restart services
sudo systemctl restart audiobooks.target

# Stop services
sudo systemctl stop audiobooks.target

# View logs
journalctl -u audiobooks-api -f
journalctl -u audiobooks-proxy -f
```

---

## Troubleshooting

### Scanner Issues

**Problem**: Scanner fails with "ffprobe not found"
```bash
# Solution: Install ffmpeg using your system's package manager
# See the Dependencies section above for your OS-specific command
```

**Problem**: No metadata extracted from files
```bash
# Check if ffprobe can read the file
ffprobe /path/to/audiobook.m4b
```

### API Issues

**Problem**: API won't start - "Address already in use"
```bash
# Check what's using the port
lsof -i :5001

# Kill the process or change the port in config
```

**Problem**: CORS errors in browser console
```bash
# CORS is handled natively by the API (no flask-cors needed)
# Verify API is running on the correct host/port
curl -I http://localhost:5001/api/audiobooks
# Check for Access-Control-Allow-Origin header in response
```

### Web Interface Issues

**Problem**: Cover images not displaying
```bash
# Check symlink exists
ls -la library/web-v2/covers

# Create if missing
cd library/web-v2
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
sqlite3 $AUDIOBOOKS_DATABASE "SELECT COUNT(*) FROM audiobooks"

# Re-import if needed
audiobooks-import
```

**Problem**: Search not working
```bash
# Verify FTS5 index exists
sqlite3 $AUDIOBOOKS_DATABASE "SELECT name FROM sqlite_master WHERE type='table'"

# Re-import database to rebuild indexes
audiobooks-import
```

### Configuration Issues

**Problem**: Commands not found
```bash
# Add ~/.local/bin to PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Or for zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Problem**: Configuration not loading
```bash
# Check config file syntax
cat ~/.config/audiobooks/audiobooks.conf

# Verify config loads correctly
source lib/audiobooks-config.sh
audiobooks_print_config
```

### SSL Certificate Issues

**Problem**: Certificate expired
```bash
# Regenerate certificate
./install-user.sh  # Will prompt to regenerate

# Or manually:
openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
    -nodes -keyout ~/.config/audiobooks/certs/server.key \
    -out ~/.config/audiobooks/certs/server.crt \
    -subj "/CN=localhost/O=Audiobooks/C=US" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

---

## Uninstalling

### User Installation
```bash
./install-user.sh --uninstall
```

### System Installation
```bash
sudo ./install-system.sh --uninstall
```

**Note**: Uninstallers do NOT remove configuration files or data directories. Remove manually if desired.

---

## Getting Help

If you encounter issues not covered here:

1. **Check Logs**: Look for error messages in:
   - `journalctl --user -u audiobooks-api`
   - `journalctl --user -u audiobooks-proxy`
   - Browser console (F12)

2. **Verify Setup**:
   ```bash
   # Check Python version
   python3 --version

   # Check FFmpeg
   ffmpeg -version

   # Check configuration
   audiobooks-config

   # Check Python packages
   pip list | grep Flask
   ```

3. **GitHub Issues**: Report bugs or ask questions at:
   https://github.com/YOUR_USERNAME/Audiobook-Manager/issues

---

## Next Steps

After installation:

1. **Scan Your Library**: Run `audiobooks-scan` to index your audiobooks
2. **Import to Database**: Run `audiobooks-import` to build searchable database
3. **Start Services**: Enable systemd services for automatic startup
4. **Access Web Interface**: Open https://localhost:8443

**Features to Explore**:
- Full-text search
- Filter by author, narrator, format
- Playback speed control (0.75x - 2.0x)
- Skip ±30 seconds
- Volume control
- PDF supplements

---

## License

This project is open source. See LICENSE file for details.

## Contributing

Contributions welcome! Please open an issue or pull request on GitHub.
