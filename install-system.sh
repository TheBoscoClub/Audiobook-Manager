#!/usr/bin/env zsh
# =============================================================================
# Audiobook Library - System-wide Installation Script
# =============================================================================
# Installs audiobook library as a system service.
#
# Locations:
#   Executables:  /usr/local/bin/audiobooks-*
#   Config:       /etc/audiobooks/
#   Library:      /usr/local/lib/audiobooks/
#   Services:     /etc/systemd/system/
#   Data:         Configurable (default: /srv/audiobooks)
#
# Usage:
#   sudo ./install-system.sh [OPTIONS]
#
# Options:
#   --data-dir PATH    Audiobook data directory (default: /srv/audiobooks)
#   --uninstall        Remove system installation
#   --no-services      Skip systemd service installation
#   --help             Show this help message
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default paths
INSTALL_PREFIX="/usr/local"
CONFIG_DIR="/etc/audiobooks"
LIB_DIR="${INSTALL_PREFIX}/lib/audiobooks"
BIN_DIR="${INSTALL_PREFIX}/bin"
SYSTEMD_DIR="/etc/systemd/system"
DATA_DIR="/srv/audiobooks"

# Script directory (source)
SCRIPT_DIR="${0:A:h}"

# Options
INSTALL_SERVICES=true
UNINSTALL=false

# -----------------------------------------------------------------------------
# Parse arguments
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --data-dir)
            DATA_DIR="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --no-services)
            INSTALL_SERVICES=false
            shift
            ;;
        --help)
            head -30 "$0" | grep -E '^#' | sed 's/^# //' | sed 's/^#//'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Check root
# -----------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root (sudo)${NC}"
    exit 1
fi

# -----------------------------------------------------------------------------
# Uninstall
# -----------------------------------------------------------------------------
if [[ "$UNINSTALL" == "true" ]]; then
    echo -e "${YELLOW}=== Uninstalling Audiobook Library (System) ===${NC}"

    # Stop and disable services
    systemctl stop audiobook-api.service audiobooks-web.service 2>/dev/null || true
    systemctl disable audiobook-api.service audiobooks-web.service 2>/dev/null || true

    # Remove files
    rm -f "${BIN_DIR}/audiobook-api"
    rm -f "${BIN_DIR}/audiobooks-web"
    rm -f "${BIN_DIR}/audiobooks-scan"
    rm -f "${BIN_DIR}/audiobooks-import"
    rm -rf "${LIB_DIR}"
    rm -f "${SYSTEMD_DIR}/audiobook-api.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-web.service"
    rm -f "${SYSTEMD_DIR}/audiobooks.target"

    # Reload systemd
    systemctl daemon-reload

    echo -e "${GREEN}Uninstallation complete.${NC}"
    echo "Note: Configuration in ${CONFIG_DIR} and data in ${DATA_DIR} were NOT removed."
    exit 0
fi

# -----------------------------------------------------------------------------
# Install
# -----------------------------------------------------------------------------
echo -e "${GREEN}=== Audiobook Library System Installation ===${NC}"
echo ""
echo "Installation paths:"
echo "  Executables:  ${BIN_DIR}/"
echo "  Config:       ${CONFIG_DIR}/"
echo "  Library:      ${LIB_DIR}/"
echo "  Services:     ${SYSTEMD_DIR}/"
echo "  Data:         ${DATA_DIR}/"
echo ""

# Create audiobooks service account
echo -e "${BLUE}Setting up service account...${NC}"
if ! getent group audiobooks >/dev/null 2>&1; then
    echo "  Creating 'audiobooks' group..."
    groupadd --system audiobooks
else
    echo "  Group 'audiobooks' already exists"
fi

if ! getent passwd audiobooks >/dev/null 2>&1; then
    echo "  Creating 'audiobooks' service user..."
    useradd --system --gid audiobooks --shell /usr/sbin/nologin \
        --home-dir /var/lib/audiobooks --comment "Audiobook Library Service" audiobooks
else
    echo "  User 'audiobooks' already exists"
fi
echo ""

# Create directories
echo -e "${BLUE}Creating directories...${NC}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${LIB_DIR}"
mkdir -p "${DATA_DIR}/Library"
mkdir -p "${DATA_DIR}/Sources"
mkdir -p "${DATA_DIR}/Supplements"
mkdir -p "/var/lib/audiobooks"
mkdir -p "/var/log/audiobooks"

# Install library files
echo -e "${BLUE}Installing library files...${NC}"
cp -r "${SCRIPT_DIR}/library" "${LIB_DIR}/"
cp -r "${SCRIPT_DIR}/lib" "${LIB_DIR}/"
[[ -d "${SCRIPT_DIR}/converter" ]] && cp -r "${SCRIPT_DIR}/converter" "${LIB_DIR}/"
[[ -f "${SCRIPT_DIR}/VERSION" ]] && cp "${SCRIPT_DIR}/VERSION" "${LIB_DIR}/"
cp "${SCRIPT_DIR}/etc/audiobooks.conf.example" "${CONFIG_DIR}/"
# Ensure installed files are readable by the audiobooks service user
chmod -R a+rX "${LIB_DIR}"

# Create config file if it doesn't exist
if [[ ! -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
    echo -e "${BLUE}Creating configuration file...${NC}"
    cat > "${CONFIG_DIR}/audiobooks.conf" << EOF
# Audiobook Library Configuration
# Generated by install-system.sh on $(date +%Y-%m-%d)

# Data directories
AUDIOBOOKS_DATA="${DATA_DIR}"
AUDIOBOOKS_LIBRARY="\${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="\${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="\${AUDIOBOOKS_DATA}/Supplements"

# Application directories
AUDIOBOOKS_HOME="${LIB_DIR}"
AUDIOBOOKS_DATABASE="/var/lib/audiobooks/audiobooks.db"
AUDIOBOOKS_COVERS="\${AUDIOBOOKS_HOME}/library/web-v2/covers"
AUDIOBOOKS_CERTS="\${AUDIOBOOKS_HOME}/library/certs"
AUDIOBOOKS_LOGS="/var/log/audiobooks"
AUDIOBOOKS_VENV="\${AUDIOBOOKS_HOME}/library/venv"

# Server settings
AUDIOBOOKS_API_PORT="5001"
AUDIOBOOKS_WEB_PORT="8090"
AUDIOBOOKS_BIND_ADDRESS="0.0.0.0"
AUDIOBOOKS_HTTPS_ENABLED="true"

# Authentication (multi-user support)
# Set AUTH_ENABLED="true" to require login for remote access
# When disabled (default), admin endpoints are restricted to localhost
AUTH_ENABLED="false"
AUTH_DATABASE="/var/lib/audiobooks/auth.db"
AUTH_KEY_FILE="/etc/audiobooks/auth.key"

# Remote access (only needed when AUTH_ENABLED="true")
# See audiobooks.conf.example for full documentation
#AUDIOBOOKS_HOSTNAME=""
#BASE_URL=""
#CORS_ORIGIN=""
EOF
fi

# Generate auth key file (for multi-user support when AUTH_ENABLED=true)
echo -e "${BLUE}Setting up authentication...${NC}"
AUTH_KEY="${CONFIG_DIR}/auth.key"
if [[ ! -f "$AUTH_KEY" ]]; then
    echo "  Generating encryption key for auth database..."
    head -c 32 /dev/urandom | xxd -p | tr -d '\n' > "$AUTH_KEY"
    chown audiobooks:audiobooks "$AUTH_KEY"
    chmod 600 "$AUTH_KEY"
    echo "  Created: $AUTH_KEY"
else
    echo "  Auth key file already exists"
fi

# Initialize database if it doesn't exist
DB_FILE="/var/lib/audiobooks/audiobooks.db"
if [[ ! -f "$DB_FILE" ]]; then
    echo -e "${BLUE}Initializing database...${NC}"
    SCHEMA_FILE="${LIB_DIR}/library/backend/schema.sql"
    if [[ -f "$SCHEMA_FILE" ]]; then
        sqlite3 "$DB_FILE" < "$SCHEMA_FILE"
        echo "  Created: $DB_FILE"
    else
        echo -e "${YELLOW}  Warning: schema.sql not found, skipping database initialization${NC}"
    fi
fi

# Set ownership on data and state directories
echo -e "${BLUE}Setting directory ownership...${NC}"
chown -R audiobooks:audiobooks "${DATA_DIR}"
chown -R audiobooks:audiobooks "/var/lib/audiobooks"
chown -R audiobooks:audiobooks "/var/log/audiobooks"
echo ""

# Create wrapper scripts in /usr/local/bin
echo -e "${BLUE}Creating executable wrappers...${NC}"

# API server wrapper
cat > "${BIN_DIR}/audiobook-api" << 'EOF'
#!/bin/bash
# Audiobook Library API Server
source /usr/local/lib/audiobooks/lib/audiobook-config.sh
exec "$(audiobooks_python)" "${AUDIOBOOKS_HOME}/library/backend/api_server.py" "$@"
EOF
chmod 755 "${BIN_DIR}/audiobook-api"

# Web server wrapper
cat > "${BIN_DIR}/audiobooks-web" << 'EOF'
#!/bin/bash
# Audiobook Library Web Server (HTTPS)
source /usr/local/lib/audiobooks/lib/audiobook-config.sh
exec python3 "${AUDIOBOOKS_HOME}/library/web-v2/https_server.py" "$@"
EOF
chmod 755 "${BIN_DIR}/audiobooks-web"

# Scanner wrapper
cat > "${BIN_DIR}/audiobooks-scan" << 'EOF'
#!/bin/bash
# Audiobook Library Scanner
source /usr/local/lib/audiobooks/lib/audiobook-config.sh
exec "$(audiobooks_python)" "${AUDIOBOOKS_HOME}/library/scanner/scan_audiobooks.py" "$@"
EOF
chmod 755 "${BIN_DIR}/audiobooks-scan"

# Database import wrapper
cat > "${BIN_DIR}/audiobooks-import" << 'EOF'
#!/bin/bash
# Audiobook Library Database Import
source /usr/local/lib/audiobooks/lib/audiobook-config.sh
exec "$(audiobooks_python)" "${AUDIOBOOKS_HOME}/library/backend/import_to_db.py" "$@"
EOF
chmod 755 "${BIN_DIR}/audiobooks-import"

# Config viewer
cat > "${BIN_DIR}/audiobooks-config" << 'EOF'
#!/bin/bash
# Show audiobook library configuration
source /usr/local/lib/audiobooks/lib/audiobook-config.sh
audiobooks_print_config
EOF
chmod 755 "${BIN_DIR}/audiobooks-config"

# Setup Python virtual environment (recreate if broken or missing)
if ! "${LIB_DIR}/library/venv/bin/python" --version &>/dev/null; then
    echo -e "${BLUE}Setting up Python virtual environment...${NC}"
    rm -rf "${LIB_DIR}/library/venv"
    python3 -m venv "${LIB_DIR}/library/venv"
fi
# Install/update dependencies
if [[ -f "${LIB_DIR}/library/requirements.txt" ]]; then
    echo -e "${BLUE}Installing Python dependencies...${NC}"
    "${LIB_DIR}/library/venv/bin/pip" install --quiet -r "${LIB_DIR}/library/requirements.txt" 2>&1 | grep -v 'already satisfied' || true
else
    echo -e "${BLUE}Installing Flask (no requirements.txt found)...${NC}"
    "${LIB_DIR}/library/venv/bin/pip" install --quiet Flask
fi

# Generate SSL certificate if needed
CERT_DIR="${LIB_DIR}/library/certs"
if [[ ! -f "${CERT_DIR}/server.crt" ]]; then
    echo -e "${BLUE}Generating SSL certificate...${NC}"
    mkdir -p "${CERT_DIR}"
    openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
        -nodes -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.crt" \
        -subj "/CN=localhost/O=Audiobooks/C=US" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        2>/dev/null
    chmod 600 "${CERT_DIR}/server.key"
    chmod 644 "${CERT_DIR}/server.crt"
fi

# Install systemd services
if [[ "$INSTALL_SERVICES" == "true" ]]; then
    echo -e "${BLUE}Installing systemd services...${NC}"

    # API service
    cat > "${SYSTEMD_DIR}/audiobook-api.service" << EOF
[Unit]
Description=Audiobooks Library API Server
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=network.target

[Service]
Type=simple
User=audiobooks
Group=audiobooks
WorkingDirectory=${LIB_DIR}/library/backend
EnvironmentFile=${CONFIG_DIR}/audiobooks.conf
Environment=PYTHONUNBUFFERED=1
ExecStart=${LIB_DIR}/library/venv/bin/python api_server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Web service
    cat > "${SYSTEMD_DIR}/audiobooks-web.service" << EOF
[Unit]
Description=Audiobooks Library Web Server (HTTPS)
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=audiobook-api.service
Wants=audiobook-api.service

[Service]
Type=simple
User=audiobooks
Group=audiobooks
WorkingDirectory=${LIB_DIR}/library/web-v2
EnvironmentFile=${CONFIG_DIR}/audiobooks.conf
Environment=PYTHONUNBUFFERED=1
ExecStart=${LIB_DIR}/library/venv/bin/python https_server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    # Target
    cat > "${SYSTEMD_DIR}/audiobooks.target" << EOF
[Unit]
Description=Audiobooks Library Services
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
Wants=audiobook-api.service audiobooks-web.service

[Install]
WantedBy=multi-user.target
EOF

    # Reload systemd
    systemctl daemon-reload

    echo ""
    echo -e "${YELLOW}To enable services at boot:${NC}"
    echo "  sudo systemctl enable audiobook-api audiobooks-web"
    echo ""
    echo -e "${YELLOW}To start services now:${NC}"
    echo "  sudo systemctl start audiobook-api audiobooks-web"
fi

# Create /etc/profile.d script for environment
echo -e "${BLUE}Creating environment profile...${NC}"
cat > /etc/profile.d/audiobooks.sh << 'EOF'
# Audiobook Library Environment
# Source the config loader to get all variables
if [[ -f /usr/local/lib/audiobooks/lib/audiobook-config.sh ]]; then
    source /usr/local/lib/audiobooks/lib/audiobook-config.sh
fi
EOF
chmod 644 /etc/profile.d/audiobooks.sh

echo ""
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo ""
echo "Configuration: ${CONFIG_DIR}/audiobooks.conf"
echo "Data directory: ${DATA_DIR}"
echo ""
echo "Commands available:"
echo "  audiobook-api      - Start API server"
echo "  audiobooks-web      - Start web server"
echo "  audiobooks-scan     - Scan audiobook library"
echo "  audiobooks-import   - Import to database"
echo "  audiobooks-config   - Show configuration"
echo ""
echo "Access the library at: https://localhost:8090"
echo ""
