#!/bin/bash
# =============================================================================
# Audiobook Library - Deployment Script
# =============================================================================
# Deploys the project source code to the installed application location.
#
# CRITICAL ARCHITECTURE:
# - PROJECT:     Git clone of Audiobook-Manager (wherever you clone it)
# - APPLICATION: Installed copy that runs independently of the project
#
# The APPLICATION must work even if the PROJECT directory is deleted.
# This script COPIES all necessary files - it creates NO symlinks to the project.
#
# Usage:
#   ./deploy.sh [--target PATH] [--dry-run] [--help]
#
# Options:
#   --target PATH   Deployment target (default: auto-detect from install type)
#   --dry-run       Show what would be deployed without making changes
#   --system        Deploy to system installation (/opt/audiobooks)
#   --user          Deploy to user installation (~/.local/lib/audiobooks)
#   --custom PATH   Deploy to custom application directory
#   --help          Show this help message
#
# Examples:
#   ./deploy.sh --system              # Update system installation
#   ./deploy.sh --user                # Update user installation
#   ./deploy.sh --custom /raid0/Audiobooks  # Update custom location
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

# Options
DRY_RUN=false
TARGET_TYPE=""
CUSTOM_TARGET=""

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

print_header() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════════╗"
    echo "║            Audiobook Library Deployment Script                    ║"
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

detect_installation() {
    # Detect existing installation type
    local found_system=false
    local found_user=false
    local found_custom=false

    # Check system installation
    if [[ -d "/opt/audiobooks/library" ]] || [[ -d "/usr/local/lib/audiobooks/library" ]]; then
        found_system=true
    fi

    # Check user installation
    if [[ -d "$HOME/.local/lib/audiobooks/library" ]]; then
        found_user=true
    fi

    # Check common custom locations
    for loc in "/raid0/Audiobooks" "/srv/audiobooks"; do
        if [[ -d "$loc/scripts" ]] && [[ -d "$loc/lib" ]]; then
            found_custom=true
            CUSTOM_TARGET="$loc"
        fi
    done

    echo "Detected installations:"
    [[ "$found_system" == "true" ]] && echo "  - System (/opt/audiobooks or /usr/local/lib/audiobooks)"
    [[ "$found_user" == "true" ]] && echo "  - User (~/.local/lib/audiobooks)"
    [[ "$found_custom" == "true" ]] && echo "  - Custom ($CUSTOM_TARGET)"

    if [[ "$found_system" == "false" ]] && [[ "$found_user" == "false" ]] && [[ "$found_custom" == "false" ]]; then
        echo "  (none detected)"
        return 1
    fi
    return 0
}

do_copy() {
    local src="$1"
    local dst="$2"
    local use_sudo="$3"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would copy: $src -> $dst"
    else
        if [[ "$use_sudo" == "sudo" ]]; then
            sudo cp -r "$src" "$dst"
        else
            cp -r "$src" "$dst"
        fi
        echo "  Copied: $(basename "$src")"
    fi
}

do_mkdir() {
    local dir="$1"
    local use_sudo="$2"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would create: $dir"
    else
        if [[ "$use_sudo" == "sudo" ]]; then
            sudo mkdir -p "$dir"
        else
            mkdir -p "$dir"
        fi
    fi
}

# -----------------------------------------------------------------------------
# Deployment Functions
# -----------------------------------------------------------------------------

deploy_to_system() {
    local target="/opt/audiobooks"
    local use_sudo="sudo"

    echo -e "${GREEN}=== Deploying to System Installation ===${NC}"
    echo "Target: $target"
    echo "Source: $SCRIPT_DIR"
    echo "Version: $(get_version)"
    echo ""

    # Check sudo
    if ! sudo -v; then
        echo -e "${RED}Error: Sudo access required for system deployment${NC}"
        return 1
    fi

    # Create target directories
    echo -e "${BLUE}Creating directories...${NC}"
    do_mkdir "$target" "$use_sudo"
    do_mkdir "$target/library" "$use_sudo"
    do_mkdir "$target/lib" "$use_sudo"
    do_mkdir "$target/converter" "$use_sudo"
    do_mkdir "$target/scripts" "$use_sudo"

    # Deploy library (web app, backend, scanner)
    echo -e "${BLUE}Deploying library components...${NC}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would sync: library/ -> $target/library/"
    else
        sudo rsync -av --delete \
            --exclude='venv' \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            --exclude='.pytest_cache' \
            --exclude='.coverage' \
            --exclude='audiobooks.db' \
            --exclude='audiobooks-dev.db' \
            --exclude='testdata' \
            "${SCRIPT_DIR}/library/" "$target/library/"
    fi

    # Deploy lib (config library)
    echo -e "${BLUE}Deploying configuration library...${NC}"
    do_copy "${SCRIPT_DIR}/lib/audiobooks-config.sh" "$target/lib/" "$use_sudo"

    # Deploy converter
    echo -e "${BLUE}Deploying converter...${NC}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would sync: converter/ -> $target/converter/"
    else
        sudo rsync -av --delete \
            --exclude='__pycache__' \
            "${SCRIPT_DIR}/converter/" "$target/converter/"
    fi

    # Deploy scripts
    echo -e "${BLUE}Deploying scripts...${NC}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would sync scripts/"
    else
        for script in "${SCRIPT_DIR}/scripts/"*; do
            if [[ -f "$script" ]]; then
                sudo cp "$script" "$target/scripts/"
                sudo chmod +x "$target/scripts/$(basename "$script")"
            fi
        done
    fi

    # Deploy systemd templates
    echo -e "${BLUE}Deploying systemd templates...${NC}"
    do_mkdir "$target/systemd" "$use_sudo"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would copy systemd files"
    else
        sudo cp "${SCRIPT_DIR}/systemd/"* "$target/systemd/" 2>/dev/null || true
    fi

    # Create fresh venv if it doesn't exist
    if [[ ! -d "$target/library/venv" ]] && [[ "$DRY_RUN" == "false" ]]; then
        echo -e "${BLUE}Creating Python virtual environment...${NC}"
        sudo python3 -m venv "$target/library/venv"
        sudo "$target/library/venv/bin/pip" install --quiet flask flask-cors mutagen
    fi

    # Set permissions
    if [[ "$DRY_RUN" == "false" ]]; then
        echo -e "${BLUE}Setting permissions...${NC}"
        sudo chown -R root:audiobooks "$target" 2>/dev/null || sudo chown -R root:root "$target"
        sudo chmod -R g+rX "$target"
    fi

    # Update /usr/local/lib/audiobooks if it exists (for compatibility)
    if [[ -d "/usr/local/lib/audiobooks" ]]; then
        echo -e "${BLUE}Updating /usr/local/lib/audiobooks...${NC}"
        do_copy "$target/lib/audiobooks-config.sh" "/usr/local/lib/audiobooks/" "$use_sudo"
    fi

    echo ""
    echo -e "${GREEN}=== System Deployment Complete ===${NC}"
    echo "Deployed version: $(get_version)"
    echo ""
    echo "Next steps:"
    echo "  sudo systemctl daemon-reload"
    echo "  sudo systemctl restart audiobooks.target"
}

deploy_to_user() {
    local target="$HOME/.local/lib/audiobooks"

    echo -e "${GREEN}=== Deploying to User Installation ===${NC}"
    echo "Target: $target"
    echo "Source: $SCRIPT_DIR"
    echo "Version: $(get_version)"
    echo ""

    # Create target directories
    echo -e "${BLUE}Creating directories...${NC}"
    mkdir -p "$target/library"
    mkdir -p "$target/lib"
    mkdir -p "$target/converter"

    # Deploy library
    echo -e "${BLUE}Deploying library components...${NC}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would sync: library/ -> $target/library/"
    else
        rsync -av --delete \
            --exclude='venv' \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            --exclude='.pytest_cache' \
            --exclude='.coverage' \
            --exclude='audiobooks.db' \
            --exclude='audiobooks-dev.db' \
            --exclude='testdata' \
            "${SCRIPT_DIR}/library/" "$target/library/"
    fi

    # Deploy lib
    echo -e "${BLUE}Deploying configuration library...${NC}"
    cp "${SCRIPT_DIR}/lib/audiobooks-config.sh" "$target/lib/"

    # Deploy converter
    echo -e "${BLUE}Deploying converter...${NC}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would sync: converter/ -> $target/converter/"
    else
        rsync -av --delete \
            --exclude='__pycache__' \
            "${SCRIPT_DIR}/converter/" "$target/converter/"
    fi

    # Create fresh venv if it doesn't exist
    if [[ ! -d "$target/library/venv" ]] && [[ "$DRY_RUN" == "false" ]]; then
        echo -e "${BLUE}Creating Python virtual environment...${NC}"
        python3 -m venv "$target/library/venv"
        "$target/library/venv/bin/pip" install --quiet flask flask-cors mutagen
    fi

    echo ""
    echo -e "${GREEN}=== User Deployment Complete ===${NC}"
    echo "Deployed version: $(get_version)"
    echo ""
    echo "Next steps:"
    echo "  systemctl --user daemon-reload"
    echo "  systemctl --user restart audiobooks.target"
}

deploy_to_custom() {
    local target="$1"
    local use_sudo=""

    # Check if we need sudo for this target
    if [[ ! -w "$target" ]] && [[ -d "$target" ]]; then
        use_sudo="sudo"
    elif [[ ! -w "$(dirname "$target")" ]]; then
        use_sudo="sudo"
    fi

    echo -e "${GREEN}=== Deploying to Custom Location ===${NC}"
    echo "Target: $target"
    echo "Source: $SCRIPT_DIR"
    echo "Version: $(get_version)"
    [[ -n "$use_sudo" ]] && echo "Mode: sudo (target not writable by current user)"
    echo ""

    # Create target directories
    echo -e "${BLUE}Creating directories...${NC}"
    do_mkdir "$target" "$use_sudo"
    do_mkdir "$target/scripts" "$use_sudo"
    do_mkdir "$target/lib" "$use_sudo"

    # Deploy scripts
    echo -e "${BLUE}Deploying scripts...${NC}"
    for script in "${SCRIPT_DIR}/scripts/"*; do
        if [[ -f "$script" ]] && [[ "$(basename "$script")" != "__pycache__" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would copy: $(basename "$script")"
            else
                if [[ -n "$use_sudo" ]]; then
                    sudo cp "$script" "$target/scripts/"
                    sudo chmod +x "$target/scripts/$(basename "$script")"
                else
                    cp "$script" "$target/scripts/"
                    chmod +x "$target/scripts/$(basename "$script")"
                fi
                echo "  Deployed: $(basename "$script")"
            fi
        fi
    done

    # Deploy lib
    echo -e "${BLUE}Deploying configuration library...${NC}"
    do_copy "${SCRIPT_DIR}/lib/audiobooks-config.sh" "$target/lib/" "$use_sudo"

    # Deploy systemd templates if directory exists
    if [[ -d "$target/systemd" ]]; then
        echo -e "${BLUE}Deploying systemd templates...${NC}"
        for file in "${SCRIPT_DIR}/systemd/"*; do
            if [[ -f "$file" ]]; then
                do_copy "$file" "$target/systemd/" "$use_sudo"
            fi
        done
    fi

    echo ""
    echo -e "${GREEN}=== Custom Deployment Complete ===${NC}"
    echo "Deployed version: $(get_version)"
    echo "Target: $target"
    echo ""
    echo "Deployed components:"
    echo "  $target/scripts/  - Executable scripts"
    echo "  $target/lib/      - Configuration library"
}

# -----------------------------------------------------------------------------
# Parse Command Line Arguments
# -----------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --system)
            TARGET_TYPE="system"
            shift
            ;;
        --user)
            TARGET_TYPE="user"
            shift
            ;;
        --custom)
            TARGET_TYPE="custom"
            CUSTOM_TARGET="$2"
            shift 2
            ;;
        --target)
            # Legacy option - treat as custom
            TARGET_TYPE="custom"
            CUSTOM_TARGET="$2"
            shift 2
            ;;
        --help|-h)
            head -35 "$0" | grep -E '^#' | sed 's/^# //' | sed 's/^#//'
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

print_header

# Verify we're in the project directory
if [[ ! -f "${SCRIPT_DIR}/install.sh" ]] || [[ ! -d "${SCRIPT_DIR}/library" ]]; then
    echo -e "${RED}Error: This script must be run from the Audiobooks project directory${NC}"
    exit 1
fi

echo "Project: $SCRIPT_DIR"
echo "Version: $(get_version)"
echo ""

# If no target specified, detect and prompt
if [[ -z "$TARGET_TYPE" ]]; then
    echo -e "${BLUE}Detecting existing installations...${NC}"
    detect_installation
    echo ""

    echo "Select deployment target:"
    echo "  1) System installation (/opt/audiobooks)"
    echo "  2) User installation (~/.local/lib/audiobooks)"
    echo "  3) Custom location"
    echo "  4) Cancel"
    echo ""

    read -r -p "Enter choice [1-4]: " choice
    case "$choice" in
        1) TARGET_TYPE="system" ;;
        2) TARGET_TYPE="user" ;;
        3)
            TARGET_TYPE="custom"
            read -r -p "Enter custom path: " CUSTOM_TARGET
            ;;
        4)
            echo "Deployment cancelled."
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            exit 1
            ;;
    esac
fi

echo ""
[[ "$DRY_RUN" == "true" ]] && echo -e "${YELLOW}=== DRY RUN MODE - No changes will be made ===${NC}" && echo ""

# Execute deployment
case "$TARGET_TYPE" in
    system)
        deploy_to_system
        ;;
    user)
        deploy_to_user
        ;;
    custom)
        if [[ -z "$CUSTOM_TARGET" ]]; then
            echo -e "${RED}Error: Custom target path required${NC}"
            exit 1
        fi
        deploy_to_custom "$CUSTOM_TARGET"
        ;;
esac

echo ""
echo -e "${CYAN}Deployment finished.${NC}"
echo ""
echo "IMPORTANT: The installed application is now independent of this project."
echo "You can safely move or delete this project directory without affecting"
echo "the deployed application."
