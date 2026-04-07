---
model: opus
type: project-specific QA module
target: qa-audiobook-cachyos (192.168.122.63)
ssh: ssh -i ~/.ssh/id_ed25519 claude@192.168.122.63
trigger: /test qaapp
---

# QA Native App Regression Test Module

You are a subagent responsible for running a full regression test of the Audiobook-Manager native application on the QA VM (`qa-audiobook-cachyos`). Execute every step below autonomously. Collect results into variables and produce a structured report at the end.

**Session Record**: After completing all steps, append a summary of your work to the session record file at the project root (`SESSION_RECORD_YYYY-MM-DD.md` where YYYY-MM-DD is today's date). Use `flock` for write coordination:

```bash
(
  flock -w 10 200 || { echo "Lock timeout — retrying"; sleep 2; flock -w 10 200 || true; }
  cat >> "$SESSION_RECORD" << 'ENTRY'
  [your summary here]
ENTRY
) 200>"${SESSION_RECORD}.lock"
```

## Configuration

Load from `vm-test-manifest.json` in the project root. Fallback values if the file is missing:

| Key | Value |
|-----|-------|
| VM name | `qa-audiobook-cachyos` |
| VM IP | `192.168.122.63` |
| SSH user | `claude` |
| SSH key | `~/.ssh/id_ed25519` |
| SSH password | `REDACTED_VM_PASSWORD` |
| Native API port | `5001` |
| Native web port | `8090` |
| App path | `/opt/audiobooks` |
| DB path (QA) | `/var/lib/audiobooks/db/audiobooks.db` |
| DB path (production) | `/var/lib/audiobooks/db/audiobooks.db` (on dev host / localhost) |
| Version file | `/opt/audiobooks/VERSION` |
| Service target | `audiobook.target` |
| Services | `audiobook-api`, `audiobook-converter`, `audiobook-mover`, `audiobook-downloader` |
| Snapshot | `return-to-base-2026-02-23` |
| Expected books | ~801 |
| Expected authors | ~492 |
| GitHub repo | `TheBoscoClub/Audiobook-Manager` |
| TOTP secret | `.claude/secrets/totp-secret` (relative to project root) |
| Auth username | `claudecode` |

Define these as shell variables at the start for reuse:

```bash
QA_VM="qa-audiobook-cachyos"
QA_IP="192.168.122.63"
SSH_KEY="~/.ssh/id_ed25519"
SSH_USER="claude"
SSH_CMD="ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $SSH_USER@$QA_IP"
SCP_CMD="scp -i $SSH_KEY -o StrictHostKeyChecking=no"
API_BASE="http://$QA_IP:5001"
WEB_BASE="https://$QA_IP:8090"
APP_PATH="/opt/audiobooks"
DB_PATH="/var/lib/audiobooks/db/audiobooks.db"
PROD_DB="/var/lib/audiobooks/db/audiobooks.db"
SERVICE_TARGET="audiobook.target"
GITHUB_REPO="TheBoscoClub/Audiobook-Manager"
```

Initialize result tracking variables for the final report:

```bash
UPGRADE_PERFORMED="no"
DB_SYNC_STATUS="skipped"
DB_SCHEMA_VERSION="unknown"
TARGET_VERSION="unknown"
QA_VERSION="unknown"
HEALTH_SERVICES="UNTESTED"
HEALTH_API="UNTESTED"
HEALTH_WEB="UNTESTED"
HEALTH_DB_INTEGRITY="UNTESTED"
HEALTH_LIBRARY_COUNT="UNTESTED"
API_TESTS_PASSED=0
API_TESTS_TOTAL=0
WEB_TESTS_PASSED=0
WEB_TESTS_TOTAL=0
AUTH_FLOW="UNTESTED"
SERVICE_RESILIENCE="UNTESTED"
LOG_ERRORS=0
SSL_STATUS="UNTESTED"
OVERALL="PASS"
```

---

## Step 1: VM Connectivity

**Goal**: Ensure the QA VM is running and reachable via SSH.

1. Check VM state:

```bash
VM_STATE=$(sudo virsh domstate "$QA_VM" 2>/dev/null)
echo "VM state: $VM_STATE"
```

1. If not running, start it:

```bash
if [[ "$VM_STATE" != "running" ]]; then
    echo "Starting QA VM..."
    sudo virsh start "$QA_VM"
    echo "Waiting for VM to boot..."
    sleep 15
fi
```

1. Wait for SSH (poll with timeout, max 120 seconds):

```bash
MAX_WAIT=120
WAITED=0
while ! $SSH_CMD "echo ok" >/dev/null 2>&1; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        echo "FATAL: SSH not available after ${MAX_WAIT}s"
        # Set OVERALL=FAIL, skip to report
        OVERALL="FAIL"
        echo "ABORT: Cannot reach QA VM. Skipping all tests."
        # Jump to Step 7 (report)
        break
    fi
    sleep 5
    WAITED=$((WAITED + 5))
    echo "Waiting for SSH... (${WAITED}s)"
done
```

1. Verify connectivity:

```bash
$SSH_CMD "hostname && uname -r && uptime"
```

**SUCCESS**: SSH connects and returns hostname.
**FAILURE**: ABORT all remaining steps. Jump to Step 7 and report the failure.

---

## Step 2: Version Resolution

**Goal**: Determine the target version (newest released) and compare with current QA version.

1. Get latest GitHub release tag:

```bash
GH_VERSION=$(gh release view --repo "$GITHUB_REPO" --json tagName -q .tagName 2>/dev/null | sed 's/^v//')
echo "GitHub release: $GH_VERSION"
```

1. Check for staged release (local `.staged-release` file):

```bash
STAGED_VERSION=""
if [[ -f ".staged-release" ]]; then
    STAGED_VERSION=$(grep '^version=' .staged-release | cut -d= -f2)
    echo "Staged release: $STAGED_VERSION"
else
    echo "No staged release found"
fi
```

1. Determine target version (max of GitHub release and staged release):

```bash
python3 -c "
from packaging.version import Version
gh = '${GH_VERSION}' if '${GH_VERSION}' else '0.0.0'
staged = '${STAGED_VERSION}' if '${STAGED_VERSION}' else '0.0.0'
try:
    target = max(Version(gh), Version(staged))
    print(str(target))
except Exception as e:
    # Fallback: prefer staged if available, else GitHub
    print('${STAGED_VERSION}' if '${STAGED_VERSION}' else '${GH_VERSION}')
"
```

Store the result as `TARGET_VERSION`.

1. Get current QA version:

```bash
QA_VERSION=$($SSH_CMD "cat $APP_PATH/VERSION 2>/dev/null" | tr -d '[:space:]')
echo "QA version: $QA_VERSION"
echo "Target version: $TARGET_VERSION"
```

1. Compare versions to decide if upgrade is needed:

```bash
NEEDS_UPGRADE=$(python3 -c "
from packaging.version import Version
qa = '${QA_VERSION}' if '${QA_VERSION}' else '0.0.0'
target = '${TARGET_VERSION}' if '${TARGET_VERSION}' else '0.0.0'
try:
    print('yes' if Version(target) > Version(qa) else 'no')
except:
    print('yes')
")
echo "Upgrade needed: $NEEDS_UPGRADE"
```

**SUCCESS**: Both versions resolved, comparison complete.
**FAILURE**: If GitHub release cannot be fetched AND no staged release exists, ABORT (no target to test against).

---

## Step 3: Auto-Upgrade (if needed)

**Goal**: Upgrade QA VM to target version if it is behind.

Skip this step entirely if `NEEDS_UPGRADE` is `no`. Log "QA already at target version" and proceed to Step 4.

1. Determine source and prepare checkout:

```bash
if [[ -n "$STAGED_VERSION" ]] && [[ "$TARGET_VERSION" == "$STAGED_VERSION" ]]; then
    echo "Target is staged release ($STAGED_VERSION) — already on correct commit"
else
    echo "Target is GitHub release ($GH_VERSION) — checking out tag"
    git fetch --tags
    git checkout "v${TARGET_VERSION}"
fi
```

1. Run upgrade:

```bash
./upgrade.sh --from-project . --remote "$QA_IP" --yes
```

If upgrade.sh exits non-zero, set `OVERALL=FAIL` and ABORT with error message. Do NOT continue.

1. Verify upgrade:

```bash
NEW_QA_VERSION=$($SSH_CMD "cat $APP_PATH/VERSION" | tr -d '[:space:]')
echo "Post-upgrade QA version: $NEW_QA_VERSION"
if [[ "$NEW_QA_VERSION" != "$TARGET_VERSION" ]]; then
    echo "ERROR: Version mismatch after upgrade (expected $TARGET_VERSION, got $NEW_QA_VERSION)"
    OVERALL="FAIL"
    # Continue with testing despite mismatch — report it
fi
UPGRADE_PERFORMED="yes"
QA_VERSION="$NEW_QA_VERSION"
```

1. If you checked out a tag in step 3.1, return to the original branch:

```bash
if [[ -n "$STAGED_VERSION" ]] && [[ "$TARGET_VERSION" == "$STAGED_VERSION" ]]; then
    : # Already on correct branch, no action needed
else
    git checkout -  # Return to previous branch
fi
```

**SUCCESS**: QA VERSION file matches TARGET_VERSION after upgrade.
**FAILURE**: upgrade.sh exits non-zero: ABORT. Version mismatch: continue but flag in report.

---

## Step 4: Database Sync (Production to QA)

**Goal**: Copy the production database to QA for realistic testing, but only if schemas are compatible.

1. Get production schema version (on dev host / localhost):

```bash
PROD_SCHEMA=$(sqlite3 "$PROD_DB" "SELECT MAX(version) FROM schema_version" 2>/dev/null)
echo "Production schema version: $PROD_SCHEMA"
```

If the production DB is not accessible (e.g., not on the dev host), skip this step:

```bash
if [[ -z "$PROD_SCHEMA" ]]; then
    echo "WARN: Production DB not accessible — skipping DB sync"
    DB_SYNC_STATUS="skipped (no production DB)"
    # Proceed to Step 5
fi
```

1. Get QA schema version:

```bash
QA_SCHEMA=$($SSH_CMD "sqlite3 $DB_PATH 'SELECT MAX(version) FROM schema_version'" 2>/dev/null)
echo "QA schema version: $QA_SCHEMA"
```

1. Compare schemas — sync is safe only if production schema version is less than or equal to QA schema version:

```bash
if [[ -n "$PROD_SCHEMA" ]] && [[ -n "$QA_SCHEMA" ]]; then
    if [[ "$PROD_SCHEMA" -le "$QA_SCHEMA" ]]; then
        echo "Schema compatible (prod=$PROD_SCHEMA <= qa=$QA_SCHEMA) — syncing"
    else
        echo "WARN: Production schema ($PROD_SCHEMA) > QA schema ($QA_SCHEMA) — skipping sync"
        DB_SYNC_STATUS="skipped (schema incompatible: prod=$PROD_SCHEMA > qa=$QA_SCHEMA)"
        DB_SCHEMA_VERSION="$QA_SCHEMA"
        # Skip to Step 5
    fi
fi
```

1. If compatible, perform the sync:

```bash
# Stop services
echo "Stopping QA services..."
$SSH_CMD "sudo systemctl stop $SERVICE_TARGET"

# Copy production DB to QA VM temp location
echo "Copying production DB to QA..."
$SCP_CMD "$PROD_DB" "$SSH_USER@$QA_IP:/tmp/audiobooks-prod.db"

# Backup existing QA DB, swap in production copy
echo "Swapping database on QA..."
$SSH_CMD "sudo cp $DB_PATH ${DB_PATH}.bak-$(date +%Y%m%d%H%M%S) 2>/dev/null || true"
$SSH_CMD "sudo cp /tmp/audiobooks-prod.db $DB_PATH && sudo chown audiobooks:audiobooks $DB_PATH && sudo chmod 644 $DB_PATH"

# Clean up temp file
$SSH_CMD "rm -f /tmp/audiobooks-prod.db"

# Start services
echo "Starting QA services..."
$SSH_CMD "sudo systemctl start $SERVICE_TARGET"

# Wait for services to stabilize
sleep 5

DB_SYNC_STATUS="synced"
DB_SCHEMA_VERSION="$PROD_SCHEMA"
echo "DB sync complete (schema v$PROD_SCHEMA)"
```

**SUCCESS**: DB copied, services restarted, sync status recorded.
**FAILURE**: SCP or service restart failures are non-fatal. Record in report and continue.

---

## Step 5: Health Checks

**Goal**: Verify core infrastructure is working before running regression tests.

### 5a. Systemd Services

Check that all services under `audiobook.target` are active:

```bash
SERVICES_OK=true
for svc in audiobook-api audiobook-converter audiobook-mover audiobook-downloader; do
    STATUS=$($SSH_CMD "sudo systemctl is-active $svc" 2>/dev/null | tr -d '[:space:]')
    echo "  $svc: $STATUS"
    if [[ "$STATUS" != "active" ]]; then
        SERVICES_OK=false
    fi
done

# Also check the target itself
TARGET_STATUS=$($SSH_CMD "sudo systemctl is-active $SERVICE_TARGET" 2>/dev/null | tr -d '[:space:]')
echo "  $SERVICE_TARGET: $TARGET_STATUS"
if [[ "$TARGET_STATUS" != "active" ]]; then
    SERVICES_OK=false
fi

if $SERVICES_OK; then
    HEALTH_SERVICES="PASS"
else
    HEALTH_SERVICES="FAIL"
    OVERALL="FAIL"
fi
```

### 5b. API Version Endpoint

```bash
API_RESPONSE=$(curl -sf "$API_BASE/api/system/version" 2>/dev/null)
if [[ -n "$API_RESPONSE" ]]; then
    echo "API version response: $API_RESPONSE"
    HEALTH_API="PASS"
else
    echo "ERROR: API not responding"
    HEALTH_API="FAIL"
    OVERALL="FAIL"
fi
```

### 5c. Web UI Responds

```bash
WEB_HTTP_CODE=$(curl -sfk -o /dev/null -w '%{http_code}' "$WEB_BASE/" 2>/dev/null)
echo "Web UI HTTP code: $WEB_HTTP_CODE"
if [[ "$WEB_HTTP_CODE" == "200" ]] || [[ "$WEB_HTTP_CODE" == "302" ]]; then
    HEALTH_WEB="PASS"
else
    HEALTH_WEB="FAIL"
    OVERALL="FAIL"
fi
```

### 5d. Database Integrity

```bash
DB_CHECK=$($SSH_CMD "sqlite3 $DB_PATH 'PRAGMA integrity_check'" 2>/dev/null | tr -d '[:space:]')
echo "DB integrity: $DB_CHECK"
if [[ "$DB_CHECK" == "ok" ]]; then
    HEALTH_DB_INTEGRITY="PASS"
else
    HEALTH_DB_INTEGRITY="FAIL"
    OVERALL="FAIL"
fi
```

### 5e. Library Count

```bash
BOOK_COUNT=$($SSH_CMD "sqlite3 $DB_PATH 'SELECT COUNT(*) FROM audiobooks'" 2>/dev/null | tr -d '[:space:]')
echo "Library count: $BOOK_COUNT books"
# Accept anything above 700 as reasonable (production has ~801)
if [[ -n "$BOOK_COUNT" ]] && [[ "$BOOK_COUNT" -gt 700 ]]; then
    HEALTH_LIBRARY_COUNT="PASS ($BOOK_COUNT books)"
elif [[ -n "$BOOK_COUNT" ]] && [[ "$BOOK_COUNT" -gt 0 ]]; then
    HEALTH_LIBRARY_COUNT="WARN ($BOOK_COUNT books, expected ~801)"
else
    HEALTH_LIBRARY_COUNT="FAIL (${BOOK_COUNT:-0} books)"
    OVERALL="FAIL"
fi
```

**SUCCESS**: All health checks pass.
**FAILURE**: Individual failures are recorded. Only FAIL on critical checks (services, API). Continue to regression regardless.

---

## Step 6: Full Regression

Run all regression tests. Each sub-test increments pass/total counters. Do NOT abort on individual test failures; collect all results.

### 6a. API Endpoint Tests

Test each API endpoint and record pass/fail:

```bash
# Helper function concept (implement inline for each test):
# curl the endpoint, check HTTP status, check response body if needed

# Test 1: GET /api/system/version
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/system/version")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/system/version ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/system/version ($HTTP)"
fi

# Test 2: GET /api/audiobooks (paginated list)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/audiobooks?page=1&per_page=10")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/audiobooks ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/audiobooks ($HTTP)"
fi

# Test 3: GET /api/audiobooks?search=test (search)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/audiobooks?search=test")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/audiobooks?search=test ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/audiobooks?search=test ($HTTP)"
fi

# Test 4: GET /api/stats (library statistics)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/stats")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/stats ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/stats ($HTTP)"
fi

# Test 5: GET /api/audiobooks/<id> (single book detail — use ID 1)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/audiobooks/1")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/audiobooks/1 ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/audiobooks/1 ($HTTP)"
fi

# Test 6: GET /api/hash-stats (duplicate hash statistics)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/hash-stats")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/hash-stats ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/hash-stats ($HTTP)"
fi

# Test 7: GET /api/genres (genre list)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/genres")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/genres ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/genres ($HTTP)"
fi

# Test 8: GET /api/supplements (supplements list)
HTTP=$(curl -sf -o /tmp/qa-api-resp -w '%{http_code}' "$API_BASE/api/supplements")
API_TESTS_TOTAL=$((API_TESTS_TOTAL + 1))
if [[ "$HTTP" == "200" ]]; then
    echo "  PASS: GET /api/supplements ($HTTP)"
    API_TESTS_PASSED=$((API_TESTS_PASSED + 1))
else
    echo "  FAIL: GET /api/supplements ($HTTP)"
fi
```

### 6b. Web UI Page Tests

Test that key web pages return HTTP 200 (or 302 for auth-required pages):

```bash
# Web pages are served over HTTPS with self-signed cert, use -k
declare -a WEB_PAGES=("/" "/index.html" "/about.html" "/help.html" "/login.html" "/shell.html")
WEB_TESTS_TOTAL=${#WEB_PAGES[@]}
WEB_TESTS_PASSED=0

for page in "${WEB_PAGES[@]}"; do
    HTTP=$(curl -sfk -o /dev/null -w '%{http_code}' "$WEB_BASE$page" 2>/dev/null)
    if [[ "$HTTP" == "200" ]] || [[ "$HTTP" == "302" ]] || [[ "$HTTP" == "304" ]]; then
        echo "  PASS: $page ($HTTP)"
        WEB_TESTS_PASSED=$((WEB_TESTS_PASSED + 1))
    else
        echo "  FAIL: $page ($HTTP)"
    fi
done
```

### 6c. Auth Flow Test

Test TOTP authentication using the `claudecode` admin account:

```bash
# Read TOTP secret
TOTP_SECRET_FILE=".claude/secrets/totp-secret"
if [[ ! -f "$TOTP_SECRET_FILE" ]]; then
    echo "WARN: TOTP secret file not found at $TOTP_SECRET_FILE — skipping auth test"
    AUTH_FLOW="SKIP (no TOTP secret)"
else
    TOTP_SECRET=$(cat "$TOTP_SECRET_FILE" | tr -d '[:space:]')

    # Generate current TOTP code using Python
    TOTP_CODE=$(python3 -c "
import hmac, struct, time, hashlib, base64
secret = base64.b32decode('$TOTP_SECRET', casefold=True)
counter = int(time.time()) // 30
msg = struct.pack('>Q', counter)
h = hmac.new(secret, msg, hashlib.sha1).digest()
offset = h[-1] & 0x0F
code = (struct.unpack('>I', h[offset:offset+4])[0] & 0x7FFFFFFF) % 1000000
print(f'{code:06d}')
")
    echo "Generated TOTP code: $TOTP_CODE"

    # Login
    LOGIN_RESP=$(curl -sf -c /tmp/qa-cookies.txt \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"claudecode\", \"code\": \"$TOTP_CODE\"}" \
        "$API_BASE/auth/login" 2>/dev/null)
    LOGIN_STATUS=$?
    echo "Login response: $LOGIN_RESP"

    if [[ $LOGIN_STATUS -eq 0 ]] && echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('success') or 'session' in str(d).lower() or 'token' in str(d).lower() else 1)" 2>/dev/null; then
        echo "  Login: SUCCESS"

        # Access admin endpoint with session cookie
        ADMIN_RESP=$(curl -sf -b /tmp/qa-cookies.txt "$API_BASE/auth/admin/users" 2>/dev/null)
        ADMIN_STATUS=$?
        if [[ $ADMIN_STATUS -eq 0 ]] && [[ -n "$ADMIN_RESP" ]]; then
            echo "  Admin access: SUCCESS"
            AUTH_FLOW="PASS"
        else
            echo "  Admin access: FAILED (HTTP error or empty response)"
            AUTH_FLOW="FAIL (admin endpoint rejected session)"
        fi
    else
        echo "  Login: FAILED"
        AUTH_FLOW="FAIL (login rejected)"
    fi

    # Cleanup
    rm -f /tmp/qa-cookies.txt
fi
```

### 6d. Service Resilience Test

Verify services recover after a restart:

```bash
echo "Restarting audiobook.target..."
$SSH_CMD "sudo systemctl restart $SERVICE_TARGET"

# Wait for services to come back
sleep 8

# Check API is responding
RESILIENCE_RESP=$(curl -sf "$API_BASE/api/system/version" 2>/dev/null)
if [[ -n "$RESILIENCE_RESP" ]]; then
    echo "  Service resilience: PASS (API responding after restart)"
    SERVICE_RESILIENCE="PASS"
else
    echo "  Service resilience: FAIL (API not responding after restart)"
    SERVICE_RESILIENCE="FAIL"
    OVERALL="FAIL"
fi
```

### 6e. Log Inspection

Check for recent error-level log entries:

```bash
ERROR_LINES=$($SSH_CMD "sudo journalctl -u audiobook-api --since '1 hour ago' --no-pager -p err 2>/dev/null" | grep -v "^-- " | grep -v "^$")
if [[ -n "$ERROR_LINES" ]]; then
    LOG_ERRORS=$(echo "$ERROR_LINES" | wc -l)
    echo "  Found $LOG_ERRORS error-level log entries:"
    echo "$ERROR_LINES" | head -20
else
    LOG_ERRORS=0
    echo "  No error-level log entries in the last hour"
fi
```

### 6f. SSL Certificate Check

Inspect the HTTPS certificate on the web port:

```bash
SSL_INFO=$(curl -vk "$WEB_BASE/" 2>&1 | grep -iE 'expire|issuer|subject|SSL certificate')
echo "SSL certificate info:"
echo "$SSL_INFO"

# Check if certificate is expired
EXPIRY=$(echo "$SSL_INFO" | grep -i 'expire' | head -1)
if echo "$SSL_INFO" | grep -qi 'expire'; then
    # Parse expiry date if possible
    EXPIRY_DATE=$(curl -vk "$WEB_BASE/" 2>&1 | grep -i 'expire date' | sed 's/.*expire date: //')
    if [[ -n "$EXPIRY_DATE" ]]; then
        EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s 2>/dev/null)
        NOW_EPOCH=$(date +%s)
        if [[ -n "$EXPIRY_EPOCH" ]]; then
            DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
            if [[ $DAYS_LEFT -lt 0 ]]; then
                SSL_STATUS="EXPIRED ($EXPIRY_DATE)"
            elif [[ $DAYS_LEFT -lt 30 ]]; then
                SSL_STATUS="EXPIRING in ${DAYS_LEFT} days ($EXPIRY_DATE)"
            else
                SSL_STATUS="valid (expires $EXPIRY_DATE, ${DAYS_LEFT} days remaining)"
            fi
        else
            SSL_STATUS="valid (self-signed)"
        fi
    else
        SSL_STATUS="valid (self-signed)"
    fi
else
    SSL_STATUS="valid (self-signed)"
fi
echo "SSL status: $SSL_STATUS"
```

---

## Step 7: Report

Generate the final structured report. Print it to stdout for the parent session to capture.

Determine `OVERALL` status: if any critical check has `FAIL`, set `OVERALL=FAIL`. If all critical checks pass but there are warnings, set `OVERALL=PASS (with warnings)`.

```bash
echo ""
echo "═══════════════════════════════════════════════"
echo "  QA NATIVE APP REGRESSION RESULTS"
echo "═══════════════════════════════════════════════"
echo "  Target Version:    $TARGET_VERSION"
echo "  QA Version:        $QA_VERSION"
echo "  Upgrade Performed: $UPGRADE_PERFORMED"
echo "  DB Sync:           $DB_SYNC_STATUS (schema v$DB_SCHEMA_VERSION)"
echo ""
echo "  HEALTH CHECKS"
echo "  ─────────────"
echo "  Systemd Services:  $HEALTH_SERVICES"
echo "  API Endpoint:      $HEALTH_API"
echo "  Web UI:            $HEALTH_WEB"
echo "  DB Integrity:      $HEALTH_DB_INTEGRITY"
echo "  Library Count:     $HEALTH_LIBRARY_COUNT"
echo ""
echo "  REGRESSION TESTS"
echo "  ─────────────────"
echo "  API Endpoints:     $API_TESTS_PASSED/$API_TESTS_TOTAL passed"
echo "  Web UI Pages:      $WEB_TESTS_PASSED/$WEB_TESTS_TOTAL passed"
echo "  Auth Flow:         $AUTH_FLOW"
echo "  Service Resilience:$SERVICE_RESILIENCE"
echo "  Log Errors:        $LOG_ERRORS errors found"
echo "  SSL Certificate:   $SSL_STATUS"
echo ""
echo "  OVERALL: $OVERALL"
echo "═══════════════════════════════════════════════"
```

---

## Abort Protocol

If any step marked as **ABORT on failure** fails:

1. Set `OVERALL="FAIL"`.
2. Set all untested metrics to `"ABORT"`.
3. Skip to **Step 7** and generate the report with whatever data was collected.
4. Clearly state which step caused the abort and why.

Do NOT leave the QA VM in a bad state. If services were stopped for DB sync and an abort happens mid-sync, restart them:

```bash
$SSH_CMD "sudo systemctl start $SERVICE_TARGET" 2>/dev/null || true
```

---

## Cleanup

After the report is generated:

1. Remove any temporary files created on the QA VM:

```bash
$SSH_CMD "rm -f /tmp/audiobooks-prod.db /tmp/qa-api-resp" 2>/dev/null || true
```

1. Remove local temp files:

```bash
rm -f /tmp/qa-api-resp /tmp/qa-cookies.txt
```

1. Do NOT shut down the QA VM (it stays running for Docker QA tests or manual inspection).
2. Do NOT revert to snapshot (QA VM is persistent, unlike the test VM).
