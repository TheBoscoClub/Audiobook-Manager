---
model: opus
type: project-specific QA module
target: qa-audiobooks-cachyos (192.168.122.63)
ssh: ssh -i ~/.claude/ssh/id_ed25519 claude@192.168.122.63
trigger: /test qadocker
---

# QA Docker Container Regression Test Module

You are a subagent responsible for running a full regression test of the Audiobook-Manager Docker container on the QA VM (`qa-audiobooks-cachyos`). Execute every step below autonomously. Collect results into variables and produce a structured report at the end.

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

Load from `vm-test-manifest.json` in the project root (key: `qa_vm`). Fallback values if the file is missing:

| Key | Value |
|-----|-------|
| VM name | `qa-audiobooks-cachyos` |
| VM IP | `192.168.122.63` |
| SSH user | `claude` |
| SSH key | `~/.claude/ssh/id_ed25519` |
| SSH password | `Claud3Cod3` |
| Docker HTTPS port | `8443` |
| Docker HTTP redirect port | `8080` |
| Docker API port (internal) | `5001` |
| Container name | `audiobooks-docker` |
| Image prefix | `audiobook-manager` |
| Docker daemon preset | `disabled` (must start manually) |
| Docker DB path (QA) | `/var/lib/audiobooks/docker-data/audiobooks.db` |
| Docker data volume | `/var/lib/audiobooks/docker-data:/app/data` |
| Library mount | `/srv/audiobooks/Library:/audiobooks:ro` |
| Supplements mount | `/srv/audiobooks/Supplements:/supplements:ro` |
| Production DB path (dev host) | `/var/lib/audiobooks/db/audiobooks.db` |
| Version file (in container) | `/app/VERSION` |
| Native API port (for consistency) | `5001` |
| Native web port (for consistency) | `8090` |
| Snapshot | `return-to-base-2026-02-23` |
| Expected books | ~801 |
| Expected authors | ~492 |
| GitHub repo | `TheBoscoClub/Audiobook-Manager` |

Define these as shell variables at the start for reuse:

```bash
QA_VM="qa-audiobooks-cachyos"
QA_IP="192.168.122.63"
SSH_KEY="~/.claude/ssh/id_ed25519"
SSH_USER="claude"
SSH_CMD="ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $SSH_USER@$QA_IP"
SCP_CMD="scp -i $SSH_KEY -o StrictHostKeyChecking=no"
DOCKER_WEB="https://$QA_IP:8443"
DOCKER_HTTP="http://$QA_IP:8080"
CONTAINER_NAME="audiobooks-docker"
IMAGE_PREFIX="audiobook-manager"
DOCKER_DB_PATH="/var/lib/audiobooks/docker-data/audiobooks.db"
PROD_DB="/var/lib/audiobooks/db/audiobooks.db"
NATIVE_API="http://$QA_IP:5001"
GITHUB_REPO="TheBoscoClub/Audiobook-Manager"
```

Initialize result tracking variables for the final report:

```bash
UPGRADE_PERFORMED="no"
DB_SYNC_STATUS="skipped"
DB_SCHEMA_VERSION="unknown"
TARGET_VERSION="unknown"
DOCKER_VERSION="unknown"
HEALTH_CONTAINER="UNTESTED"
HEALTH_HTTPS="UNTESTED"
HEALTH_HTTP_REDIRECT="UNTESTED"
HEALTH_DB_INTEGRITY="UNTESTED"
HEALTH_LOGS="UNTESTED"
WEB_TESTS_PASSED=0
WEB_TESTS_TOTAL=0
LIBRARY_BROWSING="UNTESTED"
SUPPLEMENTS_STATUS="UNTESTED"
RESTART_RESILIENCE="UNTESTED"
RESOURCE_USAGE="unknown"
CONSISTENCY_VERSION="UNTESTED"
CONSISTENCY_BOOKS="UNTESTED"
CONSISTENCY_AUTHORS="UNTESTED"
NATIVE_BOOK_COUNT="unknown"
DOCKER_BOOK_COUNT="unknown"
NATIVE_AUTHOR_COUNT="unknown"
DOCKER_AUTHOR_COUNT="unknown"
LOG_ERRORS=0
OVERALL="PASS"
```

---

## Step 1: VM Connectivity & Docker Readiness

**Goal**: Ensure the QA VM is running, reachable via SSH, and Docker daemon is ready.

1. Check VM state:

```bash
VM_STATE=$(sudo virsh domstate "$QA_VM" 2>/dev/null)
echo "VM state: $VM_STATE"
```

2. If not running, start it:

```bash
if [[ "$VM_STATE" != "running" ]]; then
    echo "Starting QA VM..."
    sudo virsh start "$QA_VM"
    echo "Waiting for VM to boot..."
    sleep 15
fi
```

3. Wait for SSH (poll with timeout, max 120 seconds):

```bash
MAX_WAIT=120
WAITED=0
while ! $SSH_CMD "echo ok" >/dev/null 2>&1; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        echo "FATAL: SSH not available after ${MAX_WAIT}s"
        OVERALL="FAIL"
        echo "ABORT: Cannot reach QA VM. Skipping all tests."
        break
    fi
    sleep 5
    WAITED=$((WAITED + 5))
    echo "Waiting for SSH... (${WAITED}s)"
done
```

4. Verify connectivity:

```bash
$SSH_CMD "hostname && uname -r && uptime"
```

5. Start Docker daemon (preset is disabled on QA VM):

```bash
echo "Starting Docker daemon..."
$SSH_CMD "sudo systemctl start docker"
```

6. Wait for Docker to be ready (retry with timeout, max 60 seconds):

```bash
DOCKER_WAIT=60
DOCKER_WAITED=0
while ! $SSH_CMD "sudo docker info" >/dev/null 2>&1; do
    if [[ $DOCKER_WAITED -ge $DOCKER_WAIT ]]; then
        echo "FATAL: Docker not ready after ${DOCKER_WAIT}s"
        OVERALL="FAIL"
        echo "ABORT: Docker daemon not responding. Skipping all tests."
        break
    fi
    sleep 3
    DOCKER_WAITED=$((DOCKER_WAITED + 3))
    echo "Waiting for Docker... (${DOCKER_WAITED}s)"
done
echo "Docker daemon is ready."
```

7. Check container status:

```bash
CONTAINER_STATUS=$($SSH_CMD "sudo docker ps -a --filter name=$CONTAINER_NAME --format '{{.Status}}'" 2>/dev/null)
echo "Container status: ${CONTAINER_STATUS:-NOT FOUND}"
```

**SUCCESS**: SSH connects, Docker daemon responds, container status is known.
**FAILURE**: SSH or Docker unreachable: ABORT all remaining steps. Jump to Step 8 (report).

---

## Step 2: Version Resolution

**Goal**: Determine the target version (newest released) and compare with the current Docker container version.

1. Get latest GitHub release tag:

```bash
GH_VERSION=$(gh release view --repo "$GITHUB_REPO" --json tagName -q .tagName 2>/dev/null | sed 's/^v//')
echo "GitHub release: $GH_VERSION"
```

2. Check for staged release (local `.staged-release` file):

```bash
STAGED_VERSION=""
if [[ -f ".staged-release" ]]; then
    STAGED_VERSION=$(grep '^version=' .staged-release | cut -d= -f2)
    echo "Staged release: $STAGED_VERSION"
else
    echo "No staged release found"
fi
```

3. Determine target version (max of GitHub release and staged release):

```bash
TARGET_VERSION=$(python3 -c "
from packaging.version import Version
gh = '${GH_VERSION}' if '${GH_VERSION}' else '0.0.0'
staged = '${STAGED_VERSION}' if '${STAGED_VERSION}' else '0.0.0'
try:
    target = max(Version(gh), Version(staged))
    print(str(target))
except Exception as e:
    # Fallback: prefer staged if available, else GitHub
    print('${STAGED_VERSION}' if '${STAGED_VERSION}' else '${GH_VERSION}')
")
echo "Target version: $TARGET_VERSION"
```

4. Get current Docker container version (try multiple methods):

```bash
# Method 1: Read VERSION file from running container
DOCKER_VERSION=$($SSH_CMD "sudo docker exec $CONTAINER_NAME cat /app/VERSION 2>/dev/null" | tr -d '[:space:]')

# Method 2: Inspect image tag if container is not running
if [[ -z "$DOCKER_VERSION" ]]; then
    DOCKER_IMAGE=$($SSH_CMD "sudo docker inspect $CONTAINER_NAME --format '{{.Config.Image}}' 2>/dev/null")
    DOCKER_VERSION=$(echo "$DOCKER_IMAGE" | sed 's/.*://')
    echo "Version from image tag: $DOCKER_VERSION"
fi

# Method 3: No container exists at all
if [[ -z "$DOCKER_VERSION" ]] || [[ "$DOCKER_VERSION" == "$IMAGE_PREFIX" ]]; then
    DOCKER_VERSION="none"
fi

echo "Docker container version: $DOCKER_VERSION"
echo "Target version: $TARGET_VERSION"
```

5. Compare versions to decide if upgrade is needed:

```bash
NEEDS_UPGRADE=$(python3 -c "
from packaging.version import Version
docker = '${DOCKER_VERSION}' if '${DOCKER_VERSION}' not in ('', 'none') else '0.0.0'
target = '${TARGET_VERSION}' if '${TARGET_VERSION}' else '0.0.0'
try:
    print('yes' if Version(target) > Version(docker) else 'no')
except:
    print('yes')
")
echo "Upgrade needed: $NEEDS_UPGRADE"
```

**SUCCESS**: Both versions resolved, comparison complete.
**FAILURE**: If GitHub release cannot be fetched AND no staged release exists, ABORT (no target to test against).

---

## Step 3: Auto-Upgrade (if needed)

**Goal**: Upgrade QA Docker container to target version if it is behind. If no container exists, create one fresh.

Skip this step entirely if `NEEDS_UPGRADE` is `no` and the container is running. Log "Docker already at target version" and proceed to Step 4.

### 3a. Build or Locate the Docker Image

```bash
# Check if the target image already exists locally (e.g., from a staged release build)
LOCAL_IMAGE=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep "^${IMAGE_PREFIX}:${TARGET_VERSION}$" 2>/dev/null)

if [[ -n "$LOCAL_IMAGE" ]]; then
    echo "Local image found: $LOCAL_IMAGE"
else
    echo "No local image for $TARGET_VERSION — building from source"

    # Determine source: staged release or GitHub tag
    ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    CHECKED_OUT_TAG=false

    if [[ -n "$STAGED_VERSION" ]] && [[ "$TARGET_VERSION" == "$STAGED_VERSION" ]]; then
        echo "Target is staged release ($STAGED_VERSION) — already on correct commit"
    else
        echo "Target is GitHub release ($GH_VERSION) — checking out tag"
        git fetch --tags
        git checkout "v${TARGET_VERSION}"
        CHECKED_OUT_TAG=true
    fi

    # Build image using Docker Buildx
    docker buildx build -t "${IMAGE_PREFIX}:${TARGET_VERSION}" \
        --build-arg APP_VERSION="${TARGET_VERSION}" \
        --load .

    if [[ $? -ne 0 ]]; then
        echo "ERROR: Docker build failed"
        OVERALL="FAIL"
        # Return to original branch if we checked out a tag
        if [[ "$CHECKED_OUT_TAG" == "true" ]]; then
            git checkout "$ORIGINAL_BRANCH"
        fi
        echo "ABORT: Cannot build Docker image. Skipping upgrade."
        # Jump to Step 5 health checks (test existing container if present)
    fi

    # Return to original branch if we checked out a tag
    if [[ "$CHECKED_OUT_TAG" == "true" ]]; then
        git checkout "$ORIGINAL_BRANCH"
    fi
fi
```

### 3b. Transfer Image to QA VM

```bash
echo "Transferring Docker image to QA VM..."
docker save "${IMAGE_PREFIX}:${TARGET_VERSION}" | \
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$QA_IP" 'sudo docker load'

if [[ $? -ne 0 ]]; then
    echo "ERROR: Image transfer failed"
    OVERALL="FAIL"
    echo "ABORT: Cannot transfer Docker image to QA VM."
    # Jump to Step 5 health checks
fi
echo "Image transferred successfully."
```

### 3c. Stop and Remove Old Container

```bash
# Check if old container exists
OLD_EXISTS=$($SSH_CMD "sudo docker ps -a --filter name=$CONTAINER_NAME --format '{{.Names}}'" 2>/dev/null)

if [[ -n "$OLD_EXISTS" ]]; then
    echo "Stopping and removing old container..."
    $SSH_CMD "sudo docker stop $CONTAINER_NAME" 2>/dev/null || true
    $SSH_CMD "sudo docker rm $CONTAINER_NAME" 2>/dev/null || true
    echo "Old container removed."
else
    echo "No existing container to remove."
fi
```

### 3d. Create New Container

```bash
echo "Creating new container with image ${IMAGE_PREFIX}:${TARGET_VERSION}..."
$SSH_CMD "sudo docker run -d --name $CONTAINER_NAME \
  -p 8443:8443 -p 8080:8080 \
  -v /srv/audiobooks/Library:/audiobooks:ro \
  -v /srv/audiobooks/Supplements:/supplements:ro \
  -v /var/lib/audiobooks/docker-data:/app/data \
  -e AUDIOBOOK_DIR=/audiobooks \
  -e DATABASE_PATH=/app/data/audiobooks.db \
  -e SUPPLEMENTS_DIR=/supplements \
  -e WEB_PORT=8443 -e API_PORT=5001 \
  -e HTTP_REDIRECT_PORT=8080 \
  -e AUDIOBOOKS_USE_WAITRESS=true \
  -e HTTP_REDIRECT_ENABLED=true \
  --restart unless-stopped \
  ${IMAGE_PREFIX}:${TARGET_VERSION}"

if [[ $? -ne 0 ]]; then
    echo "ERROR: docker run failed"
    OVERALL="FAIL"
    echo "ABORT: Cannot create Docker container."
    # Jump to Step 8 report
fi
```

### 3e. Wait for Container to be Healthy

```bash
echo "Waiting for container to start..."
sleep 10

# Check container health (HEALTHCHECK is defined in Dockerfile)
HEALTH_WAIT=60
HEALTH_WAITED=0
while true; do
    CONTAINER_HEALTH=$($SSH_CMD "sudo docker inspect $CONTAINER_NAME --format '{{.State.Health.Status}}' 2>/dev/null" | tr -d '[:space:]')
    CONTAINER_RUNNING=$($SSH_CMD "sudo docker inspect $CONTAINER_NAME --format '{{.State.Running}}' 2>/dev/null" | tr -d '[:space:]')

    if [[ "$CONTAINER_HEALTH" == "healthy" ]]; then
        echo "Container is healthy."
        break
    elif [[ "$CONTAINER_RUNNING" != "true" ]]; then
        echo "ERROR: Container is not running."
        $SSH_CMD "sudo docker logs $CONTAINER_NAME --tail 20" 2>&1
        OVERALL="FAIL"
        break
    elif [[ $HEALTH_WAITED -ge $HEALTH_WAIT ]]; then
        echo "WARN: Container health check timed out after ${HEALTH_WAIT}s (status: $CONTAINER_HEALTH)"
        # Continue anyway — container may be running without healthcheck passing
        break
    fi

    sleep 5
    HEALTH_WAITED=$((HEALTH_WAITED + 5))
    echo "Waiting for healthy status... (${HEALTH_WAITED}s, current: $CONTAINER_HEALTH)"
done
```

### 3f. Verify Upgrade

```bash
NEW_DOCKER_VERSION=$($SSH_CMD "sudo docker exec $CONTAINER_NAME cat /app/VERSION" 2>/dev/null | tr -d '[:space:]')
echo "Post-upgrade Docker version: $NEW_DOCKER_VERSION"
if [[ "$NEW_DOCKER_VERSION" != "$TARGET_VERSION" ]]; then
    echo "ERROR: Version mismatch after upgrade (expected $TARGET_VERSION, got $NEW_DOCKER_VERSION)"
    OVERALL="FAIL"
    # Continue with testing despite mismatch — report it
fi
UPGRADE_PERFORMED="yes"
DOCKER_VERSION="$NEW_DOCKER_VERSION"
```

**SUCCESS**: Container running with correct VERSION file matching TARGET_VERSION.
**FAILURE**: Build fails or docker run fails: ABORT. Version mismatch: continue but flag in report.

---

## Step 4: Database Sync (Production to QA Docker)

**Goal**: Copy the production database to QA Docker data volume for realistic testing, but only if schemas are compatible.

1. Get production schema version (on dev host / localhost):

```bash
PROD_SCHEMA=$(sqlite3 "$PROD_DB" "SELECT MAX(version) FROM schema_version" 2>/dev/null)
echo "Production schema version: $PROD_SCHEMA"
```

If the production DB is not accessible, skip this step:

```bash
if [[ -z "$PROD_SCHEMA" ]]; then
    echo "WARN: Production DB not accessible — skipping DB sync"
    DB_SYNC_STATUS="skipped (no production DB)"
    # Proceed to Step 5
fi
```

2. Get Docker DB schema version:

```bash
DOCKER_SCHEMA=$($SSH_CMD "sqlite3 $DOCKER_DB_PATH 'SELECT MAX(version) FROM schema_version'" 2>/dev/null)
echo "Docker DB schema version: $DOCKER_SCHEMA"
```

If the Docker DB does not exist or has no schema:

```bash
if [[ -z "$DOCKER_SCHEMA" ]]; then
    echo "Docker DB has no schema_version — will sync unconditionally"
    DOCKER_SCHEMA=0
fi
```

3. Compare schemas — sync is safe only if production schema version is less than or equal to what the Docker app supports:

```bash
if [[ -n "$PROD_SCHEMA" ]]; then
    # Get the app's expected schema version from the container
    APP_SCHEMA=$($SSH_CMD "sudo docker exec $CONTAINER_NAME python3 -c \"
import sqlite3
import os
db_path = os.environ.get('DATABASE_PATH', '/app/data/audiobooks.db')
try:
    c = sqlite3.connect(db_path)
    v = c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0]
    print(v)
    c.close()
except:
    print('unknown')
\"" 2>/dev/null | tr -d '[:space:]')

    if [[ "$APP_SCHEMA" == "unknown" ]] || [[ -z "$APP_SCHEMA" ]]; then
        # Fall back to Docker schema from existing DB
        APP_SCHEMA="$DOCKER_SCHEMA"
    fi

    if [[ "$PROD_SCHEMA" -le "${APP_SCHEMA:-0}" ]] 2>/dev/null; then
        echo "Schema compatible (prod=$PROD_SCHEMA <= app=$APP_SCHEMA) — syncing"
    else
        echo "WARN: Production schema ($PROD_SCHEMA) > app schema ($APP_SCHEMA) — skipping sync"
        DB_SYNC_STATUS="skipped (schema incompatible: prod=$PROD_SCHEMA > app=$APP_SCHEMA)"
        DB_SCHEMA_VERSION="$DOCKER_SCHEMA"
        # Skip to Step 5
    fi
fi
```

4. If compatible, perform the sync:

```bash
# Stop container (so DB is not in use)
echo "Stopping Docker container for DB sync..."
$SSH_CMD "sudo docker stop $CONTAINER_NAME"

# Copy production DB to QA VM temp location
echo "Copying production DB to QA VM..."
$SCP_CMD "$PROD_DB" "$SSH_USER@$QA_IP:/tmp/audiobooks-prod-docker.db"

# Backup existing Docker DB, swap in production copy
echo "Swapping database in Docker data volume..."
$SSH_CMD "sudo cp $DOCKER_DB_PATH ${DOCKER_DB_PATH}.bak-\$(date +%Y%m%d%H%M%S) 2>/dev/null || true"
$SSH_CMD "sudo cp /tmp/audiobooks-prod-docker.db $DOCKER_DB_PATH && sudo chmod 644 $DOCKER_DB_PATH"

# Clean up temp file
$SSH_CMD "rm -f /tmp/audiobooks-prod-docker.db"

# Start container
echo "Starting Docker container..."
$SSH_CMD "sudo docker start $CONTAINER_NAME"

# Wait for container to stabilize
sleep 10

# Verify container is running after restart
RUNNING=$($SSH_CMD "sudo docker inspect $CONTAINER_NAME --format '{{.State.Running}}' 2>/dev/null" | tr -d '[:space:]')
if [[ "$RUNNING" == "true" ]]; then
    DB_SYNC_STATUS="synced"
    DB_SCHEMA_VERSION="$PROD_SCHEMA"
    echo "DB sync complete (schema v$PROD_SCHEMA)"
else
    echo "ERROR: Container failed to start after DB sync"
    DB_SYNC_STATUS="failed (container not running after sync)"
    OVERALL="FAIL"
    # Try to restore backup
    $SSH_CMD "sudo cp ${DOCKER_DB_PATH}.bak-* $DOCKER_DB_PATH 2>/dev/null && sudo docker start $CONTAINER_NAME" 2>/dev/null || true
fi
```

**SUCCESS**: DB copied, container restarted, sync status recorded.
**FAILURE**: SCP or container restart failures are non-fatal. Record in report and continue.

---

## Step 5: Health Checks

**Goal**: Verify core Docker infrastructure is working before running regression tests.

### 5a. Container Running Status

```bash
CONTAINER_STATE=$($SSH_CMD "sudo docker ps --filter name=$CONTAINER_NAME --format '{{.Status}}'" 2>/dev/null)
echo "Container status: $CONTAINER_STATE"
if [[ -n "$CONTAINER_STATE" ]] && echo "$CONTAINER_STATE" | grep -qi 'up'; then
    HEALTH_CONTAINER="PASS ($CONTAINER_STATE)"
else
    HEALTH_CONTAINER="FAIL (${CONTAINER_STATE:-not found})"
    OVERALL="FAIL"
fi
```

### 5b. HTTPS Web Responds

```bash
HTTPS_CODE=$(curl -sfk -o /dev/null -w '%{http_code}' "$DOCKER_WEB/" 2>/dev/null)
echo "HTTPS web HTTP code: $HTTPS_CODE"
if [[ "$HTTPS_CODE" == "200" ]] || [[ "$HTTPS_CODE" == "302" ]]; then
    HEALTH_HTTPS="PASS ($HTTPS_CODE)"
else
    HEALTH_HTTPS="FAIL ($HTTPS_CODE)"
    OVERALL="FAIL"
fi
```

### 5c. HTTP Redirect Works

```bash
HTTP_CODE=$(curl -sf -o /dev/null -w '%{http_code}' "$DOCKER_HTTP/" 2>/dev/null)
echo "HTTP redirect code: $HTTP_CODE"
if [[ "$HTTP_CODE" == "301" ]] || [[ "$HTTP_CODE" == "302" ]]; then
    HEALTH_HTTP_REDIRECT="PASS ($HTTP_CODE)"
elif [[ "$HTTP_CODE" == "000" ]]; then
    # Connection refused — HTTP redirect may not be enabled
    HEALTH_HTTP_REDIRECT="SKIP (port 8080 not responding)"
else
    HEALTH_HTTP_REDIRECT="FAIL ($HTTP_CODE)"
fi
```

### 5d. Container VERSION File

```bash
CONTAINER_VER=$($SSH_CMD "sudo docker exec $CONTAINER_NAME cat /app/VERSION" 2>/dev/null | tr -d '[:space:]')
echo "Container VERSION: $CONTAINER_VER"
DOCKER_VERSION="$CONTAINER_VER"
```

### 5e. Database Integrity

```bash
DB_CHECK=$($SSH_CMD "sudo docker exec $CONTAINER_NAME python3 -c \"
import sqlite3
c = sqlite3.connect('/app/data/audiobooks.db')
result = c.execute('PRAGMA integrity_check').fetchone()[0]
print(result)
c.close()
\"" 2>/dev/null | tr -d '[:space:]')
echo "DB integrity: $DB_CHECK"
if [[ "$DB_CHECK" == "ok" ]]; then
    HEALTH_DB_INTEGRITY="PASS"
else
    HEALTH_DB_INTEGRITY="FAIL ($DB_CHECK)"
    OVERALL="FAIL"
fi
```

### 5f. Container Logs (Recent Errors)

```bash
ERROR_LINES=$($SSH_CMD "sudo docker logs $CONTAINER_NAME --since 5m 2>&1 | grep -i error | head -10" 2>/dev/null)
if [[ -n "$ERROR_LINES" ]]; then
    LOG_ERRORS=$(echo "$ERROR_LINES" | wc -l)
    echo "Found $LOG_ERRORS error-level log entries:"
    echo "$ERROR_LINES"
    HEALTH_LOGS="$LOG_ERRORS errors"
else
    LOG_ERRORS=0
    echo "No error-level log entries in last 5 minutes"
    HEALTH_LOGS="clean"
fi
```

**SUCCESS**: All health checks pass.
**FAILURE**: Individual failures are recorded. Only set OVERALL=FAIL on critical checks (container not running, HTTPS down, DB integrity). Continue to regression regardless.

---

## Step 6: Full Regression

Run all regression tests. Each sub-test increments pass/total counters. Do NOT abort on individual test failures; collect all results.

### 6a. Web UI Page Tests

Test that key web pages return HTTP 200 (or 302 for auth-required pages) via HTTPS:

```bash
declare -a WEB_PAGES=("/" "/index.html" "/about.html" "/help.html")
WEB_TESTS_TOTAL=${#WEB_PAGES[@]}
WEB_TESTS_PASSED=0

for page in "${WEB_PAGES[@]}"; do
    HTTP=$(curl -sfk -o /dev/null -w '%{http_code}' "$DOCKER_WEB$page" 2>/dev/null)
    if [[ "$HTTP" == "200" ]] || [[ "$HTTP" == "302" ]] || [[ "$HTTP" == "304" ]]; then
        echo "  PASS: $page ($HTTP)"
        WEB_TESTS_PASSED=$((WEB_TESTS_PASSED + 1))
    else
        echo "  FAIL: $page ($HTTP)"
    fi
done
```

### 6b. Library Browsing

Test library data is accessible through the Docker web interface:

```bash
# Test: Library page returns audiobook data
LIBRARY_RESP=$(curl -sfk "$DOCKER_WEB/" 2>/dev/null)
if [[ -n "$LIBRARY_RESP" ]] && [[ ${#LIBRARY_RESP} -gt 500 ]]; then
    echo "  Library page returned ${#LIBRARY_RESP} bytes"

    # Test: Search functionality (via API through HTTPS proxy or direct container exec)
    # Use docker exec to hit the internal API since port 5001 is not exposed
    SEARCH_RESP=$($SSH_CMD "sudo docker exec $CONTAINER_NAME curl -sf http://localhost:5001/api/audiobooks?search=king 2>/dev/null" 2>/dev/null)
    SEARCH_OK=false
    if [[ -n "$SEARCH_RESP" ]] && echo "$SEARCH_RESP" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "  Search API: valid JSON response"
        SEARCH_OK=true
    else
        echo "  Search API: failed or invalid response"
    fi

    # Test: Author filtering
    AUTHOR_RESP=$($SSH_CMD "sudo docker exec $CONTAINER_NAME curl -sf 'http://localhost:5001/api/audiobooks?author=Stephen' 2>/dev/null" 2>/dev/null)
    AUTHOR_OK=false
    if [[ -n "$AUTHOR_RESP" ]] && echo "$AUTHOR_RESP" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "  Author filter API: valid JSON response"
        AUTHOR_OK=true
    else
        echo "  Author filter API: failed or invalid response"
    fi

    if $SEARCH_OK && $AUTHOR_OK; then
        LIBRARY_BROWSING="PASS"
    elif $SEARCH_OK || $AUTHOR_OK; then
        LIBRARY_BROWSING="PARTIAL"
    else
        LIBRARY_BROWSING="FAIL"
    fi
else
    echo "  Library page returned insufficient data (${#LIBRARY_RESP} bytes)"
    LIBRARY_BROWSING="FAIL"
fi
```

### 6c. Supplements Accessible

```bash
# Check if supplements mount has content
SUPP_COUNT=$($SSH_CMD "sudo docker exec $CONTAINER_NAME ls -1 /supplements/ 2>/dev/null | wc -l" 2>/dev/null | tr -d '[:space:]')
echo "  Supplements files: $SUPP_COUNT"

if [[ "$SUPP_COUNT" -gt 0 ]] 2>/dev/null; then
    # Test supplements API endpoint
    SUPP_RESP=$($SSH_CMD "sudo docker exec $CONTAINER_NAME curl -sf http://localhost:5001/api/supplements 2>/dev/null" 2>/dev/null)
    if [[ -n "$SUPP_RESP" ]] && echo "$SUPP_RESP" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        echo "  Supplements API: valid JSON response"
        SUPPLEMENTS_STATUS="PASS ($SUPP_COUNT files)"
    else
        echo "  Supplements API: failed or invalid response"
        SUPPLEMENTS_STATUS="FAIL (mount OK, API error)"
    fi
else
    echo "  No supplements mounted or directory empty"
    SUPPLEMENTS_STATUS="N/A (no supplements)"
fi
```

### 6d. Container Restart Resilience

Verify the container recovers after a restart:

```bash
echo "Restarting Docker container..."
$SSH_CMD "sudo docker restart $CONTAINER_NAME"

# Wait for container to come back
sleep 10

# Check container is running
RESTART_STATE=$($SSH_CMD "sudo docker ps --filter name=$CONTAINER_NAME --format '{{.Status}}'" 2>/dev/null)
echo "  Post-restart status: $RESTART_STATE"

# Check HTTPS responds
RESTART_HTTP=$(curl -sfk -o /dev/null -w '%{http_code}' "$DOCKER_WEB/" 2>/dev/null)
echo "  Post-restart HTTPS code: $RESTART_HTTP"

if echo "$RESTART_STATE" | grep -qi 'up' && [[ "$RESTART_HTTP" == "200" || "$RESTART_HTTP" == "302" ]]; then
    RESTART_RESILIENCE="PASS"
    echo "  Restart resilience: PASS"
else
    RESTART_RESILIENCE="FAIL (status: $RESTART_STATE, HTTP: $RESTART_HTTP)"
    echo "  Restart resilience: FAIL"
    OVERALL="FAIL"
fi
```

### 6e. Container Resource Usage

```bash
RESOURCE_USAGE=$($SSH_CMD "sudo docker stats $CONTAINER_NAME --no-stream --format '{{.MemUsage}} / {{.CPUPerc}}'" 2>/dev/null)
echo "  Resource usage: $RESOURCE_USAGE"
```

**SUCCESS**: All regression tests pass.
**FAILURE**: Individual failures are recorded. Continue collecting results.

---

## Step 7: Consistency Check (Native vs Docker)

**Goal**: Compare the native app and Docker container to ensure they agree on version and data.

### 7a. Version Comparison

```bash
# Get native version (try API first, then VERSION file)
NATIVE_VERSION=$(curl -sf "$NATIVE_API/api/system/version" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('version', d.get('app_version', '')))
except:
    print('')
" 2>/dev/null)

if [[ -z "$NATIVE_VERSION" ]]; then
    NATIVE_VERSION=$($SSH_CMD "cat /opt/audiobooks/VERSION 2>/dev/null" | tr -d '[:space:]')
fi

echo "Native version: $NATIVE_VERSION"
echo "Docker version: $DOCKER_VERSION"

if [[ "$NATIVE_VERSION" == "$DOCKER_VERSION" ]]; then
    CONSISTENCY_VERSION="PASS ($NATIVE_VERSION)"
elif [[ -z "$NATIVE_VERSION" ]]; then
    CONSISTENCY_VERSION="SKIP (native not responding)"
else
    CONSISTENCY_VERSION="MISMATCH (native=$NATIVE_VERSION, docker=$DOCKER_VERSION)"
fi
```

### 7b. Library Count Comparison

```bash
# Docker book count (via container exec)
DOCKER_BOOK_COUNT=$($SSH_CMD "sudo docker exec $CONTAINER_NAME python3 -c \"
import sqlite3
c = sqlite3.connect('/app/data/audiobooks.db')
print(c.execute('SELECT COUNT(*) FROM audiobooks').fetchone()[0])
c.close()
\"" 2>/dev/null | tr -d '[:space:]')
echo "Docker book count: $DOCKER_BOOK_COUNT"

# Native book count (try API first, then direct DB query)
NATIVE_BOOK_COUNT=$(curl -sf "$NATIVE_API/api/stats" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('total_audiobooks', d.get('book_count', d.get('total', ''))))
except:
    print('')
" 2>/dev/null)

if [[ -z "$NATIVE_BOOK_COUNT" ]]; then
    NATIVE_BOOK_COUNT=$($SSH_CMD "sqlite3 /var/lib/audiobooks/db/audiobooks.db 'SELECT COUNT(*) FROM audiobooks'" 2>/dev/null | tr -d '[:space:]')
fi
echo "Native book count: $NATIVE_BOOK_COUNT"

if [[ -n "$DOCKER_BOOK_COUNT" ]] && [[ -n "$NATIVE_BOOK_COUNT" ]]; then
    if [[ "$DOCKER_BOOK_COUNT" == "$NATIVE_BOOK_COUNT" ]]; then
        CONSISTENCY_BOOKS="MATCH ($DOCKER_BOOK_COUNT)"
    else
        CONSISTENCY_BOOKS="MISMATCH"
    fi
else
    CONSISTENCY_BOOKS="SKIP (data unavailable)"
fi
```

### 7c. Author Count Comparison

```bash
# Docker author count
DOCKER_AUTHOR_COUNT=$($SSH_CMD "sudo docker exec $CONTAINER_NAME python3 -c \"
import sqlite3
c = sqlite3.connect('/app/data/audiobooks.db')
print(c.execute('SELECT COUNT(DISTINCT author) FROM audiobooks').fetchone()[0])
c.close()
\"" 2>/dev/null | tr -d '[:space:]')
echo "Docker author count: $DOCKER_AUTHOR_COUNT"

# Native author count (try API first, then direct DB query)
NATIVE_AUTHOR_COUNT=$(curl -sf "$NATIVE_API/api/stats" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('total_authors', d.get('author_count', d.get('authors', ''))))
except:
    print('')
" 2>/dev/null)

if [[ -z "$NATIVE_AUTHOR_COUNT" ]]; then
    NATIVE_AUTHOR_COUNT=$($SSH_CMD "sqlite3 /var/lib/audiobooks/db/audiobooks.db 'SELECT COUNT(DISTINCT author) FROM audiobooks'" 2>/dev/null | tr -d '[:space:]')
fi
echo "Native author count: $NATIVE_AUTHOR_COUNT"

if [[ -n "$DOCKER_AUTHOR_COUNT" ]] && [[ -n "$NATIVE_AUTHOR_COUNT" ]]; then
    if [[ "$DOCKER_AUTHOR_COUNT" == "$NATIVE_AUTHOR_COUNT" ]]; then
        CONSISTENCY_AUTHORS="MATCH ($DOCKER_AUTHOR_COUNT)"
    else
        CONSISTENCY_AUTHORS="MISMATCH"
    fi
else
    CONSISTENCY_AUTHORS="SKIP (data unavailable)"
fi
```

**SUCCESS**: All consistency checks match or are explained.
**FAILURE**: Mismatches flagged in report.

---

## Step 8: Report

Generate the final structured report. Print it to stdout for the parent session to capture.

Determine `OVERALL` status: if any critical check has `FAIL`, set `OVERALL=FAIL`. If all critical checks pass but there are warnings, set `OVERALL=PASS (with warnings)`.

```
echo ""
echo "═══════════════════════════════════════════════"
echo "  QA DOCKER REGRESSION RESULTS"
echo "═══════════════════════════════════════════════"
echo "  Target Version:    $TARGET_VERSION"
echo "  Docker Version:    $DOCKER_VERSION"
echo "  Upgrade Performed: $UPGRADE_PERFORMED"
echo "  DB Sync:           $DB_SYNC_STATUS (schema v$DB_SCHEMA_VERSION)"
echo ""
echo "  HEALTH CHECKS"
echo "  ─────────────"
echo "  Container Status:  $HEALTH_CONTAINER"
echo "  HTTPS Web:         $HEALTH_HTTPS"
echo "  HTTP Redirect:     $HEALTH_HTTP_REDIRECT"
echo "  DB Integrity:      $HEALTH_DB_INTEGRITY"
echo "  Container Logs:    $HEALTH_LOGS"
echo ""
echo "  REGRESSION TESTS"
echo "  ─────────────────"
echo "  Web UI Pages:      $WEB_TESTS_PASSED/$WEB_TESTS_TOTAL passed"
echo "  Library Browsing:  $LIBRARY_BROWSING"
echo "  Supplements:       $SUPPLEMENTS_STATUS"
echo "  Restart Resilience:$RESTART_RESILIENCE"
echo "  Resource Usage:    $RESOURCE_USAGE"
echo ""
echo "  CONSISTENCY (Native vs Docker)"
echo "  ──────────────────────────────"
echo "  Version Match:     $CONSISTENCY_VERSION"
echo "  Library Count:     Native=$NATIVE_BOOK_COUNT, Docker=$DOCKER_BOOK_COUNT $CONSISTENCY_BOOKS"
echo "  Author Count:      Native=$NATIVE_AUTHOR_COUNT, Docker=$DOCKER_AUTHOR_COUNT $CONSISTENCY_AUTHORS"
echo ""
echo "  OVERALL: $OVERALL"
echo "═══════════════════════════════════════════════"
```

---

## Abort Protocol

If any step marked as **ABORT on failure** fails:

1. Set `OVERALL="FAIL"`.
2. Set all untested metrics to `"ABORT"`.
3. Skip to **Step 8** and generate the report with whatever data was collected.
4. Clearly state which step caused the abort and why.

Do NOT leave the QA VM in a bad state. If the Docker container was stopped for DB sync and an abort happens mid-sync, restart it:

```bash
$SSH_CMD "sudo docker start $CONTAINER_NAME" 2>/dev/null || true
```

---

## Cleanup

After the report is generated:

1. Remove any temporary files created on the QA VM:

```bash
$SSH_CMD "rm -f /tmp/audiobooks-prod-docker.db" 2>/dev/null || true
```

2. Remove local temp files:

```bash
rm -f /tmp/qa-docker-resp
```

3. Do NOT stop the Docker container (persistent QA environment).
4. Do NOT shut down the QA VM (it stays running for native QA tests or manual inspection).
5. Do NOT revert to snapshot (QA VM is persistent, unlike the test VM).
6. Do NOT stop the Docker daemon (leave it running for subsequent testing).
