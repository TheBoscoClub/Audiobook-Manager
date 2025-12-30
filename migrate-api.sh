#!/bin/bash
# =============================================================================
# Audiobook Library - API Architecture Migration Script
# =============================================================================
# Migrates between monolithic (api.py) and modular (api_modular/) architectures.
#
# Usage:
#   ./migrate-api.sh [OPTIONS]
#
# Options:
#   --to-modular        Migrate to modular Flask Blueprint architecture
#   --to-monolithic     Migrate back to single-file architecture
#   --status            Show current architecture status
#   --explain           Explain the differences between architectures
#   --target PATH       Target installation directory
#   --dry-run           Show what would be done without making changes
#   --help              Show this help message
#
# Examples:
#   ./migrate-api.sh --status                    # Check current architecture
#   ./migrate-api.sh --to-modular --target /opt/audiobooks
#   ./migrate-api.sh --to-monolithic --dry-run
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
MAGENTA='\033[0;35m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Options
ACTION=""
TARGET_DIR=""
DRY_RUN=false

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

print_header() {
    echo ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║           API Architecture Migration Tool                         ║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

show_help() {
    cat << 'EOF'
Audiobook Library - API Architecture Migration

USAGE:
    ./migrate-api.sh [OPTIONS]

OPTIONS:
    --to-modular        Migrate to modular Flask Blueprint architecture
    --to-monolithic     Migrate back to single-file architecture
    --status            Show current architecture status
    --explain           Explain the differences between architectures
    --target PATH       Target installation directory
    --dry-run           Show what would be done without making changes
    --help              Show this help message

EXAMPLES:
    ./migrate-api.sh --status
    ./migrate-api.sh --explain
    ./migrate-api.sh --to-modular --target /opt/audiobooks
    ./migrate-api.sh --to-monolithic --target ~/.local/lib/audiobooks

ARCHITECTURE COMPARISON:

    Monolithic (api.py):
    - Single 2000-line Python file
    - Simpler deployment
    - Battle-tested with all tests passing
    - Best for: End users, production stability

    Modular (api_modular/):
    - 8 focused modules by feature area
    - Easier to navigate and modify
    - Better for development and debugging
    - Best for: Developers, contributors, forking

See README in api_modular/ for detailed documentation.
EOF
}

explain_architectures() {
    print_header

    echo -e "${BOLD}What is the difference between Monolithic and Modular?${NC}"
    echo ""
    echo "Think of it like organizing your music collection:"
    echo ""

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}${BOLD}MONOLITHIC${NC} ${DIM}(api.py)${NC} - Everything in One Place"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Like keeping all your music in one giant playlist."
    echo ""
    echo -e "  ${GREEN}✓${NC} Simple - everything is in one file"
    echo -e "  ${GREEN}✓${NC} Easy to deploy - just copy one file"
    echo -e "  ${GREEN}✓${NC} Proven stable - all tests pass against it"
    echo -e "  ${GREEN}✓${NC} No extra configuration needed"
    echo ""
    echo -e "  ${RED}✗${NC} Hard to navigate in a 2000-line file"
    echo -e "  ${RED}✗${NC} Difficult to find specific features"
    echo -e "  ${RED}✗${NC} Harder to modify without breaking things"
    echo ""
    echo -e "  ${BOLD}Best for:${NC} End users who just want the app to work"
    echo -e "           People who won't modify the code"
    echo -e "           Production servers that need stability"
    echo ""

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}${BOLD}MODULAR${NC} ${DIM}(api_modular/)${NC} - Organized by Feature"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Like organizing music into folders by genre, artist, year."
    echo ""
    echo -e "  ${GREEN}✓${NC} Easy to find specific features"
    echo -e "  ${GREEN}✓${NC} Each module is 150-450 lines (manageable)"
    echo -e "  ${GREEN}✓${NC} Better for understanding the code"
    echo -e "  ${GREEN}✓${NC} Easier to modify one feature without touching others"
    echo -e "  ${GREEN}✓${NC} Better foundation for adding new features"
    echo ""
    echo -e "  ${RED}✗${NC} More files to manage"
    echo -e "  ${RED}✗${NC} Requires understanding Python packages"
    echo -e "  ${RED}✗${NC} Test mocking paths need updating"
    echo ""
    echo -e "  ${BOLD}Best for:${NC} Developers who want to contribute"
    echo -e "           People who want to understand how it works"
    echo -e "           Those planning to fork or customize"
    echo ""

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${MAGENTA}${BOLD}THE BOTTOM LINE${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  If you're asking yourself 'which one should I choose?'"
    echo ""
    echo -e "  ${YELLOW}→${NC} Will you ever look at the Python code?      No → ${GREEN}Monolithic${NC}"
    echo -e "  ${YELLOW}→${NC} Will you fix bugs yourself?                 No → ${GREEN}Monolithic${NC}"
    echo -e "  ${YELLOW}→${NC} Will you add new features?                  No → ${GREEN}Monolithic${NC}"
    echo -e "  ${YELLOW}→${NC} Do you just want it to work?               Yes → ${GREEN}Monolithic${NC}"
    echo ""
    echo -e "  ${YELLOW}→${NC} Are you a developer who likes clean code? Yes → ${BLUE}Modular${NC}"
    echo -e "  ${YELLOW}→${NC} Do you plan to contribute or fork?        Yes → ${BLUE}Modular${NC}"
    echo -e "  ${YELLOW}→${NC} Do you want to understand the internals?  Yes → ${BLUE}Modular${NC}"
    echo ""
    echo "  Both architectures provide identical functionality."
    echo "  You can switch between them at any time."
    echo ""
}

detect_installation() {
    local dir="$1"

    # Check if it's a valid audiobooks installation
    if [[ ! -d "$dir/library/backend" ]]; then
        echo "not_found"
        return
    fi

    # Check which architecture is active
    local api_script="$dir/bin/audiobooks-api"
    if [[ -f "$api_script" ]]; then
        if grep -q "api_server.py" "$api_script" 2>/dev/null; then
            echo "modular"
        elif grep -q "api.py" "$api_script" 2>/dev/null; then
            echo "monolithic"
        else
            echo "unknown"
        fi
    else
        # No wrapper script - check systemd or just assume monolithic
        echo "monolithic"
    fi
}

show_status() {
    print_header

    local target="${TARGET_DIR:-$SCRIPT_DIR}"
    local status=$(detect_installation "$target")

    echo -e "${BOLD}Installation:${NC} $target"
    echo ""

    case "$status" in
        modular)
            echo -e "${BOLD}Current Architecture:${NC} ${BLUE}Modular${NC} (api_modular/)"
            echo ""
            echo "  The API is running from the modular Flask Blueprint package."
            echo "  Entry point: api_server.py → api_modular/"
            echo ""
            echo "  Modules:"
            if [[ -d "$target/library/backend/api_modular" ]]; then
                for module in "$target/library/backend/api_modular"/*.py; do
                    if [[ -f "$module" ]]; then
                        local name=$(basename "$module")
                        local lines=$(wc -l < "$module" 2>/dev/null || echo "?")
                        echo "    - $name ($lines lines)"
                    fi
                done
            fi
            ;;
        monolithic)
            echo -e "${BOLD}Current Architecture:${NC} ${GREEN}Monolithic${NC} (api.py)"
            echo ""
            echo "  The API is running from the single-file implementation."
            echo "  Entry point: api.py"
            echo ""
            if [[ -f "$target/library/backend/api.py" ]]; then
                local lines=$(wc -l < "$target/library/backend/api.py")
                echo "  File size: $lines lines"
            fi
            ;;
        not_found)
            echo -e "${RED}No audiobooks installation found at: $target${NC}"
            echo ""
            echo "Use --target to specify the installation directory."
            return 1
            ;;
        *)
            echo -e "${YELLOW}Unknown architecture state${NC}"
            echo ""
            echo "Could not determine current API architecture."
            ;;
    esac

    echo ""

    # Check if both are available
    local has_monolithic=false
    local has_modular=false

    if [[ -f "$target/library/backend/api.py" ]]; then
        has_monolithic=true
    fi

    if [[ -d "$target/library/backend/api_modular" ]]; then
        has_modular=true
    fi

    echo -e "${BOLD}Available Architectures:${NC}"
    if $has_monolithic; then
        echo -e "  ${GREEN}✓${NC} Monolithic (api.py)"
    else
        echo -e "  ${RED}✗${NC} Monolithic (api.py) - not installed"
    fi

    if $has_modular; then
        echo -e "  ${GREEN}✓${NC} Modular (api_modular/)"
    else
        echo -e "  ${RED}✗${NC} Modular (api_modular/) - not installed"
    fi
    echo ""
}

update_wrapper_script() {
    local wrapper="$1"
    local entry_point="$2"
    local dry_run="$3"

    if [[ ! -f "$wrapper" ]]; then
        echo -e "${YELLOW}Wrapper script not found: $wrapper${NC}"
        return 1
    fi

    if $dry_run; then
        echo -e "${DIM}[dry-run] Would update: $wrapper${NC}"
        echo -e "${DIM}[dry-run] New entry point: $entry_point${NC}"
        return 0
    fi

    # Update the wrapper script
    if grep -q "api_server.py" "$wrapper"; then
        sed -i "s|api_server.py|$entry_point|g" "$wrapper"
    elif grep -q "api.py" "$wrapper"; then
        sed -i "s|api.py|$entry_point|g" "$wrapper"
    fi

    echo -e "${GREEN}Updated: $wrapper${NC}"
}

update_systemd_service() {
    local service_file="$1"
    local entry_point="$2"
    local dry_run="$3"

    if [[ ! -f "$service_file" ]]; then
        return 0
    fi

    if $dry_run; then
        echo -e "${DIM}[dry-run] Would update: $service_file${NC}"
        return 0
    fi

    # Update ExecStart line
    if grep -q "api_server.py" "$service_file"; then
        sudo sed -i "s|api_server.py|$entry_point|g" "$service_file"
    elif grep -q "api.py" "$service_file"; then
        sudo sed -i "s|api.py|$entry_point|g" "$service_file"
    fi

    echo -e "${GREEN}Updated: $service_file${NC}"
}

migrate_to_modular() {
    local target="${TARGET_DIR:-$SCRIPT_DIR}"

    print_header
    echo -e "${BOLD}Migrating to Modular Architecture${NC}"
    echo ""

    # Check if modular package exists
    if [[ ! -d "$target/library/backend/api_modular" ]]; then
        echo -e "${RED}Error: Modular package not found at:${NC}"
        echo "  $target/library/backend/api_modular"
        echo ""
        echo "Please ensure the api_modular package is installed."
        return 1
    fi

    # Check if api_server.py exists
    if [[ ! -f "$target/library/backend/api_server.py" ]]; then
        echo -e "${RED}Error: Entry point not found at:${NC}"
        echo "  $target/library/backend/api_server.py"
        return 1
    fi

    local current=$(detect_installation "$target")
    if [[ "$current" == "modular" ]]; then
        echo -e "${GREEN}Already using modular architecture.${NC}"
        return 0
    fi

    echo "Target: $target"
    echo ""

    if $DRY_RUN; then
        echo -e "${YELLOW}DRY RUN - No changes will be made${NC}"
        echo ""
    fi

    # Update wrapper scripts
    echo -e "${BOLD}Updating wrapper scripts...${NC}"
    for wrapper in "$target/bin/audiobooks-api" \
                   "$target/bin/audiobooks" \
                   "/usr/local/bin/audiobooks-api" \
                   "$HOME/.local/bin/audiobooks-api"; do
        if [[ -f "$wrapper" ]]; then
            update_wrapper_script "$wrapper" "api_server.py" $DRY_RUN
        fi
    done
    echo ""

    # Update systemd services
    echo -e "${BOLD}Checking systemd services...${NC}"
    for service in /etc/systemd/system/audiobooks-api.service \
                   "$HOME/.config/systemd/user/audiobooks-api.service"; do
        if [[ -f "$service" ]]; then
            update_systemd_service "$service" "api_server.py" $DRY_RUN
        fi
    done
    echo ""

    if ! $DRY_RUN; then
        # Reload systemd if needed
        if [[ -f /etc/systemd/system/audiobooks-api.service ]]; then
            echo -e "${BOLD}Reloading systemd...${NC}"
            sudo systemctl daemon-reload
        fi

        if [[ -f "$HOME/.config/systemd/user/audiobooks-api.service" ]]; then
            systemctl --user daemon-reload
        fi

        echo ""
        echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║                    Migration Complete!                            ║${NC}"
        echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "The API is now using the modular architecture."
        echo ""
        echo "To restart the service:"
        echo "  sudo systemctl restart audiobooks-api"
        echo ""
        echo "To switch back to monolithic:"
        echo "  ./migrate-api.sh --to-monolithic"

        # Verify permissions after migration
        verify_installation_permissions "$target"
    fi
}

migrate_to_monolithic() {
    local target="${TARGET_DIR:-$SCRIPT_DIR}"

    print_header
    echo -e "${BOLD}Migrating to Monolithic Architecture${NC}"
    echo ""

    # Check if api.py exists
    if [[ ! -f "$target/library/backend/api.py" ]]; then
        echo -e "${RED}Error: Monolithic API not found at:${NC}"
        echo "  $target/library/backend/api.py"
        return 1
    fi

    local current=$(detect_installation "$target")
    if [[ "$current" == "monolithic" ]]; then
        echo -e "${GREEN}Already using monolithic architecture.${NC}"
        return 0
    fi

    echo "Target: $target"
    echo ""

    if $DRY_RUN; then
        echo -e "${YELLOW}DRY RUN - No changes will be made${NC}"
        echo ""
    fi

    # Update wrapper scripts
    echo -e "${BOLD}Updating wrapper scripts...${NC}"
    for wrapper in "$target/bin/audiobooks-api" \
                   "$target/bin/audiobooks" \
                   "/usr/local/bin/audiobooks-api" \
                   "$HOME/.local/bin/audiobooks-api"; do
        if [[ -f "$wrapper" ]]; then
            update_wrapper_script "$wrapper" "api.py" $DRY_RUN
        fi
    done
    echo ""

    # Update systemd services
    echo -e "${BOLD}Checking systemd services...${NC}"
    for service in /etc/systemd/system/audiobooks-api.service \
                   "$HOME/.config/systemd/user/audiobooks-api.service"; do
        if [[ -f "$service" ]]; then
            update_systemd_service "$service" "api.py" $DRY_RUN
        fi
    done
    echo ""

    if ! $DRY_RUN; then
        # Reload systemd if needed
        if [[ -f /etc/systemd/system/audiobooks-api.service ]]; then
            echo -e "${BOLD}Reloading systemd...${NC}"
            sudo systemctl daemon-reload
        fi

        if [[ -f "$HOME/.config/systemd/user/audiobooks-api.service" ]]; then
            systemctl --user daemon-reload
        fi

        echo ""
        echo -e "${GREEN}╔═══════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║                    Migration Complete!                            ║${NC}"
        echo -e "${GREEN}╚═══════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo "The API is now using the monolithic architecture."
        echo ""
        echo "To restart the service:"
        echo "  sudo systemctl restart audiobooks-api"
        echo ""
        echo "To switch to modular:"
        echo "  ./migrate-api.sh --to-modular"

        # Verify permissions after migration
        verify_installation_permissions "$target"
    fi
}

# -----------------------------------------------------------------------------
# Post-Migration Verification
# -----------------------------------------------------------------------------

verify_installation_permissions() {
    # Verify that installed files have correct permissions and ownership
    local target_dir="$1"
    local issues_found=0

    echo ""
    echo -e "${BLUE}Verifying installation permissions and ownership...${NC}"

    # Determine if this is a system or user installation
    local is_system=false
    [[ "$target_dir" == /opt/* ]] || [[ "$target_dir" == /usr/* ]] && is_system=true

    # For system installations, verify ownership is audiobooks:audiobooks
    if [[ "$is_system" == "true" ]]; then
        echo -n "  Checking ownership (audiobooks:audiobooks)... "
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

    # Check directory permissions (should be 755)
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

    # Check file permissions (should be 644 for .py, .html, .css, .js)
    echo -n "  Checking file permissions... "
    local bad_files=$(find "$target_dir" \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 -o -perm 711 \) 2>/dev/null | wc -l)
    if [[ "$bad_files" -gt 0 ]]; then
        echo -e "${YELLOW}fixing $bad_files files${NC}"
        if [[ "$is_system" == "true" ]]; then
            sudo find "$target_dir" \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 -o -perm 711 \) -exec chmod 644 {} \;
        else
            find "$target_dir" \( -name "*.py" -o -name "*.html" -o -name "*.css" -o -name "*.js" \) \( -perm 600 -o -perm 700 -o -perm 711 \) -exec chmod 644 {} \;
        fi
        ((issues_found++))
    else
        echo -e "${GREEN}OK${NC}"
    fi

    if [[ "$issues_found" -gt 0 ]]; then
        echo -e "${YELLOW}  Fixed $issues_found permission/ownership issues.${NC}"
    else
        echo -e "${GREEN}  All permissions and ownership verified.${NC}"
    fi
}

# -----------------------------------------------------------------------------
# Parse Arguments
# -----------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --to-modular)
            ACTION="to-modular"
            shift
            ;;
        --to-monolithic)
            ACTION="to-monolithic"
            shift
            ;;
        --status)
            ACTION="status"
            shift
            ;;
        --explain)
            ACTION="explain"
            shift
            ;;
        --target)
            TARGET_DIR="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
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

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

case "$ACTION" in
    to-modular)
        migrate_to_modular
        ;;
    to-monolithic)
        migrate_to_monolithic
        ;;
    status)
        show_status
        ;;
    explain)
        explain_architectures
        ;;
    "")
        # No action - show interactive menu
        print_header
        echo -e "${BOLD}What would you like to do?${NC}"
        echo ""
        echo -e "  ${GREEN}1)${NC} Show current architecture status"
        echo -e "  ${GREEN}2)${NC} Explain the difference between architectures"
        echo -e "  ${GREEN}3)${NC} Migrate to modular architecture"
        echo -e "  ${GREEN}4)${NC} Migrate to monolithic architecture"
        echo -e "  ${GREEN}5)${NC} Exit"
        echo ""
        read -r -p "Enter your choice [1-5]: " choice

        case "$choice" in
            1) show_status ;;
            2) explain_architectures ;;
            3) migrate_to_modular ;;
            4) migrate_to_monolithic ;;
            5) echo "Exiting."; exit 0 ;;
            *) echo -e "${RED}Invalid choice.${NC}"; exit 1 ;;
        esac
        ;;
esac
