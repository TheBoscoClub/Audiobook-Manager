#!/bin/bash
# Audiobooks Library - Systemd User Service Installer
# Generates self-signed SSL certificate and installs user-level systemd services
#
# For system-wide installation, use install-system.sh instead.
#
# Usage: ./install-services.sh [--no-prompt]
#
# Options:
#   --no-prompt   Use configuration from config files without prompting

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Determine script location (project directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration
source "$SCRIPT_DIR/lib/audiobooks-config.sh"

echo -e "${GREEN}=== Audiobooks Library User Service Installer ===${NC}"
echo ""

# Parse arguments
NO_PROMPT=false
for arg in "$@"; do
    case $arg in
        --no-prompt)
            NO_PROMPT=true
            shift
            ;;
    esac
done

# Use config variables with optional override prompts
PROJECT_DIR="$AUDIOBOOKS_HOME"
AUDIOBOOK_DIR="$AUDIOBOOKS_LIBRARY"
SUPPLEMENTS_DIR="$AUDIOBOOKS_SUPPLEMENTS"

# Validate project directory
if [[ ! -f "$PROJECT_DIR/library/web-v2/https_server.py" ]]; then
    echo -e "${RED}Error: Invalid project directory: $PROJECT_DIR${NC}"
    echo "Expected to find: $PROJECT_DIR/library/web-v2/https_server.py"
    exit 1
fi

# Prompt for overrides if not --no-prompt
if [[ "$NO_PROMPT" != "true" ]]; then
    read -p "Audiobook library directory [$AUDIOBOOK_DIR]: " input
    AUDIOBOOK_DIR="${input:-$AUDIOBOOK_DIR}"

    read -p "Supplements (PDF) directory [$SUPPLEMENTS_DIR]: " input
    SUPPLEMENTS_DIR="${input:-$SUPPLEMENTS_DIR}"
fi

echo ""
echo "Configuration:"
echo "  Project directory:     $PROJECT_DIR"
echo "  Audiobook directory:   $AUDIOBOOK_DIR"
echo "  Supplements directory: $SUPPLEMENTS_DIR"
echo ""

# ============================================================================
# Step 1: Generate SSL Certificate
# ============================================================================
echo -e "${YELLOW}Step 1: Generating SSL certificate (3-year validity)...${NC}"

CERT_DIR="$AUDIOBOOKS_CERTS"
CERT_FILE="$CERT_DIR/server.crt"
KEY_FILE="$CERT_DIR/server.key"

mkdir -p "$CERT_DIR"

if [[ -f "$CERT_FILE" && -f "$KEY_FILE" ]]; then
    echo "Certificate already exists. Checking validity..."
    EXPIRY=$(openssl x509 -in "$CERT_FILE" -noout -enddate 2>/dev/null | cut -d= -f2)
    echo "  Current certificate expires: $EXPIRY"

    if [[ "$NO_PROMPT" != "true" ]]; then
        read -p "  Generate new certificate? [y/N]: " REGEN
        if [[ "$REGEN" != "y" && "$REGEN" != "Y" ]]; then
            echo "  Keeping existing certificate."
        else
            rm -f "$CERT_FILE" "$KEY_FILE"
        fi
    else
        echo "  Keeping existing certificate (no-prompt mode)."
    fi
fi

if [[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
    openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
        -nodes -keyout "$KEY_FILE" -out "$CERT_FILE" \
        -subj "/CN=localhost/O=Audiobooks/C=US" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        2>/dev/null

    chmod 600 "$KEY_FILE"
    chmod 644 "$CERT_FILE"

    echo -e "${GREEN}  Certificate generated:${NC}"
    openssl x509 -in "$CERT_FILE" -noout -dates -subject | sed 's/^/    /'
fi

echo ""

# ============================================================================
# Step 2: Create systemd user service directory
# ============================================================================
echo -e "${YELLOW}Step 2: Creating systemd user services...${NC}"

SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

# API Service - uses waitress WSGI server (production-ready)
cat > "$SYSTEMD_DIR/audiobooks-api.service" << EOF
[Unit]
Description=Audiobooks Library API Server (Waitress)
Documentation=https://github.com/greogory/audiobook-toolkit
After=default.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR/library/backend
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=$PROJECT_DIR
Environment=AUDIOBOOKS_DATA=$AUDIOBOOKS_DATA
Environment=AUDIOBOOKS_LIBRARY=$AUDIOBOOK_DIR
Environment=AUDIOBOOKS_SOURCES=$AUDIOBOOKS_SOURCES
Environment=AUDIOBOOKS_SUPPLEMENTS=$SUPPLEMENTS_DIR
Environment=AUDIOBOOKS_DATABASE=$AUDIOBOOKS_DATABASE
Environment=AUDIOBOOKS_COVERS=$AUDIOBOOKS_COVERS
Environment=AUDIOBOOKS_API_PORT=$AUDIOBOOKS_API_PORT
Environment=AUDIOBOOKS_WEB_PORT=$AUDIOBOOKS_WEB_PORT
Environment=AUDIOBOOKS_USE_WAITRESS=true
Environment=AUDIOBOOKS_BIND_ADDRESS=127.0.0.1

# Only start if port is not already in use
ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:$AUDIOBOOKS_API_PORT >/dev/null 2>&1'
ExecStart=$AUDIOBOOKS_VENV/bin/python api.py
Restart=on-failure
RestartSec=5
StandardOutput=append:$AUDIOBOOKS_DATA/logs/api.log
StandardError=append:$AUDIOBOOKS_DATA/logs/api-error.log

[Install]
WantedBy=default.target
EOF

echo "  Created: audiobooks-api.service"

# Proxy Service (HTTPS reverse proxy) - replaces audiobooks-web.service
cat > "$SYSTEMD_DIR/audiobooks-proxy.service" << EOF
[Unit]
Description=Audiobooks Library HTTPS Reverse Proxy
Documentation=https://github.com/greogory/audiobook-toolkit
After=audiobooks-api.service
Requires=audiobooks-api.service

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR/library/web-v2
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=$PROJECT_DIR
Environment=AUDIOBOOKS_WEB_PORT=$AUDIOBOOKS_WEB_PORT
Environment=AUDIOBOOKS_API_PORT=$AUDIOBOOKS_API_PORT
Environment=AUDIOBOOKS_CERTS=$CERT_DIR
Environment=AUDIOBOOKS_BIND_ADDRESS=0.0.0.0

# Only start if port is not already in use
ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:$AUDIOBOOKS_WEB_PORT >/dev/null 2>&1'
ExecStart=/usr/bin/python3 $PROJECT_DIR/library/web-v2/proxy_server.py
Restart=on-failure
RestartSec=5
StandardOutput=append:$AUDIOBOOKS_DATA/logs/proxy.log
StandardError=append:$AUDIOBOOKS_DATA/logs/proxy-error.log

[Install]
WantedBy=default.target
EOF

echo "  Created: audiobooks-proxy.service"

# HTTP Redirect Service (optional)
cat > "$SYSTEMD_DIR/audiobooks-redirect.service" << EOF
[Unit]
Description=Audiobooks Library HTTP to HTTPS Redirect
Documentation=https://github.com/greogory/audiobook-toolkit
After=audiobooks-proxy.service
Wants=audiobooks-proxy.service

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR/library/web-v2
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=$PROJECT_DIR
Environment=AUDIOBOOKS_WEB_PORT=$AUDIOBOOKS_WEB_PORT
Environment=AUDIOBOOKS_HTTP_REDIRECT_PORT=$AUDIOBOOKS_HTTP_REDIRECT_PORT
Environment=AUDIOBOOKS_BIND_ADDRESS=0.0.0.0

# Only start if port is not already in use
ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:$AUDIOBOOKS_HTTP_REDIRECT_PORT >/dev/null 2>&1'
ExecStart=/usr/bin/python3 $PROJECT_DIR/library/web-v2/redirect_server.py
Restart=on-failure
RestartSec=5
StandardOutput=append:$AUDIOBOOKS_DATA/logs/redirect.log
StandardError=append:$AUDIOBOOKS_DATA/logs/redirect-error.log

[Install]
WantedBy=default.target
EOF

echo "  Created: audiobooks-redirect.service (optional)"

# Target (groups all services)
cat > "$SYSTEMD_DIR/audiobooks.target" << EOF
[Unit]
Description=Audiobooks Library Services
Documentation=https://github.com/greogory/audiobook-toolkit
Wants=audiobooks-api.service audiobooks-proxy.service audiobooks-redirect.service

[Install]
WantedBy=default.target
EOF

echo "  Created: audiobooks.target"

echo ""

# ============================================================================
# Step 3: Reload and enable services
# ============================================================================
echo -e "${YELLOW}Step 3: Enabling systemd services...${NC}"

systemctl --user daemon-reload
systemctl --user enable audiobooks-api.service audiobooks-proxy.service audiobooks-redirect.service 2>&1 | grep -v "^$" || true

echo ""

# ============================================================================
# Step 4: Start services (optional)
# ============================================================================
if [[ "$NO_PROMPT" != "true" ]]; then
    read -p "Start services now? [Y/n]: " START_NOW
else
    START_NOW="Y"
fi

if [[ "$START_NOW" != "n" && "$START_NOW" != "N" ]]; then
    echo -e "${YELLOW}Starting services...${NC}"

    # Stop any existing processes on the ports
    if lsof -i:5001 >/dev/null 2>&1; then
        echo "  Stopping existing process on port 5001..."
        kill $(lsof -t -i:5001) 2>/dev/null || true
        sleep 1
    fi

    if lsof -i:8443 >/dev/null 2>&1; then
        echo "  Stopping existing process on port 8443..."
        kill $(lsof -t -i:8443) 2>/dev/null || true
        sleep 1
    fi

    if lsof -i:8080 >/dev/null 2>&1; then
        echo "  Stopping existing process on port 8080..."
        kill $(lsof -t -i:8080) 2>/dev/null || true
        sleep 1
    fi

    systemctl --user start audiobooks.target
    sleep 2

    echo ""
    echo -e "${GREEN}Service status:${NC}"
    systemctl --user status audiobooks-api.service --no-pager | head -5
    echo ""
    systemctl --user status audiobooks-proxy.service --no-pager | head -5
    echo ""
    systemctl --user status audiobooks-redirect.service --no-pager | head -5
fi

echo ""
echo -e "${GREEN}=== Installation Complete ===${NC}"
echo ""
echo "Access your library at: https://localhost:8443"
echo ""
echo "NOTE: Your browser will show a security warning because this is a"
echo "self-signed certificate. Click 'Advanced' -> 'Proceed to localhost'"
echo "to continue."
echo ""
echo "Enable auto-start at boot:"
echo "  loginctl enable-linger \$USER"
echo ""
echo "Management commands:"
echo "  systemctl --user status audiobooks.target"
echo "  systemctl --user restart audiobooks.target"
echo "  systemctl --user stop audiobooks.target"
echo "  journalctl --user -u audiobooks-api -f"
echo "  journalctl --user -u audiobooks-proxy -f"
echo ""
echo "Certificate location: $CERT_DIR"
echo "Certificate expires:  $(openssl x509 -in "$CERT_FILE" -noout -enddate | cut -d= -f2)"
