---
model: opus
type: project-specific QA orchestrator
target: qa-audiobooks-cachyos (192.168.122.63)
ssh: ssh -i ~/.ssh/id_ed25519 claude@192.168.122.63
trigger: /test qaall
---

# QA Combined Regression Test Module (Native + Docker)

## Purpose

Run complete QA regression for **both** the native app and Docker container on the QA VM sequentially. Native runs first (establishes baseline), Docker runs second (includes consistency check against native).

This module orchestrates the two individual QA modules:
- `test-audiobook-manager-qa-app.md` — Native app regression
- `test-audiobook-manager-qa-docker.md` — Docker container regression

## Prerequisites

Both module files must exist in the project root:
```bash
ls test-audiobook-manager-qa-app.md test-audiobook-manager-qa-docker.md
```

If either is missing, ABORT with:
```
ERROR: Missing QA module file(s). Expected both:
  - test-audiobook-manager-qa-app.md
  - test-audiobook-manager-qa-docker.md
```

## Execution

### Phase 1: Native App Testing

1. **Read** the contents of `test-audiobook-manager-qa-app.md` from the project root
2. **Execute** all steps in that module in order (Steps 1-7)
3. **Record results** from the module's Step 7 report:
   - `native_version` — the version running after any upgrade
   - `native_upgrade_performed` — yes/no
   - `native_db_sync` — synced/skipped/failed
   - `native_health_pass` — number of health checks passed
   - `native_health_total` — total health checks
   - `native_regression_pass` — number of regression tests passed
   - `native_regression_total` — total regression tests
   - `native_overall` — PASS/FAIL
   - `native_library_count` — number of audiobooks in native DB
   - `native_author_count` — number of distinct authors in native DB

**If native module ABORTS** (VM connectivity, upgrade failure):
- Record the failure reason
- **Still attempt Docker testing** — Docker may be independently functional
- Note the native abort in the combined report

### Phase 2: Docker Testing

1. **Read** the contents of `test-audiobook-manager-qa-docker.md` from the project root
2. **Execute** all steps in that module in order (Steps 1-8)
3. **Record results** from the module's Step 8 report:
   - `docker_version` — the version running after any upgrade
   - `docker_upgrade_performed` — yes/no
   - `docker_db_sync` — synced/skipped/failed
   - `docker_health_pass` — number of health checks passed
   - `docker_health_total` — total health checks
   - `docker_regression_pass` — number of regression tests passed
   - `docker_regression_total` — total regression tests
   - `docker_consistency` — PASS/FAIL (native vs Docker comparison)
   - `docker_overall` — PASS/FAIL
   - `docker_library_count` — number of audiobooks in Docker DB
   - `docker_author_count` — number of distinct authors in Docker DB

**Note:** The Docker module's Step 7 (consistency check) already compares native vs Docker.
If native was not tested (Phase 1 aborted), the Docker module should still attempt
the consistency check using native API/DB data directly.

### Phase 3: Cross-Validation

After both modules complete, perform additional cross-validation:

```bash
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_CMD="ssh -i $SSH_KEY claude@192.168.122.63"
QA_IP="192.168.122.63"
```

#### 3a. Version Agreement

Both native and Docker should be at the same target version:

```bash
# Get native version
NATIVE_VER=$($SSH_CMD 'cat /opt/audiobooks/VERSION' 2>/dev/null || echo "UNAVAILABLE")

# Get Docker version
DOCKER_VER=$($SSH_CMD 'sudo docker exec audiobooks-docker cat /app/VERSION' 2>/dev/null || echo "UNAVAILABLE")

echo "Native version:  $NATIVE_VER"
echo "Docker version:  $DOCKER_VER"

if [[ "$NATIVE_VER" == "$DOCKER_VER" ]]; then
    echo "  Version match: PASS"
else
    echo "  Version match: FAIL — versions differ!"
fi
```

#### 3b. Library Data Consistency

Compare audiobook counts and author counts between native and Docker databases:

```bash
# Native DB counts
NATIVE_BOOKS=$($SSH_CMD 'sqlite3 /var/lib/audiobooks/db/audiobooks.db "SELECT COUNT(*) FROM audiobooks"' 2>/dev/null || echo "0")
NATIVE_AUTHORS=$($SSH_CMD 'sqlite3 /var/lib/audiobooks/db/audiobooks.db "SELECT COUNT(DISTINCT author) FROM audiobooks"' 2>/dev/null || echo "0")

# Docker DB counts
DOCKER_BOOKS=$($SSH_CMD 'sqlite3 /var/lib/audiobooks/docker-data/audiobooks.db "SELECT COUNT(*) FROM audiobooks"' 2>/dev/null || echo "0")
DOCKER_AUTHORS=$($SSH_CMD 'sqlite3 /var/lib/audiobooks/docker-data/audiobooks.db "SELECT COUNT(DISTINCT author) FROM audiobooks"' 2>/dev/null || echo "0")

echo "Library books:   Native=$NATIVE_BOOKS, Docker=$DOCKER_BOOKS"
echo "Library authors:  Native=$NATIVE_AUTHORS, Docker=$DOCKER_AUTHORS"

BOOKS_MATCH="FAIL"
AUTHORS_MATCH="FAIL"
[[ "$NATIVE_BOOKS" == "$DOCKER_BOOKS" ]] && BOOKS_MATCH="PASS"
[[ "$NATIVE_AUTHORS" == "$DOCKER_AUTHORS" ]] && AUTHORS_MATCH="PASS"

echo "  Books match:   $BOOKS_MATCH"
echo "  Authors match: $AUTHORS_MATCH"
```

#### 3c. API Response Consistency

Compare key API responses from both deployments:

```bash
# Native API (port 5001)
NATIVE_API_VER=$(curl -sf http://$QA_IP:5001/api/system/version 2>/dev/null || echo "UNAVAILABLE")

# Docker API (via docker exec since port 5001 is internal)
DOCKER_API_VER=$($SSH_CMD 'sudo docker exec audiobooks-docker curl -sf http://localhost:5001/api/system/version' 2>/dev/null || echo "UNAVAILABLE")

echo "Native API version response: $NATIVE_API_VER"
echo "Docker API version response: $DOCKER_API_VER"

if [[ "$NATIVE_API_VER" == "$DOCKER_API_VER" ]]; then
    echo "  API response match: PASS"
else
    echo "  API response match: FAIL — responses differ!"
fi
```

### Phase 4: Combined Report

```
╔═══════════════════════════════════════════════════════════════╗
║              QA COMBINED REGRESSION RESULTS                   ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  TARGET VERSION: {target_version}                             ║
║                                                               ║
║  NATIVE APP                                                   ║
║  ──────────                                                   ║
║  Version:         {native_version}                            ║
║  Upgrade:         {yes/no}                                    ║
║  DB Sync:         {synced/skipped/failed}                     ║
║  Health Checks:   {N}/{total} passed                          ║
║  Regression:      {N}/{total} passed                          ║
║  Status:          {PASS/FAIL/ABORTED}                         ║
║                                                               ║
║  DOCKER CONTAINER                                             ║
║  ────────────────                                             ║
║  Version:         {docker_version}                            ║
║  Upgrade:         {yes/no}                                    ║
║  DB Sync:         {synced/skipped/failed}                     ║
║  Health Checks:   {N}/{total} passed                          ║
║  Regression:      {N}/{total} passed                          ║
║  Status:          {PASS/FAIL/ABORTED}                         ║
║                                                               ║
║  CROSS-VALIDATION                                             ║
║  ────────────────                                             ║
║  Version Match:   {PASS/FAIL}                                 ║
║  Library Count:   Native={N}, Docker={N} — {MATCH/MISMATCH}  ║
║  Author Count:    Native={N}, Docker={N} — {MATCH/MISMATCH}  ║
║  API Response:    {MATCH/MISMATCH}                            ║
║                                                               ║
║  ═══════════════════════════════════════════════════════════  ║
║  OVERALL QA STATUS: {PASS/FAIL}                               ║
║  ═══════════════════════════════════════════════════════════  ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```

**Overall QA Status determination:**
- **PASS**: Both native and Docker passed AND all cross-validation checks passed
- **FAIL**: Any of the following:
  - Native overall FAIL (unless ABORTED — see below)
  - Docker overall FAIL
  - Cross-validation mismatch on version or library count
- **PARTIAL**: Native ABORTED but Docker PASSED (or vice versa) — note which component failed

### Session Record

After completing the combined report, update the session record:

```bash
SESSION_RECORD="${PROJECT_DIR}/SESSION_RECORD_$(date +%Y-%m-%d).md"
(
  flock -w 10 200 || echo "WARN: writing without lock"
  cat >> "$SESSION_RECORD" << ENTRY

## QA Combined Regression: $(date +%H:%M:%S)
- **Target Version**: {target_version}
- **Native**: {PASS/FAIL/ABORTED} (v{native_version}, {N}/{total} health, {N}/{total} regression)
- **Docker**: {PASS/FAIL/ABORTED} (v{docker_version}, {N}/{total} health, {N}/{total} regression)
- **Cross-Validation**: Version={MATCH/MISMATCH}, Books={MATCH/MISMATCH}, Authors={MATCH/MISMATCH}
- **Overall**: {PASS/FAIL/PARTIAL}
ENTRY
) 200>"${SESSION_RECORD}.lock"
```
