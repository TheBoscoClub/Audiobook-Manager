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
#   --from-github         Upgrade from latest GitHub release
#   --version VERSION     Install specific version (with --from-github)
#   --check               Check for available updates without upgrading
#   --backup              Create backup before upgrading
#   --target PATH         Target installation to upgrade
#   --switch-to-modular   Switch to modular Flask Blueprint architecture
#   --switch-to-monolithic  Switch to single-file architecture
#   --dry-run             Show what would be done without making changes
#   --help                Show this help message
#
# Examples:
#   # Upgrade from GitHub (recommended for standalone installations):
#   audiobooks-upgrade
#   ./upgrade.sh --from-github --target /opt/audiobooks
#
#   # Upgrade to specific version:
#   audiobooks-upgrade --version 3.2.0
#
#   # From local project directory:
#   ./upgrade.sh --from-project /path/to/Audiobook-Manager --target /opt/audiobooks
# =============================================================================

set -e

# Ensure files are created with proper permissions (readable by group/others)
umask 022

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
UPGRADE_SOURCE="project"  # "project" or "github"
REQUESTED_VERSION=""  # Specific version to install, or empty for latest

# GitHub configuration (loaded from .release-info or defaults)
GITHUB_REPO="greogory/Audiobook-Manager"
GITHUB_API="https://api.github.com/repos/greogory/Audiobook-Manager"

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
        "/raid0/ClaudeCodeProjects/Audiobook-Manager"
        "$HOME/Projects/Audiobook-Manager"
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
            local rsync_args=(
                -av --delete
                --exclude='venv'
                --exclude='__pycache__'
                --exclude='*.pyc'
                --exclude='.pytest_cache'
                --exclude='.coverage'
                --exclude='audiobooks.db'
                --exclude='audiobooks-dev.db'
                --exclude='testdata'
                --exclude='certs'
            )

            if [[ -n "$use_sudo" ]]; then
                sudo rsync "${rsync_args[@]}" "${project}/library/" "$target/library/"
            else
                rsync "${rsync_args[@]}" "${project}/library/" "$target/library/"
            fi
        fi
    fi

    # Upgrade converter
    if [[ -d "$target/converter" ]]; then
        echo -e "${BLUE}Upgrading converter...${NC}"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would sync converter/"
        else
            local rsync_args=(-av --delete --exclude='__pycache__')
            if [[ -n "$use_sudo" ]]; then
                sudo rsync "${rsync_args[@]}" "${project}/converter/" "$target/converter/"
            else
                rsync "${rsync_args[@]}" "${project}/converter/" "$target/converter/"
            fi
        fi
    fi

    # Upgrade systemd templates (stored in installation)
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

    # Update active systemd services and helper configuration
    if [[ -n "$use_sudo" ]] && [[ -d "/etc/systemd/system" ]]; then
        echo -e "${BLUE}Updating systemd service files...${NC}"

        # Copy new/updated service and path units
        for unit_file in "${project}/systemd/"*.service "${project}/systemd/"*.path "${project}/systemd/"*.timer; do
            if [[ -f "$unit_file" ]]; then
                local unit_name=$(basename "$unit_file")
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo "  [DRY-RUN] Would install: $unit_name"
                else
                    sudo cp "$unit_file" "/etc/systemd/system/${unit_name}"
                    sudo chmod 644 "/etc/systemd/system/${unit_name}"
                    echo "  Installed: $unit_name"
                fi
            fi
        done

        # Install/update tmpfiles.d configuration for privileged helper
        if [[ -f "${project}/systemd/audiobooks-tmpfiles.conf" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would update tmpfiles.d configuration"
            else
                sudo cp "${project}/systemd/audiobooks-tmpfiles.conf" /etc/tmpfiles.d/audiobooks.conf
                sudo chmod 644 /etc/tmpfiles.d/audiobooks.conf
                # Ensure runtime directory exists
                sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf 2>/dev/null || {
                    sudo mkdir -p /run/audiobooks
                    sudo chown audiobooks:audiobooks /run/audiobooks
                    sudo chmod 755 /run/audiobooks
                }
                echo "  Updated: tmpfiles.d/audiobooks.conf"
            fi
        fi

        # Reload systemd to pick up changes
        if [[ "$DRY_RUN" == "false" ]]; then
            sudo systemctl daemon-reload

            # Enable and start the privileged helper path unit if not already running
            if [[ -f "/etc/systemd/system/audiobooks-upgrade-helper.path" ]]; then
                sudo systemctl enable audiobooks-upgrade-helper.path 2>/dev/null || true
                sudo systemctl start audiobooks-upgrade-helper.path 2>/dev/null || true
            fi
        fi
    fi

    # Update VERSION file
    if [[ "$DRY_RUN" == "false" ]]; then
        if [[ -n "$use_sudo" ]]; then
            sudo cp "${project}/VERSION" "$target/" 2>/dev/null || true
        else
            cp "${project}/VERSION" "$target/" 2>/dev/null || true
        fi

        # Update version in utilities.html
        local new_version=$(cat "${project}/VERSION" 2>/dev/null)
        if [[ -n "$new_version" ]] && [[ -f "$target/library/web-v2/utilities.html" ]]; then
            echo -e "${BLUE}Updating version in utilities.html to v${new_version}...${NC}"
            if [[ -n "$use_sudo" ]]; then
                sudo sed -i "s/· v[0-9.]*\"/· v${new_version}\"/" "$target/library/web-v2/utilities.html"
            else
                sed -i "s/· v[0-9.]*\"/· v${new_version}\"/" "$target/library/web-v2/utilities.html"
            fi
        fi
    fi

    # Fix ownership of entire installation (cp/rsync don't set correct owner)
    if [[ -n "$use_sudo" ]]; then
        echo -e "${BLUE}Setting ownership to audiobooks:audiobooks...${NC}"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would run: chown -R audiobooks:audiobooks $target"
        else
            sudo chown -R audiobooks:audiobooks "$target"
        fi
    fi

    echo ""
    echo -e "${GREEN}=== Upgrade Complete ===${NC}"
    echo "New version: $(get_version "$project")"

    # Verify permissions after upgrade
    verify_installation_permissions "$target"
}

# -----------------------------------------------------------------------------
# Post-Upgrade Verification
# -----------------------------------------------------------------------------

stop_services() {
    # Stop audiobook services before upgrade
    local use_sudo="$1"

    echo -e "${BLUE}Stopping audiobook services...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would stop audiobooks services"
        return 0
    fi

    # Check if systemd services exist
    if systemctl list-units --type=service --all 2>/dev/null | grep -q "audiobooks"; then
        # System-level services
        if [[ -n "$use_sudo" ]]; then
            sudo systemctl stop audiobooks.target 2>/dev/null || true
            # Also stop individual services in case target doesn't exist
            for svc in audiobooks-api audiobooks-proxy audiobooks-redirect audiobooks-converter audiobooks-mover; do
                sudo systemctl stop "$svc" 2>/dev/null || true
            done
        fi
        echo -e "${GREEN}  Services stopped${NC}"
    elif systemctl --user list-units --type=service --all 2>/dev/null | grep -q "audiobooks"; then
        # User-level services
        systemctl --user stop audiobooks.target 2>/dev/null || true
        for svc in audiobooks-api audiobooks-proxy audiobooks-redirect; do
            systemctl --user stop "$svc" 2>/dev/null || true
        done
        echo -e "${GREEN}  User services stopped${NC}"
    else
        echo "  No active audiobook services found"
    fi
}

start_services() {
    # Start audiobook services after upgrade
    local use_sudo="$1"

    echo -e "${BLUE}Starting audiobook services...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would start audiobooks services"
        return 0
    fi

    # Reload systemd to pick up any service file changes
    if [[ -n "$use_sudo" ]]; then
        sudo systemctl daemon-reload
    else
        systemctl --user daemon-reload 2>/dev/null || true
    fi

    # Check if systemd services exist
    if systemctl list-units --type=service --all 2>/dev/null | grep -q "audiobooks"; then
        # System-level services
        if [[ -n "$use_sudo" ]]; then
            sudo systemctl start audiobooks.target 2>/dev/null || {
                # Fallback: start individual services
                for svc in audiobooks-api audiobooks-proxy audiobooks-redirect audiobooks-converter audiobooks-mover; do
                    sudo systemctl start "$svc" 2>/dev/null || true
                done
            }
        fi
        echo -e "${GREEN}  Services started${NC}"

        # Show service status summary
        echo ""
        echo -e "${BLUE}Service status:${NC}"
        for svc in audiobooks-api audiobooks-proxy audiobooks-converter audiobooks-mover; do
            local status
            status=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
            if [[ "$status" == "active" ]]; then
                echo -e "  $svc: ${GREEN}$status${NC}"
            else
                echo -e "  $svc: ${YELLOW}$status${NC}"
            fi
        done
    elif systemctl --user list-units --type=service --all 2>/dev/null | grep -q "audiobooks"; then
        # User-level services
        systemctl --user start audiobooks.target 2>/dev/null || {
            for svc in audiobooks-api audiobooks-proxy audiobooks-redirect; do
                systemctl --user start "$svc" 2>/dev/null || true
            done
        }
        echo -e "${GREEN}  User services started${NC}"
    else
        echo "  No audiobook services to start"
    fi
}

verify_installation_permissions() {
    # Verify that installed files have correct permissions and ownership
    local target_dir="$1"
    local issues_found=0

    echo ""
    echo -e "${BLUE}Verifying installation permissions and ownership...${NC}"

    # Determine if this is a system or user installation
    local is_system=false
    [[ "$target_dir" == /opt/* ]] || [[ "$target_dir" == /usr/* ]] && is_system=true

    # For system installations, verify ownership is audiobooks:audiobooks for ENTIRE installation
    if [[ "$is_system" == "true" ]]; then
        echo -n "  Checking ownership (audiobooks:audiobooks)... "
        # Check for files not owned by audiobooks user in the entire installation
        local wrong_owner
        wrong_owner=$(find "$target_dir" \( ! -user audiobooks -o ! -group audiobooks \) 2>/dev/null | wc -l)

        if [[ "$wrong_owner" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $wrong_owner files/dirs${NC}"
            sudo chown -R audiobooks:audiobooks "$target_dir"
            ((issues_found++))
        else
            echo -e "${GREEN}OK${NC}"
        fi
    fi

    # Check directory permissions (should be 755, not 700)
    echo -n "  Checking directory permissions... "
    local bad_dirs=$(find "$target_dir" -type d -perm 700 2>/dev/null | wc -l)
    if [[ "$bad_dirs" -gt 0 ]]; then
        echo -e "${YELLOW}fixing $bad_dirs directories${NC}"
        if [[ "$is_system" == "true" ]]; then
            sudo find "$target_dir" -type d -perm 700 -exec chmod 755 {} \;
        else
            find "$target_dir" -type d -perm 700 -exec chmod 755 {} \;
        fi
        ((issues_found++))
    else
        echo -e "${GREEN}OK${NC}"
    fi

    # Check file permissions (should be 644 for .py, .html, .css, .js, .sql, .json, .txt)
    echo -n "  Checking file permissions... "
    local bad_files=$(find "$target_dir" \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "*.sql" -o -name "*.json" -o -name "*.txt" \) \( -perm 600 -o -perm 700 -o -perm 711 \) 2>/dev/null | wc -l)
    if [[ "$bad_files" -gt 0 ]]; then
        echo -e "${YELLOW}fixing $bad_files files${NC}"
        if [[ "$is_system" == "true" ]]; then
            sudo find "$target_dir" \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "*.sql" -o -name "*.json" -o -name "*.txt" \) \( -perm 600 -o -perm 700 -o -perm 711 \) -exec chmod 644 {} \;
        else
            find "$target_dir" \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" -o -name "*.sql" -o -name "*.json" -o -name "*.txt" \) \( -perm 600 -o -perm 700 -o -perm 711 \) -exec chmod 644 {} \;
        fi
        ((issues_found++))
    else
        echo -e "${GREEN}OK${NC}"
    fi

    # Check executable permissions for shell scripts
    echo -n "  Checking executable permissions (.sh)... "
    local non_exec_scripts=$(find "$target_dir" -name "*.sh" ! -perm -u+x 2>/dev/null | wc -l)
    if [[ "$non_exec_scripts" -gt 0 ]]; then
        echo -e "${YELLOW}fixing $non_exec_scripts scripts${NC}"
        if [[ "$is_system" == "true" ]]; then
            sudo find "$target_dir" -name "*.sh" ! -perm -u+x -exec chmod +x {} \;
        else
            find "$target_dir" -name "*.sh" ! -perm -u+x -exec chmod +x {} \;
        fi
        ((issues_found++))
    else
        echo -e "${GREEN}OK${NC}"
    fi

    # Verify no symlinks point to project source directory
    echo -n "  Checking for project source dependencies... "
    local project_links=$(find /usr/local/bin -name "audiobooks-*" -type l -exec readlink {} \; 2>/dev/null | grep -c "ClaudeCodeProjects" || true)
    if [[ "$project_links" -gt 0 ]]; then
        echo -e "${RED}WARNING: $project_links binaries link to project source!${NC}"
        ((issues_found++))
    else
        echo -e "${GREEN}OK (independent)${NC}"
    fi

    if [[ "$issues_found" -gt 0 ]]; then
        echo -e "${YELLOW}  Fixed $issues_found permission/ownership issues.${NC}"
    else
        echo -e "${GREEN}  All permissions and ownership verified.${NC}"
    fi
}

# -----------------------------------------------------------------------------
# GitHub Release Functions
# -----------------------------------------------------------------------------

load_release_info() {
    # Load GitHub configuration from installation's .release-info file
    local target="$1"

    # Try multiple possible locations
    local info_file=""
    for loc in "$target/.release-info" "$target/../.release-info" "/opt/audiobooks/.release-info"; do
        if [[ -f "$loc" ]]; then
            info_file="$loc"
            break
        fi
    done

    if [[ -z "$info_file" ]]; then
        echo -e "${YELLOW}No .release-info found, using defaults${NC}"
        return 0
    fi

    # Parse JSON (jq if available, grep/sed fallback)
    if command -v jq &>/dev/null; then
        local repo=$(jq -r '.github_repo // empty' "$info_file" 2>/dev/null)
        local api=$(jq -r '.github_api // empty' "$info_file" 2>/dev/null)
        [[ -n "$repo" ]] && GITHUB_REPO="$repo"
        [[ -n "$api" ]] && GITHUB_API="$api"
    else
        # Fallback parsing without jq
        local repo=$(grep '"github_repo"' "$info_file" | sed 's/.*: *"\([^"]*\)".*/\1/')
        local api=$(grep '"github_api"' "$info_file" | sed 's/.*: *"\([^"]*\)".*/\1/')
        [[ -n "$repo" ]] && GITHUB_REPO="$repo"
        [[ -n "$api" ]] && GITHUB_API="$api"
    fi

    echo -e "${DIM:-}GitHub repo: ${GITHUB_REPO}${NC}"
}

get_latest_release() {
    # Query GitHub API for the latest release version
    local url="${GITHUB_API}/releases/latest"
    local response

    response=$(curl -sL --connect-timeout 10 "$url") || {
        echo -e "${RED}Failed to connect to GitHub API${NC}" >&2
        return 1
    }

    local version
    if command -v jq &>/dev/null; then
        version=$(echo "$response" | jq -r '.tag_name // empty' 2>/dev/null)
    else
        version=$(echo "$response" | grep '"tag_name"' | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/')
    fi

    # Remove 'v' prefix if present
    version="${version#v}"

    if [[ -z "$version" ]]; then
        echo -e "${RED}Could not determine latest version from GitHub${NC}" >&2
        return 1
    fi

    echo "$version"
}

get_release_tarball_url() {
    # Get download URL for a specific release version
    local version="$1"

    # Try with 'v' prefix first (v3.1.0), then without (3.1.0)
    for tag in "v${version}" "${version}"; do
        local url="${GITHUB_API}/releases/tags/${tag}"
        local response
        response=$(curl -sL --connect-timeout 10 "$url") || continue

        local tarball_url
        if command -v jq &>/dev/null; then
            tarball_url=$(echo "$response" | jq -r '.assets[] | select(.name | endswith(".tar.gz")) | .browser_download_url' 2>/dev/null | head -1)
        else
            # Fallback: construct URL from expected pattern
            tarball_url="https://github.com/${GITHUB_REPO}/releases/download/${tag}/audiobooks-${version}.tar.gz"
        fi

        if [[ -n "$tarball_url" ]]; then
            echo "$tarball_url"
            return 0
        fi
    done

    echo -e "${RED}Could not find release tarball for version ${version}${NC}" >&2
    return 1
}

download_and_extract_release() {
    # Download release tarball and extract to temp directory
    local url="$1"
    local temp_dir="$2"
    local tarball="${temp_dir}/release.tar.gz"

    # Status messages go to stderr so they don't pollute the return value
    echo -e "${BLUE}Downloading release...${NC}" >&2
    echo "  URL: $url" >&2

    if ! curl -sL --connect-timeout 30 -o "$tarball" "$url"; then
        echo -e "${RED}Failed to download release${NC}" >&2
        return 1
    fi

    # Verify download
    if [[ ! -s "$tarball" ]]; then
        echo -e "${RED}Downloaded file is empty${NC}" >&2
        return 1
    fi

    local size
    size=$(du -h "$tarball" | cut -f1)
    echo "  Downloaded: $size" >&2

    echo -e "${BLUE}Extracting...${NC}" >&2
    if ! tar -xzf "$tarball" -C "$temp_dir"; then
        echo -e "${RED}Failed to extract tarball${NC}" >&2
        return 1
    fi

    # Find the extracted directory
    local extract_dir
    extract_dir=$(find "$temp_dir" -maxdepth 1 -type d -name "audiobook-manager-*" | head -1)

    if [[ -z "$extract_dir" ]] || [[ ! -d "$extract_dir" ]]; then
        echo -e "${RED}Could not find extracted directory${NC}" >&2
        return 1
    fi

    # Only the path goes to stdout (for capture)
    echo "$extract_dir"
}

do_github_upgrade() {
    # Perform upgrade from GitHub release
    local target="$1"
    local version="${REQUESTED_VERSION:-latest}"

    echo -e "${BLUE}=== GitHub Upgrade Mode ===${NC}"
    echo ""

    # Load GitHub configuration from target installation
    load_release_info "$target"
    echo ""

    # Get current version
    local current_version
    current_version=$(get_version "$target")
    echo "Current version: $current_version"

    # Determine version to install
    local install_version
    if [[ "$version" == "latest" ]] || [[ -z "$version" ]]; then
        echo -e "${BLUE}Fetching latest version from GitHub...${NC}"
        install_version=$(get_latest_release) || {
            echo -e "${RED}Failed to get latest version${NC}"
            return 1
        }
        echo "Latest version:  $install_version"
    else
        install_version="$version"
        echo "Target version:  $install_version"
    fi

    # Check if upgrade needed
    if [[ "$current_version" == "$install_version" ]]; then
        echo ""
        echo -e "${GREEN}Already at version $install_version - no upgrade needed.${NC}"
        return 0
    fi

    # Version comparison
    set +e
    compare_versions "$current_version" "$install_version"
    local cmp_result=$?
    set -e

    if [[ $cmp_result -eq 1 ]]; then
        echo -e "${YELLOW}Warning: Target version ($install_version) is older than current ($current_version)${NC}"
        echo -n "Continue with downgrade? [y/N]: "
        read -r confirm
        if [[ "${confirm,,}" != "y" ]]; then
            echo "Cancelled."
            return 0
        fi
    fi

    echo ""

    # Check only mode
    if [[ "$CHECK_ONLY" == "true" ]]; then
        echo -e "${GREEN}Update available: $current_version → $install_version${NC}"
        return 0
    fi

    # Get download URL
    echo -e "${BLUE}Getting release information...${NC}"
    local tarball_url
    tarball_url=$(get_release_tarball_url "$install_version") || {
        echo -e "${RED}Failed to find release tarball${NC}"
        return 1
    }

    # Create temp directory
    local temp_dir
    temp_dir=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$temp_dir'" EXIT

    # Download and extract
    local release_dir
    release_dir=$(download_and_extract_release "$tarball_url" "$temp_dir") || {
        echo -e "${RED}Failed to download/extract release${NC}"
        return 1
    }

    echo ""
    [[ "$DRY_RUN" == "true" ]] && echo -e "${YELLOW}=== DRY RUN MODE ===${NC}" && echo ""

    # Confirm upgrade
    if [[ "$DRY_RUN" == "false" ]]; then
        read -r -p "Upgrade from $current_version to $install_version? [y/N]: " confirm
        if [[ "${confirm,,}" != "y" ]] && [[ "${confirm,,}" != "yes" ]]; then
            echo "Upgrade cancelled."
            return 0
        fi
        echo ""
    fi

    # Create backup if requested
    if [[ "$CREATE_BACKUP" == "true" ]]; then
        create_backup "$target"
        echo ""
    fi

    # Determine if we need sudo
    local use_sudo=""
    if [[ ! -w "$target" ]]; then
        use_sudo="sudo"
    fi

    # Stop services before upgrade
    stop_services "$use_sudo"
    echo ""

    # Use the existing do_upgrade function with the extracted release
    do_upgrade "$release_dir" "$target"

    echo ""

    # Start services after upgrade
    start_services "$use_sudo"

    echo ""
    echo -e "${GREEN}Successfully upgraded to version $install_version${NC}"
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
            UPGRADE_SOURCE="github"
            shift
            ;;
        --version)
            REQUESTED_VERSION="$2"
            shift 2
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

# GitHub upgrade mode - different flow
if [[ "$UPGRADE_SOURCE" == "github" ]]; then
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

    echo "Target: $TARGET_DIR"
    echo ""

    # Perform GitHub upgrade
    do_github_upgrade "$TARGET_DIR"
    exit $?
fi

# Project-based upgrade mode (original behavior)

# Find project directory
if [[ -z "$PROJECT_DIR" ]]; then
    echo -e "${BLUE}Looking for project directory...${NC}"
    PROJECT_DIR=$(find_project_dir) || {
        echo -e "${RED}Error: Cannot find project directory${NC}"
        echo "Please specify with --from-project PATH or use --from-github"
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
    # No upgrade needed - exit cleanly (matches GitHub mode behavior)
    exit 0
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

# Determine if we need sudo for service operations
use_sudo=""
if [[ ! -w "$TARGET_DIR" ]]; then
    use_sudo="true"
fi

# Stop services before upgrade
stop_services "$use_sudo"
echo ""

# Perform upgrade
do_upgrade "$PROJECT_DIR" "$TARGET_DIR"

# Start services after upgrade
echo ""
start_services "$use_sudo"

# Handle architecture switching if requested
if [[ -n "$SWITCH_ARCHITECTURE" ]]; then
    echo ""
    switch_architecture "$TARGET_DIR" "$SWITCH_ARCHITECTURE" "$use_sudo"
fi

# Show current architecture
echo ""
current_arch=$(detect_architecture "$TARGET_DIR")
echo -e "${BLUE}API Architecture:${NC} $current_arch"
echo ""
echo -e "${GREEN}Upgrade complete!${NC}"
