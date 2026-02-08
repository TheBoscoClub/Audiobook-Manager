#!/usr/bin/env zsh
# Setup script for magic link email functionality
# Uses Protonmail Bridge for SMTP access to thebosco.club

set -e

echo "=== Protonmail Bridge Setup for Magic Link Emails ==="
echo ""

# Check if bridge is installed
if ! command -v protonmail-bridge-core &> /dev/null; then
    echo "ERROR: protonmail-bridge-core not installed"
    echo "Install with: sudo pacman -S protonmail-bridge-core"
    exit 1
fi

# Check for existing configuration
if [ -f ~/.local/share/protonmail/bridge-v3/vault.enc ]; then
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

read -p "Start interactive setup now? [y/N] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Starting Protonmail Bridge CLI..."
    echo "Type 'login' to start, then 'info' after login for credentials."
    echo ""
    protonmail-bridge-core --cli
fi
