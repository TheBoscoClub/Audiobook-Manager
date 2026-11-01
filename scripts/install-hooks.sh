#!/bin/bash
# install-hooks.sh - Install git hooks for the Audiobooks project
#
# Run this after cloning to set up commit safeguards:
#   ./scripts/install-hooks.sh
#
# Hooks installed (each with lib/, the installation-value guard they share):
#   - pre-commit: Blocks hardcoded paths (must use config variables) and, when the
#     gitignored .claude/local-scrub-map.txt exists, any staged installation value
#   - commit-msg: Strips AI/vendor promotional lines, then blocks a commit message
#     carrying an installation value
#   - pre-push: Blocks a push whose commits the destination lacks (messages,
#     paths, added lines), ref names or tags (names, messages) carry an
#     installation value - also covering commits made without the other hooks
#     (cherry-pick, rebase, am, --no-verify)
#
# LIVE-SYNC and BEADS INTEGRATION blocks other tools added to an installed hook
# are carried over; a rerun with nothing to change reports "already installed".

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

# Refuse before changing anything: an installed hook whose managed-block
# markers do not pair up cannot be carried over safely.
for hook in "$HOOKS_SOURCE"/*; do
    [[ -f "$hook" ]] || continue
    dest="$HOOKS_DEST/$(basename "$hook")"
    [[ -f "$dest" ]] || continue
    for marker in "BEADS INTEGRATION" "LIVE-SYNC"; do
        begins=$(grep -c "^# --- BEGIN $marker" "$dest" || true)
        ends=$(grep -c "^# --- END $marker" "$dest" || true)
        if [[ "$begins" != "$ends" ]]; then
            echo "Error: $dest has $begins BEGIN but $ends END $marker markers;" >&2
            echo "       refusing to guess which block to preserve. Fix the hook by hand and rerun." >&2
            exit 1
        fi
    done
done

# The shared guard library first, so no hook is ever installed without it.
if [[ -d "$HOOKS_SOURCE/lib" ]]; then
    mkdir -p "$HOOKS_DEST/lib"
    for lib in "$HOOKS_SOURCE/lib"/*; do
        [[ -f "$lib" ]] || continue
        lib_name="lib/$(basename "$lib")"
        if [[ -f "$HOOKS_DEST/$lib_name" ]] && cmp -s "$lib" "$HOOKS_DEST/$lib_name"; then
            echo -e "  ${GREEN}✓${NC} $lib_name (already installed)"
        else
            cp "$lib" "$HOOKS_DEST/$lib_name"
            chmod 644 "$HOOKS_DEST/$lib_name"
            echo -e "  ${GREEN}✓${NC} $lib_name installed"
        fi
    done
fi

# Install each hook
installed=0
for hook in "$HOOKS_SOURCE"/*; do
    if [[ -f "$hook" ]]; then
        hook_name=$(basename "$hook")
        dest="$HOOKS_DEST/$hook_name"

        # Other tools add managed blocks to the installed hook: live-sync puts
        # its commit gate right after the shebang, beads appends its block at the
        # end. Carry both over, in place, so replacing the hook drops neither.
        preserved=""
        preserved_head=""
        if [[ -f "$dest" ]]; then
            preserved=$(awk '
                /^# --- BEGIN BEADS INTEGRATION/ { keep = 1 }
                keep { print }
                /^# --- END BEADS INTEGRATION/ { keep = 0 }' "$dest")
            preserved_head=$(awk '
                /^# --- BEGIN LIVE-SYNC ---/ { keep = 1 }
                keep { print }
                /^# --- END LIVE-SYNC ---/ { keep = 0 }' "$dest")
        fi

        candidate=$(mktemp)
        if [[ -n "$preserved_head" ]]; then
            {
                head -n 1 "$hook"
                printf '%s\n' "$preserved_head"
                tail -n +2 "$hook"
            } >"$candidate"
        else
            cat "$hook" >"$candidate"
        fi
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
                if [[ -n "$preserved_head" ]]; then
                    echo -e "    preserved the LIVE-SYNC block"
                fi
                if [[ -n "$preserved" ]]; then
                    echo -e "    preserved the BEADS INTEGRATION block"
                fi
            fi
        else
            cp "$candidate" "$dest"
            chmod +x "$dest"
            echo -e "  ${GREEN}✓${NC} $hook_name installed"
        fi
        rm -f "${candidate:?}"
        installed=$((installed + 1))
    fi
done

echo ""
if [[ $installed -eq 0 ]]; then
    echo "No hooks found to install."
else
    echo -e "${GREEN}Done!${NC} Installed $installed hook(s)."
    echo ""
    echo "Hooks will now enforce project coding standards on commit and push."
    echo "See CONTRIBUTING.md for details on the 'No Hardcoded Paths' rule."
fi
