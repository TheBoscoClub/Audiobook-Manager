#!/usr/bin/env zsh
# Cleanup script for Phase M sandbox
SANDBOX_DIR=$(dirname "$(realpath "$0")")
echo "Cleaning up sandbox at: $SANDBOX_DIR"
echo ""
echo "Contents to be removed:"
du -sh "$SANDBOX_DIR"
echo ""
read -r "REPLY?Remove this sandbox? (y/n): "
if [[ "$REPLY" == "y" ]]; then
  rm -rf "$SANDBOX_DIR"
  echo "Sandbox removed."
else
  echo "Cleanup cancelled."
fi
