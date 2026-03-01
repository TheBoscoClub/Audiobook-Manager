#!/bin/bash
# =============================================================================
# Vox Grotto - Upgrade Script
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
#   --remote HOST         Deploy to remote host via SSH (requires --from-project)
#   --user USER           SSH username for remote deploy (default: claude)
#   --yes, -y             Non-interactive mode (skip all confirmation prompts)
#   --switch-to-modular   Switch to modular Flask Blueprint architecture
#   --switch-to-monolithic  Switch to single-file architecture
#   --force               Force upgrade even if versions are identical
#   --dry-run             Show what would be done without making changes
#   --help                Show this help message
#
# Examples:
#   # Upgrade from GitHub (recommended for standalone installations):
#   grotto-upgrade
#   ./upgrade.sh --from-github --target /opt/audiobooks
#
#   # Upgrade to specific version:
#   grotto-upgrade --version 3.2.0
#
#   # From local project directory:
#   ./upgrade.sh --from-project /path/to/Audiobook-Manager --target /opt/audiobooks
#
#   # Deploy to remote VM (full lifecycle: stop, backup, sync, venv, restart):
#   ./upgrade.sh --from-project . --remote 192.168.122.104 --yes
#
#   # Non-interactive local upgrade:
#   ./upgrade.sh --from-project . --target /opt/audiobooks --yes
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

# Safety net: restart services if script dies after stopping them.
# set -e can kill the script mid-upgrade, leaving services dead with no 502
# recovery. This trap ensures services always come back up.
_SERVICES_STOPPED=false
_SERVICES_USE_SUDO=""
_cleanup_on_exit() {
    if [[ "$_SERVICES_STOPPED" == "true" ]]; then
        echo ""
        echo -e "${YELLOW}Script exited before services were restarted — restarting now...${NC}"
        start_services "$_SERVICES_USE_SUDO" 2>/dev/null || {
            echo -e "${RED}CRITICAL: Failed to restart services. Run manually:${NC}"
            echo -e "${RED}  sudo systemctl start audiobook-api audiobook-proxy${NC}"
        }
    fi
}
trap _cleanup_on_exit EXIT

# Script location - could be in project OR installed app
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Options
PROJECT_DIR=""
TARGET_DIR=""
DRY_RUN=false
CHECK_ONLY=false
CREATE_BACKUP=false
FORCE=false
SWITCH_ARCHITECTURE=""  # modular or monolithic
UPGRADE_SOURCE="project"  # "project" or "github"
REQUESTED_VERSION=""  # Specific version to install, or empty for latest
REMOTE_HOST=""        # Remote host for SSH-based deployment
REMOTE_USER="claude"  # SSH username for remote deployment
AUTO_YES=false        # Skip confirmation prompts (--yes/-y)

# GitHub configuration (loaded from .release-info or defaults)
GITHUB_REPO="TheBoscoClub/Audiobook-Manager"
GITHUB_API="https://api.github.com/repos/TheBoscoClub/Audiobook-Manager"

# -----------------------------------------------------------------------------
# Script-to-CLI Name Aliases
# -----------------------------------------------------------------------------
# Maps repo script names (in scripts/) to user-facing CLI names (in /usr/local/bin/).
# Scripts already named grotto-* don't need an alias — they're auto-linked.
declare -A SCRIPT_ALIASES=(
    ["convert-audiobooks-opus-parallel"]="grotto-convert"
    ["build-conversion-queue"]="grotto-build-queue"
    ["download-new-audiobooks"]="grotto-download"
    ["monitor-audiobook-conversion"]="grotto-monitor"
    ["move-staged-audiobooks"]="grotto-move-staged"
    ["copy-audiobook-metadata"]="grotto-copy-metadata"
    ["cleanup-stale-indexes"]="grotto-cleanup-indexes"
    ["find-duplicate-sources"]="grotto-find-duplicates"
    ["fix-wrong-chapters-json"]="grotto-fix-chapters"
    ["embed-cover-art.py"]="grotto-embed-covers"
)

# -----------------------------------------------------------------------------
# Migration: Clean up old audiobook-* symlinks
# -----------------------------------------------------------------------------

cleanup_old_symlinks() {
    # Remove old audiobook-* symlinks from /usr/local/bin that will be replaced
    # by grotto-* symlinks during this upgrade. This is a simple cleanup, not a
    # full migration — it only handles symlinks that this script manages.
    local bin_dir="/usr/local/bin"
    local use_sudo="${1:-}"
    local cleaned=0

    [[ -d "$bin_dir" ]] || return 0

    for link in "$bin_dir"/audiobook-*; do
        [[ -L "$link" ]] || continue
        local target_path
        target_path=$(readlink "$link" 2>/dev/null) || continue
        # Only remove symlinks that point to our application directories
        if [[ "$target_path" == /opt/audiobooks/* ]] || [[ "$target_path" == /usr/local/lib/audiobooks/* ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would remove old symlink: $link -> $target_path"
            else
                ${use_sudo} rm -f "$link"
                echo "  Removed old symlink: $(basename "$link")"
            fi
            cleaned=$((cleaned + 1))
        fi
    done

    if [[ $cleaned -gt 0 ]]; then
        echo -e "${BLUE}Cleaned up $cleaned old audiobook-* symlinks (replaced by grotto-*)${NC}"
    fi
}

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

refresh_bin_symlinks() {
    # Maintain /usr/local/bin symlinks pointing to canonical script location.
    # Called after scripts are upgraded to ensure CLI commands stay in sync.
    local target="$1"
    local use_sudo="${2:-}"
    local bin_dir="/usr/local/bin"
    local scripts_dir="$target/scripts"

    # Only refresh if /usr/local/bin exists (system installation)
    [[ -d "$bin_dir" ]] || return 0

    echo -e "${BLUE}Refreshing ${bin_dir} symlinks...${NC}"

    # 1. Auto-link all grotto-* scripts (same name, no alias needed)
    for script in "$scripts_dir"/grotto-*; do
        [[ -f "$script" ]] || continue
        local name=$(basename "$script")
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would link: ${bin_dir}/${name} -> ${script}"
        else
            ${use_sudo} rm -f "${bin_dir}/${name}"
            ${use_sudo} ln -s "$script" "${bin_dir}/${name}"
            echo "  Linked: ${name}"
        fi
    done

    # 2. Create alias symlinks for scripts with non-grotto-* names
    for script_name in "${!SCRIPT_ALIASES[@]}"; do
        local target_name="${SCRIPT_ALIASES[$script_name]}"
        local source_path="${scripts_dir}/${script_name}"
        local link_path="${bin_dir}/${target_name}"
        if [[ -f "$source_path" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would link: ${link_path} -> ${source_path}"
            else
                ${use_sudo} rm -f "$link_path"
                ${use_sudo} ln -s "$source_path" "$link_path"
                echo "  Linked: ${target_name} -> ${script_name}"
            fi
        fi
    done
}

do_remote_upgrade() {
    # Deploy to a remote host via SSH, running the full upgrade lifecycle remotely.
    # Requires --from-project to specify the local project directory.
    local project_dir="${PROJECT_DIR:-$SCRIPT_DIR}"
    local remote_tmp="/tmp/grotto-upgrade-$$"
    local ssh_key="${HOME}/.claude/ssh/id_ed25519"

    # Build SSH options
    local ssh_opts=(-o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)
    [[ -f "$ssh_key" ]] && ssh_opts+=(-i "$ssh_key")
    local ssh_target="${REMOTE_USER}@${REMOTE_HOST}"

    echo -e "${BLUE}=== Remote Upgrade Mode ===${NC}"
    echo "  Host:    $ssh_target"
    echo "  Project: $project_dir"
    echo "  Target:  ${TARGET_DIR:-/opt/audiobooks}"
    echo ""

    # Test SSH connectivity
    echo -e "${BLUE}Testing SSH connectivity...${NC}"
    if ! ssh "${ssh_opts[@]}" "$ssh_target" "echo 'SSH OK'" &>/dev/null; then
        echo -e "${RED}Error: Cannot connect to $ssh_target via SSH${NC}"
        echo "  Ensure SSH key exists: $ssh_key"
        echo "  Ensure VM is running and accessible"
        return 1
    fi
    echo -e "${GREEN}  SSH connection OK${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo ""
        echo -e "${YELLOW}=== DRY RUN MODE ===${NC}"
        echo "  Would rsync project to $ssh_target:$remote_tmp"
        echo "  Would run: sudo $remote_tmp/upgrade.sh --from-project $remote_tmp --target ${TARGET_DIR:-/opt/audiobooks} --yes"
        echo "  Would cleanup $remote_tmp"
        return 0
    fi

    # rsync project to remote temp directory
    echo -e "${BLUE}Syncing project to remote...${NC}"
    ssh "${ssh_opts[@]}" "$ssh_target" "mkdir -p '$remote_tmp'"
    rsync -az --delete \
        --exclude='venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.pytest_cache' \
        --exclude='.ruff_cache' \
        --exclude='.git' \
        --exclude='.snapshots' \
        --exclude='*.db' \
        --exclude='testdata' \
        --exclude='.claude' \
        --exclude='SESSION_RECORD*' \
        --exclude='.staged-release' \
        --exclude='test-results.json' \
        -e "ssh ${ssh_opts[*]}" \
        "$project_dir/" "$ssh_target:$remote_tmp/"
    echo -e "${GREEN}  Project synced${NC}"

    # Run upgrade.sh remotely with full lifecycle
    local remote_target="${TARGET_DIR:-/opt/audiobooks}"
    echo -e "${BLUE}Running remote upgrade (full lifecycle)...${NC}"
    echo ""
    ssh "${ssh_opts[@]}" "$ssh_target" \
        "sudo '$remote_tmp/upgrade.sh' --from-project '$remote_tmp' --target '$remote_target' --yes" \
        || {
            local rc=$?
            echo -e "${RED}Remote upgrade failed (exit code $rc)${NC}"
            # Cleanup on failure
            ssh "${ssh_opts[@]}" "$ssh_target" "rm -rf '$remote_tmp'" 2>/dev/null || true
            return $rc
        }

    # Cleanup remote temp directory
    echo ""
    echo -e "${BLUE}Cleaning up remote temp files...${NC}"
    ssh "${ssh_opts[@]}" "$ssh_target" "rm -rf '$remote_tmp'"
    echo -e "${GREEN}  Cleanup complete${NC}"

    # Health check — wait for API
    echo ""
    echo -e "${BLUE}Waiting for API health check...${NC}"
    local api_port="${API_PORT:-5001}"
    local max_wait=15
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local resp
        resp=$(curl -s --connect-timeout 3 "http://${REMOTE_HOST}:${api_port}/api/system/version" 2>/dev/null) && {
            echo -e "${GREEN}  API responding: $resp${NC}"
            break
        }
        sleep 1
        waited=$((waited + 1))
    done

    if [[ $waited -ge $max_wait ]]; then
        echo -e "${YELLOW}  API not responding after ${max_wait}s — check remote services${NC}"
    fi

    echo ""
    echo -e "${GREEN}=== Remote Upgrade Complete ===${NC}"
}

print_header() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════════╗"
    echo "║               Vox Grotto Upgrade Script                          ║"
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
        "$HOME/Projects/Audiobook-Manager"
        "$HOME/audiobooks-project"
        "$HOME/Audiobook-Manager"
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
    # Only check actual application install locations (NOT data directories)
    local candidates=(
        "/opt/audiobooks"             # Standard system installation
        "/usr/local/lib/audiobooks"   # Alternative system location
        "$HOME/.local/lib/audiobooks" # User installation
    )

    local found=()
    for dir in "${candidates[@]}"; do
        # Require ALL markers of a real installation (scripts + library + VERSION)
        # Data directories (e.g., /srv/audiobooks) may have scripts/ but no VERSION
        if [[ -d "$dir/scripts" ]] && [[ -d "$dir/library" ]] && [[ -f "$dir/VERSION" ]]; then
            found+=("$dir")
        fi
    done

    if [[ ${#found[@]} -eq 0 ]]; then
        return 1
    fi

    # Warn if multiple installations found
    if [[ ${#found[@]} -gt 1 ]]; then
        echo -e "${YELLOW}Warning: Multiple installations found:${NC}" >&2
        for dir in "${found[@]}"; do
            local ver=$(get_version "$dir")
            echo "  - $dir (v$ver)" >&2
        done
        echo -e "${YELLOW}Using: ${found[1]} (use --target to specify)${NC}" >&2
    fi

    echo "${found[1]}"
    return 0
}

detect_architecture() {
    # Detect which API architecture is currently installed
    local target="$1"

    # Check wrapper script for api_server.py (modular) vs api.py (monolithic)
    local wrapper=""
    for w in "$target/bin/grotto-api" "/usr/local/bin/grotto-api" "$HOME/.local/bin/grotto-api"; do
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
    local wrappers=("$target/bin/grotto-api")
    if [[ "$use_sudo" == "true" ]]; then
        wrappers+=("/usr/local/bin/grotto-api")
    else
        wrappers+=("$HOME/.local/bin/grotto-api")
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
            if [[ "$FORCE" == "true" ]]; then
                echo -e "${YELLOW}Versions are identical, but --force specified. Proceeding.${NC}"
                return 0
            fi
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

    # Upgrade root-level management scripts (upgrade.sh, migrate-api.sh)
    # These live at project root but get installed to target/scripts/
    for script in upgrade.sh migrate-api.sh; do
        if [[ -f "${project}/${script}" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would update: $script"
            else
                if [[ -n "$use_sudo" ]]; then
                    sudo cp "${project}/${script}" "$target/scripts/"
                    sudo chmod +x "$target/scripts/${script}"
                else
                    cp "${project}/${script}" "$target/scripts/"
                    chmod +x "$target/scripts/${script}"
                fi
                echo "  Updated: $script"
            fi
        fi
    done

    # Upgrade lib
    if [[ -d "$target/lib" ]]; then
        echo -e "${BLUE}Upgrading configuration library...${NC}"
        if [[ "$DRY_RUN" == "true" ]]; then
            echo "  [DRY-RUN] Would update: grotto-config.sh"
        else
            if [[ -n "$use_sudo" ]]; then
                sudo cp "${project}/lib/grotto-config.sh" "$target/lib/"
            else
                cp "${project}/lib/grotto-config.sh" "$target/lib/"
            fi
            echo "  Updated: grotto-config.sh"
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

        # Copy new/updated service, target, path, and timer units
        for unit_file in "${project}/systemd/"*.service "${project}/systemd/"*.target "${project}/systemd/"*.path "${project}/systemd/"*.timer; do
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

        # Patch ReadWritePaths if data dir differs from default /srv/audiobooks.
        # ProtectSystem=strict makes the filesystem read-only except for listed paths.
        # Without this, cover art extraction and other data writes silently fail.
        local conf_data_dir=""
        if [[ -f "/etc/audiobooks/audiobooks.conf" ]]; then
            conf_data_dir=$(grep -oP '^AUDIOBOOKS_DATA=\K.*' /etc/audiobooks/audiobooks.conf 2>/dev/null)
        fi
        if [[ -n "$conf_data_dir" && "$conf_data_dir" != "/srv/audiobooks" ]]; then
            local api_svc="/etc/systemd/system/grotto-api.service"
            if [[ -f "$api_svc" ]] && sudo grep -q "ReadWritePaths=" "$api_svc" 2>/dev/null; then
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo "  [DRY-RUN] Would patch ReadWritePaths += ${conf_data_dir}"
                else
                    sudo sed -i "s|ReadWritePaths=\(.*\)|ReadWritePaths=\1 ${conf_data_dir}|" "$api_svc"
                    echo "  Patched: grotto-api.service ReadWritePaths += ${conf_data_dir}"
                    # Also update RequiresMountsFor so systemd waits for the mount
                    if sudo grep -q "RequiresMountsFor=" "$api_svc" 2>/dev/null; then
                        sudo sed -i "s|RequiresMountsFor=\(.*\)|RequiresMountsFor=\1 ${conf_data_dir}|" "$api_svc"
                        echo "  Patched: grotto-api.service RequiresMountsFor += ${conf_data_dir}"
                    fi
                fi
            fi
        fi

        # Install/update tmpfiles.d configuration for runtime directories
        if [[ -f "${project}/systemd/grotto-tmpfiles.conf" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would update tmpfiles.d configuration"
            else
                sudo cp "${project}/systemd/grotto-tmpfiles.conf" /etc/tmpfiles.d/audiobooks.conf
                sudo chmod 644 /etc/tmpfiles.d/audiobooks.conf
                # Ensure runtime directories exist
                sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf 2>/dev/null || {
                    # Fallback: create directories manually if tmpfiles fails
                    local var_dir="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
                    local staging="${AUDIOBOOKS_STAGING:-/tmp/audiobook-staging}"
                    sudo mkdir -p "${var_dir}/.control" "${var_dir}/.run" "$staging"
                    sudo chown audiobooks:audiobooks "${var_dir}/.control" "${var_dir}/.run" "$staging"
                    sudo chmod 755 "${var_dir}/.control"
                    sudo chmod 775 "${var_dir}/.run" "$staging"
                }
                echo "  Updated: tmpfiles.d configuration"
            fi
        fi

        # Reload systemd to pick up changes
        if [[ "$DRY_RUN" == "false" ]]; then
            sudo systemctl daemon-reload

            # Enable and start the privileged helper path unit if not already running
            if [[ -f "/etc/systemd/system/grotto-upgrade-helper.path" ]]; then
                sudo systemctl enable grotto-upgrade-helper.path 2>/dev/null || true
                sudo systemctl start grotto-upgrade-helper.path 2>/dev/null || true
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

    # Verify venv health — recreate if broken or pointing to /home/ (pyenv)
    # systemd ProtectHome=yes blocks access to /home/, breaking pyenv-created venvs
    if [[ "$DRY_RUN" == "false" ]] && [[ -d "$target/library" ]]; then
        local venv_ok=true
        if [[ ! -d "$target/library/venv" ]]; then
            venv_ok=false
        elif ! "$target/library/venv/bin/python" --version &>/dev/null; then
            echo -e "${YELLOW}Venv has broken Python symlinks — recreating${NC}"
            venv_ok=false
        elif readlink -f "$target/library/venv/bin/python" | grep -q "^/home/"; then
            echo -e "${YELLOW}Venv points to /home/ (breaks ProtectHome=yes) — recreating${NC}"
            venv_ok=false
        fi
        if [[ "$venv_ok" == "false" ]]; then
            echo -e "${BLUE}Recreating Python virtual environment (system Python)...${NC}"
            local sys_python="/usr/bin/python3"
            [[ -x /usr/bin/python3.14 ]] && sys_python="/usr/bin/python3.14"
            if [[ -n "$use_sudo" ]]; then
                sudo rm -rf "$target/library/venv"
                sudo "$sys_python" -m venv "$target/library/venv"
                sudo chown -R audiobooks:audiobooks "$target/library/venv"
                sudo -u audiobooks "$target/library/venv/bin/pip" install --quiet \
                    -r "$target/library/requirements.txt" 2>/dev/null \
                    || sudo -u audiobooks "$target/library/venv/bin/pip" install --quiet flask mutagen
            else
                rm -rf "$target/library/venv"
                "$sys_python" -m venv "$target/library/venv"
                "$target/library/venv/bin/pip" install --quiet \
                    -r "$target/library/requirements.txt" 2>/dev/null \
                    || "$target/library/venv/bin/pip" install --quiet flask mutagen
            fi
            echo -e "${GREEN}  Venv recreated with system Python${NC}"
        fi
    fi

    # Clean up old audiobook-* symlinks before creating new grotto-* ones
    if [[ "$target" == "/opt/audiobooks" || "$target" == "/usr/local/lib/audiobooks" ]]; then
        cleanup_old_symlinks "${use_sudo}"
        refresh_bin_symlinks "$target" "${use_sudo}"
    fi

    echo ""
    echo -e "${GREEN}=== Upgrade Complete ===${NC}"
    echo "New version: $(get_version "$project")"

    # Verify permissions after upgrade
    verify_installation_permissions "$target"
}

# -----------------------------------------------------------------------------
# Auth Database Safety
# -----------------------------------------------------------------------------

backup_auth_db() {
    # Back up the auth database before any file operations
    local target="$1"
    local use_sudo="$2"

    # Try to find auth database
    local auth_db=""
    for candidate in \
        "${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}/auth.db" \
        "$target/../auth.db"; do
        if [[ -f "$candidate" ]]; then
            auth_db="$candidate"
            break
        fi
    done

    if [[ -z "$auth_db" ]]; then
        echo -e "${YELLOW}  No auth database found — skipping auth backup${NC}"
        return 0
    fi

    local backup="${auth_db}.pre-upgrade-$(date +%Y%m%d%H%M%S)"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "  [DRY-RUN] Would backup: $auth_db → $backup"
        return 0
    fi

    echo -e "${BLUE}Backing up auth database...${NC}"
    echo "  Source: $auth_db"
    echo "  Backup: $backup"
    if [[ -n "$use_sudo" ]]; then
        sudo cp -p "$auth_db" "$backup"
    else
        cp -p "$auth_db" "$backup"
    fi
    echo -e "${GREEN}  Auth database backed up${NC}"
}

validate_auth_post_upgrade() {
    # Verify auth database integrity after upgrade
    local target="$1"

    local auth_db=""
    for candidate in \
        "${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}/auth.db" \
        "$target/../auth.db"; do
        if [[ -f "$candidate" ]]; then
            auth_db="$candidate"
            break
        fi
    done

    if [[ -z "$auth_db" ]]; then
        return 0  # No auth DB — nothing to validate
    fi

    echo -e "${BLUE}Validating auth database post-upgrade...${NC}"

    # Check if API is responding
    local api_port="${API_PORT:-5001}"
    local max_wait=10
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        if curl -s "http://localhost:${api_port}/api/system/version" >/dev/null 2>&1; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if [[ $waited -ge $max_wait ]]; then
        echo -e "${YELLOW}  API not responding on port $api_port — skipping auth validation${NC}"
        return 0
    fi

    # Query user count via API (if auth enabled)
    local status_resp
    status_resp=$(curl -s "http://localhost:${api_port}/auth/status" 2>/dev/null)
    if [[ -n "$status_resp" ]]; then
        local auth_enabled
        auth_enabled=$(echo "$status_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('auth_enabled',False))" 2>/dev/null || echo "unknown")
        echo "  Auth enabled: $auth_enabled"
        echo -e "${GREEN}  Auth database validated — API responding${NC}"
    else
        echo -e "${YELLOW}  Could not reach /auth/status — auth may not be configured${NC}"
    fi
}

# -----------------------------------------------------------------------------
# Post-Upgrade Verification
# -----------------------------------------------------------------------------

stop_services() {
    # Stop Vox Grotto services before upgrade
    local use_sudo="$1"

    echo -e "${BLUE}Stopping Vox Grotto services...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would stop Vox Grotto services"
        return 0
    fi

    # Check if systemd services exist
    if systemctl list-units --type=service --all 2>/dev/null | grep -q "grotto-"; then
        # System-level services
        if [[ -n "$use_sudo" ]]; then
            sudo systemctl stop grotto.target 2>/dev/null || true
            # Also stop individual services in case target doesn't exist
            for svc in grotto-api grotto-proxy grotto-redirect grotto-converter grotto-mover grotto-downloader.timer grotto-shutdown-saver; do
                sudo systemctl stop "$svc" 2>/dev/null || true
            done
        fi
        echo -e "${GREEN}  Services stopped${NC}"
    elif systemctl --user list-units --type=service --all 2>/dev/null | grep -q "grotto-"; then
        # User-level services
        systemctl --user stop grotto.target 2>/dev/null || true
        for svc in grotto-api grotto-proxy grotto-redirect; do
            systemctl --user stop "$svc" 2>/dev/null || true
        done
        echo -e "${GREEN}  User services stopped${NC}"
    else
        echo "  No active Vox Grotto services found"
    fi
}

start_services() {
    # Start Vox Grotto services after upgrade
    local use_sudo="$1"

    echo -e "${BLUE}Starting Vox Grotto services...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would start Vox Grotto services"
        return 0
    fi

    # Reload systemd to pick up any service file changes
    if [[ -n "$use_sudo" ]]; then
        sudo systemctl daemon-reload
    else
        systemctl --user daemon-reload 2>/dev/null || true
    fi

    # Check if systemd services exist
    if systemctl list-units --type=service --all 2>/dev/null | grep -q "grotto-"; then
        # System-level services
        if [[ -n "$use_sudo" ]]; then
            sudo systemctl start grotto.target 2>/dev/null || {
                # Fallback: start individual services
                for svc in grotto-api grotto-proxy grotto-redirect grotto-converter grotto-mover grotto-downloader.timer grotto-shutdown-saver; do
                    sudo systemctl start "$svc" 2>/dev/null || true
                done
            }
        fi
        echo -e "${GREEN}  Services started${NC}"

        # Show service status summary
        echo ""
        echo -e "${BLUE}Service status:${NC}"
        for svc in grotto-api grotto-proxy grotto-converter grotto-mover grotto-downloader.timer; do
            local svc_state
            svc_state=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
            if [[ "$svc_state" == "active" ]]; then
                echo -e "  $svc: ${GREEN}$svc_state${NC}"
            else
                echo -e "  $svc: ${YELLOW}$svc_state${NC}"
            fi
        done
    elif systemctl --user list-units --type=service --all 2>/dev/null | grep -q "grotto-"; then
        # User-level services
        systemctl --user start grotto.target 2>/dev/null || {
            for svc in grotto-api grotto-proxy grotto-redirect; do
                systemctl --user start "$svc" 2>/dev/null || true
            done
        }
        echo -e "${GREEN}  User services started${NC}"
    else
        echo "  No Vox Grotto services to start"
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
            issues_found=$((issues_found + 1))
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
        issues_found=$((issues_found + 1))
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
        issues_found=$((issues_found + 1))
    else
        echo -e "${GREEN}OK${NC}"
    fi

    # Check shell script permissions (must be 755 — readable and executable by all)
    # Without world-readable, /etc/profile.d scripts can't source shared libs like grotto-config.sh
    echo -n "  Checking executable permissions (.sh)... "
    local bad_scripts=$(find "$target_dir" -name "*.sh" \( ! -perm -u+x -o ! -perm -a+r \) 2>/dev/null | wc -l)
    if [[ "$bad_scripts" -gt 0 ]]; then
        echo -e "${YELLOW}fixing $bad_scripts scripts${NC}"
        if [[ "$is_system" == "true" ]]; then
            sudo find "$target_dir" -name "*.sh" \( ! -perm -u+x -o ! -perm -a+r \) -exec chmod 755 {} \;
        else
            find "$target_dir" -name "*.sh" \( ! -perm -u+x -o ! -perm -a+r \) -exec chmod 755 {} \;
        fi
        issues_found=$((issues_found + 1))
    else
        echo -e "${GREEN}OK${NC}"
    fi

    # Verify no symlinks point to development project directory
    # The check must look for ClaudeCodeProjects paths specifically, NOT $SCRIPT_DIR,
    # because when run from /opt/audiobooks, $SCRIPT_DIR matches legitimate production links
    echo -n "  Checking for project source dependencies... "
    local project_links=$(find /usr/local/bin -name "grotto-*" -type l -exec readlink {} \; 2>/dev/null | grep -c "ClaudeCodeProjects" || true)
    if [[ "$project_links" -gt 0 ]]; then
        echo -e "${RED}WARNING: $project_links binaries link to project source!${NC}"
        issues_found=$((issues_found + 1))
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
    local http_code
    local temp_body=$(mktemp)

    # Write response body to file to keep JSON handling clean
    http_code=$(curl -sL --connect-timeout 10 -o "$temp_body" -w '%{http_code}' "$url") || {
        echo -e "${RED}Failed to connect to GitHub API${NC}" >&2
        echo -e "${RED}  URL: $url${NC}" >&2
        rm -f "$temp_body"
        return 1
    }

    if [[ "$http_code" != "200" ]]; then
        echo -e "${RED}GitHub API returned HTTP $http_code${NC}" >&2
        echo -e "${RED}  URL: $url${NC}" >&2
        # Show API error message if present
        if command -v jq &>/dev/null; then
            local api_msg
            api_msg=$(jq -r '.message // empty' "$temp_body" 2>/dev/null)
            [[ -n "$api_msg" ]] && echo -e "${RED}  API message: $api_msg${NC}" >&2
        fi
        rm -f "$temp_body"
        return 1
    fi

    local version
    if command -v jq &>/dev/null; then
        version=$(jq -r '.tag_name // empty' "$temp_body" 2>/dev/null)
    else
        version=$(grep '"tag_name"' "$temp_body" | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/')
    fi

    rm -f "$temp_body"

    # Remove 'v' prefix if present
    version="${version#v}"

    if [[ -z "$version" ]]; then
        echo -e "${RED}Could not determine latest version from GitHub${NC}" >&2
        echo -e "${RED}  Response had no tag_name field${NC}" >&2
        return 1
    fi

    echo "$version"
}

get_release_tarball_url() {
    # Get download URL for a specific release version
    local version="$1"
    local temp_body=$(mktemp)

    # Try with 'v' prefix first (v3.1.0), then without (3.1.0)
    for tag in "v${version}" "${version}"; do
        local url="${GITHUB_API}/releases/tags/${tag}"
        # Write to file for clean JSON handling
        curl -sL --connect-timeout 10 -o "$temp_body" "$url" || continue

        local tarball_url
        if command -v jq &>/dev/null; then
            tarball_url=$(jq -r '.assets[] | select(.name | endswith(".tar.gz")) | .browser_download_url' "$temp_body" 2>/dev/null | head -1)
        else
            # Fallback: construct URL from expected pattern
            tarball_url="https://github.com/${GITHUB_REPO}/releases/download/${tag}/vox-grotto-${version}.tar.gz"
        fi

        if [[ -n "$tarball_url" ]]; then
            rm -f "$temp_body"
            echo "$tarball_url"
            return 0
        fi
    done

    rm -f "$temp_body"
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

    # Find the extracted directory (flexible pattern for self-healing upgrades)
    # Try multiple patterns to handle naming changes without bootstrap problems
    local extract_dir=""
    for pattern in "vox-grotto-*" "audiobook-manager-*" "audiobook-*" "Audiobook-Manager-*"; do
        extract_dir=$(find "$temp_dir" -maxdepth 1 -type d -name "$pattern" 2>/dev/null | head -1)
        [[ -n "$extract_dir" ]] && break
    done

    # Fallback: find any directory that looks like a versioned release
    if [[ -z "$extract_dir" ]]; then
        extract_dir=$(find "$temp_dir" -maxdepth 1 -type d -name "*-[0-9]*" ! -name "*.tar.gz" 2>/dev/null | head -1)
    fi

    if [[ -z "$extract_dir" ]] || [[ ! -d "$extract_dir" ]]; then
        echo -e "${RED}Could not find extracted directory${NC}" >&2
        echo "  Contents of temp dir:" >&2
        ls -la "$temp_dir" >&2
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
        if [[ "$FORCE" == "true" ]]; then
            echo -e "${YELLOW}Already at version $install_version, but --force specified. Proceeding.${NC}"
        else
            echo ""
            echo -e "${GREEN}Already at version $install_version - no upgrade needed.${NC}"
            return 0
        fi
    fi

    # Version comparison
    set +e
    compare_versions "$current_version" "$install_version"
    local cmp_result=$?
    set -e

    if [[ $cmp_result -eq 1 ]]; then
        echo -e "${YELLOW}Warning: Target version ($install_version) is older than current ($current_version)${NC}"
        if [[ "$AUTO_YES" != "true" ]]; then
            echo -n "Continue with downgrade? [y/N]: "
            read -r confirm
            if [[ "${confirm,,}" != "y" ]]; then
                echo "Cancelled."
                return 0
            fi
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
    trap "rm -rf '$temp_dir'; _cleanup_on_exit" EXIT

    # Download and extract
    local release_dir
    release_dir=$(download_and_extract_release "$tarball_url" "$temp_dir") || {
        echo -e "${RED}Failed to download/extract release${NC}"
        return 1
    }

    echo ""
    [[ "$DRY_RUN" == "true" ]] && echo -e "${YELLOW}=== DRY RUN MODE ===${NC}" && echo ""

    # Confirm upgrade
    if [[ "$DRY_RUN" == "false" ]] && [[ "$AUTO_YES" != "true" ]]; then
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

    # Backup auth database before any changes
    backup_auth_db "$target" "$use_sudo"

    # Stop services before upgrade (trap ensures restart on failure)
    _SERVICES_USE_SUDO="$use_sudo"
    stop_services "$use_sudo"
    _SERVICES_STOPPED=true
    echo ""

    # Use the existing do_upgrade function with the extracted release
    do_upgrade "$release_dir" "$target"

    echo ""

    # Start services after upgrade
    start_services "$use_sudo"
    _SERVICES_STOPPED=false

    # Validate auth database post-upgrade
    validate_auth_post_upgrade "$target"

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
        --remote)
            REMOTE_HOST="$2"
            shift 2
            ;;
        --user)
            REMOTE_USER="$2"
            shift 2
            ;;
        --yes|-y)
            AUTO_YES=true
            shift
            ;;
        --force)
            FORCE=true
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

# Remote upgrade mode — deploy to remote host via SSH
if [[ -n "$REMOTE_HOST" ]]; then
    if [[ -z "$PROJECT_DIR" ]]; then
        PROJECT_DIR=$(find_project_dir) || {
            echo -e "${RED}Error: --remote requires --from-project PATH${NC}"
            exit 1
        }
    fi
    do_remote_upgrade
    exit $?
fi

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
if [[ "$DRY_RUN" == "false" ]] && [[ "$AUTO_YES" != "true" ]]; then
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

# Backup auth database before any changes
backup_auth_db "$TARGET_DIR" "$use_sudo"

# Stop services before upgrade (trap ensures restart on failure)
_SERVICES_USE_SUDO="$use_sudo"
stop_services "$use_sudo"
_SERVICES_STOPPED=true
echo ""

# Perform upgrade
do_upgrade "$PROJECT_DIR" "$TARGET_DIR"

# Start services after upgrade
echo ""
start_services "$use_sudo"
_SERVICES_STOPPED=false

# Validate auth database post-upgrade
validate_auth_post_upgrade "$TARGET_DIR"

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
