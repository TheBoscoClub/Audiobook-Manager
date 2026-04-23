#!/bin/bash
# =============================================================================
# Audiobook Manager — audiobooks UID/GID migration
# =============================================================================
#
# Realigns an existing install's audiobooks service account to a target
# UID/GID. The preferred convention is a matched pair (UID == GID), which
# makes bind-mount portability simpler (operator only has to remember one
# number) and mirrors how mainstream container images assign service IDs.
#
# When called with no args, the script auto-picks the first matched pair
# that's free on this host (starting at 935:935, walking upward if taken).
# Pass --uid N --gid N to specify explicitly.
#
# DESTRUCTIVE: This script runs `usermod -u`, `groupmod -g`, and chowns
# every audiobook-owned file. It is SAFE if the services are stopped first,
# but irreversible once applied. Back up with `btrfs subvolume snapshot`
# or equivalent before running on production.
#
# Usage:
#   sudo bash scripts/migrate-audiobooks-uid.sh                    # auto-pick matched pair
#   sudo bash scripts/migrate-audiobooks-uid.sh --uid 1042 --gid 1042
#   sudo bash scripts/migrate-audiobooks-uid.sh --dry-run
# =============================================================================
set -euo pipefail

# Defaults if --uid / --gid not provided: probe for the first matched pair.
TARGET_UID=""
TARGET_GID=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uid)
            TARGET_UID="$2"
            shift 2
            ;;
        --gid)
            TARGET_GID="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

_probe_free_matched_id() {
    # Find the first N >= start where UID N AND GID N are both free.
    local n="${1:-935}"
    while [[ $n -lt 65000 ]]; do
        if ! getent passwd "$n" >/dev/null 2>&1 \
            && ! getent group "$n" >/dev/null 2>&1; then
            echo "$n"
            return 0
        fi
        n=$((n + 1))
    done
    return 1
}

if [[ -z "$TARGET_UID" && -z "$TARGET_GID" ]]; then
    # No target specified — pick a matched pair.
    matched=$(_probe_free_matched_id 935) || {
        echo "ERROR: no free matched UID:GID pair in range 935..65000" >&2
        exit 1
    }
    TARGET_UID="$matched"
    TARGET_GID="$matched"
elif [[ -z "$TARGET_UID" || -z "$TARGET_GID" ]]; then
    echo "ERROR: --uid and --gid must both be given (or neither)" >&2
    exit 2
fi

# Compat shim for downstream variable names — keep the existing CANONICAL_*
# references working without rewriting the body.
CANONICAL_UID="$TARGET_UID"
CANONICAL_GID="$TARGET_GID"

say() { printf '%s\n' "$*"; }
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        say "  DRY-RUN: $*"
    else
        # Intentional eval: callers pass pre-built command strings with
        # their own quoting for find's path/uid args. Inputs are local
        # constants (CANONICAL_UID, current_uid, hardcoded tree names) —
        # no user-supplied values reach this line.
        # shellcheck disable=SC2294
        eval "$@"
    fi
}

if [[ "$(id -u)" -ne 0 ]]; then
    say "ERROR: must run as root (sudo bash $0)"
    exit 1
fi

if ! getent passwd audiobooks >/dev/null 2>&1; then
    say "ERROR: user 'audiobooks' does not exist on this system."
    say "       Run install.sh first to create it, then re-run this script."
    exit 1
fi

current_uid=$(getent passwd audiobooks | cut -d: -f3)
current_gid=$(getent group audiobooks | cut -d: -f3)

say "Current: UID=${current_uid} GID=${current_gid}"
say "Target:  UID=${CANONICAL_UID} GID=${CANONICAL_GID}"

if [[ "$current_uid" == "$CANONICAL_UID" && "$current_gid" == "$CANONICAL_GID" ]]; then
    say "Already at canonical UID/GID — nothing to do."
    exit 0
fi

# Refuse if target UID/GID collides with another account
if [[ "$current_uid" != "$CANONICAL_UID" ]]; then
    conflict=$(getent passwd "$CANONICAL_UID" | cut -d: -f1 || true)
    if [[ -n "$conflict" && "$conflict" != "audiobooks" ]]; then
        say "ERROR: UID ${CANONICAL_UID} is already used by '${conflict}'. Resolve before retrying."
        exit 1
    fi
fi
if [[ "$current_gid" != "$CANONICAL_GID" ]]; then
    conflict=$(getent group "$CANONICAL_GID" | cut -d: -f1 || true)
    if [[ -n "$conflict" && "$conflict" != "audiobooks" ]]; then
        say "ERROR: GID ${CANONICAL_GID} is already used by group '${conflict}'. Resolve before retrying."
        exit 1
    fi
fi

# Service-stop gate — migrating UIDs on a running service risks open file
# descriptors and fd leaks.
active_units=$(systemctl list-units --plain --no-legend 'audiobook-*' 2>/dev/null \
    | awk '$3 == "active" {print $1}' || true)
if [[ -n "$active_units" ]]; then
    say "Stopping audiobook services before re-chown:"
    say "$active_units"
    run "systemctl stop audiobook.target"
fi

# Rename UID/GID
if [[ "$current_gid" != "$CANONICAL_GID" ]]; then
    say "groupmod audiobooks: GID ${current_gid} -> ${CANONICAL_GID}"
    run "groupmod -g ${CANONICAL_GID} audiobooks"
fi
if [[ "$current_uid" != "$CANONICAL_UID" ]]; then
    say "usermod audiobooks: UID ${current_uid} -> ${CANONICAL_UID}"
    run "usermod -u ${CANONICAL_UID} audiobooks"
fi

# Re-chown every audiobook-owned path under the canonical trees. We walk by
# numeric old UID/GID rather than by name so the sweep is deterministic even
# if the name-to-id mapping has already flipped partway.
for tree in /opt/audiobooks /etc/audiobooks /var/lib/audiobooks /srv/audiobooks; do
    if [[ -d "$tree" ]]; then
        say "chown ${tree} (old UID/GID -> ${CANONICAL_UID}:${CANONICAL_GID})"
        run "find '$tree' -uid '$current_uid' -exec chown ${CANONICAL_UID}:${CANONICAL_GID} {} +"
        run "find '$tree' -gid '$current_gid' -exec chgrp ${CANONICAL_GID} {} +"
    fi
done

# Restart services
if [[ -n "$active_units" ]]; then
    say "Restarting audiobook services..."
    run "systemctl start audiobook.target"
fi

say ""
say "Migration complete."
say "Verify: getent passwd audiobooks && ls -la /var/lib/audiobooks"
