#!/bin/bash
# =============================================================================
# Audiobook-Manager Development User Management
# =============================================================================
# Wrapper for audiobook-user CLI in development mode.
# Uses dev database and key paths automatically.
#
# Usage:
#   ./dev/dev-user.sh list
#   ./dev/dev-user.sh add alice --totp --download
#   ./dev/dev-user.sh info alice
#   ./dev/dev-user.sh kick alice
#
# =============================================================================

set -e

# Get project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Dev database and key paths
AUTH_DB="$PROJECT_DIR/library/backend/auth-dev.db"
AUTH_KEY="$PROJECT_DIR/dev/auth-dev.key"

# Activate venv
cd "$PROJECT_DIR/library"
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found. Run start-dev.sh first."
    exit 1
fi

# Run CLI with dev paths
exec python auth/cli.py --dev -d "$AUTH_DB" -k "$AUTH_KEY" "$@"
