#!/bin/bash
# =============================================================================
# Audiobook Library - Upgrade Script
# =============================================================================
# Upgrades an installed application from a source project or GitHub release.
#
# This script is designed to be run from OR against an installed application.
# It will pull updates and apply them while preserving user data and config.
#
# Usage:
#   ./upgrade.sh [OPTIONS]
#
# Options:
#   --from-project PATH   Upgrade from local project directory
#   --from-github         Upgrade from latest GitHub release (not implemented)
#   --check               Check for available updates without upgrading
#   --backup              Create backup before upgrading
#   --target PATH         Target installation to upgrade
#   --switch-to-modular   Switch to modular Flask Blueprint architecture
#   --switch-to-monolithic  Switch to single-file architecture
#   --dry-run             Show what would be done without making changes
#   --help                Show this help message
#
# Examples:
#   # From within the project directory:
#   ./upgrade.sh --target /raid0/Audiobooks
#
#   # From anywhere, specifying project:
#   ./upgrade.sh --from-project /raid0/ClaudeCodeProjects/Audiobooks --target /opt/audiobooks
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

# Script location - could be in project OR installed app
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Options
PROJECT_DIR=""
TARGET_DIR=""
DRY_RUN=false
CHECK_ONLY=false
CREATE_BACKUP=false
SWITCH_ARCHITECTURE=""  # modular or monolithic

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

print_header() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════════╗"
    echo "║             Audiobook Library Upgrade Script                      ║"
    echo "╚═══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

get_version() {
    local dir="$1"
    if [[ -f "$dir/VERSION" ]]; then
        cat "$dir/VERSION"
    else
        echo "unknown"
    fi
}

compare_versions() {
    # Compare two version strings
    # Returns: 0 if equal, 1 if v1 > v2, 2 if v1 < v2
    local v1="$1"
    local v2="$2"

    if [[ "$v1" == "$v2" ]]; then
        return 0
    fi

    # Simple comparison - could be enhanced for semantic versioning
    local sorted=$(printf '%s\n%s\n' "$v1" "$v2" | sort -V | head -n1)
    if [[ "$sorted" == "$v1" ]]; then
        return 2  # v1 < v2
    else
        return 1  # v1 > v2
    fi
}

find_project_dir() {
    # Try to find the project directory
    local candidates=(
        "$SCRIPT_DIR"
        "/raid0/ClaudeCodeProjects/Audiobooks"
        "$HOME/Projects/Audiobooks"
        "$HOME/audiobooks-project"
    )

    for dir in "${candidates[@]}"; do
        if [[ -f "$dir/install.sh" ]] && [[ -f "$dir/VERSION" ]] && [[ -d "$dir/library" ]]; then
            echo "$dir"
            return 0
        fi
    done

    return 1
}

find_installed_dir() {
    # Try to find the installed application
    local candidates=(
        "/raid0/Audiobooks"
        "/opt/audiobooks"
        "/usr/local/lib/audiobooks"
        "$HOME/.local/lib/audiobooks"
        "/srv/audiobooks"
    )

    for dir in "${candidates[@]}"; do
        if [[ -d "$dir/scripts" ]] || [[ -d "$dir/library" ]]; then
            echo "$dir"
            return 0
        fi
    done

    return 1
}

detect_architecture() {
    # Detect which API architecture is currently installed
    local target="$1"

    # Check wrapper script for api_server.py (modular) vs api.py (monolithic)
    local wrapper=""
    for w in "$target/bin/audiobooks-api" "/usr/local/bin/audiobooks-api" "$HOME/.local/bin/audiobooks-api"; do
        if [[ -f "$w" ]]; then
            wrapper="$w"
            break
        fi
    done

    if [[ -n "$wrapper" ]]; then
        if grep -q "api_server.py" "$wrapper" 2>/dev/null; then
            echo "modular"
        elif grep -q "api.py" "$wrapper" 2>/dev/null; then
            echo "monolithic"
        else
            echo "unknown"
        fi
    else
        echo "unknown"
    fi
}

switch_architecture() {
    local target="$1"
    local new_arch="$2"
    local use_sudo="$3"

    if [[ "$new_arch" != "modular" ]] && [[ "$new_arch" != "monolithic" ]]; then
        echo -e "${RED}Invalid architecture: $new_arch${NC}"
        return 1
    fi

    local current=$(detect_architecture "$target")

    if [[ "$current" == "$new_arch" ]]; then
        echo -e "${GREEN}Already using $new_arch architecture${NC}"
        return 0
    fi

    echo -e "${BLUE}Switching architecture: $current → $new_arch${NC}"

    local entry_point
    if [[ "$new_arch" == "modular" ]]; then
        entry_point="api_server.py"
    else
        entry_point="api.py"
    fi

    # Find and update wrapper scripts
    local wrappers=("$target/bin/audiobooks-api")
    if [[ "$use_sudo" == "true" ]]; then
        wrappers+=("/usr/local/bin/audiobooks-api")
    else
        wrappers+=("$HOME/.local/bin/audiobooks-api")
    fi

    for wrapper in "${wrappers[@]}"; do
        if [[ -f "$wrapper" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would update: $wrapper"
            else
                if [[ -n "$use_sudo" ]]; then
                    sudo sed -i "s|api_server\.py|${entry_point}|g; s|api\.py|${entry_point}|g" "$wrapper"
                else
                    sed -i "s|api_server\.py|${entry_point}|g; s|api\.py|${entry_point}|g" "$wrapper"
                fi
                echo "  Updated: $wrapper"
            fi
        fi
    done

    echo -e "${GREEN}Architecture switched to: $new_arch${NC}"
}

create_backup() {
    local target="$1"
    local backup_dir="${target}.backup.$(date +%Y%m%d-%H%M%S)"

    echo -e "${BLUE}Creating backup...${NC}"
    echo "  Source: $target"
    echo "  Backup: $backup_dir"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would create backup at $backup_dir"
        return 0
    fi

    # Determine if we need sudo
    if [[ -w "$target" ]]; then
        cp -a "$target" "$backup_dir"
    else
        sudo cp -a "$target" "$backup_dir"
    fi

    echo -e "${GREEN}  Backup created successfully${NC}"
}

check_for_updates() {
    local project="$1"
    local installed="$2"

    local proj_ver=$(get_version "$project")
    local inst_ver=$(get_version "$installed")

    echo "Version comparison:"
    echo "  Project version:   $proj_ver"
    echo "  Installed version: $inst_ver"
    echo ""

    compare_versions "$inst_ver" "$proj_ver"
    local result=$?

    case $result in
        0)
            echo -e "${GREEN}Versions are identical. No upgrade needed.${NC}"
            return 1
            ;;
        1)
            echo -e "${YELLOW}Warning: Installed version ($inst_ver) is newer than project ($proj_ver)${NC}"
            echo "This might indicate the installed application was modified directly."
            return 2
            ;;
        2)
            echo -e "${GREEN}Upgrade available: $inst_ver → $proj_ver${NC}"
            return 0
            ;;
    esac
}

do_upgrade() {
    local project="$1"
    local target="$2"
    local use_sudo=""

    # Check if we need sudo
    if [[ ! -w "$target" ]]; then
        use_sudo="sudo"
        echo -e "${YELLOW}Note: Using sudo (target not writable by current user)${NC}"
        if ! sudo -v; then
            echo -e "${RED}Error: Sudo access required${NC}"
            return 1
        fi
    fi

    echo -e "${GREEN}=== Upgrading Application ===${NC}"
    echo "Project: $project"
    echo "Target:  $target"
    echo ""

    # Upgrade scripts
    if [[ -d "$target/scripts" ]]; then
        echo -e "${BLUE}Upgrading scripts...${NC}"
        for script in "${project}/scripts/"*; do
            if [[ -f "$script" ]] && [[ "$(basename "$script")" != "__pycache__" ]]; then
                local script_name=$(basename "$script")
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo "  [DRY-RUN] Would update: $script_name"
                else
                    if [[ -n "$use_sudo" ]]; then
                        sudo cp "$script" "$target/scripts/"
                        sudo chmod +x "$target/scripts/$script_name"
                    else
                        cp "$script" "$target/scripts/"
                        chmod +x "$target/scripts/$script_name"
                    fi
                    echo "  Updated: $script_name"
                fi
            fi
        done
    fi

    # Upgrade lib
    if [[ -d "$target/lib" ]]; then
        echo -e "${BLUE}Upgrading configuration library...${NC}"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would update: audiobooks-config.sh"
        else
            if [[ -n "$use_sudo" ]]; then
                sudo cp "${project}/lib/audiobooks-config.sh" "$target/lib/"
            else
                cp "${project}/lib/audiobooks-config.sh" "$target/lib/"
            fi
            echo "  Updated: audiobooks-config.sh"
        fi
    fi

    # Upgrade library (web app, backend, etc.)
    if [[ -d "$target/library" ]]; then
        echo -e "${BLUE}Upgrading library components...${NC}"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would sync library/ (excluding venv, db, cache)"
        else
            local rsync_cmd="rsync -av --delete"
            rsync_cmd+=" --exclude='venv'"
            rsync_cmd+=" --exclude='__pycache__'"
            rsync_cmd+=" --exclude='*.pyc'"
            rsync_cmd+=" --exclude='.pytest_cache'"
            rsync_cmd+=" --exclude='.coverage'"
            rsync_cmd+=" --exclude='audiobooks.db'"
            rsync_cmd+=" --exclude='audiobooks-dev.db'"
            rsync_cmd+=" --exclude='testdata'"
            rsync_cmd+=" --exclude='certs'"

            if [[ -n "$use_sudo" ]]; then
                sudo $rsync_cmd "${project}/library/" "$target/library/"
            else
                $rsync_cmd "${project}/library/" "$target/library/"
            fi
        fi
    fi

    # Upgrade converter
    if [[ -d "$target/converter" ]]; then
        echo -e "${BLUE}Upgrading converter...${NC}"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would sync converter/"
        else
            local rsync_cmd="rsync -av --delete --exclude='__pycache__'"
            if [[ -n "$use_sudo" ]]; then
                sudo $rsync_cmd "${project}/converter/" "$target/converter/"
            else
                $rsync_cmd "${project}/converter/" "$target/converter/"
            fi
        fi
    fi

    # Upgrade systemd templates
    if [[ -d "$target/systemd" ]]; then
        echo -e "${BLUE}Upgrading systemd templates...${NC}"
        for file in "${project}/systemd/"*; do
            if [[ -f "$file" ]]; then
                local file_name=$(basename "$file")
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo "  [DRY-RUN] Would update: $file_name"
                else
                    if [[ -n "$use_sudo" ]]; then
                        sudo cp "$file" "$target/systemd/"
                    else
                        cp "$file" "$target/systemd/"
                    fi
                    echo "  Updated: $file_name"
                fi
            fi
        done
    fi

    # Update VERSION file
    if [[ "$DRY_RUN" == "false" ]]; then
        if [[ -n "$use_sudo" ]]; then
            sudo cp "${project}/VERSION" "$target/" 2>/dev/null || true
        else
            cp "${project}/VERSION" "$target/" 2>/dev/null || true
        fi
    fi

    echo ""
    echo -e "${GREEN}=== Upgrade Complete ===${NC}"
    echo "New version: $(get_version "$project")"
}

# -----------------------------------------------------------------------------
# Parse Command Line Arguments
# -----------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from-project)
            PROJECT_DIR="$2"
            shift 2
            ;;
        --from-github)
            echo -e "${RED}GitHub releases not yet implemented${NC}"
            exit 1
            ;;
        --target)
            TARGET_DIR="$2"
            shift 2
            ;;
        --check)
            CHECK_ONLY=true
            shift
            ;;
        --backup)
            CREATE_BACKUP=true
            shift
            ;;
        --switch-to-modular)
            SWITCH_ARCHITECTURE="modular"
            shift
            ;;
        --switch-to-monolithic)
            SWITCH_ARCHITECTURE="monolithic"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            head -30 "$0" | grep -E '^#' | sed 's/^# //' | sed 's/^#//'
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

# Find project directory
if [[ -z "$PROJECT_DIR" ]]; then
    echo -e "${BLUE}Looking for project directory...${NC}"
    PROJECT_DIR=$(find_project_dir) || {
        echo -e "${RED}Error: Cannot find project directory${NC}"
        echo "Please specify with --from-project PATH"
        exit 1
    }
fi

if [[ ! -d "$PROJECT_DIR" ]] || [[ ! -f "$PROJECT_DIR/install.sh" ]]; then
    echo -e "${RED}Error: Invalid project directory: $PROJECT_DIR${NC}"
    exit 1
fi

echo "Project: $PROJECT_DIR"

# Find target installation
if [[ -z "$TARGET_DIR" ]]; then
    echo -e "${BLUE}Looking for installed application...${NC}"
    TARGET_DIR=$(find_installed_dir) || {
        echo -e "${RED}Error: Cannot find installed application${NC}"
        echo "Please specify with --target PATH"
        exit 1
    }
fi

if [[ ! -d "$TARGET_DIR" ]]; then
    echo -e "${RED}Error: Invalid target directory: $TARGET_DIR${NC}"
    exit 1
fi

echo "Target:  $TARGET_DIR"
echo ""

# Check for updates
if ! check_for_updates "$PROJECT_DIR" "$TARGET_DIR"; then
    if [[ "$CHECK_ONLY" == "true" ]]; then
        exit 0
    fi
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
    exit 0
fi

echo ""
[[ "$DRY_RUN" == "true" ]] && echo -e "${YELLOW}=== DRY RUN MODE ===${NC}" && echo ""

# Confirm upgrade
if [[ "$DRY_RUN" == "false" ]]; then
    read -r -p "Proceed with upgrade? [y/N]: " confirm
    if [[ "${confirm,,}" != "y" ]] && [[ "${confirm,,}" != "yes" ]]; then
        echo "Upgrade cancelled."
        exit 0
    fi
    echo ""
fi

# Create backup if requested
if [[ "$CREATE_BACKUP" == "true" ]]; then
    create_backup "$TARGET_DIR"
    echo ""
fi

# Perform upgrade
do_upgrade "$PROJECT_DIR" "$TARGET_DIR"

# Handle architecture switching if requested
if [[ -n "$SWITCH_ARCHITECTURE" ]]; then
    echo ""
    # Determine if we need sudo
    use_sudo=""
    if [[ ! -w "$TARGET_DIR" ]]; then
        use_sudo="true"
    fi
    switch_architecture "$TARGET_DIR" "$SWITCH_ARCHITECTURE" "$use_sudo"
fi

# Show current architecture
echo ""
current_arch=$(detect_architecture "$TARGET_DIR")
echo -e "${BLUE}API Architecture:${NC} $current_arch"
echo ""
echo -e "${CYAN}Remember to restart services after upgrading:${NC}"
echo "  For system installation:"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl restart audiobooks.target"
echo ""
echo "  For user installation:"
echo "    systemctl --user daemon-reload"
echo "    systemctl --user restart audiobooks.target"
