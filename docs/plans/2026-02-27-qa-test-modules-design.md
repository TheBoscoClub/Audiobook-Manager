# QA Test Modules Design

**Date**: 2026-02-27
**Status**: Design
**Version**: 1.0

## Overview

Add project-specific test modules that provide `qaapp`, `qadocker`, and `qaall` shortcuts to `/test` for complete autonomous testing of the QA VM (qa-audiobook-cachyos at 192.168.122.63).

## QA Philosophy

**QA VM = latest released version mirror (production n+1)**

- QA always runs the most current fully-released application version and Docker package
- Production may lag behind (hasn't been upgraded yet), but QA must be current
- **Target version** = max(latest GitHub release tag, latest locally staged release)
- If QA is behind target version, auto-upgrade before testing
- **Database sync**: QA databases are always refreshed from production before testing, unless the QA release version's schema is incompatible with the production database (e.g., schema migrations that can't be applied). This ensures QA tests against real production data.

## Architecture

### Two Layers

1. **Dispatcher layer** (claude-test-skill/commands/test.md)
   - Add `qaapp`, `qadocker`, `qaall` as shortcut words
   - Generic project-specific module discovery: look for `test-*-qa-{app,docker,all}.md` in project root
   - Load matching module as subagent instructions

2. **Module layer** (Audiobook-Manager project root)
   - `test-audiobook-manager-qa-app.md` — Native app QA regression
   - `test-audiobook-manager-qa-docker.md` — Docker app QA regression
   - `test-audiobook-manager-qa-all.md` — Orchestrator (both sequential)

### File Layout

```text
Audiobook-Manager/
├── test-audiobook-manager-qa-app.md      # Native QA module
├── test-audiobook-manager-qa-docker.md   # Docker QA module
├── test-audiobook-manager-qa-all.md      # Both (orchestrator)
├── vm-test-manifest.json                 # Extended with QA VM config
└── .gitignore                            # qa module .md files added

claude-test-skill/
└── commands/test.md                      # Dispatcher gains qaapp/qadocker/qaall
```

### Dispatcher Changes (test.md)

```text
argument-hint: "[help] [prodapp] [docker] [qaapp] [qadocker] [qaall] [security] ..."

Shortcuts:
  qaapp    → load test-*-qa-app.md from project root
  qadocker → load test-*-qa-docker.md from project root
  qaall    → load test-*-qa-all.md from project root
```

Discovery mechanism: `ls test-*-qa-{app,docker,all}.md` in `$PROJECT_DIR`. Fail with clear error if no matching file found.

## Module: test-audiobook-manager-qa-app.md

### Execution Flow

```text
1. VERSION RESOLUTION
   - Get latest GitHub release tag: gh release view --json tagName
   - Check for .staged-release breadcrumb (staged version)
   - Target = max(github_release, staged_version)
   - Get QA VM current version: ssh qa-vm 'cat /opt/audiobooks/VERSION'
   - If QA < target: auto-upgrade (see upgrade section)

2. UPGRADE (if needed)
   - For GitHub release: git checkout tag, ./upgrade.sh --from-project . --remote 192.168.122.63 --yes
   - For staged release: already on correct commit, ./upgrade.sh --from-project . --remote 192.168.122.63 --yes
   - Verify: ssh qa-vm 'cat /opt/audiobooks/VERSION' matches target

2b. DATABASE SYNC (production → QA)
   - Copy production database to QA VM native app:
     scp /var/lib/audiobooks/db/audiobooks.db claude@192.168.122.63:/tmp/
     ssh qa-vm 'sudo cp /tmp/audiobooks.db /var/lib/audiobooks/db/audiobooks.db'
     ssh qa-vm 'sudo chown audiobooks:audiobooks /var/lib/audiobooks/db/audiobooks.db'
   - Schema compatibility check:
     Compare schema version/migration level between production DB and QA app version
     If QA app expects newer schema: run migrations on copied DB
     If QA app expects older schema (downgrade): SKIP db sync, warn user
   - Restart services after DB swap: ssh qa-vm 'sudo systemctl restart audiobook.target'

3. HEALTH CHECK
   - All systemd services running (audiobook.target + 5 services)
   - API responds: curl http://192.168.122.63:5001/api/system/version
   - Web UI loads: curl -k https://192.168.122.63:8090/
   - Database accessible and not corrupt: PRAGMA integrity_check
   - Library count matches expected (~801 books)

4. FULL REGRESSION
   - API endpoint tests (auth, CRUD, search, streaming)
   - Web UI page loads (all routes return 200)
   - Auth flow (login with claudecode TOTP account)
   - Systemd service restart resilience
   - Log inspection for errors/warnings since last upgrade
   - SSL certificate validity

5. REPORT
   - Version deployed, tests passed/failed, upgrade performed (y/n)
   - Service health summary
   - Any log warnings
```

## Module: test-audiobook-manager-qa-docker.md

### Execution Flow

```text
1. VERSION RESOLUTION
   - Same logic as qa-app: target = max(github_release, staged_release)
   - Get QA Docker container version:
     ssh qa-vm 'sudo docker inspect audiobooks-docker --format={{.Config.Image}}'
   - Parse version from image tag

2. UPGRADE (if needed)
   - Start Docker daemon if not running: ssh qa-vm 'sudo systemctl start docker'
   - For staged release: docker save audiobook-manager:X.Y.Z | ssh qa-vm 'sudo docker load'
   - For GitHub release: rebuild from release tag, docker save | ssh load
   - Stop old container, remove, run new:
     ssh qa-vm 'sudo docker stop audiobooks-docker && sudo docker rm audiobooks-docker'
     ssh qa-vm 'sudo docker run -d --name audiobooks-docker ...' (with correct mounts)
   - Verify: ssh qa-vm 'sudo docker exec audiobooks-docker cat /app/VERSION'

2b. DATABASE SYNC (production → QA Docker)
   - Copy production database to QA VM Docker data path:
     scp /var/lib/audiobooks/db/audiobooks.db claude@192.168.122.63:/tmp/
     ssh qa-vm 'sudo cp /tmp/audiobooks.db /var/lib/audiobooks/docker-data/audiobooks.db'
   - Schema compatibility check (same logic as native):
     Compare schema version between production DB and Docker app version
     If incompatible: SKIP db sync, warn user
   - Restart container after DB swap: ssh qa-vm 'sudo docker restart audiobooks-docker'

3. HEALTH CHECK
   - Container running: docker ps shows audiobooks-docker
   - Web responds: curl -k https://192.168.122.63:8443/
   - API responds (internal to container, mapped port)
   - Database accessible within container

4. FULL REGRESSION
   - Web UI page loads via HTTPS (8443)
   - HTTP redirect works (8080 → 8443)
   - Library browsing (read-only mount working)
   - Supplements accessible (read-only mount working)
   - Auth flow within Docker
   - Container logs inspection for errors

5. CONSISTENCY CHECK
   - Compare native vs Docker: same VERSION, same library count
   - Both return consistent API responses for same endpoints
   - Flag any behavioral divergence

6. REPORT
   - Docker version deployed, tests passed/failed
   - Container health, image size
   - Native vs Docker consistency results
```

## Module: test-audiobook-manager-qa-all.md

### Execution Flow

```text
1. Run qa-app module (native first)
2. Run qa-docker module (Docker second)
3. Cross-validation: compare native and Docker results
4. Combined report with overall QA status
```

Sequential execution — native first since Docker consistency checks reference native results.

## vm-test-manifest.json Extension

Add a `qa_vm` section alongside existing `vm_testing`:

```json
{
  "qa_vm": {
    "enabled": true,
    "vm_name": "qa-audiobook-cachyos",
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
      "db_path": "/var/lib/audiobooks/docker-data/audiobooks.db",
      "library_mount": "/srv/audiobooks/Library",
      "supplements_mount": "/srv/audiobooks/Supplements"
    },
    "expected": {
      "library_count_approx": 801,
      "author_count_approx": 492
    },
    "upgrade": {
      "native_command": "./upgrade.sh --from-project . --remote 192.168.122.63 --yes",
      "docker_save_pattern": "docker save audiobook-manager:{version} | ssh -i ~/.ssh/id_ed25519 claude@192.168.122.63 'sudo docker load'"
    }
  }
}
```

## Dispatcher Integration Details

### Shortcut Routing (test.md changes)

```text
3. Handle shortcuts:
   - prodapp   → --phase=P
   - docker    → --phase=D
   - qaapp     → load project QA app module
   - qadocker  → load project QA docker module
   - qaall     → load project QA all module
   - security  → --phase=5
   - github    → --phase=G
   - holistic  → --phase=H
```

### QA Module Loading Logic

```text
IF shortcut in [qaapp, qadocker, qaall]:
    suffix = {qaapp: "app", qadocker: "docker", qaall: "all"}[shortcut]
    pattern = "test-*-qa-${suffix}.md"
    module_file = glob(PROJECT_DIR/${pattern})

    IF no match:
        ERROR: "No QA ${suffix} module found in project root."
        ERROR: "Expected file matching: ${pattern}"
        ERROR: "See docs/plans/2026-02-27-qa-test-modules-design.md for setup."
        ABORT

    IF multiple matches:
        WARN: "Multiple QA ${suffix} modules found, using first: ${module_file[0]}"

    # Execute as standalone subagent (no tier system, no other phases)
    spawn_subagent(
        instructions=read(module_file),
        model="opus",
        context={
            PROJECT_DIR, vm-test-manifest.json qa_vm section,
            SSH config, current VERSION
        }
    )

    # QA modules are self-contained — no Phase S/M/0/1 prerequisites
    # They handle their own VM connectivity, version checks, upgrades
```

### Key Difference from Normal Phases

QA modules are **standalone** — they don't participate in the tier/gate system. When you run `/test qaapp`, ONLY the QA app module runs. No preflight, no discovery, no cleanup phases. The module handles everything internally.

This is intentional: QA testing is a separate concern from project code auditing.

## Gitignore

Add to `.gitignore`:

```text
# QA test modules (project-specific, not tracked)
test-audiobook-manager-qa-*.md
```

Wait — actually these SHOULD be tracked in git since they're part of the project's test infrastructure. They contain no secrets (SSH config comes from vm-test-manifest.json which is already tracked). Keep them in version control.

## Security Considerations

- SSH key path from `~/.ssh/id_ed25519` (not embedded in modules)
- VM password from `~/.claude/rules/infrastructure.md` (not in module files)
- No production data paths in modules — QA VM has its own isolated library copy
- Docker operations use `sudo` on QA VM (claude user has NOPASSWD sudo)

## Implementation Files

| File | Action | Repository |
|------|--------|------------|
| `commands/test.md` | Add qaapp/qadocker/qaall shortcuts + QA module loader | claude-test-skill |
| `test-audiobook-manager-qa-app.md` | Create native QA module | Audiobook-Manager |
| `test-audiobook-manager-qa-docker.md` | Create Docker QA module | Audiobook-Manager |
| `test-audiobook-manager-qa-all.md` | Create orchestrator module | Audiobook-Manager |
| `vm-test-manifest.json` | Add qa_vm section | Audiobook-Manager |
| `.claude/rules/design.md` | Update project-specific modules status | claude-test-skill |
