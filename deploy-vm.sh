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
#   --user USER     SSH username (default: testuser)
#   --target PATH   Remote target path (default: /opt/audiobooks)
#   --full          Deploy ALL project files (default: only recent changes)
#   --dry-run       Show what would be deployed without making changes
#   --restart       Restart services after deployment
#   --help          Show this help message
#
# Examples:
#   ./deploy-vm.sh                              # Deploy recent changes only
#   ./deploy-vm.sh --full                       # Deploy entire project
#   ./deploy-vm.sh --full --restart             # Full deploy and restart
#   ./deploy-vm.sh --host 192.168.122.100       # Deploy to specific IP
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
VM_USER="testuser"
REMOTE_TARGET="/opt/audiobooks"
DRY_RUN=false
FULL_DEPLOY=false
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
    head -27 "$0" | tail -22
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
        --user)
            VM_USER="$2"
            shift 2
            ;;
        --target)
            REMOTE_TARGET="$2"
            shift 2
            ;;
        --full)
            FULL_DEPLOY=true
            shift
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

# Build SSH target from user and host
SSH_TARGET="${VM_USER}@${VM_HOST}"

print_header

VERSION=$(get_version)
DEPLOY_MODE="recent changes"
[[ "$FULL_DEPLOY" == "true" ]] && DEPLOY_MODE="full project"

log_info "Deploying version ${BOLD}$VERSION${NC} to ${BOLD}$SSH_TARGET:$REMOTE_TARGET${NC}"
log_info "Mode: ${BOLD}$DEPLOY_MODE${NC}"

if [[ "$DRY_RUN" == "true" ]]; then
    log_warning "DRY RUN MODE - No changes will be made"
fi

# Check SSH connectivity
log_info "Checking SSH connectivity..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$SSH_TARGET" "echo 'SSH OK'" &>/dev/null; then
    log_error "Cannot connect to $SSH_TARGET via SSH"
    log_info "Make sure SSH agent is running: eval \$(ssh-agent -s) && ssh-add"
    exit 1
fi
log_success "SSH connection OK"

# -----------------------------------------------------------------------------
# Define Files to Deploy
# -----------------------------------------------------------------------------

# Recent changes only (default) - auth/user management work
declare -A QUICK_DEPLOY_FILES=(
    # Backend
    ["library/auth/models.py"]="library/auth/"
    ["library/auth/database.py"]="library/auth/"
    ["library/auth/__init__.py"]="library/auth/"
    ["library/backend/api_modular/auth.py"]="library/backend/api_modular/"

    # Web frontend HTML
    ["library/web-v2/index.html"]="library/web-v2/"
    ["library/web-v2/login.html"]="library/web-v2/"
    ["library/web-v2/register.html"]="library/web-v2/"
    ["library/web-v2/claim.html"]="library/web-v2/"
    ["library/web-v2/utilities.html"]="library/web-v2/"
    ["library/web-v2/admin.html"]="library/web-v2/"
    ["library/web-v2/contact.html"]="library/web-v2/"

    # JavaScript
    ["library/web-v2/js/library.js"]="library/web-v2/js/"
    ["library/web-v2/js/utilities.js"]="library/web-v2/js/"

    # CSS
    ["library/web-v2/css/auth.css"]="library/web-v2/css/"
    ["library/web-v2/css/utilities.css"]="library/web-v2/css/"
    ["library/web-v2/css/library.css"]="library/web-v2/css/"
    ["library/web-v2/css/notifications.css"]="library/web-v2/css/"
    ["library/web-v2/css/modals.css"]="library/web-v2/css/"
)

deploy_quick() {
    echo -e "${BLUE}[INFO]${NC} Deploying recent changes..." >&2
    local count=0

    for src_file in "${!QUICK_DEPLOY_FILES[@]}"; do
        dest_dir="${QUICK_DEPLOY_FILES[$src_file]}"
        full_src="${SCRIPT_DIR}/${src_file}"
        full_dest="${REMOTE_TARGET}/${dest_dir}"

        if [[ -f "$full_src" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] $src_file -> $SSH_TARGET:$full_dest" >&2
            else
                ssh "$SSH_TARGET" "sudo mkdir -p '$full_dest'" 2>/dev/null || true
                # Use rsync with sudo for permissions
                rsync -az --rsync-path="sudo rsync" "$full_src" "$SSH_TARGET:$full_dest"
                echo "  $src_file" >&2
            fi
            ((count++))
        fi
    done

    echo "$count"
}

deploy_full() {
    echo -e "${BLUE}[INFO]${NC} Deploying full project..." >&2
    local count=0

    # Directories to sync
    local SYNC_DIRS=(
        "library/auth"
        "library/backend"
        "library/scanner"
        "library/web-v2"
        "library/tests"
        "scripts"
        "lib"
    )

    # Individual files in root
    local ROOT_FILES=(
        "VERSION"
        "requirements.txt"
        "config.env.example"
    )

    # Sync directories using rsync for efficiency
    for dir in "${SYNC_DIRS[@]}"; do
        local src_dir="${SCRIPT_DIR}/${dir}"
        local dest_dir="${REMOTE_TARGET}/${dir}"

        if [[ -d "$src_dir" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] $dir/ -> $SSH_TARGET:$dest_dir/" >&2
                # Count files for dry-run
                local dir_count=$(find "$src_dir" -type f | wc -l)
                ((count += dir_count))
            else
                ssh "$SSH_TARGET" "sudo mkdir -p '$dest_dir'" 2>/dev/null || true
                # Use rsync with sudo for permissions
                if command -v rsync &>/dev/null; then
                    rsync -az --delete --rsync-path="sudo rsync" \
                        --exclude='__pycache__' \
                        --exclude='*.pyc' \
                        --exclude='.pytest_cache' \
                        --exclude='*.db' \
                        --exclude='*.log' \
                        "$src_dir/" "$SSH_TARGET:$dest_dir/"
                else
                    # Fallback: copy to temp, then sudo mv
                    local tmp_dir="/tmp/audiobook-deploy-$$"
                    ssh "$SSH_TARGET" "mkdir -p '$tmp_dir'"
                    scp -rq "$src_dir/"* "$SSH_TARGET:$tmp_dir/"
                    ssh "$SSH_TARGET" "sudo cp -r '$tmp_dir/'* '$dest_dir/' && rm -rf '$tmp_dir'"
                fi
                local dir_count=$(find "$src_dir" -type f \
                    ! -name '*.pyc' \
                    ! -path '*__pycache__*' \
                    ! -path '*.pytest_cache*' \
                    ! -name '*.db' \
                    ! -name '*.log' | wc -l)
                ((count += dir_count))
                echo "  $dir/ ($dir_count files)" >&2
            fi
        fi
    done

    # Copy root files
    for file in "${ROOT_FILES[@]}"; do
        local src_file="${SCRIPT_DIR}/${file}"
        if [[ -f "$src_file" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] $file -> $SSH_TARGET:$REMOTE_TARGET/" >&2
            else
                rsync -az --rsync-path="sudo rsync" "$src_file" "$SSH_TARGET:$REMOTE_TARGET/"
                echo "  $file" >&2
            fi
            ((count++))
        fi
    done

    echo "$count"
}

# Execute deployment
if [[ "$FULL_DEPLOY" == "true" ]]; then
    DEPLOY_COUNT=$(deploy_full)
else
    DEPLOY_COUNT=$(deploy_quick)
fi

log_success "Deployed $DEPLOY_COUNT files"

# Restart services if requested
if [[ "$RESTART_SERVICES" == "true" ]]; then
    log_info "Restarting services..."
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would restart audiobook-api service"
    else
        ssh "$SSH_TARGET" "sudo systemctl restart audiobook-api" 2>/dev/null || {
            log_warning "Could not restart audiobook-api (may need sudo password)"
        }
        log_success "Services restarted"
    fi
fi

# Summary
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Deployment complete!${NC}"
echo -e "  Host:    ${BOLD}$SSH_TARGET${NC}"
echo -e "  Target:  ${BOLD}$REMOTE_TARGET${NC}"
echo -e "  Version: ${BOLD}$VERSION${NC}"
echo -e "  Mode:    ${BOLD}$DEPLOY_MODE${NC}"
echo -e "  Files:   ${BOLD}$DEPLOY_COUNT${NC}"
if [[ "$RESTART_SERVICES" == "false" ]]; then
    echo ""
    echo -e "${YELLOW}Note: Run with --restart to restart services after deployment${NC}"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
