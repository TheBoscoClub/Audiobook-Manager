#!/bin/bash
# =============================================================================
# Audiobook Library - Unified Installation Script
# =============================================================================
# Interactive installer that supports both system-wide and user installations.
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
NC='\033[0m'

# Script directory (source)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default options
INSTALL_MODE=""
DATA_DIR=""
INSTALL_SERVICES=true
UNINSTALL=false
API_ARCHITECTURE="monolithic"  # monolithic (api.py) or modular (api_modular/)

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

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
    read -r -n 1 -s -r
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
                y|yes)
                    DELETE_LIBRARY=true
                    echo -e "  ${RED}→ Will delete converted audiobooks${NC}"
                    break
                    ;;
                n|no|"")
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
                y|yes)
                    DELETE_SOURCES=true
                    echo -e "  ${RED}→ Will delete source files${NC}"
                    break
                    ;;
                n|no|"")
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
                y|yes)
                    DELETE_SUPPLEMENTS=true
                    echo -e "  ${RED}→ Will delete supplemental PDFs${NC}"
                    break
                    ;;
                n|no|"")
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
                y|yes)
                    DELETE_CONFIG=true
                    echo -e "  ${RED}→ Will delete configuration${NC}"
                    break
                    ;;
                n|no|"")
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
    if [[ "$DELETE_LIBRARY" == "true" ]] || [[ "$DELETE_SOURCES" == "true" ]] || \
       [[ "$DELETE_SUPPLEMENTS" == "true" ]] || [[ "$DELETE_CONFIG" == "true" ]]; then
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
                y|yes)
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
                n|no|"")
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
    local install_type="$1"  # "system" or "user"
    local issues_found=0

    echo ""
    echo -e "${BLUE}Verifying installation permissions...${NC}"

    if [[ "$install_type" == "system" ]]; then
        local APP_DIR="/opt/audiobooks"
        local SERVICE_USER="audiobooks"
        local SERVICE_GROUP="audiobooks"

        # Check directory permissions (should be 755)
        echo -n "  Checking directory permissions... "
        local bad_dirs=$(find "$APP_DIR" -type d -perm 700 2>/dev/null | wc -l)
        if [[ "$bad_dirs" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_dirs directories${NC}"
            sudo find "$APP_DIR" -type d -perm 700 -exec chmod 755 {} \;
            ((issues_found++))
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Check Python file permissions (should be 644, readable)
        echo -n "  Checking .py file permissions... "
        local bad_py=$(find "$APP_DIR" -name "*.py" \( -perm 600 -o -perm 700 -o -perm 711 \) 2>/dev/null | wc -l)
        if [[ "$bad_py" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_py files${NC}"
            sudo find "$APP_DIR" -name "*.py" \( -perm 600 -o -perm 700 -o -perm 711 \) -exec chmod 644 {} \;
            ((issues_found++))
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Check HTML/CSS/JS permissions (should be 644)
        echo -n "  Checking web file permissions... "
        local bad_web=$(find "$APP_DIR" \( -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 \) 2>/dev/null | wc -l)
        if [[ "$bad_web" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_web files${NC}"
            sudo find "$APP_DIR" \( -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 \) -exec chmod 644 {} \;
            ((issues_found++))
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
                    ((group_issues++))
                fi
            fi
        done
        if [[ "$group_issues" -gt 0 ]]; then
            echo -e "${YELLOW}fixed $group_issues directories${NC}"
            ((issues_found++))
        else
            echo -e "${GREEN}OK${NC}"
        fi

        # Verify no symlinks point to project source directory
        echo -n "  Checking for project source dependencies... "
        local project_links=$(find /usr/local/bin -name "audiobooks-*" -type l -exec readlink {} \; 2>/dev/null | grep -c "ClaudeCodeProjects" || true)
        if [[ "$project_links" -gt 0 ]]; then
            echo -e "${RED}WARNING: $project_links binaries link to project source!${NC}"
            echo -e "         Production should be independent of source repo."
            ((issues_found++))
        else
            echo -e "${GREEN}OK (independent)${NC}"
        fi

    else
        # User installation checks
        local APP_DIR="$HOME/.local/share/audiobooks"

        echo -n "  Checking directory permissions... "
        local bad_dirs=$(find "$APP_DIR" -type d -perm 700 2>/dev/null | wc -l)
        if [[ "$bad_dirs" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_dirs directories${NC}"
            find "$APP_DIR" -type d -perm 700 -exec chmod 755 {} \;
            ((issues_found++))
        else
            echo -e "${GREEN}OK${NC}"
        fi

        echo -n "  Checking file permissions... "
        local bad_files=$(find "$APP_DIR" -type f \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) -perm 600 2>/dev/null | wc -l)
        if [[ "$bad_files" -gt 0 ]]; then
            echo -e "${YELLOW}fixing $bad_files files${NC}"
            find "$APP_DIR" -type f \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) -perm 600 -exec chmod 644 {} \;
            ((issues_found++))
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
DEFAULT_HTTP_REDIRECT_PORT=8081

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
            return 1  # Port in use
        fi
        return 0  # Port available
    fi

    # Fallback to ss
    if command -v ss >/dev/null 2>&1; then
        if ss -tlnH "sport = :$port" 2>/dev/null | grep -q .; then
            return 1  # Port in use
        fi
        return 0  # Port available
    fi

    # Fallback to netstat
    if command -v netstat >/dev/null 2>&1; then
        if netstat -tlnp 2>/dev/null | grep -q ":$port "; then
            return 1  # Port in use
        fi
        return 0  # Port available
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

    # Paths for system installation
    local INSTALL_PREFIX="/usr/local"
    local CONFIG_DIR="/etc/audiobooks"
    local APP_DIR="/opt/audiobooks"        # Canonical application location
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
    echo ""

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
    sudo mkdir -p "${CONFIG_DIR}"
    sudo mkdir -p "${APP_DIR}"
    sudo mkdir -p "${data_dir}/Library"
    sudo mkdir -p "${data_dir}/Sources"
    sudo mkdir -p "${data_dir}/Supplements"
    sudo mkdir -p "/var/lib/audiobooks"
    sudo mkdir -p "/var/log/audiobooks"

    # Install library files
    echo -e "${BLUE}Installing library files...${NC}"
    sudo cp -r "${SCRIPT_DIR}/library" "${APP_DIR}/"
    sudo cp -r "${SCRIPT_DIR}/lib" "${APP_DIR}/"
    [[ -d "${SCRIPT_DIR}/converter" ]] && sudo cp -r "${SCRIPT_DIR}/converter" "${APP_DIR}/"

    # Create backward-compat symlink for any scripts that reference old path
    sudo mkdir -p "/usr/local/lib"
    sudo ln -sfn "${APP_DIR}/lib" "/usr/local/lib/audiobooks"
    sudo cp "${SCRIPT_DIR}/etc/audiobooks.conf.example" "${CONFIG_DIR}/"

    # Create config file if it doesn't exist
    if [[ ! -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        echo -e "${BLUE}Creating configuration file...${NC}"
        sudo tee "${CONFIG_DIR}/audiobooks.conf" > /dev/null << EOF
# Audiobook Library Configuration
# Generated by install.sh on $(date +%Y-%m-%d)

# Data directories
AUDIOBOOKS_DATA="${data_dir}"
AUDIOBOOKS_LIBRARY="\${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="\${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="\${AUDIOBOOKS_DATA}/Supplements"

# Application directories
AUDIOBOOKS_HOME="${APP_DIR}"
AUDIOBOOKS_DATABASE="/var/lib/audiobooks/audiobooks.db"
AUDIOBOOKS_COVERS="\${AUDIOBOOKS_HOME}/library/web-v2/covers"
AUDIOBOOKS_CERTS="\${AUDIOBOOKS_HOME}/library/certs"
AUDIOBOOKS_LOGS="/var/log/audiobooks"
AUDIOBOOKS_VENV="\${AUDIOBOOKS_HOME}/library/venv"

# Server settings
AUDIOBOOKS_API_PORT="${API_PORT}"
AUDIOBOOKS_WEB_PORT="${WEB_PORT}"
AUDIOBOOKS_HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT}"
AUDIOBOOKS_BIND_ADDRESS="0.0.0.0"
AUDIOBOOKS_HTTPS_ENABLED="true"
AUDIOBOOKS_HTTP_REDIRECT_ENABLED="true"
EOF
    fi

    # Create wrapper scripts
    echo -e "${BLUE}Creating executable wrappers...${NC}"

    # Determine API entry point based on architecture choice
    local api_entry=$(get_api_entry_point)
    echo -e "${DIM}API architecture: ${API_ARCHITECTURE} (${api_entry})${NC}"

    # API server wrapper
    sudo tee "${BIN_DIR}/audiobooks-api" > /dev/null << EOF
#!/bin/bash
# Audiobook Library API Server
source /opt/audiobooks/lib/audiobooks-config.sh
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/${api_entry}" "\$@"
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-api"

    # Web server wrapper
    sudo tee "${BIN_DIR}/audiobooks-web" > /dev/null << 'EOF'
#!/bin/bash
# Audiobook Library Web Server (HTTPS)
source /opt/audiobooks/lib/audiobooks-config.sh
exec python3 "${AUDIOBOOKS_HOME}/library/web-v2/https_server.py" "$@"
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-web"

    # Scanner wrapper
    sudo tee "${BIN_DIR}/audiobooks-scan" > /dev/null << 'EOF'
#!/bin/bash
# Audiobook Library Scanner
source /opt/audiobooks/lib/audiobooks-config.sh
exec "$(audiobooks_python)" "${AUDIOBOOKS_HOME}/library/scanner/scan_audiobooks.py" "$@"
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-scan"

    # Database import wrapper
    sudo tee "${BIN_DIR}/audiobooks-import" > /dev/null << 'EOF'
#!/bin/bash
# Audiobook Library Database Import
source /opt/audiobooks/lib/audiobooks-config.sh
exec "$(audiobooks_python)" "${AUDIOBOOKS_HOME}/library/backend/import_to_db.py" "$@"
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-import"

    # Config viewer
    sudo tee "${BIN_DIR}/audiobooks-config" > /dev/null << 'EOF'
#!/bin/bash
# Show audiobook library configuration
source /opt/audiobooks/lib/audiobooks-config.sh
audiobooks_print_config
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-config"

    # Install management scripts to /opt/audiobooks/scripts/ (canonical location)
    # Then create symlinks in /usr/local/bin/ for PATH accessibility
    echo -e "${BLUE}Installing management scripts...${NC}"
    local APP_SCRIPTS_DIR="/opt/audiobooks/scripts"
    sudo mkdir -p "${APP_SCRIPTS_DIR}"

    # Copy all scripts from scripts/ directory to canonical location
    if [[ -d "${SCRIPT_DIR}/scripts" ]]; then
        for script in "${SCRIPT_DIR}/scripts/"*; do
            if [[ -f "$script" ]]; then
                local script_name=$(basename "$script")
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
    echo -e "${BLUE}Creating symlinks in ${BIN_DIR}...${NC}"
    # Map original script names to user-friendly command names
    declare -A SCRIPT_ALIASES=(
        ["convert-audiobooks-opus-parallel"]="audiobooks-convert"
        ["move-staged-audiobooks"]="audiobooks-move-staged"
        ["download-new-audiobooks"]="audiobooks-download"
        ["audiobook-save-staging"]="audiobooks-save-staging"
        ["audiobook-save-staging-auto"]="audiobooks-save-staging-auto"
        ["audiobook-status"]="audiobooks-status"
        ["audiobook-start"]="audiobooks-start"
        ["audiobook-stop"]="audiobooks-stop"
        ["audiobook-enable"]="audiobooks-enable"
        ["audiobook-disable"]="audiobooks-disable"
        ["audiobook-help"]="audiobooks-help"
        ["monitor-audiobook-conversion"]="audiobooks-monitor"
        ["copy-audiobook-metadata"]="audiobooks-copy-metadata"
        ["audiobook-download-monitor"]="audiobooks-download-monitor"
        ["embed-cover-art.py"]="audiobooks-embed-cover"
        ["build-conversion-queue"]="audiobooks-build-queue"
        ["upgrade.sh"]="audiobooks-upgrade"
        ["migrate-api.sh"]="audiobooks-migrate"
    )

    for script_name in "${!SCRIPT_ALIASES[@]}"; do
        local target_name="${SCRIPT_ALIASES[$script_name]}"
        local source_path="${APP_SCRIPTS_DIR}/${script_name}"
        local link_path="${BIN_DIR}/${target_name}"
        if [[ -f "$source_path" ]]; then
            sudo rm -f "$link_path"
            sudo ln -s "$source_path" "$link_path"
            echo "  Linked: ${target_name} -> ${source_path}"
        fi
    done

    # Store release info for GitHub-based upgrades
    echo -e "${BLUE}Storing release metadata...${NC}"
    if [[ -f "${SCRIPT_DIR}/.release-info" ]]; then
        sudo cp "${SCRIPT_DIR}/.release-info" "/opt/audiobooks/"
    else
        # Create default release info
        sudo tee "/opt/audiobooks/.release-info" > /dev/null << EOF
{
  "github_repo": "greogory/Audiobook-Manager",
  "github_api": "https://api.github.com/repos/greogory/Audiobook-Manager",
  "version": "$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo "unknown")",
  "install_date": "$(date -Iseconds)",
  "install_type": "system"
}
EOF
    fi
    sudo chmod 644 "/opt/audiobooks/.release-info"

    # Create upgrade wrapper
    sudo tee "${BIN_DIR}/audiobooks-upgrade" > /dev/null << 'EOF'
#!/bin/bash
# Audiobook Toolkit Upgrade Script
# Fetches and applies updates from GitHub releases
exec /opt/audiobooks/scripts/upgrade.sh --target /opt/audiobooks "$@"
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-upgrade"
    echo "  Installed: audiobooks-upgrade"

    # Create migrate wrapper
    sudo tee "${BIN_DIR}/audiobooks-migrate" > /dev/null << 'EOF'
#!/bin/bash
# Audiobook Toolkit API Migration Script
# Switch between monolithic and modular API architectures
exec /opt/audiobooks/scripts/migrate-api.sh --target /opt/audiobooks "$@"
EOF
    sudo chmod 755 "${BIN_DIR}/audiobooks-migrate"
    echo "  Installed: audiobooks-migrate"

    # Setup Python virtual environment if needed
    if [[ ! -d "${LIB_DIR}/library/venv" ]]; then
        echo -e "${BLUE}Setting up Python virtual environment...${NC}"
        sudo python3 -m venv "${LIB_DIR}/library/venv"
        sudo "${LIB_DIR}/library/venv/bin/pip" install --quiet Flask flask-cors
    fi

    # Generate SSL certificate if needed
    local CERT_DIR="${LIB_DIR}/library/certs"
    if [[ ! -f "${CERT_DIR}/server.crt" ]]; then
        echo -e "${BLUE}Generating SSL certificate (3-year validity)...${NC}"
        sudo mkdir -p "${CERT_DIR}"
        sudo openssl req -x509 -newkey rsa:4096 -sha256 -days 1095 \
            -nodes -keyout "${CERT_DIR}/server.key" -out "${CERT_DIR}/server.crt" \
            -subj "/CN=localhost/O=Audiobooks/C=US" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
            2>/dev/null
        sudo chmod 600 "${CERT_DIR}/server.key"
        sudo chmod 644 "${CERT_DIR}/server.crt"
    fi

    # Install systemd services
    if [[ "$INSTALL_SERVICES" == "true" ]]; then
        echo -e "${BLUE}Installing systemd services...${NC}"

        # API service
        sudo tee "${SYSTEMD_DIR}/audiobooks-api.service" > /dev/null << EOF
[Unit]
Description=Audiobooks Library API Server
Documentation=https://github.com/greogory/Audiobook-Manager
After=network.target

[Service]
Type=simple
EnvironmentFile=${CONFIG_DIR}/audiobooks.conf
ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:\${AUDIOBOOKS_API_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobooks-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

        # Web service
        sudo tee "${SYSTEMD_DIR}/audiobooks-web.service" > /dev/null << EOF
[Unit]
Description=Audiobooks Library Web Server (HTTPS)
Documentation=https://github.com/greogory/Audiobook-Manager
After=audiobooks-api.service
Wants=audiobooks-api.service

[Service]
Type=simple
EnvironmentFile=${CONFIG_DIR}/audiobooks.conf
ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:\${AUDIOBOOKS_WEB_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobooks-web
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

        # Install conversion/download/mover services from systemd/ directory
        if [[ -d "${SCRIPT_DIR}/systemd" ]]; then
            echo -e "${BLUE}Installing conversion and management services...${NC}"
            for service_file in "${SCRIPT_DIR}/systemd/"*; do
                if [[ -f "$service_file" ]]; then
                    local service_name=$(basename "$service_file")
                    # Skip the target file - we handle that specially
                    if [[ "$service_name" == "audiobooks.target" ]]; then
                        continue
                    fi
                    sudo cp "$service_file" "${SYSTEMD_DIR}/${service_name}"
                    sudo chmod 644 "${SYSTEMD_DIR}/${service_name}"
                    echo "  Installed: ${service_name}"
                fi
            done
        fi

        # Target (includes all services)
        sudo tee "${SYSTEMD_DIR}/audiobooks.target" > /dev/null << EOF
[Unit]
Description=Audiobooks Library Services
Documentation=https://github.com/greogory/Audiobook-Manager
Wants=audiobooks-api.service audiobooks-web.service audiobooks-converter.service audiobooks-mover.service audiobooks-downloader.timer

[Install]
WantedBy=multi-user.target
EOF

        # Reload systemd
        sudo systemctl daemon-reload

        # Enable and start services
        echo -e "${BLUE}Enabling and starting services...${NC}"
        sudo systemctl enable audiobooks.target 2>/dev/null || true

        # Start the target (which starts all wanted services)
        sudo systemctl start audiobooks.target 2>/dev/null || {
            # Fallback: start individual services
            for svc in audiobooks-api audiobooks-proxy audiobooks-converter audiobooks-mover; do
                sudo systemctl start "$svc" 2>/dev/null || true
            done
        }

        # Verify services started
        echo ""
        echo -e "${BLUE}Service status:${NC}"
        local all_ok=true
        for svc in audiobooks-api audiobooks-proxy audiobooks-converter audiobooks-mover; do
            local status
            status=$(systemctl is-active "$svc" 2>/dev/null || echo "inactive")
            if [[ "$status" == "active" ]]; then
                echo -e "  $svc: ${GREEN}$status${NC}"
            else
                echo -e "  $svc: ${YELLOW}$status${NC}"
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
        echo "  audiobooks-api          - API server"
        echo "  audiobooks-proxy        - HTTPS proxy server"
        echo "  audiobooks-converter    - Continuous audiobook converter"
        echo "  audiobooks-mover        - Moves staged files to library"
        echo "  audiobooks-downloader   - Downloads new audiobooks (timer-triggered)"
    fi

    # Create /etc/profile.d script
    echo -e "${BLUE}Creating environment profile...${NC}"
    sudo tee /etc/profile.d/audiobooks.sh > /dev/null << 'EOF'
# Audiobook Library Environment
if [[ -f /opt/audiobooks/lib/audiobooks-config.sh ]]; then
    source /opt/audiobooks/lib/audiobooks-config.sh
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
    echo "  audiobooks-api             - Start API server"
    echo "  audiobooks-web             - Start web server"
    echo "  audiobooks-scan            - Scan audiobook library"
    echo "  audiobooks-import          - Import to database"
    echo "  audiobooks-config          - Show configuration"
    echo ""
    echo "Conversion and management:"
    echo "  audiobooks-convert         - Convert AAX/AAXC to Opus"
    echo "  audiobooks-download        - Download from Audible"
    echo "  audiobooks-move-staged     - Move staged files to library"
    echo "  audiobooks-save-staging    - Save tmpfs staging before reboot"
    echo "  audiobooks-status          - Show service status"
    echo "  audiobooks-start/stop      - Start/stop services"
    echo "  audiobooks-enable/disable  - Enable/disable at boot"
    echo "  audiobooks-monitor         - Live conversion monitor"
    echo "  audiobooks-help            - Quick reference guide"
    echo ""
    echo "Access the library at: https://localhost:${WEB_PORT}"

    # Verify permissions after installation
    verify_installation_permissions "system"
}

do_system_uninstall() {
    local BIN_DIR="/usr/local/bin"
    local LIB_DIR="/usr/local/lib/audiobooks"
    local CONFIG_DIR="/etc/audiobooks"
    local SYSTEMD_DIR="/etc/systemd/system"

    echo -e "${YELLOW}=== Uninstalling System Installation ===${NC}"

    # Stop and disable services
    echo -e "${BLUE}Stopping services...${NC}"
    sudo systemctl stop audiobooks.target 2>/dev/null || true
    sudo systemctl stop audiobooks-api.service audiobooks-web.service 2>/dev/null || true
    sudo systemctl stop audiobooks-converter.service audiobooks-mover.service 2>/dev/null || true
    sudo systemctl stop audiobooks-downloader.timer audiobooks-downloader.service 2>/dev/null || true
    sudo systemctl stop audiobooks-shutdown-saver.service 2>/dev/null || true
    sudo systemctl disable audiobooks.target 2>/dev/null || true
    sudo systemctl disable audiobooks-api.service audiobooks-web.service 2>/dev/null || true
    sudo systemctl disable audiobooks-converter.service audiobooks-mover.service 2>/dev/null || true
    sudo systemctl disable audiobooks-downloader.timer audiobooks-shutdown-saver.service 2>/dev/null || true

    # Remove application files
    echo -e "${BLUE}Removing application files...${NC}"
    # Core wrappers
    sudo rm -f "${BIN_DIR}/audiobooks-api"
    sudo rm -f "${BIN_DIR}/audiobooks-web"
    sudo rm -f "${BIN_DIR}/audiobooks-scan"
    sudo rm -f "${BIN_DIR}/audiobooks-import"
    sudo rm -f "${BIN_DIR}/audiobooks-config"
    # Management scripts
    sudo rm -f "${BIN_DIR}/audiobooks-convert"
    sudo rm -f "${BIN_DIR}/audiobooks-move-staged"
    sudo rm -f "${BIN_DIR}/audiobooks-download"
    sudo rm -f "${BIN_DIR}/audiobooks-save-staging"
    sudo rm -f "${BIN_DIR}/audiobooks-save-staging-auto"
    sudo rm -f "${BIN_DIR}/audiobooks-status"
    sudo rm -f "${BIN_DIR}/audiobooks-start"
    sudo rm -f "${BIN_DIR}/audiobooks-stop"
    sudo rm -f "${BIN_DIR}/audiobooks-enable"
    sudo rm -f "${BIN_DIR}/audiobooks-disable"
    sudo rm -f "${BIN_DIR}/audiobooks-help"
    sudo rm -f "${BIN_DIR}/audiobooks-monitor"
    sudo rm -f "${BIN_DIR}/audiobooks-copy-metadata"
    sudo rm -f "${BIN_DIR}/audiobooks-download-monitor"
    sudo rm -f "${BIN_DIR}/audiobooks-embed-cover"
    # Library
    sudo rm -rf "${LIB_DIR}"
    # Systemd services
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-api.service"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-web.service"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-converter.service"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-mover.service"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-downloader.service"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-downloader.timer"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks-shutdown-saver.service"
    sudo rm -f "${SYSTEMD_DIR}/audiobooks.target"
    sudo rm -f /etc/profile.d/audiobooks.sh

    # Remove database and logs
    sudo rm -rf /var/lib/audiobooks
    sudo rm -rf /var/log/audiobooks

    # Reload systemd
    sudo systemctl daemon-reload

    echo -e "${GREEN}Application files removed.${NC}"

    # Prompt about data directories
    if [[ -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        prompt_delete_data "${CONFIG_DIR}/audiobooks.conf" "sudo"
    else
        echo ""
        echo "Note: No configuration file found at ${CONFIG_DIR}/audiobooks.conf"
        echo "Data directories were not modified."
    fi

    echo ""
    echo -e "${GREEN}System uninstallation complete.${NC}"
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
    echo "  Logs:         ${LOG_DIR}/"
    echo ""

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
    mkdir -p "${LIB_DIR}"
    mkdir -p "${BIN_DIR}"
    mkdir -p "${data_dir}/Library"
    mkdir -p "${data_dir}/Sources"
    mkdir -p "${data_dir}/Supplements"
    mkdir -p "${STATE_DIR}"
    mkdir -p "${LOG_DIR}"
    mkdir -p "${SYSTEMD_DIR}"

    # Install library files
    echo -e "${BLUE}Installing library files...${NC}"
    cp -r "${SCRIPT_DIR}/library" "${LIB_DIR}/"
    cp -r "${SCRIPT_DIR}/lib" "${LIB_DIR}/"
    [[ -d "${SCRIPT_DIR}/converter" ]] && cp -r "${SCRIPT_DIR}/converter" "${LIB_DIR}/"
    cp "${SCRIPT_DIR}/etc/audiobooks.conf.example" "${CONFIG_DIR}/"

    # Create config file if it doesn't exist
    if [[ ! -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        echo -e "${BLUE}Creating configuration file...${NC}"
        cat > "${CONFIG_DIR}/audiobooks.conf" << EOF
# Audiobook Library Configuration
# Generated by install.sh on $(date +%Y-%m-%d)

# Data directories
AUDIOBOOKS_DATA="${data_dir}"
AUDIOBOOKS_LIBRARY="\${AUDIOBOOKS_DATA}/Library"
AUDIOBOOKS_SOURCES="\${AUDIOBOOKS_DATA}/Sources"
AUDIOBOOKS_SUPPLEMENTS="\${AUDIOBOOKS_DATA}/Supplements"

# Application directories
AUDIOBOOKS_HOME="${LIB_DIR}"
AUDIOBOOKS_DATABASE="${STATE_DIR}/audiobooks.db"
AUDIOBOOKS_COVERS="\${AUDIOBOOKS_HOME}/library/web-v2/covers"
AUDIOBOOKS_CERTS="${CONFIG_DIR}/certs"
AUDIOBOOKS_LOGS="${LOG_DIR}"
AUDIOBOOKS_VENV="\${AUDIOBOOKS_HOME}/library/venv"

# Server settings
AUDIOBOOKS_API_PORT="${API_PORT}"
AUDIOBOOKS_WEB_PORT="${WEB_PORT}"
AUDIOBOOKS_HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT}"
AUDIOBOOKS_BIND_ADDRESS="0.0.0.0"
AUDIOBOOKS_HTTPS_ENABLED="true"
AUDIOBOOKS_HTTP_REDIRECT_ENABLED="true"
EOF
    fi

    # Create wrapper scripts
    echo -e "${BLUE}Creating executable wrappers...${NC}"

    # Determine API entry point based on architecture choice
    local api_entry=$(get_api_entry_point)
    echo -e "${DIM}API architecture: ${API_ARCHITECTURE} (${api_entry})${NC}"

    # API server wrapper
    cat > "${BIN_DIR}/audiobooks-api" << EOF
#!/bin/bash
# Audiobook Library API Server
source "${LIB_DIR}/lib/audiobooks-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/${api_entry}" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobooks-api"

    # Web server wrapper
    cat > "${BIN_DIR}/audiobooks-web" << EOF
#!/bin/bash
# Audiobook Library Web Server (HTTPS)
source "${LIB_DIR}/lib/audiobooks-config.sh"
exec python3 "\${AUDIOBOOKS_HOME}/library/web-v2/https_server.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobooks-web"

    # Scanner wrapper
    cat > "${BIN_DIR}/audiobooks-scan" << EOF
#!/bin/bash
# Audiobook Library Scanner
source "${LIB_DIR}/lib/audiobooks-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/scanner/scan_audiobooks.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobooks-scan"

    # Database import wrapper
    cat > "${BIN_DIR}/audiobooks-import" << EOF
#!/bin/bash
# Audiobook Library Database Import
source "${LIB_DIR}/lib/audiobooks-config.sh"
exec "\$(audiobooks_python)" "\${AUDIOBOOKS_HOME}/library/backend/import_to_db.py" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobooks-import"

    # Config viewer
    cat > "${BIN_DIR}/audiobooks-config" << EOF
#!/bin/bash
# Show audiobook library configuration
source "${LIB_DIR}/lib/audiobooks-config.sh"
audiobooks_print_config
EOF
    chmod 755 "${BIN_DIR}/audiobooks-config"

    # Install conversion and management scripts from scripts/ directory
    echo -e "${BLUE}Installing audiobook management scripts...${NC}"
    if [[ -d "${SCRIPT_DIR}/scripts" ]]; then
        for script in "${SCRIPT_DIR}/scripts/"*; do
            if [[ -f "$script" ]]; then
                local script_name=$(basename "$script")
                # Map script names to consistent audiobooks- prefix
                local target_name
                case "$script_name" in
                    convert-audiobooks-opus-parallel)
                        target_name="audiobooks-convert"
                        ;;
                    move-staged-audiobooks)
                        target_name="audiobooks-move-staged"
                        ;;
                    download-new-audiobooks)
                        target_name="audiobooks-download"
                        ;;
                    audiobook-save-staging)
                        target_name="audiobooks-save-staging"
                        ;;
                    audiobook-save-staging-auto)
                        target_name="audiobooks-save-staging-auto"
                        ;;
                    audiobook-status)
                        target_name="audiobooks-status"
                        ;;
                    audiobook-start)
                        target_name="audiobooks-start"
                        ;;
                    audiobook-stop)
                        target_name="audiobooks-stop"
                        ;;
                    audiobook-enable)
                        target_name="audiobooks-enable"
                        ;;
                    audiobook-disable)
                        target_name="audiobooks-disable"
                        ;;
                    audiobook-help)
                        target_name="audiobooks-help"
                        ;;
                    monitor-audiobook-conversion)
                        target_name="audiobooks-monitor"
                        ;;
                    copy-audiobook-metadata)
                        target_name="audiobooks-copy-metadata"
                        ;;
                    audiobook-download-monitor)
                        target_name="audiobooks-download-monitor"
                        ;;
                    embed-cover-art.py)
                        target_name="audiobooks-embed-cover"
                        ;;
                    *)
                        target_name="audiobooks-${script_name}"
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
        cat > "${LIB_DIR}/.release-info" << EOF
{
  "github_repo": "greogory/Audiobook-Manager",
  "github_api": "https://api.github.com/repos/greogory/Audiobook-Manager",
  "version": "$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo "unknown")",
  "install_date": "$(date -Iseconds)",
  "install_type": "user"
}
EOF
    fi
    chmod 644 "${LIB_DIR}/.release-info"

    # Create upgrade wrapper
    cat > "${BIN_DIR}/audiobooks-upgrade" << EOF
#!/bin/bash
# Audiobook Toolkit Upgrade Script
# Fetches and applies updates from GitHub releases
exec "${LIB_DIR}/scripts/upgrade.sh" --target "${LIB_DIR}" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobooks-upgrade"
    echo "  Installed: audiobooks-upgrade"

    # Create migrate wrapper
    cat > "${BIN_DIR}/audiobooks-migrate" << EOF
#!/bin/bash
# Audiobook Toolkit API Migration Script
# Switch between monolithic and modular API architectures
exec "${LIB_DIR}/scripts/migrate-api.sh" --target "${LIB_DIR}" "\$@"
EOF
    chmod 755 "${BIN_DIR}/audiobooks-migrate"
    echo "  Installed: audiobooks-migrate"

    # Setup Python virtual environment if needed
    if [[ ! -d "${LIB_DIR}/library/venv" ]]; then
        echo -e "${BLUE}Setting up Python virtual environment...${NC}"
        python3 -m venv "${LIB_DIR}/library/venv"
        "${LIB_DIR}/library/venv/bin/pip" install --quiet Flask flask-cors
    fi

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
        cat > "${SYSTEMD_DIR}/audiobooks-api.service" << EOF
[Unit]
Description=Audiobooks Library API Server
Documentation=https://github.com/greogory/Audiobook-Manager
After=default.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=${LIB_DIR}
Environment=AUDIOBOOKS_DATA=${data_dir}
Environment=AUDIOBOOKS_LIBRARY=${data_dir}/Library
Environment=AUDIOBOOKS_SOURCES=${data_dir}/Sources
Environment=AUDIOBOOKS_SUPPLEMENTS=${data_dir}/Supplements
Environment=AUDIOBOOKS_DATABASE=${STATE_DIR}/audiobooks.db
Environment=AUDIOBOOKS_COVERS=${LIB_DIR}/library/web-v2/covers
Environment=AUDIOBOOKS_CERTS=${CONFIG_DIR}/certs
Environment=AUDIOBOOKS_LOGS=${LOG_DIR}
Environment=AUDIOBOOKS_API_PORT=${API_PORT}
Environment=AUDIOBOOKS_WEB_PORT=${WEB_PORT}
Environment=AUDIOBOOKS_HTTP_REDIRECT_PORT=${HTTP_REDIRECT_PORT}

ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:${API_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobooks-api
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

        # Web service
        cat > "${SYSTEMD_DIR}/audiobooks-web.service" << EOF
[Unit]
Description=Audiobooks Library Web Server (HTTPS)
Documentation=https://github.com/greogory/Audiobook-Manager
After=audiobooks-api.service
Wants=audiobooks-api.service

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=AUDIOBOOKS_HOME=${LIB_DIR}
Environment=AUDIOBOOKS_WEB_PORT=${WEB_PORT}
Environment=AUDIOBOOKS_HTTP_REDIRECT_PORT=${HTTP_REDIRECT_PORT}
Environment=AUDIOBOOKS_CERTS=${CONFIG_DIR}/certs

ExecStartPre=/bin/sh -c '! /usr/bin/lsof -i:${WEB_PORT} >/dev/null 2>&1'
ExecStart=${BIN_DIR}/audiobooks-web
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

        # Target
        cat > "${SYSTEMD_DIR}/audiobooks.target" << EOF
[Unit]
Description=Audiobooks Library Services
Documentation=https://github.com/greogory/Audiobook-Manager
Wants=audiobooks-api.service audiobooks-web.service

[Install]
WantedBy=default.target
EOF

        # Reload systemd
        systemctl --user daemon-reload

        echo ""
        echo -e "${YELLOW}To enable services at login:${NC}"
        echo "  systemctl --user enable audiobooks-api audiobooks-web"
        echo ""
        echo -e "${YELLOW}To start services now:${NC}"
        echo "  systemctl --user start audiobooks.target"
        echo ""
        echo -e "${YELLOW}To enable lingering (start at boot without login):${NC}"
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
    echo "  audiobooks-api             - Start API server"
    echo "  audiobooks-web             - Start web server"
    echo "  audiobooks-scan            - Scan audiobook library"
    echo "  audiobooks-import          - Import to database"
    echo "  audiobooks-config          - Show configuration"
    echo ""
    echo "Conversion and management:"
    echo "  audiobooks-convert         - Convert AAX/AAXC to Opus"
    echo "  audiobooks-download        - Download from Audible"
    echo "  audiobooks-move-staged     - Move staged files to library"
    echo "  audiobooks-save-staging    - Save tmpfs staging before reboot"
    echo "  audiobooks-status          - Show service status"
    echo "  audiobooks-start/stop      - Start/stop services"
    echo "  audiobooks-enable/disable  - Enable/disable at boot"
    echo "  audiobooks-monitor         - Live conversion monitor"
    echo "  audiobooks-help            - Quick reference guide"
    echo ""
    echo "Service management:"
    echo "  systemctl --user status audiobooks.target"
    echo "  systemctl --user restart audiobooks.target"
    echo "  journalctl --user -u audiobooks-converter -f"
    echo ""
    echo "Access the library at: https://localhost:${WEB_PORT}"
    echo ""
    echo "NOTE: Your browser will show a security warning for the self-signed"
    echo "certificate. Click 'Advanced' -> 'Proceed to localhost' to continue."

    # Verify permissions after installation
    verify_installation_permissions "user"
}

do_user_uninstall() {
    local BIN_DIR="$HOME/.local/bin"
    local LIB_DIR="$HOME/.local/lib/audiobooks"
    local CONFIG_DIR="$HOME/.config/audiobooks"
    local SYSTEMD_DIR="$HOME/.config/systemd/user"
    local STATE_DIR="$HOME/.local/var/lib/audiobooks"
    local LOG_DIR="$HOME/.local/var/log/audiobooks"

    echo -e "${YELLOW}=== Uninstalling User Installation ===${NC}"

    # Stop and disable services
    echo -e "${BLUE}Stopping services...${NC}"
    systemctl --user stop audiobooks.target 2>/dev/null || true
    systemctl --user stop audiobooks-api.service audiobooks-web.service 2>/dev/null || true
    systemctl --user stop audiobooks-converter.service audiobooks-mover.service 2>/dev/null || true
    systemctl --user stop audiobooks-downloader.timer audiobooks-downloader.service 2>/dev/null || true
    systemctl --user disable audiobooks.target 2>/dev/null || true
    systemctl --user disable audiobooks-api.service audiobooks-web.service 2>/dev/null || true
    systemctl --user disable audiobooks-converter.service audiobooks-mover.service 2>/dev/null || true
    systemctl --user disable audiobooks-downloader.timer 2>/dev/null || true

    # Remove application files
    echo -e "${BLUE}Removing application files...${NC}"
    # Core wrappers
    rm -f "${BIN_DIR}/audiobooks-api"
    rm -f "${BIN_DIR}/audiobooks-web"
    rm -f "${BIN_DIR}/audiobooks-scan"
    rm -f "${BIN_DIR}/audiobooks-import"
    rm -f "${BIN_DIR}/audiobooks-config"
    # Management scripts
    rm -f "${BIN_DIR}/audiobooks-convert"
    rm -f "${BIN_DIR}/audiobooks-move-staged"
    rm -f "${BIN_DIR}/audiobooks-download"
    rm -f "${BIN_DIR}/audiobooks-save-staging"
    rm -f "${BIN_DIR}/audiobooks-save-staging-auto"
    rm -f "${BIN_DIR}/audiobooks-status"
    rm -f "${BIN_DIR}/audiobooks-start"
    rm -f "${BIN_DIR}/audiobooks-stop"
    rm -f "${BIN_DIR}/audiobooks-enable"
    rm -f "${BIN_DIR}/audiobooks-disable"
    rm -f "${BIN_DIR}/audiobooks-help"
    rm -f "${BIN_DIR}/audiobooks-monitor"
    rm -f "${BIN_DIR}/audiobooks-copy-metadata"
    rm -f "${BIN_DIR}/audiobooks-download-monitor"
    rm -f "${BIN_DIR}/audiobooks-embed-cover"
    # Library
    rm -rf "${LIB_DIR}"
    # Systemd services
    rm -f "${SYSTEMD_DIR}/audiobooks-api.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-web.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-converter.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-mover.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-downloader.service"
    rm -f "${SYSTEMD_DIR}/audiobooks-downloader.timer"
    rm -f "${SYSTEMD_DIR}/audiobooks.target"

    # Remove database and logs
    rm -rf "${STATE_DIR}"
    rm -rf "${LOG_DIR}"

    # Reload systemd
    systemctl --user daemon-reload 2>/dev/null || true

    echo -e "${GREEN}Application files removed.${NC}"

    # Prompt about data directories
    if [[ -f "${CONFIG_DIR}/audiobooks.conf" ]]; then
        prompt_delete_data "${CONFIG_DIR}/audiobooks.conf" ""
    else
        echo ""
        echo "Note: No configuration file found at ${CONFIG_DIR}/audiobooks.conf"
        echo "Data directories were not modified."
    fi

    echo ""
    echo -e "${GREEN}User uninstallation complete.${NC}"
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
        --help|-h)
            head -25 "$0" | grep -E '^#' | sed 's/^# //' | sed 's/^#//'
            echo ""
            echo "Architecture options:"
            echo "  --modular          Use modular Flask Blueprint architecture"
            echo "  --monolithic       Use single-file architecture (default)"
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

# Handle command-line mode selection
if [[ -n "$INSTALL_MODE" ]]; then
    if [[ "$INSTALL_MODE" == "system" ]]; then
        if [[ "$UNINSTALL" == "true" ]]; then
            if ! check_sudo_access; then
                show_sudo_error
                exit 1
            fi
            if ! verify_sudo; then
                show_sudo_error
                exit 1
            fi
            do_system_uninstall
        else
            if ! check_sudo_access; then
                show_sudo_error
                exit 1
            fi
            if ! verify_sudo; then
                show_sudo_error
                exit 1
            fi
            do_system_install
        fi
    elif [[ "$INSTALL_MODE" == "user" ]]; then
        if [[ "$UNINSTALL" == "true" ]]; then
            do_user_uninstall
        else
            do_user_install
        fi
    fi
    exit 0
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
