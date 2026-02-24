#!/bin/bash
# Setup script for magic link email functionality
# Uses Protonmail Bridge for SMTP access to thebosco.club

set -e

echo "=== Protonmail Bridge Setup for Magic Link Emails ==="
echo ""

# Check if bridge is installed (core binary or symlink at /usr/local/bin)
if ! command -v protonmail-bridge-core &>/dev/null; then
    echo "ERROR: protonmail-bridge-core not found"
    echo "Install protonmail-bridge-bin (AUR) or protonmail-bridge-core (extra),"
    echo "then ensure /usr/local/bin/protonmail-bridge-core points to the core binary."
    exit 1
fi

# Check for existing configuration (check both possible vault locations)
if [[ -f ~/.config/protonmail/bridge-v3/vault.enc ]] || [[ -f ~/.local/share/protonmail/bridge-v3/vault.enc ]]; then
    echo "Protonmail Bridge already configured."
    echo ""

    # Check if service is running
    if systemctl --user is-active protonmail-bridge &>/dev/null; then
        echo "Bridge service is running."
    else
        echo "Starting bridge service..."
        systemctl --user enable --now protonmail-bridge
    fi

    echo ""
    echo "To view credentials: protonmail-bridge-core --cli"
    echo "Then type 'info' to see SMTP credentials."
    exit 0
fi

echo "First-time setup required."
echo ""
echo "Steps:"
echo "1. Run: protonmail-bridge-core --cli"
echo "2. Login with your Protonmail credentials"
echo "3. After login, type 'info' to get SMTP credentials"
echo "4. Update /etc/audiobooks/audiobooks.conf with:"
echo "   SMTP_USER=<your bridge username>"
echo "   SMTP_PASS=<your bridge password>"
echo "5. Enable the bridge service:"
echo "   systemctl --user enable --now protonmail-bridge"
echo "6. Restart the audiobooks API:"
echo "   sudo systemctl restart audiobook-api"
echo ""

read -r -k 1 "REPLY?Start interactive setup now? [y/N] "
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Starting Protonmail Bridge CLI..."
    echo "Type 'login' to start, then 'info' after login for credentials."
    echo ""
    protonmail-bridge-core --cli
fi
