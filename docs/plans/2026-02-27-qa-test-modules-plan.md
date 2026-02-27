# QA Test Modules Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `qaapp`, `qadocker`, `qaall` shortcuts to `/test` for autonomous QA VM regression testing with auto-upgrade and production database sync.

**Architecture:** Project-specific QA test modules in Audiobook-Manager project root, loaded by the `/test` dispatcher via glob pattern discovery. Each module is a self-contained markdown instruction file executed as a subagent. QA VM always runs the latest released version; production databases are synced before testing.

**Tech Stack:** Bash (SSH, scp, docker, systemd), SQLite (schema version checks), gh CLI (release detection), Python (version comparison)

---

### Task 1: Extend vm-test-manifest.json with QA VM Configuration

**Files:**
- Modify: `vm-test-manifest.json` (add `qa_vm` section after `ssh_config`)

**Step 1: Add qa_vm section to manifest**

Add this JSON block after the existing `ssh_config` section (before the closing `}`):

```json
,

  "qa_vm": {
    "enabled": true,
    "vm_name": "qa-audiobooks-cachyos",
    "static_ip": "192.168.122.63",
    "snapshot": "return-to-base-2026-02-23",
    "ports": {
      "native_api": 5001,
      "native_web": 8090,
      "docker_web_https": 8443,
      "docker_web_http": 8080
    },
    "docker": {
      "container_name": "audiobooks-docker",
      "image_prefix": "audiobook-manager",
      "daemon_preset": "disabled",
      "db_path": "/var/lib/audiobooks/docker-data/audiobooks.db",
      "library_mount": "/srv/audiobooks/Library:/audiobooks:ro",
      "supplements_mount": "/srv/audiobooks/Supplements:/supplements:ro",
      "data_volume": "/var/lib/audiobooks/docker-data:/app/data"
    },
    "native": {
      "app_path": "/opt/audiobooks",
      "db_path": "/var/lib/audiobooks/db/audiobooks.db",
      "version_file": "/opt/audiobooks/VERSION",
      "service_target": "audiobook.target"
    },
    "production_db": {
      "source_path": "/var/lib/audiobooks/db/audiobooks.db",
      "schema_version_query": "SELECT MAX(version) FROM schema_version",
      "integrity_check": "PRAGMA integrity_check"
    },
    "expected": {
      "library_count_approx": 801,
      "author_count_approx": 492
    },
    "upgrade": {
      "native_command": "./upgrade.sh --from-project . --remote 192.168.122.63 --yes",
      "docker_save_pattern": "docker save audiobook-manager:{version} | ssh -i ~/.claude/ssh/id_ed25519 claude@192.168.122.63 'sudo docker load'"
    }
  }
```

**Step 2: Validate JSON is well-formed**

Run: `python3 -c "import json; json.load(open('vm-test-manifest.json')); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add vm-test-manifest.json
git commit -m "feat: add QA VM configuration to vm-test-manifest.json"
```

---

### Task 2: Create Native QA Test Module

**Files:**
- Create: `test-audiobook-manager-qa-app.md` (project root)

**Step 1: Write the QA native app test module**

This is the instruction file that the `/test` dispatcher will load as subagent instructions. It must be fully self-contained with all logic, SSH commands, version resolution, database sync, and regression test steps.

The module structure:

```markdown
# QA Native App Test Module

> **Model**: `opus` | **Type**: Project-specific QA module
> **Target VM**: qa-audiobooks-cachyos (192.168.122.63)
> **SSH**: ssh -i ~/.claude/ssh/id_ed25519 claude@192.168.122.63

## Purpose

Autonomous regression testing of the QA VM's native Audiobook-Manager installation.
Ensures QA always runs the latest released version with production data.

## Execution Steps

### Step 1: VM Connectivity

[SSH connectivity check, VM state verification, start if needed]

### Step 2: Version Resolution

[Determine target version = max(GitHub release, staged release)]
[Get current QA version via SSH]
[Compare and decide if upgrade needed]

### Step 3: Auto-Upgrade (if needed)

[Checkout correct tag, run upgrade.sh --remote]
[Verify VERSION matches target after upgrade]

### Step 4: Database Sync (Production → QA)

[Copy production DB to QA VM]
[Schema compatibility check — compare schema_version]
[If compatible: swap DB, restart services]
[If incompatible: warn and skip sync]

### Step 5: Health Checks

[systemd services, API version endpoint, web UI, DB integrity]

### Step 6: Full Regression

[API endpoints, auth flow, library browsing, service restart resilience, logs]

### Step 7: Report

[Summary with pass/fail, version info, upgrade status, DB sync status]
```

The actual file content will contain complete bash commands, SSH invocations, version comparison logic, and error handling. Key details:

- SSH key: `~/.claude/ssh/id_ed25519`
- Production DB: `/var/lib/audiobooks/db/audiobooks.db` (on dev host)
- QA native DB: `/var/lib/audiobooks/db/audiobooks.db` (on QA VM)
- Schema version: `SELECT MAX(version) FROM schema_version` (current: 7)
- Services: `audiobook.target` (umbrella for 5 services)
- API: `http://192.168.122.63:5001/api/system/version`
- Web: `https://192.168.122.63:8090/`
- Upgrade: `./upgrade.sh --from-project . --remote 192.168.122.63 --yes`

**Step 2: Validate file exists and is readable**

Run: `head -5 test-audiobook-manager-qa-app.md`
Expected: First 5 lines of the module header

**Step 3: Commit**

```bash
git add test-audiobook-manager-qa-app.md
git commit -m "feat: add QA native app test module for /test qaapp"
```

---

### Task 3: Create Docker QA Test Module

**Files:**
- Create: `test-audiobook-manager-qa-docker.md` (project root)

**Step 1: Write the QA Docker test module**

Same pattern as Task 2, but targeting the Docker container. Key differences:

- Docker daemon must be started (`sudo systemctl start docker`)
- Version from: `sudo docker inspect audiobooks-docker --format={{.Config.Image}}`
- Upgrade: `docker save` + `ssh pipe` + `docker load` + stop/rm/run new container
- DB sync target: `/var/lib/audiobooks/docker-data/audiobooks.db`
- Web: `https://192.168.122.63:8443/` (HTTPS)
- HTTP redirect: `http://192.168.122.63:8080/` → 8443
- Container restart: `sudo docker restart audiobooks-docker`
- Docker run command:
  ```
  sudo docker run -d --name audiobooks-docker \
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
    audiobook-manager:{version}
  ```
- Consistency check: compare native vs Docker for same VERSION, same library count

**Step 2: Validate file exists**

Run: `head -5 test-audiobook-manager-qa-docker.md`

**Step 3: Commit**

```bash
git add test-audiobook-manager-qa-docker.md
git commit -m "feat: add QA Docker test module for /test qadocker"
```

---

### Task 4: Create QA All Orchestrator Module

**Files:**
- Create: `test-audiobook-manager-qa-all.md` (project root)

**Step 1: Write the orchestrator module**

This module coordinates sequential execution of both QA modules:

```markdown
# QA All Test Module (Orchestrator)

> **Model**: `opus` | **Type**: Project-specific QA orchestrator
> **Target VM**: qa-audiobooks-cachyos (192.168.122.63)

## Purpose

Run complete QA regression for both native app and Docker container sequentially.
Native runs first (establishes baseline), Docker runs second (includes consistency check).

## Execution

### Phase 1: Native App Testing
Read and execute: test-audiobook-manager-qa-app.md
Record results: native_version, native_pass_count, native_fail_count, db_sync_status

### Phase 2: Docker Testing
Read and execute: test-audiobook-manager-qa-docker.md
Record results: docker_version, docker_pass_count, docker_fail_count, db_sync_status

### Phase 3: Cross-Validation
- Compare native_version == docker_version (both should be at target)
- Compare library counts from both (should match)
- Compare API responses for key endpoints (version, stats)
- Flag any behavioral divergence

### Phase 4: Combined Report
- Overall QA status: PASS/FAIL
- Native results summary
- Docker results summary
- Cross-validation results
- Any divergences found
```

**Step 2: Validate file exists**

Run: `head -5 test-audiobook-manager-qa-all.md`

**Step 3: Commit**

```bash
git add test-audiobook-manager-qa-all.md
git commit -m "feat: add QA orchestrator module for /test qaall"
```

---

### Task 5: Add QA Shortcuts to /test Dispatcher

**Files:**
- Modify: `/hddRaid1/ClaudeCodeProjects/claude-test-skill/commands/test.md`

This is the critical integration point. Changes needed in 5 locations:

**Step 1: Update argument-hint (line 21)**

Change:
```
argument-hint: "[help] [prodapp] [docker] [security] [github] [holistic] [--phase=X] [--list-phases] [--skip-snapshot] [--interactive]"
```
To:
```
argument-hint: "[help] [prodapp] [docker] [qaapp] [qadocker] [qaall] [security] [github] [holistic] [--phase=X] [--list-phases] [--skip-snapshot] [--interactive]"
```

**Step 2: Add QA shortcuts to Quick Reference section (~line 60)**

After the existing `/test docker` line, add:
```
/test qaapp              # QA VM: regression test native app (auto-upgrade + DB sync)
/test qadocker           # QA VM: regression test Docker container (auto-upgrade + DB sync)
/test qaall              # QA VM: regression test both native and Docker sequentially
```

**Step 3: Add QA shortcut routing to "Handle shortcuts" section (~line 882)**

After `- docker → --phase=D (Docker validation)`, add:
```
   - `qaapp` → load project QA app module (test-*-qa-app.md)
   - `qadocker` → load project QA docker module (test-*-qa-docker.md)
   - `qaall` → load project QA all module (test-*-qa-all.md)
```

**Step 4: Add QA module loading logic**

After the existing shortcut routing section, add a new subsection:

```markdown
### QA Module Loading (Project-Specific)

When the argument is `qaapp`, `qadocker`, or `qaall`:

1. **Map shortcut to file suffix:**
   - `qaapp` → `app`
   - `qadocker` → `docker`
   - `qaall` → `all`

2. **Find module file:**
   ```bash
   SUFFIX={app|docker|all}
   MODULE_FILE=$(ls ${PROJECT_DIR}/test-*-qa-${SUFFIX}.md 2>/dev/null | head -1)
   ```

3. **Validate:**
   - If no file found: ERROR and abort with helpful message
   - If found: Read the module file contents

4. **Execute as standalone subagent:**
   - Spawn a single Task subagent with model=opus
   - Pass the module file contents as instructions
   - Pass context: PROJECT_DIR, vm-test-manifest.json qa_vm section, SSH config
   - QA modules are STANDALONE — no tier system, no S/M/0/1 prerequisites
   - The module handles its own VM connectivity, version checks, upgrades

5. **Report results:**
   - Collect subagent output
   - Display QA test summary to user
```

**Step 5: Add QA shortcuts to help text (~line 814)**

In the help box, after the Docker shortcut line, add:
```
│  /test qaapp                    QA native app regression (upgrade+sync) │
│  /test qadocker                 QA Docker regression (upgrade+sync)    │
│  /test qaall                    QA both native+Docker regression        │
```

**Step 6: Add QA section to Recommended Execution docs (~line 1133)**

After the Docker validation section, add:
```markdown
For QA native app regression:
\`\`\`
/test qaapp
\`\`\`
This auto-upgrades the QA VM to the latest release, syncs the production database, and runs full regression against the native app installation.

For QA Docker regression:
\`\`\`
/test qadocker
\`\`\`
Same as qaapp but for the Docker container. Includes consistency check against native.

For complete QA regression (both):
\`\`\`
/test qaall
\`\`\`
Runs native first, then Docker, then cross-validates results.
```

**Step 7: Validate test.md is still coherent**

Run: `grep -c 'qaapp\|qadocker\|qaall' /hddRaid1/ClaudeCodeProjects/claude-test-skill/commands/test.md`
Expected: At least 10 matches (shortcuts appear in multiple locations)

**Step 8: Commit**

```bash
cd /hddRaid1/ClaudeCodeProjects/claude-test-skill
git add commands/test.md
git commit -m "feat: add qaapp/qadocker/qaall shortcuts for project-specific QA testing"
```

---

### Task 6: Update design.md Status

**Files:**
- Modify: `/hddRaid1/ClaudeCodeProjects/claude-test-skill/.claude/rules/design.md`

**Step 1: Update project-specific modules section**

Change `**Status**: Design phase` to `**Status**: Partially implemented (QA modules)`.

Add below the existing content:
```markdown
**Implemented**: QA module discovery for `qaapp`, `qadocker`, `qaall` shortcuts.
Dispatcher looks for `test-*-qa-{app,docker,all}.md` in project root.
First project using this: Audiobook-Manager.
```

**Step 2: Commit**

```bash
cd /hddRaid1/ClaudeCodeProjects/claude-test-skill
git add .claude/rules/design.md
git commit -m "docs: update project-specific test module status"
```

---

### Task 7: Verify End-to-End

**Step 1: Verify module discovery works**

From the Audiobook-Manager project root:
```bash
ls test-*-qa-*.md
```
Expected: Three files listed (app, docker, all)

**Step 2: Verify manifest is valid**

```bash
python3 -c "
import json
m = json.load(open('vm-test-manifest.json'))
qa = m['qa_vm']
print(f'QA VM: {qa[\"vm_name\"]} at {qa[\"static_ip\"]}')
print(f'Native API port: {qa[\"ports\"][\"native_api\"]}')
print(f'Docker HTTPS port: {qa[\"ports\"][\"docker_web_https\"]}')
print(f'Expected books: ~{qa[\"expected\"][\"library_count_approx\"]}')
print('OK')
"
```
Expected: Shows QA VM config, ends with `OK`

**Step 3: Verify dispatcher recognizes shortcuts**

```bash
grep -c 'qaapp\|qadocker\|qaall' /hddRaid1/ClaudeCodeProjects/claude-test-skill/commands/test.md
```
Expected: 10+ matches

**Step 4: Quick smoke test with `/test qaapp`**

Run `/test qaapp` to verify the full flow:
1. Dispatcher parses `qaapp` shortcut
2. Finds `test-audiobook-manager-qa-app.md` in project root
3. Spawns subagent with module instructions
4. Subagent connects to QA VM, checks version, upgrades if needed, syncs DB, runs regression
5. Results reported

This is the true end-to-end verification.

---

## Summary

| Task | Repository | What |
|------|-----------|------|
| 1 | Audiobook-Manager | Extend vm-test-manifest.json with QA VM config |
| 2 | Audiobook-Manager | Create native QA test module |
| 3 | Audiobook-Manager | Create Docker QA test module |
| 4 | Audiobook-Manager | Create QA orchestrator module |
| 5 | claude-test-skill | Add shortcuts + loading logic to dispatcher |
| 6 | claude-test-skill | Update design.md status |
| 7 | Both | End-to-end verification |
