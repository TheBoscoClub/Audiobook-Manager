#!/bin/bash
# =============================================================================
# Audiobook Library - Shell Configuration Loader
# =============================================================================
# Source this file in shell scripts to load configuration:
#   source /usr/local/lib/audiobooks/audiobooks-config.sh
#   source "${AUDIOBOOKS_HOME}/lib/audiobooks-config.sh"
#
# Configuration priority (later overrides earlier):
#   1. Built-in defaults
#   2. /etc/audiobooks/audiobooks.conf (system config)
#   3. ~/.config/audiobooks/audiobooks.conf (user config)
#   4. Environment variables (already set before sourcing)
# =============================================================================

# Prevent multiple sourcing
[[ -n "${_AUDIOBOOKS_CONFIG_LOADED:-}" ]] && return 0
_AUDIOBOOKS_CONFIG_LOADED=1

# -----------------------------------------------------------------------------
# Helper: Load config file if it exists
# -----------------------------------------------------------------------------
_load_config_file() {
    local config_file="$1"
    [[ -f "$config_file" ]] || return 0

    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue

        # Clean up key and value
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")

        # Only set if not already set in environment (portable syntax)
        eval "current_val=\"\${$key:-}\""
        if [[ -z "$current_val" ]]; then
            # Expand variables in value
            value=$(eval echo "$value")
            export "$key=$value"
        fi
    done < "$config_file"
}

# -----------------------------------------------------------------------------
# Detect AUDIOBOOKS_HOME if not set
# -----------------------------------------------------------------------------
if [[ -z "${AUDIOBOOKS_HOME:-}" ]]; then
    # Try to detect from this script's location
    if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
        _script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        # If we're in lib/, go up one level
        if [[ "$(basename "$_script_dir")" == "lib" ]]; then
            AUDIOBOOKS_HOME="$(dirname "$_script_dir")"
        else
            AUDIOBOOKS_HOME="$_script_dir"
        fi
        export AUDIOBOOKS_HOME
        unset _script_dir
    fi
fi

# -----------------------------------------------------------------------------
# Load configuration files
# -----------------------------------------------------------------------------

# System config first
_load_config_file "/etc/audiobooks/audiobooks.conf"

# User config second (overrides system)
_load_config_file "${HOME}/.config/audiobooks/audiobooks.conf"

# Legacy config.env in project root (for backwards compatibility)
[[ -n "${AUDIOBOOKS_HOME:-}" ]] && _load_config_file "${AUDIOBOOKS_HOME}/config.env"

# -----------------------------------------------------------------------------
# Set defaults for any unset variables
# -----------------------------------------------------------------------------

# Core data directory
: "${AUDIOBOOKS_DATA:=/srv/audiobooks}"

# Data subdirectories
: "${AUDIOBOOKS_LIBRARY:=${AUDIOBOOKS_DATA}/Library}"
: "${AUDIOBOOKS_SOURCES:=${AUDIOBOOKS_DATA}/Sources}"
: "${AUDIOBOOKS_SUPPLEMENTS:=${AUDIOBOOKS_DATA}/Supplements}"
: "${AUDIOBOOKS_LOGS:=${AUDIOBOOKS_DATA}/logs}"

# Application directories (use AUDIOBOOKS_HOME if set)
if [[ -n "${AUDIOBOOKS_HOME:-}" ]]; then
    : "${AUDIOBOOKS_DATABASE:=${AUDIOBOOKS_HOME}/library/backend/audiobooks.db}"
    : "${AUDIOBOOKS_COVERS:=${AUDIOBOOKS_HOME}/library/web-v2/covers}"
    : "${AUDIOBOOKS_CERTS:=${AUDIOBOOKS_HOME}/library/certs}"
    : "${AUDIOBOOKS_VENV:=${AUDIOBOOKS_HOME}/library/venv}"
    : "${AUDIOBOOKS_CONVERTER:=${AUDIOBOOKS_HOME}/converter/AAXtoMP3}"
else
    : "${AUDIOBOOKS_DATABASE:=/var/lib/audiobooks/audiobooks.db}"
    : "${AUDIOBOOKS_COVERS:=/var/lib/audiobooks/covers}"
    : "${AUDIOBOOKS_CERTS:=/etc/audiobooks/certs}"
    : "${AUDIOBOOKS_VENV:=/opt/audiobooks/venv}"
    : "${AUDIOBOOKS_CONVERTER:=/usr/local/bin/AAXtoMP3}"
fi

# Server settings
: "${AUDIOBOOKS_API_PORT:=5001}"
: "${AUDIOBOOKS_WEB_PORT:=8443}"  # HTTPS port (changed from 8090)
: "${AUDIOBOOKS_HTTP_REDIRECT_PORT:=8080}"  # HTTP to HTTPS redirect port
: "${AUDIOBOOKS_BIND_ADDRESS:=0.0.0.0}"
: "${AUDIOBOOKS_HTTPS_ENABLED:=true}"
: "${AUDIOBOOKS_USE_WAITRESS:=true}"  # Use waitress WSGI server (production mode)

# Export all variables
export AUDIOBOOKS_DATA AUDIOBOOKS_LIBRARY AUDIOBOOKS_SOURCES AUDIOBOOKS_SUPPLEMENTS
export AUDIOBOOKS_HOME AUDIOBOOKS_DATABASE AUDIOBOOKS_COVERS AUDIOBOOKS_CERTS
export AUDIOBOOKS_LOGS AUDIOBOOKS_VENV AUDIOBOOKS_CONVERTER
export AUDIOBOOKS_API_PORT AUDIOBOOKS_WEB_PORT AUDIOBOOKS_HTTP_REDIRECT_PORT AUDIOBOOKS_BIND_ADDRESS AUDIOBOOKS_HTTPS_ENABLED AUDIOBOOKS_USE_WAITRESS

# -----------------------------------------------------------------------------
# Legacy variable mapping (for backwards compatibility)
# -----------------------------------------------------------------------------
# Map old variable names to new ones
export PROJECT_DIR="${AUDIOBOOKS_HOME:-}"
export AUDIOBOOK_DIR="${AUDIOBOOKS_LIBRARY:-}"
export DATABASE_PATH="${AUDIOBOOKS_DATABASE:-}"
export COVER_DIR="${AUDIOBOOKS_COVERS:-}"
export SOURCES_DIR="${AUDIOBOOKS_SOURCES:-}"
export SUPPLEMENTS_DIR="${AUDIOBOOKS_SUPPLEMENTS:-}"
export API_PORT="${AUDIOBOOKS_API_PORT:-}"
export WEB_PORT="${AUDIOBOOKS_WEB_PORT:-}"

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------

# Print current configuration
audiobooks_print_config() {
    echo "Audiobook Library Configuration"
    echo "================================"
    echo "AUDIOBOOKS_HOME:        ${AUDIOBOOKS_HOME:-<not set>}"
    echo "AUDIOBOOKS_DATA:        ${AUDIOBOOKS_DATA}"
    echo "AUDIOBOOKS_LIBRARY:     ${AUDIOBOOKS_LIBRARY}"
    echo "AUDIOBOOKS_SOURCES:     ${AUDIOBOOKS_SOURCES}"
    echo "AUDIOBOOKS_SUPPLEMENTS: ${AUDIOBOOKS_SUPPLEMENTS}"
    echo "AUDIOBOOKS_DATABASE:    ${AUDIOBOOKS_DATABASE}"
    echo "AUDIOBOOKS_COVERS:      ${AUDIOBOOKS_COVERS}"
    echo "AUDIOBOOKS_CERTS:       ${AUDIOBOOKS_CERTS}"
    echo "AUDIOBOOKS_LOGS:        ${AUDIOBOOKS_LOGS}"
    echo "AUDIOBOOKS_VENV:        ${AUDIOBOOKS_VENV}"
    echo "AUDIOBOOKS_CONVERTER:   ${AUDIOBOOKS_CONVERTER}"
    echo "AUDIOBOOKS_API_PORT:    ${AUDIOBOOKS_API_PORT}"
    echo "AUDIOBOOKS_WEB_PORT:    ${AUDIOBOOKS_WEB_PORT} (HTTPS)"
    echo "AUDIOBOOKS_HTTP_REDIRECT_PORT: ${AUDIOBOOKS_HTTP_REDIRECT_PORT}"
    echo "AUDIOBOOKS_BIND_ADDRESS: ${AUDIOBOOKS_BIND_ADDRESS}"
    echo "AUDIOBOOKS_HTTPS_ENABLED: ${AUDIOBOOKS_HTTPS_ENABLED}"
    echo "AUDIOBOOKS_USE_WAITRESS: ${AUDIOBOOKS_USE_WAITRESS}"
    echo "================================"
}

# Verify required directories exist
audiobooks_check_dirs() {
    local missing=0
    local dirs=(
        "$AUDIOBOOKS_LIBRARY"
        "$AUDIOBOOKS_SOURCES"
        "$(dirname "$AUDIOBOOKS_DATABASE")"
    )

    for dir in "${dirs[@]}"; do
        if [[ ! -d "$dir" ]]; then
            echo "Warning: Directory does not exist: $dir" >&2
            ((missing++))
        fi
    done

    return $missing
}

# Get Python interpreter from venv
audiobooks_python() {
    if [[ -x "${AUDIOBOOKS_VENV}/bin/python" ]]; then
        echo "${AUDIOBOOKS_VENV}/bin/python"
    elif [[ -x "${AUDIOBOOKS_VENV}/bin/python3" ]]; then
        echo "${AUDIOBOOKS_VENV}/bin/python3"
    else
        echo "python3"
    fi
}

# Run if executed directly (for testing)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    audiobooks_print_config
fi
