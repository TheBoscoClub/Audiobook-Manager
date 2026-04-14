#!/bin/bash
# Config migration 002: Strip legacy path overrides (v8.1.0.1+)
#
# install.sh historically wrote hardcoded path overrides into audiobooks.conf
# that have since drifted from the canonical defaults in library/config.py
# and lib/audiobook-config.sh. Existing installs carry the drifted lines and
# produce split-brain bugs where bash-sourced scripts and the Python app
# compute different paths for the same key.
#
# Drifted keys handled here:
#   AUDIOBOOKS_COVERS     legacy: ${AUDIOBOOKS_HOME}/library/{web-v2/,}covers
#                         canonical: /var/lib/audiobooks/covers
#   AUDIOBOOKS_DATABASE   legacy: /var/lib/audiobooks/audiobooks.db (flat)
#                         canonical: /var/lib/audiobooks/db/audiobooks.db
#   AUDIOBOOKS_VENV       legacy: ${AUDIOBOOKS_HOME}/library/venv
#                         canonical: (unset — falls through to config.py default)
#   AUDIOBOOKS_CERTS      legacy: ${AUDIOBOOKS_HOME}/library/certs
#                         canonical: ${CONFIG_DIR}/certs (install.sh writes this)
#
# User-customized values (anything not matching the legacy glob) are preserved.
# Matches the canonical drift list in scripts/install-manifest.sh
# (CONFIG_CANONICAL_DEFAULTS).
#
# Idempotent: runs cleanly on configs that have already been cleaned.

# shellcheck disable=SC2154  # CONF_FILE, USE_SUDO, DRY_RUN set by caller

_strip_key() {
    local key="$1"
    shift
    local -a legacy_globs=("$@")
    local current_value
    current_value=$(grep -oP "^${key}=\K.*" "$CONF_FILE" 2>/dev/null | tr -d '"' || true)

    if [[ -z "$current_value" ]]; then
        return 0
    fi

    local match="false"
    local glob
    for glob in "${legacy_globs[@]}"; do
        # shellcheck disable=SC2053  # intentional glob match
        if [[ "$current_value" == $glob ]]; then
            match="true"
            break
        fi
    done

    if [[ "$match" != "true" ]]; then
        # User-customized — preserve
        return 0
    fi

    echo "  Stripping legacy ${key} override ($current_value)"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] Would remove ${key} line from $CONF_FILE"
        return 0
    fi

    if [[ -n "$USE_SUDO" ]]; then
        sudo sed -i.bak002 "/^${key}=/d" "$CONF_FILE"
    else
        sed -i.bak002 "/^${key}=/d" "$CONF_FILE"
    fi

    echo "  Removed legacy ${key} line (backup: ${CONF_FILE}.bak002)"
}

_strip_key "AUDIOBOOKS_COVERS"   '*library/web-v2/covers' '*library/covers'
_strip_key "AUDIOBOOKS_DATABASE" '/var/lib/audiobooks/audiobooks.db'
_strip_key "AUDIOBOOKS_VENV"     '*library/venv'
_strip_key "AUDIOBOOKS_CERTS"    '*library/certs'

# Ensure canonical dirs exist for the keys we may have stripped
if [[ "$DRY_RUN" != "true" ]]; then
    if [[ -n "$USE_SUDO" ]]; then
        sudo mkdir -p /var/lib/audiobooks/covers /var/lib/audiobooks/db
        sudo chown audiobooks:audiobooks /var/lib/audiobooks/covers /var/lib/audiobooks/db 2>/dev/null || true
    else
        mkdir -p /var/lib/audiobooks/covers /var/lib/audiobooks/db 2>/dev/null || true
    fi
fi
