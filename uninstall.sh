#!/bin/bash
# =============================================================================
# Audiobook Library - Comprehensive Uninstall Script
# =============================================================================
# Removes every trace of the Audiobook Manager installation using dynamic
# discovery (glob patterns, systemctl queries) instead of hardcoded lists.
#
# Usage:
#   ./uninstall.sh [OPTIONS]
#
# Options:
#   --system           Uninstall system installation (requires sudo)
#   --user             Uninstall user installation
#   --keep-data        Keep audiobook data (Library, Sources, Supplements)
#   --delete-data      Delete all audiobook data (no prompt)
#   --dry-run          Show what would be removed without removing anything
#   --force            Skip confirmation prompts
#   --help             Show help
#
# If neither --system nor --user is specified, auto-detects based on what
# exists (/opt/audiobooks for system, ~/.local/lib/audiobooks for user).
# =============================================================================

set -e
shopt -s nullglob # Empty arrays when globs match nothing (replaces zsh )

# Ensure essential commands are in PATH (sudo may strip PATH)
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
INSTALL_MODE=""
DATA_MODE="" # "keep", "delete", or "" (interactive)
DRY_RUN=false
FORCE=false
REMOVED_COUNT=0
SKIPPED_COUNT=0

# =============================================================================
# Helpers
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_remove() {
    echo -e "${RED}[REMOVE]${NC} $1"
    ((REMOVED_COUNT++)) || true
}

log_skip() {
    echo -e "${DIM}[SKIP]${NC} $1 (not found)"
    ((SKIPPED_COUNT++)) || true
}

log_dry() {
    echo -e "${YELLOW}[DRY RUN]${NC} Would remove: $1"
    ((REMOVED_COUNT++)) || true
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_note() {
    echo -e "${CYAN}[NOTE]${NC} $1"
}

# Run command with sudo if needed (skips sudo when already root)
_sudo() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

# Remove a file or symlink (with sudo support)
remove_file() {
    local target="$1"
    local use_sudo="$2"

    if [[ -e "$target" || -L "$target" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_dry "$target"
        else
            if [[ "$use_sudo" == "sudo" ]]; then
                _sudo rm -f "$target"
            else
                rm -f "$target"
            fi
            log_remove "$target"
        fi
    else
        log_skip "$target"
    fi
}

# Remove a directory recursively (with sudo support)
remove_dir() {
    local target="$1"
    local use_sudo="$2"

    if [[ -d "$target" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_dry "$target/"
        else
            if [[ "$use_sudo" == "sudo" ]]; then
                _sudo rm -rf "$target"
            else
                rm -rf "$target"
            fi
            log_remove "$target/"
        fi
    else
        log_skip "$target/"
    fi
}

show_help() {
    echo "Audiobook Manager - Uninstall Script"
    echo ""
    echo "Usage: ./uninstall.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --system           Uninstall system installation (requires sudo)"
    echo "  --user             Uninstall user installation"
    echo "  --keep-data        Keep audiobook data (Library, Sources, Supplements)"
    echo "  --delete-data      Delete all audiobook data (no prompt)"
    echo "  --dry-run          Show what would be removed without removing anything"
    echo "  --force            Skip confirmation prompts"
    echo "  --help             Show this help message"
    echo ""
    echo "If neither --system nor --user is specified, auto-detects based on"
    echo "what is installed."
}

# =============================================================================
# Detection
# =============================================================================

detect_install_type() {
    local has_system=false
    local has_user=false

    [[ -d /opt/audiobooks ]] && has_system=true
    [[ -d "$HOME/.local/lib/audiobooks" ]] && has_user=true

    # Also check for leftover systemd units or symlinks even if dirs are gone
    if [[ "$has_system" == "false" ]]; then
        local sys_units=(/etc/systemd/system/audiobook*)
        local sys_bins=(/usr/local/bin/audiobook-*)
        [[ ${#sys_units} -gt 0 || ${#sys_bins} -gt 0 ]] && has_system=true
    fi

    if [[ "$has_user" == "false" ]]; then
        local user_units=("$HOME"/.config/systemd/user/audiobook*)
        local user_bins=("$HOME"/.local/bin/audiobook-*)
        [[ ${#user_units} -gt 0 || ${#user_bins} -gt 0 ]] && has_user=true
    fi

    if [[ "$has_system" == "true" && "$has_user" == "true" ]]; then
        echo "both"
    elif [[ "$has_system" == "true" ]]; then
        echo "system"
    elif [[ "$has_user" == "true" ]]; then
        echo "user"
    else
        echo "none"
    fi
}

# =============================================================================
# Confirmation
# =============================================================================

confirm_uninstall() {
    local mode="$1"

    if [[ "$FORCE" == "true" ]]; then
        return 0
    fi

    echo ""
    echo -e "${RED}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                    UNINSTALL CONFIRMATION                         ║${NC}"
    echo -e "${RED}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "This will remove the Audiobook Manager ${BOLD}${mode}${NC} installation:"
    echo ""

    if [[ "$mode" == "system" ]]; then
        echo "  - All audiobook-* systemd services, timers, paths, and targets"
        echo "  - All audiobook-* symlinks in /usr/local/bin/"
        echo "  - Application directory: /opt/audiobooks/"
        echo "  - Configuration: /etc/audiobooks/"
        echo "  - State/database: /var/lib/audiobooks/"
        echo "  - Logs: /var/log/audiobooks/"
        echo "  - Shared library: /usr/local/lib/audiobooks"
        echo "  - tmpfiles.d and profile.d configs"
        echo "  - Runtime/temp files in /tmp/"
        echo "  - System user and group: audiobooks"
    else
        echo "  - All audiobook-* user systemd services"
        echo "  - All audiobook-* scripts in ~/.local/bin/"
        echo "  - Application: ~/.local/lib/audiobooks/"
        echo "  - Configuration: ~/.config/audiobooks/"
        echo "  - State/database: ~/.local/var/lib/audiobooks/"
        echo "  - Logs: ~/.local/var/log/audiobooks/"
    fi

    case "$DATA_MODE" in
    keep) echo -e "\n  ${GREEN}Data directories will be KEPT${NC}" ;;
    delete) echo -e "\n  ${RED}Data directories will be DELETED${NC}" ;;
    *) echo -e "\n  Data directories: will prompt individually" ;;
    esac

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "\n  ${YELLOW}(DRY RUN — nothing will actually be removed)${NC}"
    fi

    echo ""
    while true; do
        read -r -p "Type 'yes' to proceed with uninstall: " answer
        case "${answer,,}" in
        yes)
            return 0
            ;;
        no | n | "")
            echo -e "${GREEN}Uninstall cancelled.${NC}"
            exit 0
            ;;
        *)
            echo "Please type 'yes' to confirm or press Enter to cancel."
            ;;
        esac
    done
}

# =============================================================================
# Step 1-3: Systemd Units (stop, disable, remove)
# =============================================================================

remove_systemd_units() {
    local use_sudo="$1"
    local systemctl_cmd="systemctl"
    local systemd_dir="/etc/systemd/system"

    if [[ "$use_sudo" != "sudo" ]]; then
        systemctl_cmd="systemctl --user"
        systemd_dir="$HOME/.config/systemd/user"
    fi

    echo ""
    echo -e "${BOLD}=== Systemd Units ===${NC}"

    # Step 1: Find all audiobook* units (dynamic discovery)
    local units=()
    if [[ "$use_sudo" == "sudo" ]]; then
        # Query systemd for all audiobook* units of any type
        # In dry-run mode, try without sudo first (may have read access)
        # Filter out systemctl's ● marker for failed/inactive units
        # systemctl list-* doesn't need sudo (read-only), run without privilege escalation
        mapfile -t units < <(systemctl list-units --type=service,timer,path,target,socket --all 'audiobook*' --no-legend 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i ~ /^audiobook/) {print $i; break}}')
        # Also check unit files that might not be loaded
        local unit_files
        mapfile -t unit_files < <(systemctl list-unit-files 'audiobook*' --no-legend 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i ~ /^audiobook/) {print $i; break}}')
        units+=("${unit_files[@]}")
        # Deduplicate
        readarray -t units < <(printf '%s\n' "${units[@]}" | sort -u)
        # Also discover from filesystem (catches units systemd doesn't know about)
        for f in "${systemd_dir}"/audiobook*.{service,timer,path,target,socket}; do
            local unit_name="${f##*/}"
            # Add if not already in list
            if [[ ! " ${units[*]} " =~ \ ${unit_name}\  ]]; then
                units+=("$unit_name")
            fi
        done
    else
        mapfile -t units < <(systemctl --user list-units --type=service,timer,path,target,socket --all 'audiobook*' --no-legend 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i ~ /^audiobook/) {print $i; break}}')
        local unit_files
        mapfile -t unit_files < <(systemctl --user list-unit-files 'audiobook*' --no-legend 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i ~ /^audiobook/) {print $i; break}}')
        units+=("${unit_files[@]}")
        for f in "${systemd_dir}"/audiobook*.{service,timer,path,target,socket}; do
            local unit_name="${f##*/}"
            if [[ ! " ${units[*]} " =~ \ ${unit_name}\  ]]; then
                units+=("$unit_name")
            fi
        done
    fi

    if [[ ${#units} -eq 0 ]]; then
        log_info "No audiobook systemd units found"
    else
        # Step 1: Stop all units
        log_info "Stopping ${#units} systemd unit(s)..."
        for unit in "${units[@]}"; do
            [[ -z "$unit" ]] && continue
            if [[ "$DRY_RUN" == "true" ]]; then
                log_dry "stop $unit"
            else
                if [[ "$use_sudo" == "sudo" ]]; then
                    _sudo systemctl stop "$unit" 2>/dev/null || true
                else
                    systemctl --user stop "$unit" 2>/dev/null || true
                fi
            fi
        done

        # Step 2: Disable all units
        log_info "Disabling ${#units} systemd unit(s)..."
        for unit in "${units[@]}"; do
            [[ -z "$unit" ]] && continue
            if [[ "$DRY_RUN" == "true" ]]; then
                log_dry "disable $unit"
            else
                if [[ "$use_sudo" == "sudo" ]]; then
                    _sudo systemctl disable "$unit" 2>/dev/null || true
                else
                    systemctl --user disable "$unit" 2>/dev/null || true
                fi
            fi
        done
    fi

    # Step 3: Remove unit files from disk (glob-based, catches everything)
    log_info "Removing unit files from ${systemd_dir}..."
    for f in "${systemd_dir}"/audiobook*; do
        if [[ -f "$f" || -L "$f" ]]; then
            remove_file "$f" "$use_sudo"
        elif [[ -d "$f" ]]; then
            # Drop-in override directories (e.g., audiobook-api.service.d/)
            remove_dir "$f" "$use_sudo"
        fi
    done

    # Also remove .wants directory symlinks (created by systemctl enable)
    for wants_dir in "${systemd_dir}"/audiobook*.wants; do
        remove_dir "$wants_dir" "$use_sudo"
    done
    # Clean symlinks inside other .wants dirs that point to audiobook units
    for wants_dir in "${systemd_dir}"/*.wants; do
        [[ -d "$wants_dir" ]] || continue
        for link in "${wants_dir}"/audiobook*; do
            remove_file "$link" "$use_sudo"
        done
    done

    # Reload systemd
    if [[ "$DRY_RUN" != "true" ]]; then
        if [[ "$use_sudo" == "sudo" ]]; then
            _sudo systemctl daemon-reload
        else
            systemctl --user daemon-reload 2>/dev/null || true
        fi
        log_info "systemd daemon reloaded"
    else
        log_dry "systemctl daemon-reload"
    fi
}

# =============================================================================
# Step 4: Symlinks in bin directory
# =============================================================================

remove_bin_symlinks() {
    local bin_dir="$1"
    local use_sudo="$2"

    echo ""
    echo -e "${BOLD}=== Binary Symlinks ===${NC}"

    local count=0
    for link in "${bin_dir}"/audiobook-*; do
        if [[ -L "$link" || -f "$link" ]]; then
            remove_file "$link" "$use_sudo"
            ((count++)) || true
        fi
    done

    if [[ $count -eq 0 ]]; then
        log_info "No audiobook-* files found in ${bin_dir}"
    fi
}

# =============================================================================
# Step 5-6: System configs (tmpfiles.d, profile.d)
# =============================================================================

remove_system_configs() {
    local use_sudo="$1"

    echo ""
    echo -e "${BOLD}=== System Configuration Files ===${NC}"

    # tmpfiles.d (glob catches both audiobooks.conf and audiobooks-tmpfiles.conf)
    for f in /etc/tmpfiles.d/audiobook*; do
        remove_file "$f" "$use_sudo"
    done
    for f in /usr/lib/tmpfiles.d/audiobook*; do
        remove_file "$f" "$use_sudo"
    done

    # profile.d
    remove_file "/etc/profile.d/audiobooks.sh" "$use_sudo"
}

# =============================================================================
# Step 7-8: Application directory + backward-compat library
# =============================================================================

remove_app_directory() {
    local app_dir="$1"
    local use_sudo="$2"

    echo ""
    echo -e "${BOLD}=== Application Directory ===${NC}"

    remove_dir "$app_dir" "$use_sudo"

    if [[ "$use_sudo" == "sudo" ]]; then
        # Backward-compat library symlink
        if [[ -L /usr/local/lib/audiobooks || -d /usr/local/lib/audiobooks ]]; then
            remove_file "/usr/local/lib/audiobooks" "$use_sudo"
            # Remove parent if empty (don't remove /usr/local/lib if it has other contents)
            if [[ -d /usr/local/lib ]] && [[ -z "$(ls -A /usr/local/lib 2>/dev/null)" ]]; then
                if [[ "$DRY_RUN" == "true" ]]; then
                    log_dry "rmdir /usr/local/lib (empty)"
                else
                    _sudo rmdir /usr/local/lib 2>/dev/null || true
                fi
            fi
        else
            log_skip "/usr/local/lib/audiobooks"
        fi
    fi
}

# =============================================================================
# Step 9-10: Configuration, state, and log directories
# =============================================================================

remove_config_and_state() {
    local config_dir="$1"
    local state_dir="$2"
    local log_dir="$3"
    local use_sudo="$4"

    echo ""
    echo -e "${BOLD}=== Configuration & State ===${NC}"

    remove_dir "$config_dir" "$use_sudo"
    remove_dir "$state_dir" "$use_sudo"
    remove_dir "$log_dir" "$use_sudo"
}

# =============================================================================
# Step 11: Runtime/temp files
# =============================================================================

remove_runtime_files() {
    local use_sudo="$1"

    echo ""
    echo -e "${BOLD}=== Runtime & Temporary Files ===${NC}"

    # Known runtime locations
    local known_paths=(
        "/tmp/audiobook-staging"
        "/tmp/audiobook-triggers"
        "/tmp/audiobook-downloader.lock"
    )

    for item in "${known_paths[@]}"; do
        if [[ -d "$item" ]]; then
            remove_dir "$item" "$use_sudo"
        elif [[ -e "$item" ]]; then
            remove_file "$item" "$use_sudo"
        else
            log_skip "$item"
        fi
    done

    # Catch-all: any remaining /tmp/audiobook* artifacts (FIFOs, temp files, etc.)
    for f in /tmp/audiobook*; do
        # Skip already-handled paths
        local already_handled=false
        for known in "${known_paths[@]}"; do
            [[ "$f" == "$known" ]] && already_handled=true && break
        done
        [[ "$already_handled" == "true" ]] && continue

        if [[ -d "$f" ]]; then
            remove_dir "$f" "$use_sudo"
        else
            remove_file "$f" "$use_sudo"
        fi
    done
}

# =============================================================================
# Step 12: Data directories (conditional)
# =============================================================================

handle_data_directories() {
    local use_sudo="$1"
    local config_dir="$2"

    echo ""
    echo -e "${BOLD}=== Audiobook Data ===${NC}"

    # Read data paths from config before it was deleted (we sourced it earlier)
    local data_dir="${_UNINSTALL_DATA_DIR:-/srv/audiobooks}"
    local library_dir="${_UNINSTALL_LIBRARY_DIR:-${data_dir}/Library}"
    local sources_dir="${_UNINSTALL_SOURCES_DIR:-${data_dir}/Sources}"
    local supplements_dir="${_UNINSTALL_SUPPLEMENTS_DIR:-${data_dir}/Supplements}"

    if [[ "$DATA_MODE" == "keep" ]]; then
        log_info "Keeping data directories (--keep-data)"
        # Still remove regenerable caches
        _remove_regenerable_data "$data_dir" "$use_sudo"
        return 0
    fi

    if [[ "$DATA_MODE" == "delete" ]]; then
        log_info "Deleting all data directories (--delete-data)"
        remove_dir "$library_dir" "$use_sudo"
        remove_dir "$sources_dir" "$use_sudo"
        remove_dir "$supplements_dir" "$use_sudo"
        _remove_regenerable_data "$data_dir" "$use_sudo"
        # Remove parent data dir if empty
        _remove_if_empty "$data_dir" "$use_sudo"
        return 0
    fi

    # Interactive mode — prompt per category
    if [[ "$FORCE" == "true" ]]; then
        # --force without --keep-data or --delete-data: default to keeping data
        log_info "Keeping data directories (--force defaults to keep)"
        _remove_regenerable_data "$data_dir" "$use_sudo"
        return 0
    fi

    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                    Data Removal Options                           ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    local delete_library=false
    local delete_sources=false
    local delete_supplements=false

    # Show each data directory with size info
    if [[ -d "$library_dir" ]]; then
        local lib_size=$(du -sh "$library_dir" 2>/dev/null | cut -f1)
        local lib_count=$(find "$library_dir" -type f \( -name "*.m4b" -o -name "*.mp3" -o -name "*.opus" -o -name "*.flac" \) 2>/dev/null | wc -l)
        echo -e "  ${BOLD}Converted Audiobooks:${NC} $library_dir"
        echo "    Size: ${lib_size:-unknown}  |  Files: ${lib_count} audiobook files"
        echo ""
    fi

    if [[ -d "$sources_dir" ]]; then
        local src_size=$(du -sh "$sources_dir" 2>/dev/null | cut -f1)
        local src_count=$(find "$sources_dir" -type f \( -name "*.aax" -o -name "*.aaxc" \) 2>/dev/null | wc -l)
        echo -e "  ${BOLD}Source Files (AAX/AAXC):${NC} $sources_dir"
        echo "    Size: ${src_size:-unknown}  |  Files: ${src_count} source files"
        echo ""
    fi

    if [[ -d "$supplements_dir" ]]; then
        local sup_size=$(du -sh "$supplements_dir" 2>/dev/null | cut -f1)
        local sup_count=$(find "$supplements_dir" -type f -name "*.pdf" 2>/dev/null | wc -l)
        echo -e "  ${BOLD}Supplemental PDFs:${NC} $supplements_dir"
        echo "    Size: ${sup_size:-unknown}  |  Files: ${sup_count} PDF files"
        echo ""
    fi

    if [[ ! -d "$library_dir" && ! -d "$sources_dir" && ! -d "$supplements_dir" ]]; then
        log_info "No data directories found"
        _remove_regenerable_data "$data_dir" "$use_sudo"
        return 0
    fi

    echo -e "${RED}WARNING: Deleted audiobook files cannot be recovered!${NC}"
    echo ""

    # Prompt for each category
    if [[ -d "$library_dir" ]]; then
        _prompt_delete "Delete converted audiobooks in ${library_dir}?" && delete_library=true
    fi
    if [[ -d "$sources_dir" ]]; then
        _prompt_delete "Delete source files (AAX/AAXC) in ${sources_dir}?" && delete_sources=true
    fi
    if [[ -d "$supplements_dir" ]]; then
        _prompt_delete "Delete supplemental PDFs in ${supplements_dir}?" && delete_supplements=true
    fi

    echo ""

    # Execute deletions
    [[ "$delete_library" == "true" ]] && remove_dir "$library_dir" "$use_sudo"
    [[ "$delete_sources" == "true" ]] && remove_dir "$sources_dir" "$use_sudo"
    [[ "$delete_supplements" == "true" ]] && remove_dir "$supplements_dir" "$use_sudo"

    # Always remove regenerable caches
    _remove_regenerable_data "$data_dir" "$use_sudo"

    # Remove parent data dir if empty
    _remove_if_empty "$data_dir" "$use_sudo"
}

_prompt_delete() {
    local prompt_text="$1"
    while true; do
        read -r -p "${prompt_text} [y/N]: " answer
        case "${answer,,}" in
        y | yes) return 0 ;;
        n | no | "") return 1 ;;
        *) echo "  Please answer y(es) or n(o)" ;;
        esac
    done
}

_remove_regenerable_data() {
    local data_dir="$1"
    local use_sudo="$2"

    # These are regenerable caches — always safe to remove
    for subdir in .covers .index logs; do
        local cache_path="${data_dir}/${subdir}"
        if [[ -d "$cache_path" ]]; then
            remove_dir "$cache_path" "$use_sudo"
        fi
    done
}

_remove_if_empty() {
    local dir="$1"
    local use_sudo="$2"

    if [[ -d "$dir" ]] && [[ -z "$(ls -A "$dir" 2>/dev/null)" ]]; then
        if [[ "$DRY_RUN" == "true" ]]; then
            log_dry "rmdir $dir (empty)"
        else
            if [[ "$use_sudo" == "sudo" ]]; then
                _sudo rmdir "$dir" 2>/dev/null || true
            else
                rmdir "$dir" 2>/dev/null || true
            fi
            log_info "Removed empty directory: $dir"
        fi
    fi
}

# =============================================================================
# Step 13: System user and group
# =============================================================================

remove_system_user() {
    echo ""
    echo -e "${BOLD}=== System User & Group ===${NC}"

    if ! id audiobooks &>/dev/null; then
        log_info "No audiobooks user found"
        return 0
    fi

    # Check for running processes
    if pgrep -u audiobooks >/dev/null 2>&1; then
        log_warn "audiobooks user has running processes — skipping user deletion"
        log_warn "Kill remaining processes and run: sudo userdel audiobooks && sudo groupdel audiobooks"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        log_dry "userdel audiobooks"
        log_dry "groupdel audiobooks"
    else
        _sudo userdel audiobooks 2>/dev/null || true
        log_remove "system user: audiobooks"
        # Remove other users from the audiobooks group before deleting it
        # (prevents PAM/SSH failures from dangling group references)
        if getent group audiobooks &>/dev/null; then
            local group_members
            group_members=$(getent group audiobooks | cut -d: -f4)
            if [[ -n "$group_members" ]]; then
                local IFS=','
                for member in $group_members; do
                    _sudo gpasswd -d "$member" audiobooks 2>/dev/null || true
                    log_remove "user '$member' from audiobooks group"
                done
            fi
            _sudo groupdel audiobooks 2>/dev/null || true
            log_remove "system group: audiobooks"
        fi
    fi
}

# =============================================================================
# Step 14: Scan for orphaned files
# =============================================================================

scan_for_orphans() {
    local use_sudo="$1"

    echo ""
    echo -e "${BOLD}=== Orphan Scan ===${NC}"

    local remaining=0
    local patterns

    if [[ "$use_sudo" == "sudo" ]]; then
        patterns=(
            "/usr/local/bin/audiobook*"
            "/etc/systemd/system/audiobook*"
            "/etc/audiobooks"
            "/opt/audiobooks"
            "/var/lib/audiobooks"
            "/var/log/audiobooks"
            "/etc/tmpfiles.d/audiobook*"
            "/etc/profile.d/audiobook*"
            "/usr/local/lib/audiobooks"
            "/tmp/audiobook*"
        )
    else
        patterns=(
            "$HOME/.local/bin/audiobook*"
            "$HOME/.config/systemd/user/audiobook*"
            "$HOME/.config/audiobooks"
            "$HOME/.local/lib/audiobooks"
            "$HOME/.local/var/lib/audiobooks"
            "$HOME/.local/var/log/audiobooks"
        )
    fi

    for pattern in "${patterns[@]}"; do
        for f in $pattern; do
            if [[ -e "$f" || -L "$f" ]]; then
                echo -e "  ${YELLOW}Remaining:${NC} $f"
                remaining=$((remaining + 1))
            fi
        done
    done

    if [[ $remaining -eq 0 ]]; then
        echo -e "  ${GREEN}Clean — no audiobook artifacts found${NC}"
    else
        echo ""
        log_warn "$remaining artifact(s) remaining — review and remove manually if needed"
    fi
}

# =============================================================================
# Step 15: Check shell RC files
# =============================================================================

check_shell_rc_files() {
    echo ""
    echo -e "${BOLD}=== Shell Configuration Check ===${NC}"

    local found=false
    for rc in ~/.bashrc ~/.zshrc ~/.profile ~/.bash_profile ~/.zprofile; do
        if [[ -f "$rc" ]] && grep -qi 'audiobook' "$rc" 2>/dev/null; then
            found=true
            log_note "$rc contains audiobook references — review manually:"
            grep -n -i 'audiobook' "$rc" 2>/dev/null | head -5 | while read -r line; do
                echo "    $line"
            done
        fi
    done

    if [[ "$found" == "false" ]]; then
        log_info "No audiobook references found in shell RC files"
    fi
}

# =============================================================================
# Orchestrators
# =============================================================================

do_system_uninstall() {
    echo ""
    echo -e "${BOLD}${YELLOW}=== Uninstalling System Installation ===${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}(DRY RUN — no changes will be made)${NC}"
    fi

    # Read config BEFORE deleting it (needed for data directory paths)
    if [[ -f /etc/audiobooks/audiobooks.conf ]]; then
        source /etc/audiobooks/audiobooks.conf 2>/dev/null || true
    fi
    _UNINSTALL_DATA_DIR="${AUDIOBOOKS_DATA:-/srv/audiobooks}"
    _UNINSTALL_LIBRARY_DIR="${AUDIOBOOKS_LIBRARY:-${_UNINSTALL_DATA_DIR}/Library}"
    _UNINSTALL_SOURCES_DIR="${AUDIOBOOKS_SOURCES:-${_UNINSTALL_DATA_DIR}/Sources}"
    _UNINSTALL_SUPPLEMENTS_DIR="${AUDIOBOOKS_SUPPLEMENTS:-${_UNINSTALL_DATA_DIR}/Supplements}"

    remove_systemd_units "sudo"                                                                  # Steps 1-3
    remove_bin_symlinks "/usr/local/bin" "sudo"                                                  # Step 4
    remove_system_configs "sudo"                                                                 # Steps 5-6
    remove_app_directory "/opt/audiobooks" "sudo"                                                # Steps 7-8
    remove_config_and_state "/etc/audiobooks" "/var/lib/audiobooks" "/var/log/audiobooks" "sudo" # Steps 9-10
    remove_runtime_files "sudo"                                                                  # Step 11
    handle_data_directories "sudo" "/etc/audiobooks"                                             # Step 12
    remove_system_user                                                                           # Step 13
    scan_for_orphans "sudo"                                                                      # Step 14
    check_shell_rc_files                                                                         # Step 15

    echo ""
    echo -e "${BOLD}=== Summary ===${NC}"
    echo -e "  Removed: ${RED}${REMOVED_COUNT}${NC} items"
    echo -e "  Skipped: ${DIM}${SKIPPED_COUNT}${NC} items (not found)"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo ""
        echo -e "${YELLOW}This was a dry run. No changes were made.${NC}"
        echo "Run without --dry-run to perform the actual uninstall."
    else
        echo ""
        echo -e "${GREEN}System uninstallation complete.${NC}"
    fi
}

do_user_uninstall() {
    echo ""
    echo -e "${BOLD}${YELLOW}=== Uninstalling User Installation ===${NC}"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}(DRY RUN — no changes will be made)${NC}"
    fi

    # Read config BEFORE deleting it
    local user_config="$HOME/.config/audiobooks/audiobooks.conf"
    if [[ -f "$user_config" ]]; then
        source "$user_config" 2>/dev/null || true
    fi
    _UNINSTALL_DATA_DIR="${AUDIOBOOKS_DATA:-$HOME/Audiobooks}"
    _UNINSTALL_LIBRARY_DIR="${AUDIOBOOKS_LIBRARY:-${_UNINSTALL_DATA_DIR}/Library}"
    _UNINSTALL_SOURCES_DIR="${AUDIOBOOKS_SOURCES:-${_UNINSTALL_DATA_DIR}/Sources}"
    _UNINSTALL_SUPPLEMENTS_DIR="${AUDIOBOOKS_SUPPLEMENTS:-${_UNINSTALL_DATA_DIR}/Supplements}"

    remove_systemd_units ""                                                                                                   # Steps 1-3 (user systemd)
    remove_bin_symlinks "$HOME/.local/bin" ""                                                                                 # Step 4
    remove_app_directory "$HOME/.local/lib/audiobooks" ""                                                                     # Steps 7-8
    remove_config_and_state "$HOME/.config/audiobooks" "$HOME/.local/var/lib/audiobooks" "$HOME/.local/var/log/audiobooks" "" # Steps 9-10
    remove_runtime_files ""                                                                                                   # Step 11
    handle_data_directories "" "$HOME/.config/audiobooks"                                                                     # Step 12
    scan_for_orphans ""                                                                                                       # Step 14
    check_shell_rc_files                                                                                                      # Step 15

    echo ""
    echo -e "${BOLD}=== Summary ===${NC}"
    echo -e "  Removed: ${RED}${REMOVED_COUNT}${NC} items"
    echo -e "  Skipped: ${DIM}${SKIPPED_COUNT}${NC} items (not found)"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo ""
        echo -e "${YELLOW}This was a dry run. No changes were made.${NC}"
        echo "Run without --dry-run to perform the actual uninstall."
    else
        echo ""
        echo -e "${GREEN}User uninstallation complete.${NC}"
    fi
}

# =============================================================================
# Argument Parsing
# =============================================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
    --system)
        INSTALL_MODE="system"
        shift
        ;;
    --user)
        INSTALL_MODE="user"
        shift
        ;;
    --keep-data)
        DATA_MODE="keep"
        shift
        ;;
    --delete-data)
        DATA_MODE="delete"
        shift
        ;;
    --dry-run)
        DRY_RUN=true
        shift
        ;;
    --force)
        FORCE=true
        shift
        ;;
    --help | -h)
        show_help
        exit 0
        ;;
    *)
        echo -e "${RED}Unknown option: $1${NC}"
        echo "Use --help for usage information."
        exit 1
        ;;
    esac
done

# =============================================================================
# Main
# =============================================================================

# Auto-detect if not specified
if [[ -z "$INSTALL_MODE" ]]; then
    detected=$(detect_install_type)
    case "$detected" in
    system)
        INSTALL_MODE="system"
        log_info "Auto-detected system installation"
        ;;
    user)
        INSTALL_MODE="user"
        log_info "Auto-detected user installation"
        ;;
    both)
        echo -e "${YELLOW}Both system and user installations detected.${NC}"
        while true; do
            read -r -p "Uninstall [s]ystem, [u]ser, or [b]oth? " answer
            case "${answer,,}" in
            s | system)
                INSTALL_MODE="system"
                break
                ;;
            u | user)
                INSTALL_MODE="user"
                break
                ;;
            b | both)
                INSTALL_MODE="both"
                break
                ;;
            *)
                echo "Please enter s, u, or b"
                ;;
            esac
        done
        ;;
    none)
        echo -e "${GREEN}No Audiobook Manager installation detected.${NC}"
        echo ""
        echo "Checked:"
        echo "  System: /opt/audiobooks, /usr/local/bin/audiobook-*, /etc/systemd/system/audiobook*"
        echo "  User:   ~/.local/lib/audiobooks, ~/.local/bin/audiobook-*, ~/.config/systemd/user/audiobook*"
        exit 0
        ;;
    esac
fi

# Validate sudo for system uninstall (skip for dry-run)
if [[ "$INSTALL_MODE" == "system" || "$INSTALL_MODE" == "both" ]]; then
    if [[ "$DRY_RUN" != "true" && $EUID -ne 0 ]]; then
        if ! sudo -v 2>/dev/null; then
            echo -e "${RED}Error: System uninstall requires sudo privileges.${NC}"
            exit 1
        fi
    fi
fi

# Confirm
if [[ "$INSTALL_MODE" == "both" ]]; then
    confirm_uninstall "system + user"
else
    confirm_uninstall "$INSTALL_MODE"
fi

# Execute
case "$INSTALL_MODE" in
system)
    do_system_uninstall
    ;;
user)
    do_user_uninstall
    ;;
both)
    do_system_uninstall
    # Reset counters for user
    REMOVED_COUNT=0
    SKIPPED_COUNT=0
    do_user_uninstall
    ;;
esac
