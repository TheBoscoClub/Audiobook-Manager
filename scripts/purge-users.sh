#!/usr/bin/env zsh
# purge-users.sh — Delete all users and access requests NOT in keep list
# Idempotent: safe to re-run after upgrades
#
# Usage:
#   ./scripts/purge-users.sh --host 192.168.122.104 --keep bosco,frisco,admin,claudecode
#   ./scripts/purge-users.sh --host 192.168.122.104  # uses default keep list

set -euo pipefail

# Defaults
KEEP_LIST="bosco,frisco,admin,claudecode"
HOST=""
TOTP_SECRET_FILE="${HOME}/.claude/secrets/totp-secret"
ADMIN_USER="claudecode"
PROTOCOL="https"
PORT="8443"
DRY_RUN=false

usage() {
    cat <<EOF
Usage: $(basename "$0") --host <ip> [--keep user1,user2,...] [--dry-run]

Options:
  --host       VM host IP or hostname (required)
  --keep       Comma-separated usernames to keep (default: ${KEEP_LIST})
  --port       API port (default: ${PORT})
  --protocol   http or https (default: ${PROTOCOL})
  --dry-run    Show what would be deleted without deleting
  -h, --help   Show this help
EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)     HOST="$2"; shift 2 ;;
        --keep)     KEEP_LIST="$2"; shift 2 ;;
        --port)     PORT="$2"; shift 2 ;;
        --protocol) PROTOCOL="$2"; shift 2 ;;
        --dry-run)  DRY_RUN=true; shift ;;
        -h|--help)  usage ;;
        *)          echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$HOST" ]]; then
    echo "Error: --host is required"
    usage
fi

BASE_URL="${PROTOCOL}://${HOST}:${PORT}"
COOKIE_JAR=$(mktemp)
trap 'rm -f "$COOKIE_JAR"' EXIT

# Convert keep list to array
IFS=',' read -rA KEEP_USERS <<< "$KEEP_LIST"

is_kept() {
    local username="$1"
    for kept in "${KEEP_USERS[@]}"; do
        [[ "$username" == "$kept" ]] && return 0
    done
    return 1
}

# Step 1: Login as claudecode admin via TOTP
if [[ ! -f "$TOTP_SECRET_FILE" ]]; then
    echo "Error: TOTP secret not found at $TOTP_SECRET_FILE"
    exit 1
fi

TOTP_SECRET=$(cat "$TOTP_SECRET_FILE")
TOTP_CODE=$(python3 -c "import pyotp; print(pyotp.TOTP('${TOTP_SECRET}').now())")

echo "Logging in as ${ADMIN_USER}..."
LOGIN_RESP=$(curl -sk -c "$COOKIE_JAR" -X POST "${BASE_URL}/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${ADMIN_USER}\",\"code\":\"${TOTP_CODE}\"}" 2>&1)

if ! echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" 2>/dev/null; then
    echo "Login failed: $LOGIN_RESP"
    exit 1
fi
echo "Login successful."

# Step 2: List all users
echo ""
echo "Fetching users..."
USERS_RESP=$(curl -sk -b "$COOKIE_JAR" "${BASE_URL}/auth/admin/users" 2>&1)
TOTAL=$(echo "$USERS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
echo "Found ${TOTAL} users."

# Step 3: Delete non-kept users
DELETED=0
KEPT=0
echo "$USERS_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for u in data['users']:
    print(f\"{u['id']}|{u['username']}\")
" | while IFS='|' read -r uid uname; do
    if is_kept "$uname"; then
        echo "  KEEP: ${uname} (id=${uid})"
        ((KEPT++)) || true
    else
        if $DRY_RUN; then
            echo "  WOULD DELETE: ${uname} (id=${uid})"
        else
            DEL_RESP=$(curl -sk -b "$COOKIE_JAR" -X DELETE "${BASE_URL}/auth/admin/users/${uid}" 2>&1)
            echo "  DELETED: ${uname} (id=${uid}) — ${DEL_RESP}"
        fi
        ((DELETED++)) || true
    fi
done

# Step 4: List and purge access requests for non-kept users
echo ""
echo "Fetching access requests..."
AR_RESP=$(curl -sk -b "$COOKIE_JAR" "${BASE_URL}/auth/admin/access-requests" 2>&1)
AR_COUNT=$(echo "$AR_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('requests',[])))" 2>/dev/null || echo "0")
echo "Found ${AR_COUNT} access requests."

if [[ "$AR_COUNT" -gt 0 ]]; then
    echo "$AR_RESP" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('requests', []):
    print(f\"{r['id']}|{r['username']}\")
" | while IFS='|' read -r rid rname; do
        if is_kept "$rname"; then
            echo "  KEEP REQUEST: ${rname} (id=${rid})"
        else
            if $DRY_RUN; then
                echo "  WOULD DELETE REQUEST: ${rname} (id=${rid})"
            else
                DEL_RESP=$(curl -sk -b "$COOKIE_JAR" -X DELETE "${BASE_URL}/auth/admin/access-requests/${rid}" 2>&1)
                echo "  DELETED REQUEST: ${rname} (id=${rid}) — ${DEL_RESP}"
            fi
        fi
    done
fi

# Step 5: Verify
echo ""
if ! $DRY_RUN; then
    echo "Verification — remaining users:"
    curl -sk -b "$COOKIE_JAR" "${BASE_URL}/auth/admin/users" 2>&1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
for u in data['users']:
    print(f\"  {u['username']} (id={u['id']}, admin={u['is_admin']})\")
print(f\"Total: {data['total']}\")" 2>/dev/null
fi

echo ""
echo "Done."
