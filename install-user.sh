#!/usr/bin/env zsh
# =============================================================================
# Audiobook Library - User Installation Script
# =============================================================================
# Installs audiobook library for the current user (no root required).
#
# Locations:
#   Executables:  ~/.local/bin/audiobooks-*
#   Config:       ~/.config/audiobooks/
#   Library:      ~/.local/lib/audiobooks/
#   Services:     ~/.config/systemd/user/
#   Data:         Configurable (default: ~/Audiobooks)
#
# Usage:
#   ./install-user.sh [OPTIONS]
#
# Options:
#   --data-dir PATH    Audiobook data directory (default: ~/Audiobooks)
#   --uninstall        Remove user installation
#   --no-services      Skip systemd user service installation
#   --help             Show this help message
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# Default paths (user-level)
INSTALL_PREFIX="$HOME/.local"
CONFIG_DIR="$HOME/.config/audiobooks"
LIB_DIR="${INSTALL_PREFIX}/lib/audiobooks"
BIN_DIR="${INSTALL_PREFIX}/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"
DATA_DIR="$HOME/Audiobooks"
LOG_DIR="$HOME/.local/var/log/audiobooks"
STATE_DIR="$HOME/.local/var/lib/audiobooks"

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
# Helper Functions
# -----------------------------------------------------------------------------

prompt_delete_data() {
    # Prompt user about deleting audiobook data directories
    # Reads configuration from the config file

    local config_file="${CONFIG_DIR}/audiobooks.conf"
    local data_dir=""
    local library_dir=""
    local sources_dir=""
    local supplements_dir=""

    # Read configuration to get data directories
    if [[ -f "$config_file" ]]; then
        source "$config_file"
        data_dir="${AUDIOBOOKS_DATA:-}"
        library_dir="${AUDIOBOOKS_LIBRARY:-}"
        sources_dir="${AUDIOBOOKS_SOURCES:-}"
        supplements_dir="${AUDIOBOOKS_SUPPLEMENTS:-}"
    fi

    # Initialize deletion flags
    local DELETE_LIBRARY=false
    local DELETE_SOURCES=false
    local DELETE_SUPPLEMENTS=false
    local DELETE_CONFIG=false

    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                    Data Removal Options                           ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "The following data directories were found:"
    echo ""

    # Check and display each data directory with size
    if [[ -n "$library_dir" ]] && [[ -d "$library_dir" ]]; then
        local lib_size=$(du -sh "$library_dir" 2>/dev/null | cut -f1)
        echo -e "  ${BOLD}Converted Audiobooks:${NC} $library_dir"
        echo "    Size: ${lib_size:-unknown}"
        local lib_count=$(find "$library_dir" -type f \( -name "*.m4b" -o -name "*.mp3" -o -name "*.opus" -o -name "*.flac" \) 2>/dev/null | wc -l)
        echo "    Files: ${lib_count} audiobook files"
        echo ""
    fi

    if [[ -n "$sources_dir" ]] && [[ -d "$sources_dir" ]]; then
        local src_size=$(du -sh "$sources_dir" 2>/dev/null | cut -f1)
        echo -e "  ${BOLD}Source Files (AAX/AAXC):${NC} $sources_dir"
        echo "    Size: ${src_size:-unknown}"
        local src_count=$(find "$sources_dir" -type f \( -name "*.aax" -o -name "*.aaxc" \) 2>/dev/null | wc -l)
        echo "    Files: ${src_count} source files"
        echo ""
    fi

    if [[ -n "$supplements_dir" ]] && [[ -d "$supplements_dir" ]]; then
        local sup_size=$(du -sh "$supplements_dir" 2>/dev/null | cut -f1)
        echo -e "  ${BOLD}Supplemental PDFs:${NC} $supplements_dir"
        echo "    Size: ${sup_size:-unknown}"
        local sup_count=$(find "$supplements_dir" -type f -name "*.pdf" 2>/dev/null | wc -l)
        echo "    Files: ${sup_count} PDF files"
        echo ""
    fi

    echo -e "${RED}WARNING: Deleted files cannot be recovered!${NC}"
    echo ""

    # Prompt for each category
    if [[ -n "$library_dir" ]] && [[ -d "$library_dir" ]]; then
        while true; do
            read -r -p "Delete converted audiobooks in $library_dir? [y/N]: " answer
            case "${answer,,}" in
                y|yes) DELETE_LIBRARY=true; echo -e "  ${RED}→ Will delete converted audiobooks${NC}"; break ;;
                n|no|"") echo -e "  ${GREEN}→ Keeping converted audiobooks${NC}"; break ;;
                *) echo "  Please answer y(es) or n(o)" ;;
            esac
        done
        echo ""
    fi

    if [[ -n "$sources_dir" ]] && [[ -d "$sources_dir" ]]; then
        while true; do
            read -r -p "Delete source files (AAX/AAXC) in $sources_dir? [y/N]: " answer
            case "${answer,,}" in
                y|yes) DELETE_SOURCES=true; echo -e "  ${RED}→ Will delete source files${NC}"; break ;;
                n|no|"") echo -e "  ${GREEN}→ Keeping source files${NC}"; break ;;
                *) echo "  Please answer y(es) or n(o)" ;;
            esac
        done
        echo ""
    fi

    if [[ -n "$supplements_dir" ]] && [[ -d "$supplements_dir" ]]; then
        while true; do
            read -r -p "Delete supplemental PDFs in $supplements_dir? [y/N]: " answer
            case "${answer,,}" in
                y|yes) DELETE_SUPPLEMENTS=true; echo -e "  ${RED}→ Will delete supplemental PDFs${NC}"; break ;;
                n|no|"") echo -e "  ${GREEN}→ Keeping supplemental PDFs${NC}"; break ;;
                *) echo "  Please answer y(es) or n(o)" ;;
            esac
        done
        echo ""
    fi

    if [[ -f "$config_file" ]]; then
        while true; do
            read -r -p "Delete configuration files? [y/N]: " answer
            case "${answer,,}" in
                y|yes) DELETE_CONFIG=true; echo -e "  ${RED}→ Will delete configuration${NC}"; break ;;
                n|no|"") echo -e "  ${GREEN}→ Keeping configuration${NC}"; break ;;
                *) echo "  Please answer y(es) or n(o)" ;;
            esac
        done
        echo ""
    fi

    # Confirm if anything is being deleted
    if [[ "$DELETE_LIBRARY" == "true" ]] || [[ "$DELETE_SOURCES" == "true" ]] || \
       [[ "$DELETE_SUPPLEMENTS" == "true" ]] || [[ "$DELETE_CONFIG" == "true" ]]; then
        echo ""
        echo -e "${RED}╔═══════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║                    CONFIRM DELETION                               ║${NC}"
        echo -e "${RED}╚═══════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "The following will be PERMANENTLY DELETED:"
        [[ "$DELETE_LIBRARY" == "true" ]] && echo -e "  ${RED}• Converted audiobooks${NC}"
        [[ "$DELETE_SOURCES" == "true" ]] && echo -e "  ${RED}• Source files (AAX/AAXC)${NC}"
        [[ "$DELETE_SUPPLEMENTS" == "true" ]] && echo -e "  ${RED}• Supplemental PDFs${NC}"
        [[ "$DELETE_CONFIG" == "true" ]] && echo -e "  ${RED}• Configuration files${NC}"
        echo ""

        while true; do
            read -r -p "Are you sure you want to proceed? [y/N]: " confirm
            case "${confirm,,}" in
                y|yes)
                    echo ""
                    echo -e "${YELLOW}Proceeding with deletion...${NC}"

                    [[ "$DELETE_LIBRARY" == "true" ]] && [[ -d "$library_dir" ]] && \
                        echo "Deleting converted audiobooks..." && rm -rf "$library_dir"

                    [[ "$DELETE_SOURCES" == "true" ]] && [[ -d "$sources_dir" ]] && \
                        echo "Deleting source files..." && rm -rf "$sources_dir"

                    [[ "$DELETE_SUPPLEMENTS" == "true" ]] && [[ -d "$supplements_dir" ]] && \
                        echo "Deleting supplemental PDFs..." && rm -rf "$supplements_dir"

                    [[ "$DELETE_CONFIG" == "true" ]] && \
                        echo "Deleting configuration..." && rm -rf "$CONFIG_DIR"

                    # Remove empty data directory
                    if [[ -n "$data_dir" ]] && [[ -d "$data_dir" ]] && \
                       [[ -z "$(ls -A "$data_dir" 2>/dev/null)" ]]; then
                        echo "Removing empty data directory..."
                        rmdir "$data_dir" 2>/dev/null || true
                    fi

                    echo -e "${GREEN}Data deletion complete.${NC}"
                    break
                    ;;
                n|no|"")
                    echo -e "${GREEN}Deletion cancelled. All data preserved.${NC}"
                    break
                    ;;
                *)
                    echo "Please answer y(es) or n(o)"
                    ;;
            esac
        done
    else
        echo -e "${GREEN}No data selected for deletion. All files preserved.${NC}"
    fi
}

# -----------------------------------------------------------------------------
# Uninstall
# -----------------------------------------------------------------------------
if [[ "$UNINSTALL" == "true" ]]; then
    echo -e "${YELLOW}=== Uninstalling Audiobook Library (User) ===${NC}"

    # Stop and disable services
    echo -e "${BLUE}Stopping services...${NC}"
    systemctl --user stop audiobook-api.service audiobooks-web.service 2>/dev/null || true
    systemctl --user disable audiobook-api.service audiobooks-web.service 2>/dev/null || true

    # Remove application files
    echo -e "${BLUE}Removing application files...${NC}"
    rm -f "${BIN_DIR}/audiobook-api"
    rm -f "${BIN_DIR}/audiobooks-web"
    rm -f "${BIN_DIR}/audiobooks-scan"
    rm -f "${BIN_DIR}/audiobooks-import"
    rm -f "${BIN_DIR}/audiobooks-config"
    rm -rf "${LIB_DIR}"
    rm -f "${SYSTEMD_DIR}/audiobook-api.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-web.service"
    rm -f "${SYSTEMD_DIR}/audiobooks.target"

    # Remove database and logs
    rm -rf "${STATE_DIR}"
    rm -rf "${LOG_DIR}"

    # Reload systemd
    systemctl --user daemon-reload 2>/dev/null || true

    echo -e "${GREEN}Application files removed.${NC}"

    # Prompt about data directories
    if [[ -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        prompt_delete_data
    else
        echo ""
        echo "Note: No configuration file found at ${CONFIG_DIR}/audiobooks.conf"
        echo "Data directories were not modified."
    fi

    echo ""
    echo -e "${GREEN}Uninstallation complete.${NC}"
    exit 0
fi

# -----------------------------------------------------------------------------
# Install
# -----------------------------------------------------------------------------
echo -e "${GREEN}=== Audiobook Library User Installation ===${NC}"
echo ""
echo "Installation paths:"
echo "  Executables:  ${BIN_DIR}/"
echo "  Config:       ${CONFIG_DIR}/"
echo "  Library:      ${LIB_DIR}/"
echo "  Services:     ${SYSTEMD_DIR}/"
echo "  Data:         ${DATA_DIR}/"
echo "  Logs:         ${LOG_DIR}/"
echo ""

# Create directories
echo -e "${BLUE}Creating directories...${NC}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${LIB_DIR}"
mkdir -p "${BIN_DIR}"
mkdir -p "${DATA_DIR}/Library"
mkdir -p "${DATA_DIR}/Sources"
mkdir -p "${DATA_DIR}/Supplements"
mkdir -p "${STATE_DIR}"
mkdir -p "${LOG_DIR}"
mkdir -p "${SYSTEMD_DIR}"

# Install library files
echo -e "${BLUE}Installing library files...${NC}"
cp -r "${SCRIPT_DIR}/library" "${LIB_DIR}/"
cp -r "${SCRIPT_DIR}/lib" "${LIB_DIR}/"
[[ -d "${SCRIPT_DIR}/converter" ]] && cp -r "${SCRIPT_DIR}/converter" "${LIB_DIR}/"
cp "${SCRIPT_DIR}/etc/audiobooks.conf.example" "${CONFIG_DIR}/"

# Create config file if it doesn't exist
if [[ ! -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
    echo -e "${BLUE}Creating configuration file...${NC}"
    cat > "${CONFIG_DIR}/audiobooks.conf" << EOF
# Audiobook Library Configuration
# Generated by install-user.sh on $(date +%Y-%m-%d)

# Data directories
AUDIOBOOKS_DATA="${DATA_DIR}"
AUDIOBOOKS_LIBRARY="\${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="\${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="\${AUDIOBOOKS_DATA}/Supplements"

# Application directories
AUDIOBOOKS_HOME="${LIB_DIR}"
AUDIOBOOKS_DATABASE="${STATE_DIR}/audiobooks.db"
AUDIOBOOKS_COVERS="\${AUDIOBOOKS_HOME}/library/web-v2/covers"
AUDIOBOOKS_CERTS="${CONFIG_DIR}/certs"
AUDIOBOOKS_LOGS="${LOG_DIR}"
AUDIOBOOKS_VENV="\${AUDIOBOOKS_HOME}/library/venv"

# Server settings
AUDIOBOOKS_API_PORT="5001"
AUDIOBOOKS_WEB_PORT="8090"
AUDIOBOOKS_BIND_ADDRESS="0.0.0.0"
AUDIOBOOKS_HTTPS_ENABLED="true"
EOF
fi

# Create wrapper scripts in ~/.local/bin
echo -e "${BLUE}Creating executable wrappers...${NC}"

# API server wrapper
cat > "${BIN_DIR}/audiobook-api" << EOF
#!/bin/bash
# Audiobook Library API Server
source "${LIB_DIR}/lib/audiobook-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/api.py" "\$@"
EOF
chmod 755 "${BIN_DIR}/audiobook-api"

# Web server wrapper
cat > "${BIN_DIR}/audiobooks-web" << EOF
#!/bin/bash
# Audiobook Library Web Server (HTTPS)
source "${LIB_DIR}/lib/audiobook-config.sh"
exec python3 "\${AUDIOBOOKS_HOME}/library/web-v2/https_server.py" "\$@"
EOF
chmod 755 "${BIN_DIR}/audiobooks-web"

# Scanner wrapper
cat > "${BIN_DIR}/audiobooks-scan" << EOF
#!/bin/bash
# Audiobook Library Scanner
source "${LIB_DIR}/lib/audiobook-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/scanner/scan_audiobooks.py" "\$@"
EOF
chmod 755 "${BIN_DIR}/audiobooks-scan"

# Database import wrapper
cat > "${BIN_DIR}/audiobooks-import" << EOF
#!/bin/bash
# Audiobook Library Database Import
source "${LIB_DIR}/lib/audiobook-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/import_to_db.py" "\$@"
EOF
chmod 755 "${BIN_DIR}/audiobooks-import"

# Config viewer
cat > "${BIN_DIR}/audiobooks-config" << EOF
#!/bin/bash
# Show audiobook library configuration
source "${LIB_DIR}/lib/audiobook-config.sh"
audiobooks_print_config
EOF
chmod 755 "${BIN_DIR}/audiobooks-config"

# Setup Python virtual environment if needed
if [[ ! -d "${LIB_DIR}/library/venv" ]]; then
    echo -e "${BLUE}Setting up Python virtual environment...${NC}"
    python3 -m venv "${LIB_DIR}/library/venv"
    "${LIB_DIR}/library/venv/bin/pip" install --quiet Flask
fi

# Generate SSL certificate if needed
CERT_DIR="${CONFIG_DIR}/certs"
if [[ ! -f "${CERT_DIR}/server.crt" ]]; then
    echo -e "${BLUE}Generating SSL certificate (3-year validity)...${NC}"
    mkdir -p "${CERT_DIR}"
    openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
        -nodes -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.crt" \
        -subj "/CN=localhost/O=Audiobooks/C=US" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        2>/dev/null
    chmod 600 "${CERT_DIR}/server.key"
    chmod 644 "${CERT_DIR}/server.crt"
    echo -e "${GREEN}  Certificate generated:${NC}"
    openssl x509 -in "${CERT_DIR}/server.crt" -noout -dates -subject | sed 's/^/    /'
fi

# Install systemd user services
if [[ "$INSTALL_SERVICES" == "true" ]]; then
    echo -e "${BLUE}Installing systemd user services...${NC}"

    # API service
    cat > "${SYSTEMD_DIR}/audiobook-api.service" << EOF
[Unit]
Description=Audiobooks Library API Server
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=default.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=${LIB_DIR}
Environment=AUDIOBOOKS_DATA=${DATA_DIR}
Environment=AUDIOBOOKS_LIBRARY=${DATA_DIR}/Library
Environment=AUDIOBOOKS_SOURCES=${DATA_DIR}/Sources
Environment=AUDIOBOOKS_SUPPLEMENTS=${DATA_DIR}/Supplements
Environment=AUDIOBOOKS_DATABASE=${STATE_DIR}/audiobooks.db
Environment=AUDIOBOOKS_COVERS=${LIB_DIR}/library/web-v2/covers
Environment=AUDIOBOOKS_CERTS=${CONFIG_DIR}/certs
Environment=AUDIOBOOKS_LOGS=${LOG_DIR}
Environment=AUDIOBOOKS_API_PORT=5001
Environment=AUDIOBOOKS_WEB_PORT=8090

ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:5001 >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobook-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
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
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=${LIB_DIR}
Environment=AUDIOBOOKS_WEB_PORT=8090
Environment=AUDIOBOOKS_CERTS=${CONFIG_DIR}/certs

ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:8090 >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobooks-web
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    # Target
    cat > "${SYSTEMD_DIR}/audiobooks.target" << EOF
[Unit]
Description=Audiobooks Library Services
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
Wants=audiobook-api.service audiobooks-web.service

[Install]
WantedBy=default.target
EOF

    # Reload systemd
    systemctl --user daemon-reload

    echo ""
    echo -e "${YELLOW}To enable services at login:${NC}"
    echo "  systemctl --user enable audiobook-api audiobooks-web"
    echo ""
    echo -e "${YELLOW}To start services now:${NC}"
    echo "  systemctl --user start audiobook-api audiobooks-web"
    echo ""
    echo -e "${YELLOW}To enable lingering (start services at boot without login):${NC}"
    echo "  loginctl enable-linger \$USER"
fi

# Add ~/.local/bin to PATH if not already
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo ""
    echo -e "${YELLOW}NOTE: Add ~/.local/bin to your PATH:${NC}"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
    echo "  # or for zsh:"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
fi

echo ""
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo ""
echo "Configuration: ${CONFIG_DIR}/audiobooks.conf"
echo "Data directory: ${DATA_DIR}"
echo "Logs: ${LOG_DIR}"
echo ""
echo "Commands available:"
echo "  audiobook-api      - Start API server"
echo "  audiobooks-web      - Start web server"
echo "  audiobooks-scan     - Scan audiobook library"
echo "  audiobooks-import   - Import to database"
echo "  audiobooks-config   - Show configuration"
echo ""
echo "Service management:"
echo "  systemctl --user status audiobook-api audiobooks-web"
echo "  systemctl --user restart audiobooks.target"
echo "  systemctl --user stop audiobooks.target"
echo "  journalctl --user -u audiobook-api -f"
echo ""
echo "Access the library at: https://localhost:8090"
echo ""
echo "NOTE: Your browser will show a security warning for the self-signed"
echo "certificate. Click 'Advanced' -> 'Proceed to localhost' to continue."
echo ""
