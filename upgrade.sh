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
#   --remote HOST         Deploy to remote host via SSH (requires --from-project)
#   --user USER           SSH username for remote deploy (default: claude)
#   --yes, -y             Non-interactive mode (skip all confirmation prompts)
#   --switch-to-modular   Switch to modular Flask Blueprint architecture
#   --switch-to-monolithic  Switch to single-file architecture
#   --force               Force upgrade even if versions are identical
#   --major-version, --mv Perform major version upgrade (venv rebuild,
#                         config migration, enable new services)
#   --dry-run             Show what would be done without making changes
#   --help                Show this help message
#
# Examples:
#   # Upgrade from GitHub (recommended for standalone installations):
#   audiobook-upgrade
#   ./upgrade.sh --from-github --target /opt/audiobooks
#
#   # Upgrade to specific version:
#   audiobook-upgrade --version 3.2.0
#
#   # From local project directory:
#   ./upgrade.sh --from-project /path/to/Audiobook-Manager --target /opt/audiobooks
#
#   # Deploy to remote VM (full lifecycle: stop, backup, sync, venv, restart):
#   ./upgrade.sh --from-project . --remote <vm-host> --yes
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

show_usage() {
    echo -e "${CYAN}${BOLD}Audiobook Library — Upgrade Script${NC}"
    echo ""
    echo -e "${BOLD}USAGE${NC}"
    echo "  ./upgrade.sh [OPTIONS]"
    echo ""
    echo -e "${BOLD}UPGRADE SOURCES${NC}"
    echo -e "  ${GREEN}--from-project${NC} PATH   Upgrade from a local project directory"
    echo -e "  ${GREEN}--from-github${NC}         Upgrade from the latest GitHub release"
    echo -e "  ${GREEN}--version${NC} VERSION     Install a specific version (use with --from-github)"
    echo ""
    echo -e "${BOLD}TARGET${NC}"
    echo -e "  ${GREEN}--target${NC} PATH         Target installation directory (default: auto-detect)"
    echo -e "  ${GREEN}--remote${NC} HOST         Deploy to a remote host via SSH (requires --from-project)"
    echo -e "  ${GREEN}--user${NC} USER           SSH username for remote deploy (default: current user)"
    echo -e "  ${GREEN}--ssh-key${NC} PATH        SSH private key for remote deploy (default: standard SSH discovery)"
    echo ""
    echo -e "${BOLD}MODES${NC}"
    echo -e "  ${GREEN}--check${NC}               Check for available updates without upgrading"
    echo -e "  ${GREEN}--dry-run${NC}             Show what would be done without making changes"
    echo -e "  ${GREEN}--backup${NC}              Create a backup of the installation before upgrading"
    echo -e "  ${GREEN}--force${NC}               Force upgrade even if versions are identical"
    echo -e "  ${GREEN}--yes${NC}, ${GREEN}-y${NC}             Non-interactive mode (skip all confirmation prompts)"
    echo ""
    echo -e "${BOLD}MAJOR UPGRADES${NC}"
    echo -e "  ${GREEN}--major-version${NC}, ${GREEN}--mv${NC} Perform major version upgrade (venv rebuild,"
    echo "                         config migration, enable new services)"
    echo ""
    echo -e "${BOLD}ARCHITECTURE${NC}"
    echo -e "  ${GREEN}--switch-to-modular${NC}   Switch to modular Flask Blueprint architecture"
    echo -e "  ${GREEN}--switch-to-monolithic${NC} Switch to single-file architecture"
    echo ""
    echo -e "${BOLD}COMMON WORKFLOWS${NC}"
    echo ""
    echo -e "  ${BLUE}# Deploy from local project to system installation:${NC}"
    echo "  ./upgrade.sh --from-project . --target /opt/audiobooks --yes"
    echo ""
    echo -e "  ${BLUE}# Deploy to a remote VM (full lifecycle: stop, sync, venv, restart):${NC}"
    echo "  ./upgrade.sh --from-project . --remote <vm-host> --yes"
    echo ""
    echo -e "  ${BLUE}# Upgrade from the latest GitHub release:${NC}"
    echo "  ./upgrade.sh --from-github --target /opt/audiobooks"
    echo ""
    echo -e "  ${BLUE}# Upgrade to a specific version from GitHub:${NC}"
    echo "  ./upgrade.sh --from-github --version 7.1.0 --target /opt/audiobooks"
    echo ""
    echo -e "  ${BLUE}# Check if updates are available (no changes):${NC}"
    echo "  ./upgrade.sh --from-github --check"
    echo ""
    echo -e "  ${BLUE}# Dry run to see what would happen:${NC}"
    echo "  ./upgrade.sh --from-project . --target /opt/audiobooks --dry-run"
    echo ""
    echo -e "  ${BLUE}# Major version upgrade with venv rebuild:${NC}"
    echo "  ./upgrade.sh --from-project . --target /opt/audiobooks --major-version --yes"
}

# Safety net: restart services if script dies after stopping them.
# set -e can kill the script mid-upgrade, leaving services dead with no 502
# recovery. This trap ensures services always come back up.
_SERVICES_STOPPED=false
_SERVICES_USE_SUDO=""
_cleanup_on_exit() {
    if [[ "$_SERVICES_STOPPED" == "true" ]]; then
        if [[ "$SKIP_SERVICE_LIFECYCLE" == "true" ]]; then
            echo ""
            echo -e "${YELLOW}Services were stopped but --skip-service-lifecycle is set — leaving restart to caller.${NC}"
        else
            echo ""
            echo -e "${YELLOW}Script exited before services were restarted — restarting now...${NC}"
            start_services "$_SERVICES_USE_SUDO" 2>/dev/null || {
                echo -e "${RED}CRITICAL: Failed to restart services. Run manually:${NC}"
                echo -e "${RED}  sudo systemctl start audiobook-api audiobook-proxy${NC}"
            }
        fi
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
SWITCH_ARCHITECTURE=""           # modular or monolithic
UPGRADE_SOURCE="project"         # "project" or "github"
REQUESTED_VERSION=""             # Specific version to install, or empty for latest
REMOTE_HOST=""                   # Remote host for SSH-based deployment
REMOTE_USER="${USER:-$(whoami)}" # SSH username for remote deployment (defaults to current user)
AUTO_YES=false                   # Skip confirmation prompts (--yes/-y)
MAJOR_VERSION=false              # Force venv rebuild + config migration + service enablement
SKIP_SERVICE_LIFECYCLE=false     # Internal: caller (upgrade-helper) manages service start/stop

# GitHub configuration (loaded from .release-info or defaults)
GITHUB_REPO="TheBoscoClub/Audiobook-Manager"
GITHUB_API="https://api.github.com/repos/TheBoscoClub/Audiobook-Manager"

# -----------------------------------------------------------------------------
# Script-to-CLI Name Aliases
# -----------------------------------------------------------------------------
# Maps repo script names (in scripts/) to user-facing CLI names (in /usr/local/bin/).
# Scripts already named audiobook-* don't need an alias — they're auto-linked.
declare -A SCRIPT_ALIASES=(
    ["convert-audiobooks-opus-parallel"]="audiobook-convert"
    ["build-conversion-queue"]="audiobook-build-queue"
    ["download-new-audiobooks"]="audiobook-download"
    ["monitor-audiobook-conversion"]="audiobook-monitor"
    ["move-staged-audiobooks"]="audiobook-move-staged"
    ["copy-audiobook-metadata"]="audiobook-copy-metadata"
    ["cleanup-stale-indexes"]="audiobook-cleanup-indexes"
    ["find-duplicate-sources"]="audiobook-find-duplicates"
    ["fix-wrong-chapters-json"]="audiobook-fix-chapters"
    ["embed-cover-art.py"]="audiobook-embed-covers"
)

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

    # 1. Auto-link all audiobook-* scripts (same name, no alias needed)
    for script in "$scripts_dir"/audiobook-*; do
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

    # 2. Create alias symlinks for scripts with non-audiobook-* names
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
    local remote_tmp="/tmp/audiobook-upgrade-$$"
    # Build SSH options — uses standard SSH key discovery (agent, ~/.ssh/)
    # unless --ssh-key is explicitly provided
    local ssh_opts=(-o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)
    [[ -n "${SSH_KEY:-}" ]] && ssh_opts+=(-i "$SSH_KEY")
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
        echo "  Ensure the remote host is running and accessible"
        echo "  Ensure your SSH key is authorized on the remote host"
        [[ -n "${SSH_KEY:-}" ]] && echo "  SSH key: $SSH_KEY"
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
    # shellcheck disable=SC2029  # $remote_tmp intentionally expands client-side
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
    local remote_flags="--yes"
    [[ "$FORCE" == "true" ]] && remote_flags="$remote_flags --force"
    [[ "$MAJOR_VERSION" == "true" ]] && remote_flags="$remote_flags --major-version"
    echo -e "${BLUE}Running remote upgrade (full lifecycle)...${NC}"
    echo ""
    # shellcheck disable=SC2029  # $remote_tmp, $remote_target, $remote_flags intentionally expand client-side
    ssh "${ssh_opts[@]}" "$ssh_target" \
        "sudo '$remote_tmp/upgrade.sh' --from-project '$remote_tmp' --target '$remote_target' $remote_flags" \
        || {
            local rc=$?
            echo -e "${RED}Remote upgrade failed (exit code $rc)${NC}"
            # Cleanup on failure
            # shellcheck disable=SC2029  # $remote_tmp intentionally expands client-side
            ssh "${ssh_opts[@]}" "$ssh_target" "rm -rf '$remote_tmp'" 2>/dev/null || true
            return $rc
        }

    # Cleanup remote temp directory
    echo ""
    echo -e "${BLUE}Cleaning up remote temp files...${NC}"
    # shellcheck disable=SC2029  # $remote_tmp intentionally expands client-side
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

    # Purge Cloudflare CDN cache (runs locally — CDN is external to the VM)
    purge_cloudflare_cache

    echo ""
    echo -e "${GREEN}=== Remote Upgrade Complete ===${NC}"
}

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
        return 2 # v1 < v2
    else
        return 1 # v1 > v2
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
        echo -e "${YELLOW}Using: ${found[0]} (use --target to specify)${NC}" >&2
    fi

    echo "${found[0]}"
    return 0
}

detect_architecture() {
    # Detect which API architecture is currently installed
    local target="$1"

    # Check wrapper script for api_server.py (modular) vs api.py (monolithic)
    local wrapper=""
    for w in "$target/bin/audiobook-api" "/usr/local/bin/audiobook-api" "$HOME/.local/bin/audiobook-api"; do
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
    local wrappers=("$target/bin/audiobook-api")
    if [[ "$use_sudo" == "true" ]]; then
        wrappers+=("/usr/local/bin/audiobook-api")
    else
        wrappers+=("$HOME/.local/bin/audiobook-api")
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

    # Rolling retention: keep last 5 backups, delete older ones
    local -a backups
    mapfile -t backups < <(ls -1dt "${target}.backup."* 2>/dev/null)
    if ((${#backups[@]} > 5)); then
        for old_backup in "${backups[@]:5}"; do
            echo -e "${BLUE}  Removing old backup: $old_backup${NC}"
            rm -rf "$old_backup" 2>/dev/null || sudo rm -rf "$old_backup" 2>/dev/null || true
        done
    fi
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

apply_schema_migrations() {
    # Apply database schema migrations (safe to run multiple times, idempotent).
    # Called both during upgrade and as a standalone post-check.
    local target="$1"
    local use_sudo="${2:-}"

    if [[ ! -d "$target/library" ]]; then
        return 0
    fi

    # Locate the library database from config or default
    local db_path=""
    if [[ -f "/etc/audiobooks/audiobooks.conf" ]]; then
        db_path=$(grep -oP '^AUDIOBOOKS_DATABASE=\K.*' /etc/audiobooks/audiobooks.conf 2>/dev/null)
        # Strip surrounding quotes if present
        db_path="${db_path%\"}"
        db_path="${db_path#\"}"
    fi
    db_path="${db_path:-${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}/db/audiobooks.db}"

    if [[ ! -f "$db_path" ]]; then
        return 0
    fi

    # Apply DDL migration for normalized author/narrator tables
    local migration_sql="$target/library/backend/migrations/011_multi_author_narrator.sql"
    if [[ -f "$migration_sql" ]]; then
        local needs_migration
        needs_migration=$(sqlite3 "$db_path" \
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='authors';" 2>/dev/null)
        if [[ "$needs_migration" == "0" ]]; then
            echo -e "${BLUE}Applying schema migrations...${NC}"
            if [[ -n "$use_sudo" ]]; then
                cat "$migration_sql" | sudo sqlite3 "$db_path"
            else
                sqlite3 "$db_path" <"$migration_sql"
            fi
            echo "  Applied: 011_multi_author_narrator.sql"
        fi
    fi

    # Run data migration to populate normalized tables (idempotent)
    local migration_py="$target/library/backend/migrations/migrate_to_normalized_authors.py"
    local venv_python="$target/library/venv/bin/python"
    if [[ -f "$migration_py" ]] && [[ -x "$venv_python" ]]; then
        local author_count
        author_count=$(sqlite3 "$db_path" "SELECT COUNT(*) FROM authors;" 2>/dev/null || echo "0")
        if [[ "$author_count" == "0" ]]; then
            echo -e "${BLUE}Running author/narrator data migration...${NC}"
            if [[ -n "$use_sudo" ]]; then
                (cd "$target" && sudo -u audiobooks PYTHONPATH="$target/library" \
                    "$venv_python" -m backend.migrations.migrate_to_normalized_authors \
                    --db-path "$db_path" 2>&1) || {
                    echo -e "${YELLOW}Warning: Author migration failed (non-critical, grouped sort unavailable)${NC}"
                }
            else
                (cd "$target" && PYTHONPATH="$target/library" \
                    "$venv_python" -m backend.migrations.migrate_to_normalized_authors \
                    --db-path "$db_path" 2>&1) || {
                    echo -e "${YELLOW}Warning: Author migration failed (non-critical, grouped sort unavailable)${NC}"
                }
            fi
            echo "  Data migration complete"
        fi
    fi
}

# -----------------------------------------------------------------------------
# Major Version Upgrade Functions
# -----------------------------------------------------------------------------

force_venv_rebuild() {
    # Force-rebuild the Python virtual environment from requirements.txt.
    # Ensures new dependencies are installed and old ones are removed.
    local target="$1"
    local use_sudo="${2:-}"

    if [[ ! -d "$target/library" ]]; then
        return 0
    fi

    echo -e "${BLUE}Force-rebuilding Python virtual environment...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would delete and recreate venv at $target/library/venv"
        echo "  [DRY-RUN] Would install packages from $target/library/requirements.txt"
        return 0
    fi

    local sys_python="/usr/bin/python3"
    [[ -x /usr/bin/python3.14 ]] && sys_python="/usr/bin/python3.14"
    [[ -x /usr/bin/python3.13 ]] && [[ ! -x /usr/bin/python3.14 ]] && sys_python="/usr/bin/python3.13"

    if [[ -n "$use_sudo" ]]; then
        sudo rm -rf "$target/library/venv"
        sudo "$sys_python" -m venv "$target/library/venv"
        sudo chown -R audiobooks:audiobooks "$target/library/venv"
        sudo -u audiobooks "$target/library/venv/bin/pip" install --quiet \
            -r "$target/library/requirements.txt" 2>&1 || {
            echo -e "${RED}  pip install failed — trying minimal fallback${NC}"
            sudo -u audiobooks "$target/library/venv/bin/pip" install --quiet \
                flask mutagen gunicorn gevent
        }
    else
        rm -rf "$target/library/venv"
        "$sys_python" -m venv "$target/library/venv"
        "$target/library/venv/bin/pip" install --quiet \
            -r "$target/library/requirements.txt" 2>&1 || {
            echo -e "${RED}  pip install failed — trying minimal fallback${NC}"
            "$target/library/venv/bin/pip" install --quiet flask mutagen gunicorn gevent
        }
    fi

    echo -e "${GREEN}  Venv rebuilt with fresh dependencies${NC}"
}

apply_config_migrations() {
    # Apply config file migrations — add new variables to existing audiobooks.conf.
    # Each migration in config-migrations/*.sh is idempotent (checks before modifying).
    local project="$1"
    local use_sudo="${2:-}"

    local migrations_dir="$project/config-migrations"
    if [[ ! -d "$migrations_dir" ]]; then
        return 0
    fi

    # Find the config file (system or user-level)
    local conf_file=""
    if [[ -f "/etc/audiobooks/audiobooks.conf" ]]; then
        conf_file="/etc/audiobooks/audiobooks.conf"
    elif [[ -f "${HOME}/.config/audiobooks/audiobooks.conf" ]]; then
        conf_file="${HOME}/.config/audiobooks/audiobooks.conf"
    fi

    if [[ -z "$conf_file" ]]; then
        echo -e "${YELLOW}  No audiobooks.conf found — skipping config migrations${NC}"
        return 0
    fi

    echo -e "${BLUE}Applying config migrations...${NC}"

    local migration_count=0
    for migration in "$migrations_dir"/*.sh; do
        [[ -f "$migration" ]] || continue

        # Export variables for the migration script
        export CONF_FILE="$conf_file"
        export USE_SUDO="$use_sudo"
        export DRY_RUN

        # Source the migration (runs in current shell context)
        source "$migration"
        migration_count=$((migration_count + 1))
    done

    if [[ $migration_count -gt 0 ]]; then
        echo -e "${GREEN}  Applied $migration_count config migration(s)${NC}"
    else
        echo "  No config migrations to apply"
    fi
}

apply_data_migrations() {
    # Apply data-state migrations — reclassify, backfill, or repair DB rows
    # when an upgrade crosses a version boundary that changed classification
    # or enrichment logic.
    #
    # Unlike config migrations (always-run, idempotent), data migrations are
    # version-gated: each script declares a MIN_VERSION, and only runs when
    # the installed version is below that boundary AND the target version is
    # at or above it. The scripts themselves are still idempotent (safe to
    # re-run), but gating avoids unnecessary work on large databases.
    #
    # On fresh installs (installed_version="unknown"), ALL migrations run
    # automatically — there are no user overrides to conflict with.
    local project="$1"
    local target="$2"
    local use_sudo="${3:-}"
    local interactive="${4:-true}"

    local migrations_dir="$project/data-migrations"
    if [[ ! -d "$migrations_dir" ]]; then
        return 0
    fi

    # Locate the database. Precedence: explicit DB_PATH env > conf file >
    # AUDIOBOOKS_VAR_DIR fallback. Letting DB_PATH win enables test harnesses
    # and operators to point the dispatcher at a specific DB without editing
    # /etc/audiobooks/audiobooks.conf.
    local db_path="${DB_PATH:-}"
    if [[ -z "$db_path" ]] && [[ -f "/etc/audiobooks/audiobooks.conf" ]]; then
        db_path=$(grep -oP '^AUDIOBOOKS_DATABASE=\K.*' /etc/audiobooks/audiobooks.conf 2>/dev/null)
        db_path="${db_path%\"}"
        db_path="${db_path#\"}"
    fi
    db_path="${db_path:-${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}/db/audiobooks.db}"

    if [[ ! -f "$db_path" ]]; then
        return 0
    fi

    local venv_python="$target/library/venv/bin/python"
    local installed_version
    installed_version=$(get_version "$target")
    local target_version
    target_version=$(get_version "$project")

    local migration_count=0
    local applied_count=0

    for migration in "$migrations_dir"/*.sh; do
        [[ -f "$migration" ]] || continue
        migration_count=$((migration_count + 1))

        # Read MIN_VERSION from the script (grep the variable assignment)
        local min_ver
        min_ver=$(grep -oP '^MIN_VERSION="\K[^"]+' "$migration" 2>/dev/null || true)
        if [[ -z "$min_ver" ]]; then
            echo -e "${YELLOW}  Skipping $(basename "$migration"): no MIN_VERSION declared${NC}"
            continue
        fi

        # Version gate: skip only if installed version is STRICTLY past the
        # boundary. When installed == MIN_VERSION, still run — the migration
        # may have been added mid-cycle (e.g., 8.3.2-A shipped without it,
        # 8.3.2-B added it, both report version "8.3.2"). Migrations are
        # required to be idempotent (PRAGMA / IF NOT EXISTS guards), so
        # running them again on the boundary is a safe no-op.
        # "unknown" (fresh install) always qualifies — run everything.
        if [[ "$installed_version" != "unknown" ]]; then
            set +e
            compare_versions "$installed_version" "$min_ver"
            local cmp=$?
            set -e
            # cmp=0: equal (boundary — run, idempotent guard handles no-op)
            # cmp=1: installed > min (past it — skip)
            # cmp=2: installed < min (below boundary — run)
            if [[ $cmp -eq 1 ]]; then
                continue
            fi
        fi

        local migration_name
        migration_name=$(basename "$migration" .sh)
        echo -e "${BLUE}  Data migration: ${migration_name} (boundary: v${min_ver})${NC}"

        # Export context for the migration script
        export DB_PATH="$db_path"
        export VENV_PYTHON="$venv_python"
        export APP_DIR="$target"
        export USE_SUDO="$use_sudo"
        export DRY_RUN
        export INTERACTIVE="$interactive"

        # Support two migration styles:
        #   (a) top-level commands: work is done during `source`
        #   (b) function-pattern: script defines `run_migration` and expects
        #       the dispatcher to invoke it after sourcing
        # We always source; then if `run_migration` is defined we invoke it
        # and unset it so it can't leak into the next iteration.
        source "$migration"
        if declare -F run_migration >/dev/null 2>&1; then
            if ! run_migration; then
                echo -e "${YELLOW}  Migration ${migration_name} reported non-zero exit${NC}"
            fi
            unset -f run_migration
        fi
        applied_count=$((applied_count + 1))
    done

    if [[ $applied_count -gt 0 ]]; then
        echo -e "${GREEN}  Applied $applied_count data migration(s)${NC}"
    elif [[ $migration_count -gt 0 ]]; then
        echo "  No data migrations needed for this version range"
    fi
}

enable_new_services() {
    # Enable the full audiobook unit set for persistence across reboots.
    # Idempotent — already-enabled services are silently skipped. Safe to run
    # on every upgrade (minor, patch, or major).
    #
    # Covers two disjoint sets:
    #   1. Target-wanted units: parsed from audiobook.target Wants= lines
    #      (currently: api, proxy, redirect, converter, mover, stream-translate,
    #      scheduler, downloader.timer)
    #   2. Standalone timers that fire outside audiobook.target but ship with
    #      the project and must be enabled for scheduled work:
    #      - audiobook-enrichment.timer (metadata enrichment)
    #
    # Historical bug (v8.3.1 → 8.3.2 QA): this function was gated on
    # MAJOR_VERSION=true, so patch upgrades shipped new units (stream-translate)
    # that never got enabled. After host reboot, qalib.thebosco.club returned
    # Cloudflare 502 because nothing started at boot. The gate has been
    # removed; the function now runs on every upgrade.
    #
    # The explicit reference to audiobook-stream-translate.service is required
    # by library/tests/test_stream_translate_wiring.py to guard against the
    # v8.3.1 orphan-script regression class.
    local use_sudo="${1:-}"

    # Skip only if the target file hasn't been installed yet (bare system).
    # Note: use_sudo may legitimately be empty when upgrade.sh runs as root
    # (--remote mode sudos once at the boundary and then everything inside is
    # already privileged). Do NOT gate on use_sudo — an empty value is valid.
    if [[ ! -f "/etc/systemd/system/audiobook.target" ]]; then
        return 0
    fi

    echo -e "${BLUE}Enabling all audiobook services and timers...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would enable audiobook.target + all Wants= units + standalone timers"
        return 0
    fi

    # Ensure the target itself is enabled so multi-user.target pulls it in at boot
    $use_sudo systemctl enable audiobook.target 2>/dev/null || true

    # (1) Parse Wants= lines from the target file
    local target_wants
    target_wants=$(grep '^Wants=' /etc/systemd/system/audiobook.target \
        | sed 's/Wants=//' | tr ' ' '\n' \
        | grep -v 'network-online' | grep -v '^$')

    for svc in $target_wants; do
        $use_sudo systemctl enable "$svc" 2>/dev/null || true
        echo "  Enabled: $svc"
    done

    # (2) Standalone units not declared in audiobook.target Wants=
    #     These either schedule their own services (timers) or hook reboot
    #     targets (shutdown-saver). Must be enabled explicitly.
    local standalone_units=(
        audiobook-enrichment.timer       # backfill un-enriched metadata
        audiobook-shutdown-saver.service # saves tmpfs staging before reboot/halt
    )
    for unit in "${standalone_units[@]}"; do
        if [[ -f "/etc/systemd/system/${unit}" ]]; then
            $use_sudo systemctl enable "$unit" 2>/dev/null || true
            echo "  Enabled: $unit"
        fi
    done

    # Belt-and-suspenders: explicit enable for streaming translation worker.
    # Required by library/tests/test_stream_translate_wiring.py.
    $use_sudo systemctl enable audiobook-stream-translate.service 2>/dev/null || true

    echo -e "${GREEN}  All services and timers enabled${NC}"
}

# -----------------------------------------------------------------------------
# Audit & Cleanup (runs on every upgrade)
# -----------------------------------------------------------------------------

audit_and_cleanup() {
    # Post-sync audit: remove broken symlinks, stale units, legacy files.
    # Runs on every upgrade (not gated by --major-version). Idempotent.
    local target="$1"
    local use_sudo="${2:-}"

    echo ""
    echo -e "${BLUE}=== Post-Upgrade Audit & Cleanup ===${NC}"

    local issues=0

    # --- (a) Broken symlinks in /usr/local/bin ---
    echo -e "${BLUE}Checking for broken symlinks in /usr/local/bin...${NC}"
    local broken_links
    mapfile -t broken_links < <(find /usr/local/bin -name "audiobook*" -xtype l 2>/dev/null)
    if [[ ${#broken_links[@]} -gt 0 ]]; then
        for link in "${broken_links[@]}"; do
            local link_target
            link_target=$(readlink "$link" 2>/dev/null || echo "unknown")
            if [[ "$DRY_RUN" == "true" ]]; then
                echo -e "  ${YELLOW}[DRY-RUN] Would remove broken symlink: $link -> $link_target${NC}"
            else
                if [[ -n "$use_sudo" ]]; then
                    sudo rm -f "$link"
                else
                    rm -f "$link"
                fi
                echo -e "  ${GREEN}Removed broken symlink: $link -> $link_target${NC}"
            fi
            issues=$((issues + 1))
        done
    else
        echo -e "  ${GREEN}No broken symlinks found${NC}"
    fi

    # --- (b) Stale legacy symlinks (wrong target) ---
    echo -e "${BLUE}Checking for stale legacy symlinks...${NC}"
    local legacy_found=0
    while IFS= read -r link; do
        [[ -z "$link" ]] && continue
        local link_target
        link_target=$(readlink "$link" 2>/dev/null || echo "")
        # Flag symlinks pointing to /usr/local/lib/audiobooks/ instead of /opt/audiobooks/scripts/
        if [[ "$link_target" == /usr/local/lib/audiobooks/* ]]; then
            local script_name
            script_name=$(basename "$link_target")
            local correct_target="${target}/scripts/${script_name}"
            if [[ -f "$correct_target" ]]; then
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo -e "  ${YELLOW}[DRY-RUN] Would relink: $link -> $correct_target (was $link_target)${NC}"
                else
                    if [[ -n "$use_sudo" ]]; then
                        sudo rm -f "$link"
                        sudo ln -s "$correct_target" "$link"
                    else
                        rm -f "$link"
                        ln -s "$correct_target" "$link"
                    fi
                    echo -e "  ${GREEN}Relinked: $link -> $correct_target (was $link_target)${NC}"
                fi
                legacy_found=$((legacy_found + 1))
                issues=$((issues + 1))
            fi
        fi
    done < <(find /usr/local/bin -name "audiobook*" -type l 2>/dev/null)
    if [[ $legacy_found -eq 0 ]]; then
        echo -e "  ${GREEN}No stale legacy symlinks found${NC}"
    fi

    # --- (c) Orphaned systemd units ---
    echo -e "${BLUE}Checking for orphaned systemd units...${NC}"
    local orphan_found=0
    local project_systemd_dir="${target}/systemd"
    # Fall back to the project source if target doesn't have systemd/ yet
    [[ ! -d "$project_systemd_dir" ]] && project_systemd_dir="${SCRIPT_DIR}/systemd"
    while IFS= read -r unit_path; do
        [[ -z "$unit_path" ]] && continue
        local unit_name
        unit_name=$(basename "$unit_path")
        # Skip the .wants directory (managed by systemd enable/disable)
        [[ "$unit_path" == *".wants/"* ]] && continue
        # Skip non-unit files (e.g., audiobooks-tmpfiles.conf in /etc/systemd is unlikely but be safe)
        [[ "$unit_name" != *.service && "$unit_name" != *.timer && "$unit_name" != *.path && "$unit_name" != *.target ]] && continue
        # Check if this unit exists in the project's systemd/ directory
        if [[ ! -f "${project_systemd_dir}/${unit_name}" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo -e "  ${YELLOW}[DRY-RUN] Would remove orphaned unit: $unit_name${NC}"
            else
                # $use_sudo is "" when running as root, "sudo" when non-root.
                # Either way, we have (or need and have) the privilege to rm.
                $use_sudo systemctl disable "$unit_name" 2>/dev/null || true
                $use_sudo systemctl stop "$unit_name" 2>/dev/null || true
                $use_sudo rm -f "$unit_path"
                echo -e "  ${GREEN}Removed orphaned unit: $unit_name${NC}"
            fi
            orphan_found=$((orphan_found + 1))
            issues=$((issues + 1))
        fi
    done < <(find /etc/systemd/system -maxdepth 1 -name "audiobook*" -type f 2>/dev/null)
    if [[ $orphan_found -eq 0 ]]; then
        echo -e "  ${GREEN}No orphaned systemd units found${NC}"
    fi

    # --- (c2) Orphaned drop-in fragments referencing removed services ---
    # When an orphan unit (e.g. audiobook-secrets.service) is removed in (c),
    # any drop-in fragment under /etc/systemd/system/*.service.d/*.conf that
    # references it via Requires=/After= will make the parent service fail to
    # start with "Unit audiobook-secrets.service not found". Sweep drop-in
    # dirs for any .conf that points at a removed audiobook-* unit.
    local dropin_found=0
    while IFS= read -r _conf; do
        [[ -z "$_conf" ]] && continue
        # Extract every audiobook-*.service referenced by this drop-in
        while IFS= read -r _ref; do
            [[ -z "$_ref" ]] && continue
            # If the referenced unit doesn't ship in the project, the drop-in is orphaned
            if [[ ! -f "${project_systemd_dir}/${_ref}" ]] \
                && [[ ! -f "/etc/systemd/system/${_ref}" ]]; then
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo -e "  ${YELLOW}[DRY-RUN] Would remove orphan drop-in: $_conf (ref to $_ref)${NC}"
                else
                    $use_sudo rm -f "$_conf"
                    echo -e "  ${GREEN}Removed orphan drop-in: $(basename "$_conf") (referenced missing $_ref)${NC}"
                    # Best-effort: if the drop-in dir is now empty, drop it.
                    $use_sudo rmdir "$(dirname "$_conf")" 2>/dev/null || true
                fi
                dropin_found=$((dropin_found + 1))
                issues=$((issues + 1))
                break
            fi
        done < <(grep -hoE 'audiobook-[a-zA-Z0-9_-]+\.service' "$_conf" 2>/dev/null | sort -u)
    done < <(find /etc/systemd/system -maxdepth 3 -path '*.service.d/*.conf' -type f 2>/dev/null)
    if [[ $dropin_found -eq 0 ]]; then
        echo -e "  ${GREEN}No orphan drop-ins found${NC}"
    else
        $use_sudo systemctl daemon-reload 2>/dev/null || true
    fi

    # --- (d) Legacy files in the app directory ---
    echo -e "${BLUE}Checking for legacy files in ${target}...${NC}"
    local legacy_files=(
        "$target/library/launch-v3.sh"
        "$target/install-services.sh"
        "$target/install-system.sh"
        "$target/install-user.sh"
        "$target/deploy.sh"
        "$target/deploy-vm.sh"
    )
    for legacy_file in "${legacy_files[@]}"; do
        if [[ -f "$legacy_file" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo -e "  ${YELLOW}[DRY-RUN] Would remove legacy file: $legacy_file${NC}"
            else
                if [[ -n "$use_sudo" ]]; then
                    sudo rm -f "$legacy_file"
                else
                    rm -f "$legacy_file"
                fi
                echo -e "  ${GREEN}Removed legacy file: $legacy_file${NC}"
            fi
            issues=$((issues + 1))
        fi
    done
    # Warn about waitress files in venv (venv rebuild handles these)
    local waitress_count
    waitress_count=$(find "$target/library/venv/" -name "*waitress*" 2>/dev/null | wc -l)
    if [[ "$waitress_count" -gt 0 ]]; then
        echo -e "  ${YELLOW}Found $waitress_count waitress-related file(s) in venv — will be cleaned on next venv rebuild (--major-version)${NC}"
    fi

    # --- (e) Stale config references ---
    echo -e "${BLUE}Checking for stale config references...${NC}"
    local conf_file="/etc/audiobooks/audiobooks.conf"
    if [[ -f "$conf_file" ]]; then
        if grep -q "AUDIOBOOKS_USE_WAITRESS" "$conf_file" 2>/dev/null; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo -e "  ${YELLOW}[DRY-RUN] Would remove AUDIOBOOKS_USE_WAITRESS from $conf_file${NC}"
            else
                if [[ -n "$use_sudo" ]]; then
                    sudo sed -i '/AUDIOBOOKS_USE_WAITRESS/d' "$conf_file"
                else
                    sed -i '/AUDIOBOOKS_USE_WAITRESS/d' "$conf_file"
                fi
                echo -e "  ${GREEN}Removed AUDIOBOOKS_USE_WAITRESS from $conf_file${NC}"
            fi
            issues=$((issues + 1))
        else
            echo -e "  ${GREEN}No stale config references found${NC}"
        fi
    else
        echo -e "  ${GREEN}No config file to check (not a system install)${NC}"
    fi

    # --- (f) Legacy app directory ---
    echo -e "${BLUE}Checking for legacy install location...${NC}"
    if [[ -d "/usr/local/lib/audiobooks" ]]; then
        echo -e "  ${YELLOW}WARNING: Legacy install directory /usr/local/lib/audiobooks still exists${NC}"
        echo -e "  ${YELLOW}  This is the old install location. Consider removing it:${NC}"
        echo -e "  ${YELLOW}  sudo rm -rf /usr/local/lib/audiobooks${NC}"
        issues=$((issues + 1))
    else
        echo -e "  ${GREEN}No legacy install directory found${NC}"
    fi

    # --- (g) Orphaned scripts in target/scripts/ ---
    # Mirror of section (c) for systemd units: scripts retired from the
    # project must not survive on installed systems. Added 2026-04-19 after
    # v8.3.2 retirement (fleet-watchdog.sh, translation-check.sh,
    # translation-daemon.sh) left 3 files on QA because the scripts/ copy
    # loop above uses per-file `cp` (no rsync --delete) and the legacy_files
    # list in (d) is a hand-maintained allowlist, not a diff.
    echo -e "${BLUE}Checking for orphaned scripts in ${target}/scripts/...${NC}"
    local orphan_scripts=0
    local project_scripts_dir="${PROJECT_DIR:-${SCRIPT_DIR}}/scripts"
    # Scripts copied into target/scripts/ from project root (see
    # "Upgrade root-level management scripts" block above). They live at
    # PROJECT_DIR, not PROJECT_DIR/scripts, so without this allowlist the
    # diff below would flag them as orphans on every upgrade.
    local root_level_scripts=(upgrade.sh migrate-api.sh)
    if [[ -d "${target}/scripts" ]] && [[ -d "${project_scripts_dir}" ]]; then
        while IFS= read -r installed_script; do
            [[ -z "$installed_script" ]] && continue
            local script_name
            script_name=$(basename "$installed_script")
            # Allowlist scripts sourced from the project root rather than scripts/
            local is_allowlisted=0
            for root_script in "${root_level_scripts[@]}"; do
                if [[ "$script_name" == "$root_script" ]]; then
                    is_allowlisted=1
                    break
                fi
            done
            [[ $is_allowlisted -eq 1 ]] && continue
            # If the script still ships in the project, it's current — keep it
            if [[ ! -f "${project_scripts_dir}/${script_name}" ]]; then
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo -e "  ${YELLOW}[DRY-RUN] Would remove orphan script: $script_name${NC}"
                else
                    $use_sudo rm -f "$installed_script"
                    echo -e "  ${GREEN}Removed orphan script: $script_name${NC}"
                fi
                orphan_scripts=$((orphan_scripts + 1))
                issues=$((issues + 1))
            fi
        done < <(find "${target}/scripts" -maxdepth 1 -type f 2>/dev/null)
    fi
    if [[ $orphan_scripts -eq 0 ]]; then
        echo -e "  ${GREEN}No orphaned scripts found${NC}"
    fi

    # Summary
    echo ""
    if [[ $issues -gt 0 ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            echo -e "${YELLOW}Audit found $issues issue(s) (dry-run — no changes made)${NC}"
        else
            echo -e "${GREEN}Audit complete — resolved $issues issue(s)${NC}"
        fi
    else
        echo -e "${GREEN}Audit complete — installation is clean${NC}"
    fi
}

# -----------------------------------------------------------------------------
# Preflight Check System
# -----------------------------------------------------------------------------

generate_preflight() {
    # Generate a preflight report for the pending upgrade.
    # Writes JSON to ${AUDIOBOOKS_VAR_DIR}/.control/upgrade-preflight.json.
    # Called during --check mode and before the main upgrade begins.
    #
    # Arguments:
    #   $1 - project dir (source)
    #   $2 - target dir (installed)
    local project="${1:-$PROJECT_DIR}"
    local target="${2:-$TARGET_DIR}"

    local var_dir="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
    local control_dir="${var_dir}/.control"
    local preflight_file="${control_dir}/upgrade-preflight.json"

    # Determine versions
    local current_version
    current_version=$(get_version "$target")
    local target_version
    target_version=$(get_version "$project")

    # Determine upgrade source identifier
    local source_id
    if [[ "$UPGRADE_SOURCE" == "github" ]]; then
        source_id="github:${REQUESTED_VERSION:-latest}"
    else
        source_id="project:${project}"
    fi

    # Determine if this is a major version bump (first digit change)
    local is_major="false"
    local cur_major
    cur_major=$(echo "$current_version" | cut -d. -f1)
    local new_major
    new_major=$(echo "$target_version" | cut -d. -f1)
    if [[ "$cur_major" != "$new_major" ]] && [[ "$cur_major" != "unknown" ]] && [[ "$new_major" != "unknown" ]]; then
        is_major="true"
    fi

    # Detect if venv rebuild is needed (major version or requirements.txt changed)
    local venv_rebuild_needed="false"
    if [[ "$is_major" == "true" ]] || [[ "$MAJOR_VERSION" == "true" ]]; then
        venv_rebuild_needed="true"
    elif [[ -f "${project}/library/requirements.txt" ]] && [[ -f "${target}/library/requirements.txt" ]]; then
        if ! diff -q "${project}/library/requirements.txt" "${target}/library/requirements.txt" >/dev/null 2>&1; then
            venv_rebuild_needed="true"
        fi
    fi

    # Detect config changes (new keys in audiobooks.conf template)
    local config_changes="false"
    if [[ -d "${project}/config-migrations" ]]; then
        local migration_count
        migration_count=$(find "${project}/config-migrations" -name "*.sh" 2>/dev/null | wc -l)
        if [[ "$migration_count" -gt 0 ]]; then
            config_changes="true"
        fi
    fi

    # Detect new systemd services in project vs installed
    local new_services="[]"
    if [[ -d "${project}/systemd" ]] && [[ -d "${target}/systemd" ]]; then
        local new_svc_list=""
        for svc in "${project}/systemd/"*.service; do
            [[ -f "$svc" ]] || continue
            local svc_name
            svc_name=$(basename "$svc")
            if [[ ! -f "${target}/systemd/${svc_name}" ]]; then
                new_svc_list="${new_svc_list}\"${svc_name}\","
            fi
        done
        if [[ -n "$new_svc_list" ]]; then
            new_services="[${new_svc_list%,}]"
        fi
    fi

    # Count changed files (rough estimate from library and scripts)
    local files_changed=0
    for check_dir in "library" "scripts"; do
        if [[ -d "${project}/${check_dir}" ]] && [[ -d "${target}/${check_dir}" ]]; then
            local changed
            changed=$(diff -rq --exclude="*.pyc" --exclude="__pycache__" \
                "${project}/${check_dir}" "${target}/${check_dir}" 2>/dev/null | wc -l || echo "0")
            files_changed=$((files_changed + changed))
        fi
    done

    # Collect warnings
    local warnings="[]"
    local warn_list=""
    if [[ "$is_major" == "true" ]]; then
        warn_list="${warn_list}\"Major version upgrade — manual review recommended\","
    fi

    # Check disk space: estimate 200MB needed for upgrade
    local disk_free_kb
    disk_free_kb=$(df -k "$target" 2>/dev/null | awk 'NR==2{print $4}' || echo "999999")
    if [[ "$disk_free_kb" -lt 204800 ]]; then
        warn_list="${warn_list}\"Low disk space: ${disk_free_kb}KB free at ${target}\","
    fi

    if [[ -n "$warn_list" ]]; then
        warnings="[${warn_list%,}]"
    fi

    # Ensure control directory exists
    if [[ ! -d "$control_dir" ]]; then
        if [[ ! -w "$var_dir" ]]; then
            sudo mkdir -p "$control_dir"
            sudo chown audiobooks:audiobooks "$control_dir" 2>/dev/null || true
        else
            mkdir -p "$control_dir"
        fi
    fi

    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Write JSON report using printf (no jq dependency)
    local tmp_file
    tmp_file=$(mktemp)
    printf '{
  "timestamp": "%s",
  "source": "%s",
  "current_version": "%s",
  "target_version": "%s",
  "is_major": %s,
  "venv_rebuild_needed": %s,
  "config_changes": %s,
  "new_services": %s,
  "files_changed": %d,
  "warnings": %s
}\n' \
        "$timestamp" \
        "$source_id" \
        "$current_version" \
        "$target_version" \
        "$is_major" \
        "$venv_rebuild_needed" \
        "$config_changes" \
        "$new_services" \
        "$files_changed" \
        "$warnings" >"$tmp_file"

    if [[ ! -w "$control_dir" ]]; then
        sudo mv "$tmp_file" "$preflight_file"
        sudo chown audiobooks:audiobooks "$preflight_file" 2>/dev/null || true
        sudo chmod 644 "$preflight_file"
    else
        mv "$tmp_file" "$preflight_file"
        chmod 644 "$preflight_file"
    fi

    echo -e "${BLUE}Preflight report written: $preflight_file${NC}"
    echo "  Source:          $source_id"
    echo "  Current version: $current_version"
    echo "  Target version:  $target_version"
    echo "  Major upgrade:   $is_major"
    echo "  Venv rebuild:    $venv_rebuild_needed"
    echo "  Config changes:  $config_changes"
    echo "  Files changed:   $files_changed"
    if [[ "$warnings" != "[]" ]]; then
        echo -e "  ${YELLOW}Warnings: $warnings${NC}"
    fi
}

validate_preflight() {
    # Validate the preflight report before proceeding with an upgrade.
    # Returns 0 (valid/proceed) or 1 (invalid — caller should abort or re-run check).
    #
    # Arguments:
    #   $1 - project dir (source), used to verify source matches
    local project="${1:-$PROJECT_DIR}"

    # --force bypasses preflight validation (but NOT backup)
    if [[ "$FORCE" == "true" ]]; then
        echo -e "${YELLOW}Warning: --force specified — skipping preflight validation.${NC}"
        return 0
    fi

    local var_dir="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
    local preflight_file="${var_dir}/.control/upgrade-preflight.json"

    # Check file exists
    if [[ ! -f "$preflight_file" ]]; then
        echo -e "${RED}Preflight check required before upgrade.${NC}"
        echo "Run: ./upgrade.sh --check --from-project $project --target $TARGET_DIR"
        echo "Then re-run the upgrade."
        return 1
    fi

    # Check timestamp freshness (< 30 minutes)
    local file_mtime
    file_mtime=$(stat -c %Y "$preflight_file" 2>/dev/null || echo "0")
    local now
    now=$(date +%s)
    local age_seconds=$((now - file_mtime))
    local max_age=1800 # 30 minutes

    if [[ "$age_seconds" -gt "$max_age" ]]; then
        echo -e "${RED}Preflight report is stale (${age_seconds}s old, max ${max_age}s).${NC}"
        echo "Re-run: ./upgrade.sh --check --from-project $project --target $TARGET_DIR"
        return 1
    fi

    # Check source matches current request
    local expected_source
    if [[ "$UPGRADE_SOURCE" == "github" ]]; then
        expected_source="github:${REQUESTED_VERSION:-latest}"
    else
        expected_source="project:${project}"
    fi

    local recorded_source
    recorded_source=$(grep -oP '"source":\s*"\K[^"]+' "$preflight_file" 2>/dev/null || echo "")

    if [[ "$recorded_source" != "$expected_source" ]]; then
        echo -e "${RED}Preflight source mismatch.${NC}"
        echo "  Expected: $expected_source"
        echo "  Recorded: $recorded_source"
        echo "Re-run: ./upgrade.sh --check --from-project $project --target $TARGET_DIR"
        return 1
    fi

    echo -e "${GREEN}Preflight validated (${age_seconds}s old, source: $recorded_source)${NC}"
    return 0
}

# -----------------------------------------------------------------------------
# Core Upgrade
# -----------------------------------------------------------------------------

do_upgrade() {
    # Disable errexit inside the upgrade — functions like compare_versions use
    # nonzero return codes for flow control, and the EXIT trap (_cleanup_on_exit)
    # handles catastrophic failures by restarting services.
    set +e

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
    # Dev-only scripts (git hooks, dev-machine admin tools) stay in the project
    if [[ -d "$target/scripts" ]]; then
        echo -e "${BLUE}Upgrading scripts...${NC}"
        for script in "${project}/scripts/"*; do
            if [[ -f "$script" ]] && [[ "$(basename "$script")" != "__pycache__" ]]; then
                local script_name=$(basename "$script")
                case "$script_name" in
                    install-hooks.sh | purge-users.sh | setup-email.sh) continue ;;
                esac
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
            echo "  [DRY-RUN] Would update: audiobook-config.sh"
        else
            if [[ -n "$use_sudo" ]]; then
                sudo cp "${project}/lib/audiobook-config.sh" "$target/lib/"
            else
                cp "${project}/lib/audiobook-config.sh" "$target/lib/"
            fi
            echo "  Updated: audiobook-config.sh"
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

    # Update active systemd services and helper configuration.
    # Gate only on /etc/systemd/system existing — $use_sudo is a command prefix,
    # empty when we're already root. Gating on `-n $use_sudo` was a bug that
    # skipped unit installation entirely when upgrade.sh ran under sudo.
    if [[ -d "/etc/systemd/system" ]]; then
        echo -e "${BLUE}Updating systemd service files...${NC}"

        # Copy new/updated service, target, path, and timer units
        for unit_file in "${project}/systemd/"*.service "${project}/systemd/"*.target "${project}/systemd/"*.path "${project}/systemd/"*.timer; do
            if [[ -f "$unit_file" ]]; then
                local unit_name=$(basename "$unit_file")
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo "  [DRY-RUN] Would install: $unit_name"
                else
                    $use_sudo cp "$unit_file" "/etc/systemd/system/${unit_name}"
                    $use_sudo chmod 644 "/etc/systemd/system/${unit_name}"
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
            local api_svc="/etc/systemd/system/audiobook-api.service"
            if [[ -f "$api_svc" ]] && sudo grep -q "ReadWritePaths=" "$api_svc" 2>/dev/null; then
                if [[ "$DRY_RUN" == "true" ]]; then
                    echo "  [DRY-RUN] Would patch ReadWritePaths += ${conf_data_dir}"
                else
                    sudo sed -i "s|ReadWritePaths=\(.*\)|ReadWritePaths=\1 ${conf_data_dir}|" "$api_svc"
                    echo "  Patched: audiobook-api.service ReadWritePaths += ${conf_data_dir}"
                    # Also update RequiresMountsFor so systemd waits for the mount
                    if sudo grep -q "RequiresMountsFor=" "$api_svc" 2>/dev/null; then
                        sudo sed -i "s|RequiresMountsFor=\(.*\)|RequiresMountsFor=\1 ${conf_data_dir}|" "$api_svc"
                        echo "  Patched: audiobook-api.service RequiresMountsFor += ${conf_data_dir}"
                    fi
                fi
            fi
        fi

        # Install/update logrotate configuration
        if [[ -f "${project}/config/logrotate-audiobooks" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would update logrotate configuration"
            else
                sudo cp "${project}/config/logrotate-audiobooks" /etc/logrotate.d/audiobooks
                sudo chmod 644 /etc/logrotate.d/audiobooks
                echo "  Updated: /etc/logrotate.d/audiobooks"
            fi
        fi

        # Install/update tmpfiles.d configuration for runtime directories
        if [[ -f "${project}/systemd/audiobooks-tmpfiles.conf" ]]; then
            if [[ "$DRY_RUN" == "true" ]]; then
                echo "  [DRY-RUN] Would update tmpfiles.d configuration"
            else
                sudo cp "${project}/systemd/audiobooks-tmpfiles.conf" /etc/tmpfiles.d/audiobooks.conf
                sudo chmod 644 /etc/tmpfiles.d/audiobooks.conf
                # Ensure runtime directories exist
                sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf 2>/dev/null || {
                    # Fallback: create directories manually if tmpfiles fails
                    local var_dir="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
                    local staging="${AUDIOBOOKS_STAGING:-/tmp/audiobook-staging}"
                    local stream_audio="${AUDIOBOOKS_STREAMING_AUDIO_DIR:-${var_dir}/streaming-audio}"
                    local stream_subtitles="${AUDIOBOOKS_STREAMING_SUBTITLES_DIR:-${var_dir}/streaming-subtitles}"
                    sudo mkdir -p "${var_dir}/.control" "${var_dir}/.run" "$staging" "$stream_audio" "$stream_subtitles"
                    sudo chown audiobooks:audiobooks "${var_dir}/.control" "${var_dir}/.run" "$staging" "$stream_audio" "$stream_subtitles"
                    sudo chmod 755 "${var_dir}/.control"
                    sudo chmod 775 "${var_dir}/.run" "$staging"
                    sudo chmod 750 "$stream_audio"
                    sudo chmod 755 "$stream_subtitles"
                }
                echo "  Updated: tmpfiles.d/audiobooks.conf"
            fi
        fi

        # Sync Caddy files if Caddy is installed
        if command -v caddy &>/dev/null && [[ -d "${project}/caddy" ]]; then
            echo -e "${BLUE}Upgrading Caddy maintenance page...${NC}"
            local caddy_changed=false
            for caddy_file in audiobooks.conf maintenance.html; do
                local src="${project}/caddy/${caddy_file}"
                local dst
                if [[ "$caddy_file" == "audiobooks.conf" ]]; then
                    dst="/etc/caddy/conf.d/audiobooks.conf"
                else
                    dst="/etc/caddy/${caddy_file}"
                fi
                if [[ -f "$src" ]]; then
                    # For audiobooks.conf, substitute native + docker upstream ports
                    # into the two-site template (:8084 native, :8085 docker).
                    local src_content
                    if [[ "$caddy_file" == "audiobooks.conf" ]]; then
                        # Read ports directly from the installed conf file —
                        # upgrade.sh does not source it (can't safely eval
                        # arbitrary shell from a config). Shell env is a
                        # secondary override, defaults match lib/audiobook-config.sh.
                        # v8.3.2 QA drift: without this, dual-stack hosts like
                        # QA (WEB_PORT=8090) silently fell through to 8443 and
                        # Caddy :8084 proxied into the void, producing 502.
                        local native_port=""
                        local docker_port=""
                        if [[ -f "/etc/audiobooks/audiobooks.conf" ]]; then
                            native_port=$(grep -oP '^AUDIOBOOKS_WEB_PORT="?\K[^"]*' \
                                /etc/audiobooks/audiobooks.conf 2>/dev/null | head -1)
                            docker_port=$(grep -oP '^AUDIOBOOKS_DOCKER_PORT="?\K[^"]*' \
                                /etc/audiobooks/audiobooks.conf 2>/dev/null | head -1)
                        fi
                        native_port="${native_port:-${AUDIOBOOKS_WEB_PORT:-8443}}"
                        docker_port="${docker_port:-${AUDIOBOOKS_DOCKER_PORT:-8443}}"
                        src_content=$(sed -e "s|__NATIVE_PORT__|${native_port}|g" \
                            -e "s|__DOCKER_PORT__|${docker_port}|g" "$src")
                    else
                        src_content=$(cat "$src")
                    fi
                    # Compare with installed version
                    if [[ ! -f "$dst" ]] || [[ "$src_content" != "$(cat "$dst" 2>/dev/null)" ]]; then
                        if [[ "$DRY_RUN" == "true" ]]; then
                            echo "  [DRY-RUN] Would update: $caddy_file"
                        else
                            sudo mkdir -p "$(dirname "$dst")"
                            echo "$src_content" | sudo tee "$dst" >/dev/null
                            caddy_changed=true
                            echo "  Updated: $caddy_file"
                        fi
                    fi
                fi
            done
            if [[ "$caddy_changed" == "true" ]]; then
                sudo systemctl reload caddy 2>/dev/null || true
            fi
        fi

        # Reload systemd to pick up changes
        if [[ "$DRY_RUN" == "false" ]]; then
            sudo systemctl daemon-reload

            # Enable and start the privileged helper path unit if not already running
            if [[ -f "/etc/systemd/system/audiobook-upgrade-helper.path" ]]; then
                sudo systemctl enable audiobook-upgrade-helper.path 2>/dev/null || true
                sudo systemctl start audiobook-upgrade-helper.path 2>/dev/null || true
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

        # Update reference-system.yml (shipped alongside VERSION so the
        # /api/system/reference-system endpoint has its snapshot after upgrade)
        if [[ -f "${project}/docs/reference-system.yml" ]]; then
            if [[ -n "$use_sudo" ]]; then
                sudo cp "${project}/docs/reference-system.yml" "$target/" 2>/dev/null || true
            else
                cp "${project}/docs/reference-system.yml" "$target/" 2>/dev/null || true
            fi
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

    # Venv management
    if [[ -d "$target/library" ]]; then
        if [[ "$MAJOR_VERSION" == "true" ]]; then
            # Major version: force complete venv rebuild (new deps in, old deps out)
            force_venv_rebuild "$target" "$use_sudo"
        elif [[ "$DRY_RUN" == "false" ]]; then
            # Normal upgrade: only recreate if broken or pointing to /home/ (pyenv)
            # systemd ProtectHome=yes blocks access to /home/, breaking pyenv-created venvs
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
            else
                # Venv exists and works — sync dependencies from requirements.txt
                # so new packages (added between releases) get installed
                echo -e "${BLUE}Syncing Python dependencies...${NC}"
                if [[ -n "$use_sudo" ]]; then
                    sudo -u audiobooks "$target/library/venv/bin/pip" install --quiet \
                        -r "$target/library/requirements.txt" 2>/dev/null \
                        && echo -e "${GREEN}  Dependencies synced${NC}" \
                        || echo -e "${YELLOW}  pip sync had warnings (non-fatal)${NC}"
                else
                    "$target/library/venv/bin/pip" install --quiet \
                        -r "$target/library/requirements.txt" 2>/dev/null \
                        && echo -e "${GREEN}  Dependencies synced${NC}" \
                        || echo -e "${YELLOW}  pip sync had warnings (non-fatal)${NC}"
                fi
            fi
        fi
    fi

    # Sync audible-cli isolated venv
    if [[ "$DRY_RUN" == "false" ]]; then
        local audible_venv="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}/audible-venv"
        if [[ -d "$audible_venv" ]]; then
            echo -e "${BLUE}Syncing audible-cli dependencies...${NC}"
            if [[ -n "$use_sudo" ]]; then
                sudo -u audiobooks "$audible_venv/bin/pip" install --quiet --upgrade audible-cli 2>/dev/null \
                    && echo -e "${GREEN}  audible-cli synced${NC}" \
                    || echo -e "${YELLOW}  audible-cli sync had warnings (non-fatal)${NC}"
            else
                "$audible_venv/bin/pip" install --quiet --upgrade audible-cli 2>/dev/null \
                    && echo -e "${GREEN}  audible-cli synced${NC}" \
                    || echo -e "${YELLOW}  audible-cli sync had warnings (non-fatal)${NC}"
            fi
        else
            echo -e "${YELLOW}  audible-cli venv not found at $audible_venv — run install.sh to create${NC}"
        fi
    fi

    # Apply database schema migrations
    if [[ "$DRY_RUN" == "false" ]]; then
        apply_schema_migrations "$target" "${use_sudo}"
    fi

    # Major version: config migration + service enablement
    # Config migrations are idempotent and always run — they're the only way
    # stale config drift from older install.sh versions gets corrected.
    apply_config_migrations "$project" "${use_sudo}"

    # Data-state migrations: reclassify/backfill DB rows when crossing
    # a version boundary that changed classification or enrichment logic.
    # Version-gated (only runs when upgrade crosses the boundary), idempotent.
    if [[ "$DRY_RUN" == "false" ]]; then
        apply_data_migrations "$project" "$target" "${use_sudo}" "true"
    else
        apply_data_migrations "$project" "$target" "${use_sudo}" "false"
    fi

    # Enable the full audiobook unit set on every upgrade (not just major
    # bumps). systemctl enable is idempotent; running it unconditionally cures
    # the v8.3.1→8.3.2 class of bug where new units shipped in a patch release
    # were never enabled, causing 502s after host reboot.
    enable_new_services "${use_sudo}"

    # Refresh /usr/local/bin symlinks to point to canonical scripts
    if [[ "$target" == "/opt/audiobooks" || "$target" == "/usr/local/lib/audiobooks" ]]; then
        refresh_bin_symlinks "$target" "${use_sudo}"
    fi

    # Purge Cloudflare CDN cache so visitors get fresh assets
    if [[ "$DRY_RUN" == "false" ]]; then
        purge_cloudflare_cache
    fi

    echo ""
    echo -e "${GREEN}=== Upgrade Complete ===${NC}"
    echo "New version: $(get_version "$project")"

    # Verify permissions after upgrade
    verify_installation_permissions "$target"

    # Run audit & cleanup (every upgrade)
    audit_and_cleanup "$target" "$use_sudo"

    # Reconcile filesystem against install manifest. Default is enforce: the
    # items the reconciler acts on (PHANTOM_PATHS, legacy config keys listed
    # in CONFIG_CANONICAL_DEFAULTS, stale __pycache__) are explicitly marked
    # as obsolete in the manifest — the whole point of those arrays is "this
    # must not survive." Report-only meant drift accumulated release after
    # release. Users can still opt into report mode via `RECONCILE_MODE=report`.
    if [[ -f "${project}/scripts/reconcile-filesystem.sh" ]]; then
        local recon_lib_dir="$target"
        local recon_state="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
        local recon_log="${AUDIOBOOKS_LOGS:-/var/log/audiobooks}"
        local recon_config="${AUDIOBOOKS_CONFIG_DIR:-/etc/audiobooks}"
        local recon_systemd="/etc/systemd/system"
        local recon_bin="/usr/local/bin"
        if [[ "$target" != "/opt/audiobooks" && "$target" != "/usr/local/lib/audiobooks" ]]; then
            # User install
            recon_state="${HOME}/.local/state/audiobooks"
            recon_log="${HOME}/.local/state/audiobooks/logs"
            recon_config="${HOME}/.config/audiobooks"
            recon_systemd="${HOME}/.config/systemd/user"
            recon_bin="${HOME}/.local/bin"
        fi
        PROJECT_DIR="$project" \
            LIB_DIR="$recon_lib_dir" \
            STATE_DIR="$recon_state" \
            LOG_DIR="$recon_log" \
            CONFIG_DIR="$recon_config" \
            CONF_FILE="${recon_config}/audiobooks.conf" \
            USE_SUDO="$use_sudo" \
            SYSTEMD_DIR="$recon_systemd" \
            BIN_DIR="$recon_bin" \
            RECONCILE_MODE="${RECONCILE_MODE:-enforce}" \
            DRY_RUN="$DRY_RUN" \
            bash "${project}/scripts/reconcile-filesystem.sh" || true
    fi

    set -e
}

# -----------------------------------------------------------------------------
# Cloudflare Cache Purge (post-deploy)
# -----------------------------------------------------------------------------

purge_cloudflare_cache() {
    # Purge Cloudflare CDN cache after deploying web assets.
    # Non-fatal: if credentials aren't available, just log and continue.
    # Credentials sourced from ~/.config/api-keys.env (shared with cloudflare-manager).
    # When running under sudo, $HOME is /root — use SUDO_USER's home instead.
    local real_home="$HOME"
    if [[ -n "$SUDO_USER" ]]; then
        real_home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    fi
    local cf_keys_file="${CF_KEYS_FILE:-$real_home/.config/api-keys.env}"

    if [[ -f "$cf_keys_file" ]]; then
        source "$cf_keys_file"
    fi

    if [[ -z "$CF_GLOBAL_API_KEY" || -z "$CF_AUTH_EMAIL" ]]; then
        echo -e "${YELLOW}  Cloudflare cache purge skipped (no credentials in $cf_keys_file)${NC}"
        echo -e "${YELLOW}  See: audiobook-purge-cache --help${NC}"
        return 0
    fi

    echo -e "${BLUE}Purging Cloudflare CDN cache...${NC}"

    # Delegate to the standalone script if available
    local purge_script=""
    if [[ -x "${SCRIPT_DIR}/scripts/audiobook-purge-cache" ]]; then
        purge_script="${SCRIPT_DIR}/scripts/audiobook-purge-cache"
    elif [[ -x "$(dirname "$SCRIPT_DIR")/scripts/audiobook-purge-cache" ]]; then
        purge_script="$(dirname "$SCRIPT_DIR")/scripts/audiobook-purge-cache"
    elif command -v audiobook-purge-cache &>/dev/null; then
        purge_script="audiobook-purge-cache"
    fi

    if [[ -n "$purge_script" ]]; then
        if "$purge_script" 2>&1; then
            echo -e "${GREEN}  CDN cache purged${NC}"
        else
            echo -e "${YELLOW}  CDN cache purge failed (non-fatal)${NC}"
        fi
    else
        # Inline fallback if script not found
        local zone_id="${CF_ZONE_ID:-}"
        if [[ -z "$zone_id" ]]; then
            echo -e "${YELLOW}  CDN cache purge skipped (CF_ZONE_ID not set)${NC}"
            return 0
        fi

        local result
        result=$(curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$zone_id/purge_cache" \
            -H "X-Auth-Key: $CF_GLOBAL_API_KEY" \
            -H "X-Auth-Email: $CF_AUTH_EMAIL" \
            -H "Content-Type: application/json" \
            --data '{"purge_everything":true}')
        if echo "$result" | python3 -c "import sys,json;sys.exit(0 if json.load(sys.stdin).get('success') else 1)" 2>/dev/null; then
            echo -e "${GREEN}  CDN cache purged${NC}"
        else
            echo -e "${YELLOW}  CDN cache purge failed (non-fatal)${NC}"
        fi
    fi
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
        sudo chmod 640 "$backup"
    else
        cp -p "$auth_db" "$backup"
        chmod 640 "$backup"
    fi
    echo -e "${GREEN}  Auth database backed up${NC}"

    # Retain only the 3 most recent backups
    local backup_dir
    backup_dir=$(dirname "$auth_db")
    local backup_base
    backup_base=$(basename "$auth_db")
    local old_backups
    mapfile -t old_backups < <(find "${backup_dir}" -maxdepth 1 -name "${backup_base}.pre-upgrade-*" -printf '%T@ %p\n' 2>/dev/null | sort -rn | tail -n +4 | cut -d' ' -f2-)
    if [[ ${#old_backups[@]} -gt 0 ]]; then
        echo "  Cleaning up ${#old_backups[@]} old backup(s)..."
        for old in "${old_backups[@]}"; do
            if [[ -n "$use_sudo" ]]; then
                sudo rm -f "$old"
            else
                rm -f "$old"
            fi
        done
    fi
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
        return 0 # No auth DB — nothing to validate
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
    # Stop audiobook services before upgrade
    local use_sudo="$1"

    echo -e "${BLUE}Stopping audiobook services...${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would stop audiobooks services"
        return 0
    fi

    # Check if systemd services exist
    if systemctl list-units --type=service --all 2>/dev/null | grep -q "audiobook-"; then
        # System-level services
        if [[ -n "$use_sudo" ]]; then
            sudo systemctl stop audiobook.target 2>/dev/null || true
            for svc in audiobook-api audiobook-proxy audiobook-redirect audiobook-converter audiobook-mover audiobook-downloader.timer audiobook-shutdown-saver; do
                sudo systemctl stop "$svc" 2>/dev/null || true
            done
        elif [[ $(id -u) -eq 0 ]]; then
            # Already running as root (e.g., via sudo upgrade.sh) — no sudo prefix needed
            systemctl stop audiobook.target 2>/dev/null || true
            for svc in audiobook-api audiobook-proxy audiobook-redirect audiobook-converter audiobook-mover audiobook-downloader.timer audiobook-shutdown-saver; do
                systemctl stop "$svc" 2>/dev/null || true
            done
        fi
        echo -e "${GREEN}  Services stopped${NC}"
    elif systemctl --user list-units --type=service --all 2>/dev/null | grep -q "audiobook-"; then
        # User-level services
        systemctl --user stop audiobook.target 2>/dev/null || true
        for svc in audiobook-api audiobook-proxy audiobook-redirect; do
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
    elif [[ $(id -u) -eq 0 ]]; then
        systemctl daemon-reload
    else
        systemctl --user daemon-reload 2>/dev/null || true
    fi

    # Check if systemd services exist
    if systemctl list-units --type=service --all 2>/dev/null | grep -q "audiobook-"; then
        # System-level services
        if [[ -n "$use_sudo" ]]; then
            sudo systemctl start audiobook.target 2>/dev/null || {
                # Fallback: start individual services
                for svc in audiobook-api audiobook-proxy audiobook-redirect audiobook-converter audiobook-mover audiobook-downloader.timer audiobook-shutdown-saver; do
                    sudo systemctl start "$svc" 2>/dev/null || true
                done
            }
        elif [[ $(id -u) -eq 0 ]]; then
            # Already running as root — no sudo prefix needed
            systemctl start audiobook.target 2>/dev/null || {
                for svc in audiobook-api audiobook-proxy audiobook-redirect audiobook-converter audiobook-mover audiobook-downloader.timer audiobook-shutdown-saver; do
                    systemctl start "$svc" 2>/dev/null || true
                done
            }
        fi
        echo -e "${GREEN}  Services started${NC}"

        # Show service status summary
        echo ""
        echo -e "${BLUE}Service status:${NC}"
        for svc in audiobook-api audiobook-proxy audiobook-converter audiobook-mover audiobook-downloader.timer; do
            local svc_state
            svc_state=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
            if [[ "$svc_state" == "active" ]]; then
                echo -e "  $svc: ${GREEN}$svc_state${NC}"
            else
                echo -e "  $svc: ${YELLOW}$svc_state${NC}"
            fi
        done
    elif systemctl --user list-units --type=service --all 2>/dev/null | grep -q "audiobook-"; then
        # User-level services
        systemctl --user start audiobook.target 2>/dev/null || {
            for svc in audiobook-api audiobook-proxy audiobook-redirect; do
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

    # MANDATORY: unconditional full-tree ownership + permission normalization.
    # Runs for every system upgrade regardless of prior state, because cp/rsync
    # (especially from dev machine with differing UIDs) may leave files owned by
    # the wrong user or with mode 700 directories, which breaks audiobook-proxy.
    if [[ "$is_system" == "true" ]]; then
        echo -n "  Normalizing ownership + permissions (entire tree)... "
        sudo chown -R audiobooks:audiobooks "$target_dir"
        sudo find "$target_dir" -type d -exec chmod 755 {} +
        sudo find "$target_dir" -type f -exec chmod 644 {} +
        sudo find "$target_dir" -type f \( -name "*.sh" -o -name "launch*.sh" \) -exec chmod 755 {} +
        # Extension-less shebang scripts under scripts/ (e.g. audiobook-api).
        # These back the /usr/local/bin/audiobook-* symlinks. Left at 644 the
        # reconciler reports 20 "missing wrapper" drift items on every upgrade.
        if [[ -d "$target_dir/scripts" ]]; then
            while IFS= read -r -d '' _f; do
                if sudo head -c 2 "$_f" 2>/dev/null | grep -q '^#!'; then
                    sudo chmod 755 "$_f"
                fi
            done < <(sudo find "$target_dir/scripts" -maxdepth 2 -type f -print0)
        fi
        [[ -d "$target_dir/library/venv/bin" ]] && sudo find "$target_dir/library/venv/bin" -type f -exec chmod 755 {} +
        [[ -d "$target_dir/library/audible-venv/bin" ]] && sudo find "$target_dir/library/audible-venv/bin" -type f -exec chmod 755 {} +
        local _cert_dir="${AUDIOBOOKS_CERTS:-/etc/audiobooks/certs}"
        local _var_dir="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
        [[ -f "$_cert_dir/server.key" ]] && sudo chmod 640 "$_cert_dir/server.key"
        [[ -f "$_var_dir/auth.key" ]] && sudo chmod 600 "$_var_dir/auth.key"
        [[ -f "$_var_dir/auth.db" ]] && sudo chmod 640 "$_var_dir/auth.db"
        # DB directory must be owned by audiobooks so the service can create
        # SQLite WAL/SHM sidecar files. Without this, a drifted db/ owned by
        # root:root under $AUDIOBOOKS_VAR_DIR silently blocks API startup with
        # "readonly database" the first time a new migration tries to
        # ALTER/INSERT. See QA VM 2026-04-18.
        if [[ -d "$_var_dir/db" ]]; then
            sudo chown audiobooks:audiobooks "$_var_dir/db"
            sudo chmod 0750 "$_var_dir/db"
            sudo find "$_var_dir/db" -maxdepth 1 -type f -exec chown audiobooks:audiobooks {} +
        fi
        echo -e "${GREEN}OK${NC}"
    fi

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
    # Without world-readable, /etc/profile.d scripts can't source shared libs like audiobook-config.sh
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
    local project_links=$(find /usr/local/bin -name "audiobook-*" -type l -exec readlink {} \; 2>/dev/null | grep -c "ClaudeCodeProjects" || true)
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
            tarball_url="https://github.com/${GITHUB_REPO}/releases/download/${tag}/audiobook-manager-${version}.tar.gz"
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
    for pattern in "audiobook-manager-*" "audiobook-*" "Audiobook-Manager-*"; do
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

    # Check only mode — write preflight report and return
    if [[ "$CHECK_ONLY" == "true" ]]; then
        echo -e "${GREEN}Update available: $current_version → $install_version${NC}"

        # Write preflight report so the web UI upgrade gate is satisfied.
        # In GitHub --check mode we haven't downloaded the tarball yet, so we
        # can't diff files.  Write the fields we DO know; the rest get safe
        # defaults that won't block the subsequent upgrade.
        local var_dir="${AUDIOBOOKS_VAR_DIR:-/var/lib/audiobooks}"
        local control_dir="${var_dir}/.control"
        local preflight_file="${control_dir}/upgrade-preflight.json"

        if [[ ! -d "$control_dir" ]]; then
            if [[ ! -w "$var_dir" ]]; then
                sudo mkdir -p "$control_dir"
                sudo chown audiobooks:audiobooks "$control_dir" 2>/dev/null || true
            else
                mkdir -p "$control_dir"
            fi
        fi

        local is_major="false"
        local cur_major new_major
        cur_major=$(echo "$current_version" | cut -d. -f1)
        new_major=$(echo "$install_version" | cut -d. -f1)
        if [[ "$cur_major" != "$new_major" ]] && [[ "$cur_major" != "unknown" ]] && [[ "$new_major" != "unknown" ]]; then
            is_major="true"
        fi

        local timestamp
        timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

        local tmp_file
        tmp_file=$(mktemp)
        printf '{
  "timestamp": "%s",
  "source": "github:%s",
  "current_version": "%s",
  "target_version": "%s",
  "is_major": %s,
  "venv_rebuild_needed": %s,
  "config_changes": false,
  "new_services": [],
  "files_changed": -1,
  "warnings": []
}\n' \
            "$timestamp" \
            "$install_version" \
            "$current_version" \
            "$install_version" \
            "$is_major" \
            "$is_major" >"$tmp_file"

        if [[ ! -w "$control_dir" ]]; then
            sudo mv "$tmp_file" "$preflight_file"
            sudo chown audiobooks:audiobooks "$preflight_file" 2>/dev/null || true
            sudo chmod 644 "$preflight_file"
        else
            mv "$tmp_file" "$preflight_file"
            chmod 644 "$preflight_file"
        fi

        echo -e "${BLUE}Preflight report written: $preflight_file${NC}"
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
    trap 'rm -rf '"'$temp_dir'"'; _cleanup_on_exit' EXIT

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

    # Determine if we need sudo
    local use_sudo=""
    if [[ ! -w "$target" ]]; then
        use_sudo="sudo"
    fi

    # Always create backup before upgrade (rolling retention: last 5 kept)
    create_backup "$target"
    echo ""

    # Backup auth database before any changes
    backup_auth_db "$target" "$use_sudo"

    # Stop services before upgrade (trap ensures restart on failure)
    _SERVICES_USE_SUDO="$use_sudo"
    if [[ "$SKIP_SERVICE_LIFECYCLE" != "true" ]]; then
        stop_services "$use_sudo"
        _SERVICES_STOPPED=true
    fi
    echo ""

    # Use the existing do_upgrade function with the extracted release
    do_upgrade "$release_dir" "$target"

    echo ""

    # Start services after upgrade
    if [[ "$SKIP_SERVICE_LIFECYCLE" != "true" ]]; then
        start_services "$use_sudo"
        _SERVICES_STOPPED=false
    fi

    # Validate auth database post-upgrade
    validate_auth_post_upgrade "$target"

    echo ""
    echo -e "${GREEN}Successfully upgraded to version $install_version${NC}"
}

# -----------------------------------------------------------------------------
# Parse Command Line Arguments
# -----------------------------------------------------------------------------

# Source-only guard: allow test harnesses to `source upgrade.sh` to exercise
# individual functions without triggering arg-parsing or main execution.
# Activate with `UPGRADE_SH_SOURCE_ONLY=1 source upgrade.sh`.
if [[ "${UPGRADE_SH_SOURCE_ONLY:-0}" == "1" ]]; then
    # shellcheck disable=SC2317  # `exit 0` is the executed-directly fallback
    return 0 2>/dev/null || exit 0
fi

# Show usage and exit if no arguments provided
if [[ $# -eq 0 ]]; then
    show_usage
    exit 0
fi

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
            # Backup now runs unconditionally on every upgrade; this flag is a no-op
            # kept for backwards compatibility.
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
        --ssh-key)
            SSH_KEY="$2"
            shift 2
            ;;
        --yes | -y)
            AUTO_YES=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --major-version | --mv)
            MAJOR_VERSION=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-service-lifecycle)
            # Internal flag: upgrade-helper-process manages service start/stop.
            # Not shown in --help — callers must know this flag explicitly.
            SKIP_SERVICE_LIFECYCLE=true
            shift
            ;;
        --help | -h)
            show_usage
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

# ─── Production safety gate ─────────────────────────────────────────────────
# Deploying to local production (/opt/audiobooks) requires the current git
# HEAD to be a tagged release. This prevents dev/feature code from reaching
# production accidentally. Remote deploys (QA/test VMs) are unaffected —
# the --remote path exits before reaching this point.
if [[ "$TARGET_DIR" == "/opt/audiobooks" ]] && [[ -z "$REMOTE_HOST" ]] && [[ "$PROJECT_DIR" != /tmp/audiobook-upgrade-* ]]; then
    head_tag=$(git -C "$PROJECT_DIR" tag --points-at HEAD 2>/dev/null | grep -E '^v[0-9]' | head -1)
    if [[ -z "$head_tag" ]]; then
        echo -e "${RED}${BOLD}PRODUCTION SAFETY GATE${NC}"
        echo -e "${RED}Refusing to deploy to /opt/audiobooks — HEAD is not a tagged release.${NC}"
        echo ""
        echo -e "  Current HEAD: $(git -C "$PROJECT_DIR" log -1 --format='%h %s' 2>/dev/null)"
        echo -e "  Latest tag:   $(git -C "$PROJECT_DIR" describe --tags --abbrev=0 2>/dev/null || echo 'none')"
        echo ""
        echo -e "${YELLOW}Production only receives tagged releases created via /git-release.${NC}"
        echo -e "${YELLOW}To deploy to a test/QA VM, use: --remote HOST${NC}"
        exit 1
    fi
    echo -e "${GREEN}Production release tag: $head_tag${NC}"
fi

# Check for updates
if ! check_for_updates "$PROJECT_DIR" "$TARGET_DIR"; then
    # No upgrade needed — but still ensure schema + data migrations are applied
    # (handles cases where code was deployed but migrations didn't run, or
    # where a prior upgrade silently no-op'd a data migration and we need a
    # re-apply sweep to bring the DB into spec).
    local_use_sudo=""
    [[ ! -w "$TARGET_DIR" ]] && local_use_sudo="true"
    apply_schema_migrations "$TARGET_DIR" "$local_use_sudo"
    apply_data_migrations "$PROJECT_DIR" "$TARGET_DIR" "$local_use_sudo" "false"
    exit 0
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
    # Generate preflight report during --check so the upgrade can proceed without re-running
    generate_preflight "$PROJECT_DIR" "$TARGET_DIR"
    exit 0
fi

echo ""
[[ "$DRY_RUN" == "true" ]] && echo -e "${YELLOW}=== DRY RUN MODE ===${NC}" && echo ""

# Validate preflight report before proceeding (--force bypasses)
if [[ "$DRY_RUN" == "false" ]]; then
    if ! validate_preflight "$PROJECT_DIR"; then
        echo ""
        echo -e "${YELLOW}Tip: Run with --force to skip preflight validation (not recommended).${NC}"
        exit 1
    fi
    echo ""
fi

# Confirm upgrade
if [[ "$DRY_RUN" == "false" ]] && [[ "$AUTO_YES" != "true" ]]; then
    read -r -p "Proceed with upgrade? [y/N]: " confirm
    if [[ "${confirm,,}" != "y" ]] && [[ "${confirm,,}" != "yes" ]]; then
        echo "Upgrade cancelled."
        exit 0
    fi
    echo ""
fi

# Re-run preflight to detect drift between --check and upgrade execution
# (catches cases where files changed or a different version was deployed in the gap)
if [[ "$DRY_RUN" == "false" ]] && [[ "$FORCE" != "true" ]]; then
    generate_preflight "$PROJECT_DIR" "$TARGET_DIR"
fi

# Always create backup before upgrade (rolling retention: last 5 kept)
create_backup "$TARGET_DIR"
echo ""

# Determine if we need sudo for service operations
use_sudo=""
if [[ ! -w "$TARGET_DIR" ]]; then
    use_sudo="true"
fi

# Backup auth database before any changes
backup_auth_db "$TARGET_DIR" "$use_sudo"

# Stop services before upgrade (trap ensures restart on failure)
_SERVICES_USE_SUDO="$use_sudo"
if [[ "$SKIP_SERVICE_LIFECYCLE" != "true" ]]; then
    stop_services "$use_sudo"
    _SERVICES_STOPPED=true
fi
echo ""

# Perform upgrade
do_upgrade "$PROJECT_DIR" "$TARGET_DIR"

# Start services after upgrade
echo ""
if [[ "$SKIP_SERVICE_LIFECYCLE" != "true" ]]; then
    start_services "$use_sudo"
    _SERVICES_STOPPED=false
fi

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
