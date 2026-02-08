#!/usr/bin/env zsh
# install-hooks.sh - Install git hooks for the Audiobooks project
#
# Run this after cloning to set up commit safeguards:
#   ./scripts/install-hooks.sh
#
# Hooks installed:
#   - pre-commit: Blocks hardcoded paths (must use config variables)

set -e

SCRIPT_DIR="${0:A:h}"
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

        # Check if hook already exists
        if [[ -f "$dest" ]]; then
            # Check if it's the same
            if cmp -s "$hook" "$dest"; then
                echo -e "  ${GREEN}✓${NC} $hook_name (already installed)"
            else
                echo -e "  ${YELLOW}!${NC} $hook_name exists but differs - backing up and replacing"
                mv "$dest" "$dest.backup.$(date +%Y%m%d%H%M%S)"
                cp "$hook" "$dest"
                chmod +x "$dest"
            fi
        else
            cp "$hook" "$dest"
            chmod +x "$dest"
            echo -e "  ${GREEN}✓${NC} $hook_name installed"
        fi
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
