#!/bin/bash
# =============================================================================
# Audiobook Library - Unified Installation Script
# =============================================================================
# Interactive installer that supports both system-wide and user installations.
#
# Features:
#   - Automatic storage tier detection (NVMe, SSD, HDD)
#   - Warnings for suboptimal storage placement
#   - Smart defaults based on detected hardware
#
# Usage:
#   ./install.sh [OPTIONS]
#
# Options:
#   --system           Skip menu, perform system installation
#   --user             Skip menu, perform user installation
#   --data-dir PATH    Audiobook data directory
#   --modular          Use modular Flask Blueprint architecture
#   --monolithic       Use single-file architecture (default)
#   --uninstall        Remove installation
#   --no-services      Skip systemd service installation
#   --help             Show this help message
#
# Storage Tier Recommendations:
#   Database (audiobooks.db) → NVMe/SSD (high random I/O)
#   Index files (.index/)    → NVMe/SSD (frequent access)
#   Cover art (.covers/)     → SSD (random reads)
#   Audio Library (Library/) → HDD OK (sequential streaming)
#   Source files (Sources/)  → HDD OK (sequential read/write)
# =============================================================================

set -e

# Ensure files are created with proper permissions (readable by group/others)
# This prevents the "permission denied" issues when services run as different user
umask 022

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Script directory (source)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default options
INSTALL_MODE=""
DATA_DIR=""
INSTALL_SERVICES=true
UNINSTALL=false
FRESH_INSTALL=false
API_ARCHITECTURE="monolithic" # monolithic (api.py) or modular (api_modular/)

# -----------------------------------------------------------------------------
# Script-to-CLI Name Aliases (shared with upgrade.sh)
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
# Pre-flight: verify required system dependencies
# -----------------------------------------------------------------------------
check_system_dependencies() {
    local missing=()

    # Required commands and what provides them
    local -A deps=(
        [python3]="python (3.13+)"
        [ffmpeg]="ffmpeg (with libopus codec)"
        [ffprobe]="ffmpeg"
        [sqlite3]="sqlite"
        [parallel]="GNU parallel"
        [jq]="jq"
        [openssl]="openssl"
    )

    for cmd in "${!deps[@]}"; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("  - $cmd (${deps[$cmd]})")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo -e "${RED}${BOLD}Missing required system packages:${NC}"
        printf '%s\n' "${missing[@]}"
        echo ""
        echo -e "${YELLOW}Install them with your package manager before running install.sh:${NC}"
        echo -e "  ${DIM}Arch/CachyOS: sudo pacman -S python ffmpeg sqlite parallel jq openssl${NC}"
        echo -e "  ${DIM}Debian/Ubuntu: sudo apt install python3 ffmpeg sqlite3 parallel jq openssl${NC}"
        echo -e "  ${DIM}Fedora:        sudo dnf install python3 ffmpeg sqlite parallel jq openssl${NC}"
        exit 1
    fi

    # Verify ffmpeg has opus support
    if ! ffmpeg -encoders 2>/dev/null | grep -q "libopus"; then
        echo -e "${YELLOW}Warning: ffmpeg may lack libopus encoder — Opus conversion will fail${NC}"
        echo -e "${DIM}Install the opus codec: sudo pacman -S opus libopus (or equivalent)${NC}"
    fi

    echo -e "${GREEN}  System dependencies verified${NC}"
}

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

refresh_bin_symlinks() {
    # Maintain /usr/local/bin symlinks pointing to canonical script location.
    local target="$1"
    local use_sudo="${2:-}"
    local bin_dir="/usr/local/bin"
    local scripts_dir="$target/scripts"

    [[ -d "$bin_dir" ]] || return 0

    echo -e "${BLUE}Creating symlinks in ${bin_dir}...${NC}"

    # 1. Auto-link all audiobook-* scripts (same name, no alias needed)
    for script in "$scripts_dir"/audiobook-*; do
        [[ -f "$script" ]] || continue
        local name=$(basename "$script")
        ${use_sudo} rm -f "${bin_dir}/${name}"
        ${use_sudo} ln -s "$script" "${bin_dir}/${name}"
        echo "  Linked: ${name}"
    done

    # 2. Create alias symlinks for scripts with non-audiobook-* names
    for script_name in "${!SCRIPT_ALIASES[@]}"; do
        local target_name="${SCRIPT_ALIASES[$script_name]}"
        local source_path="${scripts_dir}/${script_name}"
        local link_path="${bin_dir}/${target_name}"
        if [[ -f "$source_path" ]]; then
            ${use_sudo} rm -f "$link_path"
            ${use_sudo} ln -s "$source_path" "$link_path"
            echo "  Linked: ${target_name} -> ${script_name}"
        fi
    done
}

print_header() {
    clear
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════════╗"
    echo "║                                                                   ║"
    echo "║              Audiobook Library Installation                       ║"
    echo "║                                                                   ║"
    echo "╚═══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
}

show_usage() {
    echo -e "${CYAN}${BOLD}Audiobook Library - Unified Installation Script${NC}"
    echo ""
    echo -e "${BOLD}USAGE${NC}"
    echo "  ./install.sh [OPTIONS]"
    echo ""
    echo -e "${BOLD}OPTIONS${NC}"
    echo -e "  ${GREEN}--system${NC}              Skip menu, perform system-wide installation"
    echo -e "  ${GREEN}--user${NC}                Skip menu, perform per-user installation"
    echo -e "  ${GREEN}--data-dir PATH${NC}       Set audiobook data directory (default: /srv/audiobooks"
    echo "                        for system, ~/Audiobooks for user)"
    echo -e "  ${GREEN}--modular${NC}             Use modular Flask Blueprint API architecture"
    echo -e "  ${GREEN}--monolithic${NC}          Use single-file API architecture (default)"
    echo -e "  ${GREEN}--no-services${NC}         Skip systemd service installation"
    echo -e "  ${GREEN}--uninstall${NC}           Remove the installation (delegates to uninstall.sh)"
    echo -e "  ${GREEN}--fresh-install, -fi${NC}  Reinstall from scratch while preserving your"
    echo "                        audiobook library and configuration settings"
    echo -e "  ${GREEN}--help, -h${NC}            Show this help message"
    echo ""
    echo -e "${BOLD}WHAT A FRESH INSTALL CREATES${NC}"
    echo "  System (--system):"
    echo "    /opt/audiobooks/           Application code and Python venv"
    echo "    /etc/audiobooks/           Configuration (audiobooks.conf, auth.key)"
    echo "    /var/lib/audiobooks/       Database, auth DB, runtime state"
    echo "    /var/log/audiobooks/       Log files"
    echo "    /srv/audiobooks/           Data (Library/, Sources/, Supplements/)"
    echo "    /usr/local/bin/audiobook-* CLI commands (symlinks)"
    echo "    /etc/systemd/system/       audiobook-api, audiobook-proxy, etc."
    echo "    SSL certificate (3-year), Python venv, SQLite database"
    echo ""
    echo "  User (--user):"
    echo "    ~/.local/share/audiobooks/ Application code and Python venv"
    echo "    ~/.config/audiobooks/      Configuration and SSL certs"
    echo "    ~/.local/var/lib/audiobooks/ Database and runtime state"
    echo "    ~/.local/bin/audiobook-*   CLI commands"
    echo "    ~/.config/systemd/user/    User-level systemd services"
    echo ""
    echo -e "${BOLD}STORAGE TIER RECOMMENDATIONS${NC}"
    echo -e "  ${GREEN}NVMe/SSD${NC}  Database (audiobooks.db), index files (.index/)"
    echo -e "  ${BLUE}SSD${NC}       Cover art (.covers/) for fast random reads"
    echo -e "  ${YELLOW}HDD OK${NC}    Audio Library (Library/), source files (Sources/)"
    echo "  SQLite query times: NVMe ~0.002s vs HDD ~0.2s (100x difference)"
    echo ""
    echo -e "${BOLD}FRESH INSTALL (--fresh-install)${NC}"
    echo "  Performs a clean reinstall while preserving your audiobook library."
    echo "  1. Captures current settings from audiobooks.conf"
    echo "  2. Uninstalls the application (keeps Library/, Sources/, Supplements/)"
    echo "  3. Runs a fresh install with the same settings"
    echo "  4. Triggers a library scan to reindex preserved audiobooks"
    echo "  Use this to fix a broken installation or upgrade between major versions."
    echo ""
    echo -e "${BOLD}EXAMPLES${NC}"
    echo "  ./install.sh                         Interactive menu"
    echo "  ./install.sh --system                Non-interactive system install"
    echo "  ./install.sh --system --data-dir /mnt/data/audiobooks"
    echo "  ./install.sh --user --modular        User install with modular API"
    echo "  ./install.sh --system --fresh-install Reinstall, keep library + settings"
    echo "  ./install.sh --system --uninstall    Remove system installation"
    echo ""
}

print_menu() {
    echo -e "${BOLD}Please select an installation type:${NC}"
    echo ""
    echo -e "  ${GREEN}1)${NC} System Installation"
    echo "     - Installs to /usr/local/bin and /etc/audiobooks"
    echo "     - System-wide systemd services (start at boot)"
    echo "     - Requires sudo/root privileges"
    echo ""
    echo -e "  ${GREEN}2)${NC} User Installation"
    echo "     - Installs to ~/.local/bin and ~/.config/audiobooks"
    echo "     - User systemd services (start at login)"
    echo "     - No root privileges required"
    echo ""
    echo -e "  ${GREEN}3)${NC} Exit"
    echo ""
}

prompt_architecture_choice() {
    # Prompt user to select API architecture
    # Sets global API_ARCHITECTURE variable

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}API Architecture Selection${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "The API can be installed in two architectures:"
    echo ""
    echo -e "  ${GREEN}1)${NC} ${BOLD}Monolithic${NC} (Recommended for most users)"
    echo "     A single Python file that handles everything."
    echo "     • Simple and proven stable"
    echo "     • Best if you just want the app to work"
    echo "     • No code modifications planned"
    echo ""
    echo -e "  ${GREEN}2)${NC} ${BOLD}Modular${NC} (For developers and contributors)"
    echo "     Split into multiple focused modules."
    echo "     • Easier to navigate and modify"
    echo "     • Better for understanding the code"
    echo "     • Best if you plan to fix bugs or add features"
    echo ""
    echo -e "${DIM}Both provide identical functionality. You can switch later with:${NC}"
    echo -e "${DIM}  ./migrate-api.sh --to-modular  or  --to-monolithic${NC}"
    echo ""

    while true; do
        read -r -p "Choose architecture [1-2, default=1]: " arch_choice
        arch_choice="${arch_choice:-1}"

        case "$arch_choice" in
            1)
                API_ARCHITECTURE="monolithic"
                echo -e "${GREEN}Selected: Monolithic architecture${NC}"
                break
                ;;
            2)
                API_ARCHITECTURE="modular"
                echo -e "${BLUE}Selected: Modular architecture${NC}"
                break
                ;;
            *)
                echo -e "${RED}Invalid choice. Please enter 1 or 2.${NC}"
                ;;
        esac
    done
    echo ""
}

get_api_entry_point() {
    # Returns the API entry point based on selected architecture
    if [[ "$API_ARCHITECTURE" == "modular" ]]; then
        echo "api_server.py"
    else
        echo "api.py"
    fi
}

wait_for_keypress() {
    echo ""
    echo -e "${YELLOW}Press any key to continue...${NC}"
    read -r -k 1 -s
}

check_sudo_access() {
    # Check if user can use sudo
    # Returns 0 if user has sudo access, 1 otherwise

    local username=$(whoami)

    # Method 1: Check if user is root
    if [[ $EUID -eq 0 ]]; then
        return 0
    fi

    # Method 2: Check sudo -v (validates cached credentials or prompts)
    # Use timeout to prevent hanging
    if timeout 2 sudo -n true 2>/dev/null; then
        # User has passwordless sudo or cached credentials
        return 0
    fi

    # Method 3: Check if user is in sudo/wheel group
    if groups "$username" 2>/dev/null | grep -qE '\b(sudo|wheel|admin)\b'; then
        return 0
    fi

    # Method 4: Check sudoers file (if readable)
    if [[ -r /etc/sudoers ]]; then
        if grep -qE "^${username}[[:space:]]" /etc/sudoers 2>/dev/null; then
            return 0
        fi
    fi

    # Method 5: Check sudoers.d directory
    if [[ -d /etc/sudoers.d ]]; then
        for file in /etc/sudoers.d/*; do
            if [[ -r "$file" ]] && grep -qE "^${username}[[:space:]]" "$file" 2>/dev/null; then
                return 0
            fi
        done
    fi

    # No sudo access found
    return 1
}

verify_sudo() {
    # Attempt to authenticate with sudo
    # Returns 0 on success, 1 on failure

    echo -e "${YELLOW}Sudo authentication required for system installation.${NC}"
    echo ""

    # Try to get sudo credentials (will prompt for password)
    if sudo -v 2>/dev/null; then
        echo -e "${GREEN}Sudo authentication successful.${NC}"
        return 0
    else
        return 1
    fi
}

show_sudo_error() {
    echo ""
    echo -e "${RED}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                         ERROR                                     ║${NC}"
    echo -e "${RED}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${RED}You do not have sudo privileges required for system installation.${NC}"
    echo ""
    echo "To gain sudo access, you can:"
    echo "  1. Ask your system administrator to add you to the 'wheel' or 'sudo' group"
    echo "  2. Ask your administrator to add an entry for you in /etc/sudoers"
    echo "  3. Choose the 'User Installation' option instead (no root required)"
    echo ""
    echo "Your username: $(whoami)"
    echo "Your groups: $(groups)"
    echo ""
}

# -----------------------------------------------------------------------------
# Storage Tier Detection Functions
# -----------------------------------------------------------------------------

# Detect storage tier for a given path
# Returns: "nvme", "ssd", "hdd", "tmpfs", or "unknown"
detect_storage_tier() {
    local target_path="$1"

    # Resolve to real path (follow symlinks)
    local real_path
    real_path=$(realpath -m "$target_path" 2>/dev/null) || real_path="$target_path"

    # Find mount point and device
    local mount_info device dev_name
    mount_info=$(df "$real_path" 2>/dev/null | tail -1) || return 1
    device=$(echo "$mount_info" | awk '{print $1}')

    # Check for tmpfs (RAM disk) - ideal for staging/temp files
    if [[ "$device" == "tmpfs" ]] || [[ "$device" == "ramfs" ]]; then
        echo "tmpfs"
        return 0
    fi

    # Handle device mapper and other special devices
    if [[ "$device" == /dev/mapper/* ]]; then
        # For LVM/dm devices, try to find underlying device
        local dm_name=${device##*/}
        if [[ -L "/sys/block/$dm_name" ]]; then
            dev_name="$dm_name"
        else
            # Try to resolve through dmsetup
            local slave
            slave=$(find /sys/block/dm-*/slaves/ -maxdepth 1 -mindepth 1 2>/dev/null | head -1 | xargs -r basename)
            dev_name="${slave:-unknown}"
        fi
    elif [[ "$device" == /dev/md* ]]; then
        # RAID array - check component devices
        local md_name=${device##*/}
        local component
        component=$(find /sys/block/"$md_name"/slaves/ -maxdepth 1 -mindepth 1 2>/dev/null | head -1 | xargs -r basename)
        if [[ -n "$component" ]]; then
            dev_name="$component"
        else
            dev_name="$md_name"
        fi
    else
        # Regular device - extract base name (sda from sda1, nvme0n1 from nvme0n1p1)
        dev_name="${device#/dev/}"
        dev_name="${dev_name%[0-9]*}"
        dev_name="${dev_name%p[0-9]*}"
    fi

    # Check if NVMe (device name starts with nvme)
    if [[ "$dev_name" == nvme* ]]; then
        echo "nvme"
        return 0
    fi

    # Check rotational flag (0 = SSD/NVMe, 1 = HDD)
    local rotational
    rotational=$(cat "/sys/block/$dev_name/queue/rotational" 2>/dev/null)

    if [[ "$rotational" == "0" ]]; then
        echo "ssd"
        return 0
    elif [[ "$rotational" == "1" ]]; then
        echo "hdd"
        return 0
    fi

    echo "unknown"
}

# Get a human-readable name for storage tier
storage_tier_name() {
    local tier="$1"
    case "$tier" in
        nvme) echo "NVMe SSD" ;;
        ssd) echo "SATA SSD" ;;
        hdd) echo "HDD" ;;
        tmpfs) echo "RAM (tmpfs)" ;;
        *) echo "Unknown" ;;
    esac
}

# Get color for storage tier display
storage_tier_color() {
    local tier="$1"
    case "$tier" in
        nvme) echo "${GREEN}" ;;
        ssd) echo "${BLUE}" ;;
        tmpfs) echo "${GREEN}" ;;
        hdd) echo "${YELLOW}" ;;
        *) echo "${NC}" ;;
    esac
}

# Find the fastest available mount point from a list of candidates
# Arguments: component_type (database|library|application)
# Returns: best path or empty if none suitable
find_fastest_mount() {
    local component="$1"
    local candidates=()
    local best_path=""
    local best_tier="hdd"

    # Define candidate paths based on component type
    case "$component" in
        database)
            # Database needs fastest possible storage
            candidates=("/var/lib" "/opt" "/")
            ;;
        application)
            # Application benefits from fast storage but less critical
            candidates=("/opt" "/usr/local" "/")
            ;;
        data)
            # Bulk audio data - HDD is fine, SSD/NVMe is bonus
            candidates=("/srv" "/data" "/home" "/")
            ;;
    esac

    # Score tiers: nvme=3, ssd=2, hdd=1, unknown=0
    local tier_score
    tier_score() {
        case "$1" in
            nvme) echo 3 ;;
            ssd) echo 2 ;;
            hdd) echo 1 ;;
            *) echo 0 ;;
        esac
    }

    local best_score=0
    for candidate in "${candidates[@]}"; do
        if [[ -d "$candidate" ]]; then
            local tier
            tier=$(detect_storage_tier "$candidate")
            local score
            score=$(tier_score "$tier")
            if [[ $score -gt $best_score ]]; then
                best_score=$score
                best_path="$candidate"
                best_tier="$tier"
            fi
        fi
    done

    echo "$best_path"
}

# Display storage tier recommendations
show_storage_recommendations() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}Storage Tier Recommendations${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "For optimal performance, place components on appropriate storage:"
    echo ""
    echo -e "  ${GREEN}●${NC} ${BOLD}Database${NC} (audiobooks.db)  → ${GREEN}NVMe/SSD${NC} (high random I/O)"
    echo -e "  ${GREEN}●${NC} ${BOLD}Index files${NC} (.index/)     → ${GREEN}NVMe/SSD${NC} (frequent access)"
    echo -e "  ${BLUE}●${NC} ${BOLD}Cover art${NC} (.covers/)      → ${BLUE}SSD${NC} (random reads, small files)"
    echo -e "  ${YELLOW}●${NC} ${BOLD}Audio Library${NC} (Library/)  → ${YELLOW}HDD OK${NC} (sequential streaming)"
    echo -e "  ${YELLOW}●${NC} ${BOLD}Source files${NC} (Sources/)   → ${YELLOW}HDD OK${NC} (sequential read/write)"
    echo ""
    echo -e "${DIM}SQLite query times: NVMe ~0.002s vs HDD ~0.2s (100x difference)${NC}"
    echo ""
}

# Warn if a path is on suboptimal storage for its purpose
# Arguments: path, component_type (database|index|covers|library|sources)
warn_storage_tier() {
    local target_path="$1"
    local component="$2"
    local tier
    tier=$(detect_storage_tier "$target_path")
    local tier_name
    tier_name=$(storage_tier_name "$tier")

    # Define recommended tiers per component
    local recommended=""
    local warning=""

    case "$component" in
        database | index)
            if [[ "$tier" == "hdd" ]]; then
                recommended="NVMe or SSD"
                warning="Database on HDD will significantly impact query performance"
            fi
            ;;
        covers)
            if [[ "$tier" == "hdd" ]]; then
                recommended="SSD"
                warning="Cover art on HDD may slow down UI loading"
            fi
            ;;
        library | sources)
            # HDD is acceptable for bulk audio files
            ;;
    esac

    if [[ -n "$warning" ]]; then
        echo ""
        echo -e "${YELLOW}⚠ Storage Warning:${NC}"
        echo -e "  Path: $target_path"
        echo -e "  Detected: ${tier_name}"
        echo -e "  Recommended: ${recommended}"
        echo -e "  ${DIM}${warning}${NC}"
        echo ""
        return 1
    fi
    return 0
}

# Display detected storage tiers for installation paths
show_detected_storage() {
    local app_dir="$1"
    local data_dir="$2"
    local db_dir="$3"

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}Detected Storage Tiers${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    local app_tier data_tier db_tier
    app_tier=$(detect_storage_tier "$app_dir") || app_tier="unknown"
    data_tier=$(detect_storage_tier "$data_dir") || data_tier="unknown"
    db_tier=$(detect_storage_tier "$db_dir") || db_tier="unknown"

    local app_color data_color db_color
    app_color=$(storage_tier_color "$app_tier")
    data_color=$(storage_tier_color "$data_tier")
    db_color=$(storage_tier_color "$db_tier")

    printf "  %-25s %s%-10s${NC}\n" "Application ($app_dir):" "$app_color" "$(storage_tier_name "$app_tier")"
    printf "  %-25s %s%-10s${NC}\n" "Data ($data_dir):" "$data_color" "$(storage_tier_name "$data_tier")"
    printf "  %-25s %s%-10s${NC}\n" "Database ($db_dir):" "$db_color" "$(storage_tier_name "$db_tier")"
    echo ""
}

prompt_delete_data() {
    # Prompt user about deleting audiobook data directories
    # Arguments:
    #   $1 - config file path to read data directories from
    #   $2 - "sudo" if sudo is required for deletion, empty otherwise
    #
    # Returns: Sets global variables for what to delete

    local config_file="$1"
    local use_sudo="$2"
    local data_dir=""
    local library_dir=""
    local sources_dir=""
    local supplements_dir=""

    # Read configuration to get data directories
    if [[ -f "$config_file" ]]; then
        source "$config_file"
        data_dir="${AUDIOBOOKS_DATA:-}"
        library_dir="${AUDIOBOOKS_LIBRARY:-}"
        sources_dir="${AUDIOBOOKS_SOURCES:-}"
        supplements_dir="${AUDIOBOOKS_SUPPLEMENTS:-}"
    fi

    # Initialize deletion flags
    DELETE_LIBRARY=false
    DELETE_SOURCES=false
    DELETE_SUPPLEMENTS=false
    DELETE_CONFIG=false

    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                    Data Removal Options                           ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "The following data directories were found:"
    echo ""

    # Check and display each data directory with size
    if [[ -n "$library_dir" ]] && [[ -d "$library_dir" ]]; then
        local lib_size=$(du -sh "$library_dir" 2>/dev/null | cut -f1)
        echo -e "  ${BOLD}Converted Audiobooks:${NC} $library_dir"
        echo "    Size: ${lib_size:-unknown}"
        local lib_count=$(find "$library_dir" -type f \( -name "*.m4b" -o -name "*.mp3" -o -name "*.opus" -o -name "*.flac" \) 2>/dev/null | wc -l)
        echo "    Files: ${lib_count} audiobook files"
        echo ""
    fi

    if [[ -n "$sources_dir" ]] && [[ -d "$sources_dir" ]]; then
        local src_size=$(du -sh "$sources_dir" 2>/dev/null | cut -f1)
        echo -e "  ${BOLD}Source Files (AAX/AAXC):${NC} $sources_dir"
        echo "    Size: ${src_size:-unknown}"
        local src_count=$(find "$sources_dir" -type f \( -name "*.aax" -o -name "*.aaxc" \) 2>/dev/null | wc -l)
        echo "    Files: ${src_count} source files"
        echo ""
    fi

    if [[ -n "$supplements_dir" ]] && [[ -d "$supplements_dir" ]]; then
        local sup_size=$(du -sh "$supplements_dir" 2>/dev/null | cut -f1)
        echo -e "  ${BOLD}Supplemental PDFs:${NC} $supplements_dir"
        echo "    Size: ${sup_size:-unknown}"
        local sup_count=$(find "$supplements_dir" -type f -name "*.pdf" 2>/dev/null | wc -l)
        echo "    Files: ${sup_count} PDF files"
        echo ""
    fi

    echo -e "${RED}WARNING: Deleted files cannot be recovered!${NC}"
    echo ""

    # Prompt for each category
    if [[ -n "$library_dir" ]] && [[ -d "$library_dir" ]]; then
        while true; do
            read -r -p "Delete converted audiobooks in $library_dir? [y/N]: " answer
            case "${answer,,}" in
                y | yes)
                    DELETE_LIBRARY=true
                    echo -e "  ${RED}→ Will delete converted audiobooks${NC}"
                    break
                    ;;
                n | no | "")
                    echo -e "  ${GREEN}→ Keeping converted audiobooks${NC}"
                    break
                    ;;
                *)
                    echo "  Please answer y(es) or n(o)"
                    ;;
            esac
        done
        echo ""
    fi

    if [[ -n "$sources_dir" ]] && [[ -d "$sources_dir" ]]; then
        while true; do
            read -r -p "Delete source files (AAX/AAXC) in $sources_dir? [y/N]: " answer
            case "${answer,,}" in
                y | yes)
                    DELETE_SOURCES=true
                    echo -e "  ${RED}→ Will delete source files${NC}"
                    break
                    ;;
                n | no | "")
                    echo -e "  ${GREEN}→ Keeping source files${NC}"
                    break
                    ;;
                *)
                    echo "  Please answer y(es) or n(o)"
                    ;;
            esac
        done
        echo ""
    fi

    if [[ -n "$supplements_dir" ]] && [[ -d "$supplements_dir" ]]; then
        while true; do
            read -r -p "Delete supplemental PDFs in $supplements_dir? [y/N]: " answer
            case "${answer,,}" in
                y | yes)
                    DELETE_SUPPLEMENTS=true
                    echo -e "  ${RED}→ Will delete supplemental PDFs${NC}"
                    break
                    ;;
                n | no | "")
                    echo -e "  ${GREEN}→ Keeping supplemental PDFs${NC}"
                    break
                    ;;
                *)
                    echo "  Please answer y(es) or n(o)"
                    ;;
            esac
        done
        echo ""
    fi

    if [[ -f "$config_file" ]]; then
        while true; do
            read -r -p "Delete configuration files? [y/N]: " answer
            case "${answer,,}" in
                y | yes)
                    DELETE_CONFIG=true
                    echo -e "  ${RED}→ Will delete configuration${NC}"
                    break
                    ;;
                n | no | "")
                    echo -e "  ${GREEN}→ Keeping configuration${NC}"
                    break
                    ;;
                *)
                    echo "  Please answer y(es) or n(o)"
                    ;;
            esac
        done
        echo ""
    fi

    # Confirm if anything is being deleted
    if [[ "$DELETE_LIBRARY" == "true" ]] || [[ "$DELETE_SOURCES" == "true" ]] \
        || [[ "$DELETE_SUPPLEMENTS" == "true" ]] || [[ "$DELETE_CONFIG" == "true" ]]; then
        echo ""
        echo -e "${RED}╔═══════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${RED}║                    CONFIRM DELETION                               ║${NC}"
        echo -e "${RED}╚═══════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "The following will be PERMANENTLY DELETED:"
        [[ "$DELETE_LIBRARY" == "true" ]] && echo -e "  ${RED}• Converted audiobooks${NC}"
        [[ "$DELETE_SOURCES" == "true" ]] && echo -e "  ${RED}• Source files (AAX/AAXC)${NC}"
        [[ "$DELETE_SUPPLEMENTS" == "true" ]] && echo -e "  ${RED}• Supplemental PDFs${NC}"
        [[ "$DELETE_CONFIG" == "true" ]] && echo -e "  ${RED}• Configuration files${NC}"
        echo ""

        while true; do
            read -r -p "Are you sure you want to proceed? [y/N]: " confirm
            case "${confirm,,}" in
                y | yes)
                    echo ""
                    echo -e "${YELLOW}Proceeding with deletion...${NC}"

                    # Perform deletions
                    if [[ "$DELETE_LIBRARY" == "true" ]] && [[ -d "$library_dir" ]]; then
                        echo "Deleting converted audiobooks..."
                        if [[ "$use_sudo" == "sudo" ]]; then
                            sudo rm -rf "$library_dir"
                        else
                            rm -rf "$library_dir"
                        fi
                    fi

                    if [[ "$DELETE_SOURCES" == "true" ]] && [[ -d "$sources_dir" ]]; then
                        echo "Deleting source files..."
                        if [[ "$use_sudo" == "sudo" ]]; then
                            sudo rm -rf "$sources_dir"
                        else
                            rm -rf "$sources_dir"
                        fi
                    fi

                    if [[ "$DELETE_SUPPLEMENTS" == "true" ]] && [[ -d "$supplements_dir" ]]; then
                        echo "Deleting supplemental PDFs..."
                        if [[ "$use_sudo" == "sudo" ]]; then
                            sudo rm -rf "$supplements_dir"
                        else
                            rm -rf "$supplements_dir"
                        fi
                    fi

                    if [[ "$DELETE_CONFIG" == "true" ]]; then
                        local config_dir=$(dirname "$config_file")
                        echo "Deleting configuration..."
                        if [[ "$use_sudo" == "sudo" ]]; then
                            sudo rm -rf "$config_dir"
                        else
                            rm -rf "$config_dir"
                        fi
                    fi

                    # Also delete empty parent data directory if it exists and is empty
                    if [[ -n "$data_dir" ]] && [[ -d "$data_dir" ]]; then
                        if [[ -z "$(ls -A "$data_dir" 2>/dev/null)" ]]; then
                            echo "Removing empty data directory..."
                            if [[ "$use_sudo" == "sudo" ]]; then
                                sudo rmdir "$data_dir" 2>/dev/null || true
                            else
                                rmdir "$data_dir" 2>/dev/null || true
                            fi
                        fi
                    fi

                    echo -e "${GREEN}Data deletion complete.${NC}"
                    break
                    ;;
                n | no | "")
                    echo -e "${GREEN}Deletion cancelled. All data preserved.${NC}"
                    break
                    ;;
                *)
                    echo "Please answer y(es) or n(o)"
                    ;;
            esac
        done
    else
        echo -e "${GREEN}No data selected for deletion. All files preserved.${NC}"
    fi
}

# -----------------------------------------------------------------------------
# Post-Install Verification
# -----------------------------------------------------------------------------

verify_installation_permissions() {
    # Verify that installed files have correct permissions and ownership
    # for the audiobooks service user to access them
    local install_type="$1" # "system" or "user"
    local issues_found=0

    echo ""
    echo -e "${BLUE}Verifying installation permissions...${NC}"

    if [[ "$install_type" == "system" ]]; then
        local APP_DIR="/opt/audiobooks"
        local SERVICE_USER="audiobooks"
        local SERVICE_GROUP="audiobooks"

        # MANDATORY: unconditional full-tree ownership + permission normalization.
        # Prior deployments left files owned by the deploying user (e.g. bosco)
        # with mode 700 directories, which broke audiobook-proxy static serving.
        # Every install/upgrade now normalizes the entire tree before exiting.
        echo -n "  Normalizing ownership + permissions (entire tree)... "
        sudo chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$APP_DIR"
        sudo find "$APP_DIR" -type d -exec chmod 755 {} +
        sudo find "$APP_DIR" -type f -exec chmod 644 {} +
        sudo find "$APP_DIR" -type f \( -name "*.sh" -o -name "launch*.sh" \) -exec chmod 755 {} +
        # Extension-less shebang scripts under scripts/ (e.g. audiobook-api,
        # audiobook-config). These are the canonical targets of /usr/local/bin
        # symlinks — if left at 644 the reconciler reports "missing wrapper".
        if [[ -d "$APP_DIR/scripts" ]]; then
            while IFS= read -r -d '' _f; do
                if sudo head -c 2 "$_f" 2>/dev/null | grep -q '^#!'; then
                    sudo chmod 755 "$_f"
                fi
            done < <(sudo find "$APP_DIR/scripts" -maxdepth 2 -type f -print0)
        fi
        [[ -d "$APP_DIR/library/venv/bin" ]] && sudo find "$APP_DIR/library/venv/bin" -type f -exec chmod 755 {} +
        [[ -d "$APP_DIR/library/audible-venv/bin" ]] && sudo find "$APP_DIR/library/audible-venv/bin" -type f -exec chmod 755 {} +
        # Sensitive files: tighter modes
        [[ -f "${CERT_DIR:-/etc/audiobooks/certs}/server.key" ]] && sudo chmod 640 "${CERT_DIR:-/etc/audiobooks/certs}/server.key"
        [[ -f /var/lib/audiobooks/auth.key ]] && sudo chmod 600 /var/lib/audiobooks/auth.key
        [[ -f /var/lib/audiobooks/auth.db ]] && sudo chmod 640 /var/lib/audiobooks/auth.db
        echo -e "${GREEN}OK${NC}"

        # Check directory permissions (should be 755)
        echo -n "  Checking directory permissions... "
        local bad_dirs=$(find "$APP_DIR" -type d -perm 700 2>/dev/null | wc -l)
        if [[ "$bad_dirs" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_dirs directories${NC}"
            sudo find "$APP_DIR" -type d -perm 700 -exec chmod 755 {} \;
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Check Python file permissions (should be 644, readable)
        echo -n "  Checking .py file permissions... "
        local bad_py=$(find "$APP_DIR" -name "*.py" \( -perm 600 -o -perm 700 -o -perm 711 \) 2>/dev/null | wc -l)
        if [[ "$bad_py" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_py files${NC}"
            sudo find "$APP_DIR" -name "*.py" \( -perm 600 -o -perm 700 -o -perm 711 \) -exec chmod 644 {} \;
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Check HTML/CSS/JS permissions (should be 644)
        echo -n "  Checking web file permissions... "
        local bad_web=$(find "$APP_DIR" \( -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 \) 2>/dev/null | wc -l)
        if [[ "$bad_web" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_web files${NC}"
            sudo find "$APP_DIR" \( -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 \) -exec chmod 644 {} \;
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Check critical directories have audiobooks group access
        echo -n "  Checking group ownership... "
        local critical_dirs=("$APP_DIR" "$APP_DIR/library" "/var/lib/audiobooks")
        local group_issues=0
        for dir in "${critical_dirs[@]}"; do
            if [[ -d "$dir" ]]; then
                local current_group=$(stat -c "%G" "$dir" 2>/dev/null)
                if [[ "$current_group" != "$SERVICE_GROUP" && "$current_group" != "root" ]]; then
                    sudo chgrp "$SERVICE_GROUP" "$dir" 2>/dev/null
                    ((group_issues++)) || true
                fi
            fi
        done
        if [[ "$group_issues" -gt 0 ]]; then
            echo -e "${YELLOW}fixed $group_issues directories${NC}"
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Verify no symlinks point to development project directory
        # Must check for ClaudeCodeProjects specifically, not $SCRIPT_DIR,
        # because when run from /opt/audiobooks, $SCRIPT_DIR matches legitimate production links
        echo -n "  Checking for project source dependencies... "
        local project_links=$(find /usr/local/bin -name "audiobook-*" -type l -exec readlink {} \; 2>/dev/null | grep -c "ClaudeCodeProjects" || true)
        if [[ "$project_links" -gt 0 ]]; then
            echo -e "${RED}WARNING: $project_links binaries link to project source!${NC}"
            echo -e "         Production should be independent of source repo."
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK (independent)${NC}"
        fi

    else
        # User installation checks
        local APP_DIR="$HOME/.local/share/audiobooks"

        # MANDATORY: unconditional full-tree permission normalization (user install).
        echo -n "  Normalizing permissions (entire tree)... "
        find "$APP_DIR" -type d -exec chmod 755 {} + 2>/dev/null
        find "$APP_DIR" -type f -exec chmod 644 {} + 2>/dev/null
        find "$APP_DIR" -type f \( -name "*.sh" -o -name "launch*.sh" \) -exec chmod 755 {} + 2>/dev/null
        [[ -d "$APP_DIR/library/venv/bin" ]] && find "$APP_DIR/library/venv/bin" -type f -exec chmod 755 {} + 2>/dev/null
        echo -e "${GREEN}OK${NC}"

        echo -n "  Checking directory permissions... "
        local bad_dirs=$(find "$APP_DIR" -type d -perm 700 2>/dev/null | wc -l)
        if [[ "$bad_dirs" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_dirs directories${NC}"
            find "$APP_DIR" -type d -perm 700 -exec chmod 755 {} \;
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK${NC}"
        fi

        echo -n "  Checking file permissions... "
        local bad_files=$(find "$APP_DIR" -type f \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) -perm 600 2>/dev/null | wc -l)
        if [[ "$bad_files" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_files files${NC}"
            find "$APP_DIR" -type f \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) -perm 600 -exec chmod 644 {} \;
            ((issues_found++)) || true
        else
            echo -e "${GREEN}OK${NC}"
        fi
    fi

    if [[ "$issues_found" -gt 0 ]]; then
        echo -e "${YELLOW}  Fixed $issues_found permission issues.${NC}"
    else
        echo -e "${GREEN}  All permissions verified.${NC}"
    fi

    return 0
}

# -----------------------------------------------------------------------------
# Port Availability Checking
# -----------------------------------------------------------------------------

# Default ports
DEFAULT_API_PORT=5001
DEFAULT_WEB_PORT=8090
DEFAULT_HTTP_REDIRECT_PORT=8080

# Current port settings (can be modified by user)
API_PORT="${API_PORT:-$DEFAULT_API_PORT}"
WEB_PORT="${WEB_PORT:-$DEFAULT_WEB_PORT}"
HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT:-$DEFAULT_HTTP_REDIRECT_PORT}"

check_port_available() {
    # Check if a port is available. Returns 0 if available, 1 if in use.
    local port="$1"

    # Try lsof first (most reliable)
    if command -v lsof >/dev/null 2>&1; then
        if lsof -i ":$port" >/dev/null 2>&1; then
            return 1 # Port in use
        fi
        return 0 # Port available
    fi

    # Fallback to ss
    if command -v ss >/dev/null 2>&1; then
        if ss -tlnH "sport = :$port" 2>/dev/null | grep -q .; then
            return 1 # Port in use
        fi
        return 0 # Port available
    fi

    # Fallback to netstat
    if command -v netstat >/dev/null 2>&1; then
        if netstat -tlnp 2>/dev/null | grep -q ":$port "; then
            return 1 # Port in use
        fi
        return 0 # Port available
    fi

    # Cannot check - assume available
    return 0
}

get_port_user() {
    # Get information about what's using a port
    local port="$1"

    if command -v lsof >/dev/null 2>&1; then
        lsof -i ":$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1 " (PID " $2 ")"}'
        return
    fi

    if command -v ss >/dev/null 2>&1; then
        ss -tlnp "sport = :$port" 2>/dev/null | awk 'NR==2 {gsub(/.*pid=/,""); gsub(/,.*$/,""); print "PID " $0}'
        return
    fi

    echo "unknown process"
}

prompt_alternate_port() {
    # Prompt user for an alternate port
    local port_name="$1"
    local current_port="$2"
    local default_alt="$3"

    echo ""
    while true; do
        read -r -p "Enter alternate port for ${port_name} [${default_alt}]: " new_port
        new_port="${new_port:-$default_alt}"

        # Validate it's a number
        if ! [[ "$new_port" =~ ^[0-9]+$ ]]; then
            echo -e "${RED}Invalid port number. Please enter a number.${NC}"
            continue
        fi

        # Validate range
        if [[ "$new_port" -lt 1 ]] || [[ "$new_port" -gt 65535 ]]; then
            echo -e "${RED}Port must be between 1 and 65535.${NC}"
            continue
        fi

        # Check if this alternate is also in use
        if ! check_port_available "$new_port"; then
            local user=$(get_port_user "$new_port")
            echo -e "${RED}Port $new_port is also in use by: $user${NC}"
            echo "Please choose a different port."
            continue
        fi

        echo "$new_port"
        return 0
    done
}

check_all_ports() {
    # Check all ports and handle conflicts interactively
    # Returns 0 if all ports are available/resolved, 1 if user chose to abort

    local has_conflicts=false
    local api_conflict=false
    local web_conflict=false
    local redirect_conflict=false

    echo -e "${BLUE}Checking port availability...${NC}"

    # Check API port
    if ! check_port_available "$API_PORT"; then
        local user=$(get_port_user "$API_PORT")
        echo -e "${YELLOW}  Port $API_PORT (API) is in use by: $user${NC}"
        api_conflict=true
        has_conflicts=true
    else
        echo -e "${GREEN}  Port $API_PORT (API) is available${NC}"
    fi

    # Check HTTPS port
    if ! check_port_available "$WEB_PORT"; then
        local user=$(get_port_user "$WEB_PORT")
        echo -e "${YELLOW}  Port $WEB_PORT (HTTPS) is in use by: $user${NC}"
        web_conflict=true
        has_conflicts=true
    else
        echo -e "${GREEN}  Port $WEB_PORT (HTTPS) is available${NC}"
    fi

    # Check HTTP redirect port
    if ! check_port_available "$HTTP_REDIRECT_PORT"; then
        local user=$(get_port_user "$HTTP_REDIRECT_PORT")
        echo -e "${YELLOW}  Port $HTTP_REDIRECT_PORT (HTTP redirect) is in use by: $user${NC}"
        redirect_conflict=true
        has_conflicts=true
    else
        echo -e "${GREEN}  Port $HTTP_REDIRECT_PORT (HTTP redirect) is available${NC}"
    fi

    # If no conflicts, we're done
    if [[ "$has_conflicts" == "false" ]]; then
        echo ""
        return 0
    fi

    # Handle conflicts
    echo ""
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                    PORT CONFLICT DETECTED                         ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "One or more ports are already in use. Options:"
    echo ""
    echo "  1) Choose alternate ports"
    echo "  2) Continue anyway (services may fail to start)"
    echo "  3) Abort installation"
    echo ""

    while true; do
        read -r -p "Enter your choice [1-3]: " choice
        case "$choice" in
            1)
                # Prompt for alternate ports for each conflict
                if [[ "$api_conflict" == "true" ]]; then
                    local new_api=$(prompt_alternate_port "API server" "$API_PORT" "$((API_PORT + 1))")
                    API_PORT="$new_api"
                    echo -e "${GREEN}  API port set to: $API_PORT${NC}"
                fi

                if [[ "$web_conflict" == "true" ]]; then
                    local new_web=$(prompt_alternate_port "HTTPS web server" "$WEB_PORT" "$((WEB_PORT + 1))")
                    WEB_PORT="$new_web"
                    echo -e "${GREEN}  HTTPS port set to: $WEB_PORT${NC}"
                fi

                if [[ "$redirect_conflict" == "true" ]]; then
                    local new_redirect=$(prompt_alternate_port "HTTP redirect" "$HTTP_REDIRECT_PORT" "$((HTTP_REDIRECT_PORT + 1))")
                    HTTP_REDIRECT_PORT="$new_redirect"
                    echo -e "${GREEN}  HTTP redirect port set to: $HTTP_REDIRECT_PORT${NC}"
                fi

                echo ""
                echo -e "${GREEN}Port configuration updated.${NC}"
                return 0
                ;;
            2)
                echo ""
                echo -e "${YELLOW}Continuing with installation. Note: Services may fail to start if ports are in use.${NC}"
                return 0
                ;;
            3)
                echo ""
                echo -e "${RED}Installation aborted.${NC}"
                return 1
                ;;
            *)
                echo "Please enter 1, 2, or 3."
                ;;
        esac
    done
}

# -----------------------------------------------------------------------------
# System Installation
# -----------------------------------------------------------------------------

do_system_install() {
    local data_dir="${DATA_DIR:-/srv/audiobooks}"
    local db_dir="/var/lib/audiobooks"

    # Paths for system installation
    local INSTALL_PREFIX="/usr/local"
    local CONFIG_DIR="/etc/audiobooks"
    local APP_DIR="/opt/audiobooks" # Canonical application location
    local BIN_DIR="${INSTALL_PREFIX}/bin"
    local SYSTEMD_DIR="/etc/systemd/system"

    echo -e "${GREEN}=== System Installation ===${NC}"
    echo ""
    echo "Installation paths:"
    echo "  Executables:  ${BIN_DIR}/"
    echo "  Config:       ${CONFIG_DIR}/"
    echo "  Application:  ${APP_DIR}/"
    echo "  Services:     ${SYSTEMD_DIR}/"
    echo "  Data:         ${data_dir}/"
    echo "  Database:     ${db_dir}/"
    echo ""

    # Show detected storage tiers
    show_detected_storage "$APP_DIR" "$data_dir" "$db_dir"

    # Warn about suboptimal storage placement
    local storage_warnings=0
    if ! warn_storage_tier "$db_dir" "database"; then
        ((storage_warnings++)) || true
    fi

    if [[ $storage_warnings -gt 0 ]]; then
        echo -e "${YELLOW}Consider using --data-dir to specify a path on faster storage,${NC}"
        echo -e "${YELLOW}or configure AUDIOBOOKS_DATABASE in /etc/audiobooks/audiobooks.conf${NC}"
        echo -e "${YELLOW}to place the database on NVMe/SSD after installation.${NC}"
        echo ""
        read -r -p "Continue with current storage configuration? [Y/n]: " continue_choice
        if [[ "${continue_choice,,}" == "n" ]]; then
            echo -e "${YELLOW}Installation cancelled. Adjust paths and try again.${NC}"
            return 1
        fi
    fi

    # Check port availability before proceeding
    if ! check_all_ports; then
        return 1
    fi

    echo "Port configuration:"
    echo "  API:           ${API_PORT}"
    echo "  HTTPS:         ${WEB_PORT}"
    echo "  HTTP redirect: ${HTTP_REDIRECT_PORT}"
    echo ""

    # Create audiobooks service account with canonical UID/GID.
    #
    # Canonical values are fixed across ALL environments (prod host, dev VM, QA
    # VM, test VM, Docker image) so that bind-mounted data directories, ownership
    # stamps, and cross-env data transfers (transfer.py) stay consistent. Without
    # this, a Docker container built with the default `audiobooks` UID from the
    # distro's --system range won't match the host's chown stamps — resulting in
    # the container seeing files as "not mine", triggering re-scans, and writing
    # new files that the host-side service account can't then read.
    #
    # Conflict handling:
    #   - If the audiobooks group/user already exist with DIFFERENT UID/GID,
    #     install.sh emits a WARN and continues (don't break existing installs
    #     with a forced renumber — that would require re-chowning every file).
    #     A separate migration (scripts/migrate-audiobooks-uid.sh) handles that.
    #   - If UID/GID 935/934 are taken by a different user/group on this host,
    #     abort with a clear error.
    # Preferred starting point for matched UID:GID. install.sh auto-probes
    # upward if this pair is taken — operators never have to edit this file.
    # Also honors AUDIOBOOKS_PREFERRED_UID / _GID env vars if the operator
    # wants to steer the pick (e.g. match an existing Docker-host UID for
    # bind-mount portability).
    AUDIOBOOKS_PREFERRED_UID="${AUDIOBOOKS_PREFERRED_UID:-935}"
    AUDIOBOOKS_PREFERRED_GID="${AUDIOBOOKS_PREFERRED_GID:-935}"

    # Probe for a free matched UID:GID pair, starting at the preferred value
    # and walking upward to the first number where BOTH are free. This
    # eliminates install-time collisions on hosts where 935 or 935 is
    # already in use by an unrelated service (e.g. prod's GID 935 is held
    # by an unrelated 'empower' group). Cap the search at UID 65000 —
    # realistic ceiling well below the libc 16-bit boundary.
    _probe_free_uidgid() {
        local n="$1"
        while [[ $n -lt 65000 ]]; do
            if ! getent passwd "$n" >/dev/null 2>&1 \
                && ! getent group "$n" >/dev/null 2>&1; then
                echo "$n"
                return 0
            fi
            n=$((n + 1))
        done
        return 1
    }

    echo -e "${BLUE}Setting up service account...${NC}"

    # If the user already exists, honor its UID and its group's GID. No
    # migration here — operator must run scripts/migrate-audiobooks-uid.sh
    # explicitly if they want to change it.
    if getent passwd audiobooks >/dev/null 2>&1; then
        AUDIOBOOKS_UID=$(getent passwd audiobooks | cut -d: -f3)
        AUDIOBOOKS_GID=$(getent passwd audiobooks | cut -d: -f4)
        if [[ "$AUDIOBOOKS_UID" == "$AUDIOBOOKS_GID" ]]; then
            echo "  User 'audiobooks' already exists at UID=${AUDIOBOOKS_UID} GID=${AUDIOBOOKS_GID} (matched)"
        else
            echo -e "${YELLOW}  WARN: User 'audiobooks' exists with UID=${AUDIOBOOKS_UID} GID=${AUDIOBOOKS_GID} (not matched)${NC}"
            echo -e "${YELLOW}        For matched UID:GID on future upgrades, run scripts/migrate-audiobooks-uid.sh${NC}"
        fi
    else
        # Fresh creation. Find a free matched pair starting at the preferred value.
        local matched_id
        matched_id=$(_probe_free_uidgid "$AUDIOBOOKS_PREFERRED_UID") || {
            echo -e "${RED}  ERROR: could not find a free matched UID:GID pair in range ${AUDIOBOOKS_PREFERRED_UID}..65000${NC}"
            exit 1
        }
        AUDIOBOOKS_UID="$matched_id"
        AUDIOBOOKS_GID="$matched_id"
        if [[ "$matched_id" != "$AUDIOBOOKS_PREFERRED_UID" ]]; then
            echo -e "${YELLOW}  Preferred UID=${AUDIOBOOKS_PREFERRED_UID} was taken; using UID=${matched_id} GID=${matched_id} instead${NC}"
        fi

        echo "  Creating 'audiobooks' group with GID ${AUDIOBOOKS_GID}..."
        sudo groupadd --system --gid "${AUDIOBOOKS_GID}" audiobooks

        echo "  Creating 'audiobooks' service user with UID ${AUDIOBOOKS_UID}..."
        sudo useradd --system --uid "${AUDIOBOOKS_UID}" --gid audiobooks \
            --shell /usr/sbin/nologin \
            --home-dir /var/lib/audiobooks --comment "Audiobook Library Service" audiobooks
    fi

    # Persist resolved UID/GID into /etc/audiobooks/audiobooks.conf so every
    # downstream component (Dockerfile build args, docker-compose.yml PUID/PGID,
    # systemd units, migration scripts) reads from one source of truth.
    # The config dir may not exist yet on first install — we'll write this
    # file below right after `mkdir -p ${CONFIG_DIR}`.
    export AUDIOBOOKS_UID AUDIOBOOKS_GID

    # Add installer to audiobooks group for file access
    if ! groups "$USER" 2>/dev/null | grep -qw audiobooks; then
        echo "  Adding $USER to 'audiobooks' group..."
        sudo usermod -aG audiobooks "$USER"
        echo -e "${YELLOW}  NOTE: Log out and back in for group membership to take effect${NC}"
    fi
    echo ""

    # Create directories
    echo -e "${BLUE}Creating directories...${NC}"
    sudo mkdir -p "${CONFIG_DIR}"
    sudo mkdir -p "${CONFIG_DIR}/scripts"
    sudo mkdir -p "${APP_DIR}"
    sudo mkdir -p "${APP_DIR}/library/data"
    sudo mkdir -p "${data_dir}/Library"
    sudo mkdir -p "${data_dir}/Sources"
    sudo mkdir -p "${data_dir}/Supplements"
    sudo mkdir -p "/var/lib/audiobooks"
    sudo mkdir -p "/var/lib/audiobooks/data"
    sudo mkdir -p "/var/lib/audiobooks/db"
    # Streaming translation audio buffer — per-segment opus files staged by
    # the stream-translate worker. Matches AUDIOBOOKS_STREAMING_AUDIO_DIR
    # default in lib/audiobook-config.sh and library/config.py.
    sudo mkdir -p "/var/lib/audiobooks/streaming-audio"
    # Streaming translation subtitles cache — consolidated per-chapter VTT
    # files written by the streaming pipeline. Lives here (writable under
    # /var/lib) rather than the read-only install tree at /opt/audiobooks/
    # library, which systemd ProtectSystem=strict makes immutable at runtime.
    # Matches AUDIOBOOKS_STREAMING_SUBTITLES_DIR default.
    sudo mkdir -p "/var/lib/audiobooks/streaming-subtitles"
    sudo mkdir -p "/var/log/audiobooks"
    sudo mkdir -p "${data_dir}/.index"
    sudo chown -R audiobooks:audiobooks "/var/lib/audiobooks"
    sudo chmod 0750 "/var/lib/audiobooks/streaming-audio"
    sudo chmod 0755 "/var/lib/audiobooks/streaming-subtitles"
    sudo chown audiobooks:audiobooks "/var/log/audiobooks"
    sudo chown audiobooks:audiobooks "${data_dir}"
    sudo chown audiobooks:audiobooks "${data_dir}/Library"
    sudo chown audiobooks:audiobooks "${data_dir}/Sources"
    sudo chown audiobooks:audiobooks "${data_dir}/Supplements"
    sudo chown audiobooks:audiobooks "${data_dir}/.index"

    # Install logrotate configuration
    if [[ -f "${SCRIPT_DIR}/config/logrotate-audiobooks" ]]; then
        sudo cp "${SCRIPT_DIR}/config/logrotate-audiobooks" /etc/logrotate.d/audiobooks
        sudo chmod 644 /etc/logrotate.d/audiobooks
        echo "  Installed: /etc/logrotate.d/audiobooks"
    fi

    # Install library files
    echo -e "${BLUE}Installing library files...${NC}"
    sudo cp -r "${SCRIPT_DIR}/library" "${APP_DIR}/"
    sudo cp -r "${SCRIPT_DIR}/lib" "${APP_DIR}/"
    [[ -d "${SCRIPT_DIR}/converter" ]] && sudo cp -r "${SCRIPT_DIR}/converter" "${APP_DIR}/"

    # Install VERSION file
    if [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
        sudo cp "${SCRIPT_DIR}/VERSION" "${APP_DIR}/"
        sudo chmod 644 "${APP_DIR}/VERSION"
        echo "  Installed: ${APP_DIR}/VERSION ($(cat "${SCRIPT_DIR}/VERSION"))"
    fi

    # Install reference-system snapshot for the About page
    if [[ -f "${SCRIPT_DIR}/docs/reference-system.yml" ]]; then
        sudo cp "${SCRIPT_DIR}/docs/reference-system.yml" "${APP_DIR}/"
        sudo chmod 644 "${APP_DIR}/reference-system.yml"
        echo "  Installed: ${APP_DIR}/reference-system.yml"
    fi

    # Remove copied project venv — it contains dev-machine symlinks
    # The venv will be recreated below with system Python
    [[ -d "${APP_DIR}/library/venv" ]] && sudo rm -rf "${APP_DIR}/library/venv"

    # Fix ownership — sudo cp creates files as root:root, but the audiobooks
    # service user needs to read them (ProtectSystem=strict prevents world-read fallback)
    sudo chown -R audiobooks:audiobooks "${APP_DIR}"
    # Ensure source files are readable (644) and shell scripts are executable (755)
    sudo find "${APP_DIR}" -type f \( -name "*.py" -o -name "*.sql" -o -name "*.css" -o -name "*.html" -o -name "*.js" \) -exec chmod 644 {} +
    sudo find "${APP_DIR}" -type f -name "*.sh" -exec chmod 755 {} +

    # Update version in utilities.html
    local new_version=$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null)
    if [[ -n "$new_version" ]] && [[ -f "${APP_DIR}/library/web-v2/utilities.html" ]]; then
        echo -e "${BLUE}Setting version to v${new_version} in utilities.html...${NC}"
        sudo sed -i "s/· v[0-9.]*\"/· v${new_version}\"/" "${APP_DIR}/library/web-v2/utilities.html"
    fi

    # Bump HTML cachebust stamps — prevents browsers from serving pre-install
    # cached JS/CSS after a fresh install on a machine that had an older
    # version. Same helper upgrade.sh uses. See scripts/bump-cachebust.sh.
    if [[ -x "${SCRIPT_DIR}/scripts/bump-cachebust.sh" ]]; then
        sudo bash "${SCRIPT_DIR}/scripts/bump-cachebust.sh" "$(date +%s)" "${APP_DIR}/library/web-v2" \
            || echo -e "${YELLOW}  cachebust bump had a warning (non-fatal)${NC}"
    fi

    # Create backward-compat symlink for any scripts that reference old path
    sudo mkdir -p "/usr/local/lib"
    sudo ln -sfn "${APP_DIR}/lib" "/usr/local/lib/audiobooks"
    sudo cp "${SCRIPT_DIR}/etc/audiobooks.conf.example" "${CONFIG_DIR}/"

    # Create config file if it doesn't exist
    if [[ ! -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        echo -e "${BLUE}Creating configuration file...${NC}"
        sudo tee "${CONFIG_DIR}/audiobooks.conf" >/dev/null <<EOF
# Audiobook Library Configuration
# Generated by install.sh on $(date +%Y-%m-%d)

# Data directories
AUDIOBOOKS_DATA="${data_dir}"
AUDIOBOOKS_LIBRARY="\${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="\${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="\${AUDIOBOOKS_DATA}/Supplements"

# Application directories
# NOTE: AUDIOBOOKS_COVERS, AUDIOBOOKS_DATABASE, AUDIOBOOKS_CERTS, and
# AUDIOBOOKS_VENV are intentionally NOT set here — they fall through to
# library/config.py defaults (/var/lib/audiobooks/covers, .../db/audiobooks.db,
# \${AUDIOBOOKS_HOME}/library/certs, \${AUDIOBOOKS_HOME}/library/venv). Setting
# them here caused drift whenever config.py defaults changed. Override only if
# you need a non-default location.
AUDIOBOOKS_HOME="${APP_DIR}"
AUDIOBOOKS_LOGS="/var/log/audiobooks"

# Internal data directory for scan results and intermediate files
DATA_DIR="/var/lib/audiobooks/data"

# Runtime directory for locks and FIFOs
AUDIOBOOKS_RUN_DIR="/var/lib/audiobooks/.run"

# Server settings
AUDIOBOOKS_API_PORT="${API_PORT}"
AUDIOBOOKS_WEB_PORT="${WEB_PORT}"
AUDIOBOOKS_HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT}"
AUDIOBOOKS_BIND_ADDRESS="0.0.0.0"
AUDIOBOOKS_HTTPS_ENABLED="true"
AUDIOBOOKS_HTTP_REDIRECT_ENABLED="true"

# Authentication (multi-user support)
# Set AUTH_ENABLED="true" to require login for remote access
# When disabled (default), admin endpoints are restricted to localhost
AUTH_ENABLED="false"
AUTH_DATABASE="/var/lib/audiobooks/auth.db"
AUTH_KEY_FILE="/etc/audiobooks/auth.key"

# Remote access (only needed when AUTH_ENABLED="true")
# See audiobooks.conf.example for full documentation
#AUDIOBOOKS_HOSTNAME=""
#BASE_URL=""
#CORS_ORIGIN=""

# --- Streaming translation (optional) ---
# Enables the real-time streaming translation pipeline (non-EN locales).
# If no STT backend is configured, streaming translation is disabled and only
# DeepL text-only translation runs. See docs/STREAMING-TRANSLATION.md and
# docs/SERVERLESS-OPS.md for setup.
#
# Translation backend — DeepL is currently the only supported translator.
#AUDIOBOOKS_DEEPL_API_KEY=""                        # Get one at https://www.deepl.com/pro-api
#
# STT backend — choose ONE OR MORE of the options below. Configuring multiple
# providers gives peer redundancy (if one farm is down the others keep serving)
# and load sharing (parallel workers naturally spread across farms). Endpoint
# IDs must be provisioned separately per deployment.
#
# Option 1 — RunPod serverless (pay-per-second, no minimum spend)
#AUDIOBOOKS_RUNPOD_API_KEY=""                       # https://www.runpod.io/console/user/settings
#AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT=""    # Streaming endpoint ID (warm pool, min_workers>=1)
#AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT=""      # Backlog endpoint ID (cold pool, min_workers=0)
#
# Option 2 — Vast.ai serverless (cheaper on some GPU classes)
#AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY=""            # https://cloud.vast.ai/ → Account → API Keys
#AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT="" # Vast.ai serverless endpoint ID for streaming STT
#AUDIOBOOKS_VASTAI_SERVERLESS_BACKLOG_ENDPOINT=""   # Vast.ai serverless endpoint ID for backlog STT
#
# Option 3 — Self-hosted GPU Whisper service (CUDA/ROCm/Apple Silicon)
#AUDIOBOOKS_WHISPER_GPU_HOST=""                     # e.g., 192.168.1.50 or whisper.lan
#AUDIOBOOKS_WHISPER_GPU_PORT="8080"                 # HTTP port of the whisper-gpu-service
#
# Option 4 — CPU-only faster-whisper (no GPU; slower but zero cost)
#   Currently requires code changes to wire in; see library/localization/stt/
#   for the provider interface. Treat as an advanced deployment option.
#
#AUDIOBOOKS_TTS_PROVIDER="edge-tts"                 # edge-tts (free, no GPU) | xtts (GPU) | coqui
EOF
    fi

    # Set up authentication key file (for multi-user support)
    echo -e "${BLUE}Setting up authentication...${NC}"
    local auth_key_file="${CONFIG_DIR}/auth.key"
    if [[ ! -f "$auth_key_file" ]]; then
        echo "  Generating encryption key for auth database..."
        # Generate 32-byte random key as 64 hex characters
        sudo sh -c "head -c 32 /dev/urandom | xxd -p | tr -d '\\n' > '$auth_key_file'"
        sudo chown audiobooks:audiobooks "$auth_key_file"
        sudo chmod 600 "$auth_key_file"
        echo "  Created: $auth_key_file"
    else
        echo "  Auth key file already exists"
    fi

    # Create Cloudflare CDN cache purge token file (placeholder)
    local cf_token_file="${CONFIG_DIR}/cloudflare-api-token"
    if [[ ! -f "$cf_token_file" ]]; then
        echo "  Creating Cloudflare token placeholder..."
        sudo tee "$cf_token_file" >/dev/null <<'CFEOF'
# Cloudflare credentials for CDN cache purge
# Used by audiobook-api service (POST /api/system/purge-cache)
# Fill in your credentials to enable CDN cache purging from the web UI.
#CF_GLOBAL_API_KEY=your-global-api-key
#CF_AUTH_EMAIL=your-cloudflare-email
CFEOF
        sudo chown audiobooks:audiobooks "$cf_token_file"
        sudo chmod 640 "$cf_token_file"
        echo "  Created: $cf_token_file (edit to enable CDN cache purge)"
    fi

    # Initialize auth database directory
    sudo mkdir -p "/var/lib/audiobooks"
    sudo chown audiobooks:audiobooks "/var/lib/audiobooks"

    # Initialize audiobook database if it doesn't exist
    local db_file="/var/lib/audiobooks/db/audiobooks.db"
    if [[ ! -f "$db_file" ]]; then
        echo -e "${BLUE}Initializing database...${NC}"
        local schema_file="${APP_DIR}/library/backend/schema.sql"
        if [[ -f "$schema_file" ]]; then
            cat "$schema_file" | sudo -u audiobooks sqlite3 "$db_file"
            echo "  Created: $db_file"
        else
            echo -e "${YELLOW}  Warning: schema.sql not found, skipping database initialization${NC}"
        fi
    fi

    # Schema migration: add enrichment_source column if missing (idempotent)
    if [[ -f "$db_file" ]]; then
        local has_enrichment_source
        has_enrichment_source=$(sudo -u audiobooks sqlite3 "$db_file" \
            "PRAGMA table_info(audiobooks);" 2>/dev/null | grep -c "enrichment_source" || true)
        if [[ "$has_enrichment_source" == "0" ]]; then
            echo -e "${BLUE}Migrating database: adding enrichment_source column...${NC}"
            sudo -u audiobooks sqlite3 "$db_file" \
                "ALTER TABLE audiobooks ADD COLUMN enrichment_source TEXT;" 2>/dev/null || true
            echo "  Added: enrichment_source column to audiobooks table"
        fi
    fi

    # Data-state migrations: reclassify/backfill DB rows after fresh install.
    # Fresh installs have no user overrides, so all migrations run automatically.
    local data_migrations_dir="${SCRIPT_DIR}/data-migrations"
    if [[ -d "$data_migrations_dir" ]] && [[ -f "$db_file" ]]; then
        local venv_python="${APP_DIR}/library/venv/bin/python"
        if [[ -x "$venv_python" ]]; then
            echo -e "${BLUE}Running data migrations...${NC}"
            for migration in "$data_migrations_dir"/*.sh; do
                [[ -f "$migration" ]] || continue
                export DB_PATH="$db_file"
                export VENV_PYTHON="$venv_python"
                export APP_DIR
                export USE_SUDO="sudo"
                export DRY_RUN="false"
                export INTERACTIVE="false"
                # Support two migration styles:
                #   (a) top-level commands: work is done during `source`
                #   (b) function-pattern: script defines `run_migration` and
                #       expects the caller to invoke it after sourcing
                # Always source; if `run_migration` is defined, invoke and
                # unset it so it can't leak into the next iteration. This
                # mirrors upgrade.sh::apply_data_migrations — both paths MUST
                # stay in sync or fresh installs will silently drift from
                # upgraded installs.
                source "$migration"
                if declare -F run_migration >/dev/null 2>&1; then
                    if ! run_migration; then
                        echo -e "${YELLOW}  Migration $(basename "$migration") reported non-zero exit${NC}"
                    fi
                    unset -f run_migration
                fi
            done
        fi
    fi
    echo ""

    # Install ALL scripts to /opt/audiobooks/scripts/ (canonical location)
    # This includes management scripts AND wrapper scripts (audiobook-api, etc.)
    # All /usr/local/bin/ entries will be symlinks to this canonical location.
    echo -e "${BLUE}Installing scripts to canonical location...${NC}"
    local APP_SCRIPTS_DIR="/opt/audiobooks/scripts"
    sudo mkdir -p "${APP_SCRIPTS_DIR}"

    # Copy operational scripts from scripts/ directory to canonical location
    # Dev-only scripts (git hooks, dev-machine admin tools) stay in the project
    if [[ -d "${SCRIPT_DIR}/scripts" ]]; then
        for script in "${SCRIPT_DIR}/scripts/"*; do
            if [[ -f "$script" ]]; then
                local script_name=$(basename "$script")
                case "$script_name" in
                    install-hooks.sh | purge-users.sh | setup-email.sh) continue ;;
                esac
                sudo cp "$script" "${APP_SCRIPTS_DIR}/"
                sudo chmod 755 "${APP_SCRIPTS_DIR}/${script_name}"
                echo "  Installed: ${APP_SCRIPTS_DIR}/${script_name}"
            fi
        done
    fi

    # Copy upgrade and migrate scripts to canonical location
    for script in upgrade.sh migrate-api.sh; do
        if [[ -f "${SCRIPT_DIR}/${script}" ]]; then
            sudo cp "${SCRIPT_DIR}/${script}" "${APP_SCRIPTS_DIR}/"
            sudo chmod 755 "${APP_SCRIPTS_DIR}/${script}"
            echo "  Installed: ${APP_SCRIPTS_DIR}/${script}"
        fi
    done

    # Create symlinks in /usr/local/bin/ pointing to canonical scripts
    refresh_bin_symlinks "/opt/audiobooks" "sudo"

    # Store release info for GitHub-based upgrades
    echo -e "${BLUE}Storing release metadata...${NC}"
    if [[ -f "${SCRIPT_DIR}/.release-info" ]]; then
        sudo cp "${SCRIPT_DIR}/.release-info" "/opt/audiobooks/"
    else
        # Create default release info
        sudo tee "/opt/audiobooks/.release-info" >/dev/null <<EOF
{
  "github_repo": "TheBoscoClub/Audiobook-Manager",
  "github_api": "https://api.github.com/repos/TheBoscoClub/Audiobook-Manager",
  "version": "$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo "unknown")",
  "install_date": "$(date -Iseconds)",
  "install_type": "system"
}
EOF
    fi
    sudo chmod 644 "/opt/audiobooks/.release-info"

    # Setup Python virtual environment
    # CRITICAL: Use system Python explicitly — pyenv shims create symlinks into
    # /home/ which are inaccessible under systemd ProtectHome=yes
    # Always recreate: project venv was removed above, and fresh venv ensures
    # correct symlinks for this machine's Python installation
    echo -e "${BLUE}Setting up Python virtual environment (system Python)...${NC}"
    [[ -d "${APP_DIR}/library/venv" ]] && sudo rm -rf "${APP_DIR}/library/venv"
    local sys_python="/usr/bin/python3"
    [[ -x /usr/bin/python3.14 ]] && sys_python="/usr/bin/python3.14"
    sudo "$sys_python" -m venv "${APP_DIR}/library/venv"
    sudo chown -R audiobooks:audiobooks "${APP_DIR}/library/venv"
    # Install all dependencies from requirements.txt
    if [[ -f "${APP_DIR}/library/requirements.txt" ]]; then
        echo -e "${BLUE}Installing Python dependencies from requirements.txt...${NC}"
        sudo -u audiobooks "${APP_DIR}/library/venv/bin/pip" install --quiet \
            -r "${APP_DIR}/library/requirements.txt"
    else
        echo -e "${YELLOW}Warning: requirements.txt not found, installing Flask only${NC}"
        sudo -u audiobooks "${APP_DIR}/library/venv/bin/pip" install --quiet Flask
    fi

    # Isolated venv for audible-cli (avoids httpx version conflict with main app)
    local audible_venv="/var/lib/audiobooks/audible-venv"
    echo -e "${BLUE}Setting up audible-cli virtual environment...${NC}"
    [[ -d "$audible_venv" ]] && sudo rm -rf "$audible_venv"
    sudo "$sys_python" -m venv "$audible_venv"
    sudo chown -R audiobooks:audiobooks "$audible_venv"
    sudo -u audiobooks "$audible_venv/bin/pip" install --quiet 'audible-cli>=0.3.2'
    echo -e "${GREEN}  audible-cli installed in isolated venv${NC}"

    # Generate SSL certificate if needed
    local CERT_DIR="${APP_DIR}/library/certs"
    if [[ ! -f "${CERT_DIR}/server.crt" ]]; then
        echo -e "${BLUE}Generating SSL certificate (3-year validity)...${NC}"
        sudo mkdir -p "${CERT_DIR}"
        sudo openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
            -nodes -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.crt" \
            -subj "/CN=localhost/O=Audiobooks/C=US" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
            2>/dev/null
        sudo chown audiobooks:audiobooks "${CERT_DIR}/server.key" "${CERT_DIR}/server.crt"
        sudo chmod 640 "${CERT_DIR}/server.key"
        sudo chmod 644 "${CERT_DIR}/server.crt"
    fi

    # Install systemd services
    if [[ "$INSTALL_SERVICES" == "true" ]]; then
        echo -e "${BLUE}Installing systemd services...${NC}"

        # API service
        sudo tee "${SYSTEMD_DIR}/audiobook-api.service" >/dev/null <<EOF
[Unit]
Description=Audiobooks Library API Server
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=network.target

[Service]
Type=simple
EnvironmentFile=${CONFIG_DIR}/audiobooks.conf
ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:\${AUDIOBOOKS_API_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobook-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

        # Install services from systemd/ directory (includes proxy, redirect, converter, etc.)
        # Note: audiobook-proxy.service replaces the old audiobook-web.service for system installs
        # Filter to unit types only — audiobooks-tmpfiles.conf is a tmpfiles.d config
        # and MUST NOT land in ${SYSTEMD_DIR}; it's handled separately below.
        if [[ -d "${SCRIPT_DIR}/systemd" ]]; then
            echo -e "${BLUE}Installing conversion and management services...${NC}"
            for service_file in "${SCRIPT_DIR}/systemd/"*.service "${SCRIPT_DIR}/systemd/"*.timer "${SCRIPT_DIR}/systemd/"*.path "${SCRIPT_DIR}/systemd/"*.target; do
                if [[ -f "$service_file" ]]; then
                    local service_name=$(basename "$service_file")
                    # Skip the target file - we handle that specially
                    if [[ "$service_name" == "audiobook.target" ]]; then
                        continue
                    fi
                    sudo cp "$service_file" "${SYSTEMD_DIR}/${service_name}"
                    sudo chmod 644 "${SYSTEMD_DIR}/${service_name}"
                    echo "  Installed: ${service_name}"
                fi
            done

            # Patch ReadWritePaths if data dir differs from default /srv/audiobooks.
            # ProtectSystem=strict makes the filesystem read-only except for listed paths.
            # Without this, cover art extraction and other data writes silently fail.
            if [[ "$data_dir" != "/srv/audiobooks" ]]; then
                local api_service="${SYSTEMD_DIR}/audiobook-api.service"
                if sudo grep -q "ReadWritePaths=" "$api_service" 2>/dev/null; then
                    sudo sed -i "s|ReadWritePaths=\(.*\)|ReadWritePaths=\1 ${data_dir}|" "$api_service"
                    echo "  Patched: audiobook-api.service ReadWritePaths += ${data_dir}"
                fi
                # Also update RequiresMountsFor so systemd waits for the mount
                if sudo grep -q "RequiresMountsFor=" "$api_service" 2>/dev/null; then
                    sudo sed -i "s|RequiresMountsFor=\(.*\)|RequiresMountsFor=\1 ${data_dir}|" "$api_service"
                    echo "  Patched: audiobook-api.service RequiresMountsFor += ${data_dir}"
                fi
            fi
        fi

        # Install tmpfiles.d configuration for runtime directories
        if [[ -f "${SCRIPT_DIR}/systemd/audiobooks-tmpfiles.conf" ]]; then
            echo -e "${BLUE}Installing tmpfiles.d configuration...${NC}"
            sudo cp "${SCRIPT_DIR}/systemd/audiobooks-tmpfiles.conf" /etc/tmpfiles.d/audiobooks.conf
            sudo chmod 644 /etc/tmpfiles.d/audiobooks.conf
            # Create the runtime directories immediately
            sudo systemd-tmpfiles --create /etc/tmpfiles.d/audiobooks.conf 2>/dev/null || {
                # Fallback: create manually if systemd-tmpfiles not available
                sudo mkdir -p /var/lib/audiobooks/.control /var/lib/audiobooks/.run /tmp/audiobook-staging
                sudo chown audiobooks:audiobooks /var/lib/audiobooks/.control /var/lib/audiobooks/.run /tmp/audiobook-staging
                sudo chmod 755 /var/lib/audiobooks/.control
                sudo chmod 775 /var/lib/audiobooks/.run /tmp/audiobook-staging
            }
            echo "  Created: /var/lib/audiobooks/.control, /var/lib/audiobooks/.run, /tmp/audiobook-staging"
        fi

        # Install Caddy maintenance page (if Caddy is installed)
        if command -v caddy &>/dev/null; then
            echo -e "${BLUE}Installing Caddy maintenance page configuration...${NC}"
            sudo mkdir -p /etc/caddy/conf.d
            # Two sites: :8084 -> native app, :8085 -> Docker app.
            # Defaults match the canonical lib/audiobook-config.sh value (8443).
            # Dual-stack hosts (e.g., QA) override AUDIOBOOKS_WEB_PORT (native)
            # to something non-colliding (e.g., 8090) so both stacks coexist.
            local native_port="${AUDIOBOOKS_WEB_PORT:-8443}"
            local docker_port="${AUDIOBOOKS_DOCKER_PORT:-8443}"
            sed -e "s|__NATIVE_PORT__|${native_port}|g" \
                -e "s|__DOCKER_PORT__|${docker_port}|g" \
                "${SCRIPT_DIR}/caddy/audiobooks.conf" | sudo tee /etc/caddy/conf.d/audiobooks.conf >/dev/null
            sudo cp -f "${SCRIPT_DIR}/caddy/maintenance.html" /etc/caddy/maintenance.html
            sudo systemctl reload caddy 2>/dev/null || true
            echo "  Installed: Caddy reverse proxy (:8084->native:${native_port}, :8085->docker:${docker_port})"
        fi

        # Enable the upgrade helper path unit (monitors for privileged operation requests)
        if [[ -f "${SYSTEMD_DIR}/audiobook-upgrade-helper.path" ]]; then
            echo -e "${BLUE}Enabling privileged operations helper...${NC}"
            sudo systemctl enable audiobook-upgrade-helper.path 2>/dev/null || true
            sudo systemctl start audiobook-upgrade-helper.path 2>/dev/null || true
            echo "  Enabled: audiobook-upgrade-helper.path"
        fi

        # Target — copy canonical file from systemd/ directory
        sudo cp "${SCRIPT_DIR}/systemd/audiobook.target" "${SYSTEMD_DIR}/audiobook.target"
        sudo chmod 644 "${SYSTEMD_DIR}/audiobook.target"

        # Reload systemd
        sudo systemctl daemon-reload

        # Enable and start services
        echo -e "${BLUE}Enabling services for automatic start at boot...${NC}"

        # Enable the target and all individual services.
        # This list MUST stay in sync with upgrade.sh::enable_new_services().
        # audiobook-shutdown-saver.service hooks halt/reboot/shutdown targets
        # and must be enabled so tmpfs staging is flushed on clean shutdown.
        sudo systemctl enable audiobook.target 2>/dev/null || true
        for svc in audiobook-api audiobook-proxy audiobook-redirect audiobook-converter audiobook-mover audiobook-downloader.timer audiobook-scheduler audiobook-enrichment.timer audiobook-stream-translate audiobook-shutdown-saver.service; do
            sudo systemctl enable "$svc" 2>/dev/null || true
        done
        # Explicit enable for streaming translation worker (belt-and-suspenders
        # alongside the loop above). The literal reference is required by
        # library/tests/test_stream_translate_wiring.py to guard against
        # orphan-script regressions (see 8.3.1 stream-translate-worker.py
        # incident where the script shipped without any wiring).
        sudo systemctl enable audiobook-stream-translate.service 2>/dev/null || true

        echo -e "${BLUE}Starting services...${NC}"
        # Start the target (which starts all wanted services)
        sudo systemctl start audiobook.target 2>/dev/null || {
            # Fallback: start individual services
            for svc in audiobook-api audiobook-proxy audiobook-converter audiobook-mover; do
                sudo systemctl start "$svc" 2>/dev/null || true
            done
        }

        # Verify services started
        echo ""
        echo -e "${BLUE}Service status:${NC}"
        local all_ok=true
        for svc in audiobook-api audiobook-proxy audiobook-converter audiobook-mover; do
            local svc_state
            svc_state=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
            if [[ "$svc_state" == "active" ]]; then
                echo -e "  $svc: ${GREEN}$svc_state${NC}"
            else
                echo -e "  $svc: ${YELLOW}$svc_state${NC}"
                all_ok=false
            fi
        done

        if [[ "$all_ok" == "true" ]]; then
            echo -e "${GREEN}All services started successfully${NC}"
        else
            echo -e "${YELLOW}Some services not yet active (may need configuration first)${NC}"
        fi

        echo ""
        echo -e "${DIM}Available services:${NC}"
        echo "  audiobook-api          - API server"
        echo "  audiobook-proxy        - HTTPS proxy server"
        echo "  audiobook-converter    - Continuous audiobook converter"
        echo "  audiobook-mover        - Moves staged files to library"
        echo "  audiobook-downloader   - Downloads new audiobooks (timer-triggered)"
    fi

    # Create /etc/profile.d script
    echo -e "${BLUE}Creating environment profile...${NC}"
    sudo tee /etc/profile.d/audiobooks.sh >/dev/null <<'EOF'
# Audiobook Library Environment
if [[ -f /opt/audiobooks/lib/audiobook-config.sh ]]; then
    source /opt/audiobooks/lib/audiobook-config.sh
fi
EOF
    sudo chmod 644 /etc/profile.d/audiobooks.sh

    echo ""
    echo -e "${GREEN}=== System Installation Complete ===${NC}"
    echo ""
    echo "Configuration: ${CONFIG_DIR}/audiobooks.conf"
    echo "Data directory: ${data_dir}"
    echo ""
    echo "Commands available:"
    echo "  audiobook-api             - Start API server"
    echo "  audiobook-web             - Start web server"
    echo "  audiobook-scan            - Scan audiobook library"
    echo "  audiobook-import          - Import to database"
    echo "  audiobook-config          - Show configuration"
    echo ""
    echo "Conversion and management:"
    echo "  audiobook-convert         - Convert AAX/AAXC to Opus"
    echo "  audiobook-download        - Download from Audible"
    echo "  audiobook-move-staged     - Move staged files to library"
    echo "  audiobook-save-staging    - Save tmpfs staging before reboot"
    echo "  audiobook-status          - Show service status"
    echo "  audiobook-start/stop      - Start/stop services"
    echo "  audiobook-enable/disable  - Enable/disable at boot"
    echo "  audiobook-monitor         - Live conversion monitor"
    echo "  audiobook-help            - Quick reference guide"
    echo ""
    echo "Access the library at: https://localhost:${WEB_PORT}"

    # Verify permissions after installation
    verify_installation_permissions "system"

    # Reconcile filesystem against install manifest. Enforce by default — the
    # acted-on items (PHANTOM_PATHS, legacy config keys, stale __pycache__)
    # are explicitly marked obsolete in the manifest. Override with
    # RECONCILE_MODE=report to audit without mutating.
    local _conf_file="${CONFIG_DIR}/audiobooks.conf"
    PROJECT_DIR="$SCRIPT_DIR" \
        LIB_DIR="$APP_DIR" \
        STATE_DIR="/var/lib/audiobooks" \
        LOG_DIR="/var/log/audiobooks" \
        CONFIG_DIR="$CONFIG_DIR" \
        CONF_FILE="$_conf_file" \
        USE_SUDO="sudo" \
        SYSTEMD_DIR="/etc/systemd/system" \
        BIN_DIR="/usr/local/bin" \
        RECONCILE_MODE="${RECONCILE_MODE:-enforce}" \
        bash "${SCRIPT_DIR}/scripts/reconcile-filesystem.sh" || true

    # Hard gate: validate release requirements AND run functional smoke probe
    # before declaring installation complete. Same contract as upgrade.sh —
    # never print success unless the system actually works. See
    # scripts/release-requirements.sh and scripts/smoke_probe.sh.
    if [[ -f "${SCRIPT_DIR}/scripts/release-requirements.sh" ]]; then
        # shellcheck source=/dev/null
        source "${SCRIPT_DIR}/scripts/release-requirements.sh"
        local _db_path="${DB_PATH:-/var/lib/audiobooks/db/audiobooks.db}"
        if ! validate_release_requirements "$_conf_file" "$_db_path" "sudo"; then
            echo ""
            echo -e "${RED}=== Release requirements NOT satisfied — install incomplete ===${NC}"
            echo -e "${YELLOW}Edit ${_conf_file} to add the missing keys, then re-run:${NC}"
            echo "  sudo bash ${SCRIPT_DIR}/scripts/smoke_probe.sh"
            return 1
        fi
    fi
    if [[ -f "${SCRIPT_DIR}/scripts/smoke_probe.sh" ]]; then
        local _new_version
        _new_version=$(tr -d '[:space:]' <"${SCRIPT_DIR}/VERSION" 2>/dev/null || echo "")
        DB_PATH="${DB_PATH:-/var/lib/audiobooks/db/audiobooks.db}" \
            USE_SUDO="sudo" \
            EXPECTED_VERSION="$_new_version" \
            bash "${SCRIPT_DIR}/scripts/smoke_probe.sh" || {
            echo ""
            echo -e "${RED}=== Post-install smoke probe FAILED — install incomplete ===${NC}"
            echo -e "${RED}Files copied and services enabled but functional check failed.${NC}"
            echo -e "${YELLOW}Review probe output above; re-run after fixing:${NC}"
            echo "  sudo bash ${SCRIPT_DIR}/scripts/smoke_probe.sh"
            return 1
        }
    fi
}

# shellcheck disable=SC2120
do_system_uninstall() {
    # Delegate to comprehensive uninstall.sh (dynamic discovery, full cleanup)
    local uninstall_script="${SCRIPT_DIR}/uninstall.sh"
    if [[ -f "$uninstall_script" ]]; then
        exec "$uninstall_script" --system "$@"
    else
        echo -e "${RED}Error: uninstall.sh not found at ${uninstall_script}${NC}"
        echo "Download it from: https://github.com/TheBoscoClub/Audiobook-Manager"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# User Installation
# -----------------------------------------------------------------------------

do_user_install() {
    local data_dir="${DATA_DIR:-$HOME/Audiobooks}"

    # Paths for user installation
    local INSTALL_PREFIX="$HOME/.local"
    local CONFIG_DIR="$HOME/.config/audiobooks"
    local LIB_DIR="${INSTALL_PREFIX}/lib/audiobooks"
    local BIN_DIR="${INSTALL_PREFIX}/bin"
    local SYSTEMD_DIR="$HOME/.config/systemd/user"
    local LOG_DIR="$HOME/.local/var/log/audiobooks"
    local STATE_DIR="$HOME/.local/var/lib/audiobooks"

    echo -e "${GREEN}=== User Installation ===${NC}"
    echo ""
    echo "Installation paths:"
    echo "  Executables:  ${BIN_DIR}/"
    echo "  Config:       ${CONFIG_DIR}/"
    echo "  Library:      ${LIB_DIR}/"
    echo "  Services:     ${SYSTEMD_DIR}/"
    echo "  Data:         ${data_dir}/"
    echo "  Database:     ${STATE_DIR}/"
    echo "  Logs:         ${LOG_DIR}/"
    echo ""

    # Show detected storage tiers
    show_detected_storage "$LIB_DIR" "$data_dir" "$STATE_DIR"

    # Warn about suboptimal storage placement
    local storage_warnings=0
    if ! warn_storage_tier "$STATE_DIR" "database"; then
        ((storage_warnings++)) || true
    fi

    if [[ $storage_warnings -gt 0 ]]; then
        echo -e "${YELLOW}Consider using --data-dir to specify a path on faster storage,${NC}"
        echo -e "${YELLOW}or configure AUDIOBOOKS_DATABASE in ~/.config/audiobooks/audiobooks.conf${NC}"
        echo -e "${YELLOW}to place the database on NVMe/SSD after installation.${NC}"
        echo ""
        read -r -p "Continue with current storage configuration? [Y/n]: " continue_choice
        if [[ "${continue_choice,,}" == "n" ]]; then
            echo -e "${YELLOW}Installation cancelled. Adjust paths and try again.${NC}"
            return 1
        fi
    fi

    # Check port availability before proceeding
    if ! check_all_ports; then
        return 1
    fi

    echo "Port configuration:"
    echo "  API:           ${API_PORT}"
    echo "  HTTPS:         ${WEB_PORT}"
    echo "  HTTP redirect: ${HTTP_REDIRECT_PORT}"
    echo ""

    # Create directories
    echo -e "${BLUE}Creating directories...${NC}"
    mkdir -p "${CONFIG_DIR}"
    mkdir -p "${CONFIG_DIR}/scripts"
    mkdir -p "${LIB_DIR}"
    mkdir -p "${BIN_DIR}"
    mkdir -p "${data_dir}/Library"
    mkdir -p "${data_dir}/Sources"
    mkdir -p "${data_dir}/Supplements"
    mkdir -p "${STATE_DIR}"
    mkdir -p "${STATE_DIR}/db"
    mkdir -p "${LOG_DIR}"
    mkdir -p "${SYSTEMD_DIR}"

    # Install library files
    echo -e "${BLUE}Installing library files...${NC}"
    cp -r "${SCRIPT_DIR}/library" "${LIB_DIR}/"
    cp -r "${SCRIPT_DIR}/lib" "${LIB_DIR}/"
    [[ -d "${SCRIPT_DIR}/converter" ]] && cp -r "${SCRIPT_DIR}/converter" "${LIB_DIR}/"
    cp "${SCRIPT_DIR}/etc/audiobooks.conf.example" "${CONFIG_DIR}/"

    # Install VERSION file
    if [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
        cp "${SCRIPT_DIR}/VERSION" "${LIB_DIR}/"
        chmod 644 "${LIB_DIR}/VERSION"
        echo "  Installed: ${LIB_DIR}/VERSION ($(cat "${SCRIPT_DIR}/VERSION"))"
    fi

    # Install reference-system snapshot for the About page
    if [[ -f "${SCRIPT_DIR}/docs/reference-system.yml" ]]; then
        cp "${SCRIPT_DIR}/docs/reference-system.yml" "${LIB_DIR}/"
        chmod 644 "${LIB_DIR}/reference-system.yml"
        echo "  Installed: ${LIB_DIR}/reference-system.yml"
    fi

    # Remove copied project venv — it contains dev-machine symlinks
    [[ -d "${LIB_DIR}/library/venv" ]] && rm -rf "${LIB_DIR}/library/venv"

    # Update version in utilities.html
    local new_version=$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null)
    if [[ -n "$new_version" ]] && [[ -f "${LIB_DIR}/library/web-v2/utilities.html" ]]; then
        echo -e "${BLUE}Setting version to v${new_version} in utilities.html...${NC}"
        sed -i "s/· v[0-9.]*\"/· v${new_version}\"/" "${LIB_DIR}/library/web-v2/utilities.html"
    fi

    # Bump HTML cachebust stamps (user install — no sudo).
    if [[ -x "${SCRIPT_DIR}/scripts/bump-cachebust.sh" ]]; then
        bash "${SCRIPT_DIR}/scripts/bump-cachebust.sh" "$(date +%s)" "${LIB_DIR}/library/web-v2" \
            || echo -e "${YELLOW}  cachebust bump had a warning (non-fatal)${NC}"
    fi

    # Create config file if it doesn't exist
    if [[ ! -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        echo -e "${BLUE}Creating configuration file...${NC}"
        cat >"${CONFIG_DIR}/audiobooks.conf" <<EOF
# Audiobook Library Configuration
# Generated by install.sh on $(date +%Y-%m-%d)

# Data directories
AUDIOBOOKS_DATA="${data_dir}"
AUDIOBOOKS_LIBRARY="\${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="\${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="\${AUDIOBOOKS_DATA}/Supplements"

# Application directories
# NOTE: AUDIOBOOKS_COVERS, AUDIOBOOKS_DATABASE, and AUDIOBOOKS_VENV are
# intentionally NOT set — they fall through to library/config.py defaults.
# Setting them here caused drift whenever config.py defaults changed.
AUDIOBOOKS_HOME="${LIB_DIR}"
AUDIOBOOKS_CERTS="${CONFIG_DIR}/certs"
AUDIOBOOKS_LOGS="${LOG_DIR}"

# Server settings
AUDIOBOOKS_API_PORT="${API_PORT}"
AUDIOBOOKS_WEB_PORT="${WEB_PORT}"
AUDIOBOOKS_HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT}"
AUDIOBOOKS_BIND_ADDRESS="0.0.0.0"
AUDIOBOOKS_HTTPS_ENABLED="true"
AUDIOBOOKS_HTTP_REDIRECT_ENABLED="true"

# Authentication (multi-user support)
# Set AUTH_ENABLED="true" to require login for remote access
# When disabled (default), admin endpoints are restricted to localhost
AUTH_ENABLED="false"

# Remote access (only needed when AUTH_ENABLED="true")
# See audiobooks.conf.example for full documentation
#AUDIOBOOKS_HOSTNAME=""
#BASE_URL=""
#CORS_ORIGIN=""

# --- Streaming translation (optional) ---
# Enables the real-time streaming translation pipeline (non-EN locales).
# If no STT backend is configured, streaming translation is disabled and only
# DeepL text-only translation runs. See docs/STREAMING-TRANSLATION.md and
# docs/SERVERLESS-OPS.md for setup.
#
# Translation backend — DeepL is currently the only supported translator.
#AUDIOBOOKS_DEEPL_API_KEY=""                        # Get one at https://www.deepl.com/pro-api
#
# STT backend — choose ONE OR MORE of the options below. Configuring multiple
# providers gives peer redundancy (if one farm is down the others keep serving)
# and load sharing (parallel workers naturally spread across farms). Endpoint
# IDs must be provisioned separately per deployment.
#
# Option 1 — RunPod serverless (pay-per-second, no minimum spend)
#AUDIOBOOKS_RUNPOD_API_KEY=""                       # https://www.runpod.io/console/user/settings
#AUDIOBOOKS_RUNPOD_STREAMING_WHISPER_ENDPOINT=""    # Streaming endpoint ID (warm pool, min_workers>=1)
#AUDIOBOOKS_RUNPOD_BACKLOG_WHISPER_ENDPOINT=""      # Backlog endpoint ID (cold pool, min_workers=0)
#
# Option 2 — Vast.ai serverless (cheaper on some GPU classes)
#AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY=""            # https://cloud.vast.ai/ → Account → API Keys
#AUDIOBOOKS_VASTAI_SERVERLESS_STREAMING_ENDPOINT="" # Vast.ai serverless endpoint ID for streaming STT
#AUDIOBOOKS_VASTAI_SERVERLESS_BACKLOG_ENDPOINT=""   # Vast.ai serverless endpoint ID for backlog STT
#
# Option 3 — Self-hosted GPU Whisper service (CUDA/ROCm/Apple Silicon)
#AUDIOBOOKS_WHISPER_GPU_HOST=""                     # e.g., 192.168.1.50 or whisper.lan
#AUDIOBOOKS_WHISPER_GPU_PORT="8080"                 # HTTP port of the whisper-gpu-service
#
# Option 4 — CPU-only faster-whisper (no GPU; slower but zero cost)
#   Currently requires code changes to wire in; see library/localization/stt/
#   for the provider interface. Treat as an advanced deployment option.
#
#AUDIOBOOKS_TTS_PROVIDER="edge-tts"                 # edge-tts (free, no GPU) | xtts (GPU) | coqui
EOF
    fi

    # Create wrapper scripts
    echo -e "${BLUE}Creating executable wrappers...${NC}"

    # Determine API entry point based on architecture choice
    local api_entry=$(get_api_entry_point)
    echo -e "${DIM}API architecture: ${API_ARCHITECTURE} (${api_entry})${NC}"

    # API server wrapper
    cat >"${BIN_DIR}/audiobook-api" <<EOF
#!/bin/bash
# Audiobook Library API Server
source "${LIB_DIR}/lib/audiobook-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/${api_entry}" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobook-api"

    # Web server wrapper
    cat >"${BIN_DIR}/audiobook-web" <<EOF
#!/bin/bash
# Audiobook Library Web Server (HTTPS)
source "${LIB_DIR}/lib/audiobook-config.sh"
exec python3 "\${AUDIOBOOKS_HOME}/library/web-v2/proxy_server.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobook-web"

    # Scanner wrapper
    cat >"${BIN_DIR}/audiobook-scan" <<EOF
#!/bin/bash
# Audiobook Library Scanner
source "${LIB_DIR}/lib/audiobook-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/scanner/scan_audiobooks.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobook-scan"

    # Database import wrapper
    cat >"${BIN_DIR}/audiobook-import" <<EOF
#!/bin/bash
# Audiobook Library Database Import
source "${LIB_DIR}/lib/audiobook-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/import_to_db.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobook-import"

    # Config viewer
    cat >"${BIN_DIR}/audiobook-config" <<EOF
#!/bin/bash
# Show audiobook library configuration
source "${LIB_DIR}/lib/audiobook-config.sh"
audiobooks_print_config
EOF
    chmod 755 "${BIN_DIR}/audiobook-config"

    # Install conversion and management scripts from scripts/ directory
    # Dev-only scripts (git hooks, dev-machine admin tools) stay in the project
    echo -e "${BLUE}Installing audiobook management scripts...${NC}"
    if [[ -d "${SCRIPT_DIR}/scripts" ]]; then
        for script in "${SCRIPT_DIR}/scripts/"*; do
            if [[ -f "$script" ]]; then
                local script_name=$(basename "$script")
                case "$script_name" in
                    install-hooks.sh | purge-users.sh | setup-email.sh) continue ;;
                esac
                # Map script names to consistent audiobook- prefix
                local target_name
                case "$script_name" in
                    convert-audiobooks-opus-parallel)
                        target_name="audiobook-convert"
                        ;;
                    move-staged-audiobooks)
                        target_name="audiobook-move-staged"
                        ;;
                    download-new-audiobooks)
                        target_name="audiobook-download"
                        ;;
                    audiobook-save-staging)
                        target_name="audiobook-save-staging"
                        ;;
                    audiobook-save-staging-auto)
                        target_name="audiobook-save-staging-auto"
                        ;;
                    audiobook-status)
                        target_name="audiobook-status"
                        ;;
                    audiobook-start)
                        target_name="audiobook-start"
                        ;;
                    audiobook-stop)
                        target_name="audiobook-stop"
                        ;;
                    audiobook-enable)
                        target_name="audiobook-enable"
                        ;;
                    audiobook-disable)
                        target_name="audiobook-disable"
                        ;;
                    audiobook-help)
                        target_name="audiobook-help"
                        ;;
                    monitor-audiobook-conversion)
                        target_name="audiobook-monitor"
                        ;;
                    copy-audiobook-metadata)
                        target_name="audiobook-copy-metadata"
                        ;;
                    audiobook-download-monitor)
                        target_name="audiobook-download-monitor"
                        ;;
                    embed-cover-art.py)
                        # Python script needs venv wrapper — create bash wrapper instead of raw copy
                        cat >"${BIN_DIR}/audiobook-embed-cover" <<PYEOF
#!/bin/bash
# Audiobook Library Cover Art Embedder
# Wrapper — uses venv Python for mutagen dependency
exec "${LIB_DIR}/library/venv/bin/python" "${LIB_DIR}/scripts/embed-cover-art.py" "\$@"
PYEOF
                        chmod 755 "${BIN_DIR}/audiobook-embed-cover"
                        echo "  Installed: audiobook-embed-cover (venv wrapper)"
                        continue
                        ;;
                    *)
                        target_name="audiobook-${script_name}"
                        ;;
                esac
                cp "$script" "${BIN_DIR}/${target_name}"
                chmod 755 "${BIN_DIR}/${target_name}"
                echo "  Installed: ${target_name}"
            fi
        done
    fi

    # Install management scripts to user's scripts directory
    echo -e "${BLUE}Installing management scripts...${NC}"
    local APP_SCRIPTS_DIR="${LIB_DIR}/scripts"
    mkdir -p "${APP_SCRIPTS_DIR}"

    # Copy upgrade and migrate scripts
    for script in upgrade.sh migrate-api.sh; do
        if [[ -f "${SCRIPT_DIR}/${script}" ]]; then
            cp "${SCRIPT_DIR}/${script}" "${APP_SCRIPTS_DIR}/"
            chmod 755 "${APP_SCRIPTS_DIR}/${script}"
            echo "  Installed: ${APP_SCRIPTS_DIR}/${script}"
        fi
    done

    # Store release info for GitHub-based upgrades
    echo -e "${BLUE}Storing release metadata...${NC}"
    if [[ -f "${SCRIPT_DIR}/.release-info" ]]; then
        cp "${SCRIPT_DIR}/.release-info" "${LIB_DIR}/"
    else
        # Create default release info
        cat >"${LIB_DIR}/.release-info" <<EOF
{
  "github_repo": "TheBoscoClub/Audiobook-Manager",
  "github_api": "https://api.github.com/repos/TheBoscoClub/Audiobook-Manager",
  "version": "$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo "unknown")",
  "install_date": "$(date -Iseconds)",
  "install_type": "user"
}
EOF
    fi
    chmod 644 "${LIB_DIR}/.release-info"

    # Create upgrade wrapper
    cat >"${BIN_DIR}/audiobook-upgrade" <<EOF
#!/bin/bash
# Audiobook Toolkit Upgrade Script
# Fetches and applies updates from GitHub releases
exec "${LIB_DIR}/scripts/upgrade.sh" --target "${LIB_DIR}" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobook-upgrade"
    echo "  Installed: audiobook-upgrade"

    # Create migrate wrapper
    cat >"${BIN_DIR}/audiobook-migrate" <<EOF
#!/bin/bash
# Audiobook Toolkit API Migration Script
# Switch between monolithic and modular API architectures
exec "${LIB_DIR}/scripts/migrate-api.sh" --target "${LIB_DIR}" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobook-migrate"
    echo "  Installed: audiobook-migrate"

    # Setup Python virtual environment
    # Use system Python explicitly — pyenv shims create symlinks into /home/
    # Always recreate: project venv was removed above, and fresh venv ensures
    # correct symlinks for this machine's Python installation
    echo -e "${BLUE}Setting up Python virtual environment (system Python)...${NC}"
    [[ -d "${LIB_DIR}/library/venv" ]] && rm -rf "${LIB_DIR}/library/venv"
    local sys_python="/usr/bin/python3"
    [[ -x /usr/bin/python3.14 ]] && sys_python="/usr/bin/python3.14"
    "$sys_python" -m venv "${LIB_DIR}/library/venv"
    # Install all dependencies from requirements.txt
    if [[ -f "${LIB_DIR}/library/requirements.txt" ]]; then
        echo -e "${BLUE}Installing Python dependencies from requirements.txt...${NC}"
        "${LIB_DIR}/library/venv/bin/pip" install --quiet \
            -r "${LIB_DIR}/library/requirements.txt"
    else
        echo -e "${YELLOW}Warning: requirements.txt not found, installing Flask only${NC}"
        "${LIB_DIR}/library/venv/bin/pip" install --quiet Flask
    fi

    # Isolated venv for audible-cli (avoids httpx version conflict with main app)
    local audible_venv="${HOME}/.local/share/audiobooks/audible-venv"
    echo -e "${BLUE}Setting up audible-cli virtual environment...${NC}"
    [[ -d "$audible_venv" ]] && rm -rf "$audible_venv"
    "$sys_python" -m venv "$audible_venv"
    "$audible_venv/bin/pip" install --quiet 'audible-cli>=0.3.2'
    echo -e "${GREEN}  audible-cli installed in isolated venv${NC}"

    # Generate SSL certificate if needed
    local CERT_DIR="${CONFIG_DIR}/certs"
    if [[ ! -f "${CERT_DIR}/server.crt" ]]; then
        echo -e "${BLUE}Generating SSL certificate (3-year validity)...${NC}"
        mkdir -p "${CERT_DIR}"
        openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
            -nodes -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.crt" \
            -subj "/CN=localhost/O=Audiobooks/C=US" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
            2>/dev/null
        chmod 600 "${CERT_DIR}/server.key"
        chmod 644 "${CERT_DIR}/server.crt"
        echo -e "${GREEN}  Certificate generated:${NC}"
        openssl x509 -in "${CERT_DIR}/server.crt" -noout -dates -subject | sed 's/^/    /'
    fi

    # Install systemd user services
    if [[ "$INSTALL_SERVICES" == "true" ]]; then
        echo -e "${BLUE}Installing systemd user services...${NC}"

        # API service
        cat >"${SYSTEMD_DIR}/audiobook-api.service" <<EOF
[Unit]
Description=Audiobooks Library API Server
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=default.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=${LIB_DIR}
Environment=AUDIOBOOKS_DATA=${data_dir}
Environment=AUDIOBOOKS_LIBRARY=${data_dir}/Library
Environment=AUDIOBOOKS_SOURCES=${data_dir}/Sources
Environment=AUDIOBOOKS_SUPPLEMENTS=${data_dir}/Supplements
Environment=AUDIOBOOKS_CERTS=${CONFIG_DIR}/certs
Environment=AUDIOBOOKS_LOGS=${LOG_DIR}
Environment=AUDIOBOOKS_API_PORT=${API_PORT}
Environment=AUDIOBOOKS_WEB_PORT=${WEB_PORT}
Environment=AUDIOBOOKS_HTTP_REDIRECT_PORT=${HTTP_REDIRECT_PORT}
# AUDIOBOOKS_DATABASE and AUDIOBOOKS_COVERS intentionally omitted — fall
# through to library/config.py defaults to avoid drift.

ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:${API_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobook-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

        # Web service
        cat >"${SYSTEMD_DIR}/audiobook-web.service" <<EOF
[Unit]
Description=Audiobooks Library Web Server (HTTPS)
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
After=audiobook-api.service
Wants=audiobook-api.service

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=${LIB_DIR}
Environment=AUDIOBOOKS_WEB_PORT=${WEB_PORT}
Environment=AUDIOBOOKS_HTTP_REDIRECT_PORT=${HTTP_REDIRECT_PORT}
Environment=AUDIOBOOKS_CERTS=${CONFIG_DIR}/certs

ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:${WEB_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobook-web
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

        # Target
        cat >"${SYSTEMD_DIR}/audiobook.target" <<EOF
[Unit]
Description=Audiobooks Library Services
Documentation=https://github.com/TheBoscoClub/Audiobook-Manager
Wants=audiobook-api.service audiobook-web.service

[Install]
WantedBy=default.target
EOF

        # Reload systemd
        systemctl --user daemon-reload

        # Enable and start services by default
        echo -e "${BLUE}Enabling and starting user services...${NC}"
        systemctl --user enable audiobook.target 2>/dev/null || true
        systemctl --user enable audiobook-api.service audiobook-web.service 2>/dev/null || true

        # Start services
        systemctl --user start audiobook.target 2>/dev/null || {
            # Fallback: start individual services
            systemctl --user start audiobook-api.service 2>/dev/null || true
            systemctl --user start audiobook-web.service 2>/dev/null || true
        }

        echo ""
        echo -e "${GREEN}Services enabled and started.${NC}"
        echo ""
        echo -e "${YELLOW}To enable services to start at boot (without login):${NC}"
        echo "  loginctl enable-linger \$USER"
    fi

    # Check if ~/.local/bin is in PATH
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo ""
        echo -e "${YELLOW}NOTE: Add ~/.local/bin to your PATH:${NC}"
        echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
        echo "  # or for zsh:"
        echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
    fi

    echo ""
    echo -e "${GREEN}=== User Installation Complete ===${NC}"
    echo ""
    echo "Configuration: ${CONFIG_DIR}/audiobooks.conf"
    echo "Data directory: ${data_dir}"
    echo "Logs: ${LOG_DIR}"
    echo ""
    echo "Commands available:"
    echo "  audiobook-api             - Start API server"
    echo "  audiobook-web             - Start web server"
    echo "  audiobook-scan            - Scan audiobook library"
    echo "  audiobook-import          - Import to database"
    echo "  audiobook-config          - Show configuration"
    echo ""
    echo "Conversion and management:"
    echo "  audiobook-convert         - Convert AAX/AAXC to Opus"
    echo "  audiobook-download        - Download from Audible"
    echo "  audiobook-move-staged     - Move staged files to library"
    echo "  audiobook-save-staging    - Save tmpfs staging before reboot"
    echo "  audiobook-status          - Show service status"
    echo "  audiobook-start/stop      - Start/stop services"
    echo "  audiobook-enable/disable  - Enable/disable at boot"
    echo "  audiobook-monitor         - Live conversion monitor"
    echo "  audiobook-help            - Quick reference guide"
    echo ""
    echo "Service management:"
    echo "  systemctl --user status audiobook.target"
    echo "  systemctl --user restart audiobook.target"
    echo "  journalctl --user -u audiobook-converter -f"
    echo ""
    echo "Access the library at: https://localhost:${WEB_PORT}"
    echo ""
    echo "NOTE: Your browser will show a security warning for the self-signed"
    echo "certificate. Click 'Advanced' -> 'Proceed to localhost' to continue."

    # Verify permissions after installation
    verify_installation_permissions "user"

    # Reconcile filesystem against install manifest. Enforce by default — the
    # acted-on items (PHANTOM_PATHS, legacy config keys, stale __pycache__)
    # are explicitly marked obsolete in the manifest. Override with
    # RECONCILE_MODE=report to audit without mutating.
    local _conf_file="${CONFIG_DIR}/audiobooks.conf"
    PROJECT_DIR="$SCRIPT_DIR" \
        LIB_DIR="$LIB_DIR" \
        STATE_DIR="$STATE_DIR" \
        LOG_DIR="$LOG_DIR" \
        CONFIG_DIR="$CONFIG_DIR" \
        CONF_FILE="$_conf_file" \
        USE_SUDO="" \
        SYSTEMD_DIR="${HOME}/.config/systemd/user" \
        BIN_DIR="${HOME}/.local/bin" \
        RECONCILE_MODE="${RECONCILE_MODE:-enforce}" \
        bash "${SCRIPT_DIR}/scripts/reconcile-filesystem.sh" || true
}

# shellcheck disable=SC2120
do_user_uninstall() {
    # Delegate to comprehensive uninstall.sh (dynamic discovery, full cleanup)
    local uninstall_script="${SCRIPT_DIR}/uninstall.sh"
    if [[ -f "$uninstall_script" ]]; then
        exec "$uninstall_script" --user "$@"
    else
        echo -e "${RED}Error: uninstall.sh not found at ${uninstall_script}${NC}"
        echo "Download it from: https://github.com/TheBoscoClub/Audiobook-Manager"
        return 1
    fi
}

# -----------------------------------------------------------------------------
# Fresh Install (reinstall preserving audiobook library)
# -----------------------------------------------------------------------------

do_fresh_install() {
    local install_type="$1" # "system" or "user"

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}Fresh Install — Reinstall with Library Preservation${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    # Determine config file location based on install type
    local config_file
    if [[ "$install_type" == "system" ]]; then
        config_file="/etc/audiobooks/audiobooks.conf"
    else
        config_file="$HOME/.config/audiobooks/audiobooks.conf"
    fi

    # --- Step 1: Detect existing installation ---
    local existing_install=false
    if [[ "$install_type" == "system" ]]; then
        if [[ -d "/opt/audiobooks" ]] || [[ -f "$config_file" ]]; then
            existing_install=true
        fi
    else
        if [[ -d "$HOME/.local/share/audiobooks" ]] || [[ -f "$config_file" ]]; then
            existing_install=true
        fi
    fi

    if [[ "$existing_install" == "false" ]]; then
        echo -e "${YELLOW}No existing installation detected.${NC}"
        echo "Running a normal fresh install instead."
        echo ""
        if [[ "$install_type" == "system" ]]; then
            do_system_install
        else
            do_user_install
        fi
        return $?
    fi

    echo -e "${GREEN}Existing installation detected.${NC}"
    echo ""

    # --- Step 2: Capture settings from existing audiobooks.conf ---
    echo -e "${BLUE}Capturing settings from ${config_file}...${NC}"

    # Settings to preserve — used only for header display and port/data-dir
    # hints passed into do_system_install. The full old config is merged back
    # after reinstall in Step 5, so these locals are *not* the source of truth
    # for the restored config.
    local saved_AUDIOBOOKS_DATA=""
    local saved_AUDIOBOOKS_API_PORT=""
    local saved_AUDIOBOOKS_WEB_PORT=""
    local saved_AUDIOBOOKS_HTTP_REDIRECT_PORT=""

    if [[ -f "$config_file" ]]; then
        # Source the config in a subshell to extract values safely
        while IFS='=' read -r key value; do
            # Skip comments and empty lines
            [[ "$key" =~ ^[[:space:]]*# ]] && continue
            [[ -z "$key" ]] && continue
            # Strip leading/trailing whitespace and quotes
            key=$(echo "$key" | xargs)
            value=$(echo "$value" | sed 's/^["'"'"']//; s/["'"'"']$//' | xargs)
            case "$key" in
                AUDIOBOOKS_DATA) saved_AUDIOBOOKS_DATA="$value" ;;
                AUDIOBOOKS_API_PORT) saved_AUDIOBOOKS_API_PORT="$value" ;;
                AUDIOBOOKS_WEB_PORT) saved_AUDIOBOOKS_WEB_PORT="$value" ;;
                AUDIOBOOKS_HTTP_REDIRECT_PORT) saved_AUDIOBOOKS_HTTP_REDIRECT_PORT="$value" ;;
            esac
        done <"$config_file"
    fi

    # Display the install-level hints we captured. The full config (all keys)
    # will be merged back automatically in Step 5 — no need to enumerate here.
    echo ""
    echo -e "${BOLD}Install-level settings (data dir + ports will be reused):${NC}"
    [[ -n "$saved_AUDIOBOOKS_DATA" ]] && echo -e "  AUDIOBOOKS_DATA              = ${CYAN}${saved_AUDIOBOOKS_DATA}${NC}"
    [[ -n "$saved_AUDIOBOOKS_API_PORT" ]] && echo -e "  AUDIOBOOKS_API_PORT          = ${CYAN}${saved_AUDIOBOOKS_API_PORT}${NC}"
    [[ -n "$saved_AUDIOBOOKS_WEB_PORT" ]] && echo -e "  AUDIOBOOKS_WEB_PORT          = ${CYAN}${saved_AUDIOBOOKS_WEB_PORT}${NC}"
    [[ -n "$saved_AUDIOBOOKS_HTTP_REDIRECT_PORT" ]] && echo -e "  AUDIOBOOKS_HTTP_REDIRECT_PORT= ${CYAN}${saved_AUDIOBOOKS_HTTP_REDIRECT_PORT}${NC}"
    echo ""
    echo -e "${BOLD}State files and the full config will be restored after reinstall.${NC}"
    echo ""

    # Apply captured ports to global variables so the fresh install uses them
    [[ -n "$saved_AUDIOBOOKS_API_PORT" ]] && API_PORT="$saved_AUDIOBOOKS_API_PORT"
    [[ -n "$saved_AUDIOBOOKS_WEB_PORT" ]] && WEB_PORT="$saved_AUDIOBOOKS_WEB_PORT"
    [[ -n "$saved_AUDIOBOOKS_HTTP_REDIRECT_PORT" ]] && HTTP_REDIRECT_PORT="$saved_AUDIOBOOKS_HTTP_REDIRECT_PORT"

    # Apply captured data dir so the fresh install uses it
    [[ -n "$saved_AUDIOBOOKS_DATA" ]] && DATA_DIR="$saved_AUDIOBOOKS_DATA"

    # --- Step 2b: Back up state files belt-and-suspenders ---
    # As of v8.1, uninstall.sh --keep-data ALSO preserves DB/auth/covers/conf
    # via stage_preserved_state. We still stage here for two reasons:
    #   1) Defense in depth — if uninstall.sh stage/restore is interrupted
    #      (ctrl-C, disk full), we still have our copy to restore from.
    #   2) The config merge in Step 5 needs the OLD conf as a reference even
    #      though install.sh writes a FRESH default conf over it.
    # Paths below use the CANONICAL locations from etc/audiobooks.conf.example:
    #   AUDIOBOOKS_DATABASE=/var/lib/audiobooks/db/audiobooks.db
    #   AUTH_DATABASE=/var/lib/audiobooks/auth.db  (NOT in db/ subdir)
    #   AUTH_KEY_FILE=/etc/audiobooks/auth.key
    #   AUDIOBOOKS_COVERS=/var/lib/audiobooks/covers
    local fresh_backup_dir
    fresh_backup_dir=$(mktemp -d -t audiobooks-fresh-XXXXXX)
    # Ensure staging dir is cleaned up even on early return (uninstall failure,
    # reinstall failure, etc.). Removed explicitly at the end of Step 5 on the
    # happy path; the trap is idempotent.
    # shellcheck disable=SC2064  # intentional early expansion of $fresh_backup_dir
    trap "rm -rf '$fresh_backup_dir' 2>/dev/null || true" RETURN
    echo -e "${BLUE}Staging state files to ${fresh_backup_dir}...${NC}"

    local state_src conf_src_dir use_sudo_fresh=""
    if [[ "$install_type" == "system" ]]; then
        state_src="/var/lib/audiobooks"
        conf_src_dir="/etc/audiobooks"
        use_sudo_fresh="sudo"
    else
        state_src="${HOME}/.local/state/audiobooks"
        conf_src_dir="${HOME}/.config/audiobooks"
    fi

    _stage_if_exists() {
        local src="$1" label="$2"
        [[ -e "$src" ]] || return 1
        local dest="${fresh_backup_dir}/${label}"
        $use_sudo_fresh cp -a "$src" "$dest" 2>/dev/null || return 1
        $use_sudo_fresh chmod -R u+rwX "$dest" 2>/dev/null || true
        echo "  staged: ${label} (from ${src})"
        return 0
    }

    local staged_main_db="false" staged_auth_db="false" staged_auth_key="false"
    local staged_covers="false" staged_full_conf="false"
    _stage_if_exists "${state_src}/db/audiobooks.db" "audiobooks.db" && staged_main_db="true"
    _stage_if_exists "${state_src}/auth.db" "auth.db" && staged_auth_db="true"
    # Fallback: legacy location (some pre-v8 installs had auth.db under db/)
    if [[ "$staged_auth_db" == "false" ]]; then
        _stage_if_exists "${state_src}/db/auth.db" "auth.db" && staged_auth_db="true"
    fi
    _stage_if_exists "${conf_src_dir}/auth.key" "auth.key" && staged_auth_key="true"
    _stage_if_exists "${state_src}/covers" "covers" && staged_covers="true"
    _stage_if_exists "$config_file" "audiobooks.conf" && staged_full_conf="true"

    unset -f _stage_if_exists

    # Success flags for the restore phase (set in Step 5)
    local restored_main_db="false" restored_auth_db="false"
    local restored_auth_key="false" restored_covers="false" restored_full_conf="false"

    # --- Step 3: Uninstall (keep data) ---
    echo -e "${YELLOW}Uninstalling existing application (keeping audiobook data)...${NC}"
    echo ""

    local uninstall_script="${SCRIPT_DIR}/uninstall.sh"
    if [[ ! -f "$uninstall_script" ]]; then
        echo -e "${RED}Error: uninstall.sh not found at ${uninstall_script}${NC}"
        echo "Cannot proceed with fresh install without the uninstall script."
        return 1
    fi

    if [[ "$install_type" == "system" ]]; then
        "$uninstall_script" --system --keep-data --force
    else
        "$uninstall_script" --user --keep-data --force
    fi

    local uninstall_rc=$?
    if [[ $uninstall_rc -ne 0 ]]; then
        echo -e "${RED}Uninstall failed (exit code $uninstall_rc). Aborting fresh install.${NC}"
        return 1
    fi

    echo ""
    echo -e "${GREEN}Uninstall complete. Audiobook data preserved.${NC}"
    echo ""

    # --- Step 3b: Force install.sh to write a FRESH default conf ---
    # uninstall.sh --keep-data now restores audiobooks.conf via
    # stage_preserved_state. That's correct for standalone uninstall, but in
    # fresh-install we WANT install.sh to write the new default conf so new
    # v8.1+ keys get introduced. Step 5 merges the user's old overrides back
    # on top. We use the fresh_backup_dir copy as the merge source, which is
    # why that staging remains in Step 2b.
    if [[ -f "$config_file" ]]; then
        echo -e "${BLUE}Clearing restored config so install.sh writes fresh defaults...${NC}"
        $use_sudo_fresh rm -f "$config_file"
    fi

    # --- Step 4: Run fresh install ---
    echo -e "${BLUE}Running fresh install...${NC}"
    echo ""

    if [[ "$install_type" == "system" ]]; then
        do_system_install
    else
        do_user_install
    fi

    local install_rc=$?
    if [[ $install_rc -ne 0 ]]; then
        echo -e "${RED}Fresh install failed (exit code $install_rc).${NC}"
        return 1
    fi

    # --- Step 5: Restore staged state files + merge full old config ---
    echo ""
    echo -e "${BLUE}Restoring preserved state and merging configuration...${NC}"

    if [[ "$install_type" == "system" ]]; then
        config_file="/etc/audiobooks/audiobooks.conf"
    else
        config_file="$HOME/.config/audiobooks/audiobooks.conf"
    fi

    # Restore state files (DBs, covers, auth key) back over the fresh install.
    # The reinstall created empty placeholders — these overwrites are safe.
    _restore_if_staged() {
        local label="$1" dest="$2" owner="$3"
        local staged="${fresh_backup_dir}/${label}"
        [[ -e "$staged" ]] || return 1
        $use_sudo_fresh mkdir -p "$(dirname "$dest")"
        $use_sudo_fresh rm -rf "$dest"
        $use_sudo_fresh cp -a "$staged" "$dest"
        [[ -n "$owner" ]] && $use_sudo_fresh chown -R "$owner" "$dest" 2>/dev/null || true
        echo -e "  restored: ${CYAN}${dest}${NC}"
        return 0
    }

    local state_owner=""
    [[ "$install_type" == "system" ]] && state_owner="audiobooks:audiobooks"

    _restore_if_staged audiobooks.db "${state_src}/db/audiobooks.db" "$state_owner" && restored_main_db="true"
    _restore_if_staged auth.db "${state_src}/auth.db" "$state_owner" && restored_auth_db="true"
    _restore_if_staged auth.key "${conf_src_dir}/auth.key" "$state_owner" && restored_auth_key="true"
    _restore_if_staged covers "${state_src}/covers" "$state_owner" && restored_covers="true"
    [[ "$restored_auth_key" == "true" ]] && $use_sudo_fresh chmod 600 "${conf_src_dir}/auth.key" 2>/dev/null || true

    unset -f _restore_if_staged

    # Merge the full old audiobooks.conf into the new one. Any key present in
    # the old config is carried over verbatim; new keys introduced by this
    # release are kept at their fresh defaults. This replaces the pre-v8.1
    # behavior of only preserving 13 hardcoded keys, which silently dropped
    # everything else.
    if [[ "$staged_full_conf" == "true" && -f "${fresh_backup_dir}/audiobooks.conf" ]]; then
        local merged_conf
        merged_conf=$(mktemp)
        cp "$config_file" "$merged_conf.new" 2>/dev/null || $use_sudo_fresh cat "$config_file" >"$merged_conf.new"

        # For every KEY=... line in the old config, replace or append in the new one.
        while IFS= read -r line; do
            [[ "$line" =~ ^[[:space:]]*# ]] && continue
            [[ -z "${line// /}" ]] && continue
            [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]] || continue
            local k="${BASH_REMATCH[1]}"
            # Skip keys we know are drift (handled by migration 002)
            case "$k" in
                AUDIOBOOKS_COVERS | AUDIOBOOKS_DATABASE | AUDIOBOOKS_VENV) continue ;;
            esac
            if grep -q "^${k}=" "$merged_conf.new" 2>/dev/null; then
                sed -i "s|^${k}=.*|${line}|" "$merged_conf.new"
            else
                echo "$line" >>"$merged_conf.new"
            fi
        done <"${fresh_backup_dir}/audiobooks.conf"

        $use_sudo_fresh cp "$merged_conf.new" "$config_file"
        [[ -n "$state_owner" ]] && $use_sudo_fresh chown root:audiobooks "$config_file" 2>/dev/null || true
        $use_sudo_fresh chmod 640 "$config_file" 2>/dev/null || true
        rm -f "$merged_conf" "$merged_conf.new"
        restored_full_conf="true"
        echo -e "  merged: ${CYAN}${config_file}${NC} (old keys carried over, drift keys dropped)"
    fi

    # Staging dir is removed by the RETURN trap installed in Step 2b

    # --- Step 6: Trigger library scan only if the main DB could not be restored ---
    if [[ "$restored_main_db" == "true" ]]; then
        echo ""
        echo -e "${GREEN}Main database restored — skipping library rescan.${NC}"
        echo ""
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}${BOLD}Fresh install complete!${NC}"
        echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo "Restored: main DB$([[ "$restored_auth_db" == "true" ]] && echo ", auth DB")$([[ "$restored_auth_key" == "true" ]] && echo ", auth key")$([[ "$restored_covers" == "true" ]] && echo ", cover cache")"
        echo "Configuration: ${config_file}"
        echo ""
        return 0
    fi

    echo ""
    echo -e "${BLUE}Main DB was not preserved — scanning library to reindex audiobooks...${NC}"

    local scan_cmd=""
    if [[ "$install_type" == "system" ]]; then
        scan_cmd="/usr/local/bin/audiobook-scan"
    else
        scan_cmd="$HOME/.local/bin/audiobook-scan"
    fi

    if [[ -x "$scan_cmd" ]]; then
        if [[ "$install_type" == "system" ]]; then
            sudo -u audiobooks "$scan_cmd" 2>&1 || {
                echo -e "${YELLOW}Library scan returned non-zero. You can run it manually later:${NC}"
                echo "  $scan_cmd"
            }
        else
            "$scan_cmd" 2>&1 || {
                echo -e "${YELLOW}Library scan returned non-zero. You can run it manually later:${NC}"
                echo "  $scan_cmd"
            }
        fi
        echo -e "${GREEN}Library scan complete.${NC}"
    else
        echo -e "${YELLOW}Scanner not found at ${scan_cmd}.${NC}"
        echo "Run the library scan manually after installation:"
        echo "  audiobook-scan"
    fi

    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}${BOLD}Fresh install complete!${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Your audiobook library and settings have been preserved."
    echo "Configuration: ${config_file}"
    echo ""
}

# -----------------------------------------------------------------------------
# Parse Command Line Arguments
# -----------------------------------------------------------------------------

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
        --data-dir)
            DATA_DIR="$2"
            shift 2
            ;;
        --modular)
            API_ARCHITECTURE="modular"
            shift
            ;;
        --monolithic)
            API_ARCHITECTURE="monolithic"
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --no-services)
            INSTALL_SERVICES=false
            shift
            ;;
        --fresh-install | -fi)
            FRESH_INSTALL=true
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
# Main Script
# -----------------------------------------------------------------------------

# Pre-flight: verify system packages are installed
check_system_dependencies

# Handle command-line mode selection
if [[ -n "$INSTALL_MODE" ]]; then
    if [[ "$INSTALL_MODE" == "system" ]]; then
        if ! check_sudo_access; then
            show_sudo_error
            exit 1
        fi
        if ! verify_sudo; then
            show_sudo_error
            exit 1
        fi
        if [[ "$FRESH_INSTALL" == "true" ]]; then
            do_fresh_install "system"
        elif [[ "$UNINSTALL" == "true" ]]; then
            do_system_uninstall
        else
            do_system_install
        fi
    elif [[ "$INSTALL_MODE" == "user" ]]; then
        if [[ "$FRESH_INSTALL" == "true" ]]; then
            do_fresh_install "user"
        elif [[ "$UNINSTALL" == "true" ]]; then
            do_user_uninstall
        else
            do_user_install
        fi
    fi
    exit 0
fi

# Fresh install requires --system or --user
if [[ "$FRESH_INSTALL" == "true" ]]; then
    echo -e "${RED}Error: --fresh-install requires --system or --user to be specified.${NC}"
    echo "Example: ./install.sh --system --fresh-install"
    exit 1
fi

# Interactive menu loop
while true; do
    print_header
    print_menu

    read -r -p "Enter your choice [1-3]: " choice
    echo ""

    case "$choice" in
        1)
            # System installation
            echo -e "${BLUE}Checking sudo privileges...${NC}"
            echo ""

            if ! check_sudo_access; then
                show_sudo_error
                wait_for_keypress
                continue
            fi

            if ! verify_sudo; then
                show_sudo_error
                wait_for_keypress
                continue
            fi

            # Prompt for data directory if not set
            if [[ -z "$DATA_DIR" ]]; then
                echo ""
                read -r -p "Audiobook data directory [/srv/audiobooks]: " input_dir
                DATA_DIR="${input_dir:-/srv/audiobooks}"
            fi

            # Prompt for API architecture choice
            if [[ "$UNINSTALL" != "true" ]]; then
                prompt_architecture_choice
            fi

            if [[ "$UNINSTALL" == "true" ]]; then
                do_system_uninstall
            else
                do_system_install
            fi

            echo ""
            wait_for_keypress
            exit 0
            ;;
        2)
            # User installation
            # Prompt for data directory if not set
            if [[ -z "$DATA_DIR" ]]; then
                echo ""
                read -r -p "Audiobook data directory [$HOME/Audiobooks]: " input_dir
                DATA_DIR="${input_dir:-$HOME/Audiobooks}"
            fi

            # Prompt for API architecture choice
            if [[ "$UNINSTALL" != "true" ]]; then
                prompt_architecture_choice
            fi

            if [[ "$UNINSTALL" == "true" ]]; then
                do_user_uninstall
            else
                do_user_install
            fi

            echo ""
            wait_for_keypress
            exit 0
            ;;
        3)
            # Exit
            echo -e "${GREEN}Exiting installer.${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice. Please enter 1, 2, or 3.${NC}"
            sleep 1
            ;;
    esac
done
