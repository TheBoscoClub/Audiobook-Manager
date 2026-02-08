#!/usr/bin/env zsh
# =============================================================================
# create-release.sh - Build release tarballs for Audiobook-Manager
# =============================================================================
# Creates a distributable tarball containing all files needed for installation.
# The tarball can be uploaded to GitHub releases for standalone installation.
#
# Usage:
#   ./create-release.sh              # Build release tarball
#   ./create-release.sh --dry-run    # Show what would be included
#   ./create-release.sh --clean      # Remove build artifacts
# =============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Script location
SCRIPT_DIR="${0:A:h}"
BUILD_DIR="${SCRIPT_DIR}/build"

# GitHub repository info
GITHUB_REPO="TheBoscoClub/Audiobook-Manager"
GITHUB_API="https://api.github.com/repos/${GITHUB_REPO}"

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

get_version() {
    if [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
        cat "${SCRIPT_DIR}/VERSION" | tr -d '[:space:]'
    else
        log_error "VERSION file not found"
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# Build Functions
# -----------------------------------------------------------------------------

create_release_info() {
    local dest="$1"
    local version="$2"

    cat > "${dest}/.release-info" << EOF
{
  "github_repo": "${GITHUB_REPO}",
  "github_api": "${GITHUB_API}",
  "version": "${version}",
  "release_date": "$(date -Iseconds)",
  "build_host": "$(hostname)",
  "build_user": "$(whoami)"
}
EOF
}

build_release() {
    local version
    version=$(get_version)
    local release_name="audiobook-manager-${version}"
    local staging_dir="${BUILD_DIR}/${release_name}"
    local tarball="${BUILD_DIR}/${release_name}.tar.gz"

    log_info "Building release ${version}..."

    # Clean and create staging directory
    rm -rf "${staging_dir}"
    mkdir -p "${staging_dir}"

    # ---------------------------------------------------------------------
    # Copy files to staging
    # ---------------------------------------------------------------------

    log_info "Copying core files..."

    # VERSION file
    cp "${SCRIPT_DIR}/VERSION" "${staging_dir}/"

    # Main scripts (root level)
    for script in install.sh upgrade.sh migrate-api.sh; do
        if [[ -f "${SCRIPT_DIR}/${script}" ]]; then
            cp "${SCRIPT_DIR}/${script}" "${staging_dir}/"
            chmod 755 "${staging_dir}/${script}"
        fi
    done

    # Library (Python code)
    log_info "Copying library..."
    rsync -a --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='.pytest_cache' --exclude='*.db' \
        --exclude='testdata' --exclude='*.local.*' --exclude='.coverage' \
        --exclude='htmlcov' --exclude='.mypy_cache' --exclude='nohup.out' \
        --exclude='tests' --exclude='covers' --exclude='certs' \
        --exclude='.ruff_cache' --exclude='_archive' --exclude='.git' \
        "${SCRIPT_DIR}/library/" "${staging_dir}/library/"

    # Lib (shell config)
    log_info "Copying lib..."
    mkdir -p "${staging_dir}/lib"
    cp "${SCRIPT_DIR}/lib/audiobook-config.sh" "${staging_dir}/lib/"

    # Scripts (management scripts)
    log_info "Copying scripts..."
    rsync -a "${SCRIPT_DIR}/scripts/" "${staging_dir}/scripts/"

    # Systemd units
    log_info "Copying systemd units..."
    rsync -a "${SCRIPT_DIR}/systemd/" "${staging_dir}/systemd/"

    # Converter (AAXtoMP3)
    if [[ -d "${SCRIPT_DIR}/converter" ]]; then
        log_info "Copying converter..."
        rsync -a --exclude='*.aax' --exclude='*.aaxc' --exclude='*.mp3' \
            --exclude='*.opus' --exclude='*.m4b' \
            "${SCRIPT_DIR}/converter/" "${staging_dir}/converter/"
    fi

    # Config examples
    if [[ -d "${SCRIPT_DIR}/etc" ]]; then
        log_info "Copying config examples..."
        rsync -a "${SCRIPT_DIR}/etc/" "${staging_dir}/etc/"
    fi

    # Optional files
    for file in README.md LICENSE CHANGELOG.md CONTRIBUTING.md .env.example; do
        if [[ -f "${SCRIPT_DIR}/${file}" ]]; then
            cp "${SCRIPT_DIR}/${file}" "${staging_dir}/"
        fi
    done

    # ---------------------------------------------------------------------
    # Create release metadata
    # ---------------------------------------------------------------------

    log_info "Creating release metadata..."
    create_release_info "${staging_dir}" "${version}"

    # ---------------------------------------------------------------------
    # Create tarball
    # ---------------------------------------------------------------------

    log_info "Creating tarball..."
    mkdir -p "${BUILD_DIR}"
    tar -czf "${tarball}" -C "${BUILD_DIR}" "${release_name}"

    # Calculate checksum
    local checksum
    checksum=$(sha256sum "${tarball}" | cut -d' ' -f1)
    echo "${checksum}  ${release_name}.tar.gz" > "${tarball}.sha256"

    # ---------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------

    local size
    size=$(du -h "${tarball}" | cut -f1)

    echo ""
    log_success "Release ${version} built successfully!"
    echo ""
    echo "  Tarball: ${tarball}"
    echo "  Size:    ${size}"
    echo "  SHA256:  ${checksum}"
    echo ""
    echo "To upload to GitHub releases:"
    echo "  gh release upload v${version} ${tarball} ${tarball}.sha256"
    echo ""
}

dry_run() {
    local version
    version=$(get_version)

    log_info "Dry run - showing what would be included in release ${version}:"
    echo ""

    echo "=== Root files ==="
    for f in VERSION install.sh upgrade.sh migrate-api.sh README.md LICENSE CHANGELOG.md; do
        [[ -f "${SCRIPT_DIR}/${f}" ]] && echo "  ${f}"
    done

    echo ""
    echo "=== library/ (excluding venv, __pycache__, tests, db) ==="
    find "${SCRIPT_DIR}/library" -type f \
        ! -path "*/venv/*" ! -path "*/__pycache__/*" ! -name "*.pyc" \
        ! -path "*/.pytest_cache/*" ! -name "audiobooks.db" \
        ! -path "*/testdata/*" ! -name "*.local.*" \
        ! -path "*/.coverage" ! -path "*/htmlcov/*" ! -path "*/.mypy_cache/*" \
        | sed "s|${SCRIPT_DIR}/||" | head -50
    echo "  ..."

    echo ""
    echo "=== lib/ ==="
    ls -1 "${SCRIPT_DIR}/lib/" 2>/dev/null | sed 's/^/  /'

    echo ""
    echo "=== scripts/ ==="
    ls -1 "${SCRIPT_DIR}/scripts/" 2>/dev/null | sed 's/^/  /'

    echo ""
    echo "=== systemd/ ==="
    ls -1 "${SCRIPT_DIR}/systemd/" 2>/dev/null | sed 's/^/  /'

    if [[ -d "${SCRIPT_DIR}/converter" ]]; then
        echo ""
        echo "=== converter/ ==="
        find "${SCRIPT_DIR}/converter" -type f ! -name "*.aax" ! -name "*.aaxc" \
            ! -name "*.mp3" ! -name "*.opus" ! -name "*.m4b" \
            | sed "s|${SCRIPT_DIR}/||" | head -20
    fi

    if [[ -d "${SCRIPT_DIR}/etc" ]]; then
        echo ""
        echo "=== etc/ ==="
        ls -1 "${SCRIPT_DIR}/etc/" 2>/dev/null | sed 's/^/  /'
    fi
}

clean() {
    log_info "Cleaning build artifacts..."
    rm -rf "${BUILD_DIR}"
    log_success "Build directory removed"
}

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Build release tarballs for Audiobook-Manager.

Options:
  --dry-run    Show what would be included without building
  --clean      Remove build artifacts
  -h, --help   Show this help message

Examples:
  $(basename "$0")              # Build release tarball
  $(basename "$0") --dry-run    # Preview release contents
  $(basename "$0") --clean      # Clean build directory
EOF
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

main() {
    case "${1:-}" in
        --dry-run)
            dry_run
            ;;
        --clean)
            clean
            ;;
        -h|--help)
            show_help
            ;;
        "")
            build_release
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
