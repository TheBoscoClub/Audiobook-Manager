#!/bin/bash
# install-hooks.sh - Install git hooks for the Audiobooks project
#
# Run this after cloning to set up commit safeguards:
#   ./scripts/install-hooks.sh
#
# Hooks installed:
#   - pre-commit: Blocks hardcoded paths (must use config variables)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
HOOKS_SOURCE="$SCRIPT_DIR/hooks"
HOOKS_DEST="$PROJECT_ROOT/.git/hooks"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "Installing git hooks for Audiobooks project..."
echo ""

# Ensure we're in a git repository
if [[ ! -d "$PROJECT_ROOT/.git" ]]; then
    echo "Error: Not a git repository. Run this from the project root."
    exit 1
fi

# Ensure hooks source directory exists
if [[ ! -d "$HOOKS_SOURCE" ]]; then
    echo "Error: Hooks source directory not found: $HOOKS_SOURCE"
    exit 1
fi

# Install each hook
installed=0
for hook in "$HOOKS_SOURCE"/*; do
    if [[ -f "$hook" ]]; then
        hook_name=$(basename "$hook")
        dest="$HOOKS_DEST/$hook_name"

        # Another tool (beads) appends a managed block to the installed hook.
        # Carry it over so replacing the hook does not silently drop it.
        preserved=""
        if [[ -f "$dest" ]]; then
            preserved=$(awk '
                /^# --- BEGIN BEADS INTEGRATION/ { keep = 1 }
                keep { print }
                /^# --- END BEADS INTEGRATION/ { keep = 0 }' "$dest")
        fi

        candidate=$(mktemp)
        cat "$hook" >"$candidate"
        if [[ -n "$preserved" ]]; then
            printf '\n%s\n' "$preserved" >>"$candidate"
        fi

        if [[ -f "$dest" ]]; then
            if cmp -s "$candidate" "$dest"; then
                echo -e "  ${GREEN}✓${NC} $hook_name (already installed)"
            else
                echo -e "  ${YELLOW}!${NC} $hook_name exists but differs - backing up and replacing"
                mv "$dest" "$dest.backup.$(date +%Y%m%d%H%M%S)"
                cp "$candidate" "$dest"
                chmod +x "$dest"
                if [[ -n "$preserved" ]]; then
                    echo -e "    preserved the BEADS INTEGRATION block"
                fi
            fi
        else
            cp "$candidate" "$dest"
            chmod +x "$dest"
            echo -e "  ${GREEN}✓${NC} $hook_name installed"
        fi
        rm -f "$candidate"
        installed=$((installed + 1))
    fi
done

echo ""
if [[ $installed -eq 0 ]]; then
    echo "No hooks found to install."
else
    echo -e "${GREEN}Done!${NC} Installed $installed hook(s)."
    echo ""
    echo "Hooks will now enforce project coding standards on commit."
    echo "See CONTRIBUTING.md for details on the 'No Hardcoded Paths' rule."
fi
