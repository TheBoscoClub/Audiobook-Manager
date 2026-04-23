#!/bin/bash
# bump-cachebust.sh — Rewrite ?v=<digits> cachebust stamps in HTML entrypoints.
#
# EXCEPTION (per upgrade-consistency.md "New-Script Wiring Enforcement"):
# This is a standalone build-time helper invoked directly by install.sh and
# upgrade.sh (shell-out) immediately before the web service restarts. It is
# an internal deploy helper — NOT a long-running service and NOT a user-facing
# CLI. Therefore it intentionally has:
#   - No systemd unit (not a daemon, not timer-driven, runs synchronously)
#   - No symlink from /usr/local/bin (internal to the install/upgrade flow,
#     never run by end users or operators directly)
#   - No install-manifest.sh entry for a system location (it is copied into
#     /opt/audiobooks/scripts/ alongside the other internal helpers and lives
#     only as a source-of-truth file shipped with the install)
# Documented exception per upgrade-consistency.md new-script wiring rule.
#
# Run by upgrade.sh and install.sh after HTML files have been synced into the
# target and before the web service restarts. Fixes the recurring
# "stale browsers run old JS" bug that previously required manual bumps of
# index.html / shell.html / utilities.html in every commit that touched JS or
# CSS. One stamp per deploy, applied to every *.html under web-v2/.
#
# Previous manifestation the automation prevents: v8.3.4 qalib.thebosco.club
# 2000-ID URL-overflow 400 — caused by Qing's browser still running an old
# library.js from a prior deploy because the cachebust stamps in index.html
# had not been updated. That class of incident is what this script makes
# impossible by construction.
#
# Usage:
#   bump-cachebust.sh [STAMP] [TARGET_DIR]
#     STAMP      — cachebust value to write (default: $(date +%s))
#     TARGET_DIR — directory containing the HTML files (default: web-v2 dir
#                  resolved relative to the script location)
#
# Exit codes:
#   0 — success (even if 0 files needed a bump — idempotent by design)
#   2 — TARGET_DIR missing or unreadable
#
# Contract:
#   - Only files under TARGET_DIR/*.html (depth 1) are touched. Nested HTML
#     fragments (e.g. tutorial step partials) are intentionally excluded —
#     they get re-requested without a cachebust by the browser anyway.
#   - Rewrites are atomic: we write to a temp file in the same dir and
#     rename-over the original. A partial disk-full or kill -9 mid-sed can
#     not leave a half-rewritten HTML file in place.
#   - Idempotent: running twice with the same STAMP is a no-op, with a new
#     STAMP just updates the references.

set -uo pipefail

STAMP="${1:-$(date +%s)}"

# Default TARGET_DIR: library/web-v2 relative to this script's install path.
# scripts/ sits next to library/ in both the installed layout
# (/opt/audiobooks/scripts/ + /opt/audiobooks/library/web-v2/) and the
# project tree.
if [[ -n "${2:-}" ]]; then
    TARGET_DIR="$2"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    TARGET_DIR="${SCRIPT_DIR}/../library/web-v2"
fi

if [[ ! -d "$TARGET_DIR" ]]; then
    echo "ERROR: TARGET_DIR not found: $TARGET_DIR" >&2
    exit 2
fi

# Validate STAMP — only digits, dots, or short SHAs are allowed in the
# output. Defense against command injection if the caller passes something
# weird as $1.
if [[ ! "$STAMP" =~ ^[a-zA-Z0-9._-]{1,32}$ ]]; then
    echo "ERROR: invalid stamp (must match [a-zA-Z0-9._-]{1,32}): $STAMP" >&2
    exit 2
fi

bumped=0
skipped=0
for file in "$TARGET_DIR"/*.html; do
    [[ -f "$file" ]] || continue
    # grep -q exit codes: 0=match, 1=no match, 2=error
    if ! grep -q '?v=[A-Za-z0-9._-]\+' "$file" 2>/dev/null; then
        skipped=$((skipped + 1))
        continue
    fi
    tmp=$(mktemp "${file}.XXXXXX")
    # Write new content to tmp, then atomic rename.
    if ! sed "s/?v=[A-Za-z0-9._-]\+/?v=${STAMP}/g" "$file" >"$tmp"; then
        rm -f "$tmp"
        echo "ERROR: sed failed on $file" >&2
        exit 1
    fi
    # Preserve mode + owner from the original.
    chmod --reference="$file" "$tmp" 2>/dev/null || true
    chown --reference="$file" "$tmp" 2>/dev/null || true
    mv "$tmp" "$file"
    bumped=$((bumped + 1))
done

echo "cachebust: stamp=${STAMP} bumped=${bumped} skipped=${skipped} dir=${TARGET_DIR}"
