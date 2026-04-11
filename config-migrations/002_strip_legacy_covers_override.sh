#!/bin/bash
# Config migration 002: Strip legacy AUDIOBOOKS_COVERS override (v8.1.0.1)
#
# install.sh historically wrote AUDIOBOOKS_COVERS="${AUDIOBOOKS_HOME}/library/
# web-v2/covers" into audiobooks.conf. That path was removed from the running
# code months ago — library/config.py now defaults to /var/lib/audiobooks/covers.
# Existing installs still carry the drifted line and serve 404s for every cover.
#
# This migration removes the legacy override so the code default takes effect.
# If a user has set AUDIOBOOKS_COVERS to a non-legacy path, it is preserved.
#
# Idempotent: runs cleanly on configs that have already been cleaned.

# shellcheck disable=SC2154  # CONF_FILE, USE_SUDO, DRY_RUN set by caller

current_value=$(grep -oP '^AUDIOBOOKS_COVERS=\K.*' "$CONF_FILE" 2>/dev/null | tr -d '"' || true)

if [[ -z "$current_value" ]]; then
    return 0
fi

# Match the legacy path patterns — both ${AUDIOBOOKS_HOME}-interpolated
# and the unexpanded literal.
case "$current_value" in
    *library/web-v2/covers | *library/covers)
        ;;
    *)
        # Non-legacy value — user has customized, preserve it.
        return 0
        ;;
esac

echo "  Stripping legacy AUDIOBOOKS_COVERS override ($current_value)"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] Would remove AUDIOBOOKS_COVERS line from $CONF_FILE"
    return 0
fi

if [[ -n "$USE_SUDO" ]]; then
    sudo sed -i.bak002 '/^AUDIOBOOKS_COVERS=/d' "$CONF_FILE"
    # Ensure canonical covers dir exists
    sudo mkdir -p /var/lib/audiobooks/covers
    sudo chown audiobooks:audiobooks /var/lib/audiobooks/covers 2>/dev/null || true
else
    sed -i.bak002 '/^AUDIOBOOKS_COVERS=/d' "$CONF_FILE"
    mkdir -p /var/lib/audiobooks/covers 2>/dev/null || true
fi

echo "  Removed legacy AUDIOBOOKS_COVERS line (backup: ${CONF_FILE}.bak002)"
echo "  AUDIOBOOKS_COVERS now falls through to library/config.py default (/var/lib/audiobooks/covers)"
