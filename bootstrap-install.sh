#!/usr/bin/env zsh
# =============================================================================
# bootstrap-install.sh - Bootstrap installer for Audiobook-Manager
# =============================================================================
# Downloads the latest release and runs the installer.
#
# Usage:
#   curl -sSL https://github.com/TheBoscoClub/Audiobook-Manager/raw/main/bootstrap-install.sh | bash
#   curl -sSL https://github.com/TheBoscoClub/Audiobook-Manager/raw/main/bootstrap-install.sh | bash -s -- --user
#   curl -sSL https://github.com/TheBoscoClub/Audiobook-Manager/raw/main/bootstrap-install.sh | bash -s -- --version 3.1.0
# =============================================================================

set -euo pipefail

# Configuration
GITHUB_REPO="TheBoscoClub/Audiobook-Manager"
GITHUB_API="https://api.github.com/repos/${GITHUB_REPO}"

# Colors (only if terminal supports them)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    BLUE='\033[0;34m'
    YELLOW='\033[1;33m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    BLUE=''
    YELLOW=''
    NC=''
fi

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

check_requirements() {
    local missing=()

    for cmd in curl tar; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required commands: ${missing[*]}"
        log_info "Please install them and try again"
        exit 1
    fi
}

get_latest_version() {
    local url="${GITHUB_API}/releases/latest"
    local response

    response=$(curl -sL "$url") || {
        log_error "Failed to fetch latest release info"
        exit 1
    }

    # Try jq first, fall back to grep/sed
    if command -v jq &>/dev/null; then
        echo "$response" | jq -r '.tag_name // empty' | sed 's/^v//'
    else
        echo "$response" | grep '"tag_name"' | head -1 | sed 's/.*"v\?\([^"]*\)".*/\1/'
    fi
}

get_tarball_url() {
    local version="$1"
    echo "https://github.com/${GITHUB_REPO}/releases/download/v${version}/audiobook-manager-${version}.tar.gz"
}

cleanup() {
    if [[ -n "${TEMP_DIR:-}" ]] && [[ -d "$TEMP_DIR" ]]; then
        log_info "Cleaning up..."
        rm -rf "$TEMP_DIR"
    fi
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
    local version=""
    local install_args=()

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version|-v)
                version="$2"
                shift 2
                ;;
            --help|-h)
                cat << EOF
Bootstrap installer for Audiobook-Manager

Usage:
  curl -sSL https://github.com/TheBoscoClub/Audiobook-Manager/raw/main/bootstrap-install.sh | bash
  curl -sSL ... | bash -s -- [OPTIONS]

Options:
  --version, -v VERSION    Install specific version (default: latest)
  --system                 System-wide installation (default)
  --user                   User installation (~/.local)
  --data-dir DIR           Set data directory
  --help, -h               Show this help

Examples:
  # Install latest version system-wide
  curl -sSL .../bootstrap-install.sh | bash

  # Install specific version for current user
  curl -sSL .../bootstrap-install.sh | bash -s -- --user --version 3.1.0
EOF
                exit 0
                ;;
            *)
                # Pass through to install.sh
                install_args+=("$1")
                shift
                ;;
        esac
    done

    echo ""
    echo "======================================"
    echo "  Audiobook Toolkit Bootstrap"
    echo "======================================"
    echo ""

    # Check requirements
    check_requirements

    # Get version
    if [[ -z "$version" ]]; then
        log_info "Fetching latest version..."
        version=$(get_latest_version)
        if [[ -z "$version" ]]; then
            log_error "Could not determine latest version"
            exit 1
        fi
    fi

    log_info "Installing version: ${version}"

    # Create temp directory
    TEMP_DIR=$(mktemp -d)
    trap cleanup EXIT

    # Download tarball
    local tarball_url
    tarball_url=$(get_tarball_url "$version")
    local tarball="${TEMP_DIR}/audiobook-manager-${version}.tar.gz"

    log_info "Downloading from: ${tarball_url}"
    if ! curl -sL -o "$tarball" "$tarball_url"; then
        log_error "Failed to download release"
        log_info "Check that version ${version} exists at:"
        log_info "  https://github.com/${GITHUB_REPO}/releases"
        exit 1
    fi

    # Verify download
    if [[ ! -s "$tarball" ]]; then
        log_error "Downloaded file is empty"
        exit 1
    fi

    # Extract
    log_info "Extracting..."
    cd "$TEMP_DIR"
    if ! tar -xzf "$tarball"; then
        log_error "Failed to extract tarball"
        exit 1
    fi

    # Find extracted directory
    local extract_dir
    extract_dir=$(find . -maxdepth 1 -type d -name "audiobook-manager-*" | head -1)
    if [[ -z "$extract_dir" ]] || [[ ! -d "$extract_dir" ]]; then
        log_error "Could not find extracted directory"
        exit 1
    fi

    # Run installer
    log_info "Running installer..."
    echo ""
    cd "$extract_dir"
    chmod +x install.sh

    if [[ ${#install_args[@]} -gt 0 ]]; then
        ./install.sh "${install_args[@]}"
    else
        ./install.sh
    fi

    echo ""
    log_success "Bootstrap complete!"
}

main "$@"
