#!/bin/bash
# =============================================================================
# Audiobook Library - VM Deployment Script
# =============================================================================
# Deploys the project to a remote VM via SSH/SCP.
#
# Usage:
#   ./deploy-vm.sh [OPTIONS]
#
# Options:
#   --host HOST     VM hostname or IP (default: test-vm-cachyos)
#   --target PATH   Remote target path (default: /opt/audiobooks)
#   --dry-run       Show what would be deployed without making changes
#   --restart       Restart services after deployment
#   --help          Show this help message
#
# Examples:
#   ./deploy-vm.sh                              # Deploy to test-vm-cachyos
#   ./deploy-vm.sh --host 192.168.122.100       # Deploy to specific IP
#   ./deploy-vm.sh --restart                    # Deploy and restart services
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Script directory (source project)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="${SCRIPT_DIR}/VERSION"

# Defaults
VM_HOST="test-vm-cachyos"
REMOTE_TARGET="/opt/audiobooks"
DRY_RUN=false
RESTART_SERVICES=false

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

print_header() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════════╗"
    echo "║            Audiobook Library VM Deployment Script                 ║"
    echo "╚═══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

get_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        cat "$VERSION_FILE"
    else
        echo "unknown"
    fi
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
    head -25 "$0" | tail -20
    exit 0
}

# -----------------------------------------------------------------------------
# Parse Arguments
# -----------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            VM_HOST="$2"
            shift 2
            ;;
        --target)
            REMOTE_TARGET="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --restart)
            RESTART_SERVICES=true
            shift
            ;;
        --help|-h)
            show_help
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Main Deployment
# -----------------------------------------------------------------------------

print_header

VERSION=$(get_version)
log_info "Deploying version ${BOLD}$VERSION${NC} to ${BOLD}$VM_HOST:$REMOTE_TARGET${NC}"

if [[ "$DRY_RUN" == "true" ]]; then
    log_warning "DRY RUN MODE - No changes will be made"
fi

# Check SSH connectivity
log_info "Checking SSH connectivity..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" "echo 'SSH OK'" &>/dev/null; then
    log_error "Cannot connect to $VM_HOST via SSH"
    log_info "Make sure SSH agent is running: eval \$(ssh-agent -s) && ssh-add"
    exit 1
fi
log_success "SSH connection OK"

# Define files to deploy
declare -A DEPLOY_FILES=(
    # Backend
    ["library/auth/models.py"]="library/auth/"
    ["library/backend/api_modular/auth.py"]="library/backend/api_modular/"

    # Web frontend
    ["library/web-v2/index.html"]="library/web-v2/"
    ["library/web-v2/login.html"]="library/web-v2/"
    ["library/web-v2/register.html"]="library/web-v2/"
    ["library/web-v2/claim.html"]="library/web-v2/"
    ["library/web-v2/utilities.html"]="library/web-v2/"
    ["library/web-v2/admin.html"]="library/web-v2/"

    # JavaScript
    ["library/web-v2/js/library.js"]="library/web-v2/js/"
    ["library/web-v2/js/utilities.js"]="library/web-v2/js/"

    # CSS
    ["library/web-v2/css/auth.css"]="library/web-v2/css/"
    ["library/web-v2/css/utilities.css"]="library/web-v2/css/"
)

# Deploy files
log_info "Deploying files..."
DEPLOY_COUNT=0

for src_file in "${!DEPLOY_FILES[@]}"; do
    dest_dir="${DEPLOY_FILES[$src_file]}"
    full_src="${SCRIPT_DIR}/${src_file}"
    full_dest="${REMOTE_TARGET}/${dest_dir}"

    if [[ -f "$full_src" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] $src_file -> $VM_HOST:$full_dest"
        else
            # Ensure remote directory exists
            ssh "$VM_HOST" "mkdir -p '$full_dest'" 2>/dev/null || true
            scp -q "$full_src" "$VM_HOST:$full_dest"
            echo "  $src_file -> $full_dest"
        fi
        ((DEPLOY_COUNT++))
    else
        log_warning "File not found: $src_file"
    fi
done

log_success "Deployed $DEPLOY_COUNT files"

# Restart services if requested
if [[ "$RESTART_SERVICES" == "true" ]]; then
    log_info "Restarting services..."
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would restart audiobook-api service"
    else
        ssh "$VM_HOST" "sudo systemctl restart audiobook-api" 2>/dev/null || {
            log_warning "Could not restart audiobook-api (may need sudo password)"
        }
        log_success "Services restarted"
    fi
fi

# Summary
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Deployment complete!${NC}"
echo -e "  Host:    ${BOLD}$VM_HOST${NC}"
echo -e "  Target:  ${BOLD}$REMOTE_TARGET${NC}"
echo -e "  Version: ${BOLD}$VERSION${NC}"
echo -e "  Files:   ${BOLD}$DEPLOY_COUNT${NC}"
if [[ "$RESTART_SERVICES" == "false" ]]; then
    echo ""
    echo -e "${YELLOW}Note: Run with --restart to restart services after deployment${NC}"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
